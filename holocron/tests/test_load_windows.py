"""Coverage is evidence of complete loads per day, not the span between first and last record."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta

import pytest

from holocron.platform.load_windows import (
    coverage,
    record_load_windows,
    window_evidence,
    window_statuses,
)
from tests.test_market_release import database, warehouse  # noqa: F401 (fixtures)


def _page(start: str, end: str, *, skip: int, take: int, received: int, total: int | None):
    return {
        "date_window": {"start_date": start, "end_date": end},
        "skip": skip,
        "take": take,
        "api_row_count": received,
        "total": total,
    }


def test_window_evidence_reads_pagination_to_tell_complete_from_partial() -> None:
    pages = [
        # Two full pages then a short one: the end of the result set was reached.
        _page("2026-06-01", "2026-06-07", skip=0, take=2, received=2, total=5),
        _page("2026-06-01", "2026-06-07", skip=2, take=2, received=2, total=5),
        _page("2026-06-01", "2026-06-07", skip=4, take=2, received=1, total=5),
        # Capped after one full page of a larger window: stored rows are a subset.
        _page("2026-06-08", "2026-06-14", skip=0, take=2, received=2, total=9),
        # An empty window: complete, and its days are verified zeros.
        _page("2026-06-15", "2026-06-15", skip=0, take=2, received=0, total=None),
        # A resumed chunk that finishes a window an earlier chunk started.
        _page("2026-06-16", "2026-06-20", skip=4, take=2, received=1, total=5),
    ]
    rows = [{"date_window": {"start_date": "2026-06-01", "end_date": "2026-06-07"}}] * 5
    evidence = {
        (w["window_start"], w["status"], w["rows"], w["evidence"])
        for w in window_evidence(pages, rows)
    }
    assert evidence == {
        (date(2026, 6, 1), "complete", 5, ""),
        (date(2026, 6, 8), "partial", 0, ""),
        (date(2026, 6, 15), "complete", 0, ""),
        (date(2026, 6, 16), "complete", 0, "continued from skip=4"),
    }


def test_window_evidence_records_the_rows_each_load_read_and_rejects_shifted_totals() -> None:
    pages = [
        _page("2026-07-01", "2026-07-31", skip=0, take=2, received=2, total=5),
        _page("2026-07-01", "2026-07-31", skip=2, take=2, received=2, total=6),
    ]
    (window,) = window_evidence(pages, [])
    # Rows were registered mid-crawl: skip/take pages shifted, so nothing here is proven.
    assert window["status"] == "partial" and "TOTAL changed" in window["evidence"]
    assert (window["first_row"], window["end_row"], window["total_rows"]) == (0, 4, -1)


JUNE = (date(2026, 6, 1), date(2026, 6, 30))


def _load(status: str, first: int, end: int, total: int = 9, evidence: str = ""):
    return (*JUNE, status, evidence, first, end, total)


def test_a_resumed_chunk_proves_a_window_only_after_the_rows_before_it_were_loaded() -> None:
    # Chunk 1 (rows 0-4) reached bronze, chunk 2 read the rest: the window is proven.
    assert [s for *_, s in window_statuses([_load("partial", 0, 5), _load("complete", 5, 9)])] == [
        "partial",
        "verified",
    ]
    # Chunk 1's bronze load failed, so only chunk 2 is recorded: rows 0-4 are missing from
    # bronze although chunk 2 reached the end of the result set.
    assert [s for *_, s in window_statuses([_load("complete", 5, 9)])] == ["partial"]
    # A gap between chunks, or a TOTAL that moved between them, breaks the chain too.
    assert window_statuses([_load("partial", 0, 4), _load("complete", 5, 9)])[-1][2] == "partial"
    assert window_statuses([_load("partial", 0, 5), _load("complete", 5, 10, 10)])[-1][2] == (
        "partial"
    )
    # A later skip-0 load replaces the window and starts a new chain.
    loads = [_load("complete", 5, 9), _load("partial", 0, 5), _load("complete", 5, 9)]
    assert [s for *_, s in window_statuses(loads)] == ["partial", "partial", "verified"]


def test_loads_recorded_before_row_offsets_keep_their_meaning() -> None:
    legacy_full = (*JUNE, "complete", "", -1, -1, -1)
    legacy_resumed = (*JUNE, "complete", "continued from skip=4", -1, -1, -1)
    attested = (*JUNE, "attested", "Pulse archive manifest", -1, -1, -1)
    assert window_statuses([legacy_full])[0][2] == "verified"
    # Nothing shows the earlier chunk reached bronze: fail closed.
    assert window_statuses([legacy_resumed])[0][2] == "partial"
    assert window_statuses([attested])[0][2] == "verified"


@pytest.mark.skipif(
    os.environ.get("HOLOCRON_CLICKHOUSE_INTEGRATION") != "1",
    reason="set HOLOCRON_CLICKHOUSE_INTEGRATION=1 with local ClickHouse",
)
def test_latest_window_decides_each_day_and_releases_record_their_coverage(warehouse) -> None:  # noqa: F811
    from holocron.market import release

    def record(start: date, end: date, status: str) -> None:
        record_load_windows(
            warehouse,
            bronze_table="dld_od_transactions_bronze",
            date_field="instance_date",
            windows=[{"window_start": start, "window_end": end, "status": status, "rows": 1,
                      "evidence": ""}],
            run_id="run",
            manifest_key="",
        )  # fmt: skip

    record(date(2026, 6, 1), date(2026, 6, 14), "complete")
    # A later capped reload of 10-12 June replaced complete data with a subset: partial now.
    record(date(2026, 6, 10), date(2026, 6, 12), "partial")
    record(date(2000, 1, 1), date(2025, 12, 31), "attested")
    result = coverage(
        warehouse,
        bronze_table="dld_od_transactions_bronze",
        date_field="instance_date",
        loaded_before=datetime.now(UTC) + timedelta(minutes=1),
    )
    assert result["ranges"] == [
        {"from": "2000-01-01", "to": "2025-12-31", "status": "verified"},
        {"from": "2026-06-01", "to": "2026-06-09", "status": "verified"},
        {"from": "2026-06-10", "to": "2026-06-12", "status": "partial"},
        {"from": "2026-06-13", "to": "2026-06-14", "status": "verified"},
    ]

    # A release records what it can vouch for when it starts building; later loads do not
    # change the coverage of an already-built release.
    warehouse.insert_rows(
        table="dld_od_transactions_bronze",
        column_names=["row_key", "run_id", "transaction_number", "instance_date", "group_en",
                      "trans_value", "procedure_area", "area_en", "is_offplan_en", "source_system",
                      "area_id"],
        rows=[("r1", "run", "T1", datetime(2026, 6, 5), "Sales", 1_000_000, 80.0, "Marsa Dubai",
               "Ready", "dld_open_data", 330)],
    )  # fmt: skip
    published = release.materialize_market_sale_events(clickhouse=warehouse)
    record(date(2026, 6, 15), date(2026, 6, 20), "complete")
    entry = release._ledger(warehouse, "market_sale_events")[str(published["release_id"])]
    assert json.loads(entry["coverage_json"])["ranges"] == result["ranges"]
