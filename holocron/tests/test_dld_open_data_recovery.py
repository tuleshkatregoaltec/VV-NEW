"""DLD open-data extraction recovers from outages and never advances past what it loaded.

Unit tests drive the real extractor against a fake gateway; the ClickHouse test loads
overlapping, reordered and repeated pulls through the real bronze loader and publishes them.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from holocron.platform.checkpoints import checkpoint_s3_key
from holocron.platform.http_retry import SourceAccessDenied
from holocron.sources import dld_open_data_orchestration as orchestration
from holocron.sources.dld_od_transactions.extract import extract_release
from holocron.sources.dld_open_data_orchestration import (
    DLD_OPEN_DATA_INCREMENTAL_DEFAULTS,
    open_data_checkpoint_operation,
    resolve_open_data_config,
    write_open_data_checkpoint,
    write_open_data_watermark,
)
from holocron.sources.dld_open_data_shared import DldOpenDataApiClient, PaginationIncomplete
from tests.fakes import FakeS3

_KEY = checkpoint_s3_key(
    source="dld_od_transactions", operation=open_data_checkpoint_operation("incremental")
)


def _dld_date(text: str) -> date:
    return datetime.strptime(text, "%m/%d/%Y").date()


class FakeGateway:
    """Serves transactions by INSTANCE_DATE with skip/take paging, like the DLD gateway."""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.requested_days: set[date] = set()
        self.on_page: Any = None

    def fetch(self, *, command: str, payload: dict[str, str]) -> dict:
        start, end = _dld_date(payload["P_FROM_DATE"]), _dld_date(payload["P_TO_DATE"])
        day = start
        while day <= end:
            self.requested_days.add(day)
            day += timedelta(days=1)
        matching = sorted(
            (r for r in self.rows if start <= date.fromisoformat(r["INSTANCE_DATE"][:10]) <= end),
            key=lambda r: r["INSTANCE_DATE"],
            reverse=True,
        )
        skip, take = int(payload["P_SKIP"]), int(payload["P_TAKE"])
        page = [
            {**row, "RN": skip + i + 1, "TOTAL": len(matching)}
            for i, row in enumerate(matching[skip : skip + take])
        ]
        if self.on_page:
            self.on_page(skip)
        return {"responseCode": 200, "validationErrorsList": [], "response": {"result": page}}


def _txn(number: str, day: date, price: int = 1_000_000) -> dict[str, Any]:
    return {
        "TRANSACTION_NUMBER": number,
        "INSTANCE_DATE": f"{day.isoformat()}T10:00:00",
        "GROUP_EN": "Sales",
        "TRANS_VALUE": price,
        "PROCEDURE_AREA": 80.0,
        "AREA_EN": "Marsa Dubai",
        "AREA_ID": 330,  # DLD's district id, the crosswalk's district evidence
        "IS_OFFPLAN_EN": "Ready",
    }


def _resolved(s3: FakeS3, today: date, monkeypatch) -> dict[str, Any]:
    monkeypatch.setattr(
        orchestration, "utc_now", lambda: datetime.combine(today, datetime.min.time(), UTC)
    )
    return resolve_open_data_config(
        source_name="dld_od_transactions",
        source_config=dict(DLD_OPEN_DATA_INCREMENTAL_DEFAULTS),
        object_storage_client=s3,
    )


def _manifest(release, key: str = "raw/manifest.json") -> dict[str, Any]:
    return {
        "source": release.source,
        "run_id": release.run_id,
        "metadata": dict(release.metadata),
        "manifest_s3_key": key,
    }


def test_seven_day_outage_is_caught_up_from_the_last_loaded_window(
    tmp_path: Path, monkeypatch
) -> None:
    s3 = FakeS3()
    first_day = date(2026, 6, 1)
    rows = [_txn(f"T{i}", first_day + timedelta(days=i)) for i in range(30)]
    gateway = FakeGateway(rows)

    config = _resolved(s3, date(2026, 6, 10), monkeypatch)
    first = extract_release(
        tmp_path / "a", config=config, api_client=gateway, now=datetime(2026, 6, 10, tzinfo=UTC)
    )
    # Raw storage alone does not move the watermark; the bronze load does.
    write_open_data_checkpoint(raw_release=first, manifest_key="m1", object_storage_client=s3)
    assert _KEY not in s3.json_objects
    write_open_data_watermark(raw_manifest=_manifest(first), object_storage_client=s3)
    assert s3.json_objects[_KEY]["effective_end_date"] == "2026-06-10"

    # Seven daily runs are missed. The next run starts at the watermark minus the overlap,
    # however long the outage was, so every missed day is requested again.
    gateway.requested_days.clear()
    config = _resolved(s3, date(2026, 6, 18), monkeypatch)
    assert config["start_date"] == "2026-06-03" and config["end_date"] == "2026-06-18"
    second = extract_release(
        tmp_path / "b", config=config, api_client=gateway, now=datetime(2026, 6, 18, tzinfo=UTC)
    )
    missed = {date(2026, 6, 11) + timedelta(days=i) for i in range(8)}
    assert missed <= gateway.requested_days
    loaded = {
        json.loads(line)["raw"]["TRANSACTION_NUMBER"]
        for line in Path(next(f.path for f in second.files if f.path.endswith("_rows.jsonl")))
        .read_text()
        .splitlines()
    }
    assert {f"T{i}" for i in range(10, 18)} <= loaded


def test_capped_run_never_replaces_the_complete_watermark(tmp_path: Path, monkeypatch) -> None:
    s3 = FakeS3()
    s3.json_objects[_KEY] = {
        "mode": "incremental",
        "completed": True,
        "effective_end_date": "2026-06-10",
        "filters": {},
        "sort": "",
    }
    gateway = FakeGateway([_txn(f"T{i}", date(2026, 6, 5)) for i in range(5)])
    config = {**_resolved(s3, date(2026, 6, 12), monkeypatch), "page_size": 2, "max_pages": 1}
    capped = extract_release(tmp_path, config=config, api_client=gateway)
    assert capped.metadata["completed_result_set"] is False

    write_open_data_watermark(raw_manifest=_manifest(capped), object_storage_client=s3)
    assert s3.json_objects[_KEY]["effective_end_date"] == "2026-06-10"
    assert s3.json_objects[_KEY]["completed"] is True


def test_rows_registered_mid_crawl_fail_the_window_instead_of_loading_a_shifted_set(
    tmp_path: Path, monkeypatch
) -> None:
    rows = [_txn(f"T{i}", date(2026, 6, 5)) for i in range(5)]
    gateway = FakeGateway(rows)

    def register_new_sale(skip: int) -> None:
        if skip == 0:  # after page one, a new sale shifts every later row down by one
            gateway.rows.append(_txn("NEW", date(2026, 6, 6)))

    gateway.on_page = register_new_sale
    config = {**_resolved(FakeS3(), date(2026, 6, 7), monkeypatch), "page_size": 2}
    with pytest.raises(PaginationIncomplete, match="TOTAL changed"):
        extract_release(tmp_path, config=config, api_client=gateway)


def test_access_denial_stops_the_run_before_any_watermark_moves(
    tmp_path: Path, monkeypatch
) -> None:
    s3 = FakeS3()
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(403, text="consumer blocked")

    api = DldOpenDataApiClient(
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        base_url="https://gateway.test",
        consumer_id="x",
        min_request_interval_seconds=0,
    )
    config = _resolved(s3, date(2026, 6, 7), monkeypatch)
    with pytest.raises(SourceAccessDenied):
        extract_release(tmp_path, config=config, api_client=api)
    assert len(calls) == 1 and _KEY not in s3.json_objects


# --- real ClickHouse -----------------------------------------------------------------------


@pytest.mark.skipif(
    os.environ.get("HOLOCRON_CLICKHOUSE_INTEGRATION") != "1",
    reason="set HOLOCRON_CLICKHOUSE_INTEGRATION=1 with local ClickHouse",
)
def test_overlapping_reordered_and_repeated_pulls_keep_exact_business_cardinality(
    tmp_path: Path, monkeypatch
) -> None:
    from holocron.market import release
    from holocron.sources.dld_od_transactions.bronze import load_release_to_clickhouse
    from tests.test_market_release import _client, _settings

    import clickhouse_connect
    from uuid import uuid4

    from holocron.platform.clickhouse_schema import ensure_schema

    name = "holocron_dld_replay_test_" + uuid4().hex
    s = _settings("default")
    admin = clickhouse_connect.get_client(
        host=s.clickhouse_host, port=s.clickhouse_port,
        username=s.clickhouse_user, password=s.clickhouse_password,
    )  # fmt: skip
    admin.command(f"CREATE DATABASE {name}")
    try:
        warehouse = _client(name)
        ensure_schema(clickhouse=warehouse)
        s3 = FakeS3()
        start = date(2026, 6, 1)
        rows = [_txn(f"T{i}", start + timedelta(days=i % 20)) for i in range(40)]
        gateway = FakeGateway(rows)

        def pull(run: str, window: tuple[str, str], page_size: int) -> None:
            raw = extract_release(
                tmp_path / run,
                config={"mode": "backfill", "start_date": window[0], "end_date": window[1],
                        "page_size": page_size, "slice_days": 14},
                api_client=gateway,
            )  # fmt: skip
            files = []
            for file in raw.files:
                payload = Path(file.path).read_bytes()
                key = f"raw/{run}/{Path(file.path).name}"
                s3.objects[key] = payload
                files.append({
                    "path": Path(file.path).name, "s3_key": key, "row_count": file.row_count,
                    "sha256": hashlib.sha256(payload).hexdigest(),
                })  # fmt: skip
            manifest = {**_manifest(raw, f"raw/{run}/manifest.json"), "files": files}
            load_release_to_clickhouse(raw_manifest=manifest, s3=s3, clickhouse=warehouse)

        pull("a", ("2026-06-01", "2026-06-14"), page_size=7)
        # Overlapping window, different page size (rows arrive in a different order on
        # different pages), and a corrected price for T10 (11 June, inside the overlap)
        # published by DLD in between. A correction older than the re-pulled window is only
        # picked up by a backfill of its dates.
        gateway.rows = [
            {**r, "TRANS_VALUE": 1_250_000} if r["TRANSACTION_NUMBER"] == "T10" else r
            for r in gateway.rows
        ]
        pull("b", ("2026-06-08", "2026-06-21"), page_size=5)
        pull("b-repeat", ("2026-06-08", "2026-06-21"), page_size=5)  # retry after uncertain load

        bronze = warehouse.query(
            "SELECT count(), uniqExact(transaction_number) FROM dld_od_transactions_bronze"
        ).result_rows[0]
        assert bronze == (40, 40)
        release.materialize_market_sale_events(clickhouse=warehouse)
        served = dict(
            warehouse.query(
                "SELECT transaction_id, actual_worth FROM market_sale_events WHERE is_served = 1"
            ).result_rows
        )
        assert len(served) == 40 and served["T10"] == 1_250_000
    finally:
        admin.command(f"DROP DATABASE {name} SYNC")
