from datetime import date

from app.analytics import charts, service
from app.project.service import get_similar_projects


async def test_chat_capabilities_execute_real_queries_with_existing_scope(warehouse):
    await warehouse.command("""
        CREATE TABLE ch_transactions (
            instance_date Date, area_name_en String, master_project_en String,
            project_name_en String, trans_group_en String, property_type_en String,
            property_usage_en String, actual_worth Float64, actual_area Float64,
            meter_sale_price Float64, rooms_en String, reg_type_en String,
            procedure_area Float64, district_name String,
            -- Uniform layer columns the chat capabilities read, as Holocron derives them.
            location_name String MATERIALIZED area_name_en,
            property_type String MATERIALIZED if(property_type_en = 'Unit', 'Apartment', ''),
            size_sqm Float64 MATERIALIZED procedure_area
        ) ENGINE = MergeTree ORDER BY (area_name_en, instance_date)
    """)
    today = date.today()
    rows = []
    for area, project, price in [
        ("Area A", "Reference", 1000000),
        ("Area B", "Comparable", 1100000),
    ]:
        rows.extend(
            [
                [
                    today,
                    area,
                    project,
                    project,
                    "Sales",
                    "Unit",
                    "Residential",
                    price,
                    100,
                    price / 100,
                    "1 B/R",
                    "Existing Properties",
                    100,
                    area,  # canonical district (market layer) ranks the areas
                ]
            ]
            * 10
        )
    # A mortgage is not an achieved sale price: neither the pulse nor the chart counts it.
    rows.append(
        [
            today,
            "Area A",
            "Reference",
            "Reference",
            "Mortgages",
            "Unit",
            "Residential",
            1000000,
            100,
            10000,
            "1 B/R",
            "Existing Properties",
            100,
            "Area A",
        ]
    )
    await warehouse.insert("ch_transactions", rows)
    pulse = await service.get_market_pulse("Area A")
    assert pulse["success"] is True
    assert pulse["transaction_count"] == 10
    assert pulse["median_price_aed"] == 1000000
    # 100 sqm = 1,076.4 sqft; AED 1,000,000 / 1,076.39 sqft = AED 929 per sqft.
    assert pulse["median_area_sqft"] == 1076.4
    assert pulse["median_price_per_sqft_aed"] == 929
    ranked = await service.rank_areas("volume", 2)
    assert ranked["success"] is True
    # The mortgage in Area A is not a sale, so both areas rank on 10 sales.
    assert {item["area"]: item["transaction_count"] for item in ranked["top_areas"]} == {
        "Area A": 10,
        "Area B": 10,
    }
    chart = await charts.sales_chart(
        {"group_by": "area", "metric": "transaction_count", "min_records": 1}
    )
    assert sorted((point["label"], point["value"]) for point in chart["data"]["points"]) == [
        ("Area A", 10),
        ("Area B", 10),
    ]
    similar = await get_similar_projects("Reference")
    assert similar["success"] is True
    assert similar["reference_project"]["name"] == "Reference"
    assert similar["similar_projects"][0]["project"] == "Comparable"
    assert similar["similar_projects"][0]["price_difference_aed"] == 100000


async def test_project_chart_aggregates_pipeline_without_chat_context(warehouse):
    await warehouse.command(
        "CREATE TABLE ch_project_fact (area_name_en String, no_of_units UInt32) ENGINE = MergeTree ORDER BY area_name_en"
    )
    await warehouse.insert("ch_project_fact", [["Area A", 100], ["Area A", 200], ["Area B", 50]])
    chart = await charts.project_chart({"group_by": "area", "metric": "pipeline_units"})
    assert chart["data"]["points"] == [
        {"label": "Area A", "value": 300.0, "record_count": 2},
        {"label": "Area B", "value": 50.0, "record_count": 1},
    ]
    assert chart["data"]["series"] == []
