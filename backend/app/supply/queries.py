from datetime import date, datetime, time, timezone
from typing import Any

from app.clickhouse.client import query
from app.supply.scope import reelly_dubai_project_scope


async def fetch_matching_inventory(parameters):
    return await query(
        f"""
        SELECT
            p.project_id,
            p.project_name,
            p.developer_name,
            p.area_name,
            p.sale_status,
            p.units_in_sale,
            p.min_price_aed,
            p.max_price_aed,
            p.completion_date,
            p.readiness_progress,
            p.website_url,
            p.scraped_at,
            u.unit_type_key,
            u.unit_bedrooms,
            u.unit_type,
            u.price_from_aed AS unit_price_from,
            u.price_to_aed AS unit_price_to,
            u.area_from_sqft,
            u.area_to_sqft,
            u.units_amount
        FROM (
            SELECT
                project_id,
                project_name,
                developer_name,
                area_name,
                sale_status,
                units_in_sale,
                min_price_aed,
                max_price_aed,
                completion_date,
                readiness_progress,
                website_url,
                scraped_at
            FROM reelly_projects_bronze
            WHERE {reelly_dubai_project_scope()}
            ORDER BY parseDateTimeBestEffortOrNull(scraped_at) DESC
            LIMIT 1 BY project_id
        ) AS p
        LEFT JOIN (
            SELECT
                project_id,
                unit_type_key,
                unit_bedrooms,
                unit_type,
                price_from_aed,
                price_to_aed,
                area_from_sqft,
                area_to_sqft,
                units_amount
            FROM reelly_project_unit_types_bronze
            ORDER BY parseDateTimeBestEffortOrNull(scraped_at) DESC
            LIMIT 1 BY unit_type_key
        ) AS u USING (project_id)
        WHERE
            lowerUTF8(p.sale_status) = 'on sale'
            AND (
                {{area:String}} = ''
                OR positionCaseInsensitiveUTF8(p.area_name, {{area:String}}) > 0
                OR positionCaseInsensitiveUTF8(p.project_name, {{area:String}}) > 0
            )
        ORDER BY coalesce(u.price_from_aed, p.min_price_aed) ASC
        LIMIT 120
        """,
        parameters,
    )


async def fetch_catalog_freshness(parameters):
    return await query(
        "SELECT max(parseDateTimeBestEffortOrNull(scraped_at)) AS refreshed "
        "FROM reelly_projects_bronze "
        f"WHERE {reelly_dubai_project_scope()}",
        parameters,
    )


SUPPLY_CATALOGUE_SORTS = {
    "recommended",
    "newest",
    "price_low",
    "price_high",
    "delivery_soon",
    "delivery_latest",
}


async def _latest_sales_date() -> date | None:
    rows = await query(
        """
        SELECT max(instance_date) AS max_date
        FROM ch_transactions
        WHERE trans_group_en = 'Sales'
          AND actual_worth > 0
        """,
        {},
    )
    if not rows:
        return None
    value = rows[0].get("max_date")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _catalogue_today_ms() -> int:
    today_start = datetime.combine(date.today(), time.min, tzinfo=timezone.utc)
    return int(today_start.timestamp() * 1000)


def _catalogue_ch_filters(
    search: str | None,
    area: str | None,
    status: str | None,
    developer: str | None = None,
    include_past: bool = False,
) -> tuple[str, dict[str, Any]]:
    clauses = [
        reelly_dubai_project_scope(),
        "JSON_VALUE(list_item_json, '$.Project_name') != ''",
    ]
    params: dict[str, Any] = {}
    if not include_past:
        clauses.append(
            """
            (
                toInt64OrNull(JSON_VALUE(list_item_json, '$.Completion_time')) IS NULL
                OR toInt64OrNull(JSON_VALUE(list_item_json, '$.Completion_time')) >= {today_ms:Int64}
            )
            """
        )
        params["today_ms"] = _catalogue_today_ms()
    if search:
        clauses.append(
            """
            (
                positionCaseInsensitive(JSON_VALUE(list_item_json, '$.Project_name'), {search:String}) > 0
                OR positionCaseInsensitive(JSON_VALUE(list_item_json, '$.Developers_name'), {search:String}) > 0
                OR positionCaseInsensitive(JSON_VALUE(list_item_json, '$.Area_name'), {search:String}) > 0
            )
            """
        )
        params["search"] = search
    if area:
        clauses.append("JSON_VALUE(list_item_json, '$.Area_name') = {area:String}")
        params["area"] = area
    if status:
        clauses.append("JSON_VALUE(list_item_json, '$.Status') = {status:String}")
        params["status"] = status
    if developer:
        clauses.append(
            "positionCaseInsensitive(JSON_VALUE(list_item_json, '$.Developers_name'), {developer:String}) > 0"
        )
        params["developer"] = developer
    return " AND ".join(clauses), params


def _catalogue_ch_order_by(sort: str) -> str:
    normalized = sort if sort in SUPPLY_CATALOGUE_SORTS else "recommended"
    min_price = "nullIf(toInt64OrNull(JSON_VALUE(list_item_json, '$.min_price')), 0)"
    completion_time = "toInt64OrNull(JSON_VALUE(list_item_json, '$.Completion_time'))"
    if normalized == "newest":
        return "parseDateTimeBestEffortOrNull(scraped_at) DESC NULLS LAST, toInt64OrNull(project_key) DESC"
    if normalized == "price_low":
        return f"{min_price} ASC NULLS LAST, JSON_VALUE(list_item_json, '$.Project_name') ASC"
    if normalized == "price_high":
        return f"{min_price} DESC NULLS LAST, JSON_VALUE(list_item_json, '$.Project_name') ASC"
    if normalized == "delivery_soon":
        return f"{completion_time} ASC NULLS LAST, JSON_VALUE(list_item_json, '$.Project_name') ASC"
    if normalized == "delivery_latest":
        return (
            f"{completion_time} DESC NULLS LAST, JSON_VALUE(list_item_json, '$.Project_name') ASC"
        )
    return f"""
        multiIf(
            JSON_VALUE(list_item_json, '$.sale_status') = 'On sale', 0,
            positionCaseInsensitive(JSON_VALUE(list_item_json, '$.sale_status'), 'presale') > 0, 1,
            2
        ) ASC,
        {min_price} ASC NULLS LAST,
        JSON_VALUE(list_item_json, '$.Project_name') ASC
    """


async def _catalogue_facet_values(
    field_path: str,
    filters: dict[str, Any],
    limit: int,
) -> list[str]:
    where, params = _catalogue_ch_filters(**filters)
    rows = await query(
        f"""
        SELECT JSON_VALUE(list_item_json, '$.{field_path}') AS value, count() AS n
        FROM reelly_projects_bronze
        WHERE {where}
          AND JSON_VALUE(list_item_json, '$.{field_path}') != ''
        GROUP BY value
        ORDER BY n DESC, value ASC
        LIMIT {{limit:Int32}}
        """,
        {**params, "limit": limit},
    )
    return [str(row["value"]) for row in rows if row.get("value")]


async def fetch_developer_ranking_rows(parameters):
    return await query(
        """
        SELECT
            max(pf.developer_id) AS developer_id,
            any(pf.developer_name) AS developer_name,
            sum(pf.sales_transaction_count_12m) AS units_sold,
            sum(pf.sales_transaction_count_12m) AS sales_transaction_count,
            sum(pf.total_sales_volume_12m) AS sales_volume_aed,
            if(
                sum(pf.sales_transaction_count_12m) > 0,
                sum(pf.avg_sale_price_sqm_12m * pf.sales_transaction_count_12m)
                    / sum(pf.sales_transaction_count_12m) / {sqm_to_sqft:Float64},
                NULL
            ) AS avg_price_sqft_aed,
            countIf(pf.sales_transaction_count_12m > 0) AS selling_projects,
            count() AS total_projects,
            countIf(pf.completion_date IS NULL AND pf.cancellation_date IS NULL) AS active_projects,
            countIf(pf.completion_date IS NOT NULL) AS completed_projects,
            sumIf(pf.no_of_units, pf.completion_date IS NOT NULL) AS completed_units,
            sumIf(pf.no_of_units, pf.completion_date IS NULL AND pf.cancellation_date IS NULL) AS pipeline_units,
            sum(pf.no_of_units) AS registered_units,
            avgIf(pf.percent_completed, pf.completion_date IS NULL AND pf.cancellation_date IS NULL) AS avg_completion_pct,
            sumIf(
                pf.no_of_units,
                pf.completion_date IS NOT NULL
                AND pf.project_end_date IS NOT NULL
                AND pf.completion_date <= pf.project_end_date
            ) AS on_time_units,
            sumIf(
                pf.no_of_units,
                pf.completion_date IS NOT NULL AND pf.project_end_date IS NOT NULL
            ) AS completed_units_with_schedule,
            countIf(pf.project_start_date >= subtractYears({end_date:Date}, 10)) AS initiated_projects_10y,
            countIf(
                pf.project_start_date >= subtractYears({end_date:Date}, 10)
                AND pf.completion_date IS NOT NULL
            ) AS completed_projects_10y,
            if(
                completed_units_with_schedule > 0,
                on_time_units / completed_units_with_schedule * 100,
                NULL
            ) AS on_time_delivery_score,
            if(registered_units > 0, least(100, units_sold / registered_units * 100), NULL)
                AS sales_completion_score,
            if(
                initiated_projects_10y > 0,
                completed_projects_10y / initiated_projects_10y * 100,
                NULL
            ) AS historical_project_success_score
        FROM ch_project_fact AS pf
        WHERE pf.developer_name != ''
        GROUP BY lowerUTF8(pf.developer_name)
        """,
        parameters,
    )


async def fetch_upcoming_projects(parameters):
    return await query(
        """
            SELECT
                p.project_id,
                p.project_name_en,
                p.developer_id,
                d.developer_name_en as developer_name,
                p.area_name_en,
                p.project_start_date,
                p.project_end_date,
                p.percent_completed,
                p.no_of_buildings,
                p.no_of_units
            FROM ch_projects p
            LEFT JOIN ch_developers d ON p.developer_id = d.developer_id
            WHERE
                coalesce(p.no_of_units, 0) > 0
                AND coalesce(p.completion_date, p.project_end_date) > {anchor_date:Date}
                AND p.cancellation_date IS NULL
            ORDER BY
                coalesce(p.completion_date, p.project_end_date) ASC NULLS LAST,
                p.project_name_en ASC
            LIMIT {limit:Int32}
            OFFSET {offset:Int32}
        """,
        parameters,
    )


async def fetch_dld_project_signals(parameters):
    return await query(
        """
            SELECT
                p.project_id AS project_id,
                p.project_name_en AS project_name,
                coalesce(nullIf(d.developer_name_en, ''), nullIf(p.developer_name, '')) AS developer_name,
                p.area_name_en AS area_name,
                p.project_start_date AS project_start_date,
                coalesce(p.completion_date, p.project_end_date) AS project_end_date,
                p.percent_completed AS percent_completed,
                p.no_of_units AS no_of_units
            FROM ch_projects p
            LEFT JOIN ch_developers d ON p.developer_id = d.developer_id
            WHERE p.project_name_en != ''
              AND (
                    {area_count:Int32} = 0
                    OR has({areas:Array(String)}, lowerUTF8(coalesce(p.area_name_en, '')))
                  )
            LIMIT 5000
            """,
        parameters,
    )


async def fetch_catalogue_rows(filters, *, limit: int, offset: int, sort: str):
    where, parameters = _catalogue_ch_filters(**filters)
    parameters = {**parameters, "limit": limit, "offset": offset}
    return await query(
        f"""
        SELECT project_key, project_id, run_id, scraped_at, list_item_json, raw_json
        FROM reelly_projects_bronze
        WHERE {where}
        ORDER BY {_catalogue_ch_order_by(sort)}
        LIMIT {{limit:Int32}} OFFSET {{offset:Int32}}
        """,
        parameters,
    )


async def fetch_catalogue_count(filters):
    where, parameters = _catalogue_ch_filters(**filters)
    return await query(
        f"""
        SELECT count() AS total
        FROM reelly_projects_bronze
        WHERE {where}
        """,
        parameters,
    )


async def fetch_catalogue_detail(parameters):
    return await query(
        f"""
        SELECT project_key, project_id, run_id, scraped_at, list_item_json, raw_json
        FROM reelly_projects_bronze
        WHERE (project_key = {{project_id:String}} OR project_id = {{project_id:String}})
          AND {reelly_dubai_project_scope()}
        LIMIT 1
        """,
        parameters,
    )
