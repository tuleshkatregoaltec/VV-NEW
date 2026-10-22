import logging
from datetime import date, datetime
from typing import Any, Dict, List

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.supply import queries
from app.supply.mapping import (
    _as_list,
    _catalogue_data_from_clickhouse_row,
    _developer_logo_match_key,
    _freshness_datetime,
    _reelly_launch_date,
    _row_data,
    _text_or_none,
)
from app.supply.models import (
    DeveloperRankingResponse,
    SupplyCatalogueBuildingResponse,
    SupplyCatalogueNearbyPointResponse,
    SupplyCataloguePaymentStepResponse,
    SupplyCatalogueProjectCardResponse,
    SupplyCatalogueProjectDetailResponse,
    SupplyCatalogueProjectsResponse,
    SupplyCatalogueUnitResponse,
)
from app.supply.queries import _catalogue_facet_values, _latest_sales_date
from app.supply.scoring import (
    _delivery_indicator,
    _developer_indicator,
    _financial_risk_metrics,
    _launch_indicator,
    _progress_indicator,
    _project_match_score,
    _project_risk_badge,
    _project_sales_inventory,
    _sales_indicator,
    _score_weighted_indicators,
)

logger = logging.getLogger(__name__)


async def get_upcoming_projects_ch(limit: int = 1000, offset: int = 0) -> List[Dict[str, Any]]:
    """
    Get all active/upcoming projects from ClickHouse.
    Active = delivery date after the latest loaded DLD sales date and no cancellation.
    """
    try:
        anchor_date = await _latest_sales_date() or date.today()

        params = {"anchor_date": anchor_date.isoformat(), "limit": limit, "offset": offset}

        result = await queries.fetch_upcoming_projects(params)
        return result

    except Exception as e:
        logger.error(f"Error fetching upcoming projects from ClickHouse: {str(e)}")
        return []


def _card_from_row(
    row: Any,
    developer_scores: dict[str, DeveloperRankingResponse] | None = None,
    dld_signals: dict[int, dict[str, Any]] | None = None,
) -> SupplyCatalogueProjectCardResponse:
    data = _row_data(row)
    developer_scores = developer_scores or {}
    dld_signal = (dld_signals or {}).get(int(data["project_id"]))
    launch_date = _reelly_launch_date(data)
    sales_inventory = _project_sales_inventory(data, dld_signal)
    risk_indicators = [
        _developer_indicator(data, developer_scores),
        _launch_indicator(data, dld_signal),
        _delivery_indicator(data, dld_signal),
        _progress_indicator(dld_signal),
        _sales_indicator(data, dld_signal),
    ]
    project_score, score_coverage = _score_weighted_indicators(risk_indicators)
    developer = developer_scores.get(_developer_logo_match_key(data.get("developer_name")))

    return SupplyCatalogueProjectCardResponse(
        project_id=data["project_id"],
        project_name=data["project_name"],
        developer_name=data.get("developer_name") or "Unknown developer",
        developer_id=data.get("developer_id"),
        area_name=data.get("area_name"),
        region=data.get("region"),
        status=data.get("status"),
        sale_status=data.get("sale_status"),
        completion_date=data.get("completion_date"),
        launch_date=launch_date.isoformat() if launch_date else None,
        min_price_aed=data.get("min_price_aed"),
        max_price_aed=data.get("max_price_aed"),
        units_in_sale=data.get("units_in_sale"),
        units_sold=sales_inventory.get("units_sold"),
        units_unsold=sales_inventory.get("units_unsold"),
        total_units=sales_inventory.get("total_units"),
        registered_units=sales_inventory.get("registered_units"),
        sales_absorption_pct=sales_inventory.get("sales_absorption_pct"),
        sales_inventory_source=sales_inventory.get("source"),
        unit_types=_as_list(data.get("unit_types")),
        overview_excerpt=data.get("overview_excerpt"),
        cover_image_url=data.get("cover_image_url"),
        developer_logo_url=data.get("developer_logo_url"),
        image_count=data.get("image_count") or 0,
        floorplan_count=data.get("floorplan_count") or 0,
        brochure_count=data.get("brochure_count") or 0,
        project_score=project_score,
        project_risk_badge=_project_risk_badge(project_score, score_coverage),
        score_coverage_pct=score_coverage,
        developer_score=developer.proprietary_score if developer else None,
        developer_risk_badge=developer.risk_badge if developer else None,
        risk_indicators=risk_indicators,
    )


async def _developer_score_lookup(
    db: AsyncSession | None = None,
) -> dict[str, DeveloperRankingResponse]:
    del db
    return {}


async def _dld_project_signal_lookup(
    catalogue_rows: list[dict[str, Any]],
) -> dict[int, dict[str, Any]]:
    if not catalogue_rows:
        return {}
    area_keys = sorted(
        {
            str(row.get("area_name") or "").strip().lower()
            for row in catalogue_rows
            if row.get("area_name")
        }
    )
    try:
        anchor_date = await _latest_sales_date() or date.today()
        rows = await queries.fetch_dld_project_signals(
            {"area_count": len(area_keys), "areas": area_keys}
        )
    except Exception as exc:
        logger.warning("DLD project signal lookup failed for supply catalogue: %s", exc)
        return {}

    matches: dict[int, dict[str, Any]] = {}
    for catalogue in catalogue_rows:
        best_score = 0.0
        best_row: dict[str, Any] | None = None
        for candidate in rows:
            score = _project_match_score(catalogue, candidate)
            if score > best_score:
                best_score = score
                best_row = candidate
        if best_row and best_score >= 0.42:
            matches[int(catalogue["project_id"])] = {
                **best_row,
                "match_score": round(best_score, 2),
                "anchor_date": anchor_date,
            }
    return matches


async def list_supply_catalogue_projects(
    db: AsyncSession | None = None,
    limit: int = 48,
    offset: int = 0,
    search: str | None = None,
    area: str | None = None,
    status: str | None = None,
    developer: str | None = None,
    sort: str = "recommended",
    include_past: bool = False,
) -> SupplyCatalogueProjectsResponse:
    del db
    filters = dict(
        search=search, area=area, status=status, developer=developer, include_past=include_past
    )
    rows = await queries.fetch_catalogue_rows(filters, limit=limit, offset=offset, sort=sort)
    count_rows = await queries.fetch_catalogue_count(filters)
    row_data = [_catalogue_data_from_clickhouse_row(row) for row in rows]
    developer_scores = await _developer_score_lookup()
    dld_signals = await _dld_project_signal_lookup(row_data)
    projects = [_card_from_row(row, developer_scores, dld_signals) for row in row_data]
    base_filters = dict(
        search=None, area=None, status=None, developer=None, include_past=include_past
    )
    developer_filters = dict(
        search=search, area=area, status=status, developer=None, include_past=include_past
    )
    return SupplyCatalogueProjectsResponse(
        projects=projects,
        total=int(count_rows[0]["total"]) if count_rows else 0,
        limit=limit,
        offset=offset,
        areas=await _catalogue_facet_values("Area_name", base_filters, 40),
        statuses=await _catalogue_facet_values("Status", base_filters, 20),
        developers=await _catalogue_facet_values("Developers_name", developer_filters, 120),
    )


async def get_supply_catalogue_project(
    db: AsyncSession | None, project_id: int
) -> SupplyCatalogueProjectDetailResponse:
    del db
    rows = await queries.fetch_catalogue_detail({"project_id": str(project_id)})
    if not rows:
        raise HTTPException(status_code=404, detail="Supply catalogue project not found")

    data = _catalogue_data_from_clickhouse_row(rows[0])
    developer_scores = await _developer_score_lookup()
    dld_signals = await _dld_project_signal_lookup([data])
    card = _card_from_row(data, developer_scores, dld_signals)
    scraped_at = data.get("scraped_at")

    return SupplyCatalogueProjectDetailResponse(
        **card.model_dump(),
        overview=data.get("overview"),
        coordinates=data.get("coordinates"),
        units=[
            SupplyCatalogueUnitResponse.model_validate(item) for item in _as_list(data.get("units"))
        ],
        payment_plan=[
            SupplyCataloguePaymentStepResponse.model_validate(item)
            for item in _as_list(data.get("payment_plan"))
        ],
        facilities=[str(item) for item in _as_list(data.get("facilities")) if item],
        buildings=[
            SupplyCatalogueBuildingResponse.model_validate(item)
            for item in _as_list(data.get("buildings"))
        ],
        nearby_points=[
            SupplyCatalogueNearbyPointResponse.model_validate(item)
            for item in _as_list(data.get("nearby_points"))
        ],
        gallery=data.get("gallery", [])[:18],
        floorplans=data.get("floorplans", [])[:12],
        brochures=data.get("brochures", [])[:6],
        master_plans=data.get("master_plans", [])[:4],
        source_metadata={
            "source": "ph_box ClickHouse reelly_projects_bronze",
            "source_run_id": data.get("source_run_id"),
            "scraped_at": scraped_at.isoformat()
            if isinstance(scraped_at, datetime)
            else (_text_or_none(scraped_at)),
        },
        financial_risk_metrics=_financial_risk_metrics(),
        estimated_project_irr="~",
    )


async def list_matching_inventory(area: str) -> list[dict[str, Any]]:
    return await queries.fetch_matching_inventory({"area": area})


async def get_catalog_freshness() -> datetime | None:
    try:
        rows = await queries.fetch_catalog_freshness({})
        return _freshness_datetime(rows[0].get("refreshed") if rows else None)
    except Exception:
        return None
