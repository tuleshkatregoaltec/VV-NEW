"""Business logic for project analytics — merges parallel ClickHouse results."""

import asyncio
import logging
from datetime import date, timedelta
from typing import Any, Optional

from app.analytics import queries
from app.analytics.metrics import (
    _add_months,
    _coalesce_delivery_date,
    _normalize_label,
    _positive_float,
    _quarter_start,
    _room_sort_key,
    _trend_interval_for_days,
    _unique_normalized_labels,
    build_configuration_rows,
)
from app.analytics.models import (
    AnalyticsResponse,
    AnalyticsSummary,
    AnalyticsUnitComposition,
    MarketTrendPoint,
    MarketTrendResponse,
    PriceDistributionBucket,
    ProjectAnalyticsResponse,
    ProjectForecastPoint,
    ProjectForecastResponse,
    ProjectMeta,
    ProjectPipelinePoint,
    ProjectPipelineResponse,
    ProjectPriceDistributionResponse,
    ProjectRentalSummary,
    ProjectSalesSummary,
    ProjectSearchItem,
    ProjectSearchResponse,
    ProjectTrendPoint,
    ProjectTrendsResponse,
    PropertyTypeBreakdown,
    RegTypeBreakdown,
    RentalAnalyticsResponse,
    RentalSummary,
    RoomAnalytics,
    SubProjectItem,
    SubProjectsResponse,
)
from app.analytics.queries import MARKET_FILTERS, ProjectFilterType, ProjectScopeType
from app.core.observability import operation

# Room sort order: Studio first, then numeric bedrooms, then anything else


logger = logging.getLogger(__name__)


async def _latest_sales_anchor_date() -> date:
    rows = await queries.fetch_latest_sales_anchor_date_rows({})
    if not rows:
        return date.today()

    value = rows[0].get("latest_sales_date")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return date.today()
    return date.today()


async def get_project_search(search: str, limit: int = 50) -> ProjectSearchResponse:
    """Return matching projects for the dropdown selector."""
    pattern = f"%{search.lower()}%" if search.strip() else "%"
    rows = await queries.fetch_search_projects({"search_pattern": pattern, "limit": limit})
    return ProjectSearchResponse(
        projects=[ProjectSearchItem(name=r["name"], filter_type=r["filter_type"]) for r in rows]
    )


async def get_sub_project_list(
    master_project: str, filter_type: ProjectFilterType = "master"
) -> SubProjectsResponse:
    """Return sub-projects for a given master/virtual_master, sorted by transaction volume."""
    param = master_project + " - %" if filter_type == "virtual_master" else master_project
    rows = await queries.fetch_search_sub_projects({"master_project": param}, filter_type)
    return SubProjectsResponse(
        sub_projects=[
            SubProjectItem(name=r["project_name_en"], transaction_count=int(r["transaction_count"]))
            for r in rows
        ]
    )


async def _get_market_summary(project_name: str) -> ProjectAnalyticsResponse:
    """Return a quick citywide summary from the local materialized fact table.

    The detailed analytics queries intentionally retain their raw, unit-level
    paths for a selected project.  Running all of those citywide at once is
    prohibitively expensive on a local ClickHouse instance, so the landing
    scope uses the already materialized 12-month project aggregates instead.
    """
    rows = await queries.fetch_get_market_summary_rows({})
    row = rows[0] if rows else {}
    sales_count = int(row.get("sales_count") or 0)
    rental_count = int(row.get("rental_count") or 0)
    median_sale_price = float(row.get("median_sale_price") or 0)
    median_annual_rent = float(row.get("median_annual_rent") or 0)
    yield_pct = _positive_float(row.get("gross_yield_pct"))
    if yield_pct is not None:
        yield_pct = round(yield_pct, 2)

    return ProjectAnalyticsResponse(
        project=ProjectMeta(
            project_name=project_name,
            area_name="Dubai",
            developer_name="All developers",
            no_of_units=int(row.get("unit_count") or 0) or None,
        ),
        sales=ProjectSalesSummary(
            transaction_count=sales_count,
            total_volume=float(row.get("sales_volume") or 0),
            avg_price=float(row.get("avg_sale_price") or 0),
            avg_price_sqm=float(row.get("avg_sale_price_sqm") or 0),
            median_price=median_sale_price,
        ),
        rentals=ProjectRentalSummary(
            contract_count=rental_count,
            total_annual_value=float(row.get("rental_value") or 0),
            median_annual_rent=median_annual_rent,
            median_rent_sqm=float(row.get("median_rent_sqm") or 0),
        ),
        overall_gross_yield_pct=yield_pct,
        by_rooms=[],
        by_property_type=[],
        by_reg_type=[],
        unit_composition=[],
        configuration_analysis=[],
    )


async def get_project_summary(
    project_name: str,
    filter_type: ProjectScopeType = "project",
    master_project: Optional[str] = None,
) -> ProjectAnalyticsResponse:
    """
    Run ClickHouse queries in parallel and merge into a single response.
    filter_type controls the WHERE clause: 'master', 'virtual_master', 'project', or 'market'.
    For virtual_master, project_name is used as a LIKE prefix (suffix ' - %' appended here).
    """
    if filter_type == "market":
        return await _get_market_summary(project_name)

    # For LIKE queries the param needs the wildcard appended
    like_name = project_name + " - %" if filter_type == "virtual_master" else project_name
    params = {} if filter_type == "market" else {"project_name": like_name}

    # Unit composition: master/project use master_project_en equality;
    # virtual_master uses project_name_en LIKE (ch_units also lacks master_project_en for orphans)
    if filter_type == "market":
        units_params = {}
    elif filter_type == "virtual_master":
        units_params = {"project_name": like_name}
    elif filter_type == "master":
        units_params = {"project_name": project_name}
    else:
        units_params = {"project_name": master_project or project_name}

    if filter_type == "market":
        meta_task = asyncio.sleep(0, result=[])
    else:
        # meta for master/virtual_master uses the like_name for virtual (LIKE) or plain name
        meta_params = (
            {"project_name": like_name}
            if filter_type == "virtual_master"
            else {"project_name": project_name}
        )
        meta_task = queries.fetch_project_meta(meta_params, filter_type)

    if filter_type in ("master", "virtual_master"):
        developer_task = queries.fetch_project_developers_by_scope(meta_params, filter_type)
    else:
        developer_task = asyncio.sleep(0, result=[])

    existing_params = {**params, "reg_type_en": "Existing Properties"}
    offplan_params = {**params, "reg_type_en": "Off-Plan Properties"}

    # project_rentals_by_rooms uses {project_name} for both rent and units filters
    rent_rooms_params = params

    (
        sales_sum_rows,
        sales_sum_existing_rows,
        sales_rooms_rows,
        sales_rooms_existing_rows,
        config_all_rows,
        config_offplan_rows,
        config_secondary_rows,
        sales_type_rows,
        sales_reg_rows,
        rent_sum_rows,
        rent_rooms_rows,
        units_rows,
        meta_rows,
        developer_rows,
    ) = await asyncio.gather(
        queries.fetch_project_sales_summary(params, filter_type),
        queries.fetch_project_sales_summary(
            existing_params, filter_type, reg_type="Existing Properties"
        ),
        queries.fetch_project_sales_by_rooms(params, filter_type),
        queries.fetch_project_sales_by_rooms(
            existing_params, filter_type, reg_type="Existing Properties"
        ),
        queries.fetch_project_configuration_sales(params, filter_type),
        queries.fetch_project_configuration_sales(
            offplan_params, filter_type, reg_type="Off-Plan Properties"
        ),
        queries.fetch_project_configuration_sales(
            existing_params, filter_type, reg_type="Existing Properties"
        ),
        queries.fetch_project_sales_by_property_type(params, filter_type),
        queries.fetch_project_sales_by_reg_type(params, filter_type),
        queries.fetch_project_rentals_summary(params, filter_type),
        queries.fetch_project_rentals_by_rooms(rent_rooms_params, filter_type),
        queries.fetch_project_units_composition(units_params, filter_type),
        meta_task,
        developer_task,
    )

    # --- Project metadata ---
    project: Optional[ProjectMeta] = None
    if filter_type == "market":
        project = ProjectMeta(
            project_name=project_name,
            area_name="Dubai",
            developer_name="All developers",
        )
    elif meta_rows:
        m = meta_rows[0]
        if filter_type in ("master", "virtual_master"):
            raw_devs = m.get("developer_names") or []
            developer_names = _unique_normalized_labels(
                [row.get("developer_name_en") for row in developer_rows]
            )
            if not developer_names:
                developer_names = _unique_normalized_labels(raw_devs) if raw_devs else []
            top_developer = _normalize_label(m.get("developer_name_en"))
            if not developer_names and top_developer:
                developer_names = [top_developer, *developer_names]
            project = ProjectMeta(
                project_name=project_name,
                area_name=m.get("area_name_en") or None,
                developer_name=developer_names[0] if developer_names else top_developer,
                developer_names=developer_names,
                project_status=m.get("project_status") or None,
                completion_date=m.get("completion_date") or None,
                percent_completed=float(m["percent_completed"])
                if m.get("percent_completed") is not None
                else None,
                no_of_units=int(m["no_of_units"]) if m.get("no_of_units") is not None else None,
                no_of_buildings=int(m["no_of_buildings"])
                if m.get("no_of_buildings") is not None
                else None,
            )
        else:
            project = ProjectMeta(
                project_name=m.get("project_name_en", project_name),
                area_name=m.get("area_name_en") or None,
                developer_name=_normalize_label(m.get("developer_name_en")),
                project_status=m.get("project_status") or None,
                completion_date=m.get("completion_date") or None,
                percent_completed=float(m["percent_completed"])
                if m.get("percent_completed") is not None
                else None,
                no_of_units=int(m["no_of_units"]) if m.get("no_of_units") is not None else None,
                no_of_buildings=int(m["no_of_buildings"])
                if m.get("no_of_buildings") is not None
                else None,
            )

    # --- Sales summary ---
    s = sales_sum_rows[0] if sales_sum_rows else {}
    sales = ProjectSalesSummary(
        transaction_count=int(s.get("transaction_count", 0)),
        total_volume=float(s.get("total_volume", 0) or 0),
        avg_price=float(s.get("avg_price", 0) or 0),
        avg_price_sqm=float(s.get("avg_price_sqm", 0) or 0),
        median_price=float(s.get("median_price", 0) or 0),
    )

    # --- Rental summary ---
    r = rent_sum_rows[0] if rent_sum_rows else {}
    rentals = ProjectRentalSummary(
        contract_count=int(r.get("contract_count", 0)),
        total_annual_value=float(r.get("total_annual_value", 0) or 0),
        median_annual_rent=float(r.get("median_annual_rent", 0) or 0),
        median_rent_sqm=float(r.get("median_rent_sqm", 0) or 0),
    )

    # --- Overall gross yield ---
    # Use existing-only median_price as denominator — off-plan launch prices are discounted
    # and would produce artificially inflated yields (e.g. JVC showing ~25%).
    # Median/median: both sides use the same central tendency measure for a consistent yield.
    existing_median_price = float(
        (sales_sum_existing_rows[0].get("median_price") or 0) if sales_sum_existing_rows else 0
    )
    overall_yield: Optional[float] = None
    if existing_median_price > 0 and rentals.median_annual_rent > 0:
        overall_yield = round(rentals.median_annual_rent / existing_median_price * 100, 2)

    # --- By rooms (merge sales + rentals) ---
    sales_by_room = {row["rooms_en"]: row for row in sales_rooms_rows}
    # Existing-only prices used as yield denominator — same rationale as overall_gross_yield_pct
    sales_existing_by_room = {row["rooms_en"]: row for row in sales_rooms_existing_rows}
    rent_by_room = {row["rooms_en"]: row for row in rent_rooms_rows}
    all_rooms = set(sales_by_room) | set(rent_by_room)

    by_rooms: list[RoomAnalytics] = []
    for rooms in all_rooms:
        s_row = sales_by_room.get(rooms, {})
        s_ex_row = sales_existing_by_room.get(rooms, {})
        r_row = rent_by_room.get(rooms, {})
        avg_sale = float(s_row.get("avg_sale_price", 0) or 0)
        existing_median_sale = float(s_ex_row.get("median_sale_price", 0) or 0)
        median_rent = float(r_row.get("median_annual_rent", 0) or 0)
        yield_pct: Optional[float] = None
        if existing_median_sale > 0 and median_rent > 0:
            yield_pct = round(median_rent / existing_median_sale * 100, 2)
        by_rooms.append(
            RoomAnalytics(
                rooms=rooms,
                sale_count=int(s_row.get("sale_count", 0)),
                avg_sale_price=avg_sale,
                avg_sale_price_sqm=float(s_row.get("avg_sale_price_sqm", 0) or 0),
                rental_count=int(r_row.get("rental_count", 0)),
                median_annual_rent=median_rent,
                median_rent_sqm=float(r_row.get("median_rent_sqm", 0) or 0),
                gross_yield_pct=yield_pct,
            )
        )
    by_rooms.sort(key=lambda x: _room_sort_key(x.rooms))

    # --- By property type ---
    total_sales = sum(int(row["count"]) for row in sales_type_rows) or 1
    by_property_type = [
        PropertyTypeBreakdown(
            property_type=row["property_type_en"],
            count=int(row["count"]),
            avg_price=float(row["avg_price"] or 0),
            pct_of_total=round(int(row["count"]) / total_sales * 100, 1),
        )
        for row in sales_type_rows
    ]

    # --- By registration type ---
    total_reg = sum(int(row["count"]) for row in sales_reg_rows) or 1
    by_reg_type = [
        RegTypeBreakdown(
            reg_type=row["reg_type_en"],
            count=int(row["count"]),
            avg_price=float(row["avg_price"] or 0),
            pct_of_total=round(int(row["count"]) / total_reg * 100, 1),
        )
        for row in sales_reg_rows
    ]

    # --- Unit composition ---
    unit_count_total = sum(int(row["unit_count"]) for row in units_rows)
    if filter_type == "market" and project is not None:
        project.no_of_units = unit_count_total if unit_count_total > 0 else None

    total_units = unit_count_total or 1
    unit_composition = [
        AnalyticsUnitComposition(
            rooms=room,
            count=int(row["unit_count"]),
            pct_of_total=round(int(row["unit_count"]) / total_units * 100, 1),
            avg_unit_size_sqm=float(row.get("avg_unit_size_sqm", 0) or 0),
        )
        for row in units_rows
        if (room := _normalize_label(row.get("rooms_en"))) is not None
    ]
    unit_composition.sort(key=lambda x: _room_sort_key(x.rooms))

    unit_rows_by_room = {
        room: row
        for row in units_rows
        if (room := _normalize_label(row.get("rooms_en"))) is not None
    }

    configuration_analysis = [
        *build_configuration_rows("all", config_all_rows, unit_rows_by_room, rent_by_room),
        *build_configuration_rows("off_plan", config_offplan_rows, unit_rows_by_room, rent_by_room),
        *build_configuration_rows(
            "secondary", config_secondary_rows, unit_rows_by_room, rent_by_room
        ),
    ]

    return ProjectAnalyticsResponse(
        project=project,
        sales=sales,
        rentals=rentals,
        overall_gross_yield_pct=overall_yield,
        by_rooms=by_rooms,
        by_property_type=by_property_type,
        by_reg_type=by_reg_type,
        unit_composition=unit_composition,
        configuration_analysis=configuration_analysis,
    )


async def get_project_trends(
    project_name: str, days: int, filter_type: ProjectScopeType = "project"
) -> ProjectTrendsResponse:
    """Price trend time series for a project over the given number of days."""
    end_dt = date.today()
    start_dt = end_dt - timedelta(days=days)
    interval = _trend_interval_for_days(days)

    like_name = project_name + " - %" if filter_type == "virtual_master" else project_name
    params = {
        "start_date": start_dt.isoformat(),
        "end_date": end_dt.isoformat(),
    }
    if filter_type != "market":
        params["project_name"] = like_name

    rows = await queries.fetch_project_price_trends(params, interval, filter_type)

    data = [
        ProjectTrendPoint(
            date=row["period"],
            transaction_count=int(row["transaction_count"]),
            avg_price=float(row["avg_price"] or 0),
            avg_price_sqm=float(row["avg_price_sqm"] or 0),
        )
        for row in rows
    ]
    return ProjectTrendsResponse(data=data)


async def get_project_forecast(
    project_name: str, days: int, filter_type: ProjectScopeType = "project"
) -> ProjectForecastResponse:
    """Build a project-level forward price path from historical trend and off-plan curve data."""
    if filter_type == "market":
        return ProjectForecastResponse(
            data=[],
            methodology="Citywide Dubai forecasts are not generated from project delivery curves.",
        )

    end_dt = date.today()
    start_dt = end_dt - timedelta(days=days)
    interval = _trend_interval_for_days(days)

    like_name = project_name + " - %" if filter_type == "virtual_master" else project_name
    params = {
        "project_name": like_name,
        "start_date": start_dt.isoformat(),
        "end_date": end_dt.isoformat(),
    }
    trend_rows, offplan_rows = await asyncio.gather(
        queries.fetch_project_price_trends(params, interval, filter_type),
        queries.fetch_project_offplan_price_curve(params, interval, filter_type),
    )

    historical = [
        ProjectForecastPoint(
            date=row["period"],
            avg_price=float(row["avg_price"] or 0),
            avg_price_sqm=float(row["avg_price_sqm"] or 0),
            series="historical",
        )
        for row in trend_rows
        if float(row.get("avg_price") or 0) > 0
    ]

    summary = await get_project_summary(project_name=project_name, filter_type=filter_type)
    delivery_date = summary.project.completion_date if summary.project else None

    if not historical:
        historical = [
            ProjectForecastPoint(
                date=row["period"],
                avg_price=float(row["avg_price"] or 0),
                avg_price_sqm=float(row["avg_price_sqm"] or 0),
                series="historical",
            )
            for row in offplan_rows
            if float(row.get("avg_price") or 0) > 0
        ]

    if not historical:
        return ProjectForecastResponse(
            data=[],
            delivery_date=delivery_date,
            methodology="No residential sales history was available to construct a project-level forecast.",
        )

    monthly_growth_rates: list[float] = []
    for prev, curr in zip(historical, historical[1:]):
        prev_price = float(prev.avg_price_sqm or 0)
        curr_price = float(curr.avg_price_sqm or 0)
        if prev_price > 0 and curr_price > 0:
            monthly_growth_rates.append((curr_price / prev_price) - 1)

    delivery_curve_rates: list[float] = []
    for prev, curr in zip(offplan_rows, offplan_rows[1:]):
        prev_price = float(prev.get("avg_price_sqm") or 0)
        curr_price = float(curr.get("avg_price_sqm") or 0)
        prev_months = float(prev.get("avg_months_to_delivery") or 0)
        curr_months = float(curr.get("avg_months_to_delivery") or 0)
        delivery_step = prev_months - curr_months
        if prev_price > 0 and curr_price > 0 and delivery_step > 0:
            delivery_curve_rates.append(((curr_price / prev_price) - 1) / delivery_step)

    recent_growth = (
        sum(monthly_growth_rates[-3:]) / len(monthly_growth_rates[-3:])
        if monthly_growth_rates[-3:]
        else 0.0
    )
    delivery_curve = (
        sum(delivery_curve_rates[-4:]) / len(delivery_curve_rates[-4:])
        if delivery_curve_rates[-4:]
        else 0.0
    )
    monthly_growth = max(-0.01, min(0.018, recent_growth * 0.7 + delivery_curve * 0.3))

    last_point = historical[-1]
    forecast_points: list[ProjectForecastPoint] = []
    if delivery_date and delivery_date > last_point.date:
        horizon_months = max(
            1,
            min(
                12,
                (delivery_date.year - last_point.date.year) * 12
                + (delivery_date.month - last_point.date.month),
            ),
        )
    else:
        horizon_months = 6

    next_date = _add_months(last_point.date, 1)
    next_price = last_point.avg_price
    next_price_sqm = last_point.avg_price_sqm

    for _ in range(horizon_months):
        next_price *= 1 + monthly_growth
        next_price_sqm *= 1 + monthly_growth
        forecast_points.append(
            ProjectForecastPoint(
                date=next_date,
                avg_price=round(next_price, 2),
                avg_price_sqm=round(next_price_sqm, 2),
                series="forecast",
            )
        )
        next_date = _add_months(next_date, 1)

    return ProjectForecastResponse(
        data=[*historical, *forecast_points],
        delivery_date=delivery_date,
        methodology=(
            "Forecast extends the recorded residential project trend, then blends recent "
            "price/sqm movement with the off-plan delivery curve where delivery-dated launches exist."
        ),
    )


async def get_project_pipeline(
    project_name: str, filter_type: ProjectScopeType = "project"
) -> ProjectPipelineResponse:
    """Area-level delivery pipeline grouped by quarter around the selected project."""
    if filter_type == "market":
        area_name = "Dubai"
        developer_name = None
        rows = await queries.fetch_area_supply_projects({}, filter_by_area=False)
    else:
        summary = await get_project_summary(project_name=project_name, filter_type=filter_type)
        area_name = summary.project.area_name if summary.project else None
        developer_name = summary.project.developer_name if summary.project else None

        if not area_name:
            return ProjectPipelineResponse(area_name=None, developer_name=developer_name, data=[])

        rows = await queries.fetch_area_supply_projects({"area_name": area_name})

    if not rows:
        return ProjectPipelineResponse(area_name=area_name, developer_name=developer_name, data=[])

    today = await _latest_sales_anchor_date()
    start_quarter = _quarter_start(date(today.year - 1, 1, 1))
    periods = [_add_months(start_quarter, quarter_index * 3) for quarter_index in range(16)]

    completed_by_quarter: dict[date, int] = {}
    pipeline_by_quarter: dict[date, int] = {}

    for row in rows:
        units = int(row.get("no_of_units") or 0)
        if units <= 0:
            continue

        completion_dt = _coalesce_delivery_date(row)
        if completion_dt:
            quarter = _quarter_start(completion_dt)
            if completion_dt <= today:
                completed_by_quarter[quarter] = completed_by_quarter.get(quarter, 0) + units
            else:
                pipeline_by_quarter[quarter] = pipeline_by_quarter.get(quarter, 0) + units

    cumulative_existing = sum(units for q, units in completed_by_quarter.items() if q < periods[0])
    cumulative_future = 0

    data: list[ProjectPipelinePoint] = []
    today_quarter = _quarter_start(today)
    for period in periods:
        new_pipeline_units = pipeline_by_quarter.get(period, 0)
        if period <= today_quarter:
            cumulative_existing += completed_by_quarter.get(period, 0)
            data.append(
                ProjectPipelinePoint(
                    period=period,
                    existing_stock_units=cumulative_existing,
                    carried_stock_units=0,
                    new_pipeline_units=new_pipeline_units if period == today_quarter else 0,
                )
            )
            continue

        carried_stock_units = cumulative_existing + cumulative_future
        cumulative_future += new_pipeline_units
        data.append(
            ProjectPipelinePoint(
                period=period,
                existing_stock_units=0,
                carried_stock_units=carried_stock_units,
                new_pipeline_units=new_pipeline_units,
            )
        )

    return ProjectPipelineResponse(
        area_name=area_name,
        developer_name=developer_name,
        data=data,
    )


async def get_project_price_distribution(
    project_name: str,
    filter_type: ProjectScopeType = "project",
    reg_type: Optional[str] = None,
) -> ProjectPriceDistributionResponse:
    """Price distribution statistics per bedroom type for bell-curve style visualisation."""
    like_name = project_name + " - %" if filter_type == "virtual_master" else project_name
    params: dict = {} if filter_type == "market" else {"project_name": like_name}
    if reg_type:
        params["reg_type_en"] = reg_type

    rows = await queries.fetch_project_price_distribution(params, filter_type, reg_type)

    all_bucket: Optional[PriceDistributionBucket] = None
    by_rooms: list[PriceDistributionBucket] = []

    for row in rows:
        bucket = PriceDistributionBucket(
            rooms=row["rooms_en"],
            sale_count=int(row["sale_count"]),
            mean_price=float(row["mean_price"] or 0),
            std_price=float(row["std_price"] or 0),
            log_mean_price=float(row["log_mean_price"] or 0),
            log_std_price=float(row["log_std_price"] or 0),
            p05_price=float(row["p05_price"] or 0),
            p25_price=float(row["p25_price"] or 0),
            p50_price=float(row["p50_price"] or 0),
            p75_price=float(row["p75_price"] or 0),
            p95_price=float(row["p95_price"] or 0),
            mean_price_sqm=float(row["mean_price_sqm"] or 0),
            std_price_sqm=float(row["std_price_sqm"] or 0),
        )
        if row["rooms_en"] == "__all__":
            all_bucket = bucket
        else:
            by_rooms.append(bucket)

    by_rooms.sort(key=lambda x: _room_sort_key(x.rooms))
    return ProjectPriceDistributionResponse(all=all_bucket, by_rooms=by_rooms)


def _resolve_project_scope(
    *,
    master_project: Optional[str],
    project: Optional[str],
    filter_type: ProjectFilterType,
) -> tuple[str, ProjectScopeType]:
    if project:
        return project, "project"
    if master_project:
        return master_project, filter_type
    return "Dubai", "market"


async def get_sales_analytics(
    market_type: Optional[str] = None,
    property_usage: Optional[str] = "Residential",
    timeframe_days: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    master_project: Optional[str] = None,
    property_type: Optional[str] = None,
    rooms: Optional[str] = None,
    reg_type: Optional[str] = None,
    price_bucket: Optional[str] = None,
    area_bucket: Optional[str] = None,
):
    """Get sales analytics with flexible filtering and custom date ranges.

    Supports:
    - Custom date ranges (use start_date/end_date or timeframe_days)
    - market_type for taxonomy-correct filtering (recommended)
    - Manual filtering via property_usage + property_type (legacy)

    Examples:
    - Last 30 days: ?timeframe_days=30
    - By market: ?market_type=residential_units&timeframe_days=90
    - Custom range: ?start_date=2024-01-01&end_date=2024-12-31
    """
    from fastapi import HTTPException

    # Resolve market_type into property_usage / property_type / property_usage_in
    resolved_property_usage = property_usage
    resolved_property_type = property_type
    resolved_property_usage_in = None

    if market_type is not None:
        if market_type not in MARKET_FILTERS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid market_type. Valid values: {list(MARKET_FILTERS)}",
            )
        mf = MARKET_FILTERS[market_type]
        resolved_property_usage = mf.get("property_usage_en")
        resolved_property_usage_in = mf.get("property_usage_en__in")
        resolved_property_type = mf.get("property_type_en", property_type)

    # Calculate date range
    if timeframe_days:
        from datetime import timedelta

        end_date = date.today()
        start_date = end_date - timedelta(days=timeframe_days)
    else:
        if not start_date:
            from datetime import timedelta

            start_date = date.today() - timedelta(days=180)  # Default 6 months
        if not end_date:
            end_date = date.today()

    # Execute ClickHouse query
    try:
        parameters = {
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
        }

        # Add optional parameters
        if resolved_property_usage is not None:
            parameters["property_usage"] = resolved_property_usage
        if master_project:
            parameters["master_project"] = master_project
        if resolved_property_type:
            parameters["property_type"] = resolved_property_type
        if rooms:
            parameters["rooms"] = rooms
        if reg_type:
            parameters["reg_type"] = reg_type
        if price_bucket:
            parameters["price_bucket"] = price_bucket
        if area_bucket:
            parameters["area_bucket"] = area_bucket

        result = await queries.fetch_sales_summary(
            parameters,
            property_usage=resolved_property_usage,
            property_usage_in=resolved_property_usage_in,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            master_project=master_project,
            property_type=resolved_property_type,
            rooms=rooms,
            reg_type=reg_type,
            price_bucket=price_bucket,
            area_bucket=area_bucket,
        )

    except Exception as e:
        # Log error and return empty response instead of crashing
        import logging

        logger = logging.getLogger(__name__)
        logger.error(f"Error executing sales analytics query: {str(e)}")
        result = []

    if not result:
        # Return empty analytics response
        summary = AnalyticsSummary(
            transaction_count=0,
            total_volume=0.0,
            avg_price=0.0,
            avg_price_sqft=0.0,
            min_price=0.0,
            max_price=0.0,
        )
        return AnalyticsResponse(
            timeframe_days=timeframe_days or (end_date - start_date).days,
            property_usage=property_usage,
            summary=summary,
            trends=[],
            by_property_type=[],
            by_rooms=[],
            by_reg_type=[],
            by_master_project=[],
        )

    # Convert result to summary
    row = result[0]
    summary = AnalyticsSummary(
        transaction_count=row["transaction_count"],
        total_volume=float(row["total_volume"]),
        avg_price=float(row["avg_price"]),
        avg_price_sqft=float(row.get("avg_price_sqft") or 0),
        min_price=float(row.get("min_price") or 0),
        max_price=float(row.get("max_price") or 0),
    )

    # For now, return basic response with just summary
    # TODO: Add trends and breakdowns queries
    return AnalyticsResponse(
        timeframe_days=timeframe_days or (end_date - start_date).days,
        property_usage=property_usage,
        summary=summary,
        trends=[],  # TODO: Implement trends query
        by_property_type=[],  # TODO: Implement breakdowns
        by_rooms=[],
        by_reg_type=[],
        by_master_project=[],
    )


async def get_rental_analytics(
    property_usage: str = "Residential",
    timeframe_days: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    master_project: Optional[str] = None,
    property_type: Optional[str] = None,
    rent_bucket: Optional[str] = None,
    area_bucket: Optional[str] = None,
):
    """Get rental analytics with flexible filtering and custom date ranges.

    Supports:
    - Custom date ranges (use start_date/end_date or timeframe_days)
    - Multi-dimensional filtering (combine property_type, location, rent ranges)
    - Bucketed filtering (rent ranges, area ranges)

    Examples:
    - Last 90 days: ?timeframe_days=90
    - Custom range: ?start_date=2024-06-01&end_date=2024-08-31
    - Filtered: ?property_usage=Commercial&rent_bucket=200K-500K
    """
    # Calculate date range
    if timeframe_days:
        from datetime import timedelta

        end_date = date.today()
        start_date = end_date - timedelta(days=timeframe_days)
    else:
        if not start_date:
            from datetime import timedelta

            start_date = date.today() - timedelta(days=180)  # Default 6 months
        if not end_date:
            end_date = date.today()

    # Execute ClickHouse query

    parameters = {
        "property_usage": property_usage,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
    }

    # Add optional parameters
    if master_project:
        parameters["master_project"] = master_project
    if property_type:
        parameters["property_type"] = property_type

    result = await queries.fetch_rentals_summary(
        parameters,
        property_usage=property_usage,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        master_project=master_project,
        property_type=property_type,
    )

    if not result:
        # Return empty rental analytics response
        summary = RentalSummary(
            contract_count=0,
            total_annual_value=0.0,
            avg_annual_rent=0.0,
            avg_rent_sqft=0.0,
            min_annual_rent=0.0,
            max_annual_rent=0.0,
        )
        return RentalAnalyticsResponse(
            timeframe_days=timeframe_days or (end_date - start_date).days,
            property_usage=property_usage,
            summary=summary,
            trends=[],
            by_property_type=[],
            by_master_project=[],
        )

    # Convert result to summary
    row = result[0]
    summary = RentalSummary(
        contract_count=row["contract_count"],
        total_annual_value=float(row["total_annual_value"]),
        avg_annual_rent=float(row["avg_rent"]),
        avg_rent_sqft=float(row.get("avg_rent_sqft") or 0),
        min_annual_rent=float(row.get("min_rent") or 0),
        max_annual_rent=float(row.get("max_rent") or 0),
    )

    # For now, return basic response with just summary
    # TODO: Add trends and breakdowns queries
    return RentalAnalyticsResponse(
        timeframe_days=timeframe_days or (end_date - start_date).days,
        property_usage=property_usage,
        summary=summary,
        trends=[],  # TODO: Implement trends query
        by_property_type=[],  # TODO: Implement breakdowns
        by_master_project=[],
    )


async def get_market_trend(
    days: int = 180,
    market_type: Optional[str] = None,
    property_usage: Optional[str] = "Residential",
):
    """Get aggregated price trend data for the market trend chart.

    Returns trend points plus period-over-period change vs the equivalent prior period.
    """
    from fastapi import HTTPException

    # Resolve market_type
    resolved_property_usage = property_usage
    resolved_property_usage_in = None
    resolved_property_type = None

    if market_type is not None:
        if market_type not in MARKET_FILTERS:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid market_type. Valid values: {list(MARKET_FILTERS)}",
            )
        mf = MARKET_FILTERS[market_type]
        resolved_property_usage = mf.get("property_usage_en")
        resolved_property_usage_in = mf.get("property_usage_en__in")
        resolved_property_type = mf.get("property_type_en")

    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    prev_end = start_date
    prev_start = prev_end - timedelta(days=days)

    if days <= 30:
        interval = "day"
    elif days <= 180:
        interval = "week"
    else:
        interval = "month"

    def _trend_params(s: str, e: str) -> dict:
        p: dict = {"start_date": s, "end_date": e}
        if resolved_property_usage is not None:
            p["property_usage"] = resolved_property_usage
        if resolved_property_type is not None:
            p["property_type"] = resolved_property_type
        return p

    try:
        trend_result, current_result, prev_result = await asyncio.gather(
            queries.fetch_price_trends(
                _trend_params(start_date.isoformat(), end_date.isoformat()),
                property_usage=resolved_property_usage,
                property_usage_in=resolved_property_usage_in,
                property_type=resolved_property_type,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
                interval=interval,
            ),
            queries.fetch_sales_summary(
                _trend_params(start_date.isoformat(), end_date.isoformat()),
                property_usage=resolved_property_usage,
                property_usage_in=resolved_property_usage_in,
                property_type=resolved_property_type,
                start_date=start_date.isoformat(),
                end_date=end_date.isoformat(),
            ),
            queries.fetch_sales_summary(
                _trend_params(prev_start.isoformat(), prev_end.isoformat()),
                property_usage=resolved_property_usage,
                property_usage_in=resolved_property_usage_in,
                property_type=resolved_property_type,
                start_date=prev_start.isoformat(),
                end_date=prev_end.isoformat(),
            ),
        )
    except Exception as e:
        import logging

        logging.getLogger(__name__).error(f"Error executing market trend query: {e}")
        return MarketTrendResponse(data=[], total_transactions=0)

    data = [
        MarketTrendPoint(
            date=row["period"],
            transaction_count=int(row["transaction_count"]),
            avg_price=float(row["avg_price"]),
            avg_price_sqm=float(row["avg_price_sqm"]),
        )
        for row in trend_result
    ]

    def pct_change(current: float, previous: float) -> Optional[float]:
        if not previous:
            return None
        return round((current - previous) / previous * 100, 1)

    avg_price_change_pct = None
    avg_price_sqm_change_pct = None
    if current_result and prev_result:
        curr = current_result[0]
        prev = prev_result[0]
        avg_price_change_pct = pct_change(float(curr["avg_price"]), float(prev["avg_price"]))
        avg_price_sqm_change_pct = pct_change(
            float(curr["median_price_per_sqm"]), float(prev["median_price_per_sqm"])
        )

    return MarketTrendResponse(
        data=data,
        total_transactions=sum(p.transaction_count for p in data),
        avg_price_change_pct=avg_price_change_pct,
        avg_price_sqm_change_pct=avg_price_sqm_change_pct,
    )


@operation
async def get_market_pulse(area: str, period_days: int = 90) -> dict[str, Any]:
    """Get vital market statistics for a specific area.

    Returns aggregate metrics including median price, average yield, transaction volume,
    and price trends for the specified area and time period.

    Args:
        ctx: Runtime context (unused)
        area: Area name (e.g., "Dubai Marina", "Downtown Dubai")
        period_days: Analysis period in days (default: 90)

    Returns:
        Dictionary with price, yield, volume, and trend data
    """

    try:
        rows = await queries.fetch_market_pulse({"area": area, "period_days": period_days})

        if not rows:
            return {
                "success": False,
                "error": f"No data found for area '{area}' in the last {period_days} days",
            }

        row = rows[0]
        return {
            "success": True,
            "area": area,
            "period_days": period_days,
            "transaction_count": row["transaction_count"],
            "median_price_aed": row["median_price"],
            "avg_price_aed": row["avg_price"],
            "median_area_sqft": row["median_area_sqft"],
            "median_price_per_sqft_aed": row["median_price_per_sqft"],
            "period_start": str(row["period_start"]),
            "period_end": str(row["period_end"]),
        }
    except Exception as e:
        logger.exception(f"Error executing get_market_pulse for area {area}")
        return {"success": False, "error": str(e)}


@operation
async def rank_areas(
    metric: str = "volume",
    limit: int = 5,
    period_days: int = 90,
) -> dict[str, Any]:
    """Rank top areas by a specific metric.

    Args:
        ctx: Runtime context (unused)
        metric: Ranking metric - "volume" (transaction count), "price" (median price),
                or "growth" (price change percentage)
        limit: Number of top areas to return (default: 5)
        period_days: Analysis period in days (default: 90)

    Returns:
        List of top areas with their respective metrics
    """
    if metric not in {"volume", "price", "growth"}:
        return {
            "success": False,
            "error": f"Invalid metric '{metric}'. Use 'volume', 'price', or 'growth'",
        }

    try:
        rows = await queries.fetch_rank_areas(metric, {"period_days": period_days, "limit": limit})

        if not rows:
            return {
                "success": False,
                "error": f"No data found for metric '{metric}' in the last {period_days} days",
            }

        areas = []
        for row in rows:
            if metric == "volume":
                areas.append(
                    {
                        "area": row["area"],
                        "transaction_count": row["transaction_count"],
                        "median_price_aed": row["median_price"],
                    }
                )
            elif metric == "price":
                areas.append(
                    {
                        "area": row["area"],
                        "median_price_aed": row["median_price"],
                        "transaction_count": row["transaction_count"],
                    }
                )
            elif metric == "growth":
                areas.append(
                    {
                        "area": row["area"],
                        "current_median_price_aed": row["current_median_price"],
                        "previous_median_price_aed": row["previous_median_price"],
                        "growth_percentage": row["growth_percentage"],
                    }
                )

        return {
            "success": True,
            "metric": metric,
            "period_days": period_days,
            "top_areas": areas,
        }
    except Exception as e:
        logger.exception(f"Error executing rank_areas for metric {metric}")
        return {"success": False, "error": str(e)}
