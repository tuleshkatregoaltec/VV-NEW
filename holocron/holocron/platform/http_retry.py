"""Bounded retries and provider-wide throttling at outbound HTTP boundaries.

``send`` retries only what a provider tells us is transient (timeouts, connection errors,
429 and 500/502/503/504), honours ``Retry-After`` in both seconds and HTTP-date forms, backs
off exponentially with full jitter and never sleeps past the overall deadline. Access denial
(401/403/407) raises ``SourceAccessDenied`` at once: the run must stop, not retry or evade.
Other 4xx responses and parse errors are permanent and are raised to the caller unretried.

``ProviderLimiter`` enforces a minimum interval between request starts and a concurrency cap
for every client of one provider in this process. Runs in different processes share a quota
only through Dagster: each provider's assets run in a ``pool`` with limit 1, so one process
holds a provider's quota at a time.
"""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
DENIED_STATUS = frozenset({401, 403, 407})


class SourceAccessDenied(RuntimeError):
    """The provider refused access; continuing would only repeat or evade the refusal."""


class RetriesExhausted(RuntimeError):
    """A transient failure outlasted the retry budget or the deadline."""


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 4  # total tries, the first included
    base_seconds: float = 1.0
    max_seconds: float = 60.0
    deadline_seconds: float = 600.0


@dataclass
class ProviderLimiter:
    """Minimum spacing between request starts plus a concurrency cap, with counters."""

    name: str
    min_interval_seconds: float = 0.0
    max_concurrency: int = 1
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], None] = time.sleep
    counters: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _next_start: float = 0.0
    _slots: threading.BoundedSemaphore = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._slots = threading.BoundedSemaphore(self.max_concurrency)

    def acquire(self) -> None:
        self._slots.acquire()
        with self._lock:
            now = self.clock()
            start = max(now, self._next_start)
            self._next_start = start + self.min_interval_seconds
        if start > now:
            self.sleep(start - now)

    def release(self) -> None:
        self._slots.release()

    def count(self, key: str, amount: int = 1) -> None:
        with self._lock:
            self.counters[key] = self.counters.get(key, 0) + amount


_LIMITERS: dict[str, ProviderLimiter] = {}
_LIMITERS_LOCK = threading.Lock()


def provider_limiter(
    name: str, *, min_interval_seconds: float = 0.0, max_concurrency: int = 1
) -> ProviderLimiter:
    """The one limiter for ``name`` in this process (settings from the first caller)."""
    with _LIMITERS_LOCK:
        if name not in _LIMITERS:
            _LIMITERS[name] = ProviderLimiter(
                name, min_interval_seconds=min_interval_seconds, max_concurrency=max_concurrency
            )
        return _LIMITERS[name]


def retry_after_seconds(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds to wait from a Retry-After header (delta-seconds or HTTP-date)."""
    if not value:
        return None
    text = value.strip()
    if text.isdigit():
        return float(text)
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - (now or datetime.now(UTC))).total_seconds(), 0.0)


def send(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    policy: RetryPolicy = RetryPolicy(),
    limiter: ProviderLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    jitter: Callable[[], float] = random.random,
    **kwargs: Any,
) -> httpx.Response:
    """Send one request with bounded retries; returns a 2xx/3xx response or raises."""
    deadline = clock() + policy.deadline_seconds
    last: str = ""
    for attempt in range(policy.attempts):
        if limiter:
            limiter.acquire()
        try:
            if limiter:
                limiter.count("requests")
            response = client.request(method, url, **kwargs)
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            response, last = None, f"{type(exc).__name__}: {exc}"
        finally:
            if limiter:
                limiter.release()

        wait_hint = None
        if response is not None:
            if limiter:
                limiter.count("bytes", len(response.content))
            if response.status_code in DENIED_STATUS:
                if limiter:
                    limiter.count("denied")
                raise SourceAccessDenied(
                    f"{method} {url} refused with HTTP {response.status_code}; stopping the run"
                )
            if response.status_code not in RETRYABLE_STATUS:
                response.raise_for_status()
                return response
            if response.status_code == 429 and limiter:
                limiter.count("throttled")
            wait_hint = retry_after_seconds(response.headers.get("retry-after"))
            last = f"HTTP {response.status_code}"

        if attempt + 1 >= policy.attempts:
            break
        backoff = jitter() * min(policy.max_seconds, policy.base_seconds * 2**attempt)
        wait = max(backoff, wait_hint or 0.0)
        if wait > policy.max_seconds or clock() + wait > deadline:
            # The provider asked for longer than this run may wait: stop, keep the checkpoint.
            raise RetriesExhausted(
                f"{method} {url}: {last}; provider asked to wait {wait:.0f}s, beyond the budget"
            )
        if limiter:
            limiter.count("retries")
        sleep(wait)
    raise RetriesExhausted(f"{method} {url}: {last} after {policy.attempts} attempts")
