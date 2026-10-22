"""Pinned, immutable market releases for one operation (a chat turn, an MCP call, a report).

Holocron keeps every published sales and rentals release as an immutable table,
``market_<sale|rental>_events__r<release_id>``, and records the release, its state and its
load-evidence coverage in ``market_release_ledger`` (holocron.market.release). Pinning
resolves the currently published release of each source once. While the pin is active, every
shared metrics query in this context reads those immutable tables, so totals, groupings,
comparisons and evidence come from the same versions however many queries the operation
runs, and whatever is published meanwhile.

Safety without lease rows: a pin lasts at most ``MAX_LEASE`` and fails closed afterwards,
while Holocron drops a superseded version only ``RELEASE_RETENTION`` (24 h) after it stopped
being published. A pin is taken only on a version that is published at that moment, so the
version outlives every pin on it. Versions of releases that never went live are never pinned.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from app.clickhouse import query

# Must equal holocron.market.release.MAX_READER_LEASE (retention is sized against it).
MAX_LEASE = timedelta(minutes=30)
RELEASED_SOURCES = {"sales": "market_sale_events", "rentals": "market_rental_events"}
_RELEASE_ID = re.compile(r"^[0-9a-f]{12}$")


class ReleaseUnavailable(RuntimeError):
    """The pinned operation needs a source that has no published immutable version."""


class ReleaseLeaseExpired(RuntimeError):
    """The pin outlived MAX_LEASE; its versions may no longer be retained."""


@dataclass(frozen=True, slots=True)
class SourceVersion:
    source: str
    release_id: str
    table: str
    # (start, end, status) day ranges from the release's ledger entry; status complete means
    # every day was loaded to the end of its source window (or is an attested archive).
    coverage: tuple[tuple[date, date, str], ...]


@dataclass(frozen=True, slots=True)
class PinnedRelease:
    versions: dict[str, SourceVersion]
    acquired_at: datetime
    expires_at: datetime
    missing: tuple[str, ...] = field(default=())

    @property
    def release_id(self) -> str:
        """One immutable identity for the whole pin, e.g. 'rentals=4d289c398c41;sales=…'."""
        return ";".join(f"{s}={v.release_id}" for s, v in sorted(self.versions.items()))

    @property
    def sources(self) -> frozenset[str]:
        return frozenset(self.versions)

    def coverage(self) -> tuple[tuple[str, date, date, str], ...]:
        """(source, start, end, status) ranges, the shape of chat's SourceCoverage."""
        return tuple(
            (source, start, end, status)
            for source, version in sorted(self.versions.items())
            for start, end, status in version.coverage
        )


_pinned: ContextVar[PinnedRelease | None] = ContextVar("pinned_market_release", default=None)


def _ranges(coverage_json: str) -> tuple[tuple[date, date, str], ...]:
    status = {"verified": "complete", "partial": "partial"}
    return tuple(
        (date.fromisoformat(r["from"]), date.fromisoformat(r["to"]), status[r["status"]])
        for r in (json.loads(coverage_json or "{}").get("ranges") or [])
    )


async def _published_version(source: str) -> SourceVersion | None:
    served = RELEASED_SOURCES[source]
    rows = await query(
        "SELECT release_id, coverage_json FROM market_release_ledger FINAL "
        "WHERE dataset = {dataset:String} AND state = 'published' "
        "ORDER BY started_at DESC, updated_at DESC LIMIT 1",
        {"dataset": served},
    )
    if not rows or not _RELEASE_ID.match(str(rows[0]["release_id"])):
        return None
    release_id = str(rows[0]["release_id"])
    table = f"{served}__r{release_id}"
    exists = await query(
        "SELECT count() AS n FROM system.tables "
        "WHERE database = currentDatabase() AND name = {table:String}",
        {"table": table},
    )
    if not exists or not int(exists[0]["n"]):
        return None
    return SourceVersion(source, release_id, table, _ranges(str(rows[0]["coverage_json"])))


@asynccontextmanager
async def pin_market_release(
    sources: tuple[str, ...] = ("sales", "rentals"),
) -> AsyncIterator[PinnedRelease]:
    """Pin the published immutable version of each source for this context.

    A source without a published version is listed in ``missing``; queries on it inside the
    pin raise ReleaseUnavailable instead of reading the mutable serving views.
    """
    versions, missing = {}, []
    for source in sources:
        version = await _published_version(source)
        if version is None:
            missing.append(source)
        else:
            versions[source] = version
    now = datetime.now(UTC)
    pinned = PinnedRelease(versions, now, now + MAX_LEASE, tuple(missing))
    token = _pinned.set(pinned)
    try:
        yield pinned
    finally:
        _pinned.reset(token)


def current_pin() -> PinnedRelease | None:
    return _pinned.get()


def pinned_table(source: str) -> str | None:
    """The immutable table to read for ``source`` in this context, or None when unpinned."""
    pinned = _pinned.get()
    if pinned is None:
        return None
    if datetime.now(UTC) > pinned.expires_at:
        raise ReleaseLeaseExpired(f"market release pin expired at {pinned.expires_at}")
    version = pinned.versions.get(source)
    if version is None:
        raise ReleaseUnavailable(f"no published immutable {source} release is available")
    return version.table
