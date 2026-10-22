"""Bayut snapshots: only a certified complete crawl becomes the inventory or withdraws."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import pytest

from holocron.sources.bayut_listings import bronze
from tests.test_market_release import database, warehouse  # noqa: F401 (fixtures)


def _export(path: Path, listings: list[dict], coverage: str) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "listings.jsonl").write_text("".join(json.dumps(item) + "\n" for item in listings))
    (path / "summary.json").write_text(json.dumps({"coverage_status": coverage}))
    return path


def _listing(listing_id: str, price: int = 1_000_000, day: int = 1) -> dict:
    return {
        "listing_id": listing_id, "purpose": "sale", "property_type": "apartments",
        "title": f"Unit {listing_id}", "listing_url": f"https://www.bayut.com/property/details-{listing_id}.html",
        "price_aed": price, "size_sqft": None, "scraped_at": f"2026-10-{day:02d}T00:00:00Z",
    }  # fmt: skip


def test_collection_time_comes_from_listing_observations_not_load_time() -> None:
    # The newest observation fixes the snapshot in time, whenever the export is loaded.
    assert bronze._collected_at(["2026-10-01T00:00:00Z", "2026-10-03T08:00:00+00:00", ""]) == (
        bronze.datetime(2026, 10, 3, 8, tzinfo=bronze.UTC)
    )
    with pytest.raises(ValueError, match="collection time is unknown"):
        bronze._collected_at(["", "not a date"])


def test_uncertified_collection_is_refused_unless_explicitly_kept_unserved(tmp_path) -> None:
    export = _export(tmp_path / "x", [_listing("1")], "not_complete")
    with pytest.raises(bronze.BayutSnapshotNotCertified, match="not_complete"):
        # A client that is never touched: the refusal comes before any write.
        bronze.load_export(cast(Any, object()), export)


live = pytest.mark.skipif(
    os.environ.get("HOLOCRON_CLICKHOUSE_INTEGRATION") != "1",
    reason="set HOLOCRON_CLICKHOUSE_INTEGRATION=1 with local ClickHouse",
)


@live
def test_partial_crawls_never_replace_inventory_or_withdraw_listings(warehouse, tmp_path) -> None:  # noqa: F811
    def current() -> set[str]:
        rows = warehouse.query("SELECT listing_id FROM bayut_listings_current").result_rows
        return {row[0] for row in rows}

    def withdrawn() -> set[str]:
        rows = warehouse.query("SELECT listing_id FROM bayut_listing_withdrawals").result_rows
        return {row[0] for row in rows}

    first = bronze.load_export(
        warehouse,
        # Listing 1 appears on two pages: one listing.
        _export(tmp_path / "a", [_listing("1"), _listing("1"), _listing("2"), _listing("3")],
                "complete"),
    )  # fmt: skip
    assert first["listings"] == 3 and current() == {"1", "2", "3"} and withdrawn() == set()
    # Reloading the same export is the same snapshot, not a second copy.
    bronze.load_export(warehouse, tmp_path / "a")
    assert warehouse.query("SELECT count() FROM bayut_listings_bronze").result_rows[0][0] == 3

    # A blocked crawl saw only listing 1: kept for inspection, but it serves nothing and
    # withdraws nothing.
    partial = bronze.load_export(
        warehouse, _export(tmp_path / "b", [_listing("1")], "not_complete"), allow_partial=True
    )
    assert partial["status"] == "partial"
    assert current() == {"1", "2", "3"} and withdrawn() == set()

    # The next certified crawl no longer has listing 3: that is a withdrawal.
    bronze.load_export(
        warehouse, _export(tmp_path / "c", [_listing("1"), _listing("2", 900_000)], "complete")
    )
    assert current() == {"1", "2"} and withdrawn() == {"3"}


@live
def test_reloading_an_older_export_never_makes_it_current(warehouse, tmp_path) -> None:  # noqa: F811
    older = _export(tmp_path / "old", [_listing("1", day=1), _listing("2", day=1)], "complete")
    newer = _export(tmp_path / "new", [_listing("1", day=5)], "complete")
    bronze.load_export(warehouse, older)
    bronze.load_export(warehouse, newer)
    # An operator repairs the table by reloading last week's export after the newer one.
    bronze.load_export(warehouse, older)

    current = warehouse.query("SELECT listing_id FROM bayut_listings_current").result_rows
    withdrawn = warehouse.query("SELECT listing_id FROM bayut_listing_withdrawals").result_rows
    # The newer crawl stays the inventory and listing 2 stays withdrawn (it was not reversed).
    assert {row[0] for row in current} == {"1"}
    assert {row[0] for row in withdrawn} == {"2"}
