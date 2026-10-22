import asyncio
from typing import Any

from app.clickhouse.client import query
from app.market.reference import (
    SQM_TO_SQFT,
    developer_filter_names,
)
from app.project.mapping import (
    RADAR_YIELD_MIN_SAMPLE_COUNT,
)
from app.supply.scope import reelly_dubai_project_scope

CH_GEO_BUILDINGS_SOURCE = "ch_geo_buildings"

REELLY_PROJECT_ID_OFFSET = 9_000_000_000_000


def _radar_geo_cte() -> str:
    return f"""
    legacy_geo_by_project AS (
        SELECT
            lowerUTF8(trim(gb.project_name_en)) AS project_key,
            replaceRegexpAll(lowerUTF8(gb.project_name_en), '[^a-z0-9]', '')
                AS normalized_project_key,
            min(gb.property_id) AS property_id,
            any(gb.project_name_en) AS project_name_en,
            any(gb.area_name_en) AS area_name_en,
            any(gb.developer_name) AS developer_name,
            avg(gb.lat) AS legacy_latitude,
            avg(gb.lng) AS legacy_longitude,
            count() AS geo_pin_count,
            count() AS building_point_count
        FROM ch_geo_buildings AS gb
        WHERE gb.project_name_en != ''
          AND gb.lat BETWEEN 20 AND 30
          AND gb.lng BETWEEN 50 AND 60
        GROUP BY project_key, normalized_project_key
    ),
    unique_pf_project_coordinates AS (
        SELECT
            candidate.normalized_name AS normalized_project_key,
            any(candidate.latitude) AS candidate_latitude,
            any(candidate.longitude) AS candidate_longitude
        FROM geo_location_nodes AS candidate
        WHERE candidate.location_type IN ('TOWER', 'SUBCOMMUNITY')
          AND candidate.normalized_name != ''
          AND candidate.latitude BETWEEN 24 AND 26
          AND candidate.longitude BETWEEN 54 AND 57
        GROUP BY candidate.normalized_name
        HAVING uniqExact(candidate.canonical_location_id) = 1
    ),
    reelly_catalogue_projects AS (
        SELECT
            lowerUTF8(trim(project_name)) AS project_key,
            replaceRegexpAll(lowerUTF8(project_name), '[^a-z0-9]', '')
                AS normalized_project_key,
            {REELLY_PROJECT_ID_OFFSET} + toUInt64(project_id) AS property_id,
            any(project_name) AS project_name_en,
            any(area_name) AS area_name_en,
            any(developer_name) AS developer_name,
            any(latitude) AS reelly_latitude,
            any(longitude) AS reelly_longitude,
            any(sale_status) AS supply_status,
            any(completion_time) AS supply_completion_time,
            any(units_in_sale) AS units_in_sale,
            any(readiness_progress) AS supply_readiness
        FROM silver_reelly_projects FINAL
        WHERE project_name != ''
          AND {reelly_dubai_project_scope()}
          AND latitude BETWEEN 24 AND 26
          AND longitude BETWEEN 54 AND 57
          AND toUInt64OrZero(project_id) > 0
        GROUP BY project_key, normalized_project_key, property_id
        HAVING uniqExact(project_id) = 1
    ),
    geo_candidates AS (
        SELECT
            lg.project_key AS project_key,
            lg.normalized_project_key AS normalized_project_key,
            lg.property_id AS property_id,
            lg.project_name_en AS project_name_en,
            lg.area_name_en AS area_name_en,
            lg.developer_name AS developer_name,
            lg.legacy_latitude AS legacy_latitude,
            lg.legacy_longitude AS legacy_longitude,
            lg.geo_pin_count AS geo_pin_count,
            lg.building_point_count AS building_point_count,
            pf.candidate_latitude AS pf_latitude,
            pf.candidate_longitude AS pf_longitude,
            reelly.reelly_latitude AS reelly_latitude,
            reelly.reelly_longitude AS reelly_longitude,
            if(
                pf.candidate_latitude IS NOT NULL AND pf.candidate_longitude IS NOT NULL,
                greatCircleDistance(
                    lg.legacy_longitude,
                    lg.legacy_latitude,
                    pf.candidate_longitude,
                    pf.candidate_latitude
                ),
                1000000000
            ) AS pf_distance_m,
            if(
                reelly.reelly_latitude IS NOT NULL
                AND reelly.reelly_longitude IS NOT NULL,
                greatCircleDistance(
                    lg.legacy_longitude,
                    lg.legacy_latitude,
                    reelly.reelly_longitude,
                    reelly.reelly_latitude
                ),
                1000000000
            ) AS reelly_distance_m,
            if(
                pf.candidate_latitude IS NOT NULL
                AND pf.candidate_longitude IS NOT NULL
                AND reelly.reelly_latitude IS NOT NULL
                AND reelly.reelly_longitude IS NOT NULL,
                greatCircleDistance(
                    pf.candidate_longitude,
                    pf.candidate_latitude,
                    reelly.reelly_longitude,
                    reelly.reelly_latitude
                ),
                1000000000
            ) AS source_spread_m
        FROM legacy_geo_by_project AS lg
        LEFT JOIN unique_pf_project_coordinates AS pf
            ON pf.normalized_project_key = lg.normalized_project_key
        LEFT JOIN reelly_catalogue_projects AS reelly
            ON reelly.normalized_project_key = lg.normalized_project_key
    ),
    resolved_legacy_projects AS (
        SELECT
            project_key,
            normalized_project_key,
            property_id,
            project_name_en,
            area_name_en,
            developer_name,
            multiIf(
                pf_distance_m <= 2000, pf_latitude,
                reelly_distance_m <= 2000, reelly_latitude,
                legacy_latitude
            ) AS latitude,
            multiIf(
                pf_distance_m <= 2000, pf_longitude,
                reelly_distance_m <= 2000, reelly_longitude,
                legacy_longitude
            ) AS longitude,
            geo_pin_count,
            multiIf(
                pf_distance_m <= 2000 AND reelly_distance_m <= 2000 AND source_spread_m <= 500,
                    'pf_reelly_consensus',
                pf_distance_m <= 2000, 'pf_location_hierarchy',
                reelly_distance_m <= 2000, 'reelly_catalogue',
                '{CH_GEO_BUILDINGS_SOURCE}'
            ) AS coordinate_source,
            multiIf(
                pf_distance_m <= 2000 AND reelly_distance_m <= 2000 AND source_spread_m <= 500,
                    'unique_name_distance_consensus',
                pf_distance_m <= 2000, 'unique_pf_name_within_2km',
                reelly_distance_m <= 2000, 'unique_reelly_name_within_2km',
                'building_centroid_fallback'
            ) AS coordinate_method,
            toUInt64(pf_distance_m <= 2000) + toUInt64(reelly_distance_m <= 2000)
                AS detail_point_count,
            building_point_count,
            0 AS land_point_count,
            'legacy_project' AS entity_source,
            '' AS supply_status,
            CAST(NULL, 'Nullable(DateTime)') AS supply_completion_time,
            toUInt32(0) AS units_in_sale,
            CAST(NULL, 'Nullable(Float64)') AS supply_readiness
        FROM geo_candidates
    ),
    reelly_geo_projects AS (
        SELECT
            reelly.project_key AS project_key,
            reelly.normalized_project_key AS normalized_project_key,
            reelly.property_id AS property_id,
            reelly.project_name_en AS project_name_en,
            reelly.area_name_en AS area_name_en,
            reelly.developer_name AS developer_name,
            if(
                pf.candidate_latitude IS NOT NULL
                AND pf.candidate_longitude IS NOT NULL
                AND greatCircleDistance(
                    reelly.reelly_longitude,
                    reelly.reelly_latitude,
                    pf.candidate_longitude,
                    pf.candidate_latitude
                ) <= 500,
                pf.candidate_latitude,
                reelly.reelly_latitude
            ) AS latitude,
            if(
                pf.candidate_latitude IS NOT NULL
                AND pf.candidate_longitude IS NOT NULL
                AND greatCircleDistance(
                    reelly.reelly_longitude,
                    reelly.reelly_latitude,
                    pf.candidate_longitude,
                    pf.candidate_latitude
                ) <= 500,
                pf.candidate_longitude,
                reelly.reelly_longitude
            ) AS longitude,
            toUInt64(1) AS geo_pin_count,
            if(
                pf.candidate_latitude IS NOT NULL
                AND pf.candidate_longitude IS NOT NULL
                AND greatCircleDistance(
                    reelly.reelly_longitude,
                    reelly.reelly_latitude,
                    pf.candidate_longitude,
                    pf.candidate_latitude
                ) <= 500,
                'pf_reelly_consensus',
                'reelly_catalogue'
            ) AS coordinate_source,
            if(
                pf.candidate_latitude IS NOT NULL
                AND pf.candidate_longitude IS NOT NULL
                AND greatCircleDistance(
                    reelly.reelly_longitude,
                    reelly.reelly_latitude,
                    pf.candidate_longitude,
                    pf.candidate_latitude
                ) <= 500,
                'exact_name_within_500m',
                'reelly_project_coordinate'
            ) AS coordinate_method,
            toUInt64(1) + toUInt64(
                pf.candidate_latitude IS NOT NULL
                AND pf.candidate_longitude IS NOT NULL
                AND greatCircleDistance(
                    reelly.reelly_longitude,
                    reelly.reelly_latitude,
                    pf.candidate_longitude,
                    pf.candidate_latitude
                ) <= 500
            ) AS detail_point_count,
            ifNull(legacy.building_point_count, 0) AS building_point_count,
            0 AS land_point_count,
            'reelly_project' AS entity_source,
            reelly.supply_status AS supply_status,
            reelly.supply_completion_time AS supply_completion_time,
            reelly.units_in_sale AS units_in_sale,
            reelly.supply_readiness AS supply_readiness
        FROM reelly_catalogue_projects AS reelly
        LEFT JOIN unique_pf_project_coordinates AS pf
            ON pf.normalized_project_key = reelly.normalized_project_key
        LEFT JOIN legacy_geo_by_project AS legacy
            ON legacy.normalized_project_key = reelly.normalized_project_key
    ),
    geo_by_project AS (
        SELECT *
        FROM reelly_geo_projects
        UNION ALL
        SELECT legacy.*
        FROM resolved_legacy_projects AS legacy
        LEFT JOIN reelly_catalogue_projects AS reelly
            ON reelly.normalized_project_key = legacy.normalized_project_key
        WHERE reelly.property_id = 0
    )
    """


def _radar_developer_lookup_sql() -> str:
    return """
        SELECT developer_name_ar, developer_name_en
        FROM ch_developers
        WHERE developer_name_en != ''
    """


def _radar_stats_sql(radar_geo_cte: str) -> str:
    return f"""
        WITH {radar_geo_cte},
        activity_anchor AS (
            SELECT greatest(
                (SELECT max(transaction_date)
                 FROM dxbi_sales_unit_events FINAL
                 WHERE transaction_date <= today()),
                (SELECT max(lease_start)
                 FROM dxbi_rental_events FINAL
                 WHERE lease_start <= today())
            ) AS period_end
        ),
        sales_activity AS (
            SELECT
                count() AS sales_transaction_count,
                sum(sale_amount_aed) AS total_sales_volume
            FROM dxbi_sales_unit_events AS e FINAL
            CROSS JOIN activity_anchor AS a
            WHERE e.transaction_date > a.period_end - toIntervalDay({{period_days:UInt16}})
              AND e.transaction_date <= a.period_end
        ),
        rental_activity AS (
            SELECT count() AS rental_contract_count
            FROM dxbi_rental_events AS e FINAL
            CROSS JOIN activity_anchor AS a
            WHERE e.lease_start > a.period_end - toIntervalDay({{period_days:UInt16}})
              AND e.lease_start <= a.period_end
        ),
        mapped_sales_activity AS (
            SELECT count() AS mapped_sales_transaction_count
            FROM dxbi_sales_unit_events AS e FINAL
            CROSS JOIN activity_anchor AS a
            INNER JOIN geo_mapped_source_facts AS gm
                ON gm.source_system = 'dxbi_sales'
               AND gm.raw_area_name = e.area_name
               AND gm.raw_project_name = e.project_name
               AND gm.raw_building_name = e.building_name
               AND gm.property_type = e.property_type
               AND gm.market_status = e.market_status
               AND gm.source_grain = multiIf(
                    lowerUTF8(e.market_status) = 'offplan', 'project',
                    e.property_type IN ('Villa', 'Townhouse'), 'landed_phase',
                    e.building_name != '', 'building',
                    e.project_name != '', 'project',
                    'area'
               )
            WHERE gm.match_status IN ('auto_approved', 'manual_approved')
              AND e.transaction_date > a.period_end - toIntervalDay({{period_days:UInt16}})
              AND e.transaction_date <= a.period_end
        ),
        mapped_rental_activity AS (
            SELECT count() AS mapped_rental_contract_count
            FROM dxbi_rental_events AS e FINAL
            CROSS JOIN activity_anchor AS a
            INNER JOIN geo_mapped_source_facts AS gm
                ON gm.source_system = 'dxbi_rentals'
               AND gm.raw_area_name = e.area_name
               AND gm.raw_project_name = e.project_name
               AND gm.raw_building_name = e.building_name
               AND gm.property_type = e.property_type
               AND gm.market_status = ''
               AND gm.source_grain = multiIf(
                    e.property_type IN ('Villa', 'Townhouse'), 'landed_phase',
                    e.building_name != '', 'building',
                    e.project_name != '', 'project',
                    'area'
               )
            WHERE gm.match_status IN ('auto_approved', 'manual_approved')
              AND e.lease_start > a.period_end - toIntervalDay({{period_days:UInt16}})
              AND e.lease_start <= a.period_end
        ),
        dda_stats AS (
            SELECT
                count() AS dda_plot_count,
                sum(plot_area_sqm) AS dda_total_plot_area_sqm,
                sum(max_gfa_sqm) AS dda_total_gfa_sqm
            FROM dda_land_plots
        )
        SELECT
            (SELECT countDistinct(property_id) FROM geo_by_project) AS total_projects,
            (SELECT countDistinct(property_id) FROM geo_by_project) AS mapped_projects,
            countIf(
                lowerUTF8(completion_status) NOT LIKE '%complete%'
                AND lowerUTF8(completion_status) NOT LIKE '%finish%'
                AND lowerUTF8(completion_status) NOT LIKE '%cancel%'
            ) AS active_projects,
            countIf(
                lowerUTF8(completion_status) LIKE '%complete%'
                OR lowerUTF8(completion_status) LIKE '%finish%'
            ) AS finished_projects,
            sumIf(
                no_of_units,
                lowerUTF8(completion_status) NOT LIKE '%complete%'
                AND lowerUTF8(completion_status) NOT LIKE '%finish%'
                AND lowerUTF8(completion_status) NOT LIKE '%cancel%'
            ) AS pipeline_units,
            sumIf(
                no_of_units,
                lowerUTF8(completion_status) NOT LIKE '%complete%'
                AND lowerUTF8(completion_status) NOT LIKE '%finish%'
                AND lowerUTF8(completion_status) NOT LIKE '%cancel%'
                AND project_end_date IS NOT NULL
                AND toDate(project_end_date) BETWEEN today() AND today() + INTERVAL 365 DAY
            ) AS units_delivering_next_12_months,
            any(sa.sales_transaction_count) AS sales_transaction_count,
            any(ra.rental_contract_count) AS rental_contract_count,
            any(sa.total_sales_volume) AS total_sales_volume,
            any(msa.mapped_sales_transaction_count) AS mapped_sales_transaction_count,
            any(mra.mapped_rental_contract_count) AS mapped_rental_contract_count,
            round(
                100 * (
                    any(msa.mapped_sales_transaction_count)
                    + any(mra.mapped_rental_contract_count)
                ) / greatest(
                    1,
                    any(sa.sales_transaction_count) + any(ra.rental_contract_count)
                ),
                1
            ) AS activity_coverage_pct,
            any(ds.dda_plot_count) AS dda_plot_count,
            any(ds.dda_total_plot_area_sqm) AS dda_total_plot_area_sqm,
            any(ds.dda_total_gfa_sqm) AS dda_total_gfa_sqm,
            any(a.period_end) - toIntervalDay({{period_days:UInt16}}) AS activity_period_start,
            any(a.period_end) AS activity_period_end
        FROM ch_project_fact
        CROSS JOIN activity_anchor AS a
        CROSS JOIN sales_activity AS sa
        CROSS JOIN rental_activity AS ra
        CROSS JOIN mapped_sales_activity AS msa
        CROSS JOIN mapped_rental_activity AS mra
        CROSS JOIN dda_stats AS ds
    """


def _radar_area_sql(radar_geo_cte: str) -> str:
    return f"""
        WITH {radar_geo_cte},
        activity_anchor AS (
            SELECT greatest(
                (SELECT max(transaction_date)
                 FROM dxbi_sales_unit_events FINAL
                 WHERE transaction_date <= today()),
                (SELECT max(lease_start)
                 FROM dxbi_rental_events FINAL
                 WHERE lease_start <= today())
            ) AS period_end
        ),
        location_community AS (
            SELECT
                c.descendant_location_id,
                argMin(c.ancestor_location_id, c.depth) AS community_location_id
            FROM geo_location_closure AS c
            INNER JOIN geo_location_nodes AS n
                ON n.canonical_location_id = c.ancestor_location_id
            WHERE n.location_type = 'COMMUNITY'
            GROUP BY c.descendant_location_id
        ),
        unique_pf_project_nodes AS (
            SELECT
                candidate.normalized_name,
                any(candidate.canonical_location_id) AS matched_location_id
            FROM geo_location_nodes AS candidate
            WHERE candidate.location_type IN ('TOWER', 'SUBCOMMUNITY')
              AND candidate.normalized_name != ''
            GROUP BY normalized_name
            HAVING uniqExact(candidate.canonical_location_id) = 1
        ),
        project_community_status AS (
            SELECT
                lc.community_location_id,
                count() AS mapped_projects,
                countIf(
                    lowerUTF8(pf.completion_status) NOT LIKE '%complete%'
                    AND lowerUTF8(pf.completion_status) NOT LIKE '%finish%'
                    AND lowerUTF8(pf.completion_status) NOT LIKE '%cancel%'
                ) AS active_projects,
                countIf(
                    lowerUTF8(pf.completion_status) LIKE '%complete%'
                    OR lowerUTF8(pf.completion_status) LIKE '%finish%'
                ) AS completed_projects,
                countIf(
                    lowerUTF8(pf.completion_status) LIKE '%delay%'
                    OR lowerUTF8(pf.completion_status) LIKE '%overdue%'
                    OR lowerUTF8(pf.completion_status) LIKE '%stall%'
                    OR lowerUTF8(pf.completion_status) LIKE '%suspend%'
                    OR lowerUTF8(pf.completion_status) LIKE '%hold%'
                ) AS overdue_projects,
                sumIf(
                    pf.no_of_units,
                    lowerUTF8(pf.completion_status) NOT LIKE '%complete%'
                    AND lowerUTF8(pf.completion_status) NOT LIKE '%finish%'
                    AND lowerUTF8(pf.completion_status) NOT LIKE '%cancel%'
                ) AS pipeline_units,
                sumIf(
                    pf.no_of_units,
                    lowerUTF8(pf.completion_status) NOT LIKE '%complete%'
                    AND lowerUTF8(pf.completion_status) NOT LIKE '%finish%'
                    AND lowerUTF8(pf.completion_status) NOT LIKE '%cancel%'
                    AND pf.project_end_date IS NOT NULL
                    AND toDate(pf.project_end_date) BETWEEN today() AND today() + INTERVAL 365 DAY
                ) AS units_delivering_next_12_months,
                uniqExactIf(
                    pf.developer_name,
                    pf.developer_name != ''
                    AND lowerUTF8(pf.completion_status) NOT LIKE '%complete%'
                    AND lowerUTF8(pf.completion_status) NOT LIKE '%finish%'
                    AND lowerUTF8(pf.completion_status) NOT LIKE '%cancel%'
                ) AS active_developers,
                avg(pf.percent_completed) AS avg_completion_pct,
                avg(pf.estimated_delivery_confidence) AS avg_delivery_confidence,
                arrayFilter(x -> x != '', topK(5)(pf.developer_name)) AS top_developers,
                arrayFilter(x -> x != '', topK(5)(pf.master_project_en)) AS top_master_projects
            FROM ch_project_fact AS pf
            INNER JOIN unique_pf_project_nodes AS pn
                ON pn.normalized_name = replaceRegexpAll(
                    lowerUTF8(pf.project_name_en),
                    '[^a-z0-9]',
                    ''
                )
            INNER JOIN location_community AS lc
                ON lc.descendant_location_id = pn.matched_location_id
            WHERE pf.project_name_en != ''
            GROUP BY lc.community_location_id
        ),
        sales_area_activity AS (
            SELECT
                lc.community_location_id,
                count() AS sales_transaction_count,
                sum(e.sale_amount_aed) AS total_sales_volume,
                quantile(0.5)(e.sale_amount_aed) AS median_sale_price,
                avgIf(
                    e.price_per_sqft_aed * {SQM_TO_SQFT},
                    e.price_per_sqft_aed IS NOT NULL AND e.price_per_sqft_aed > 0
                ) AS avg_sale_price_sqm
            FROM dxbi_sales_unit_events AS e FINAL
            CROSS JOIN activity_anchor AS a
            INNER JOIN geo_mapped_source_facts AS gm
                ON gm.source_system = 'dxbi_sales'
               AND gm.raw_area_name = e.area_name
               AND gm.raw_project_name = e.project_name
               AND gm.raw_building_name = e.building_name
               AND gm.property_type = e.property_type
               AND gm.market_status = e.market_status
               AND gm.source_grain = multiIf(
                    lowerUTF8(e.market_status) = 'offplan', 'project',
                    e.property_type IN ('Villa', 'Townhouse'), 'landed_phase',
                    e.building_name != '', 'building',
                    e.project_name != '', 'project',
                    'area'
               )
            INNER JOIN location_community AS lc
                ON lc.descendant_location_id = gm.canonical_location_id
            WHERE gm.match_status IN ('auto_approved', 'manual_approved')
              AND e.transaction_date > a.period_end - toIntervalDay({{period_days:UInt16}})
              AND e.transaction_date <= a.period_end
            GROUP BY lc.community_location_id
        ),
        rental_area_activity AS (
            SELECT
                lc.community_location_id,
                count() AS rental_contract_count,
                quantile(0.5)(e.annual_rent_aed) AS median_annual_rent,
                avgIf(
                    (e.annual_rent_aed / e.size_sqft) * {SQM_TO_SQFT},
                    e.annual_rent_aed > 0 AND e.size_sqft > 0
                ) AS avg_rent_price_sqm
            FROM dxbi_rental_events AS e FINAL
            CROSS JOIN activity_anchor AS a
            INNER JOIN geo_mapped_source_facts AS gm
                ON gm.source_system = 'dxbi_rentals'
               AND gm.raw_area_name = e.area_name
               AND gm.raw_project_name = e.project_name
               AND gm.raw_building_name = e.building_name
               AND gm.property_type = e.property_type
               AND gm.market_status = ''
               AND gm.source_grain = multiIf(
                    e.property_type IN ('Villa', 'Townhouse'), 'landed_phase',
                    e.building_name != '', 'building',
                    e.project_name != '', 'project',
                    'area'
               )
            INNER JOIN location_community AS lc
                ON lc.descendant_location_id = gm.canonical_location_id
            WHERE gm.match_status IN ('auto_approved', 'manual_approved')
              AND e.lease_start > a.period_end - toIntervalDay({{period_days:UInt16}})
              AND e.lease_start <= a.period_end
            GROUP BY lc.community_location_id
        ),
        area_geo AS (
            SELECT
                lowerUTF8(trim(g.area_name_en)) AS area_key,
                countDistinct(g.project_key) AS mapped_projects
            FROM geo_by_project g
            GROUP BY area_key
        ),
        area_status AS (
            SELECT
                lowerUTF8(trim(area_name_en)) AS area_key,
                any(area_id) AS area_id,
                countIf(
                    NOT (
                        upperUTF8(completion_status) IN ('FINISHED', 'COMPLETED')
                        OR lowerUTF8(completion_status) LIKE '%complete%'
                        OR lowerUTF8(completion_status) LIKE '%cancel%'
                    )
                ) AS active_projects,
                countIf(
                    upperUTF8(completion_status) IN ('FINISHED', 'COMPLETED')
                    OR lowerUTF8(completion_status) LIKE '%complete%'
                ) AS completed_projects,
                countIf(
                    lowerUTF8(completion_status) LIKE '%delay%'
                    OR lowerUTF8(completion_status) LIKE '%overdue%'
                    OR lowerUTF8(completion_status) LIKE '%stall%'
                    OR lowerUTF8(completion_status) LIKE '%suspend%'
                    OR lowerUTF8(completion_status) LIKE '%hold%'
                ) AS overdue_projects,
                sumIf(
                    no_of_units,
                    NOT (
                        upperUTF8(completion_status) IN ('FINISHED', 'COMPLETED')
                        OR lowerUTF8(completion_status) LIKE '%complete%'
                        OR lowerUTF8(completion_status) LIKE '%cancel%'
                    )
                ) AS pipeline_units,
                sumIf(
                    no_of_units,
                    NOT (
                        upperUTF8(completion_status) IN ('FINISHED', 'COMPLETED')
                        OR lowerUTF8(completion_status) LIKE '%complete%'
                        OR lowerUTF8(completion_status) LIKE '%cancel%'
                    )
                    AND project_end_date IS NOT NULL
                    AND toDate(project_end_date) BETWEEN today() AND today() + INTERVAL 365 DAY
                ) AS units_delivering_next_12_months,
                uniqExactIf(
                    developer_name,
                    developer_name != ''
                    AND NOT (
                        upperUTF8(completion_status) IN ('FINISHED', 'COMPLETED')
                        OR lowerUTF8(completion_status) LIKE '%complete%'
                        OR lowerUTF8(completion_status) LIKE '%cancel%'
                    )
                ) AS active_developers
                ,avg(percent_completed) AS avg_completion_pct
                ,avg(estimated_delivery_confidence) AS avg_delivery_confidence
                ,arrayFilter(x -> x != '', topK(5)(developer_name)) AS top_developers
                ,arrayFilter(x -> x != '', topK(5)(master_project_en)) AS top_master_projects
            FROM ch_project_fact
            WHERE area_name_en != ''
            GROUP BY area_key
        ),
        dda_area AS (
            SELECT
                lowerUTF8(trim(community_name)) AS area_key,
                count() AS dda_plot_count,
                sum(plot_area_sqm) AS dda_total_plot_area_sqm,
                sum(max_gfa_sqm) AS dda_total_gfa_sqm,
                arrayFilter(x -> x != '', topK(5)(land_use)) AS dda_dominant_land_uses
            FROM dda_land_plots
            WHERE community_name != ''
            GROUP BY area_key
        )
        SELECT
            toInt64OrZero(n.source_location_id) AS area_id,
            n.name AS area_name_en,
            n.latitude AS latitude,
            n.longitude AS longitude,
            ifNull(pcs.mapped_projects, ifNull(ag.mapped_projects, 0)) AS mapped_projects,
            ifNull(pcs.active_projects, ifNull(ast.active_projects, 0)) AS active_projects,
            ifNull(pcs.completed_projects, ifNull(ast.completed_projects, 0)) AS completed_projects,
            ifNull(pcs.overdue_projects, ifNull(ast.overdue_projects, 0)) AS overdue_projects,
            ifNull(pcs.pipeline_units, ifNull(ast.pipeline_units, 0)) AS pipeline_units,
            ifNull(
                pcs.units_delivering_next_12_months,
                ifNull(ast.units_delivering_next_12_months, 0)
            ) AS units_delivering_next_12_months,
            ifNull(pcs.active_developers, ifNull(ast.active_developers, 0)) AS active_developers,
            ifNull(pcs.avg_completion_pct, ifNull(ast.avg_completion_pct, 0)) AS avg_completion_pct,
            ifNull(
                pcs.avg_delivery_confidence,
                ifNull(ast.avg_delivery_confidence, 0)
            ) AS avg_delivery_confidence,
            ifNull(sa.sales_transaction_count, 0) AS sales_transaction_count,
            ifNull(ra.rental_contract_count, 0) AS rental_contract_count,
            ifNull(sa.total_sales_volume, 0) AS total_sales_volume,
            sa.median_sale_price AS median_sale_price,
            ra.median_annual_rent AS median_annual_rent,
            if(
                sa.sales_transaction_count >= {RADAR_YIELD_MIN_SAMPLE_COUNT}
                AND ra.rental_contract_count >= {RADAR_YIELD_MIN_SAMPLE_COUNT}
                AND sa.median_sale_price > 0
                AND ra.median_annual_rent > 0,
                ra.median_annual_rent / sa.median_sale_price * 100,
                NULL
            ) AS gross_yield_pct,
            sa.avg_sale_price_sqm AS avg_sale_price_sqm,
            ra.avg_rent_price_sqm AS avg_rent_price_sqm,
            ifNull(pcs.top_developers, ifNull(ast.top_developers, [])) AS top_developers,
            ifNull(pcs.top_master_projects, ifNull(ast.top_master_projects, [])) AS top_master_projects,
            ifNull(da.dda_plot_count, 0) AS dda_plot_count,
            ifNull(da.dda_total_plot_area_sqm, 0) AS dda_total_plot_area_sqm,
            ifNull(da.dda_total_gfa_sqm, 0) AS dda_total_gfa_sqm,
            ifNull(da.dda_dominant_land_uses, []) AS dda_dominant_land_uses,
            ifNull(sa.sales_transaction_count, 0) AS yield_existing_sale_count,
            ifNull(ra.rental_contract_count, 0) AS yield_rental_contract_count,
            sa.median_sale_price AS yield_existing_median_sale_price,
            ra.median_annual_rent AS yield_median_annual_rent
        FROM geo_location_nodes AS n
        LEFT JOIN sales_area_activity AS sa
            ON sa.community_location_id = n.canonical_location_id
        LEFT JOIN rental_area_activity AS ra
            ON ra.community_location_id = n.canonical_location_id
        LEFT JOIN project_community_status AS pcs
            ON pcs.community_location_id = n.canonical_location_id
        LEFT JOIN area_geo AS ag
            ON ag.area_key = lowerUTF8(trim(n.name))
        LEFT JOIN area_status AS ast
            ON ast.area_key = lowerUTF8(trim(n.name))
        LEFT JOIN dda_area AS da
            ON da.area_key = lowerUTF8(trim(n.name))
        WHERE n.location_type = 'COMMUNITY'
          AND (
            sa.sales_transaction_count > 0
            OR ra.rental_contract_count > 0
            OR pcs.active_projects > 0
            OR ast.active_projects > 0
            OR da.dda_plot_count > 0
          )
        ORDER BY
            ifNull(sa.sales_transaction_count, 0) + ifNull(ra.rental_contract_count, 0) DESC,
            pipeline_units DESC
        LIMIT 160
    """


def _radar_project_pins_sql(radar_geo_cte: str, where_clause: str) -> str:
    return f"""
        WITH {radar_geo_cte},
        activity_anchor AS (
            SELECT greatest(
                (SELECT max(transaction_date)
                 FROM dxbi_sales_unit_events FINAL
                 WHERE transaction_date <= today()),
                (SELECT max(lease_start)
                 FROM dxbi_rental_events FINAL
                 WHERE lease_start <= today())
            ) AS period_end
        ),
        fact_by_project AS (
            SELECT
                replaceRegexpAll(lowerUTF8(pf.project_name_en), '[^a-z0-9]', '') AS project_key,
                any(pf.project_name_en) AS project_name_en,
                any(pf.area_name_en) AS area_name_en,
                any(pf.developer_name) AS developer_name,
                any(pf.master_project_en) AS master_project_en,
                any(pf.completion_status) AS completion_status,
                any(pf.pipeline_status) AS pipeline_status,
                any(pf.project_status) AS project_status,
                any(pf.no_of_units) AS no_of_units,
                any(pf.no_of_buildings) AS no_of_buildings,
                any(pf.percent_completed) AS percent_completed,
                any(pf.estimated_delivery_confidence) AS estimated_delivery_confidence
            FROM ch_project_fact AS pf
            WHERE pf.project_name_en != ''
            GROUP BY project_key
        ),
        sales_by_project AS (
            SELECT
                if(
                    lowerUTF8(e.market_status) = 'offplan'
                    AND orm.match_status IN ('auto_approved', 'manual_approved')
                    AND orm.normalized_reelly_project_name != '',
                    orm.normalized_reelly_project_name,
                    replaceRegexpAll(
                        lowerUTF8(trim(if(
                            lowerUTF8(e.market_status) = 'offplan' AND e.building_name != '',
                            e.building_name,
                            e.project_name
                        ))),
                        '[^a-z0-9]',
                        ''
                    )
                ) AS project_key,
                count() AS sales_transaction_count,
                sum(sale_amount_aed) AS total_sales_volume,
                quantile(0.5)(sale_amount_aed) AS median_sale_price,
                avgIf(
                    price_per_sqft_aed * {SQM_TO_SQFT},
                    price_per_sqft_aed IS NOT NULL AND price_per_sqft_aed > 0
                ) AS avg_sale_price_sqm
            FROM dxbi_sales_unit_events AS e FINAL
            CROSS JOIN activity_anchor AS a
            LEFT JOIN offplan_reelly_mapped_source_facts AS orm
                ON orm.source_system = 'dxbi_sales'
               AND orm.raw_area_name = e.area_name
               AND orm.raw_project_name = e.project_name
               AND orm.raw_building_name = e.building_name
               AND orm.property_type = e.property_type
            WHERE if(
                    lowerUTF8(e.market_status) = 'offplan'
                    AND orm.match_status IN ('auto_approved', 'manual_approved')
                    AND orm.normalized_reelly_project_name != '',
                    orm.normalized_reelly_project_name,
                    if(
                        lowerUTF8(e.market_status) = 'offplan' AND e.building_name != '',
                        e.building_name,
                        e.project_name
                    )
                  ) != ''
              AND e.transaction_date > a.period_end - toIntervalDay({{period_days:UInt16}})
              AND e.transaction_date <= a.period_end
            GROUP BY project_key
        ),
        rents_by_project AS (
            SELECT
                replaceRegexpAll(lowerUTF8(trim(project_name)), '[^a-z0-9]', '') AS project_key,
                count() AS rental_contract_count,
                quantile(0.5)(annual_rent_aed) AS median_annual_rent,
                avgIf(
                    (annual_rent_aed / size_sqft) * {SQM_TO_SQFT},
                    annual_rent_aed > 0 AND size_sqft > 0
                ) AS avg_rent_price_sqm
            FROM dxbi_rental_events AS e FINAL
            CROSS JOIN activity_anchor AS a
            WHERE project_name != ''
              AND e.lease_start > a.period_end - toIntervalDay({{period_days:UInt16}})
              AND e.lease_start <= a.period_end
            GROUP BY project_key
        )
        SELECT
            -toInt64(g.property_id) AS project_id,
            coalesce(nullIf(f.project_name_en, ''), g.project_name_en) AS project_name_en,
            coalesce(nullIf(f.area_name_en, ''), g.area_name_en) AS area_name_en,
            coalesce(nullIf(f.developer_name, ''), g.developer_name) AS developer_name,
            f.master_project_en AS master_project_en,
            g.latitude AS latitude,
            g.longitude AS longitude,
            g.geo_pin_count AS geo_pin_count,
            g.coordinate_source AS coordinate_source,
            g.coordinate_method AS coordinate_method,
            g.detail_point_count AS detail_point_count,
            g.building_point_count AS building_point_count,
            g.land_point_count AS land_point_count,
            coalesce(
                nullIf(f.completion_status, ''),
                if(g.supply_readiness >= 100, 'Finished', '')
            ) AS completion_status,
            coalesce(
                nullIf(f.pipeline_status, ''),
                multiIf(
                    g.supply_completion_time BETWEEN today() AND today() + INTERVAL 365 DAY,
                        'delivering_next_12_months',
                    g.supply_completion_time > today(), 'pipeline',
                    ''
                )
            ) AS pipeline_status,
            coalesce(f.project_status, nullIf(g.supply_status, '')) AS project_status,
            ifNull(f.no_of_units, 0) AS no_of_units,
            ifNull(f.no_of_buildings, 0) AS no_of_buildings,
            coalesce(f.percent_completed, g.supply_readiness) AS percent_completed,
            ifNull(f.estimated_delivery_confidence, 0) AS estimated_delivery_confidence,
            g.entity_source AS entity_source,
            g.supply_status AS supply_status,
            g.supply_completion_time AS supply_completion_time,
            g.units_in_sale AS units_in_sale,
            ifNull(s.sales_transaction_count, 0) AS sales_transaction_count,
            ifNull(r.rental_contract_count, 0) AS rental_contract_count,
            ifNull(s.total_sales_volume, 0) AS total_sales_volume,
            nullIf(s.median_sale_price, 0) AS median_sale_price,
            nullIf(r.median_annual_rent, 0) AS median_annual_rent,
            if(
                s.sales_transaction_count >= {RADAR_YIELD_MIN_SAMPLE_COUNT}
                AND r.rental_contract_count >= {RADAR_YIELD_MIN_SAMPLE_COUNT}
                AND s.median_sale_price > 0
                AND r.median_annual_rent > 0,
                r.median_annual_rent / s.median_sale_price * 100,
                NULL
            ) AS gross_yield_pct,
            nullIf(s.avg_sale_price_sqm, 0) AS avg_sale_price_sqm,
            nullIf(r.avg_rent_price_sqm, 0) AS avg_rent_price_sqm,
            ifNull(s.sales_transaction_count, 0) AS yield_existing_sale_count,
            ifNull(r.rental_contract_count, 0) AS yield_rental_contract_count,
            nullIf(s.median_sale_price, 0) AS yield_existing_median_sale_price,
            nullIf(r.median_annual_rent, 0) AS yield_median_annual_rent
        FROM geo_by_project g
        LEFT JOIN fact_by_project f ON f.project_key = g.normalized_project_key
        LEFT JOIN sales_by_project s ON s.project_key = g.normalized_project_key
        LEFT JOIN rents_by_project r ON r.project_key = g.normalized_project_key
        WHERE {where_clause}
        ORDER BY
            (f.pipeline_status = 'delivering_next_12_months') DESC,
            f.no_of_units DESC,
            total_sales_volume DESC
        LIMIT {{limit:UInt32}}
    """


def _radar_project_pin_filters(
    *,
    west: float | None,
    south: float | None,
    east: float | None,
    north: float | None,
    area: str | None,
    developer: str | None,
) -> tuple[str, dict[str, Any]]:
    # The coordinate CTE already excludes unnamed projects. Keeping the base
    # predicate out of the outer query matters because ClickHouse otherwise
    # pushes it into the aggregation that produces the display name.
    filters = ["1"]
    params: dict[str, Any] = {}
    if west is not None and south is not None and east is not None and north is not None:
        filters.extend(
            [
                "g.longitude BETWEEN {west:Float64} AND {east:Float64}",
                "g.latitude BETWEEN {south:Float64} AND {north:Float64}",
            ]
        )
        params.update({"west": west, "south": south, "east": east, "north": north})
    if area:
        filters.append("lower(g.area_name_en) = lower({area:String})")
        params["area"] = area
    if developer:
        developer_names = developer_filter_names(developer)
        if developer_names:
            filters.append(
                """
                lower(coalesce(nullIf(f.developer_name, ''), g.developer_name))
                IN {developer_names:Array(String)}
                """
            )
            params["developer_names"] = developer_names
    return " AND ".join(filters), params


def _radar_building_pins_sql() -> str:
    return """
        WITH activity_anchor AS (
            SELECT greatest(
                (SELECT max(transaction_date)
                 FROM dxbi_sales_unit_events FINAL
                 WHERE transaction_date <= today()),
                (SELECT max(lease_start)
                 FROM dxbi_rental_events FINAL
                 WHERE lease_start <= today())
            ) AS period_end
        ),
        tower_nodes AS (
            SELECT
                canonical_location_id,
                source_location_id,
                name,
                latitude,
                longitude
            FROM geo_location_nodes
            WHERE location_type = 'TOWER'
              AND latitude BETWEEN {south:Float64} AND {north:Float64}
              AND longitude BETWEEN {west:Float64} AND {east:Float64}
              AND coordinate_status = 'valid'
        ),
        location_context AS (
            SELECT
                c.descendant_location_id AS canonical_location_id,
                argMinIf(n.name, c.depth, n.location_type = 'SUBCOMMUNITY')
                    AS project_name,
                argMinIf(n.name, c.depth, n.location_type = 'COMMUNITY')
                    AS area_name
            FROM geo_location_closure AS c
            INNER JOIN geo_location_nodes AS n
                ON n.canonical_location_id = c.ancestor_location_id
            INNER JOIN tower_nodes AS tower
                ON tower.canonical_location_id = c.descendant_location_id
            GROUP BY c.descendant_location_id
        ),
        tower_links AS (
            SELECT
                gm.source_system,
                gm.source_key,
                gm.canonical_location_id,
                gm.property_type,
                gm.market_status,
                gm.raw_area_name,
                gm.raw_project_name,
                gm.raw_building_name
            FROM geo_mapped_source_facts AS gm
            INNER JOIN tower_nodes AS tower
                ON tower.canonical_location_id = gm.canonical_location_id
            WHERE gm.source_system IN ('dxbi_sales', 'dxbi_rentals')
              AND gm.source_grain = 'building'
              AND gm.target_grain = 'tower'
              AND gm.match_status IN ('auto_approved', 'manual_approved')
        ),
        sales_by_tower AS (
            SELECT
                links.canonical_location_id,
                uniqExact(links.source_key) AS matched_sale_signatures,
                count() AS sales_transaction_count,
                sum(e.sale_amount_aed) AS total_sales_volume,
                quantile(0.5)(e.sale_amount_aed) AS median_sale_price,
                max(e.transaction_date) AS last_sale_date
            FROM dxbi_sales_unit_events AS e FINAL
            CROSS JOIN activity_anchor AS anchor
            INNER JOIN tower_links AS links
                ON links.source_system = 'dxbi_sales'
               AND links.raw_area_name = e.area_name
               AND links.raw_project_name = e.project_name
               AND links.raw_building_name = e.building_name
               AND links.property_type = e.property_type
               AND links.market_status = e.market_status
            WHERE lowerUTF8(e.market_status) != 'offplan'
              AND e.transaction_date > anchor.period_end
                    - toIntervalDay({period_days:UInt16})
              AND e.transaction_date <= anchor.period_end
            GROUP BY links.canonical_location_id
        ),
        rents_by_tower AS (
            SELECT
                links.canonical_location_id,
                uniqExact(links.source_key) AS matched_rental_signatures,
                count() AS rental_contract_count,
                quantile(0.5)(e.annual_rent_aed) AS median_annual_rent,
                max(e.lease_start) AS last_rental_date
            FROM dxbi_rental_events AS e FINAL
            CROSS JOIN activity_anchor AS anchor
            INNER JOIN tower_links AS links
                ON links.source_system = 'dxbi_rentals'
               AND links.raw_area_name = e.area_name
               AND links.raw_project_name = e.project_name
               AND links.raw_building_name = e.building_name
               AND links.property_type = e.property_type
               AND links.market_status = ''
            WHERE e.lease_start > anchor.period_end - toIntervalDay({period_days:UInt16})
              AND e.lease_start <= anchor.period_end
            GROUP BY links.canonical_location_id
        ),
        listings_by_tower AS (
            SELECT
                building_location_id AS source_location_id,
                uniqExactIf(
                    if(listing_id != '', listing_id, property_key),
                    is_available = 1
                ) AS listing_inventory_count
            FROM pf_listings_bronze
            INNER JOIN tower_nodes AS tower
                ON tower.source_location_id = building_location_id
            WHERE building_location_id != ''
            GROUP BY building_location_id
        )
        SELECT
            tower.canonical_location_id AS location_id,
            tower.source_location_id AS source_location_id,
            tower.name AS building_name,
            nullIf(context.project_name, '') AS project_name,
            if(context.area_name != '', context.area_name, 'Unknown') AS area_name,
            tower.latitude AS latitude,
            tower.longitude AS longitude,
            'property_finder' AS coordinate_source,
            'tower' AS coordinate_precision,
            ifNull(sales.matched_sale_signatures, 0)
                + ifNull(rents.matched_rental_signatures, 0) AS matched_source_signatures,
            ifNull(sales.sales_transaction_count, 0) AS sales_transaction_count,
            ifNull(rents.rental_contract_count, 0) AS rental_contract_count,
            ifNull(sales.total_sales_volume, 0) AS total_sales_volume,
            nullIf(sales.median_sale_price, 0) AS median_sale_price,
            nullIf(rents.median_annual_rent, 0) AS median_annual_rent,
            ifNull(listings.listing_inventory_count, 0) AS listing_inventory_count,
            nullIf(
                greatest(
                    ifNull(sales.last_sale_date, toDate(0)),
                    ifNull(rents.last_rental_date, toDate(0))
                ),
                toDate(0)
            ) AS last_activity_date
        FROM tower_nodes AS tower
        LEFT JOIN location_context AS context
            ON context.canonical_location_id = tower.canonical_location_id
        LEFT JOIN sales_by_tower AS sales
            ON sales.canonical_location_id = tower.canonical_location_id
        LEFT JOIN rents_by_tower AS rents
            ON rents.canonical_location_id = tower.canonical_location_id
        LEFT JOIN listings_by_tower AS listings
            ON listings.source_location_id = tower.source_location_id
        ORDER BY
            sales_transaction_count + rental_contract_count DESC,
            listing_inventory_count DESC,
            building_name
        LIMIT {limit:UInt32}
    """


async def fetch_project_pins(*, area, developer, east, limit, north, period_days, south, west):
    radar_geo_cte = _radar_geo_cte()
    where_clause, params = _radar_project_pin_filters(
        west=west,
        south=south,
        east=east,
        north=north,
        area=area,
        developer=developer,
    )
    params["limit"] = limit
    params["period_days"] = period_days
    project_rows, developer_rows = await asyncio.gather(
        query(_radar_project_pins_sql(radar_geo_cte, where_clause), params),
        query(_radar_developer_lookup_sql(), {}),
    )
    return project_rows, developer_rows


async def fetch_overview(*, period_days):
    radar_geo_cte = _radar_geo_cte()
    params = {"period_days": period_days}
    area_rows, stats_rows, developer_rows = await asyncio.gather(
        query(_radar_area_sql(radar_geo_cte), params),
        query(_radar_stats_sql(radar_geo_cte), params),
        query(_radar_developer_lookup_sql(), {}),
    )
    return area_rows, stats_rows, developer_rows


async def fetch_building_pins(parameters):
    return await query(_radar_building_pins_sql(), parameters)
