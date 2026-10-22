from datetime import date, timedelta
from typing import Any

from app.analytics.metrics import (
    _chart_area_inputs,
    _chart_days,
    _chart_min_records,
    _chart_sort_direction,
    _optional_chart_filter,
    _rental_property_type,
    _sales_property_type,
    _trend_interval,
    clamp_int,
    clean_text,
)
from app.analytics.queries import price_trends
from app.clickhouse.client import query as ch_query
from app.market.reference import SQM_TO_SQFT, area_search_text


async def _resolve_transaction_area(area: str | None) -> str | None:
    if not area:
        return None

    search_text = area_search_text(area)
    sql = """
    SELECT location_name
    FROM ch_transactions
    WHERE location_name != ''
      AND (
        lower(location_name) = lower({area:String})
        OR lower(location_name) = lower({search_text:String})
        OR location_name ILIKE {pattern:String}
        OR lower(master_project_en) = lower({search_text:String})
        OR master_project_en ILIKE {pattern:String}
        OR project_name_en ILIKE {pattern:String}
      )
      AND trans_group_en = 'Sales'
      AND actual_worth > 0
    GROUP BY location_name
    ORDER BY
        (lower(location_name) = lower({area:String})) DESC,
        (lower(location_name) = lower({search_text:String})) DESC,
        count() DESC
    LIMIT 1
    """
    rows = await ch_query(
        sql, {"area": area, "search_text": search_text, "pattern": f"%{search_text}%"}
    )
    return rows[0]["location_name"] if rows else area


async def _latest_transaction_date(
    *,
    property_usage: str | None,
    property_type: str | None,
    area: str | None,
    master_project: str | None = None,
) -> date | None:
    where_clauses = ["trans_group_en = 'Sales'", "actual_worth > 0", "instance_date IS NOT NULL"]
    params: dict[str, Any] = {}

    if property_usage:
        where_clauses.append("property_usage_en = {property_usage:String}")
        params["property_usage"] = property_usage
    if property_type:
        where_clauses.append("property_type = {property_type:String}")
        params["property_type"] = property_type
    if area:
        where_clauses.append("location_name = {area_name:String}")
        params["area_name"] = area
    if master_project:
        where_clauses.append("master_project_en = {master_project:String}")
        params["master_project"] = master_project

    rows = await ch_query(
        f"""
        SELECT max(instance_date) AS max_date
        FROM ch_transactions
        WHERE {" AND ".join(where_clauses)}
        """,
        params,
    )
    if not rows:
        return None

    max_date = rows[0].get("max_date")
    if isinstance(max_date, date):
        return max_date
    if isinstance(max_date, str):
        try:
            return date.fromisoformat(max_date)
        except ValueError:
            return None
    return None


async def _latest_rental_contract_date(
    *,
    property_usage: str | None,
    property_type: str | None,
    area: str | None,
    master_project: str | None = None,
) -> date | None:
    where_clauses = [
        "annual_amount > 0",
        "contract_start_date IS NOT NULL",
        "contract_start_date <= today()",
    ]
    params: dict[str, Any] = {}

    if property_usage:
        where_clauses.append("property_usage_en = {property_usage:String}")
        params["property_usage"] = property_usage
    if property_type:
        where_clauses.append("property_type = {property_type:String}")
        params["property_type"] = property_type
    if area:
        where_clauses.append("location_name = {area_name:String}")
        params["area_name"] = area
    if master_project:
        where_clauses.append("master_project_en = {master_project:String}")
        params["master_project"] = master_project

    rows = await ch_query(
        f"""
        SELECT max(contract_start_date) AS max_date
        FROM ch_rent_contracts
        WHERE {" AND ".join(where_clauses)}
        """,
        params,
    )
    if not rows:
        return None

    max_date = rows[0].get("max_date")
    if isinstance(max_date, date):
        return max_date
    if isinstance(max_date, str):
        try:
            return date.fromisoformat(max_date)
        except ValueError:
            return None
    return None


async def _resolve_transaction_scope(area: str | None) -> dict[str, str | None]:
    if not area:
        return {"label": None, "area": None, "master_project": None}

    search_text = area_search_text(area)
    master_rows = await ch_query(
        """
        SELECT master_project_en
        FROM ch_transactions
        WHERE master_project_en != ''
          AND lower(master_project_en) = lower({search_text:String})
          AND trans_group_en = 'Sales'
          AND actual_worth > 0
        GROUP BY master_project_en
        ORDER BY count() DESC
        LIMIT 1
        """,
        {"search_text": search_text},
    )
    if master_rows:
        master_project = str(master_rows[0]["master_project_en"])
        return {"label": master_project, "area": None, "master_project": master_project}

    resolved_area = await _resolve_transaction_area(area)
    return {"label": resolved_area, "area": resolved_area, "master_project": None}


async def _resolve_fact_area(area: str | None) -> str | None:
    if not area:
        return None

    search_text = area_search_text(area)
    rows = await ch_query(
        """
        SELECT area_name_en
        FROM ch_area_fact
        WHERE area_name_en != ''
          AND (
            lower(area_name_en) = lower({area:String})
            OR lower(area_name_en) = lower({search_text:String})
            OR positionCaseInsensitive(area_name_en, {search_text:String}) > 0
            OR arrayExists(
                project -> positionCaseInsensitive(project, {search_text:String}) > 0,
                top_master_projects
            )
          )
        ORDER BY
            (lower(area_name_en) = lower({area:String})) DESC,
            (lower(area_name_en) = lower({search_text:String})) DESC,
            sales_transaction_count_12m DESC,
            pipeline_units DESC,
            area_name_en ASC
        LIMIT 1
        """,
        {"area": area, "search_text": search_text},
    )
    return str(rows[0]["area_name_en"]) if rows else search_text


def _scope_clauses(scope: dict[str, str | None], params: dict[str, Any], prefix: str) -> list[str]:
    clauses: list[str] = []
    if scope.get("area"):
        key = f"{prefix}_area"
        params[key] = scope["area"]
        clauses.append(f"location_name = {{{key}:String}}")
    if scope.get("master_project"):
        key = f"{prefix}_master_project"
        params[key] = scope["master_project"]
        clauses.append(f"master_project_en = {{{key}:String}}")
    return clauses


def _scope_filter_and_label_expr(
    scopes: list[dict[str, str | None]], params: dict[str, Any], prefix: str
) -> tuple[str | None, str | None]:
    filters: list[str] = []
    label_expr_parts: list[str] = []
    for index, scope in enumerate(scopes):
        scope_prefix = f"{prefix}_{index}"
        clauses = _scope_clauses(scope, params, scope_prefix)
        label = scope.get("label")
        if not clauses or not label:
            continue

        condition = "(" + " OR ".join(clauses) + ")"
        label_key = f"{scope_prefix}_label"
        params[label_key] = label
        filters.append(condition)
        label_expr_parts.extend([condition, f"{{{label_key}:String}}"])

    if not filters:
        return None, None
    return "(" + " OR ".join(filters) + ")", f"multiIf({', '.join(label_expr_parts)}, 'Other')"


async def _resolve_transaction_scopes(area_inputs: list[str]) -> list[dict[str, str | None]]:
    scopes: list[dict[str, str | None]] = []
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for area in area_inputs:
        scope = await _resolve_transaction_scope(area)
        key = (scope.get("area"), scope.get("master_project"), scope.get("label"))
        if scope.get("label") and key not in seen:
            scopes.append(scope)
            seen.add(key)
    return scopes


async def _latest_transaction_date_for_scopes(
    *,
    property_usage: str | None,
    property_type: str | None,
    scopes: list[dict[str, str | None]],
) -> date | None:
    where_clauses = ["trans_group_en = 'Sales'", "actual_worth > 0", "instance_date IS NOT NULL"]
    params: dict[str, Any] = {}

    if property_usage:
        where_clauses.append("property_usage_en = {property_usage:String}")
        params["property_usage"] = property_usage
    if property_type:
        where_clauses.append("property_type = {property_type:String}")
        params["property_type"] = property_type

    scope_filter, _ = _scope_filter_and_label_expr(scopes, params, "scope")
    if scope_filter:
        where_clauses.append(scope_filter)

    rows = await ch_query(
        f"""
        SELECT max(instance_date) AS max_date
        FROM ch_transactions
        WHERE {" AND ".join(where_clauses)}
        """,
        params,
    )
    if not rows:
        return None

    max_date = rows[0].get("max_date")
    if isinstance(max_date, date):
        return max_date
    if isinstance(max_date, str):
        try:
            return date.fromisoformat(max_date)
        except ValueError:
            return None
    return None


async def _latest_rental_contract_date_for_scopes(
    *,
    property_usage: str | None,
    property_type: str | None,
    scopes: list[dict[str, str | None]],
) -> date | None:
    where_clauses = [
        "annual_amount > 0",
        "contract_start_date IS NOT NULL",
        "contract_start_date <= today()",
    ]
    params: dict[str, Any] = {}

    if property_usage:
        where_clauses.append("property_usage_en = {property_usage:String}")
        params["property_usage"] = property_usage
    if property_type:
        where_clauses.append("property_type = {property_type:String}")
        params["property_type"] = property_type

    scope_filter, _ = _scope_filter_and_label_expr(scopes, params, "scope")
    if scope_filter:
        where_clauses.append(scope_filter)

    rows = await ch_query(
        f"""
        SELECT max(contract_start_date) AS max_date
        FROM ch_rent_contracts
        WHERE {" AND ".join(where_clauses)}
        """,
        params,
    )
    if not rows:
        return None

    max_date = rows[0].get("max_date")
    if isinstance(max_date, date):
        return max_date
    if isinstance(max_date, str):
        try:
            return date.fromisoformat(max_date)
        except ValueError:
            return None
    return None


def _chart_order_clause(sort_direction: str, *, limit_key: str) -> str:
    if sort_direction == "asc":
        return f"ORDER BY value ASC LIMIT {{{limit_key}:Int32}}"
    return f"ORDER BY value DESC LIMIT {{{limit_key}:Int32}}"


async def fetch_market_trend_rows(args: dict[str, Any]):
    days = _chart_days(args.get("days"))
    interval = _trend_interval(days)
    property_usage = clean_text(args.get("property_usage")) or "Residential"
    property_type = clean_text(args.get("property_type"))
    area = await _resolve_transaction_area(clean_text(args.get("area")))
    end_date = (
        await _latest_transaction_date(
            property_usage=property_usage,
            property_type=property_type,
            area=area,
        )
        or date.today()
    )
    start_date = end_date - timedelta(days=days)

    sql = price_trends(
        property_usage=property_usage,
        property_type=property_type,
        area_name=area,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        interval=interval,
    )
    params: dict[str, Any] = {
        "property_usage": property_usage,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }
    if property_type:
        params["property_type"] = property_type
    if area:
        params["area_name"] = area

    rows = await ch_query(sql, params)
    return (
        rows,
        area,
        days,
        interval,
        property_usage,
        property_type,
    )


async def fetch_sales_chart_rows(args: dict[str, Any]):
    days = _chart_days(args.get("days"))
    limit = clamp_int(args.get("limit"), default=8, min_value=1, max_value=20)
    group_by = str(args.get("group_by") or "area").lower()
    metric = str(args.get("metric") or "transaction_count").lower()
    sort_direction = _chart_sort_direction(args.get("sort_direction"))
    property_usage = _optional_chart_filter(args.get("property_usage")) or "Residential"
    property_type = _sales_property_type(args.get("property_type"))
    area_inputs = _chart_area_inputs(args)
    scopes = await _resolve_transaction_scopes(area_inputs) if len(area_inputs) > 1 else []
    is_multi_area = len(scopes) > 1
    scope = (
        {"label": None, "area": None, "master_project": None}
        if is_multi_area
        else await _resolve_transaction_scope(area_inputs[0] if area_inputs else None)
    )
    area = scope["area"]
    master_project = scope["master_project"]
    scope_label = scope["label"]

    group_exprs = {
        "time": None,
        "area": "location_name",
        "property_type": "property_type",
        "bedrooms": "rooms_en",
        "project": "project_name_en",
        "master_project": "master_project_en",
        "transaction_type": "reg_type_en",
    }
    if group_by not in group_exprs:
        group_by = "area"

    metric_exprs = {
        "transaction_count": "count()",
        "total_volume": "round(sum(actual_worth), 0)",
        "avg_price": "round(avg(actual_worth), 0)",
        "median_price": "round(median(actual_worth), 0)",
        "avg_price_sqm": "round(avg(meter_sale_price), 0)",
        "median_price_sqm": "round(median(meter_sale_price), 0)",
        "avg_price_sqft": f"round(avg(meter_sale_price) / {SQM_TO_SQFT}, 0)",
        "median_price_sqft": f"round(median(meter_sale_price) / {SQM_TO_SQFT}, 0)",
    }
    if metric not in metric_exprs:
        metric = "transaction_count"
    min_records = _chart_min_records(args, group_by=group_by, metric=metric)

    if is_multi_area:
        end_date = (
            await _latest_transaction_date_for_scopes(
                property_usage=property_usage,
                property_type=property_type,
                scopes=scopes,
            )
            or date.today()
        )
    else:
        end_date = (
            await _latest_transaction_date(
                property_usage=property_usage,
                property_type=property_type,
                area=area,
                master_project=master_project,
            )
            or date.today()
        )
    start_date = end_date - timedelta(days=days)
    interval = _trend_interval(days)
    params: dict[str, Any] = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "limit": limit,
        "series_limit": limit * max(len(scopes), 1),
        "min_records": min_records,
    }
    scope_filter = None
    scope_label_expr = None
    if is_multi_area:
        scope_filter, scope_label_expr = _scope_filter_and_label_expr(scopes, params, "scope")

    if group_by == "time":
        if interval == "day":
            label_expr = "instance_date"
        elif interval == "week":
            label_expr = "toStartOfWeek(instance_date)"
        else:
            label_expr = "toStartOfMonth(instance_date)"
    elif is_multi_area and group_by == "area" and scope_label_expr:
        label_expr = scope_label_expr
    else:
        label_expr = group_exprs[group_by]

    where = [
        "trans_group_en = 'Sales'",
        "actual_worth > 0",
        "instance_date BETWEEN {start_date:Date} AND {end_date:Date}",
    ]
    if property_usage:
        where.append("property_usage_en = {property_usage:String}")
        params["property_usage"] = property_usage
    if property_type:
        where.append("property_type = {property_type:String}")
        params["property_type"] = property_type
    if is_multi_area and scope_filter:
        where.append(scope_filter)
    if not is_multi_area and area:
        where.append("location_name = {area_name:String}")
        params["area_name"] = area
    if not is_multi_area and master_project:
        where.append("master_project_en = {master_project:String}")
        params["master_project"] = master_project
    if group_by != "time" and label_expr:
        where.append(f"{label_expr} != ''")

    if is_multi_area and group_by != "area" and scope_label_expr:
        order_limit = (
            "ORDER BY series_label ASC, label ASC"
            if group_by == "time"
            else _chart_order_clause(sort_direction, limit_key="series_limit")
        )
        rows = await ch_query(
            f"""
            SELECT
                {scope_label_expr} AS series_label,
                {label_expr} AS label,
                {metric_exprs[metric]} AS value,
                count() AS record_count
            FROM ch_transactions
            WHERE {" AND ".join(where)}
            GROUP BY series_label, label
            HAVING value > 0 AND record_count >= {{min_records:Int32}}
            {order_limit}
            """,
            params,
        )
        series = True
    else:
        order_limit = (
            "ORDER BY label ASC"
            if group_by == "time"
            else _chart_order_clause(sort_direction, limit_key="limit")
        )
        rows = await ch_query(
            f"""
            SELECT
                {label_expr} AS label,
                {metric_exprs[metric]} AS value,
                count() AS record_count
            FROM ch_transactions
            WHERE {" AND ".join(where)}
            GROUP BY label
            HAVING value > 0 AND record_count >= {{min_records:Int32}}
            {order_limit}
            """,
            params,
        )
        series = False
    return (
        rows,
        series,
        group_by,
        metric,
        sort_direction,
        scopes,
        is_multi_area,
        scope_label,
        area,
        master_project,
        property_usage,
        property_type,
        days,
        interval,
        limit,
        min_records,
        start_date,
        end_date,
    )


async def fetch_rental_chart_rows(args: dict[str, Any]):
    days = _chart_days(args.get("days"))
    limit = clamp_int(args.get("limit"), default=8, min_value=1, max_value=20)
    group_by = str(args.get("group_by") or "area").lower()
    metric = str(args.get("metric") or "contract_count").lower()
    sort_direction = _chart_sort_direction(args.get("sort_direction"))
    property_usage = _optional_chart_filter(args.get("property_usage")) or "Residential"
    property_type = _rental_property_type(args.get("property_type"))
    area_inputs = _chart_area_inputs(args)
    scopes = await _resolve_transaction_scopes(area_inputs) if len(area_inputs) > 1 else []
    is_multi_area = len(scopes) > 1
    scope = (
        {"label": None, "area": None, "master_project": None}
        if is_multi_area
        else await _resolve_transaction_scope(area_inputs[0] if area_inputs else None)
    )
    area = scope["area"]
    master_project = scope["master_project"]
    scope_label = scope["label"]

    group_exprs = {
        "time": None,
        "area": "location_name",
        "property_type": "property_type",
        "project": "project_name_en",
        "master_project": "master_project_en",
        "tenant_type": "tenant_type_en",
    }
    if group_by not in group_exprs:
        group_by = "area"

    metric_exprs = {
        "contract_count": "count()",
        "total_annual_value": "round(sum(annual_amount), 0)",
        "avg_rent": "round(avg(annual_amount), 0)",
        "median_rent": "round(median(annual_amount), 0)",
        "median_rent_sqm": "round(median(annual_amount / nullIf(size_sqm, 0)), 0)",
        "median_rent_sqft": f"round(median(annual_amount / nullIf(size_sqm, 0)) / {SQM_TO_SQFT}, 0)",
    }
    if metric not in metric_exprs:
        metric = "contract_count"
    min_records = _chart_min_records(args, group_by=group_by, metric=metric)

    if is_multi_area:
        end_date = (
            await _latest_rental_contract_date_for_scopes(
                property_usage=property_usage,
                property_type=property_type,
                scopes=scopes,
            )
            or date.today()
        )
    else:
        end_date = (
            await _latest_rental_contract_date(
                property_usage=property_usage,
                property_type=property_type,
                area=area,
                master_project=master_project,
            )
            or date.today()
        )
    start_date = end_date - timedelta(days=days)
    interval = _trend_interval(days)
    params: dict[str, Any] = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "limit": limit,
        "series_limit": limit * max(len(scopes), 1),
        "min_records": min_records,
    }
    scope_filter = None
    scope_label_expr = None
    if is_multi_area:
        scope_filter, scope_label_expr = _scope_filter_and_label_expr(scopes, params, "scope")

    if group_by == "time":
        if interval == "day":
            label_expr = "contract_start_date"
        elif interval == "week":
            label_expr = "toStartOfWeek(contract_start_date)"
        else:
            label_expr = "toStartOfMonth(contract_start_date)"
    elif is_multi_area and group_by == "area" and scope_label_expr:
        label_expr = scope_label_expr
    else:
        label_expr = group_exprs[group_by]

    where = [
        "annual_amount > 0",
        "contract_start_date <= today()",
        "contract_start_date BETWEEN {start_date:Date} AND {end_date:Date}",
    ]
    if property_usage:
        where.append("property_usage_en = {property_usage:String}")
        params["property_usage"] = property_usage
    if property_type:
        where.append("property_type = {property_type:String}")
        params["property_type"] = property_type
    if is_multi_area and scope_filter:
        where.append(scope_filter)
    if not is_multi_area and area:
        where.append("location_name = {area_name:String}")
        params["area_name"] = area
    if not is_multi_area and master_project:
        where.append("master_project_en = {master_project:String}")
        params["master_project"] = master_project
    if group_by != "time" and label_expr:
        where.append(f"{label_expr} != ''")

    if is_multi_area and group_by != "area" and scope_label_expr:
        order_limit = (
            "ORDER BY series_label ASC, label ASC"
            if group_by == "time"
            else _chart_order_clause(sort_direction, limit_key="series_limit")
        )
        rows = await ch_query(
            f"""
            SELECT
                {scope_label_expr} AS series_label,
                {label_expr} AS label,
                {metric_exprs[metric]} AS value,
                count() AS record_count
            FROM ch_rent_contracts
            WHERE {" AND ".join(where)}
            GROUP BY series_label, label
            HAVING value > 0 AND record_count >= {{min_records:Int32}}
            {order_limit}
            """,
            params,
        )
        series = True
    else:
        order_limit = (
            "ORDER BY label ASC"
            if group_by == "time"
            else _chart_order_clause(sort_direction, limit_key="limit")
        )
        rows = await ch_query(
            f"""
            SELECT
                {label_expr} AS label,
                {metric_exprs[metric]} AS value,
                count() AS record_count
            FROM ch_rent_contracts
            WHERE {" AND ".join(where)}
            GROUP BY label
            HAVING value > 0 AND record_count >= {{min_records:Int32}}
            {order_limit}
            """,
            params,
        )
        series = False
    return (
        rows,
        series,
        group_by,
        metric,
        sort_direction,
        scopes,
        is_multi_area,
        scope_label,
        area,
        master_project,
        property_usage,
        property_type,
        days,
        interval,
        limit,
        min_records,
        start_date,
        end_date,
    )


async def fetch_project_chart_rows(args: dict[str, Any]):
    limit = clamp_int(args.get("limit"), default=8, min_value=1, max_value=20)
    group_by = str(args.get("group_by") or "area").lower()
    metric = str(args.get("metric") or "pipeline_units").lower()
    sort_direction = _chart_sort_direction(args.get("sort_direction"))
    area_inputs = _chart_area_inputs(args)
    areas: list[str] = []
    seen_areas: set[str] = set()
    for area_input in area_inputs:
        resolved_area = await _resolve_fact_area(area_input)
        key = (resolved_area or "").lower()
        if resolved_area and key not in seen_areas:
            areas.append(resolved_area)
            seen_areas.add(key)
    is_multi_area = len(areas) > 1
    area = None if is_multi_area else (areas[0] if areas else None)

    group_exprs = {
        "area": "area_name_en",
        "developer": "developer_name",
        "completion_status": "completion_status",
        "pipeline_status": "pipeline_status",
        "master_project": "master_project_en",
    }
    if group_by not in group_exprs:
        group_by = "area"

    metric_exprs = {
        "project_count": "count()",
        "active_projects": "countIf(completion_status != 'FINISHED')",
        "completed_projects": "countIf(completion_status = 'FINISHED')",
        "pipeline_units": "sum(no_of_units)",
        "units_delivering_next_12_months": (
            "sumIf(no_of_units, pipeline_status = 'delivering_next_12_months' "
            "AND completion_status != 'FINISHED')"
        ),
        "sales_transaction_count_12m": "sum(sales_transaction_count_12m)",
        "rental_contract_count_12m": "sum(rental_contract_count_12m)",
        "total_sales_volume_12m": "round(sum(total_sales_volume_12m), 0)",
        "gross_yield_pct": "round(avg(gross_yield_pct), 2)",
        "avg_completion_pct": "round(avg(percent_completed), 1)",
    }
    if metric not in metric_exprs:
        metric = "pipeline_units"
    min_records = _chart_min_records(args, group_by=group_by, metric=metric)

    label_expr = group_exprs[group_by]
    where = [f"{label_expr} != ''"]
    params: dict[str, Any] = {
        "limit": limit,
        "series_limit": limit * max(len(areas), 1),
        "min_records": min_records,
    }
    if is_multi_area:
        where.append("area_name_en IN {areas:Array(String)}")
        params["areas"] = areas
    elif area:
        where.append("area_name_en = {area_name:String}")
        params["area_name"] = area

    if is_multi_area and group_by != "area":
        rows = await ch_query(
            f"""
            SELECT
                area_name_en AS series_label,
                {label_expr} AS label,
                {metric_exprs[metric]} AS value,
                count() AS record_count
            FROM ch_project_fact
            WHERE {" AND ".join(where)}
            GROUP BY series_label, label
            HAVING value > 0 AND record_count >= {{min_records:Int32}}
            {_chart_order_clause(sort_direction, limit_key="series_limit")}
            """,
            params,
        )
        series = True
    else:
        rows = await ch_query(
            f"""
            SELECT
                {label_expr} AS label,
                {metric_exprs[metric]} AS value,
                count() AS record_count
            FROM ch_project_fact
            WHERE {" AND ".join(where)}
            GROUP BY label
            HAVING value > 0 AND record_count >= {{min_records:Int32}}
            {_chart_order_clause(sort_direction, limit_key="limit")}
            """,
            params,
        )
        series = False
    return (
        rows,
        series,
        group_by,
        metric,
        sort_direction,
        areas,
        is_multi_area,
        area,
        limit,
        min_records,
    )


async def fetch_area_yield_chart_rows(args: dict[str, Any]):
    days = _chart_days(args.get("days"))
    limit = clamp_int(args.get("limit"), default=8, min_value=1, max_value=20)
    sort_direction = _chart_sort_direction(args.get("sort_direction"))
    min_records = clamp_int(args.get("min_records"), default=250, min_value=1, max_value=10_000)
    property_usage = _optional_chart_filter(args.get("property_usage")) or "Residential"
    raw_property_type = clean_text(args.get("property_type")) or ""
    if raw_property_type.lower() in {"all", "any", "none"}:
        yield_property_type = None
    else:
        yield_property_type = raw_property_type or "Flat"
    sales_property_type = _sales_property_type(yield_property_type)
    rental_property_type = _rental_property_type(yield_property_type)

    latest_sales_date = await _latest_transaction_date(
        property_usage=property_usage,
        property_type=sales_property_type,
        area=None,
    )
    latest_rental_date = await _latest_rental_contract_date(
        property_usage=property_usage,
        property_type=rental_property_type,
        area=None,
    )
    latest_dates = [value for value in [latest_sales_date, latest_rental_date] if value]
    end_date = min(latest_dates) if latest_dates else date.today()
    start_date = end_date - timedelta(days=days)

    sales_where = [
        "trans_group_en = 'Sales'",
        "actual_worth > 0",
        "location_name != ''",
        "lower(location_name) != 'unknown'",
        "instance_date BETWEEN {start_date:Date} AND {end_date:Date}",
    ]
    rental_where = [
        "annual_amount > 0",
        "location_name != ''",
        "lower(location_name) != 'unknown'",
        "contract_start_date <= today()",
        "contract_start_date BETWEEN {start_date:Date} AND {end_date:Date}",
    ]
    params: dict[str, Any] = {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "limit": limit,
        "min_records": min_records,
    }
    if property_usage:
        sales_where.append("property_usage_en = {property_usage:String}")
        rental_where.append("property_usage_en = {property_usage:String}")
        params["property_usage"] = property_usage
    if sales_property_type:
        sales_where.append("property_type = {sales_property_type:String}")
        params["sales_property_type"] = sales_property_type
    if rental_property_type:
        rental_where.append("property_type = {rental_property_type:String}")
        params["rental_property_type"] = rental_property_type

    rows = await ch_query(
        f"""
        WITH
            sales AS (
                SELECT
                    location_name AS area,
                    median(actual_worth) AS median_sale_price,
                    count() AS sales_count
                FROM ch_transactions
                WHERE {" AND ".join(sales_where)}
                GROUP BY area
                HAVING sales_count >= {{min_records:Int32}}
            ),
            rentals AS (
                SELECT
                    location_name AS area,
                    median(annual_amount) AS median_annual_rent,
                    count() AS rental_count
                FROM ch_rent_contracts
                WHERE {" AND ".join(rental_where)}
                GROUP BY area
                HAVING rental_count >= {{min_records:Int32}}
            )
        SELECT
            s.area AS label,
            round(r.median_annual_rent / nullIf(s.median_sale_price, 0) * 100, 2) AS value,
            least(s.sales_count, r.rental_count) AS record_count
        FROM sales AS s
        INNER JOIN rentals AS r ON s.area = r.area
        WHERE value BETWEEN 1 AND 25
        {_chart_order_clause(sort_direction, limit_key="limit")}
        """,
        params,
    )
    return (
        rows,
        days,
        limit,
        sort_direction,
        min_records,
        property_usage,
        yield_property_type,
        start_date,
        end_date,
    )
