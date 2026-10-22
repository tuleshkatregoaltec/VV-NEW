import asyncio
import logging
from typing import Any, List, Optional

from app.core.observability import operation
from app.project import queries
from app.project.mapping import _float_or_none, _room_sort_key
from app.project.models import (
    BuildingDetailResponse,
    BuildingInfo,
    BuildingUnitType,
    GeoPin,
    GeoPolygon,
    MasterProjectResponse,
    MasterProjectSummary,
    ProjectDetailResponse,
    ProjectMarketSnapshotResponse,
    ProjectSearchResult,
    ProjectUnitComposition,
)
from app.project.radar_queries import REELLY_PROJECT_ID_OFFSET

logger = logging.getLogger(__name__)


async def list_all_projects() -> List[ProjectSearchResult]:
    """Get all map-searchable Reelly and legacy projects."""

    result = await queries.fetch_project_catalogue({})

    return [
        ProjectSearchResult(
            project_id=p["project_id"],
            project_name=p["project_name_en"],
            area_name=p.get("area_name_en", "Unknown"),
            developer_name=p.get("developer_name") or None,
            master_project_en=p.get("master_project_en") or None,
            latitude=p["latitude"],
            longitude=p["longitude"],
        )
        for p in result
    ]


async def get_project_details(project_id: int) -> Optional[ProjectDetailResponse]:
    """Get full project details including buildings and unit composition."""
    # Radar/search synthetic ids are negative. Reelly ids occupy a reserved,
    # reversible range; smaller values remain legacy coordinate property ids.
    if project_id < 0:
        property_id = abs(project_id)
        if property_id >= REELLY_PROJECT_ID_OFFSET:
            reelly_id = property_id - REELLY_PROJECT_ID_OFFSET
            reelly_rows = await queries.fetch_reelly_project({"project_id": str(reelly_id)})
            if not reelly_rows:
                return None
            reelly_project = reelly_rows[0]
            completion_time = reelly_project.get("completion_time")
            completion_date = (
                completion_time.date()
                if completion_time is not None and hasattr(completion_time, "date")
                else None
            )
            return ProjectDetailResponse(
                project_id=project_id,
                project_name=reelly_project["project_name"],
                project_number=str(reelly_id),
                master_project_en=None,
                area_id=None,
                area_name=reelly_project.get("area_name") or "Unknown",
                latitude=float(reelly_project["latitude"]),
                longitude=float(reelly_project["longitude"]),
                developer_id=None,
                developer_name=reelly_project.get("developer_name") or None,
                master_developer_id=None,
                master_developer_name=None,
                no_of_buildings=None,
                no_of_units=None,
                no_of_villas=None,
                no_of_lands=None,
                project_start_date=None,
                project_end_date=completion_date,
                completion_date=completion_date,
                percent_completed=_float_or_none(reelly_project.get("readiness_progress")),
                project_status=reelly_project.get("sale_status") or None,
                unit_composition=[],
                buildings=[],
                geo_pins=[],
            )

        geo_project_rows = await queries.fetch_legacy_project({"property_id": property_id})
        if not geo_project_rows:
            return None

        geo_project = geo_project_rows[0]
        geo_rows = await queries.fetch_legacy_project_geography(
            {"project_name": geo_project["project_name_en"]}
        )
        geo_pins = [
            GeoPin(
                building_name=row["building_name_en"] or f"Building {row['property_id']}",
                project_name=row["project_name_en"],
                latitude=float(row["lat"]),
                longitude=float(row["lng"]),
                property_id=int(row["property_id"]),
                point_type="building",
            )
            for row in geo_rows
        ]
        buildings = [
            BuildingInfo(
                property_id=int(row["property_id"]),
                building_number=None,
                building_name=row["building_name_en"] or f"Building {row['property_id']}",
                floors=None,
            )
            for row in geo_rows
        ]
        return ProjectDetailResponse(
            project_id=project_id,
            project_name=geo_project["project_name_en"],
            project_number=None,
            master_project_en=None,
            area_id=None,
            area_name=geo_project.get("area_name_en") or "Unknown",
            latitude=float(geo_project["latitude"]),
            longitude=float(geo_project["longitude"]),
            developer_id=None,
            developer_name=geo_project.get("developer_name") or None,
            master_developer_id=None,
            master_developer_name=None,
            no_of_buildings=len(buildings) or None,
            no_of_units=None,
            no_of_villas=None,
            no_of_lands=None,
            project_start_date=None,
            project_end_date=None,
            completion_date=None,
            percent_completed=None,
            project_status=None,
            unit_composition=[],
            buildings=buildings,
            geo_pins=geo_pins,
        )

    project_result = await queries.fetch_project_details({"project_id": project_id})

    if not project_result:
        return None

    project = project_result[0]

    query_tasks = [
        queries.fetch_project_buildings({"project_id": project_id}),
        queries.fetch_project_composition({"project_id": project_id}),
        queries.fetch_project_geography({"project_name": project["project_name_en"]}),
    ]
    results = await asyncio.gather(*query_tasks)
    buildings_result = results[0]
    composition_result = results[1]
    geo_result = results[2]

    buildings = [
        BuildingInfo(
            property_id=b["property_id"],
            building_number=b.get("building_number") or None,
            building_name=b.get("building_number") or f"Building {b['property_id']}",
            floors=b.get("floors") or None,
        )
        for b in buildings_result
    ]

    unit_composition: List[ProjectUnitComposition] = []
    total_units = sum(row["count"] for row in composition_result)
    if total_units > 0:
        for row in composition_result:
            unit_composition.append(
                ProjectUnitComposition(
                    property_sub_type=row["property_sub_type_en"],
                    count=row["count"],
                    percentage=round((row["count"] / total_units) * 100, 2),
                    avg_area_sqm=float(row["avg_area_sqm"]) if row["avg_area_sqm"] else None,
                )
            )

    # Build normalized lookup: building name → property_id for geo pin matching
    def _norm(s: str) -> str:
        return "".join(c.lower() for c in s if c.isalnum())

    building_by_norm = {
        _norm(b.building_name or ""): b.property_id for b in buildings if b.building_name
    }

    geo_pins = [
        GeoPin(
            building_name=r["building_name_en"],
            project_name=r["project_name_en"],
            latitude=r["lat"],
            longitude=r["lng"],
            property_id=building_by_norm.get(_norm(r["building_name_en"])) or None,
            point_type="building",
        )
        for r in geo_result
    ]

    # Derive project center from geo pins when ch_geo_projects has no entry
    proj_lat = float(project["latitude"])
    proj_lng = float(project["longitude"])
    if proj_lat in (0.0, 25.2048) and geo_pins:
        proj_lat = sum(p.latitude for p in geo_pins) / len(geo_pins)
        proj_lng = sum(p.longitude for p in geo_pins) / len(geo_pins)

    raw_polygon = project.get("area_polygon") or []
    area_polygon = GeoPolygon(coordinates=raw_polygon) if raw_polygon else None

    return ProjectDetailResponse(
        project_id=project["project_id"],
        project_name=project["project_name_en"] or f"Project {project['project_id']}",
        project_number=project.get("project_number"),
        master_project_en=project.get("master_project_en") or None,
        area_id=project.get("area_id"),
        area_name=project.get("area_name_en", "Unknown"),
        latitude=proj_lat,
        longitude=proj_lng,
        area_polygon=area_polygon,
        developer_id=project.get("developer_id"),
        developer_name=project.get("developer_name") or None,
        master_developer_id=project.get("master_developer_id"),
        master_developer_name=project.get("master_developer_name") or None,
        no_of_buildings=project.get("no_of_buildings"),
        no_of_units=project.get("no_of_units"),
        no_of_villas=project.get("no_of_villas"),
        no_of_lands=project.get("no_of_lands"),
        project_start_date=project.get("project_start_date"),
        project_end_date=project.get("project_end_date"),
        completion_date=project.get("completion_date"),
        percent_completed=project.get("percent_completed"),
        project_status=project.get("project_status"),
        unit_composition=unit_composition,
        buildings=buildings,
        geo_pins=geo_pins,
    )


async def get_master_project(master_name: str) -> Optional[MasterProjectResponse]:
    """Get aggregated data for a master project and all its sub-projects."""

    sub_projects_result = await queries.fetch_master_projects({"name": master_name})

    if not sub_projects_result:
        return None

    project_names = [p["project_name_en"] for p in sub_projects_result]
    project_ids = [p["project_id"] for p in sub_projects_result]
    area_name = sub_projects_result[0].get("area_name_en", "Unknown")
    developer_name = sub_projects_result[0].get("developer_name") or None

    geo_result, composition_result = await asyncio.gather(
        queries.fetch_master_geography({"names": project_names}),
        queries.fetch_master_composition({"ids": project_ids}),
    )

    geo_pins = [
        GeoPin(
            building_name=r["building_name_en"],
            project_name=r["project_name_en"],
            latitude=r["lat"],
            longitude=r["lng"],
        )
        for r in geo_result
    ]

    total_units_comp = sum(r["count"] for r in composition_result)
    unit_composition: List[ProjectUnitComposition] = []
    if total_units_comp > 0:
        for row in composition_result:
            unit_composition.append(
                ProjectUnitComposition(
                    property_sub_type=row["property_sub_type_en"],
                    count=row["count"],
                    percentage=round((row["count"] / total_units_comp) * 100, 2),
                    avg_area_sqm=float(row["avg_area_sqm"]) if row["avg_area_sqm"] else None,
                )
            )

    projects = [
        MasterProjectSummary(
            project_id=p["project_id"],
            project_name=p["project_name_en"],
            project_status=p.get("project_status") or None,
            percent_completed=p.get("percent_completed"),
            no_of_buildings=p.get("no_of_buildings"),
            no_of_units=p.get("no_of_units"),
        )
        for p in sub_projects_result
    ]

    total_buildings = sum((p.no_of_buildings or 0) for p in projects)
    total_units = sum((p.no_of_units or 0) for p in projects)

    return MasterProjectResponse(
        master_name=master_name,
        area_name=area_name,
        developer_name=developer_name,
        total_projects=len(projects),
        total_buildings=total_buildings,
        total_units=total_units,
        projects=projects,
        geo_pins=geo_pins,
        unit_composition=unit_composition,
    )


async def get_building_details(property_id: int) -> Optional[BuildingDetailResponse]:
    """Get building metadata and unit composition by bedroom type."""

    building_result = await queries.fetch_building({"property_id": property_id})
    if not building_result:
        return None

    b = building_result[0]

    units_result = await queries.fetch_building_units({"property_id": property_id})

    total_units = sum(row["count"] for row in units_result)
    unit_composition = [
        BuildingUnitType(
            rooms_en=row["rooms_en"],
            count=row["count"],
            percentage=round((row["count"] / total_units) * 100, 1) if total_units else 0,
            avg_area_sqm=float(row["avg_area_sqm"]) if row["avg_area_sqm"] else None,
        )
        for row in units_result
    ]
    unit_composition.sort(key=lambda row: _room_sort_key(row.rooms_en))

    return BuildingDetailResponse(
        property_id=b["property_id"],
        building_number=b.get("building_number") or None,
        floors=b.get("floors") or None,
        project_id=b["project_id"],
        project_name=b.get("project_name_en") or f"Project {b['project_id']}",
        area_name=b.get("area_name_en") or "Unknown",
        total_units=total_units,
        unit_composition=unit_composition,
    )


async def get_project_market_snapshot(project_id: int) -> Optional[ProjectMarketSnapshotResponse]:
    result = await queries.fetch_market_snapshot({"project_id": project_id})
    if not result:
        return None
    row = result[0]
    return ProjectMarketSnapshotResponse(
        project_id=row["project_id"],
        project_name=row.get("project_name_en") or f"Project {row['project_id']}",
        area_name=row.get("area_name_en") or "Unknown",
        developer_name=row.get("developer_name") or None,
        master_project_en=row.get("master_project_en") or None,
        completion_status=row.get("completion_status") or "planned",
        pipeline_status=row.get("pipeline_status") or "unscheduled",
        estimated_delivery_confidence=float(row.get("estimated_delivery_confidence") or 0),
        sales_transaction_count_12m=row.get("sales_transaction_count_12m", 0),
        rental_contract_count_12m=row.get("rental_contract_count_12m", 0),
        total_sales_volume_12m=float(row.get("total_sales_volume_12m") or 0),
        median_sale_price=row.get("median_sale_price"),
        median_annual_rent=row.get("median_annual_rent"),
        gross_yield_pct=row.get("gross_yield_pct"),
        avg_sale_price_sqm_12m=row.get("avg_sale_price_sqm_12m"),
        avg_rent_price_sqm_12m=row.get("avg_rent_price_sqm_12m"),
    )


@operation
async def get_similar_projects(project_name: str, limit: int = 5) -> dict[str, Any]:
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

    try:
        ref_rows = await queries.fetch_similar_project_reference(
            {"project_name": f"%{project_name}%"}
        )

        if not ref_rows:
            return {
                "success": False,
                "error": f"No data found for project '{project_name}'",
            }

        ref_row = ref_rows[0]
        ref_project = ref_row["master_project_en"]
        ref_area = ref_row["area_name_en"]
        ref_type = ref_row["property_type_en"]
        ref_price = ref_row["median_price"]
        ref_area_sqft = ref_row["median_area"]

        # Find similar projects

        similar_rows = await queries.fetch_similar_projects(
            {
                "ref_price": ref_price,
                "ref_project": ref_project,
                "ref_type": ref_type,
                "limit": limit,
            },
        )

        similar_projects = []
        for row in similar_rows:
            similar_projects.append(
                {
                    "project": row["master_project_en"],
                    "area": row["area_name_en"],
                    "property_type": row["property_type_en"],
                    "median_price_aed": row["median_price"],
                    "median_area_sqft": row["median_area"],
                    "transaction_count": row["transaction_count"],
                    "price_difference_aed": row["price_diff"],
                }
            )

        return {
            "success": True,
            "reference_project": {
                "name": ref_project,
                "area": ref_area,
                "property_type": ref_type,
                "median_price_aed": ref_price,
                "median_area_sqft": ref_area_sqft,
            },
            "similar_projects": similar_projects,
        }
    except Exception as e:
        logger.exception(f"Error executing get_similar_projects for {project_name}")
        return {"success": False, "error": str(e)}
