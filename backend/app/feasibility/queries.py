import logging
from typing import Any

from app.clickhouse.client import query
from app.feasibility.normalization import (
    Stage,
)
from app.plots.models import Usage

logger = logging.getLogger(__name__)


def transaction_usage_clause(usage: Usage, column: str = "property_usage_en") -> str:
    if usage == "residential":
        return f"({column} IN ('Residential', 'Hospitality ', 'Hospitality'))"
    if usage == "commercial":
        return f"({column} IN ('Commercial', 'Industrial', 'Storage'))"
    if usage == "mixed_use":
        return f"({column} IN ('Residential / Commercial', 'Multi-Use', 'Industrial / Commercial', 'Industrial / Commercial / Residential'))"
    return "1 = 1"


def transaction_stage_clause(stage: Stage, column: str = "reg_type_en") -> str:
    if stage == "existing":
        return f"{column} = 'Existing Properties'"
    if stage == "off_plan":
        return f"{column} = 'Off-Plan Properties'"
    return "1 = 1"


def canonical_unit_type_case_sql(column: str = "rooms_en") -> str:
    return f"""
    multiIf(
        match(lowerUTF8(trimBoth({column})), '^studio$'), 'studio',
        match(lowerUTF8(trimBoth({column})), '^(1|one)\\s*(b/r|br|bed(room)?s?\\+?hall?)$'), '1br',
        match(lowerUTF8(trimBoth({column})), '^(2|two)\\s*(b/r|br|bed(room)?s?\\+?hall?)$'), '2br',
        match(lowerUTF8(trimBoth({column})), '^(3|three)\\s*(b/r|br|bed(room)?s?\\+?hall?)$'), '3br',
        match(lowerUTF8(trimBoth({column})), '^(4|four)\\s*(b/r|br|bed(room)?s?\\+?hall?)$'), '4br',
        match(lowerUTF8(trimBoth({column})), '^(5|6|7|8|9|10|\\d+)\\s*(b/r|br|bed(room)?s?\\+?hall?)$'), '4br+',
        match(lowerUTF8(trimBoth({column})), 'penthouse'), '4br+',
        match(lowerUTF8(trimBoth({column})), 'villa'), 'villa',
        match(lowerUTF8(trimBoth({column})), 'townhouse'), 'townhouse',
        match(lowerUTF8(trimBoth({column})), 'office'), 'office',
        match(lowerUTF8(trimBoth({column})), 'shop|retail|store|showroom|kiosk|restaurant'), 'retail',
        match(lowerUTF8(trimBoth({column})), 'warehouse|workshop|industrial|factory|labor camp|staff accommod'), 'industrial',
        match(lowerUTF8(trimBoth({column})), 'hotel'), 'hospitality',
        'other'
    )
    """


async def fetch_market_pricing(area_name, days, stage, usage):
    canonical_unit_sql = canonical_unit_type_case_sql("rooms_en")
    where = [
        "trans_group_en = 'Sales'",
        "meter_sale_price > 0",
        "lower(area_name_en) = lower({area_name:String})",
        transaction_usage_clause(usage),
        transaction_stage_clause(stage),
        "instance_date >= today() - {days:Int32}",
    ]
    sql = f"""
    SELECT
        {canonical_unit_sql} AS unit_type,
        count() AS transaction_count,
        round(median(meter_sale_price), 0) AS median_price_sqm,
        round(avg(procedure_area), 0) AS avg_area_sqm
    FROM ch_transactions
    WHERE {" AND ".join(where)}
    GROUP BY unit_type
    HAVING unit_type != 'other'
    ORDER BY transaction_count DESC
    """
    rows = await query(sql, {"area_name": area_name, "days": days})
    return rows


async def fetch_off_plan_pricing_curve(area_name, days, target_horizon_months, usage):
    canonical_unit_sql = canonical_unit_type_case_sql("t.rooms_en")
    sql = f"""
    SELECT
        {canonical_unit_sql} AS unit_type,
        multiIf(
            months_to_completion <= 12, '0-12m',
            months_to_completion <= 24, '13-24m',
            months_to_completion <= 36, '25-36m',
            '37m+'
        ) AS horizon_bucket,
        round(avg(months_to_completion), 1) AS avg_months_to_completion,
        count() AS transaction_count,
        round(median(t.meter_sale_price), 0) AS median_price_sqm,
        round(avg(t.procedure_area), 0) AS avg_area_sqm,
        toString(max(toDate(t.instance_date))) AS last_transaction_date
    FROM (
        SELECT
            t.*,
            greatest(
                dateDiff('month', toDate(t.instance_date), toDate(p.completion_date)),
                0
            ) AS months_to_completion
        FROM ch_transactions t
        INNER JOIN ch_projects p
            ON lowerUTF8(trimBoth(p.project_name_en)) = lowerUTF8(trimBoth(t.project_name_en))
        WHERE t.trans_group_en = 'Sales'
          AND t.meter_sale_price > 0
          AND lower(t.area_name_en) = lower({{area_name:String}})
          AND {transaction_usage_clause(usage, "t.property_usage_en")}
          AND {transaction_stage_clause("off_plan", "t.reg_type_en")}
          AND t.instance_date >= today() - {{days:Int32}}
          AND p.completion_date IS NOT NULL
    ) t
    GROUP BY unit_type, horizon_bucket
    HAVING unit_type != 'other'
    ORDER BY
        abs(avg_months_to_completion - {{target_horizon_months:Int32}}) ASC,
        transaction_count DESC
    """
    rows = await query(
        sql,
        {
            "area_name": area_name,
            "days": days,
            "target_horizon_months": target_horizon_months,
        },
    )
    return rows


async def fetch_areas(parameters):
    return await query(
        """
    SELECT area_name_en
    FROM ch_transactions
    WHERE trans_group_en = 'Sales'
      AND area_name_en != ''
    GROUP BY area_name_en
    ORDER BY count() DESC, area_name_en
    """,
        parameters,
    )


async def fetch_market_area(parameters):
    return await query(
        """
    SELECT area_name_en
    FROM ch_transactions
    WHERE trans_group_en = 'Sales'
      AND area_name_en ILIKE {pattern:String}
    GROUP BY area_name_en
    ORDER BY (lower(area_name_en) = lower({name:String})) DESC, count() DESC
    LIMIT 1
    """,
        parameters,
    )


async def fetch_rental_size_band(parameters):
    return await query(
        """
        SELECT
            median(toFloat64(annual_amount) / greatest(toFloat64(actual_area), 1)) AS median_rent_psm_annual,
            median(toFloat64(annual_amount)) AS median_annual_rent,
            count() AS contract_count
        FROM ch_rent_contracts
        WHERE lower(area_name_en) = lower({area_name:String})
          AND actual_area > {lo:Float64}
          AND actual_area <= {hi:Float64}
          AND annual_amount > 0
          AND contract_start_date >= today() - {days:Int32}
        """,
        parameters,
    )


async def fetch_project_unit_mix(parameters):
    return await query(
        f"""
    SELECT
        project_id,
        {canonical_unit_type_case_sql("rooms_en")} AS unit_type,
        count() AS cnt,
        round(avg(actual_area), 1) AS avg_area_sqm
    FROM ch_units
    WHERE project_id IN ({{project_ids:Array(Int32)}})
      AND actual_area > 0
    GROUP BY project_id, unit_type
    HAVING unit_type != 'other'
    ORDER BY project_id, cnt DESC
    """,
        parameters,
    )


async def fetch_comparable_projects(parameters):
    return await query(
        """
    WITH land AS (
        SELECT
            p.project_id,
            sum(toFloat64(t.procedure_area)) AS total_land_area_sqm
        FROM ch_transactions t
        INNER JOIN ch_projects p ON p.project_number = t.project_number
        WHERE t.property_type_en = 'Land'
          AND t.trans_group_en = 'Sales'
          AND t.procedure_area > 0
          AND t.project_number != ''
        GROUP BY p.project_id
    )
    SELECT
        p.project_id,
        any(p.project_name_en) AS project_name,
        any(p.area_name_en) AS area_name,
        any(p.project_status) AS project_status,
        any(p.no_of_buildings) AS no_of_buildings,
        any(p.no_of_units) AS no_of_units,
        any(land.total_land_area_sqm) AS total_land_area_sqm
    FROM ch_projects p
    LEFT JOIN land ON p.project_id = land.project_id
    WHERE p.area_name_en = {market_area:String}
      AND p.project_name_en != ''
    GROUP BY p.project_id
    LIMIT 250
    """,
        parameters,
    )


async def fetch_comparable_prices(parameters, usage):
    return await query(
        f"""
        SELECT
            p.project_id AS project_id,
            round(median(t.meter_sale_price), 0) AS median_price_sqm,
            argMax(unit_type, cnt) AS dominant_unit_type
        FROM (
            SELECT
                project_name_en,
                {canonical_unit_type_case_sql("rooms_en")} AS unit_type,
                count() AS cnt,
                meter_sale_price
            FROM ch_transactions
            WHERE trans_group_en = 'Sales'
              AND area_name_en = {{market_area:String}}
              AND {transaction_usage_clause(usage)}
              AND {transaction_stage_clause("existing")}
              AND meter_sale_price > 0
            GROUP BY project_name_en, unit_type, meter_sale_price
        ) t
        INNER JOIN ch_projects p ON p.project_name_en = t.project_name_en
        GROUP BY p.project_id
        """,
        parameters,
    )


async def fetch_comparable_buildings(parameters):
    return await query(
        """
    SELECT
        count() AS no_of_buildings,
        avg(floors) AS avg_floors
    FROM ch_buildings
    WHERE project_id IN ({project_ids:Array(Int32)})
      AND floors > 0
    """,
        parameters,
    )


async def fetch_comparable_names(parameters):
    return await query(
        """
    SELECT project_id, project_name_en, area_name_en
    FROM ch_projects
    WHERE project_id IN ({project_ids:Array(Int32)})
    """,
        parameters,
    )


async def fetch_comparable_units(parameters):
    return await query(
        f"""
    SELECT
        {canonical_unit_type_case_sql("rooms_en")} AS unit_type,
        count() AS cnt,
        round(avg(actual_area), 2) AS avg_size_sqm
    FROM ch_units
    WHERE project_id IN ({{project_ids:Array(Int32)}})
      AND actual_area > 0
    GROUP BY unit_type
    HAVING unit_type != 'other'
    ORDER BY cnt DESC
    """,
        parameters,
    )


async def fetch_comparable_unit_prices(parameters):
    return await query(
        f"""
    SELECT
        {canonical_unit_type_case_sql("rooms_en")} AS unit_type,
        round(median(meter_sale_price), 0) AS median_price_sqm
    FROM ch_transactions
    WHERE project_name_en IN ({{project_names:Array(String)}})
      AND meter_sale_price > 0
      AND instance_date >= today() - 730
    GROUP BY unit_type
    HAVING unit_type != 'other'
    """,
        parameters,
    )


async def fetch_chat_transactions(area, days, limit, stage, unit_type, usage):
    where = [
        "trans_group_en = 'Sales'",
        "meter_sale_price > 0",
        transaction_usage_clause(usage),
        transaction_stage_clause(stage),
    ]
    params: dict[str, Any] = {"limit": limit}
    if area:
        where.append("area_name_en = {area:String}")
        params["area"] = area
    if unit_type:
        where.append("rooms_en = {unit_type:String}")
        params["unit_type"] = unit_type
    if days:
        where.append("instance_date >= today() - {days:Int32}")
        params["days"] = days
    sql = f"""
    SELECT transaction_id, instance_date, project_name_en, rooms_en,
           procedure_area, actual_worth, meter_sale_price, reg_type_en
    FROM ch_transactions
    WHERE {" AND ".join(where)}
    ORDER BY instance_date DESC
    LIMIT {{limit:Int32}}
    """
    rows = await query(sql, params)
    return rows


async def fetch_chat_analytics(area, days, project_name, stage, usage):
    where = [
        "meter_sale_price > 0",
        "trans_group_en = 'Sales'",
        transaction_usage_clause(usage),
        transaction_stage_clause(stage),
    ]
    params: dict[str, Any] = {}
    if project_name:
        where.append("project_name_en = {project_name:String}")
        params["project_name"] = project_name
    elif area:
        where.append("area_name_en = {area:String}")
        params["area"] = area
    if days:
        where.append("instance_date >= today() - {days:Int32}")
        params["days"] = days
    where_clause = " AND ".join(where)
    trend_sql = f"""
    SELECT toStartOfMonth(instance_date) AS month,
           round(median(meter_sale_price), 0) AS median_price_sqm,
           count() AS txn_count
    FROM ch_transactions
    WHERE {where_clause}
    GROUP BY month ORDER BY month
    """
    rooms_sql = f"""
    SELECT rooms_en, count() AS txn_count,
           round(median(meter_sale_price), 0) AS median_price_sqm,
           round(avg(procedure_area), 0) AS avg_area_sqm
    FROM ch_transactions
    WHERE {where_clause}
    GROUP BY rooms_en ORDER BY txn_count DESC
    """
    trend_rows = await query(trend_sql, params)
    rooms_rows = await query(rooms_sql, params)
    return trend_rows, rooms_rows


async def fetch_chat_area(parameters):
    return await query(
        """
    SELECT area_name_en
    FROM ch_transactions
    WHERE area_name_en ILIKE {pattern:String}
      AND trans_group_en = 'Sales' AND meter_sale_price > 0
    GROUP BY area_name_en
    ORDER BY (area_name_en = {area:String}) DESC, count() DESC
    LIMIT 1
    """,
        parameters,
    )


async def fetch_tool_market_area(parameters):
    return await query(
        """
    SELECT area_name_en
    FROM ch_transactions
    WHERE trans_group_en = 'Sales'
      AND area_name_en ILIKE {pattern:String}
    GROUP BY area_name_en
    ORDER BY (lower(area_name_en) = lower({area:String})) DESC, count() DESC
    LIMIT 1
    """,
        parameters,
    )
