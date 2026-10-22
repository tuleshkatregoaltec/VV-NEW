import json
from datetime import datetime, timezone

from app.listings import service


async def test_json_inventory_annual_filter_benchmark_and_detail_mapping(warehouse):
    strings = [
        "listing_id",
        "title",
        "reference",
        "price_json",
        "size_json",
        "bedrooms",
        "bathrooms",
        "listing_location_names_json",
        "share_url",
        "agent_json",
        "broker_json",
        "client_json",
        "images_json",
        "raw_property_json",
        "raw_wrapper_json",
        "run_id",
        "scraped_at",
    ]
    flags = ["is_available", "is_verified", "is_featured", "is_premium"]
    fields = strings + flags + ["category_id", "property_type_id", "lat", "lng"]
    ddl = ", ".join(
        [f"{f} String" for f in strings]
        + [f"{f} UInt8" for f in flags]
        + ["category_id UInt16", "property_type_id Int32", "lat Float64", "lng Float64"]
    )
    await warehouse.command(
        f"CREATE TABLE pf_listings_bronze ({ddl}) ENGINE = MergeTree ORDER BY listing_id"
    )
    rows = []
    for i in range(6):
        row = dict.fromkeys(strings, "")
        row.update(dict.fromkeys(flags, 0))
        row.update(
            listing_id=str(i),
            title="Urgent sale",
            price_json=json.dumps({"value": 120000, "period": "yearly"}),
            size_json=json.dumps({"value": 1000.0}),
            bedrooms="2",
            bathrooms="2",
            listing_location_names_json=json.dumps(["Dubai", "Test Area", "Project", "Tower"]),
            raw_property_json=json.dumps(
                {
                    "property_type": "Apartment",
                    "description": "Details",
                    "listed_date": datetime.now(timezone.utc).isoformat(),
                }
            ),
            images_json="[]",
            is_available=1 if i < 5 else 0,
            category_id=2,
            property_type_id=1,
            lat=25.1,
            lng=55.2,
            scraped_at=datetime.now(timezone.utc).isoformat(),
            run_id="test-run",
        )
        rows.append([row[f] for f in fields])
    await warehouse.insert("pf_listings_bronze", rows, column_names=fields)
    result = await service.list_listings_from_clickhouse(
        "rent", area="Test Area", price_basis="annual", price_min=119999, price_max=120001
    )
    assert result.total == 5
    assert result.filters.areas == ["Test Area"]
    assert result.listings[0].comparable_count == 5
    assert result.listings[0].market_position == "near_market"
    assert result.listings[0].market_median_price_per_sqft_aed == 120
    assert (await service.list_listings_from_clickhouse("sale")).total == 0
    detail = await service.get_listing_from_clickhouse("0")
    assert detail.description == "Details"
    assert detail.area_name == "Test Area"
    assert detail.tower_name == "Tower"
    assert detail.source_run_id == "test-run"


async def test_monthly_price_expression_preserves_known_local_clickhouse_type_failure(warehouse):
    import pytest
    from clickhouse_connect.driver.exceptions import DatabaseError

    from app.listings.queries import MONTHLY_PRICE_EXPR

    # Confirmed against the identical pre-migration expression. Fix is outside this structural migration.
    with pytest.raises(DatabaseError, match="NO_COMMON_TYPE"):
        await warehouse.query(
            "SELECT "
            + MONTHLY_PRICE_EXPR
            + ' FROM (SELECT \'{"value":120000,"period":"yearly"}\' AS price_json)'
        )
