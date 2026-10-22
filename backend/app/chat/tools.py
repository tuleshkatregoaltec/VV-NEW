from datetime import date
from typing import Any, Literal, Optional

from pydantic_ai import RunContext

from app.analytics import market_metrics
from app.analytics import service as analytics_service
from app.analytics.models import MarketMetricsRequest
from app.listings import service as listings_service
from app.market.reference import community_location
from app.project import delivery
from app.project import service as project_service
from app.project.models import DeliveryStatus


async def get_market_pulse(
    ctx: RunContext[None], area: str, period_days: int = 90
) -> dict[str, Any]:
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
    return await analytics_service.get_market_pulse(area, period_days)


async def rank_areas(
    ctx: RunContext[None],
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
    return await analytics_service.rank_areas(metric, limit, period_days)


async def get_similar_projects(
    ctx: RunContext[None], project_name: str, limit: int = 5
) -> dict[str, Any]:
    """Find similar projects based on attributes.

    Uses ClickHouse projections to efficiently search for comparable projects
    based on property characteristics, location, and developer.

    Args:
        ctx: Runtime context (unused)
        project_name: Name of the reference project
        limit: Number of similar projects to return (default: 5)

    Returns:
        List of similar projects with comparison metrics
    """
    # First, get the reference project details
    return await project_service.get_similar_projects(project_name, limit)


async def query_market_metrics(
    ctx: RunContext[None], request: MarketMetricsRequest
) -> dict[str, Any]:
    """Compute sales or rental metrics from the stored DLD/DXBI records.

    Use this for counts, totals, averages (mean_aed), medians, quartiles, prices per sqm/sqft,
    price brackets, rankings by building/project/community/area (area = official DLD
    district), monthly series and new-versus-renewed rents. Periods are calendar periods in
    Dubai time: "last month" is {"type": "previous_month"}, "last quarter"
    {"type": "previous_quarter"}, "last 90 days" {"type": "rolling_days", "days": 90}, "last
    six complete months" {"type": "previous_months", "months": 6}. "Average" means mean_aed
    unless the user asks for the median.

    The result states the resolved dates, coverage (complete, partial, unverified or
    unavailable; only complete means every day is verified as loaded), eligible record
    count, metric definitions, sources, release_id and limitations. Report those with the
    numbers. A community answer leaves out records DLD locates only by a wider district; the
    limitations say how many. If coverage is unavailable or a value is null, say the stored
    data does not contain it; never fill the gap from other sources.
    """
    result = await market_metrics.get_market_metrics(request)
    return result.model_dump(mode="json")


async def get_listing_inventory(
    ctx: RunContext[None],
    mode: Literal["sale", "rent"],
    location: Optional[str] = None,
    bedrooms: Optional[str] = None,
    property_type: Optional[str] = None,
    price_max: Optional[int] = None,
) -> dict[str, Any]:
    """Current Property Finder listing inventory: count and asking prices.

    These are asking prices from the latest captured listing snapshot, not achieved prices.
    Medians here are approximate (t-digest). snapshot_at is when the inventory was captured.

    Args:
        ctx: Runtime context (unused)
        mode: "sale" or "rent" (rent prices are annual)
        location: Community, e.g. "Dubai Hills Estate" or "JVC"
        bedrooms: Bedroom value as listed, e.g. "studio", "1", "2"
        property_type: Listing property type, e.g. "Apartment"
        price_max: Maximum asking price in AED
    """
    area = community_location(location).name if location else None
    result = await listings_service.listing_analytics_from_clickhouse(
        mode=mode,
        area=area,
        bedrooms=bedrooms,
        property_type=property_type,
        price_max=price_max,
    )
    payload = result.model_dump(mode="json", exclude={"trend"})
    return payload | {
        "source": "Property Finder listings (asking prices)",
        "filters": {"mode": mode, "community": area, "bedrooms": bedrooms},
    }


async def get_project_delivery_summary(
    ctx: RunContext[None],
    location: Optional[str] = None,
    group_by: Literal["developer", "delivery_status", "delivery_year", "project"] = "developer",
    delivery_statuses: Optional[list[DeliveryStatus]] = None,
    horizon_months: int = 24,
    project_statuses: Optional[list[str]] = None,
    reference_date: Optional[date] = None,
) -> dict[str, Any]:
    """DLD-registered projects and units by delivery status, developer, year or project.

    Delivery status on the reference date (Dubai): missing_date, overdue,
    scheduled_in_horizon (planned end within horizon_months), scheduled_later, completed,
    cancelled. project_statuses are DLD register statuses (default ["ACTIVE"]).
    Example: developers' share of units due in the next two years ->
    group_by="developer", delivery_statuses=["scheduled_in_horizon"], horizon_months=24.
    """
    result = await delivery.get_delivery_summary(
        location=location,
        project_statuses=tuple(project_statuses or ["ACTIVE"]),
        delivery_statuses=tuple(delivery_statuses or delivery.ALL_DELIVERY_STATUSES),
        group_by=group_by,
        horizon_months=horizon_months,
        reference_date=reference_date,
    )
    return result.model_dump(mode="json")
