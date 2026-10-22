import asyncio
from typing import Any

from app.plots.geometry import land_use_labels
from app.project import radar_queries
from app.project.mapping import (
    RADAR_YIELD_MIN_SAMPLE_COUNT,
    _date_or_none,
    _developer_display_list,
    _developer_display_name,
    _developer_lookup,
    _float_or_none,
    _int_value,
)
from app.project.models import (
    ProjectRadarArea,
    ProjectRadarOverviewResponse,
    ProjectRadarProject,
    ProjectRadarResponse,
    ProjectRadarStats,
    RadarBuildingPin,
)
from app.project.radar_queries import (
    CH_GEO_BUILDINGS_SOURCE,
)

RADAR_YIELD_MIN_PCT = 1

RADAR_YIELD_MAX_PCT = 25

RADAR_ACTIVITY_PERIODS = frozenset({30, 90, 180, 365})


def _radar_gross_yield_pct(row: dict[str, Any]) -> float | None:
    """Compute yield with the same existing-sale denominator used by analytics."""
    sale_count = _int_value(row.get("yield_existing_sale_count"))
    rent_count = _int_value(row.get("yield_rental_contract_count"))
    median_price = _float_or_none(row.get("yield_existing_median_sale_price"))
    median_rent = _float_or_none(row.get("yield_median_annual_rent"))
    if (
        sale_count < RADAR_YIELD_MIN_SAMPLE_COUNT
        or rent_count < RADAR_YIELD_MIN_SAMPLE_COUNT
        or not median_price
        or not median_rent
    ):
        fact_yield = _float_or_none(row.get("gross_yield_pct"))
        if fact_yield is not None and RADAR_YIELD_MIN_PCT <= fact_yield <= RADAR_YIELD_MAX_PCT:
            return round(fact_yield, 2)
        return None

    yield_pct = round(median_rent / median_price * 100, 2)
    if yield_pct < RADAR_YIELD_MIN_PCT or yield_pct > RADAR_YIELD_MAX_PCT:
        return None
    return yield_pct


def _validated_radar_period_days(period_days: int) -> int:
    if period_days not in RADAR_ACTIVITY_PERIODS:
        raise ValueError("Radar activity period must be one of 30, 90, 180, or 365 days")
    return period_days


def _radar_source_notes(period_days: int) -> list[str]:
    return [
        f"Market activity uses detailed DLD sales and Ejari lease records for the trailing {period_days} days.",
        "Activity is anchored to the latest available record and never extends beyond one trailing year.",
        "Transaction locations use approved canonical geography links; coverage is shown with the map totals.",
        "Project pins include every geocoded Dubai Reelly project plus non-duplicate historical and secondary projects.",
        "The tower layer uses Property Finder's physical tower coordinates and only attaches exact, approved DLD/Ejari evidence.",
        "Small villa parcels may be marked probable when their enclosing subdivision has confirmed development evidence.",
    ]


def _radar_stats_from_row(row: dict[str, Any], period_days: int) -> ProjectRadarStats:
    sales_count = int(row.get("sales_transaction_count") or 0)
    rent_count = int(row.get("rental_contract_count") or 0)
    sales_volume = _float_or_none(row.get("total_sales_volume")) or 0.0
    return ProjectRadarStats(
        total_projects=int(row.get("total_projects") or 0),
        mapped_projects=int(row.get("mapped_projects") or 0),
        active_projects=int(row.get("active_projects") or 0),
        finished_projects=int(row.get("finished_projects") or 0),
        pipeline_units=int(row.get("pipeline_units") or 0),
        units_delivering_next_12_months=int(row.get("units_delivering_next_12_months") or 0),
        sales_transaction_count_12m=sales_count,
        rental_contract_count_12m=rent_count,
        total_sales_volume_12m=sales_volume,
        dda_plot_count=int(row.get("dda_plot_count") or 0),
        dda_total_plot_area_sqm=_float_or_none(row.get("dda_total_plot_area_sqm")) or 0.0,
        dda_total_gfa_sqm=_float_or_none(row.get("dda_total_gfa_sqm")) or 0.0,
        activity_period_days=period_days,
        activity_period_start=row.get("activity_period_start"),
        activity_period_end=row.get("activity_period_end"),
        sales_transaction_count=sales_count,
        rental_contract_count=rent_count,
        total_sales_volume=sales_volume,
        mapped_sales_transaction_count=int(row.get("mapped_sales_transaction_count") or 0),
        mapped_rental_contract_count=int(row.get("mapped_rental_contract_count") or 0),
        activity_coverage_pct=_float_or_none(row.get("activity_coverage_pct")) or 0.0,
    )


def _radar_projects_from_rows(
    rows: list[dict[str, Any]], developers: dict[str, str]
) -> list[ProjectRadarProject]:
    return [
        ProjectRadarProject(
            project_id=row["project_id"],
            project_name=row["project_name_en"],
            area_name=row.get("area_name_en") or "Unknown",
            developer_name=_developer_display_name(row.get("developer_name"), developers),
            master_project_en=row.get("master_project_en") or None,
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            geo_pin_count=int(row.get("geo_pin_count") or 0),
            coordinate_source=row.get("coordinate_source") or CH_GEO_BUILDINGS_SOURCE,
            coordinate_method=row.get("coordinate_method") or None,
            detail_point_count=int(row.get("detail_point_count") or 0),
            building_point_count=int(row.get("building_point_count") or 0),
            land_point_count=int(row.get("land_point_count") or 0),
            entity_source=row.get("entity_source") or "legacy_project",
            supply_status=row.get("supply_status") or None,
            supply_completion_date=_date_or_none(row.get("supply_completion_time")),
            units_in_sale=int(row.get("units_in_sale") or 0),
            completion_status=row.get("completion_status") or "",
            pipeline_status=row.get("pipeline_status") or "",
            project_status=row.get("project_status") or None,
            no_of_units=int(row.get("no_of_units") or 0),
            no_of_buildings=int(row.get("no_of_buildings") or 0),
            percent_completed=_float_or_none(row.get("percent_completed")),
            delivery_confidence=_float_or_none(row.get("estimated_delivery_confidence")) or 0.0,
            sales_transaction_count_12m=int(row.get("sales_transaction_count") or 0),
            rental_contract_count_12m=int(row.get("rental_contract_count") or 0),
            total_sales_volume_12m=_float_or_none(row.get("total_sales_volume")) or 0.0,
            median_sale_price=_float_or_none(row.get("median_sale_price")),
            median_annual_rent=_float_or_none(row.get("median_annual_rent")),
            gross_yield_pct=_radar_gross_yield_pct(row),
            avg_sale_price_sqm_12m=_float_or_none(row.get("avg_sale_price_sqm")),
            avg_rent_price_sqm_12m=_float_or_none(row.get("avg_rent_price_sqm")),
            sales_transaction_count=int(row.get("sales_transaction_count") or 0),
            rental_contract_count=int(row.get("rental_contract_count") or 0),
            total_sales_volume=_float_or_none(row.get("total_sales_volume")) or 0.0,
            avg_sale_price_sqm=_float_or_none(row.get("avg_sale_price_sqm")),
            avg_rent_price_sqm=_float_or_none(row.get("avg_rent_price_sqm")),
        )
        for row in rows
    ]


def _radar_areas_from_rows(
    rows: list[dict[str, Any]], developers: dict[str, str]
) -> list[ProjectRadarArea]:
    areas = [
        ProjectRadarArea(
            area_id=int(row.get("area_id") or 0),
            area_name=row.get("area_name_en") or "Unknown",
            latitude=_float_or_none(row.get("latitude")),
            longitude=_float_or_none(row.get("longitude")),
            mapped_projects=int(row.get("mapped_projects") or 0),
            active_projects=int(row.get("active_projects") or 0),
            completed_projects=int(row.get("completed_projects") or 0),
            overdue_projects=int(row.get("overdue_projects") or 0),
            pipeline_units=int(row.get("pipeline_units") or 0),
            units_delivering_next_12_months=int(row.get("units_delivering_next_12_months") or 0),
            active_developers=int(row.get("active_developers") or 0),
            avg_completion_pct=_float_or_none(row.get("avg_completion_pct")) or 0.0,
            avg_delivery_confidence=_float_or_none(row.get("avg_delivery_confidence")) or 0.0,
            sales_transaction_count_12m=int(row.get("sales_transaction_count") or 0),
            rental_contract_count_12m=int(row.get("rental_contract_count") or 0),
            total_sales_volume_12m=_float_or_none(row.get("total_sales_volume")) or 0.0,
            median_sale_price=_float_or_none(row.get("median_sale_price")),
            median_annual_rent=_float_or_none(row.get("median_annual_rent")),
            avg_sale_price_sqm_12m=_float_or_none(row.get("avg_sale_price_sqm")),
            avg_rent_price_sqm_12m=_float_or_none(row.get("avg_rent_price_sqm")),
            gross_yield_pct=_radar_gross_yield_pct(row),
            top_developers=_developer_display_list(row.get("top_developers"), developers, limit=5),
            top_master_projects=list(row.get("top_master_projects") or [])[:5],
            dda_plot_count=int(row.get("dda_plot_count") or 0),
            dda_total_plot_area_sqm=_float_or_none(row.get("dda_total_plot_area_sqm")) or 0.0,
            dda_total_gfa_sqm=_float_or_none(row.get("dda_total_gfa_sqm")) or 0.0,
            dda_dominant_land_uses=land_use_labels(row.get("dda_dominant_land_uses"), limit=5),
            sales_transaction_count=int(row.get("sales_transaction_count") or 0),
            rental_contract_count=int(row.get("rental_contract_count") or 0),
            total_sales_volume=_float_or_none(row.get("total_sales_volume")) or 0.0,
            avg_sale_price_sqm=_float_or_none(row.get("avg_sale_price_sqm")),
            avg_rent_price_sqm=_float_or_none(row.get("avg_rent_price_sqm")),
        )
        for row in rows
    ]
    unique_areas: list[ProjectRadarArea] = []
    seen_area_names: set[str] = set()
    for area in areas:
        area_key = area.area_name.strip().lower()
        if area_key in seen_area_names:
            continue
        seen_area_names.add(area_key)
        unique_areas.append(area)
    return unique_areas


def _radar_building_pins_from_rows(rows: list[dict[str, Any]]) -> list[RadarBuildingPin]:
    return [
        RadarBuildingPin(
            location_id=str(row["location_id"]),
            source_location_id=str(row["source_location_id"]),
            building_name=str(row["building_name"]),
            project_name=str(row.get("project_name") or "") or None,
            area_name=str(row.get("area_name") or "Unknown"),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            coordinate_source=str(row.get("coordinate_source") or "property_finder"),
            coordinate_precision=str(row.get("coordinate_precision") or "tower"),
            matched_source_signatures=int(row.get("matched_source_signatures") or 0),
            sales_transaction_count=int(row.get("sales_transaction_count") or 0),
            rental_contract_count=int(row.get("rental_contract_count") or 0),
            total_sales_volume=_float_or_none(row.get("total_sales_volume")) or 0.0,
            median_sale_price=_float_or_none(row.get("median_sale_price")),
            median_annual_rent=_float_or_none(row.get("median_annual_rent")),
            listing_inventory_count=int(row.get("listing_inventory_count") or 0),
            last_activity_date=row.get("last_activity_date"),
        )
        for row in rows
    ]


async def get_radar_building_pins(
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    limit: int = 3000,
    period_days: int = 365,
) -> list[RadarBuildingPin]:
    """Return physical PF tower pins for a bounded map viewport."""
    period_days = _validated_radar_period_days(period_days)
    rows = await radar_queries.fetch_building_pins(
        {
            "west": west,
            "south": south,
            "east": east,
            "north": north,
            "limit": min(max(limit, 10), 10_000),
            "period_days": period_days,
        }
    )
    return _radar_building_pins_from_rows(rows)


async def get_project_radar_overview(period_days: int = 365) -> ProjectRadarOverviewResponse:
    """Return the initial radar payload without project pins."""
    period_days = _validated_radar_period_days(period_days)
    area_rows, stats_rows, developer_rows = await radar_queries.fetch_overview(
        period_days=period_days
    )
    developers = _developer_lookup(developer_rows)
    return ProjectRadarOverviewResponse(
        stats=_radar_stats_from_row(stats_rows[0] if stats_rows else {}, period_days),
        areas=_radar_areas_from_rows(area_rows, developers),
        source_notes=_radar_source_notes(period_days),
    )


async def get_project_radar_pins(
    *,
    limit: int = 700,
    west: float | None = None,
    south: float | None = None,
    east: float | None = None,
    north: float | None = None,
    area: str | None = None,
    developer: str | None = None,
    period_days: int = 365,
) -> list[ProjectRadarProject]:
    """Return bounded project pins for a viewport, area, or developer overlay."""
    period_days = _validated_radar_period_days(period_days)
    project_rows, developer_rows = await radar_queries.fetch_project_pins(
        area=area,
        developer=developer,
        east=east,
        limit=limit,
        north=north,
        period_days=period_days,
        south=south,
        west=west,
    )
    return _radar_projects_from_rows(project_rows, _developer_lookup(developer_rows))


async def get_project_radar(limit: int = 1400, period_days: int = 365) -> ProjectRadarResponse:
    """Return the legacy combined radar payload from the fast serving paths."""
    period_days = _validated_radar_period_days(period_days)
    overview, projects = await asyncio.gather(
        get_project_radar_overview(period_days=period_days),
        get_project_radar_pins(
            limit=min(max(limit, 10), 1400),
            period_days=period_days,
        ),
    )
    return ProjectRadarResponse(
        stats=overview.stats,
        areas=overview.areas,
        projects=projects,
        source_notes=overview.source_notes,
    )
