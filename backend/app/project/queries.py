from app.clickhouse.client import query
from app.market.scope import PROJECT_LOCATION_PREDICATE
from app.project.radar_queries import _radar_geo_cte
from app.supply.scope import reelly_dubai_project_scope

GET_SIMILAR_PROJECTS_REF_SQL = """
    SELECT
        master_project_en,
        area_name_en,
        property_type_en,
        round(median(actual_worth), 0) as median_price,
        round(median(actual_area), 1) as median_area,
        count(*) as transaction_count
    FROM ch_transactions
    WHERE
        master_project_en ILIKE {project_name:String}
        AND actual_worth > 0
        AND actual_area > 0
        AND instance_date >= today() - 365
    GROUP BY master_project_en, area_name_en, property_type_en
    LIMIT 1
    """

GET_SIMILAR_PROJECTS_SIMILAR_SQL = """
        SELECT
            master_project_en,
            area_name_en,
            property_type_en,
            round(median(actual_worth), 0) as median_price,
            round(median(actual_area), 1) as median_area,
            count(*) as transaction_count,
            abs(median(actual_worth) - {ref_price:Float64}) as price_diff
        FROM ch_transactions
        WHERE
            master_project_en != {ref_project:String}
            AND property_type_en = {ref_type:String}
            AND actual_worth > 0
            AND actual_area > 0
            AND instance_date >= today() - 365
            AND master_project_en != ''
        GROUP BY master_project_en, area_name_en, property_type_en
        HAVING transaction_count >= 5
        ORDER BY price_diff ASC
        LIMIT {limit:Int32}
        """


async def fetch_similar_projects(parameters: dict):
    return await query(GET_SIMILAR_PROJECTS_SIMILAR_SQL, parameters)


async def fetch_similar_project_reference(parameters: dict):
    return await query(GET_SIMILAR_PROJECTS_REF_SQL, parameters)


async def fetch_project_catalogue(parameters):
    return await query(
        f"""
        WITH {_radar_geo_cte()}
        SELECT
            -toInt64(property_id) AS project_id,
            project_name_en,
            area_name_en,
            '' AS master_project_en,
            developer_name,
            latitude,
            longitude
        FROM geo_by_project
        ORDER BY project_name_en
        LIMIT 5000
    """,
        parameters,
    )


async def fetch_reelly_project(parameters):
    return await query(
        f"""
                SELECT
                    project_name,
                    area_name,
                    developer_name,
                    latitude,
                    longitude,
                    sale_status,
                    completion_time,
                    readiness_progress
                FROM silver_reelly_projects FINAL
                WHERE project_id = {{project_id:String}}
                  AND {reelly_dubai_project_scope()}
                  AND latitude BETWEEN 24 AND 26
                  AND longitude BETWEEN 54 AND 57
                LIMIT 1
                """,
        parameters,
    )


async def fetch_legacy_project(parameters):
    return await query(
        """
            SELECT
                any(gb.project_name_en) AS project_name_en,
                any(gb.area_name_en) AS area_name_en,
                any(gb.developer_name) AS developer_name,
                avg(gb.lat) AS latitude,
                avg(gb.lng) AS longitude
            FROM ch_geo_buildings AS gb
            WHERE gb.property_id = {property_id:UInt64}
              AND gb.project_name_en != ''
            GROUP BY gb.property_id
        """,
        parameters,
    )


async def fetch_legacy_project_geography(parameters):
    return await query(
        """
            SELECT property_id, building_name_en, project_name_en, lat, lng
            FROM ch_geo_buildings
            WHERE project_name_en = {project_name:String}
              AND lat BETWEEN 20 AND 30
              AND lng BETWEEN 50 AND 60
            ORDER BY building_name_en
            """,
        parameters,
    )


async def fetch_project_details(parameters):
    return await query(
        """
        SELECT
            p.project_id AS project_id,
            p.project_number,
            p.project_name_en,
            p.master_project_en,
            p.area_id,
            p.area_name_en,
            p.developer_id,
            COALESCE(NULLIF(d1.developer_name_en, ''), p.developer_name) AS developer_name,
            p.master_developer_id,
            COALESCE(NULLIF(d2.developer_name_en, ''), p.master_developer_name) AS master_developer_name,
            p.no_of_buildings,
            p.no_of_units,
            p.no_of_villas,
            p.no_of_lands,
            p.project_start_date,
            p.project_end_date,
            p.completion_date,
            p.percent_completed,
            p.project_status,
            COALESCE(NULLIF(g.centroid_lat, 0), 25.2048) AS latitude,
            COALESCE(NULLIF(g.centroid_lng, 0), 55.2708) AS longitude,
            g.polygon AS area_polygon
        FROM ch_projects p
        LEFT JOIN ch_developers d1 ON p.developer_name = d1.developer_name_ar
        LEFT JOIN ch_developers d2 ON p.master_developer_name = d2.developer_name_ar
        LEFT JOIN ch_geo_projects g ON p.project_id = g.project_id
        WHERE p.project_id = {project_id:Int32}
        LIMIT 1
    """,
        parameters,
    )


async def fetch_project_buildings(parameters):
    return await query(
        """
        SELECT property_id, building_number, floors
        FROM ch_buildings
        WHERE project_id = {project_id:Int32}
        ORDER BY building_number
        LIMIT 100
    """,
        parameters,
    )


async def fetch_project_composition(parameters):
    return await query(
        """
        SELECT
            property_sub_type_en,
            count() as count,
            round(avg(actual_area), 2) as avg_area_sqm
        FROM ch_units
        WHERE project_id = {project_id:Int32}
          AND property_sub_type_en != ''
        GROUP BY property_sub_type_en
        ORDER BY count DESC
    """,
        parameters,
    )


async def fetch_project_geography(parameters):
    return await query(
        """
        SELECT building_name_en, project_name_en, lat, lng
        FROM ch_geo_buildings
        WHERE project_name_en = {project_name:String}
        ORDER BY building_name_en
    """,
        parameters,
    )


async def fetch_master_projects(parameters):
    return await query(
        """
        SELECT
            p.project_id,
            p.project_name_en,
            p.project_status,
            p.percent_completed,
            p.no_of_buildings,
            p.no_of_units,
            p.area_name_en,
            COALESCE(NULLIF(d.developer_name_en, ''), p.developer_name) AS developer_name
        FROM ch_projects p
        LEFT JOIN ch_developers d ON p.developer_name = d.developer_name_ar
        WHERE p.master_project_en = {name:String}
          AND p.project_name_en != ''
        ORDER BY p.project_name_en
    """,
        parameters,
    )


async def fetch_master_geography(parameters):
    return await query(
        """
        SELECT building_name_en, project_name_en, lat, lng
        FROM ch_geo_buildings
        WHERE project_name_en IN {names:Array(String)}
        ORDER BY project_name_en, building_name_en
    """,
        parameters,
    )


async def fetch_master_composition(parameters):
    return await query(
        """
        SELECT
            property_sub_type_en,
            count() AS count,
            round(avg(actual_area), 2) AS avg_area_sqm
        FROM ch_units
        WHERE project_id IN {ids:Array(Int32)}
          AND property_sub_type_en != ''
        GROUP BY property_sub_type_en
        ORDER BY count DESC
    """,
        parameters,
    )


async def fetch_building(parameters):
    return await query(
        """
        SELECT
            property_id,
            building_number,
            floors,
            project_id,
            project_name_en,
            area_name_en
        FROM ch_buildings
        WHERE property_id = {property_id:Int32}
        LIMIT 1
    """,
        parameters,
    )


async def fetch_building_units(parameters):
    return await query(
        """
        SELECT
            rooms_en,
            count() AS count,
            round(avg(actual_area), 2) AS avg_area_sqm
        FROM ch_units
        WHERE parent_property_id = {property_id:Int32}
          AND rooms_en != ''
        GROUP BY rooms_en
        ORDER BY count DESC
    """,
        parameters,
    )


async def fetch_market_snapshot(parameters):
    return await query(
        """
        SELECT
            project_id,
            project_name_en,
            area_name_en,
            developer_name,
            master_project_en,
            completion_status,
            pipeline_status,
            estimated_delivery_confidence,
            sales_transaction_count_12m,
            rental_contract_count_12m,
            total_sales_volume_12m,
            median_sale_price,
            median_annual_rent,
            gross_yield_pct,
            avg_sale_price_sqm_12m,
            avg_rent_price_sqm_12m
        FROM ch_project_fact
        WHERE project_id = {project_id:Int32}
        LIMIT 1
        """,
        parameters,
    )


# Delivery status of a DLD-registered project on the reference date. Completion and
# cancellation recorded by DLD take precedence over the planned end date.
_DELIVERY_STATUS = """multiIf(
    cancellation_date IS NOT NULL, 'cancelled',
    completion_date IS NOT NULL, 'completed',
    project_end_date IS NULL, 'missing_date',
    toDate(project_end_date) < {reference_date:Date}, 'overdue',
    toDate(project_end_date) <= {horizon_end:Date}, 'scheduled_in_horizon',
    'scheduled_later')"""

DELIVERY_GROUPS = {
    "delivery_status": "delivery_status",
    "developer": "if(developer_name != '', developer_name, developer_name_en)",
    "delivery_year": "toString(toYear(project_end_date))",
    "project": "project_name_en",
}


async def fetch_district_only_projects(parameters: dict) -> list[dict]:
    """Districts of the located projects, with how many projects there name no master project.

    Such a project may belong to the requested community, but DLD does not say so.
    """
    return await query(
        f"""
        SELECT area_name_en AS district, countIf(master_project_en = '') AS projects
        FROM ch_projects
        WHERE project_status IN {{statuses:Array(String)}}
          AND lowerUTF8(area_name_en) IN (
              SELECT lowerUTF8(area_name_en) FROM ch_projects
              WHERE {PROJECT_LOCATION_PREDICATE} AND master_project_en != '')
        GROUP BY district
        HAVING projects > 0
        ORDER BY district
        """,
        parameters,
    )


async def fetch_delivery_summary(parameters: dict, *, group_by: str, located: bool):
    location = f"AND {PROJECT_LOCATION_PREDICATE}" if located else ""
    return await query(
        f"""
        SELECT
            {DELIVERY_GROUPS[group_by]} AS group_key,
            count() AS project_count,
            sum(no_of_units) AS units,
            countIf(no_of_units = 0) AS projects_without_units
        FROM (
            SELECT *, {_DELIVERY_STATUS} AS delivery_status
            FROM ch_projects
            WHERE project_status IN {{statuses:Array(String)}} {location}
        )
        WHERE delivery_status IN {{delivery_statuses:Array(String)}}
        GROUP BY group_key
        ORDER BY units DESC, group_key
        LIMIT {{limit:UInt32}}
        """,
        parameters,
    )


async def fetch_project_register_snapshot():
    return await query(
        "SELECT max(scraped_at) AS snapshot_at, count() AS projects FROM ch_projects"
    )
