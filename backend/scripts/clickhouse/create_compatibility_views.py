"""Create app-facing ClickHouse compatibility views on ph_box.

The production data server keeps raw/source tables as bronze tables. The app
still has a broad read surface built around the historical `ch_*` tables, so
these views provide that contract without mutating bronze data.
"""

from __future__ import annotations

import argparse
import time
from collections.abc import Sequence

import clickhouse_connect

from app.config import settings

VIEW_DDL: Sequence[tuple[str, str]] = (
    # Sales and rentals are served from the canonical market layer built by Holocron
    # (holocron/holocron/market): DXBI and DLD reconciled, duplicates removed, labels
    # normalised. Unserved rows (dxbi_probable_duplicate) stay in the tables for audit. The
    # tables carry every column these views used to expose, plus match provenance.
    (
        "ch_transactions",
        """
        CREATE OR REPLACE VIEW ch_transactions AS
        SELECT * FROM market_sale_events WHERE is_served = 1
        """,
    ),
    (
        "ch_rent_contracts",
        """
        CREATE OR REPLACE VIEW ch_rent_contracts AS
        SELECT * FROM market_rental_events WHERE is_served = 1
        """,
    ),
    (
        "ch_projects",
        """
        CREATE OR REPLACE VIEW ch_projects AS
        SELECT
            project_id,
            toString(project_number) AS project_number,
            project_en AS project_name_en,
            project_ar AS project_name_ar,
            adoption_date,
            start_date AS project_start_date,
            end_date AS project_end_date,
            completion_date,
            inspection_date,
            project_status,
            prj_type_en AS project_type_en,
            prj_type_ar AS project_type_ar,
            percent_completed,
            project_value,
            escrow_account_number,
            cnt_total AS total_units,
            cnt_unit AS no_of_units,
            cnt_building AS no_of_buildings,
            cnt_villa AS no_of_villas,
            cnt_land AS no_of_lands,
            developer_number,
            developer_id,
            if(developer_name != '', developer_name, developer_en) AS developer_name,
            developer_en AS developer_name_en,
            developer_ar AS developer_name_ar,
            master_developer_id,
            master_developer_number,
            master_developer_name,
            area_id,
            area_en AS area_name_en,
            area_ar AS area_name_ar,
            zone_en,
            zone_ar,
            master_project_en,
            master_project_ar,
            description_en,
            description_ar,
            project_type_id,
            project_classification_id,
            project_classification_ar,
            escrow_agent_id,
            escrow_agent_name,
            cancellation_date,
            property_id,
            zoning_authority_id,
            zoning_authority_en,
            zoning_authority_ar,
            row_key,
            run_id,
            scraped_at
        -- One row per DLD project number: the bronze table keeps the Pulse snapshot and
        -- every open-data snapshot (and repeated rows within one run), which double counted
        -- projects and units. The latest snapshot wins; open data carries no project_id, so
        -- the Pulse id is kept for the same project number.
        FROM (
            SELECT * REPLACE (
                if(project_id != 0, project_id, max(project_id) OVER (PARTITION BY project_number))
                    AS project_id
            )
            FROM dld_od_projects_bronze
            ORDER BY project_number, scraped_at DESC, row_key
            LIMIT 1 BY project_number
        )
        """,
    ),
    (
        "ch_developers",
        """
        CREATE OR REPLACE VIEW ch_developers AS
        SELECT
            developer_id,
            developer_number,
            developer_en AS developer_name,
            if(developer_name_en != '', developer_name_en, developer_en) AS developer_name_en,
            if(developer_name_ar != '', developer_name_ar, developer_ar) AS developer_name_ar,
            developer_en,
            developer_ar,
            registration_date,
            license_number,
            license_issue_date,
            license_expiry_date,
            license_source_id,
            license_source_en,
            license_source_ar,
            license_type_id,
            license_type_en,
            license_type_ar,
            legal_status,
            legal_status_en,
            legal_status_ar,
            chamber_of_commerce_no,
            participant_id,
            phone,
            fax,
            webpage,
            row_key,
            run_id,
            scraped_at
        FROM dld_od_developers_bronze
        """,
    ),
    (
        "ch_buildings",
        """
        CREATE OR REPLACE VIEW ch_buildings AS
        SELECT
            property_id,
            property_id AS building_id,
            building_number,
            building_number AS building_name_en,
            '' AS building_name_ar,
            project_id,
            project_number,
            project_en AS project_name_en,
            project_ar AS project_name_ar,
            area_id,
            area_en AS area_name_en,
            area_ar AS area_name_ar,
            property_type_id,
            property_type_en,
            property_type_ar,
            prop_sub_type_id AS property_sub_type_id,
            prop_sub_type_en AS property_sub_type_en,
            prop_sub_type_ar AS property_sub_type_ar,
            actual_area,
            built_up_area,
            common_area,
            actual_common_area,
            floors,
            bld_levels,
            flats AS units,
            rooms,
            rooms_en,
            rooms_ar,
            offices,
            shops,
            car_parks,
            elevators,
            swimming_pools,
            is_free_hold,
            is_free_hold_en,
            is_free_hold_ar,
            is_lease_hold,
            is_lease_hold_en,
            is_lease_hold_ar,
            parcel_id,
            row_key,
            run_id,
            scraped_at
        FROM dld_od_buildings_bronze
        """,
    ),
    (
        "ch_units",
        """
        CREATE OR REPLACE VIEW ch_units AS
        SELECT
            property_id AS unit_id,
            property_id,
            unit_number,
            unit_number AS unit_name_en,
            parent_property_id AS building_id,
            building_number,
            building_number AS building_name_en,
            project_id,
            toString(project_id) AS project_number,
            project_en AS project_name_en,
            project_ar AS project_name_ar,
            area_id,
            area_en AS area_name_en,
            area_ar AS area_name_ar,
            property_type_id,
            property_type_en,
            property_type_ar,
            property_sub_type_id,
            prop_sub_type_en AS property_sub_type_en,
            prop_sub_type_ar AS property_sub_type_ar,
            rooms,
            rooms_en,
            rooms_ar,
            actual_area,
            common_area,
            actual_common_area,
            floor,
            parent_property_id,
            grandparent_property_id,
            creation_date,
            parcel_id,
            is_free_hold,
            is_lease_hold,
            is_registered,
            master_project_id,
            master_project_en,
            master_project_ar,
            land_type_id,
            land_type_en,
            land_type_ar,
            row_key,
            run_id,
            scraped_at
        FROM dld_od_units_bronze
        """,
    ),
    (
        "ch_gis_land_plots",
        """
        CREATE OR REPLACE VIEW ch_gis_land_plots AS
        SELECT
            plot_number,
            old_numbers,
            project_name,
            community_name,
            master_developer,
            plot_area_sqm,
            plot_area_sqft,
            max_gfa_sqm,
            max_gfa_sqft,
            max_height,
            max_coverage,
            site_plan_issue_date,
            site_plan_expiry_date,
            side1_building,
            side1_podium,
            side2_building,
            side2_podium,
            side3_building,
            side3_podium,
            side4_building,
            side4_podium,
            land_use,
            general_notes,
            coordinates,
            landuse_symbols,
            gfa_type,
            is_verified,
            verify_comments
        FROM dda_land_plots
        """,
    ),
    (
        "ch_geo_buildings",
        """
        CREATE OR REPLACE VIEW ch_geo_buildings AS
        WITH
            JSONExtractString(payload_json, 'Building', 'building_coordinates') AS coords,
            splitByChar(',', coords) AS parts
        SELECT
            toUInt64OrZero(JSONExtractString(payload_json, 'Building', 'location_id')) AS property_id,
            JSONExtractString(payload_json, 'Building', 'location') AS building_name_en,
            JSONExtractString(payload_json, 'Building', 'project') AS project_name_en,
            JSONExtractString(payload_json, 'Building', 'area_name') AS area_name_en,
            toFloat64OrZero(parts[1]) AS lat,
            toFloat64OrZero(parts[2]) AS lng,
            toFloat64OrZero(parts[1]) AS latitude,
            toFloat64OrZero(parts[2]) AS longitude,
            JSONExtractString(payload_json, 'Building', 'dev') AS developer_name,
            JSONExtractString(payload_json, 'Building', 'logo_url') AS logo_url,
            JSONExtractString(payload_json, 'Building', 'developer_logo_url') AS developer_logo_url,
            JSONExtractString(payload_json, 'Building', 'brochure_url') AS brochure_url,
            run_id,
            scraped_at
        FROM dxbi_heatmap_buildings_bronze
        WHERE coords != ''
        """,
    ),
    (
        "ch_geo_projects",
        """
        CREATE OR REPLACE VIEW ch_geo_projects AS
        SELECT
            p.project_id AS project_id,
            p.project_name_en AS project_name_en,
            avgIf(g.lat, g.lat BETWEEN 20 AND 30 AND g.lng BETWEEN 50 AND 60) AS centroid_lat,
            avgIf(g.lng, g.lat BETWEEN 20 AND 30 AND g.lng BETWEEN 50 AND 60) AS centroid_lng,
            avgIf(g.lat, g.lat BETWEEN 20 AND 30 AND g.lng BETWEEN 50 AND 60) AS latitude,
            avgIf(g.lng, g.lat BETWEEN 20 AND 30 AND g.lng BETWEEN 50 AND 60) AS longitude,
            [] AS polygon
        FROM ch_projects p
        LEFT JOIN ch_geo_buildings g
            ON lowerUTF8(g.project_name_en) = lowerUTF8(p.project_name_en)
        GROUP BY p.project_id, p.project_name_en
        """,
    ),
    (
        "ch_project_fact",
        """
        CREATE OR REPLACE VIEW ch_project_fact AS
        WITH
            anchor AS (
                SELECT max(instance_date) AS max_date
                FROM ch_transactions
                WHERE trans_group_en = 'Sales'
            ),
            sales AS (
                SELECT
                    lowerUTF8(project_name_en) AS project_key,
                    count() AS sales_transaction_count_12m,
                    sum(actual_worth) AS total_sales_volume_12m,
                    quantile(0.5)(actual_worth) AS median_sale_price,
                    avg(meter_sale_price) AS avg_sale_price_sqm_12m
                FROM ch_transactions, anchor
                WHERE trans_group_en = 'Sales'
                  AND actual_worth > 0
                  AND project_name_en != ''
                  AND instance_date >= max_date - INTERVAL 365 DAY
                GROUP BY project_key
            ),
            rents AS (
                SELECT
                    lowerUTF8(project_name_en) AS project_key,
                    count() AS rental_contract_count_12m,
                    quantile(0.5)(annual_amount) AS median_annual_rent,
                    avg(rent_per_sqm) AS avg_rent_price_sqm_12m
                FROM ch_rent_contracts, anchor
                WHERE annual_amount > 0
                  AND project_name_en != ''
                  AND contract_start_date >= max_date - INTERVAL 365 DAY
                GROUP BY project_key
            )
        SELECT
            p.project_id,
            p.project_number,
            p.project_name_en,
            p.area_id,
            p.area_name_en,
            p.developer_id,
            p.developer_name,
            p.master_developer_id,
            p.master_developer_name,
            p.master_project_en,
            p.no_of_units,
            p.no_of_buildings,
            p.no_of_villas,
            p.no_of_lands,
            p.project_start_date,
            p.project_end_date,
            p.completion_date,
            p.cancellation_date,
            p.percent_completed,
            p.project_status,
            multiIf(
                p.cancellation_date IS NOT NULL, 'cancelled',
                p.completion_date IS NOT NULL OR p.percent_completed >= 100, 'completed',
                p.percent_completed > 0, 'under_construction',
                'planned'
            ) AS completion_status,
            multiIf(
                p.cancellation_date IS NOT NULL, 'cancelled',
                p.completion_date IS NULL, 'active',
                'completed'
            ) AS pipeline_status,
            if(p.cancellation_date IS NULL, greatest(0.0, least(100.0, p.percent_completed)), 0.0)
                AS estimated_delivery_confidence,
            ifNull(s.sales_transaction_count_12m, 0) AS sales_transaction_count_12m,
            ifNull(r.rental_contract_count_12m, 0) AS rental_contract_count_12m,
            ifNull(s.total_sales_volume_12m, 0) AS total_sales_volume_12m,
            s.median_sale_price AS median_sale_price,
            r.median_annual_rent AS median_annual_rent,
            s.avg_sale_price_sqm_12m AS avg_sale_price_sqm_12m,
            r.avg_rent_price_sqm_12m AS avg_rent_price_sqm_12m,
            if(
                s.median_sale_price > 0 AND r.median_annual_rent > 0,
                r.median_annual_rent / s.median_sale_price * 100,
                NULL
            ) AS gross_yield_pct
        FROM ch_projects p
        LEFT JOIN sales s ON s.project_key = lowerUTF8(p.project_name_en)
        LEFT JOIN rents r ON r.project_key = lowerUTF8(p.project_name_en)
        """,
    ),
    (
        "ch_area_fact",
        """
        CREATE OR REPLACE VIEW ch_area_fact AS
        -- One row per official DLD district. Sales and rentals use the canonical district
        -- of the market layer (open-data labels such as DUBAI MARINA no longer form their
        -- own areas); the project register names the district directly.
        WITH
            anchor AS (
                SELECT max(instance_date) AS max_date
                FROM ch_transactions
                WHERE trans_group_en = 'Sales'
            ),
            projects AS (
                SELECT
                    lowerUTF8(area_name_en) AS area_key,
                    any(area_name_en) AS area_label,
                    any(area_id) AS area_id,
                    count() AS active_projects,
                    countIf(completion_status = 'completed') AS completed_projects,
                    countIf(
                        completion_status != 'completed'
                        AND project_end_date IS NOT NULL
                        AND project_end_date < today()
                    ) AS overdue_projects,
                    sum(no_of_units) AS pipeline_units,
                    sumIf(
                        no_of_units,
                        completion_status != 'completed'
                        AND project_end_date BETWEEN today() AND today() + INTERVAL 365 DAY
                    ) AS units_delivering_next_12_months,
                    uniqExact(developer_name) AS active_developers,
                    avg(percent_completed) AS avg_completion_pct,
                    avg(estimated_delivery_confidence) AS avg_delivery_confidence,
                    groupUniqArray(10)(developer_name) AS top_developers,
                    groupUniqArray(10)(master_project_en) AS top_master_projects
                FROM ch_project_fact
                WHERE area_name_en != ''
                GROUP BY area_key
            ),
            sales AS (
                SELECT
                    lowerUTF8(district_name) AS area_key,
                    any(district_name) AS area_label,
                    any(district_id) AS area_id,
                    count() AS sales_transaction_count_12m,
                    sum(actual_worth) AS total_sales_volume_12m,
                    quantile(0.5)(actual_worth) AS median_sale_price,
                    avg(meter_sale_price) AS avg_sale_price_sqm_12m
                FROM ch_transactions, anchor
                WHERE trans_group_en = 'Sales'
                  AND actual_worth > 0
                  AND district_name != ''
                  AND instance_date >= max_date - INTERVAL 365 DAY
                GROUP BY area_key
            ),
            rents AS (
                SELECT
                    lowerUTF8(district_name) AS area_key,
                    any(district_name) AS area_label,
                    count() AS rental_contract_count_12m,
                    quantile(0.5)(annual_amount) AS median_annual_rent,
                    avg(rent_per_sqm) AS avg_rent_price_sqm_12m
                FROM ch_rent_contracts, anchor
                WHERE annual_amount > 0
                  AND district_name != ''
                  AND contract_start_date >= max_date - INTERVAL 365 DAY
                GROUP BY area_key
            ),
            area_names AS (
                SELECT area_key, any(area_label) AS area_label
                FROM (
                    SELECT area_key, area_label FROM sales
                    UNION ALL
                    SELECT area_key, area_label FROM rents
                    UNION ALL
                    SELECT area_key, area_label FROM projects
                )
                GROUP BY area_key
            )
        SELECT
            names.area_label AS area_name_en,
            if(ifNull(p.area_id, 0) != 0, p.area_id, ifNull(s.area_id, 0)) AS area_id,
            ifNull(p.active_projects, 0) AS active_projects,
            ifNull(p.completed_projects, 0) AS completed_projects,
            ifNull(p.overdue_projects, 0) AS overdue_projects,
            ifNull(p.pipeline_units, 0) AS pipeline_units,
            ifNull(p.units_delivering_next_12_months, 0) AS units_delivering_next_12_months,
            ifNull(p.active_developers, 0) AS active_developers,
            p.avg_completion_pct,
            p.avg_delivery_confidence,
            ifNull(s.sales_transaction_count_12m, 0) AS sales_transaction_count_12m,
            ifNull(r.rental_contract_count_12m, 0) AS rental_contract_count_12m,
            ifNull(s.total_sales_volume_12m, 0) AS total_sales_volume_12m,
            s.median_sale_price,
            r.median_annual_rent,
            s.avg_sale_price_sqm_12m,
            r.avg_rent_price_sqm_12m,
            if(
                s.median_sale_price > 0 AND r.median_annual_rent > 0,
                r.median_annual_rent / s.median_sale_price * 100,
                NULL
            ) AS gross_yield_pct,
            ifNull(p.top_developers, []) AS top_developers,
            ifNull(p.top_master_projects, []) AS top_master_projects
        FROM area_names names
        LEFT JOIN projects p ON p.area_key = names.area_key
        LEFT JOIN sales s ON s.area_key = names.area_key
        LEFT JOIN rents r ON r.area_key = names.area_key
        """,
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the view names that would be created without executing DDL.",
    )
    return parser.parse_args()


def get_client():
    return clickhouse_connect.get_client(
        host=settings.CLICKHOUSE_HOST,
        port=settings.CLICKHOUSE_PORT,
        username=settings.CLICKHOUSE_USER,
        password=settings.CLICKHOUSE_PASSWORD,
        database=settings.CLICKHOUSE_DATABASE,
        connect_timeout=10,
        send_receive_timeout=300,
    )


# Views served from the canonical market layer, and the Holocron table each one reads.
MARKET_BACKED_VIEWS = {
    "ch_transactions": "market_sale_events",
    "ch_rent_contracts": "market_rental_events",
}


class MarketLayerNotPublished(RuntimeError):
    """A market-backed view would serve an empty or never-published table."""


def require_published_market_layer(client) -> None:
    """Refuse to point the app at a market table no validated release has filled.

    The views read Holocron's canonical release. On a warehouse where the market rebuild has
    never published (fresh install, schedule never run), creating them would silently empty
    every sales and rentals endpoint, so nothing is changed and the operator is told why.
    """
    problems = []
    for view, table in MARKET_BACKED_VIEWS.items():
        exists = client.query(
            "SELECT count() FROM system.tables "
            "WHERE database = currentDatabase() AND name IN ({table:String}, 'market_release_ledger')",
            parameters={"table": table},
        ).result_rows[0][0]
        if exists < 2:
            problems.append(f"{view}: {table} or market_release_ledger does not exist")
            continue
        published = client.query(
            "SELECT count() FROM market_release_ledger FINAL "
            "WHERE dataset = {table:String} AND state = 'published'",
            parameters={"table": table},
        ).result_rows[0][0]
        served = client.query(f"SELECT count() FROM `{table}` WHERE is_served = 1").result_rows[0][
            0
        ]
        if not published or not served:
            problems.append(
                f"{view}: {table} has {published} published release(s) and {served} served rows"
            )
    if problems:
        raise MarketLayerNotPublished(
            "views not changed; run Holocron's market_events_refresh job first: "
            + "; ".join(problems)
        )


def main() -> None:
    args = parse_args()
    if args.dry_run:
        for name, _ in VIEW_DDL:
            print(name)
        return

    client = get_client()
    require_published_market_layer(client)
    started = time.time()
    for name, ddl in VIEW_DDL:
        client.command(ddl)
        print(f"created view {name}")
    print(f"created {len(VIEW_DDL)} compatibility views in {time.time() - started:.2f}s")


if __name__ == "__main__":
    main()
