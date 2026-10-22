import json
from datetime import date

from app.supply import rankings, service


async def test_catalogue_keeps_source_identity_facets_and_pending_financial_fields(warehouse):
    await warehouse.command("""CREATE TABLE reelly_projects_bronze (
        project_key String, project_id String, run_id String, scraped_at String,
        list_item_json String, raw_json String, region String, area_name String,
        latitude Nullable(Float64), longitude Nullable(Float64)
    ) ENGINE = MergeTree ORDER BY project_key""")
    for identifier, region in [("1", "Dubai"), ("2", "Abu Dhabi")]:
        payload = {
            "id": identifier,
            "Project_name": "Test Tower",
            "Developers_name": "Emaar",
            "Area_name": "Test Area",
            "Status": "Under construction",
            "sale_status": "On sale",
            "min_price": 1000000,
        }
        await warehouse.insert(
            "reelly_projects_bronze",
            [
                [
                    identifier,
                    identifier,
                    "run",
                    "2026-09-28",
                    json.dumps(payload),
                    "{}",
                    region,
                    "Test Area",
                    25.1,
                    55.2,
                ]
            ],
        )
    await warehouse.command(
        "CREATE TABLE ch_transactions (instance_date Date, trans_group_en String, actual_worth Float64) ENGINE = MergeTree ORDER BY instance_date"
    )
    await warehouse.insert("ch_transactions", [[date.today(), "Sales", 100]])
    await warehouse.command("""CREATE TABLE ch_projects (
        project_id Int32, project_name_en String, developer_id Int32, developer_name String,
        area_name_en String, project_start_date Nullable(Date), completion_date Nullable(Date),
        project_end_date Nullable(Date), percent_completed Float64, no_of_units Int32
    ) ENGINE = MergeTree ORDER BY project_id""")
    await warehouse.command(
        "CREATE TABLE ch_developers (developer_id Int32, developer_name_en String) ENGINE = MergeTree ORDER BY developer_id"
    )
    page = await service.list_supply_catalogue_projects(area="Test Area", include_past=True)
    assert page.total == 1
    assert [p.project_id for p in page.projects] == [1]
    assert page.developers == ["Emaar"]
    detail = await service.get_supply_catalogue_project(None, 1)
    assert detail.project_name == "Test Tower"
    assert detail.developer_score is None  # Existing lookup is deliberately still a stub.
    assert detail.estimated_project_irr == "~"
    assert all(item.status == "Pending" for item in detail.financial_risk_metrics)
    assert await rankings._developer_logo_lookup() == {}
    assert await service._developer_score_lookup() == {}


async def test_developer_rankings_execute_fact_aggregation_and_keep_score_coverage(warehouse):
    await warehouse.command(
        "CREATE TABLE ch_transactions (instance_date Date, trans_group_en String, actual_worth Float64) ENGINE = MergeTree ORDER BY instance_date"
    )
    await warehouse.insert("ch_transactions", [[date(2026, 9, 1), "Sales", 100]])
    await warehouse.command("""CREATE TABLE ch_project_fact (
        developer_id Int32, developer_name String, sales_transaction_count_12m Int32,
        total_sales_volume_12m Float64, avg_sale_price_sqm_12m Float64,
        completion_date Nullable(Date), cancellation_date Nullable(Date), no_of_units Int32,
        percent_completed Float64, project_end_date Nullable(Date), project_start_date Nullable(Date)
    ) ENGINE = MergeTree ORDER BY developer_id""")
    await warehouse.insert(
        "ch_project_fact",
        [
            [
                1,
                "EMAAR DEVELOPMENT P.J.S.C.",
                20,
                20000000,
                10000,
                date(2025, 1, 1),
                None,
                100,
                100,
                date(2025, 2, 1),
                date(2023, 1, 1),
            ]
        ],
    )
    result = await rankings.get_developer_rankings(metric="units_sold")
    assert result.summary.end_date == date(2026, 9, 1)
    assert result.summary.total_developers == 1
    assert result.rankings[0].developer_name == "Emaar"
    assert result.rankings[0].units_sold == 20
    assert result.rankings[0].on_time_delivery_score == 100
    assert result.rankings[0].developer_logo_url is None
