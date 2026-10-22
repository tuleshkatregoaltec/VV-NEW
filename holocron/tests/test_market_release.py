"""The canonical market layer validates a staged release before it replaces the served one,
allows one writer at a time and recovers an interrupted publication without losing the
last good release."""

from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from holocron.market import release


def test_render_retargets_whole_table_names_only() -> None:
    names = {"market_sale_events": "market_sale_events__staging_x"}
    assert (
        release.render("FROM `market_sale_events` JOIN market_sale_events_old", names)
        == "FROM `market_sale_events__staging_x` JOIN market_sale_events_old"
    )


class _Tables:
    """Answers the system.tables listing and records commands."""

    def __init__(self, tables: dict[str, str]) -> None:
        self.tables = tables
        self.commands: list[str] = []

    def query(self, sql: str) -> Any:
        return SimpleNamespace(result_rows=list(self.tables.items()))

    def command(self, sql: str) -> None:
        self.commands.append(sql)


def test_cleanup_never_drops_a_staging_name_that_holds_another_release() -> None:
    # After EXCHANGE, the new release's staging name holds the replaced (good) release. If the
    # run then fails, cleanup by name would delete the only copy of the last good release.
    client = _Tables(
        {
            "market_sale_matches__staging_new": "new",  # never promoted: ours, droppable
            "market_sale_events__staging_new": "old",  # displaced good release: keep
            "market_sale_events": "new",
        }
    )
    release._drop_own_staging(cast(Any, client), "new")
    assert client.commands == ["DROP TABLE IF EXISTS `market_sale_matches__staging_new`"]


# --- real ClickHouse -----------------------------------------------------------------------

_LIVE = os.environ.get("HOLOCRON_CLICKHOUSE_INTEGRATION") == "1"
live = pytest.mark.skipif(
    not _LIVE, reason="set HOLOCRON_CLICKHOUSE_INTEGRATION=1 with local ClickHouse"
)


def _settings(database: str) -> Any:
    host = os.environ.get("CLICKHOUSE_HOST", "localhost")
    if host not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("ClickHouse integration requires a local service")
    return SimpleNamespace(
        clickhouse_host=host,
        clickhouse_port=int(os.environ.get("CLICKHOUSE_PORT", "8123")),
        clickhouse_user=os.environ.get("CLICKHOUSE_USER", "default"),
        clickhouse_password=os.environ.get("CLICKHOUSE_PASSWORD", ""),
        clickhouse_database=database,
        clickhouse_secure=False,
    )


@pytest.fixture
def database():
    import clickhouse_connect

    name = "holocron_release_test_" + uuid4().hex
    settings = _settings("default")
    admin = clickhouse_connect.get_client(
        host=settings.clickhouse_host,
        port=settings.clickhouse_port,
        username=settings.clickhouse_user,
        password=settings.clickhouse_password,
    )
    admin.command(f"CREATE DATABASE {name}")
    try:
        yield name
    finally:
        admin.command(f"DROP DATABASE {name} SYNC")


def _client(database: str):
    from holocron.platform.clickhouse import ClickHouseClient

    return ClickHouseClient(_settings(database))


@pytest.fixture
def warehouse(database):
    from holocron.platform.clickhouse_schema import ensure_schema

    client = _client(database)
    ensure_schema(clickhouse=client)
    return client


_SALE_COLUMNS = [
    "row_key", "run_id", "transaction_number", "instance_date", "group_en",
    "trans_value", "procedure_area", "area_en", "is_offplan_en", "source_system", "area_id",
]  # fmt: skip
MARSA_DUBAI_ID = 330  # DLD area_id: the evidence that makes "Marsa Dubai" a district


def _dld_sales(client, rows: list[tuple[str, str, int]], day: datetime | None = None) -> None:
    client.insert_rows(
        table="dld_od_transactions_bronze",
        column_names=_SALE_COLUMNS,
        rows=[
            (f"r-{number}", "run", number, day or datetime(2026, 9, 10), group, price, 80.0,
             "Marsa Dubai", "Ready", "dld_open_data", MARSA_DUBAI_ID)
            for number, group, price in rows
        ],
    )  # fmt: skip


def _daily_sales(client, days: list[date], per_day: int, prefix: str = "D") -> None:
    client.insert_rows(
        table="dld_od_transactions_bronze",
        column_names=_SALE_COLUMNS,
        rows=[
            (f"r-{prefix}{d}-{i}", "run", f"{prefix}{d}-{i}", datetime.combine(d, datetime.min.time()),
             "Sales", 1_000_000 + i, 80.0, "Marsa Dubai", "Ready", "dld_open_data", MARSA_DUBAI_ID)
            for d in days
            for i in range(per_day)
        ],
    )  # fmt: skip


def _served(client, table: str = "market_sale_events") -> int:
    return int(client.query(f"SELECT count() FROM {table} WHERE is_served = 1").result_rows[0][0])


def _tables(client) -> dict[str, str]:
    return release._market_tables(client)


def _ledger(client) -> dict[str, str]:
    return {rid: e["state"] for rid, e in release._ledger(client, "market_sale_events").items()}


@live
def test_invalid_or_partial_staged_release_keeps_last_good_release(warehouse) -> None:
    good = [(f"T{i}", "Sales", 1_000_000 + i) for i in range(100)]
    _dld_sales(warehouse, good)
    first = release.materialize_market_sale_events(clickhouse=warehouse)
    assert _served(warehouse) == 100 and first["served_rows"] == 100

    # Partial upstream: half the DLD bronze disappears. The release is refused.
    warehouse.command("TRUNCATE TABLE dld_od_transactions_bronze")
    _dld_sales(warehouse, good[:50])
    with pytest.raises(release.MarketReleaseRejected, match="served_rows=50"):
        release.materialize_market_sale_events(clickhouse=warehouse)
    assert _served(warehouse) == 100

    # Invalid vocabulary in an otherwise complete input is refused too.
    _dld_sales(warehouse, good[50:] + [("T999", "Unknown group", 5)])
    with pytest.raises(release.MarketReleaseRejected, match="sale_label_vocabulary=1"):
        release.materialize_market_sale_events(clickhouse=warehouse)
    assert _served(warehouse) == 100

    # Valid new data publishes; the replaced release is retained once; no staging is left.
    warehouse.command(
        "ALTER TABLE dld_od_transactions_bronze DELETE WHERE transaction_number = 'T999' SETTINGS mutations_sync = 1"
    )
    _dld_sales(warehouse, [("T100", "Sales", 2_000_000)])
    second = release.materialize_market_sale_events(clickhouse=warehouse)
    assert _served(warehouse) == 101
    tables = _tables(warehouse)
    assert tables["market_sale_events__previous"] == first["release_id"]
    assert tables["market_sale_matches__previous"] == first["release_id"]
    assert tables["market_sale_events"] == second["release_id"]
    assert not any("__staging_" in name for name in tables)
    assert _served(warehouse, "market_sale_events__previous") == 100
    # Readers can tell which release answered: every served row carries its id.
    ids = warehouse.query("SELECT DISTINCT release_id FROM market_sale_events").result_rows
    assert ids == [(second["release_id"],)]

    # The durable record explains every attempt.
    states = sorted(_ledger(warehouse).values())
    assert states == ["published", "rejected", "rejected", "superseded"]
    entry = release._ledger(warehouse, "market_sale_events")[str(second["release_id"])]
    assert entry["previous_release_id"] == first["release_id"]
    inputs = json.loads(entry["inputs_json"])
    assert inputs["dld_od_transactions_bronze"]["dld_open_data"]["rows"] == 101


@live
def test_missing_week_between_populated_dates_is_rejected_despite_row_floor(warehouse) -> None:
    start = date(2026, 8, 1)
    days = [start + timedelta(days=i) for i in range(21)]
    _daily_sales(warehouse, days, per_day=30)
    release.materialize_market_sale_events(clickhouse=warehouse)
    assert _served(warehouse) == 630

    # The middle week vanishes upstream while later days grow: more rows than before, so a
    # count floor passes, but seven previously served days are gone.
    warehouse.command(
        "ALTER TABLE dld_od_transactions_bronze DELETE WHERE toDate(instance_date) "
        "BETWEEN '2026-08-08' AND '2026-08-14' SETTINGS mutations_sync = 1"
    )
    _daily_sales(warehouse, [date(2026, 8, 22)], per_day=300, prefix="E")
    with pytest.raises(release.MarketReleaseRejected, match=r"days_lost=7 \(first: 2026-08-08"):
        release.materialize_market_sale_events(clickhouse=warehouse)
    assert _served(warehouse) == 630


@live
def test_unmeasurable_dxbi_check_fails_only_when_dxbi_input_exists(warehouse) -> None:
    _dld_sales(warehouse, [(f"T{i}", "Sales", 1_000_000 + i) for i in range(30)])
    published = release.materialize_market_sale_events(clickhouse=warehouse)
    checks = json.loads(
        release._ledger(warehouse, "market_sale_events")[str(published["release_id"])][
            "checks_json"
        ]
    )
    # No DXBI evidence at all: DXBI ratios are recorded as not applicable, not as passed.
    assert {c["name"]: c["status"] for c in checks}["sale_pair_size_agreement"] == "not_applicable"

    # DXBI evidence exists but nothing pairs with DLD: the same ratio is now a failure.
    warehouse.insert_rows(
        table="dxbi_sales_unit_events",
        column_names=["sale_key", "transaction_date", "sale_amount_aed", "size_sqft", "area_name"],
        rows=[(f"X{i}", date(2020, 1, 1), 7, 500.0, "Dubai Marina") for i in range(3)],
    )
    with pytest.raises(
        release.MarketReleaseRejected, match="sale_pair_size_agreement=unmeasurable"
    ):
        release.materialize_market_sale_events(clickhouse=warehouse)


@live
def test_concurrent_publication_is_refused_while_another_writer_holds_the_lock(
    database, warehouse
) -> None:
    _dld_sales(warehouse, [(f"T{i}", "Sales", 1_000_000 + i) for i in range(40)])
    release.materialize_market_sale_events(clickhouse=warehouse)
    _dld_sales(warehouse, [("T40", "Sales", 3_000_000)])

    held, done = threading.Event(), threading.Event()

    def other_writer() -> None:
        with release.release_lock(_client(database), owner="other-run"):
            held.set()
            done.wait(30)

    thread = threading.Thread(target=other_writer)
    thread.start()
    try:
        assert held.wait(30)
        with pytest.raises(release.MarketReleaseBusy, match="other-run"):
            release.materialize_market_sale_events(clickhouse=warehouse)
        assert _served(warehouse) == 40
    finally:
        done.set()
        thread.join()

    # Two real attempts at once: exactly one publishes, the other is refused, data is consistent.
    outcomes: list[str] = []

    def attempt() -> None:
        try:
            release.materialize_market_sale_events(clickhouse=_client(database))
            outcomes.append("published")
        except release.MarketReleaseBusy:
            outcomes.append("busy")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(outcomes) in (["busy", "published"], ["published", "published"])
    # (Two successes means the attempts did not overlap; either way nothing is duplicated.)
    assert _served(warehouse) == 41
    assert not any("__staging_" in name for name in _tables(warehouse))
    assert release.LOCK_TABLE not in {
        r[0]
        for r in warehouse.query(
            "SELECT name FROM system.tables WHERE database = currentDatabase()"
        ).result_rows
    }


class _CrashAfter:
    """Delegates to a real client and raises once a command matching ``needle`` has run."""

    def __init__(self, client, needle: str) -> None:
        self._client, self._needle, self.crashed = client, needle, False

    def command(self, sql: str) -> Any:
        result = self._client.command(sql)
        if not self.crashed and self._needle in sql:
            self.crashed = True
            raise ConnectionError(f"simulated crash after: {sql}")
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


@live
def test_crash_after_events_exchange_keeps_and_recovers_the_replaced_release(warehouse) -> None:
    _dld_sales(warehouse, [(f"T{i}", "Sales", 1_000_000 + i) for i in range(50)])
    r1 = str(release.materialize_market_sale_events(clickhouse=warehouse)["release_id"])
    _dld_sales(warehouse, [("T50", "Sales", 2_000_000)])
    r2 = str(release.materialize_market_sale_events(clickhouse=warehouse)["release_id"])
    _dld_sales(warehouse, [("T51", "Sales", 2_000_000)])

    # Crash immediately after the events table is exchanged: the staging name now holds r2,
    # the release just replaced, and __previous still holds r1.
    crashing = _CrashAfter(warehouse, "EXCHANGE TABLES `market_sale_events` AND")
    with pytest.raises(ConnectionError):
        release.materialize_market_sale_events(clickhouse=cast(Any, crashing))
    tables = _tables(warehouse)
    r3 = tables["market_sale_events"]
    assert r3 not in {r1, r2} and _served(warehouse) == 52
    assert r2 in {tables[n] for n in tables if n.startswith("market_sale_events__staging_")}
    assert _ledger(warehouse)[r3] == "publishing"

    with release.release_lock(warehouse, owner="test"):
        actions = release.recover(warehouse)
    assert any("marked published" in action for action in actions)
    tables = _tables(warehouse)
    assert tables["market_sale_events__previous"] == r2  # newest good release retained
    assert not any("__staging_" in name for name in tables)
    assert _ledger(warehouse) == {r1: "superseded", r2: "superseded", r3: "published"}

    assert release.rollback(warehouse, "market_sale_events") == r2
    assert _served(warehouse) == 51 and _tables(warehouse)["market_sale_events"] == r2


@live
def test_crash_between_ledger_and_events_exchange_serves_one_consistent_release(
    warehouse,
) -> None:
    _dld_sales(warehouse, [(f"T{i}", "Sales", 1_000_000 + i) for i in range(50)])
    r1 = str(release.materialize_market_sale_events(clickhouse=warehouse)["release_id"])
    _dld_sales(warehouse, [("T50", "Sales", 2_000_000)])

    # The match ledger is promoted, then the process dies before the events exchange.
    crashing = _CrashAfter(warehouse, "DROP TABLE `market_sale_matches__staging_")
    with pytest.raises(ConnectionError):
        release.materialize_market_sale_events(clickhouse=cast(Any, crashing))
    tables = _tables(warehouse)
    assert tables["market_sale_events"] == r1 and _served(warehouse) == 50  # readers unaffected
    r2 = tables["market_sale_matches"]
    assert r2 != r1

    # The next run recovers first (ledger realigned to r1, r2 marked failed) and publishes.
    r3 = str(release.materialize_market_sale_events(clickhouse=warehouse)["release_id"])
    states = _ledger(warehouse)
    assert states[r2] == "failed" and states[r3] == "published" and states[r1] == "superseded"
    tables = _tables(warehouse)
    assert tables["market_sale_matches"] == tables["market_sale_events"] == r3
    assert tables["market_sale_events__previous"] == tables["market_sale_matches__previous"] == r1
    assert _served(warehouse) == 51


@live
def test_published_versions_stay_immutable_until_no_reader_can_hold_them(warehouse) -> None:
    from datetime import UTC

    _dld_sales(warehouse, [(f"T{i}", "Sales", 1_000_000 + i) for i in range(40)])
    r1 = str(release.materialize_market_sale_events(clickhouse=warehouse)["release_id"])
    v1 = release.version_table("market_sale_events", r1)
    _dld_sales(warehouse, [("T40", "Sales", 3_000_000)])
    r2 = str(release.materialize_market_sale_events(clickhouse=warehouse)["release_id"])
    v2 = release.version_table("market_sale_events", r2)

    # A reader pinned on r1 keeps reading exactly r1, although r2 now serves 41 rows.
    assert _served(warehouse, v1) == 40 and _served(warehouse, v2) == _served(warehouse) == 41
    assert warehouse.query(f"SELECT DISTINCT release_id FROM {v1}").result_rows == [(r1,)]

    # A release that never went live leaves no pinnable version behind.
    _dld_sales(warehouse, [("T41", "Sales", 3_000_000)])
    crashing = _CrashAfter(warehouse, "EXCHANGE TABLES `market_sale_matches` AND")
    with pytest.raises(ConnectionError):
        release.materialize_market_sale_events(clickhouse=cast(Any, crashing))
    r3 = next(rid for rid, state in _ledger(warehouse).items() if state == "publishing")
    assert release.version_table("market_sale_events", r3) in _tables(warehouse)
    with release.release_lock(warehouse, owner="test"):
        release.recover(warehouse)
        dropped = release.cleanup_versions(warehouse)
    assert any(r3 in item for item in dropped)

    # Superseded versions survive retention (a pin lasts at most MAX_READER_LEASE) ...
    assert release.RELEASE_RETENTION > release.MAX_READER_LEASE * 10
    with release.release_lock(warehouse, owner="test"):
        assert release.cleanup_versions(warehouse) == []
        later = datetime.now(UTC) + release.RELEASE_RETENTION + timedelta(minutes=1)
        dropped = release.cleanup_versions(warehouse, now=later)
    # ... and then go; the published version never does.
    assert [item.split(":")[0] for item in dropped] == [v1]
    assert v2 in _tables(warehouse) and v1 not in _tables(warehouse)

    # Rolling back to a release whose version aged out recreates it for readers.
    assert release.rollback(warehouse, "market_sale_events") == r1
    assert _served(warehouse, v1) == 40


@live
def test_corrections_duplicates_and_cut_over_are_served_once(warehouse) -> None:
    columns = [
        "row_key", "run_id", "scraped_at", "transaction_number", "instance_date", "group_en",
        "trans_value", "procedure_area", "area_en", "is_offplan_en", "source_system", "area_id",
    ]  # fmt: skip
    rows = [
        # T1 corrected by a later open-data extract: only the later price is served.
        ("t1-v1", "r1", "2026-09-10T00:00:00", "T1", datetime(2026, 9, 2), "Sales", 1_000_000),
        ("t1-v2", "r2", "2026-09-12T00:00:00", "T1", datetime(2026, 9, 2), "Sales", 1_100_000),
        # T2 delivered twice in one run: served once.
        ("t2-a", "r2", "2026-09-12T00:00:00", "T2", datetime(2026, 9, 3), "Sales", 2_000_000),
        ("t2-b", "r2", "2026-09-12T00:00:00", "T2", datetime(2026, 9, 3), "Sales", 2_000_000),
    ]
    warehouse.insert_rows(
        table="dld_od_transactions_bronze",
        column_names=columns,
        rows=[
            (*row, 80.0, "Marsa Dubai", "Ready", "dld_open_data", MARSA_DUBAI_ID) for row in rows
        ],
    )
    # Pulse history: kept before the open-data start (2026-09-02), dropped from it on.
    warehouse.insert_rows(
        table="dld_od_transactions_bronze",
        column_names=columns,
        rows=[
            ("p-old", "pulse", "2026-05-16T00:00:00", "P1", datetime(2026, 8, 30), "Sales",
             900_000, 80.0, "Marsa Dubai", "Ready", "dubai_pulse", MARSA_DUBAI_ID),
            ("p-overlap", "pulse", "2026-05-16T00:00:00", "P2", datetime(2026, 9, 2), "Sales",
             1_000_000, 80.0, "Marsa Dubai", "Ready", "dubai_pulse", MARSA_DUBAI_ID),
        ],
    )  # fmt: skip
    release.materialize_market_sale_events(clickhouse=warehouse)
    served = warehouse.query(
        "SELECT transaction_id, actual_worth FROM market_sale_events WHERE is_served = 1 "
        "ORDER BY transaction_id"
    ).result_rows
    assert served == [("P1", 900_000), ("T1", 1_100_000), ("T2", 2_000_000)]


@live
def test_every_line_of_a_multi_line_dld_number_is_served_once(warehouse) -> None:
    """Open data registers a Lease-to-Own sale and its mortgage under one number. One row
    per number served one of them at random; the sale must be a sale and the mortgage a
    mortgage, and a re-extract of the number replaces both lines."""
    columns = [*_SALE_COLUMNS, "scraped_at", "procedure_en"]

    def lines(scraped: str, sale: int, mortgage: int) -> list[tuple]:
        return [
            (f"{scraped}-s", "run", "110-421-2026", datetime(2026, 9, 3), "Sales", sale, 130.0,
             "Marsa Dubai", "Ready", "dld_open_data", MARSA_DUBAI_ID, scraped,
             "Lease to Own Registration"),
            (f"{scraped}-m", "run", "110-421-2026", datetime(2026, 9, 3), "Mortgage", mortgage,
             130.0, "Marsa Dubai", "Ready", "dld_open_data", MARSA_DUBAI_ID, scraped,
             "Lease to Own Registration"),
            # The same sale line delivered twice in one extract is one line.
            (f"{scraped}-s2", "run", "110-421-2026", datetime(2026, 9, 3), "Sales", sale, 130.0,
             "Marsa Dubai", "Ready", "dld_open_data", MARSA_DUBAI_ID, scraped,
             "Lease to Own Registration"),
        ]  # fmt: skip

    warehouse.insert_rows(
        table="dld_od_transactions_bronze",
        column_names=columns,
        rows=lines("2026-09-06T00:00:00", 1_265_000, 1_000_000),
    )
    release.materialize_market_sale_events(clickhouse=warehouse)

    def served() -> list[tuple]:
        return warehouse.query(
            "SELECT trans_group_en, actual_worth, transaction_number FROM market_sale_events "
            "WHERE is_served = 1 ORDER BY trans_group_en"
        ).result_rows

    assert served() == [
        ("Mortgages", 1_000_000, "110-421-2026"),
        ("Sales", 1_265_000, "110-421-2026"),
    ]
    ids = warehouse.query("SELECT transaction_id FROM market_sale_events").result_rows
    assert len({row[0] for row in ids}) == 2 and all("#" in row[0] for row in ids)

    # A later extract corrects the sale price: both lines come from it, none from the old one.
    warehouse.insert_rows(
        table="dld_od_transactions_bronze",
        column_names=columns,
        rows=lines("2026-09-08T00:00:00", 1_300_000, 1_000_000),
    )
    release.materialize_market_sale_events(clickhouse=warehouse)
    assert served() == [
        ("Mortgages", 1_000_000, "110-421-2026"),
        ("Sales", 1_300_000, "110-421-2026"),
    ]
