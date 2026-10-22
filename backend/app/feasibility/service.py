import logging

from fastapi import HTTPException

from app.feasibility.models import (
    ComparableData,
)
from app.feasibility.research import get_areas, get_comparable_data
from app.plots.evidence import get_land_sale_evidence
from app.plots.models import LandSaleEvidence, Plot, PlotDetails
from app.plots.service import (
    get_land_plots,
    get_plot_details,
    list_communities,
    search_land_plots,
    search_plots,
)

logger = logging.getLogger(__name__)


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=404, detail=detail)


def _parse_project_ids(project_ids: str) -> list[int]:
    return [int(value.strip()) for value in project_ids.split(",") if value.strip().isdigit()]


async def list_transaction_areas() -> list[str]:
    return await get_areas()


async def list_plot_communities() -> list[str]:
    return await list_communities()


async def search_gis_plots(
    *,
    q: str,
    community: str,
    min_plot_area_sqm: float | None,
    max_plot_area_sqm: float | None,
    offset: int,
    limit: int,
) -> list[Plot]:
    return await search_plots(
        q=q,
        community=community,
        min_plot_area_sqm=min_plot_area_sqm,
        max_plot_area_sqm=max_plot_area_sqm,
        offset=offset,
        limit=limit,
    )


async def get_plot_details_or_404(plot_number: str) -> PlotDetails:
    plot = await get_plot_details(plot_number)
    if plot is None:
        raise HTTPException(status_code=404, detail="GIS plot not found")
    return plot


async def get_plot_land_sales_or_404(
    *,
    plot_number: str,
    days: int,
    limit: int,
) -> list[LandSaleEvidence]:
    plot = await get_plot_details_or_404(plot_number)
    return await get_land_sale_evidence(
        area_name=plot.community_name,
        plot_area_sqm=plot.plot_area_sqm,
        days=days,
        limit=limit,
    )


async def list_nearby_land_plots(area: str, size_sqm: float, tolerance: float) -> list[Plot]:
    return await get_land_plots(area, size_sqm, tolerance)


async def search_nearby_land_plots(area: str, q: str) -> list[Plot]:
    return await search_land_plots(area, q)


async def get_comparable_or_404(project_ids: str) -> ComparableData:
    comparable = await get_comparable_data(_parse_project_ids(project_ids))
    if comparable is None:
        raise HTTPException(status_code=404, detail="No data found for given project IDs")
    return comparable
