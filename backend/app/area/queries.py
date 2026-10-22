from __future__ import annotations

from app.clickhouse.client import query

_SORT_OPTIONS = {
    "units": "pipeline_units DESC, active_projects DESC, area_name_en ASC",
    "volume": "total_sales_volume_12m DESC, pipeline_units DESC, area_name_en ASC",
    "transactions": "sales_transaction_count_12m DESC, pipeline_units DESC, area_name_en ASC",
    "yield": "gross_yield_pct DESC NULLS LAST, pipeline_units DESC, area_name_en ASC",
    "completion": "avg_completion_pct DESC NULLS LAST, pipeline_units DESC, area_name_en ASC",
    "developers": "active_developers DESC, pipeline_units DESC, area_name_en ASC",
}

_AREA_MATCH_SQL = """
    replaceRegexpAll(lowerUTF8(trimBoth(ifNull(area_name_en, ''))), '[\\s\\-\\(\\)\\.,]+', '')
    = replaceRegexpAll(lowerUTF8(trimBoth(ifNull({area_name:String}, ''))), '[\\s\\-\\(\\)\\.,]+', '')
"""


def _resolve_sort_by(sort_by: str) -> str:
    return _SORT_OPTIONS.get(sort_by, _SORT_OPTIONS["units"])


async def fetch_areas(*, limit, offset, search, sort_by):
    order_by_sql = _resolve_sort_by(sort_by)
    where_clauses = ["area_name_en != ''"]
    params: dict[str, object] = {"limit": limit, "offset": offset}
    if search and search.strip():
        where_clauses.append(
            "positionCaseInsensitive(coalesce(area_name_en, ''), {search:String}) > 0"
        )
        params["search"] = search.strip()
    where_sql = " AND ".join(where_clauses)
    rows = await query(
        f"""
        SELECT
            area_name_en,
            area_id,
            active_projects,
            completed_projects,
            overdue_projects,
            pipeline_units,
            units_delivering_next_12_months,
            active_developers,
            avg_completion_pct,
            avg_delivery_confidence,
            sales_transaction_count_12m,
            rental_contract_count_12m,
            total_sales_volume_12m,
            median_sale_price,
            median_annual_rent,
            avg_sale_price_sqm_12m,
            avg_rent_price_sqm_12m,
            gross_yield_pct,
            top_developers
        FROM ch_area_fact
        WHERE {where_sql}
        ORDER BY {order_by_sql}
        LIMIT {{limit:Int32}} OFFSET {{offset:Int32}}
        """,
        params,
    )
    count_params = {k: v for k, v in params.items() if k not in {"limit", "offset"}}
    total_result = await query(
        f"""
        SELECT count() AS total
        FROM ch_area_fact
        WHERE {where_sql}
        """,
        count_params,
    )
    return rows, total_result


async def fetch_area_detail(parameters):
    return await query(
        f"""
        SELECT
            area_name_en,
            area_id,
            active_projects,
            completed_projects,
            overdue_projects,
            pipeline_units,
            units_delivering_next_12_months,
            active_developers,
            avg_completion_pct,
            avg_delivery_confidence,
            sales_transaction_count_12m,
            rental_contract_count_12m,
            total_sales_volume_12m,
            median_sale_price,
            median_annual_rent,
            avg_sale_price_sqm_12m,
            avg_rent_price_sqm_12m,
            gross_yield_pct,
            top_developers,
            top_master_projects
        FROM ch_area_fact
        WHERE {_AREA_MATCH_SQL}
        LIMIT 1
        """,
        parameters,
    )


async def fetch_developer_exposure(parameters):
    return await query(
        f"""
        SELECT
            developer_name,
            count() AS project_count,
            sum(no_of_units) AS pipeline_units,
            round(sum(total_sales_volume_12m), 2) AS total_sales_volume_12m,
            round(avgIf(percent_completed, percent_completed IS NOT NULL), 2) AS avg_completion_pct
        FROM ch_project_fact
        WHERE {_AREA_MATCH_SQL}
          AND developer_name != ''
        GROUP BY developer_name
        ORDER BY pipeline_units DESC, total_sales_volume_12m DESC, developer_name ASC
        LIMIT 8
        """,
        parameters,
    )


async def fetch_project_snapshot(parameters):
    return await query(
        f"""
        SELECT
            project_id,
            project_name_en,
            developer_name,
            master_project_en,
            completion_status,
            pipeline_status,
            no_of_units,
            percent_completed,
            estimated_delivery_confidence,
            total_sales_volume_12m,
            median_sale_price,
            median_annual_rent,
            gross_yield_pct
        FROM ch_project_fact
        WHERE {_AREA_MATCH_SQL}
        ORDER BY total_sales_volume_12m DESC, no_of_units DESC, project_name_en ASC
        LIMIT 12
        """,
        parameters,
    )
