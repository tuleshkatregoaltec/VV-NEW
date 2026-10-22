"""Load a Bayut full-collection export into ClickHouse as one snapshot.

Input is the directory ``full_collection.export_collection`` writes: ``listings.jsonl`` (one
row per unique listing id) and ``summary.json`` (its coverage certificate). Acquisition is
unchanged and stays manual: nothing here fetches from Bayut, and no schedule runs it, while
access and licensing (decision D1) are unresolved.

Only a certified collection (``coverage_status`` complete or complete_with_tolerance) becomes
``published`` and so the current inventory (view ``bayut_listings_current``). Any other
export can be loaded only explicitly as ``partial``. It is kept for inspection, never served,
and never marks unseen listings as withdrawn: withdrawals come only from comparing two
published snapshots.

    python -m holocron.sources.bayut_listings.bronze EXPORT_DIR [--allow-partial]
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from holocron.contracts import ClickHouseInserter
from holocron.platform.clickhouse import sql_string

CERTIFIED = {"complete", "complete_with_tolerance"}
_COLUMNS = (
    "snapshot_id", "listing_id", "purpose", "property_type", "title", "listing_url",
    "price_aed", "size_sqft", "search_path", "root_name", "observed_at", "raw_json",
)  # fmt: skip
_SNAPSHOT_COLUMNS = (
    "snapshot_id", "status", "coverage_status", "listing_count", "summary_json", "export_sha256",
    "collected_at",
)  # fmt: skip


class BayutSnapshotNotCertified(RuntimeError):
    """The collection did not certify complete coverage; it cannot become the inventory."""


def _number(value: Any) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _collected_at(observed: list[str]) -> datetime:
    """When the crawl last saw a listing: the snapshot's place in time.

    Load time cannot order snapshots: reloading last week's export would make it the newest
    inventory again. The latest listing observation is fixed by the export's content.
    """
    times = []
    for value in observed:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            continue
        times.append(parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC))
    if not times:
        raise ValueError("export has no listing scraped_at: its collection time is unknown")
    return max(times).astimezone(UTC)


def load_export(
    clickhouse: ClickHouseInserter, export_dir: str | Path, *, allow_partial: bool = False
) -> dict[str, Any]:
    export = Path(export_dir)
    summary = json.loads((export / "summary.json").read_text(encoding="utf-8"))
    coverage = str(summary.get("coverage_status") or "")
    if coverage not in CERTIFIED and not allow_partial:
        raise BayutSnapshotNotCertified(
            f"coverage_status={coverage or 'missing'}: pass allow_partial to keep it unserved"
        )
    listings_bytes = (export / "listings.jsonl").read_bytes()
    digest = hashlib.sha256(listings_bytes).hexdigest()
    snapshot_id = f"bayut-{digest[:16]}"  # same export loaded twice is one snapshot
    rows: dict[str, tuple[Any, ...]] = {}
    for line in listings_bytes.decode("utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        listing_id = str(item.get("listing_id") or "")
        if not listing_id:
            continue
        # A listing seen on several pages or shards is one listing.
        rows.setdefault(listing_id, (
            snapshot_id, listing_id, str(item.get("purpose") or ""),
            str(item.get("property_type") or ""), str(item.get("title") or ""),
            str(item.get("listing_url") or ""), _number(item.get("price_aed")),
            _number(item.get("size_sqft")), str(item.get("search_path") or ""),
            str(item.get("root_name") or ""), str(item.get("scraped_at") or ""),
            json.dumps(item, sort_keys=True, separators=(",", ":")),
        ))  # fmt: skip
    status = "published" if coverage in CERTIFIED else "partial"
    if status == "published":
        if not rows:
            raise BayutSnapshotNotCertified(
                "a certified export with no listings cannot be published"
            )
        collected_at = _collected_at([row[10] for row in rows.values()])
    else:
        # Never served, so its place in time only labels it; load time is enough.
        collected_at = datetime.now(UTC)
    quoted = sql_string(snapshot_id)
    clickhouse.command(
        f"DELETE FROM bayut_listings_bronze WHERE snapshot_id = {quoted} "
        "SETTINGS lightweight_deletes_sync = 2"
    )
    if rows:
        clickhouse.insert_rows(
            table="bayut_listings_bronze", rows=list(rows.values()), column_names=_COLUMNS
        )
    # The snapshot record is written last: a crash before it leaves rows no view serves.
    clickhouse.insert_rows(
        table="bayut_listing_snapshots",
        rows=[
            (snapshot_id, status, coverage, len(rows), json.dumps(summary), digest, collected_at)
        ],  # fmt: skip
        column_names=_SNAPSHOT_COLUMNS,
    )
    return {"snapshot_id": snapshot_id, "status": status, "listings": len(rows)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    from holocron.platform.clickhouse import ClickHouseClient
    from holocron.platform.clickhouse_schema import ensure_schema
    from holocron.platform.settings import Settings

    parser = argparse.ArgumentParser(prog="python -m holocron.sources.bayut_listings.bronze")
    parser.add_argument("export_dir")
    parser.add_argument("--allow-partial", action="store_true")
    args = parser.parse_args(argv)
    clickhouse = ClickHouseClient(Settings())
    ensure_schema(clickhouse=clickhouse)
    print(load_export(clickhouse, args.export_dir, allow_partial=args.allow_partial))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
