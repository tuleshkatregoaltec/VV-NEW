"""Evidence of which source days were loaded in full, and the coverage a release can claim.

Event dates alone cannot show completeness: records on the first and last day of a month
say nothing about the days between. A bronze load therefore records every date window it
replaced, with the pagination evidence from its page file. Coverage is then a property of
days: verified (the latest window covering the day was read to its end, or an operator
attested the archive it came from), partial (the latest window stopped inside the day's
window) or unverified (no window).

A window too large for one chunk is read by several loads: the first (skip 0) replaces the
window in bronze, later ones resume at a row offset and append. Each load records the rows
it read (``first_row``..``end_row`` of ``total_rows``). A resumed load proves the window only
when it continues an unbroken chain from a skip-0 load of the same window with the same
TOTAL: if an earlier chunk never reached bronze, or the source's TOTAL changed between
chunks (rows shifted), its days stay partial however far the last chunk read.

Operators record an archive that has no page evidence (Dubai Pulse history) with
``python -m holocron.platform.load_windows attest TABLE DATE_FIELD FROM TO EVIDENCE``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import date, datetime, timedelta
from typing import Any

from holocron.contracts import ClickHouseInserter
from holocron.platform.raw_files import string_value

_COLUMNS = (
    "bronze_table", "date_field", "window_start", "window_end", "status", "rows", "run_id",
    "manifest_key", "evidence", "first_row", "end_row", "total_rows",
)  # fmt: skip
# Row offsets of loads recorded before offsets were kept (and of attested archives).
UNKNOWN_ROW = -1


def _window(row: Mapping[str, Any]) -> tuple[date, date] | None:
    meta = row.get("date_window")
    if not isinstance(meta, Mapping) or not meta.get("start_date") or not meta.get("end_date"):
        return None
    return date.fromisoformat(str(meta["start_date"])), date.fromisoformat(str(meta["end_date"]))


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def window_evidence(
    page_rows: Iterable[Mapping[str, Any]], raw_rows: Iterable[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Per date window: was the end of its result set reached, and how many rows were stored."""
    windows: dict[tuple[date, date], dict[str, Any]] = {}
    for page in page_rows:
        key = _window(page)
        if key is None:
            continue
        entry = windows.setdefault(
            key, {"complete": False, "first_skip": None, "end_row": 0, "totals": set(), "rows": 0}
        )
        skip, take = _int(page.get("skip")) or 0, _int(page.get("take")) or 0
        received, total = _int(page.get("api_row_count")) or 0, _int(page.get("total"))
        first = entry["first_skip"]
        entry["first_skip"] = skip if first is None else min(first, skip)
        entry["end_row"] = max(entry["end_row"], skip + received)
        if total is not None:
            entry["totals"].add(total)
        if received == 0 or received < take or (total is not None and skip + received >= total):
            entry["complete"] = True
    for row in raw_rows:
        key = _window(row)
        if key in windows:
            windows[key]["rows"] += 1
    result = []
    for (start, end), entry in sorted(windows.items()):
        continued = entry["first_skip"] not in (None, 0)
        totals = entry["totals"]
        shifted = len(totals) > 1
        notes = []
        if continued:
            # A chunk resumed at skip N completes a window an earlier chunk started.
            notes.append(f"continued from skip={entry['first_skip']}")
        if shifted:
            notes.append(f"TOTAL changed during pagination {sorted(totals)}")
        result.append(
            {
                "window_start": start,
                "window_end": end,
                "status": "complete" if entry["complete"] and not shifted else "partial",
                "rows": entry["rows"],
                "evidence": "; ".join(notes),
                "first_row": entry["first_skip"] or 0,
                "end_row": entry["end_row"],
                "total_rows": next(iter(totals)) if len(totals) == 1 else UNKNOWN_ROW,
            }
        )
    return result


def record_load_windows(
    clickhouse: ClickHouseInserter,
    *,
    bronze_table: str,
    date_field: str,
    windows: list[dict[str, Any]],
    run_id: str,
    manifest_key: str,
) -> None:
    clickhouse.insert_rows(
        table="source_load_windows",
        column_names=_COLUMNS,
        rows=[
            (bronze_table, date_field, w["window_start"], w["window_end"], w["status"], w["rows"],
             run_id, manifest_key, w["evidence"], w.get("first_row", UNKNOWN_ROW),
             w.get("end_row", UNKNOWN_ROW), w.get("total_rows", UNKNOWN_ROW))
            for w in windows
        ],
    )  # fmt: skip


def window_statuses(
    loads: Iterable[tuple[date, date, str, str, int, int, int]],
) -> list[tuple[date, date, str]]:
    """verified / partial per load, in load order, following each window's chunk chain.

    ``loads``: (window_start, window_end, status, evidence, first_row, end_row, total_rows)
    ordered by load time.
    """
    chains: dict[tuple[date, date], tuple[int, int] | None] = {}  # window -> (end_row, total)
    result = []
    for start, end, state, evidence, first_row, end_row, total in loads:
        key = (start, end)
        if state == "attested":
            chains[key] = None
            result.append((start, end, "verified"))
            continue
        legacy = first_row == UNKNOWN_ROW
        if (legacy and not evidence) or first_row == 0:
            # Starts the window afresh (bronze replaced the whole window).
            chains[key] = None if legacy else (end_row, total)
            intact = True
        else:
            # Resumes inside the window: valid only directly after the rows already proven.
            chain = chains.get(key)
            intact = not legacy and chain is not None and chain == (first_row, total)
            chains[key] = (end_row, total) if intact else None
        verified = intact and state == "complete"
        result.append((start, end, "verified" if verified else "partial"))
    return result


def coverage(
    clickhouse: Any, *, bronze_table: str, date_field: str, loaded_before: datetime
) -> dict[str, Any]:
    """Day ranges by status from the windows loaded before ``loaded_before``."""
    rows = clickhouse.query(
        "SELECT window_start, window_end, status, toString(loaded_at), evidence, first_row, "
        "end_row, total_rows FROM source_load_windows "
        f"WHERE bronze_table = '{bronze_table}' AND date_field = '{date_field}' "
        f"AND loaded_at <= toDateTime64('{loaded_before:%Y-%m-%d %H:%M:%S}', 3, 'UTC') "
        "ORDER BY loaded_at, window_start"
    ).result_rows
    status: dict[date, str] = {}
    for start, end, window_status in window_statuses(
        [(row[0], row[1], row[2], row[4], row[5], row[6], row[7]) for row in rows]
    ):
        day = start
        while day <= end:
            status[day] = window_status
            day += timedelta(days=1)
    ranges: list[dict[str, str]] = []
    for day in sorted(status):
        last = ranges[-1] if ranges else None
        if (
            last
            and last["status"] == status[day]
            and date.fromisoformat(last["to"]) == day - timedelta(days=1)
        ):
            last["to"] = day.isoformat()
        else:
            ranges.append({"from": day.isoformat(), "to": day.isoformat(), "status": status[day]})
    return {
        "basis": date_field,
        "ranges": ranges,
        "last_loaded_at": rows[-1][3] if rows else None,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse

    from holocron.platform.clickhouse import ClickHouseClient
    from holocron.platform.clickhouse_schema import ensure_schema
    from holocron.platform.settings import Settings

    parser = argparse.ArgumentParser(prog="python -m holocron.platform.load_windows")
    sub = parser.add_subparsers(dest="command", required=True)
    attest = sub.add_parser("attest", help="record an archive load that has no page evidence")
    attest.add_argument("bronze_table")
    attest.add_argument("date_field")
    attest.add_argument("start", type=date.fromisoformat)
    attest.add_argument("end", type=date.fromisoformat)
    attest.add_argument("evidence", help="what proves completeness, e.g. the archive manifest")
    args = parser.parse_args(argv)
    if not string_value(args.evidence):
        parser.error("evidence is required")
    clickhouse = ClickHouseClient(Settings())
    ensure_schema(clickhouse=clickhouse)
    record_load_windows(
        clickhouse,
        bronze_table=args.bronze_table,
        date_field=args.date_field,
        windows=[
            {"window_start": args.start, "window_end": args.end, "status": "attested",
             "rows": 0, "evidence": args.evidence}
        ],
        run_id="attested",
        manifest_key="",
    )  # fmt: skip
    print(f"attested {args.bronze_table}.{args.date_field} {args.start}..{args.end}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
