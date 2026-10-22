"""Analytics API endpoints with flexible filtering from daily aggregates."""

from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.analytics import market_metrics
from app.analytics import service as analytics_service
from app.analytics.models import (
    AnalyticsResponse,
    MarketMetricsRequest,
    MarketMetricsResponse,
    MarketTrendResponse,
    ProjectAnalyticsResponse,
    ProjectForecastResponse,
    ProjectPipelineResponse,
    ProjectPriceDistributionResponse,
    ProjectSearchResponse,
    ProjectTrendsResponse,
    RentalAnalyticsResponse,
    SubProjectsResponse,
)
from app.analytics.queries import MARKET_FILTERS, ProjectFilterType
from app.analytics.service import (
    _resolve_project_scope,
    get_project_forecast,
    get_project_pipeline,
    get_project_price_distribution,
    get_project_search,
    get_project_summary,
    get_project_trends,
    get_sub_project_list,
)
from app.core.decorators import cached_endpoint
from app.core.dependencies import AuthContext, require_active_subscription

router = APIRouter(prefix="/api/v1/analytics", tags=["analytics"])


@router.get("/sales", response_model=AnalyticsResponse)
async def get_sales_analytics(
    context: AuthContext = Depends(require_active_subscription),
    market_type: Optional[str] = Query(
        default=None,
        description=f"Market type (overrides property_usage/property_type). One of: {', '.join(MARKET_FILTERS)}",
    ),
    property_usage: Optional[str] = Query(
        default="Residential",
        description="Residential or Commercial (ignored if market_type set)",
    ),
    timeframe_days: Optional[int] = Query(
        default=None,
        description="Number of days to look back (overrides start_date if provided)",
    ),
    start_date: Optional[date] = Query(
        default=None, description="Explicit start date (YYYY-MM-DD)"
    ),
    end_date: Optional[date] = Query(
        default=None, description="Explicit end date (YYYY-MM-DD), defaults to today"
    ),
    master_project: Optional[str] = Query(
        default=None, description="Filter by master project name"
    ),
    property_type: Optional[str] = Query(
        default=None, description="Filter by property type (ignored if market_type set)"
    ),
    rooms: Optional[str] = Query(default=None, description="Filter by room count"),
    reg_type: Optional[str] = Query(default=None, description="Filter by registration type"),
    price_bucket: Optional[str] = Query(
        default=None, description="Filter by price bucket (e.g., '1M-2M')"
    ),
    area_bucket: Optional[str] = Query(
        default=None, description="Filter by area bucket (e.g., '1K-2K')"
    ),
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
    return await analytics_service.get_sales_analytics(
        market_type=market_type,
        property_usage=property_usage,
        timeframe_days=timeframe_days,
        start_date=start_date,
        end_date=end_date,
        master_project=master_project,
        property_type=property_type,
        rooms=rooms,
        reg_type=reg_type,
        price_bucket=price_bucket,
        area_bucket=area_bucket,
    )


@router.get("/rentals", response_model=RentalAnalyticsResponse)
async def get_rental_analytics(
    context: AuthContext = Depends(require_active_subscription),
    property_usage: str = Query(default="Residential", description="Residential or Commercial"),
    timeframe_days: Optional[int] = Query(
        default=None,
        description="Number of days to look back (overrides start_date if provided)",
    ),
    start_date: Optional[date] = Query(
        default=None, description="Explicit start date (YYYY-MM-DD)"
    ),
    end_date: Optional[date] = Query(
        default=None, description="Explicit end date (YYYY-MM-DD), defaults to today"
    ),
    master_project: Optional[str] = Query(
        default=None, description="Filter by master project name"
    ),
    property_type: Optional[str] = Query(default=None, description="Filter by property type"),
    rent_bucket: Optional[str] = Query(
        default=None, description="Filter by rent bucket (e.g., '100K-200K')"
    ),
    area_bucket: Optional[str] = Query(
        default=None, description="Filter by area bucket (e.g., '1K-2K')"
    ),
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
    return await analytics_service.get_rental_analytics(
        property_usage=property_usage,
        timeframe_days=timeframe_days,
        start_date=start_date,
        end_date=end_date,
        master_project=master_project,
        property_type=property_type,
        rent_bucket=rent_bucket,
        area_bucket=area_bucket,
    )


@router.get("/market-trend", response_model=MarketTrendResponse)
async def get_market_trend(
    context: AuthContext = Depends(require_active_subscription),
    days: int = Query(default=180, description="Number of days to look back (30, 90, 180, 365)"),
    market_type: Optional[str] = Query(
        default=None,
        description=f"Market type (overrides property_usage). One of: {', '.join(MARKET_FILTERS)}",
    ),
    property_usage: Optional[str] = Query(
        default="Residential",
        description="Residential or Commercial (ignored if market_type set)",
    ),
):
    """Get aggregated price trend data for the market trend chart.

    Returns trend points plus period-over-period change vs the equivalent prior period.
    """
    return await analytics_service.get_market_trend(
        days=days, market_type=market_type, property_usage=property_usage
    )


# =============================================================================
# PROJECT ANALYTICS ENDPOINTS
# =============================================================================


@router.get("/projects", response_model=ProjectSearchResponse)
async def search_projects_endpoint(
    context: AuthContext = Depends(require_active_subscription),
    search: str = Query(default="", description="Master project name search term"),
    limit: int = Query(default=50, description="Max results to return"),
):
    """Search master project names for the selector dropdown."""
    return await get_project_search(search=search, limit=limit)


@router.get("/project/sub-projects", response_model=SubProjectsResponse)
async def get_sub_projects_endpoint(
    context: AuthContext = Depends(require_active_subscription),
    master_project: str = Query(description="Master project name"),
    filter_type: ProjectFilterType = Query(
        default="master", description="Filter type: master or virtual_master"
    ),
):
    """List sub-projects within a master or virtual master, sorted by transaction count."""
    return await get_sub_project_list(master_project=master_project, filter_type=filter_type)


@router.get("/project/summary", response_model=ProjectAnalyticsResponse)
@cached_endpoint(prefix="analytics:project_summary", expire=900)
async def get_project_summary_endpoint(
    context: AuthContext = Depends(require_active_subscription),
    master_project: Optional[str] = Query(default=None, description="Master project name"),
    project: Optional[str] = Query(default=None, description="Sub-project name (project_name_en)"),
    filter_type: ProjectFilterType = Query(
        default="project", description="Filter type: master, virtual_master, or project"
    ),
):
    """
    Full analytics for a master project, virtual master, or specific sub-project.
    Pass master_project with filter_type=master/virtual_master to aggregate the whole development,
    or project with filter_type=project for a specific sub-project.
    """
    project_name, resolved_filter_type = _resolve_project_scope(
        master_project=master_project, project=project, filter_type=filter_type
    )
    return await get_project_summary(
        project_name=project_name,
        filter_type=resolved_filter_type,
        master_project=master_project,
    )


@router.get("/project/trends", response_model=ProjectTrendsResponse)
async def get_project_trends_endpoint(
    context: AuthContext = Depends(require_active_subscription),
    master_project: Optional[str] = Query(default=None, description="Master project name"),
    project: Optional[str] = Query(default=None, description="Sub-project name (project_name_en)"),
    filter_type: ProjectFilterType = Query(
        default="project", description="Filter type: master, virtual_master, or project"
    ),
    days: int = Query(default=365, description="Number of days to look back"),
):
    """Sales price trend time series for a project, master, or virtual master."""
    project_name, resolved_filter_type = _resolve_project_scope(
        master_project=master_project, project=project, filter_type=filter_type
    )
    return await get_project_trends(
        project_name=project_name, days=days, filter_type=resolved_filter_type
    )


@router.get("/project/forecast", response_model=ProjectForecastResponse)
async def get_project_forecast_endpoint(
    context: AuthContext = Depends(require_active_subscription),
    master_project: Optional[str] = Query(default=None, description="Master project name"),
    project: Optional[str] = Query(default=None, description="Sub-project name (project_name_en)"),
    filter_type: ProjectFilterType = Query(
        default="project", description="Filter type: master, virtual_master, or project"
    ),
    days: int = Query(default=365, description="Number of days to look back"),
):
    project_name, resolved_filter_type = _resolve_project_scope(
        master_project=master_project, project=project, filter_type=filter_type
    )
    return await get_project_forecast(
        project_name=project_name, days=days, filter_type=resolved_filter_type
    )


@router.get("/project/pipeline", response_model=ProjectPipelineResponse)
async def get_project_pipeline_endpoint(
    context: AuthContext = Depends(require_active_subscription),
    master_project: Optional[str] = Query(default=None, description="Master project name"),
    project: Optional[str] = Query(default=None, description="Sub-project name (project_name_en)"),
    filter_type: ProjectFilterType = Query(
        default="project", description="Filter type: master, virtual_master, or project"
    ),
):
    project_name, resolved_filter_type = _resolve_project_scope(
        master_project=master_project, project=project, filter_type=filter_type
    )
    return await get_project_pipeline(project_name=project_name, filter_type=resolved_filter_type)


@router.get(
    "/project/price-distribution",
    response_model=ProjectPriceDistributionResponse,
)
async def get_project_price_distribution_endpoint(
    context: AuthContext = Depends(require_active_subscription),
    master_project: Optional[str] = Query(default=None, description="Master project name"),
    project: Optional[str] = Query(default=None, description="Sub-project name (project_name_en)"),
    filter_type: ProjectFilterType = Query(
        default="project", description="Filter type: master, virtual_master, or project"
    ),
    reg_type: Optional[str] = Query(
        default=None,
        description="Optional registration type filter (e.g. 'Off-Plan Properties')",
    ),
):
    project_name, resolved_filter_type = _resolve_project_scope(
        master_project=master_project, project=project, filter_type=filter_type
    )
    return await get_project_price_distribution(
        project_name=project_name,
        filter_type=resolved_filter_type,
        reg_type=reg_type,
    )


@router.post("/market-metrics", response_model=MarketMetricsResponse)
async def get_market_metrics_endpoint(
    request: MarketMetricsRequest,
    context: AuthContext = Depends(require_active_subscription),
):
    """Sales or rental metrics with explicit period, coverage, definitions and evidence.

    The same operation backs the chat tool, so API and chat answers agree.
    """
    try:
        return await market_metrics.get_market_metrics(request)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
