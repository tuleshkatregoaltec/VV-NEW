"""Publish a canonical market release only after it passes validation, and recover safely.

One writer at a time: every build, rollback and recovery holds ``market_release_lock``, a
table whose atomic CREATE is the mutex (ClickHouse has no advisory locks). Sales and rentals
share it because the rental build reads the served sales release.

A run builds every table of a release (match ledger, then events) into run-unique staging
tables stamped with the release id (table comment and the ``release_id`` column), evaluates
the blocking checks, a per-day completeness comparison, source presence and a served-row
floor against those staging tables, and only then promotes them. ``market_release_ledger``
records each release's inputs, check results and state.

Promotion exchanges the match ledger first and the reader-facing events table last, so a
release is published exactly when its events table is exchanged. The replaced release is
kept as ``<table>__previous`` (one release retained). ClickHouse has no multi-table
transaction; instead every table names its release, cleanup only drops tables that hold the
failed release itself, and ``recover`` (run before every build) reconciles the ledger,
realigns a match ledger left ahead of its events and returns a displaced good release to
``__previous``. ``rollback`` swaps a dataset back to its previous release.

Immutable versions for readers: just before its events table goes live, every release is
also kept as ``<events table>__r<release_id>``, a hard-linked copy of the staged parts (no
data copy) that nothing writes to again. A reader that must give one consistent answer
across several queries (a chat turn, an MCP call, a report) pins the published versions and
reads only those tables (backend ``app.market.releases``). Pins hold at most
``MAX_READER_LEASE``; a superseded version is dropped only ``RELEASE_RETENTION`` after it
stopped being published, so a version can never disappear under a reader that pinned it
while it was current. Versions of releases that never went live are dropped at once.
"""

from __future__ import annotations

import json
import math
import os
import re
import socket
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from holocron.market import transforms
from holocron.market.checks import CHECKS, MarketCheck, dxbi_input_rows, evaluate
from holocron.platform.clickhouse import ClickHouseClient, sql_string
from holocron.platform.clickhouse_schema import ensure_schema
from holocron.platform.load_windows import coverage

# A served-row floor is a coarse guard only: DLD does not delete registered records, so a
# release serving clearly fewer rows than the live one means truncated input. It cannot prove
# completeness (a lost week hides inside growth elsewhere); _day_losses checks each day.
MIN_SERVED_ROW_RATIO = 0.99
# A day the live release serves at least this many rows must keep at least half of them.
DAY_LOSS_MIN_ROWS = 20
DAY_LOSS_RATIO = 0.5

LOCK_TABLE = "market_release_lock"
# ponytail: stale-lock break is check-then-drop, not compare-and-swap. Safe while the TTL is
# far above the longest build (~2 min measured, rentals included); add a CAS store if two
# operators ever race to break the same stale lock.
LOCK_TTL = timedelta(hours=6)

# Tables of each served dataset in promotion order: the reader-facing events table last.
GROUPS = {
    "market_sale_events": ("market_sale_matches", "market_geo_crosswalk", "market_sale_events"),
    "market_rental_events": ("market_rental_matches", "market_rental_events"),
}
_EVENT_DATE = {
    # DLD's registration date where the row has one: instance_date may carry DXBI's date
    # (one business day earlier on 21% of matched sales), which would read as a lost day.
    "market_sale_events": "toDate(ifNull(dld_instance_date, instance_date))",
    "market_rental_events": "toDate(registration_date)",
}
_GOOD_STATES = {"published", "superseded", "rolled_back"}
# Must equal backend app.market.releases.MAX_LEASE; retention must stay far above it.
MAX_READER_LEASE = timedelta(minutes=30)
RELEASE_RETENTION = timedelta(hours=24)
# The bronze table and date each dataset's metrics count by; coverage is claimed on that date.
_COVERAGE_BASIS = {
    "market_sale_events": ("dld_od_transactions_bronze", "instance_date"),
    "market_rental_events": ("dld_od_rents_bronze", "registration_date"),
}


class MarketReleaseRejected(RuntimeError):
    """The staged release failed validation; the served release was not changed."""


class MarketReleaseBusy(RuntimeError):
    """Another writer holds the market release lock."""


def render(sql: str, names: dict[str, str]) -> str:
    """Point every whole-word reference to a served table at its staging table."""
    pattern = r"\b(" + "|".join(re.escape(name) for name in names) + r")\b"
    return re.sub(pattern, lambda match: names[match.group(1)], sql)


def _now() -> datetime:
    return datetime.now(UTC)


def _scalar(clickhouse: ClickHouseClient, sql: str) -> Any:
    return clickhouse.query(sql).result_rows[0][0]


def _owner() -> str:
    return os.environ.get("DAGSTER_RUN_ID") or f"{socket.gethostname()}:{os.getpid()}"


# --- lock -----------------------------------------------------------------------------------


def _lock_comment(clickhouse: ClickHouseClient) -> str:
    rows = clickhouse.query(
        "SELECT comment FROM system.tables "
        f"WHERE database = currentDatabase() AND name = {sql_string(LOCK_TABLE)}"
    ).result_rows
    return str(rows[0][0]) if rows else ""


@contextmanager
def release_lock(clickhouse: ClickHouseClient, *, owner: str) -> Iterator[None]:
    token = f"{owner}|{_now().isoformat()}"
    for _ in range(2):
        try:
            clickhouse.command(
                f"CREATE TABLE `{LOCK_TABLE}` (owner String) ENGINE = Memory "
                f"COMMENT {sql_string(token)}"
            )
            break
        except Exception as exc:
            if "already exists" not in str(exc).lower():
                raise
            holder, _, acquired = _lock_comment(clickhouse).partition("|")
            if acquired and _now() - datetime.fromisoformat(acquired) > LOCK_TTL:
                clickhouse.command(f"DROP TABLE IF EXISTS `{LOCK_TABLE}`")
                continue
            raise MarketReleaseBusy(
                f"market release lock held by {holder or 'unknown'} since {acquired or '?'}"
            ) from exc
    else:
        raise MarketReleaseBusy("market release lock could not be acquired")
    try:
        yield
    finally:
        if _lock_comment(clickhouse) == token:
            clickhouse.command(f"DROP TABLE IF EXISTS `{LOCK_TABLE}`")


# --- ledger ---------------------------------------------------------------------------------

_LEDGER_COLUMNS = (
    "release_id", "dataset", "state", "owner", "started_at", "updated_at", "served_rows",
    "previous_release_id", "depends_on_json", "inputs_json", "checks_json", "detail",
    "coverage_json",
)  # fmt: skip


def _ledger(clickhouse: ClickHouseClient, dataset: str) -> dict[str, dict[str, Any]]:
    result = clickhouse.query(
        f"SELECT {', '.join(_LEDGER_COLUMNS)} FROM market_release_ledger FINAL "
        f"WHERE dataset = {sql_string(dataset)}"
    )
    return {row[0]: dict(zip(_LEDGER_COLUMNS, row, strict=True)) for row in result.result_rows}


def _record(clickhouse: ClickHouseClient, entry: dict[str, Any], **changes: Any) -> None:
    entry.update(changes, updated_at=_now())
    clickhouse.insert_rows(
        table="market_release_ledger",
        rows=[[entry[column] for column in _LEDGER_COLUMNS]],
        column_names=_LEDGER_COLUMNS,
    )


def _new_entry(release_id: str, dataset: str, owner: str) -> dict[str, Any]:
    now = _now()
    return {
        "release_id": release_id, "dataset": dataset, "state": "building", "owner": owner,
        "started_at": now, "updated_at": now, "served_rows": 0, "previous_release_id": "",
        "depends_on_json": "{}", "inputs_json": "{}", "checks_json": "[]", "detail": "",
        "coverage_json": "{}",
    }  # fmt: skip


# --- table identity -------------------------------------------------------------------------


def _market_tables(clickhouse: ClickHouseClient) -> dict[str, str]:
    """Market table name -> release id (its comment; '' for tables built before releases)."""
    rows = clickhouse.query(
        "SELECT name, comment FROM system.tables "
        "WHERE database = currentDatabase() AND name LIKE 'market\\_%'"
    ).result_rows
    return {str(name): str(comment) for name, comment in rows}


def _staging_names(tables: dict[str, str], table: str) -> list[str]:
    return [name for name in tables if name.startswith(f"{table}__staging_")]


def _promote(clickhouse: ClickHouseClient, table: str, staging: str, has_previous: bool) -> None:
    """Swap staging in; the replaced release becomes __previous; the older one is dropped.

    After the first EXCHANGE the staging name holds the replaced (good) release, so nothing
    may drop it by name until it is renamed: cleanup and recovery go by release id instead.
    """
    clickhouse.command(f"EXCHANGE TABLES `{table}` AND `{staging}`")
    if has_previous:
        clickhouse.command(f"EXCHANGE TABLES `{staging}` AND `{table}__previous`")
        clickhouse.command(f"DROP TABLE `{staging}`")
    else:
        clickhouse.command(f"RENAME TABLE `{staging}` TO `{table}__previous`")


def version_table(served: str, release_id: str) -> str:
    return f"{served}__r{release_id}"


def _snapshot(clickhouse: ClickHouseClient, source: str, target: str, release_id: str) -> None:
    """Immutable copy of ``source`` as ``target``: parts are hard-linked, not rewritten."""
    clickhouse.command(f"DROP TABLE IF EXISTS `{target}`")
    clickhouse.command(f"CREATE TABLE `{target}` AS `{source}`")
    clickhouse.command(f"ALTER TABLE `{target}` MODIFY COMMENT {sql_string(release_id)}")
    partitions = clickhouse.query(
        "SELECT DISTINCT partition_id FROM system.parts WHERE active "
        f"AND database = currentDatabase() AND table = {sql_string(source)}"
    ).result_rows
    for (partition_id,) in partitions:
        clickhouse.command(
            f"ALTER TABLE `{target}` ATTACH PARTITION ID {sql_string(partition_id)} FROM `{source}`"
        )
    copied = _scalar(clickhouse, f"SELECT count() FROM `{target}`")
    expected = _scalar(clickhouse, f"SELECT count() FROM `{source}`")
    if copied != expected:
        raise RuntimeError(f"{target}: snapshot holds {copied} of {expected} rows")


def cleanup_versions(clickhouse: ClickHouseClient, *, now: datetime | None = None) -> list[str]:
    """Drop immutable versions no reader can still hold. Caller holds the lock."""
    now = now or _now()
    dropped = []
    tables = _market_tables(clickhouse)
    for served in GROUPS:
        ledger = _ledger(clickhouse, served)
        prefix = f"{served}__r"
        for name in [n for n in tables if n.startswith(prefix)]:
            entry = ledger.get(name.removeprefix(prefix))
            state = entry["state"] if entry else ""
            if state == "published" or tables.get(served) == name.removeprefix(prefix):
                continue
            if (
                entry
                and state in {"superseded", "rolled_back"}
                and (now - entry["updated_at"].replace(tzinfo=UTC) < RELEASE_RETENTION)
            ):
                continue
            clickhouse.command(f"DROP TABLE IF EXISTS `{name}`")
            dropped.append(f"{name}: dropped ({state or 'unknown'} release)")
    return dropped


def _drop_own_staging(clickhouse: ClickHouseClient, release_id: str) -> None:
    for name, comment in _market_tables(clickhouse).items():
        if "__staging_" in name and comment == release_id:
            clickhouse.command(f"DROP TABLE IF EXISTS `{name}`")


# --- recovery -------------------------------------------------------------------------------


def recover(clickhouse: ClickHouseClient) -> list[str]:
    """Reconcile tables and ledger after an interrupted publication. Caller holds the lock."""
    actions: list[str] = []
    for served, group in GROUPS.items():
        tables = _market_tables(clickhouse)
        ledger = _ledger(clickhouse, served)
        live = tables.get(served, "")

        # A release stuck in 'publishing' is published iff its events table went live; one
        # stuck in 'building' died mid-build (recovery runs under the lock, so none is live).
        for release_id, entry in ledger.items():
            if entry["state"] == "building":
                _record(clickhouse, entry, state="failed", detail="interrupted during build")
                actions.append(f"{served}: interrupted build {release_id} marked failed")
            elif entry["state"] == "publishing":
                state = "published" if release_id == live else "failed"
                _record(clickhouse, entry, state=state, detail="reconciled by recovery")
                actions.append(f"{served}: release {release_id} marked {state}")
                if state == "published" and entry["previous_release_id"] in ledger:
                    _record(clickhouse, ledger[entry["previous_release_id"]], state="superseded")

        # A table exchanged ahead of its events goes back to the served release, which sits in
        # __previous, or still in a staging name if the crash came right after the exchange.
        for table in group[:-1]:
            if tables.get(table, "") == live:
                continue
            holder = next(
                (
                    name
                    for name in (f"{table}__previous", *_staging_names(tables, table))
                    if name in tables and tables[name] == live
                ),
                None,
            )
            if holder:
                clickhouse.command(f"EXCHANGE TABLES `{table}` AND `{holder}`")
                actions.append(f"{table}: realigned to served release {live or '(legacy)'}")
        tables = _market_tables(clickhouse)

        # Keep the newest good non-live release as __previous; drop every other leftover.
        for table in group:
            previous = f"{table}__previous"
            held = tables.get(previous)
            if held and ledger.get(held, {}).get("state") not in _GOOD_STATES:
                # e.g. the release a realigned match ledger had run ahead with: never roll to it.
                clickhouse.command(f"DROP TABLE `{previous}`")
                actions.append(f"{previous}: dropped unpublished release {held}")
                tables = _market_tables(clickhouse)
            candidates = [
                name
                for name in (previous, *_staging_names(tables, table))
                if name in tables
                and tables[name] != tables.get(table, "")
                and (ledger.get(tables[name], {}).get("state") in _GOOD_STATES)
            ]
            keep = max(
                candidates, key=lambda name: ledger[tables[name]]["started_at"], default=None
            )
            if keep and keep != previous:
                if previous in tables:
                    clickhouse.command(f"EXCHANGE TABLES `{keep}` AND `{previous}`")
                else:
                    clickhouse.command(f"RENAME TABLE `{keep}` TO `{previous}`")
                actions.append(f"{table}: release {tables[keep]} restored as __previous")
            for name in _staging_names(_market_tables(clickhouse), table):
                clickhouse.command(f"DROP TABLE IF EXISTS `{name}`")
                actions.append(f"{name}: dropped leftover staging")
    return actions


def rollback(clickhouse: ClickHouseClient, served: str, *, owner: str | None = None) -> str:
    """Serve the retained previous release of ``served`` again; returns its release id."""
    with release_lock(clickhouse, owner=owner or _owner()):
        recover(clickhouse)
        tables = _market_tables(clickhouse)
        group = GROUPS[served]
        target = tables.get(f"{served}__previous")
        if target is None or any(tables.get(f"{t}__previous") != target for t in group):
            raise RuntimeError(f"{served}: no complete previous release to roll back to")
        current = tables.get(served, "")
        for table in group:
            # A table retained from before release ids existed lacks the column readers select.
            clickhouse.command(
                f"ALTER TABLE `{table}__previous` ADD COLUMN IF NOT EXISTS release_id "
                "LowCardinality(String) DEFAULT ''"
            )
        for table in group:
            clickhouse.command(f"EXCHANGE TABLES `{table}` AND `{table}__previous`")
        if version_table(served, target) not in tables:
            # Its version may have aged out of retention; readers can only pin what exists.
            _snapshot(clickhouse, served, version_table(served, target), target)
        ledger = _ledger(clickhouse, served)
        if current in ledger:
            _record(clickhouse, ledger[current], state="rolled_back", detail="operator rollback")
        if target in ledger:
            _record(clickhouse, ledger[target], state="published", detail="restored by rollback")
        return target


# --- validation -----------------------------------------------------------------------------


def _check_results(
    clickhouse: ClickHouseClient,
    checks: tuple[MarketCheck, ...],
    staged: dict[str, str],
    has_dxbi: bool,
) -> tuple[list[dict[str, Any]], list[str]]:
    results, failures = [], []
    for check in checks:
        if not check.blocking:
            continue
        passed, value = evaluate(clickhouse, replace(check, sql=render(check.sql, staged)))
        unmeasured = math.isnan(value)
        if unmeasured and check.needs_dxbi and not has_dxbi:
            # Nothing to measure because no DXBI evidence is loaded at all: recorded, not
            # waived silently. A DXBI ratio that is unmeasurable *with* DXBI input fails.
            status = "not_applicable"
        else:
            status = "passed" if passed else "failed"
        if status == "failed":
            shown = "unmeasurable" if unmeasured else f"{value:g}"
            failures.append(f"{check.name}={shown} (needs {check.op} {check.threshold:g})")
        results.append(
            {"name": check.name, "value": None if unmeasured else value, "status": status}
        )
    return results, failures


def _served_by_source(clickhouse: ClickHouseClient, table: str) -> dict[str, int]:
    rows = clickhouse.query(
        f"SELECT source_system, count() FROM `{table}` WHERE is_served = 1 GROUP BY source_system"
    ).result_rows
    return {str(source): int(count) for source, count in rows}


def _day_losses(clickhouse: ClickHouseClient, served: str, staging: str) -> list[str]:
    """Days the live release serves that the staged release lost most of (a missing window)."""
    day = _EVENT_DATE[served]
    rows = clickhouse.query(
        f"""
        SELECT live.d, live.n, staged.n
        FROM (SELECT {day} AS d, count() AS n FROM `{served}` WHERE is_served = 1 GROUP BY d)
            AS live
        LEFT JOIN (SELECT {day} AS d, count() AS n FROM `{staging}` WHERE is_served = 1
                   GROUP BY d) AS staged USING d
        WHERE live.n >= {DAY_LOSS_MIN_ROWS} AND staged.n < live.n * {DAY_LOSS_RATIO}
        ORDER BY live.d
        """
    ).result_rows
    return [f"{d}: {new} of {old}" for d, old, new in rows]


def _validate(
    clickhouse: ClickHouseClient,
    *,
    served: str,
    staged: dict[str, str],
    checks: tuple[MarketCheck, ...],
    has_dxbi: bool,
) -> tuple[list[dict[str, Any]], list[str], int]:
    results, failures = _check_results(clickhouse, checks, staged, has_dxbi)
    live_sources = _served_by_source(clickhouse, served)
    new_sources = _served_by_source(clickhouse, staged[served])
    lost = sorted(set(live_sources) - set(new_sources))
    if lost:
        failures.append(f"sources_lost={','.join(lost)} (served by the live release)")
    live, new = sum(live_sources.values()), sum(new_sources.values())
    if new < live * MIN_SERVED_ROW_RATIO:
        failures.append(f"served_rows={new} (live release serves {live})")
    losses = _day_losses(clickhouse, served, staged[served])
    if losses:
        failures.append(f"days_lost={len(losses)} (first: {'; '.join(losses[:5])})")
    return results, failures, new


def _inputs(clickhouse: ClickHouseClient, served: str) -> dict[str, Any]:
    """What the release was built from, so a published number can be traced to its inputs."""
    bronze = (
        "dld_od_transactions_bronze" if served == "market_sale_events" else "dld_od_rents_bronze"
    )
    dxbi = "dxbi_sales_unit_events" if served == "market_sale_events" else "dxbi_rental_events"
    dld = clickhouse.query(
        f"SELECT source_system, count(), toString(max(scraped_at)), uniqExact(run_id) "
        f"FROM {bronze} GROUP BY source_system"
    ).result_rows
    dxbi_rows, dxbi_loaded = clickhouse.query(
        f"SELECT count(), toString(max(loaded_at)) FROM {dxbi}"
    ).result_rows[0]
    return {
        bronze: {
            str(source): {"rows": int(rows), "latest_scraped_at": latest, "runs": int(runs)}
            for source, rows, latest, runs in dld
        },
        dxbi: {"rows": int(dxbi_rows), "latest_loaded_at": dxbi_loaded},
    }


def _release_coverage(
    clickhouse: ClickHouseClient, served: str, entry: dict[str, Any]
) -> dict[str, Any]:
    bronze_table, date_field = _COVERAGE_BASIS[served]
    return coverage(
        clickhouse,
        bronze_table=bronze_table,
        date_field=date_field,
        loaded_before=entry["started_at"],
    )


# --- publication ----------------------------------------------------------------------------


def publish_validated(
    *,
    clickhouse: ClickHouseClient,
    builds: dict[str, tuple[str, ...]],
    served_table: str,
    checks: tuple[MarketCheck, ...],
    owner: str | None = None,
    depends_on: tuple[str, ...] = (),
) -> dict[str, int | str]:
    owner = owner or _owner()
    with release_lock(clickhouse, owner=owner):
        # Runtime DDL under the lock: two publishers installing views at once collide.
        # ponytail: other writers (bronze loaders) still run ensure_schema unlocked until
        # versioned warehouse migrations replace it (PIPELINE_STANDARDS S4.2).
        ensure_schema(clickhouse=clickhouse)
        recovered = recover(clickhouse)
        release_id = uuid4().hex[:12]
        staged = {table: f"{table}__staging_{release_id}" for table in builds}
        tables = _market_tables(clickhouse)
        entry = _new_entry(release_id, served_table, owner)
        _record(
            clickhouse,
            entry,
            previous_release_id=tables.get(served_table, ""),
            depends_on_json=json.dumps({name: tables.get(name, "") for name in depends_on}),
            inputs_json=json.dumps(_inputs(clickhouse, served_table)),
            # Windows loaded before this build started: the days this release can vouch for.
            coverage_json=json.dumps(_release_coverage(clickhouse, served_table, entry)),
            detail="; ".join(recovered),
        )
        try:
            for table, statements in builds.items():
                clickhouse.command(f"CREATE TABLE `{staged[table]}` AS `{table}`")
                clickhouse.command(
                    f"ALTER TABLE `{staged[table]}` MODIFY COMMENT {sql_string(release_id)}"
                )
                clickhouse.command(
                    f"ALTER TABLE `{staged[table]}` MODIFY COLUMN release_id "
                    f"LowCardinality(String) DEFAULT {sql_string(release_id)}"
                )
                for statement in statements:
                    clickhouse.command(render(statement, staged))
                # A fresh INSERT ... SELECT leaves dozens of parts; the backend's ordered LIMIT
                # queries read every part, so the table is merged before it goes live.
                clickhouse.command(f"OPTIMIZE TABLE `{staged[table]}` FINAL")

            results, failures, served_rows = _validate(
                clickhouse,
                served=served_table,
                staged=staged,
                checks=checks,
                has_dxbi=dxbi_input_rows(clickhouse, served_table) > 0,
            )
            if failures:
                detail = "; ".join(failures)
                _record(clickhouse, entry, state="rejected", checks_json=json.dumps(results),
                        served_rows=served_rows, detail=detail)  # fmt: skip
                raise MarketReleaseRejected(
                    f"{served_table} release {release_id} rejected, served release unchanged: "
                    + detail
                )

            _record(clickhouse, entry, state="publishing", checks_json=json.dumps(results),
                    served_rows=served_rows)  # fmt: skip
            # The pinnable immutable version exists before the release can be read at all.
            _snapshot(
                clickhouse,
                staged[served_table],
                version_table(served_table, release_id),
                release_id,
            )
            for table in builds:
                _promote(clickhouse, table, staged[table], f"{table}__previous" in tables)
            _record(clickhouse, entry, state="published")
            previous = _ledger(clickhouse, served_table).get(entry["previous_release_id"])
            if previous:
                _record(clickhouse, previous, state="superseded")
        except Exception as exc:
            # A failure while 'publishing' is settled by recover() on the next run: the events
            # table may already be live, and only the tables' release ids can tell.
            if entry["state"] == "building":
                _record(clickhouse, entry, state="failed", detail=repr(exc)[:2000])
            raise
        finally:
            _drop_own_staging(clickhouse, release_id)
        cleanup_versions(clickhouse)

    rows = clickhouse.query(
        f"SELECT match_status, count() FROM `{served_table}` GROUP BY match_status"
    ).result_rows
    return {
        "release_id": release_id,
        "row_count": sum(int(count) for _, count in rows),
        "served_rows": served_rows,
        **{str(status): int(count) for status, count in rows},
    }


def _checks_for(asset: str) -> tuple[MarketCheck, ...]:
    return tuple(check for check in CHECKS if check.asset == asset)


def materialize_market_sale_events(
    *, clickhouse: ClickHouseClient, owner: str | None = None
) -> dict[str, int | str]:
    return publish_validated(
        clickhouse=clickhouse,
        builds={
            "market_sale_matches": (transforms._SALE_MATCHES_SQL,),
            # Rentals read the served crosswalk, so both datasets place rows the same way.
            "market_geo_crosswalk": transforms._GEO_CROSSWALK_STATEMENTS,
            "market_sale_events": (
                transforms._SALE_EVENTS_DLD_SQL,
                transforms._SALE_EVENTS_DXBI_SQL,
            ),
        },
        served_table="market_sale_events",
        checks=_checks_for("market_sale_events"),
        owner=owner,
    )


def materialize_market_rental_events(
    *, clickhouse: ClickHouseClient, owner: str | None = None
) -> dict[str, int | str]:
    return publish_validated(
        clickhouse=clickhouse,
        builds={
            "market_rental_matches": transforms._RENTAL_MATCH_STATEMENTS,
            "market_rental_events": (
                transforms._RENTAL_EVENTS_DLD_SQL,
                transforms._RENTAL_EVENTS_DXBI_SQL,
            ),
        },
        served_table="market_rental_events",
        checks=_checks_for("market_rental_events"),
        owner=owner,
        # Rental areas are learned from the served sales release; the lock keeps it fixed.
        depends_on=("market_sale_events",),
    )


def main(argv: list[str] | None = None) -> int:
    """Operator entry point: ``python -m holocron.market.release recover|rollback <table>``."""
    import argparse

    from holocron.platform.settings import Settings

    parser = argparse.ArgumentParser(prog="python -m holocron.market.release")
    parser.add_argument("command", choices=("recover", "rollback"))
    parser.add_argument("table", nargs="?", choices=tuple(GROUPS))
    args = parser.parse_args(argv)
    clickhouse = ClickHouseClient(Settings())
    if args.command == "rollback":
        if not args.table:
            parser.error("rollback needs the served table")
        print(f"{args.table} now serves release {rollback(clickhouse, args.table)}")
    else:
        with release_lock(clickhouse, owner=_owner()):
            actions = recover(clickhouse) + cleanup_versions(clickhouse)
            for action in actions or ["nothing to recover"]:
                print(action)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
