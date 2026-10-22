from __future__ import annotations

import json
from datetime import UTC, datetime

from holocron.platform import census
from holocron.sources import registry

NOW = datetime(2026, 9, 30, 12, tzinfo=UTC)
NO_ACCESS = {"access": census.UNKNOWN_ENV}
NO_LIVE = {k: NO_ACCESS for k in ("clickhouse", "object_storage", "dagster", "app_postgres", "api")}
STATIC = {
    "jobs": ["dld_od_transactions_daily_incremental", "dld_od_transactions_manual_backfill"],
    "backend_consumers": {},
}


def _expected() -> dict[str, list[str]]:
    return census.parse_expected_schema(census.SCHEMA_SQL.read_text(encoding="utf-8"))


def _dataset(dataset_id: str) -> census.Dataset:
    return next(d for d in census.DATASETS if d.id == dataset_id)


def test_expected_schema_includes_columns_added_by_alter_table() -> None:
    # Serving views read Pulse-era columns that only exist via ALTER TABLE ADD COLUMN;
    # missing them would report false schema drift on every DLD dataset.
    columns = _expected()["dld_od_transactions_bronze"]
    assert "transaction_number" in columns
    assert "meter_sale_price" in columns
    assert "no_of_parties_role_3" in columns


def test_every_census_table_has_a_producer_or_declares_it_missing() -> None:
    # A table the census tracks must either be created by Holocron DDL or be explicitly
    # recorded as having no producer; otherwise renames silently turn into "MISSING".
    expected = set(_expected())
    for dataset in census.DATASETS:
        declared_missing = any(cls == "MISSING" for _, cls, _ in dataset.static_breaks)
        for table in dataset.tables:
            assert table in expected or declared_missing, (dataset.id, table)


def test_every_registered_source_is_mapped_to_a_census_dataset() -> None:
    # New Holocron sources must be added to the census so coverage never shrinks unnoticed.
    mapped = {s for d in census.DATASETS for s in d.sources}
    assert {s.name for s in registry.list_sources()} <= mapped


def test_no_live_access_is_unknown_not_guessed_but_static_facts_survive() -> None:
    result = census.assess(_dataset("reelly_projects"), NO_LIVE, STATIC, NOW)
    assert result["classification"] == "UNKNOWN"
    assert result["static_classification"] == "SCHEMA_DRIFT"
    assert result["first_break_stage"] == "bronze"


def test_missing_live_table_is_classified_missing_at_bronze() -> None:
    live = {**NO_LIVE, "clickhouse": {"access": "ok", "objects": {}, "bronze_load_ledger": {}}}
    result = census.assess(_dataset("dld_rents"), live, STATIC, NOW)
    assert result["classification"] == "MISSING"
    assert result["first_break_stage"] == "bronze"


def test_old_failed_backfill_does_not_mask_newer_daily_success() -> None:
    dagster = {
        "access": "ok",
        "jobs": {
            "dld_od_transactions_manual_backfill": {
                "last_run": {"status": "FAILURE", "created": "2026-01-01 00:00:00", "runtime_s": 5}
            },
            "dld_od_transactions_daily_incremental": {
                "last_run": {
                    "status": "SUCCESS",
                    "created": "2026-09-30 00:00:00",
                    "runtime_s": 60,
                },
                "last_success": {"created": "2026-09-30 00:00:00", "runtime_s": 60},
            },
        },
    }
    result = census.assess(
        _dataset("dld_transactions"), {**NO_LIVE, "dagster": dagster}, STATIC, NOW
    )
    assert result["stages"]["extraction"]["status"] == "ok"


def test_stale_table_is_classified_stale() -> None:
    table = {
        "exists": True,
        "query_ok": True,
        "row_count": 10,
        "latest_record": "2026-09-01 00:00:00",
    }
    view = {"exists": True, "query_ok": True, "row_count": 10}
    objects = {"dld_od_rents_bronze": table, "ch_rent_contracts": view}
    live = {**NO_LIVE, "clickhouse": {"access": "ok", "objects": objects, "bronze_load_ledger": {}}}
    result = census.assess(_dataset("dld_rents"), live, STATIC, NOW)
    assert result["classification"] == "STALE"


def test_missing_serving_view_over_healthy_bronze_is_downstream_broken() -> None:
    table = {
        "exists": True,
        "query_ok": True,
        "row_count": 10,
        "latest_record": "2026-09-30 00:00:00",
    }
    live = {
        **NO_LIVE,
        "clickhouse": {
            "access": "ok",
            "objects": {"dld_od_rents_bronze": table},
            "bronze_load_ledger": {},
        },
    }
    result = census.assess(_dataset("dld_rents"), live, STATIC, NOW)
    assert result["classification"] == "DOWNSTREAM_BROKEN"
    assert result["stages"]["bronze"]["status"] == "ok"


def test_frontend_calls_without_backend_route_are_reported() -> None:
    front = ["/api/v1/transactions/sales", "/api/v1/transactions/{p}/match-units"]
    back = ["/api/v1/transactions/sales", "/api/v1/transactions/rentals"]
    assert census.frontend_only_paths(front, back) == ["/api/v1/transactions/{p}/match-units"]


def test_empty_raw_bucket_with_populated_bronze_is_not_reported_as_missing_data() -> None:
    # A bucket without manifests only proves raw lives elsewhere when bronze has rows;
    # calling that MISSING would hide millions of real rows behind a lineage gap.
    table = {
        "exists": True,
        "query_ok": True,
        "row_count": 10,
        "latest_record": "2026-09-30 00:00:00",
    }
    view = {"exists": True, "query_ok": True, "row_count": 10}
    objects = {"dld_od_rents_bronze": table, "ch_rent_contracts": view}
    s3 = {"access": "ok", "bucket": "b", "sources": {"dld_od_rents": {"manifest_count": 0}}}
    live = {
        **NO_LIVE,
        "object_storage": s3,
        "clickhouse": {"access": "ok", "objects": objects, "bronze_load_ledger": {}},
    }
    result = census.assess(_dataset("dld_rents"), live, STATIC, NOW)
    assert result["classification"] == "AVAILABLE"
    assert result["stages"]["raw"]["status"] == "unknown"


def test_key_collisions_alone_do_not_downgrade_but_exact_duplicates_do() -> None:
    # Rent contracts legitimately repeat contract_number (multi-property, versions);
    # only rows identical in every business column indicate the reload defect.
    base = {
        "exists": True,
        "query_ok": True,
        "row_count": 10,
        "latest_record": "2026-09-30 00:00:00",
    }
    view = {"exists": True, "query_ok": True, "row_count": 10}

    def classify(table: dict) -> str:
        objects = {"dld_od_rents_bronze": table, "ch_rent_contracts": view}
        live = {
            **NO_LIVE,
            "clickhouse": {"access": "ok", "objects": objects, "bronze_load_ledger": {}},
        }
        return census.assess(_dataset("dld_rents"), live, STATIC, NOW)["classification"]

    key_only = {**base, "duplicate_keys": {"key": ["contract_number"], "extra_rows": 5}}
    assert classify(key_only) == "AVAILABLE"
    exact = {**key_only, "exact_duplicates": {"compared_columns": 40, "extra_rows": 3}}
    assert classify(exact) == "PARTIAL"


def test_recorded_evidence_fills_api_and_frontend_without_claiming_a_live_probe(tmp_path) -> None:
    # Recorded checks of the same environment count as observations, but must stay
    # labelled as recorded and must not erase breaks the code proves.
    report = tmp_path / "verification-report.json"
    report.write_text(
        json.dumps(
            {
                "features": [
                    {"path": "/api/v1/transactions/rentals?limit=5", "status": 200, "seconds": 58.0}
                ],
                "browser": {
                    "pages": [{"page": "/transactions/rentals", "status": 200, "data_status": 200}]
                },
            }
        )
    )
    recorded = census.load_recorded_evidence(report)
    result = census.assess(_dataset("dld_rents"), {**NO_LIVE, "recorded": recorded}, STATIC, NOW)
    api = result["stages"]["api"]
    assert api["status"] == "partial"  # the static analytics TODO still applies
    assert any(e.startswith("recorded (verification-report.json)") for e in api["evidence"])
    assert result["stages"]["frontend"]["status"] == "ok"


def test_empty_dagster_history_with_populated_tables_is_not_missing_extraction() -> None:
    # A fresh Dagster instance next to restored data has no runs; the rows prove
    # extraction happened elsewhere, so this must not read as "never extracted".
    table = {
        "exists": True,
        "query_ok": True,
        "row_count": 10,
        "latest_record": "2026-09-30 00:00:00",
    }
    view = {"exists": True, "query_ok": True, "row_count": 10}
    objects = {"dld_od_rents_bronze": table, "ch_rent_contracts": view}
    live = {
        **NO_LIVE,
        "dagster": {"access": "ok", "jobs": {}, "schedule_state": {}},
        "clickhouse": {"access": "ok", "objects": objects, "bronze_load_ledger": {}},
    }
    result = census.assess(_dataset("dld_rents"), live, STATIC, NOW)
    assert result["stages"]["extraction"]["status"] == "unknown"
    assert result["classification"] == "AVAILABLE"
