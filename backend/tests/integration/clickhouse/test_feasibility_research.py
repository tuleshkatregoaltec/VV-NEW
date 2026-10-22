from datetime import date

from app.feasibility import research


async def test_research_filters_pricing_and_keeps_rental_band_minimum_sample(warehouse):
    await warehouse.command("""CREATE TABLE ch_transactions (
        transaction_id String, instance_date Date, area_name_en String, project_name_en String,
        rooms_en String, trans_group_en String, meter_sale_price Float64,
        procedure_area Float64, actual_worth Float64, property_usage_en String, reg_type_en String
    ) ENGINE = MergeTree ORDER BY transaction_id""")
    await warehouse.insert(
        "ch_transactions",
        [
            [
                "sale",
                date.today(),
                "Test Area",
                "Tower",
                "1 B/R",
                "Sales",
                10000,
                70,
                700000,
                "Residential",
                "Existing Properties",
            ],
            [
                "excluded",
                date.today(),
                "Test Area",
                "Tower",
                "1 B/R",
                "Mortgage",
                20000,
                70,
                1400000,
                "Residential",
                "Existing Properties",
            ],
        ],
    )
    assert await research.resolve_market_area("Test") == "Test Area"
    prices = await research.get_market_pricing("test area", "residential")
    assert [(r.unit_type, r.transaction_count, r.median_price_sqm) for r in prices] == [
        ("1br", 1, 10000)
    ]
    payload = await research.fetch_chat_research(
        "show_transactions", {"area": "Test", "limit": 200}
    )
    assert [r["transaction_id"] for r in payload["rows"]] == ["sale"]
    analytics = await research.fetch_chat_research("show_analytics", {"area": "Test"})
    assert analytics["by_rooms"][0]["txn_count"] == 1
    await warehouse.command("""CREATE TABLE ch_rent_contracts (
        area_name_en String, actual_area Float64, annual_amount Float64, contract_start_date Date
    ) ENGINE = MergeTree ORDER BY area_name_en""")
    await warehouse.insert(
        "ch_rent_contracts",
        [["Test Area", 55, 55000, date.today()]] * 5 + [["Test Area", 90, 90000, date.today()]] * 4,
    )
    rates = await research.get_rental_rates_by_size_band("test area")
    assert [(r.unit_type_proxy, r.contract_count, r.median_rent_psm_annual) for r in rates] == [
        ("studio", 5, 1000)
    ]
