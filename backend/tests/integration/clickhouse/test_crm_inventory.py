from datetime import date, datetime, timezone

from app.crm import opportunities, opportunity_queries
from app.listings.service import get_inventory_freshness
from app.supply.service import get_catalog_freshness, list_matching_inventory


async def test_crm_legacy_identity_and_sales_inventory_execute_in_clickhouse(warehouse):
    for suffix in ("", "_legacy"):
        await warehouse.command(
            f"CREATE TABLE dxbi_rental_unit_candidates{suffix} (event_key String, unit_candidate_key String) ENGINE = ReplacingMergeTree ORDER BY event_key"
        )
    await warehouse.insert("dxbi_rental_unit_candidates_legacy", [["old-lead", "old-unit"]])
    assert await opportunities.resolve_candidate_key("old-lead") == "old-unit"
    await warehouse.insert("dxbi_rental_unit_candidates", [["old-lead", "current-unit"]])
    assert await opportunities.resolve_candidate_key("old-lead") == "current-unit"
    await warehouse.command("""CREATE TABLE dxbi_sales_unit_events (
        sale_key String, building_key String, unit_number String, transaction_date Date,
        building_name String, area_name String, property_type String, bedrooms String,
        size_sqft Float64, sale_amount_aed Int64, ltv_pct Nullable(Float64), seller_type String,
        detail_url String
    ) ENGINE = ReplacingMergeTree ORDER BY sale_key""")
    await warehouse.insert(
        "dxbi_sales_unit_events",
        [
            [
                "sale-old",
                "tower",
                "2A",
                date(2025, 1, 1),
                "Tower",
                "JBR",
                "Apartment",
                "2",
                1000,
                1000000,
                None,
                "Individual",
                "",
            ],
            [
                "sale-new",
                "tower",
                "2A",
                date(2026, 1, 1),
                "Tower",
                "JBR",
                "Apartment",
                "2",
                1000,
                1200000,
                None,
                "Individual",
                "",
            ],
        ],
    )
    rows = await opportunities.list_sales_inventory({"area": "JBR"})
    assert len(rows) == 1
    assert (rows[0]["normalized_unit"], rows[0]["sale_amount_aed"]) == ("2a", 1200000)
    exact = await opportunities.get_instruction_evidence({"building": "Tow", "unit_number": "2a"})
    assert exact[0]["sale_key"] == "sale-new"
    assert await opportunity_queries.fetch_sales_inventory({"area": "Elsewhere"}) == []


async def test_matching_inventory_preserves_dubai_eligibility_and_freshness(warehouse):
    now = datetime.now(timezone.utc).isoformat()
    await warehouse.command(
        "CREATE TABLE pf_listings_bronze (scraped_at String) ENGINE = MergeTree ORDER BY scraped_at"
    )
    await warehouse.insert("pf_listings_bronze", [[now]])
    assert (await get_inventory_freshness())[1] is True
    await warehouse.command("""CREATE TABLE reelly_projects_bronze (
        project_id Int64, project_name String, developer_name String, area_name String,
        sale_status String, units_in_sale Int32, min_price_aed Int64, max_price_aed Int64,
        completion_date String, readiness_progress Float64, website_url String, scraped_at String,
        region String, latitude Nullable(Float64), longitude Nullable(Float64)
    ) ENGINE = MergeTree ORDER BY project_id""")
    await warehouse.command("""CREATE TABLE reelly_project_unit_types_bronze (
        project_id Int64, unit_type_key String, unit_bedrooms String, unit_type String,
        price_from_aed Int64, price_to_aed Int64, area_from_sqft Float64, area_to_sqft Float64,
        units_amount Int32, scraped_at String
    ) ENGINE = MergeTree ORDER BY unit_type_key""")
    for project_id, area, region, latitude in [
        (1, "JBR", "Dubai", 25.0),
        (2, "Sharjah", "Dubai", 25.0),
        (3, "JBR", "Abu Dhabi", 25.0),
        (4, "JBR", "Dubai", 23.0),
    ]:
        await warehouse.insert(
            "reelly_projects_bronze",
            [
                [
                    project_id,
                    f"Project {project_id}",
                    "Developer",
                    area,
                    "On sale",
                    5,
                    1000000,
                    2000000,
                    "2028",
                    20,
                    "",
                    now,
                    region,
                    latitude,
                    55.0,
                ]
            ],
        )
    inventory = await list_matching_inventory("JBR")
    assert [row["project_id"] for row in inventory] == [1]
    assert await get_catalog_freshness() is not None
