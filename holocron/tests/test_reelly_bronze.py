import hashlib
import json
from collections.abc import Sequence
from typing import Any

import os

import pytest

from holocron.sources.reelly_supply.bronze import load_release_to_clickhouse
from tests.fakes import FakeS3
from tests.test_market_release import database, warehouse  # noqa: F401 (fixtures)


class FakeClickHouse:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.insert_calls: list[dict[str, Any]] = []

    def command(self, sql: str) -> None:
        self.commands.append(sql)

    def insert_rows(
        self,
        *,
        table: str,
        rows: Sequence[tuple[Any, ...]],
        column_names: Sequence[str],
    ) -> None:
        self.insert_calls.append({"table": table, "rows": rows, "column_names": column_names})


def _jsonl_payload(rows: list[dict[str, Any]]) -> bytes:
    return ("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n").encode()


def _file(path: str, key: str, payload: bytes, *, sha256: str | None = None) -> dict:
    return {
        "path": path,
        "s3_key": key,
        "sha256": sha256 or hashlib.sha256(payload).hexdigest(),
        "row_count": len(payload.splitlines()),
    }


def test_reelly_bronze_loads_projects_documents_and_availability() -> None:
    projects = _jsonl_payload(
        [
            {
                "scraped_at": "2026-05-05T00:00:00+00:00",
                "project_id": 123,
                "delta_reason": "new",
                "list_item": {"id": 123},
                "raw": {
                    "id": 123,
                    "name": "Project",
                    "Region": "Dubai",
                    "Area_name": "Business Bay",
                    "Coordinates": "25.186, 55.264",
                },
            }
        ]
    )
    documents = _jsonl_payload(
        [
            {
                "scraped_at": "2026-05-05T00:00:00+00:00",
                "project_id": 123,
                "endpoint": "projects",
                "field_path": "brochure",
                "name": "Brochure",
                "url": "https://api.reelly.io/file.pdf",
                "normalized_url": "https://api.reelly.io/file.pdf",
                "status": "download_skipped",
                "raw": {"url": "https://api.reelly.io/file.pdf"},
            }
        ]
    )
    availability = _jsonl_payload(
        [
            {
                "scraped_at": "2026-05-05T00:00:00+00:00",
                "project_id": 123,
                "endpoint": "evolutions/availability/floors",
                "candidate": 999,
                "status": "success",
                "raw": {"floors": []},
            }
        ]
    )
    clickhouse = FakeClickHouse()

    result = load_release_to_clickhouse(
        raw_manifest={
            "source": "reelly_supply",
            "run_id": "run-1",
            "files": [
                _file("reelly_supply_project_details.jsonl", "raw/projects.jsonl", projects),
                _file("reelly_supply_project_documents.jsonl", "raw/documents.jsonl", documents),
                _file(
                    "reelly_supply_matrix_availability.jsonl",
                    "raw/availability.jsonl",
                    availability,
                ),
            ],
        },
        s3=FakeS3(
            {
                "raw/projects.jsonl": projects,
                "raw/documents.jsonl": documents,
                "raw/availability.jsonl": availability,
            }
        ),
        clickhouse=clickhouse,
    )

    assert result["table_row_counts"] == {
        "reelly_projects_bronze": 1,
        "reelly_documents_bronze": 1,
        "reelly_matrix_availability_bronze": 1,
        # This payload has no prices or payment plan: nothing is invented for it.
        "reelly_project_unit_types_bronze": 0,
        "reelly_project_payment_plans_bronze": 0,
    }
    assert {call["table"] for call in clickhouse.insert_calls} == {
        "reelly_projects_bronze",
        "reelly_documents_bronze",
        "reelly_matrix_availability_bronze",
        "silver_reelly_projects",
    }
    assert any(
        "DELETE FROM `reelly_projects_bronze` WHERE `project_id` IN ('123')" in sql
        for sql in clickhouse.commands
    )
    assert any(
        "DELETE FROM `reelly_documents_bronze` WHERE `project_id` IN ('123')" in sql
        for sql in clickhouse.commands
    )
    assert any(
        "DELETE FROM `reelly_matrix_availability_bronze` WHERE `project_id` IN ('123')" in sql
        for sql in clickhouse.commands
    )
    project_call = next(
        call for call in clickhouse.insert_calls if call["table"] == "reelly_projects_bronze"
    )
    assert project_call["rows"][0][1:5] == (
        "run-1",
        "2026-05-05T00:00:00+00:00",
        "123",
        "new",
    )


def test_reelly_bronze_rejects_non_dubai_projects_and_their_children() -> None:
    projects = _jsonl_payload(
        [
            {
                "project_id": 123,
                "list_item": {"id": 123},
                "raw": {
                    "Region": "Dubai",
                    "Area_name": "Dubai Hills",
                    "Coordinates": "25.112,55.245",
                },
            },
            {
                "project_id": 456,
                "list_item": {"id": 456},
                "raw": {
                    "Region": "Dubai",
                    "Area_name": "Yas Island",
                    "Coordinates": "24.514,54.620",
                },
            },
            {
                "project_id": 789,
                "list_item": {"id": 789},
                "raw": {
                    "Region": "Bali",
                    "Area_name": "Ubud, Bali",
                    "Coordinates": "-8.55,115.24",
                },
            },
        ]
    )
    documents = _jsonl_payload(
        [
            {"project_id": project_id, "url": f"https://example.com/{project_id}.pdf"}
            for project_id in (123, 456, 789)
        ]
    )
    clickhouse = FakeClickHouse()

    result = load_release_to_clickhouse(
        raw_manifest={
            "source": "reelly_supply",
            "run_id": "dubai-only",
            "files": [
                _file("reelly_supply_project_details.jsonl", "raw/projects.jsonl", projects),
                _file("reelly_supply_project_documents.jsonl", "raw/documents.jsonl", documents),
            ],
        },
        s3=FakeS3({"raw/projects.jsonl": projects, "raw/documents.jsonl": documents}),
        clickhouse=clickhouse,
    )

    assert result["table_row_counts"]["reelly_projects_bronze"] == 1
    assert result["table_row_counts"]["reelly_documents_bronze"] == 1
    project_call = next(
        call for call in clickhouse.insert_calls if call["table"] == "reelly_projects_bronze"
    )
    document_call = next(
        call for call in clickhouse.insert_calls if call["table"] == "reelly_documents_bronze"
    )
    assert [row[3] for row in project_call["rows"]] == ["123"]
    assert [row[3] for row in document_call["rows"]] == ["123"]
    assert any("'123', '456', '789'" in sql for sql in clickhouse.commands)


def test_reelly_bronze_rejects_checksum_before_mutation() -> None:
    projects = _jsonl_payload([{"project_id": 123}])
    clickhouse = FakeClickHouse()

    with pytest.raises(ValueError, match="checksum mismatch"):
        load_release_to_clickhouse(
            raw_manifest={
                "source": "reelly_supply",
                "run_id": "run-1",
                "files": [
                    _file(
                        "reelly_supply_project_details.jsonl",
                        "raw/projects.jsonl",
                        projects,
                        sha256="bad",
                    )
                ],
            },
            s3=FakeS3({"raw/projects.jsonl": projects}),
            clickhouse=clickhouse,
        )

    assert not clickhouse.commands
    assert not clickhouse.insert_calls


def test_reelly_loader_produces_unit_prices_and_payment_plans_for_serving() -> None:
    """The supply/off-plan serving tables had readers but no producer (F02)."""
    projects = _jsonl_payload(
        [
            {
                "scraped_at": "2026-05-05T00:00:00+00:00",
                "project_id": 2961,
                "list_item": {
                    "id": 2961,
                    "Project_name": "Helvetia Verde",
                    "Developers_name": "DHG Properties",
                    "Area_name": "Meydan City",
                    "sale_status": "On sale",
                    "min_price": "0",
                    "Completion_time": 1838160000000,
                    "Starting_price": [
                        {"Price_from_AED": 1579516, "Price_to_AED": 0},
                        {"Price_from_AED": "2,100,000", "Price_to_AED": 2600000},
                    ],
                },
                "raw": {
                    "Region": "Dubai",
                    "Coordinates": "25.1792,55.3225",
                    "Units_types": ["1 bedroom", "2 bedroom"],
                    "Payment_plans": [
                        {"Name": "On booking", "Payment_time": "On booking",
                         "Percent_of_payment": "20", "Order": 1},
                        {"Name": "During construction", "Payment_time": "During construction",
                         "Percent_of_payment": "40", "Order": 2},
                        {"Name": "Upon Handover", "Payment_time": "Upon Handover",
                         "Percent_of_payment": "40", "Order": 3},
                    ],
                },
            }
        ]
    )  # fmt: skip
    clickhouse = FakeClickHouse()
    result = load_release_to_clickhouse(
        raw_manifest={
            "source": "reelly_supply",
            "run_id": "run-2",
            "manifest_s3_key": "raw/source=reelly_supply/manifests/run-2.json",
            "files": [_file("reelly_supply_project_details.jsonl", "raw/projects.jsonl", projects)],
        },
        s3=FakeS3({"raw/projects.jsonl": projects}),
        clickhouse=clickhouse,
    )
    assert result["table_row_counts"]["reelly_project_unit_types_bronze"] == 2
    assert result["table_row_counts"]["reelly_project_payment_plans_bronze"] == 3

    def rows(table: str) -> list[dict[str, Any]]:
        call = next(c for c in clickhouse.insert_calls if c["table"] == table)
        return [dict(zip(call["column_names"], row, strict=True)) for row in call["rows"]]

    units = rows("reelly_project_unit_types_bronze")
    assert [(u["unit_type"], u["price_from_aed"], u["price_to_aed"]) for u in units] == [
        ("1 bedroom", 1579516.0, None),  # Reelly's 0 "to" price is unknown, not AED 0
        ("2 bedroom", 2100000.0, 2600000.0),
    ]
    plan = rows("reelly_project_payment_plans_bronze")
    assert [(s["order_index"], s["payment_time"], s["percent_of_payment"]) for s in plan] == [
        (1, "On booking", 20.0),
        (2, "During construction", 40.0),
        (3, "Upon Handover", 40.0),
    ]
    assert plan[0]["raw_manifest_s3_key"] == "raw/source=reelly_supply/manifests/run-2.json"
    project = rows("reelly_projects_bronze")[0]
    # The catalogue's min_price of 0 means unknown; the lowest unit price stands in for it.
    assert (project["project_name"], project["region"], project["min_price_aed"]) == (
        "Helvetia Verde",
        "Dubai",
        1579516.0,
    )
    assert (project["latitude"], project["longitude"]) == (25.1792, 55.3225)
    assert project["completion_time"].startswith("2028-")
    # Steps of a project are replaced with the project, never accumulated across loads.
    assert any(
        "DELETE FROM `reelly_project_payment_plans_bronze` WHERE `project_id` IN ('2961')" in sql
        for sql in clickhouse.commands
    )
    assert rows("silver_reelly_projects")[0]["project_name"] == "Helvetia Verde"


@pytest.mark.skipif(
    os.environ.get("HOLOCRON_CLICKHOUSE_INTEGRATION") != "1",
    reason="set HOLOCRON_CLICKHOUSE_INTEGRATION=1 with local ClickHouse",
)
def test_reelly_serving_tables_load_into_the_repository_schema(warehouse) -> None:  # noqa: F811
    """Types and columns of every produced table match the warehouse DDL, twice over."""
    raw = {
        "Region": "Dubai",
        "Units_types": ["Studio"],
        "Payment_plans": [{"Payment_time": "On booking", "Percent_of_payment": "100", "Order": 1}],
    }
    for run in ("run-a", "run-b"):  # a reload replaces, it does not duplicate
        payload = _jsonl_payload([{
            "scraped_at": "2026-05-05T00:00:00+00:00", "project_id": 7,
            "list_item": {"Project_name": "P", "Completion_time": "2028-03-31T00:00:00+00:00",
                          "Starting_price": [{"Price_from_AED": 900000}]},
            "raw": raw,
        }])  # fmt: skip
        load_release_to_clickhouse(
            raw_manifest={"source": "reelly_supply", "run_id": run, "files": [
                _file("reelly_supply_project_details.jsonl", "raw/p.jsonl", payload)]},
            s3=FakeS3({"raw/p.jsonl": payload}),
            clickhouse=warehouse,
        )  # fmt: skip
    counts = {
        table: warehouse.query(f"SELECT count() FROM {table}").result_rows[0][0]
        for table in ("reelly_projects_bronze", "reelly_project_unit_types_bronze",
                      "reelly_project_payment_plans_bronze")
    }  # fmt: skip
    assert counts == {
        "reelly_projects_bronze": 1,
        "reelly_project_unit_types_bronze": 1,
        "reelly_project_payment_plans_bronze": 1,
    }
    silver = warehouse.query(
        "SELECT project_name, completion_time, min_price_aed FROM silver_reelly_projects FINAL"
    ).result_rows
    assert [(name, str(when)[:10], price) for name, when, price in silver] == [
        ("P", "2028-03-31", 900000.0)
    ]
