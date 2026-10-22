import json
from datetime import date, datetime

from app.plots import evidence
from app.plots import service as plots
from app.project import service as projects


async def test_plot_search_geometry_and_details_share_identity_without_triggering_bulk_valuation(
    warehouse, monkeypatch
):
    monkeypatch.setattr(plots, "_dda_geometry_cache", None)
    await warehouse.command("""CREATE TABLE ch_gis_land_plots (
        plot_number String, community_name String, project_name String,
        plot_area_sqm Float64, max_gfa_sqm Float64, max_gfa_sqft Float64,
        max_height String, max_coverage String, gfa_type String, is_verified UInt8,
        land_use String, coordinates String, general_notes String,
        site_plan_issue_date Nullable(Date), site_plan_expiry_date Nullable(Date)
    ) ENGINE = ReplacingMergeTree ORDER BY plot_number""")
    await warehouse.command("""CREATE TABLE ch_buildings (
        parcel_id String, floors Int32, units Int32, built_up_area Float64, actual_area Float64
    ) ENGINE = MergeTree ORDER BY parcel_id""")
    coordinates = [[55.2, 25.1], [55.3, 25.1], [55.3, 25.2], [55.2, 25.1]]
    await warehouse.insert(
        "ch_gis_land_plots",
        [
            [
                "001",
                "Test's Community",
                "Development",
                1000,
                5000,
                53819.5,
                "G+10",
                "60%",
                "Residential",
                1,
                '[{"type":"Residential","use":"Apartment"}]',
                json.dumps(coordinates),
                "[]",
                None,
                None,
            ]
        ],
    )
    await warehouse.insert("ch_buildings", [["001", 10, 20, 2000, 1000]])
    found = await plots.search_plots(
        community="test's community", min_plot_area_sqm=999, max_plot_area_sqm=1001
    )
    assert [item.plot_number for item in found] == ["001"]
    assert await plots.search_plots(community="Elsewhere") == []
    detail = await plots.get_plot_details("001")
    assert (detail.plot_number, detail.far, detail.inferred_usage) == ("001", 5.0, "residential")
    assert detail.coordinates == coordinates[:-1]  # Feasibility strips the closing point.
    polygons = await plots.get_dda_plots_for_area(area="Test's Community")
    assert polygons[0].plot_number == detail.plot_number
    assert polygons[0].coordinates == coordinates  # The map contract retains a closed ring.
    assert polygons[0].development_status == "developed_confirmed"
    assert polygons[0].valuation_note == "Valuation deferred for bulk map rendering."
    assert await plots.get_dda_plots_for_area(west=54, south=24, east=54.5, north=24.5) == []
    assert await plots.list_communities() == ["Test's Community"]


async def test_land_evidence_preserves_usage_fallback_units_and_far_calculation(warehouse):
    await warehouse.command("""CREATE TABLE ch_transactions (
        transaction_id String, instance_date Date, area_name_en String, project_name_en String,
        procedure_name_en String, property_usage_en String, procedure_area Float64,
        actual_worth Float64, nearest_landmark_en String, trans_group_en String, property_type_en String
    ) ENGINE = MergeTree ORDER BY transaction_id""")
    await warehouse.insert(
        "ch_transactions",
        [
            [
                "land-sale",
                date.today(),
                "Test Area",
                "Comparable",
                "Sell",
                "Commercial",
                10764,
                2000000,
                "",
                "Sales",
                "Land",
            ]
        ],
    )
    rows = await evidence.get_land_sale_evidence(
        "Test Area", plot_area_sqm=1000, usage="residential", target_far=4
    )
    assert len(rows) == 1  # Existing fallback broadens usage only when the first query is empty.
    assert (rows[0].plot_area_sqm, rows[0].price_sqm, rows[0].price_per_gfa_sqm) == (
        1000,
        2000,
        500,
    )
    assert rows[0].property_usage == "Commercial"


async def test_synthetic_project_ids_resolve_distinct_reelly_and_legacy_sources(warehouse):
    await warehouse.command("""CREATE TABLE silver_reelly_projects (
        project_id String, project_name String, area_name String, developer_name String,
        latitude Float64, longitude Float64, sale_status String, completion_time DateTime,
        readiness_progress Float64, region String
    ) ENGINE = ReplacingMergeTree ORDER BY project_id""")
    await warehouse.insert(
        "silver_reelly_projects",
        [
            [
                "42",
                "Reelly Tower",
                "JBR",
                "Developer",
                25.1,
                55.2,
                "On sale",
                datetime(2028, 1, 1),
                20,
                "Dubai",
            ]
        ],
    )
    await warehouse.command("""CREATE TABLE ch_geo_buildings (
        property_id UInt64, project_name_en String, area_name_en String, developer_name String,
        lat Float64, lng Float64, building_name_en String
    ) ENGINE = MergeTree ORDER BY property_id""")
    await warehouse.insert(
        "ch_geo_buildings", [[42, "Legacy Tower", "JBR", "Developer", 25.2, 55.3, "Building A"]]
    )
    reelly = await projects.get_project_details(-9000000000042)
    legacy = await projects.get_project_details(-42)
    assert (reelly.project_id, reelly.project_name, reelly.project_number) == (
        -9000000000042,
        "Reelly Tower",
        "42",
    )
    assert reelly.completion_date == date(2028, 1, 1)
    assert (legacy.project_id, legacy.project_name, legacy.geo_pins[0].property_id) == (
        -42,
        "Legacy Tower",
        42,
    )
    assert await projects.get_project_details(-9000000000043) is None
    # Catalogue and radar share the actual geographic CTE: nearby PF coordinates
    # refine Reelly coordinates, while unmatched legacy projects keep their centroid.
    await warehouse.command(
        "ALTER TABLE silver_reelly_projects ADD COLUMN units_in_sale UInt32 DEFAULT 5"
    )
    await warehouse.command("""CREATE TABLE geo_location_nodes (
        normalized_name String, latitude Float64, longitude Float64,
        location_type String, canonical_location_id String
    ) ENGINE = MergeTree ORDER BY canonical_location_id""")
    await warehouse.insert(
        "geo_location_nodes", [["reellytower", 25.1001, 55.2001, "TOWER", "pf-reelly"]]
    )
    catalogue = {item.project_name: item for item in await projects.list_all_projects()}
    assert catalogue["Reelly Tower"].project_id == reelly.project_id
    assert (catalogue["Reelly Tower"].latitude, catalogue["Reelly Tower"].longitude) == (
        25.1001,
        55.2001,
    )
    assert (catalogue["Legacy Tower"].project_id, catalogue["Legacy Tower"].latitude) == (-42, 25.2)
