from datetime import datetime

import pytest

from app.area.service import get_area_detail, list_areas


async def test_area_filters_sort_and_detail_use_the_same_normalized_name(warehouse):
    await warehouse.command("""CREATE TABLE ch_area_fact (
        area_name_en String, area_id Int32, active_projects UInt32, completed_projects UInt32,
        overdue_projects UInt32, pipeline_units UInt32, units_delivering_next_12_months UInt32,
        active_developers UInt32, avg_completion_pct Float64, avg_delivery_confidence Float64,
        sales_transaction_count_12m UInt32, rental_contract_count_12m UInt32,
        total_sales_volume_12m Float64, median_sale_price Float64, median_annual_rent Float64,
        avg_sale_price_sqm_12m Float64, avg_rent_price_sqm_12m Float64, gross_yield_pct Float64,
        top_developers Array(String), top_master_projects Array(String)
    ) ENGINE = MergeTree ORDER BY area_name_en""")
    await warehouse.insert(
        "ch_area_fact",
        [["Business Bay", 1, 200, 1000000], ["Other Area", 2, 100, 2000000]],
        column_names=["area_name_en", "area_id", "pipeline_units", "total_sales_volume_12m"],
    )
    await warehouse.command("""CREATE TABLE ch_project_fact (
        area_name_en String, project_id Int32, project_name_en String, developer_name String,
        master_project_en String, completion_status String, pipeline_status String,
        no_of_units UInt32, percent_completed Float64, estimated_delivery_confidence Float64,
        total_sales_volume_12m Float64, median_sale_price Float64, median_annual_rent Float64,
        gross_yield_pct Float64
    ) ENGINE = MergeTree ORDER BY project_id""")
    await warehouse.insert(
        "ch_project_fact",
        [["Business Bay", 1, "Tower", "Builder", 200]],
        column_names=[
            "area_name_en",
            "project_id",
            "project_name_en",
            "developer_name",
            "no_of_units",
        ],
    )
    assert [area.area_name for area in (await list_areas(sort_by="volume")).areas] == [
        "Other Area",
        "Business Bay",
    ]
    filtered = await list_areas(search=" business ", limit=1)
    assert (filtered.total, filtered.areas[0].pipeline_units) == (1, 200)
    detail = await get_area_detail("business-bay")
    assert detail.area_name == "Business Bay"
    assert detail.projects[0].project_id == 1
    assert detail.developer_exposure[0].pipeline_units == 200
    with pytest.raises(ValueError, match="Area not found"):
        await get_area_detail("Missing")


async def test_area_list_is_one_row_per_official_district_across_source_vocabularies(
    warehouse, install_serving
):
    """Pulse names the district (Marsa Dubai) and open data the community (DUBAI MARINA); the
    served area list must show one district, not two areas splitting its sales."""
    await install_serving(
        tables=("market_sale_events", "market_rental_events", "dld_od_projects_bronze"),
        views=("ch_transactions", "ch_rent_contracts", "ch_projects", "ch_project_fact",
               "ch_area_fact"),
    )  # fmt: skip
    sale_columns = [
        "row_key", "transaction_id", "instance_date", "trans_group_en", "actual_worth",
        "area_name_en", "master_project_en", "is_served", "district_name", "district_id",
        "community_key",
    ]  # fmt: skip
    await warehouse.insert(
        "market_sale_events",
        [
            ["s1", "s1", datetime(2026, 9, 1), "Sales", 2_000_000, "Marsa Dubai", "Dubai Marina",
             1, "Marsa Dubai", 330, "dubai marina"],
            ["s2", "s2", datetime(2026, 9, 2), "Sales", 3_000_000, "DUBAI MARINA", "",
             1, "Marsa Dubai", 330, "dubai marina"],
            ["s3", "s3", datetime(2026, 9, 3), "Sales", 4_000_000, "BUSINESS BAY", "",
             1, "Business Bay", 345, "business bay"],
        ],
        column_names=sale_columns,
    )  # fmt: skip
    await warehouse.insert(
        "market_rental_events",
        [["r1", "r1", datetime(2026, 9, 1), datetime(2026, 9, 1), datetime(2027, 8, 31),
          100_000, "Marsa Dubai", 1, "Marsa Dubai"]],
        column_names=["row_key", "contract_id", "registration_date", "contract_start_date",
                      "contract_end_date", "annual_amount", "area_name_en", "is_served",
                      "district_name"],
    )  # fmt: skip
    await warehouse.insert(
        "dld_od_projects_bronze",
        [["p1", "run", "2026-09-20T00:00:00", "dld_open_data", 7, 70, "Marina Tower",
          "ACTIVE", 120, "Marsa Dubai", 330]],
        column_names=["row_key", "run_id", "scraped_at", "source_system", "project_number",
                      "project_id", "project_en", "project_status", "cnt_unit", "area_en",
                      "area_id"],
    )  # fmt: skip
    rows = (
        await warehouse.query(
            "SELECT area_name_en, area_id, sales_transaction_count_12m, rental_contract_count_12m,"
            " active_projects FROM ch_area_fact ORDER BY area_name_en"
        )
    ).result_rows
    assert rows == [("Business Bay", 345, 1, 0, 0), ("Marsa Dubai", 330, 2, 1, 1)]
    listed = await list_areas(sort_by="transactions")
    assert [area.area_name for area in listed.areas] == ["Marsa Dubai", "Business Bay"]
