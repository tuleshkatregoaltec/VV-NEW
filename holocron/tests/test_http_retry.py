"""Outbound calls retry only transient failures, obey provider back-off and stop on denial."""

from __future__ import annotations

import threading
import time
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime

import httpx
import pytest

from holocron.platform.http_retry import (
    ProviderLimiter,
    RetriesExhausted,
    RetryPolicy,
    SourceAccessDenied,
    retry_after_seconds,
    send,
)


def _client(responses: list[httpx.Response | Exception]) -> tuple[httpx.Client, list[str]]:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def _send(client: httpx.Client, sleeps: list[float], **kwargs):
    # Zero jitter: the only waits left are the ones the provider asked for.
    return send(
        client, "GET", "https://api.test/x", sleep=sleeps.append, jitter=lambda: 0.0, **kwargs
    )


def test_429_waits_exactly_as_long_as_retry_after_seconds_asks() -> None:
    client, calls = _client(
        [httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={"ok": 1})]
    )
    sleeps: list[float] = []
    assert _send(client, sleeps).json() == {"ok": 1}
    assert sleeps == [7.0] and len(calls) == 2


def test_retry_after_http_date_form_is_honoured() -> None:
    now = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)
    header = format_datetime(now + timedelta(seconds=30), usegmt=True)
    assert retry_after_seconds(header, now=now) == 30.0
    assert retry_after_seconds("garbage") is None


def test_transient_5xx_and_timeouts_retry_with_exponential_backoff() -> None:
    client, calls = _client(
        [
            httpx.ConnectTimeout("slow"),
            httpx.Response(503),
            httpx.Response(200, json={}),
        ]
    )
    sleeps: list[float] = []
    send(
        client,
        "GET",
        "https://api.test/x",
        policy=RetryPolicy(attempts=4, base_seconds=2.0),
        sleep=sleeps.append,
        jitter=lambda: 1.0,
    )
    assert sleeps == [2.0, 4.0] and len(calls) == 3


@pytest.mark.parametrize("status", [401, 403, 407])
def test_access_denial_stops_without_retrying(status: int) -> None:
    client, calls = _client([httpx.Response(status), httpx.Response(200)])
    with pytest.raises(SourceAccessDenied):
        _send(client, [])
    assert len(calls) == 1


def test_permanent_client_errors_are_not_retried() -> None:
    client, calls = _client([httpx.Response(404), httpx.Response(200)])
    with pytest.raises(httpx.HTTPStatusError):
        _send(client, [])
    assert len(calls) == 1


def test_retry_after_beyond_the_budget_stops_at_once_instead_of_sleeping() -> None:
    client, calls = _client([httpx.Response(429, headers={"Retry-After": "3600"})])
    sleeps: list[float] = []
    with pytest.raises(RetriesExhausted, match="3600s"):
        _send(client, sleeps, policy=RetryPolicy(max_seconds=60))
    assert sleeps == [] and len(calls) == 1


def test_persistent_failure_is_bounded_by_attempts_and_deadline() -> None:
    client, calls = _client([httpx.Response(500)] * 10)
    sleeps: list[float] = []
    with pytest.raises(RetriesExhausted, match="after 3 attempts"):
        _send(client, sleeps, policy=RetryPolicy(attempts=3))
    assert len(calls) == 3

    # The deadline wins over remaining attempts: a fake clock already past it stops at once.
    ticks = iter([0.0, 1000.0])
    client, calls = _client([httpx.Response(500)] * 10)
    with pytest.raises(RetriesExhausted):
        send(
            client,
            "GET",
            "https://api.test/x",
            policy=RetryPolicy(attempts=10, deadline_seconds=10),
            sleep=sleeps.append,
            clock=lambda: next(ticks),
            jitter=lambda: 1.0,
        )
    assert len(calls) == 1


def test_limiter_spaces_request_starts_and_counts_traffic() -> None:
    now = [0.0]
    waits: list[float] = []

    def sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    limiter = ProviderLimiter("p", min_interval_seconds=2.0, clock=lambda: now[0], sleep=sleep)
    client, _ = _client([httpx.Response(429), httpx.Response(200), httpx.Response(200)])
    _send(client, [], limiter=limiter)
    _send(client, [], limiter=limiter)
    assert waits == [2.0, 2.0]  # three request starts, each two seconds apart
    assert limiter.counters["requests"] == 3
    assert limiter.counters["throttled"] == 1 and limiter.counters["retries"] == 1


def test_limiter_caps_concurrency_across_threads() -> None:
    limiter = ProviderLimiter("p", max_concurrency=2)
    active, peak, lock = [0], [0], threading.Lock()

    def handler(request: httpx.Request) -> httpx.Response:
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.02)
        with lock:
            active[0] -= 1
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    threads = [
        threading.Thread(
            target=send, args=(client, "GET", "https://api.test/x"), kwargs={"limiter": limiter}
        )
        for _ in range(8)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak[0] == 2 and limiter.counters["requests"] == 8


def test_provider_and_market_assets_run_in_single_slot_pools() -> None:
    # The in-process limiter is provider-wide only because a provider's raw assets share one
    # pool (default limit 1 in dagster.yaml); the market lock is backed by the same mechanism.
    from pathlib import Path

    import yaml

    from holocron.definitions import defs

    assert defs.resolve_assets_def("dld_od_transactions_raw").op.pool == "provider_dld"
    assert defs.resolve_assets_def("dld_od_rents_raw").op.pool == "provider_dld"
    assert defs.resolve_assets_def("reelly_supply_raw").op.pool == "provider_reelly"
    assert defs.resolve_assets_def("market_rental_events").op.pool == "market_release"
    instance = yaml.safe_load((Path(__file__).parents[1] / "dagster.yaml").read_text())
    assert instance["concurrency"]["pools"]["default_limit"] == 1
