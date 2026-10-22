import time
from typing import Any

from app.plots import queries
from app.plots.evidence import _annotate_dda_development_evidence
from app.plots.geometry import (
    _bounds_intersects,
    _build_plot_warnings,
    _build_setbacks,
    _convex_hull,
    _coordinate_bounds,
    _float_or_none,
    _parse_coordinates,
    _parse_land_use,
    _parse_notes,
    _parse_plot_coordinates,
    infer_usage_from_plot,
    land_use_labels,
)
from app.plots.models import DDAOutlineFeature, DDAPlotFeature, Plot, PlotDetails
from app.plots.valuation import (
    _dda_plot_valuation,
    _dda_transaction_valuation_lookup,
    _empty_dda_valuation,
)

DDA_GEOMETRY_CACHE_TTL_SECONDS = 60 * 60

_dda_geometry_cache: tuple[float, list[dict[str, Any]]] | None = None


async def _get_dda_plot_geometries() -> list[dict[str, Any]]:
    global _dda_geometry_cache

    now = time.monotonic()
    if _dda_geometry_cache and now - _dda_geometry_cache[0] < DDA_GEOMETRY_CACHE_TTL_SECONDS:
        return _dda_geometry_cache[1]

    rows = await queries.fetch_plot_geometries({})
    geometries: list[dict[str, Any]] = []
    for row in rows:
        coordinates = _parse_plot_coordinates(row.get("coordinates", ""))
        if len(coordinates) < 3:
            continue
        geometries.append(
            {
                "plot_number": row.get("plot_number") or "",
                "project_name": row.get("project_name") or "",
                "community_name": row.get("community_name") or "",
                "plot_area_sqm": _float_or_none(row.get("plot_area_sqm")) or 0.0,
                "max_gfa_sqm": _float_or_none(row.get("max_gfa_sqm")),
                "max_height": row.get("max_height") or None,
                "max_coverage": row.get("max_coverage") or None,
                "site_plan_issue_date": row.get("site_plan_issue_date"),
                "site_plan_expiry_date": row.get("site_plan_expiry_date"),
                "land_use": row.get("land_use") or None,
                "gfa_type": row.get("gfa_type") or None,
                "is_verified": bool(row.get("is_verified") or False),
                "coordinates": coordinates,
                "bounds": _coordinate_bounds(coordinates),
                "land_use_summary": land_use_labels(row.get("land_use"), limit=4),
                "linked_asset_count": int(row.get("linked_asset_count") or 0),
                "linked_physical_asset_count": int(row.get("linked_physical_asset_count") or 0),
            }
        )

    _annotate_dda_development_evidence(geometries)
    _dda_geometry_cache = (now, geometries)
    return geometries


async def get_dda_outlines_for_bounds(
    *,
    west: float,
    south: float,
    east: float,
    north: float,
    zoom: float,
    limit: int = 160,
) -> list[DDAOutlineFeature]:
    """Return coarse DDA outlines for low-zoom planning context."""
    rows = await _get_dda_plot_geometries()
    group_by_project = zoom >= 12
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        coords = row["coordinates"]
        if not _bounds_intersects(
            row["bounds"],
            west=west,
            south=south,
            east=east,
            north=north,
        ):
            continue

        project_name = str(row.get("project_name") or "").strip()
        community_name = str(row.get("community_name") or "").strip()
        name = project_name if group_by_project and project_name else community_name
        if not name:
            continue

        group = groups.setdefault(
            name,
            {
                "name": name,
                "kind": "project" if group_by_project and project_name else "community",
                "points": [],
                "plot_count": 0,
                "total_plot_area_sqm": 0.0,
                "total_gfa_sqm": 0.0,
                "land_uses": [],
                "development_counts": {},
            },
        )
        group["points"].extend(coords[:-1] if coords[0] == coords[-1] else coords)
        group["plot_count"] += 1
        group["total_plot_area_sqm"] += row["plot_area_sqm"]
        group["total_gfa_sqm"] += row["max_gfa_sqm"] or 0.0
        group["land_uses"].extend(row["land_use_summary"][:2])
        development_status = str(row.get("development_status") or "no_observed_asset")
        group["development_counts"][development_status] = (
            group["development_counts"].get(development_status, 0) + 1
        )

    min_plot_count = 8 if zoom < 12 else 3
    outlines: list[DDAOutlineFeature] = []
    for group in sorted(
        groups.values(),
        key=lambda item: (item["plot_count"], item["total_plot_area_sqm"]),
        reverse=True,
    ):
        if group["plot_count"] < min_plot_count:
            continue
        hull = _convex_hull(group["points"])
        if len(hull) < 4:
            continue
        land_uses = list(dict.fromkeys(group["land_uses"]))[:4]
        development_counts = group["development_counts"]
        developed_count = int(development_counts.get("developed_confirmed", 0)) + int(
            development_counts.get("developed_probable", 0)
        )
        outlines.append(
            DDAOutlineFeature(
                name=group["name"],
                kind=group["kind"],
                plot_count=group["plot_count"],
                total_plot_area_sqm=round(group["total_plot_area_sqm"], 2),
                total_gfa_sqm=round(group["total_gfa_sqm"], 2),
                coordinates=hull,
                land_use_summary=land_uses,
                developed_confirmed_count=int(development_counts.get("developed_confirmed", 0)),
                developed_probable_count=int(development_counts.get("developed_probable", 0)),
                planned_unbuilt_count=int(development_counts.get("planned_unbuilt_candidate", 0)),
                no_observed_asset_count=int(development_counts.get("no_observed_asset", 0)),
                non_developable_count=int(development_counts.get("non_developable", 0)),
                developed_share=round(developed_count / max(1, group["plot_count"]) * 100, 1),
            )
        )
        if len(outlines) >= limit:
            break
    return outlines


async def get_dda_plots_for_area(
    area: str | None = None,
    limit: int = 300,
    west: float | None = None,
    south: float | None = None,
    east: float | None = None,
    north: float | None = None,
    include_valuations: bool = False,
) -> list[DDAPlotFeature]:
    """Return DDA GIS plot polygons for a community or current map bounds."""
    has_bounds = all(value is not None for value in (west, south, east, north))
    area_query = area.strip().lower() if area else ""
    rows = await _get_dda_plot_geometries()
    target_rows: list[dict[str, Any]] = []
    for row in rows:
        if (
            area_query
            and area_query not in " ".join([row["community_name"], row["project_name"]]).lower()
        ):
            continue
        if has_bounds and not _bounds_intersects(
            row["bounds"],
            west=west or 0,
            south=south or 0,
            east=east or 0,
            north=north or 0,
        ):
            continue
        target_rows.append(row)
        if len(target_rows) >= limit:
            break

    valuations = (
        await _dda_transaction_valuation_lookup(target_rows, rows) if include_valuations else {}
    )
    results: list[DDAPlotFeature] = []
    for row in target_rows:
        if include_valuations:
            valuation = valuations.get(str(row.get("plot_number") or "")) or _dda_plot_valuation(
                row
            )
        else:
            valuation = _empty_dda_valuation("Valuation deferred for bulk map rendering.")
        results.append(
            DDAPlotFeature(
                plot_number=row["plot_number"],
                project_name=row["project_name"],
                community_name=row["community_name"],
                plot_area_sqm=row["plot_area_sqm"],
                coordinates=row["coordinates"],
                land_use_summary=row["land_use_summary"],
                max_gfa_sqm=row["max_gfa_sqm"],
                max_height=row["max_height"],
                max_coverage=row["max_coverage"],
                land_use=row["land_use"],
                gfa_type=row["gfa_type"],
                is_verified=row["is_verified"],
                development_status=row.get("development_status", "no_observed_asset"),
                development_confidence=row.get("development_confidence", "low"),
                development_method=row.get("development_method", "no_observed_asset"),
                development_note=row.get("development_note"),
                linked_asset_count=int(row.get("linked_asset_count") or 0),
                linked_physical_asset_count=int(row.get("linked_physical_asset_count") or 0),
                subdivision_plot_count=int(row.get("subdivision_plot_count") or 0),
                subdivision_confirmed_count=int(row.get("subdivision_confirmed_count") or 0),
                **valuation,
            )
        )
    return results


async def search_plots(
    q: str = "",
    community: str = "",
    min_plot_area_sqm: float | None = None,
    max_plot_area_sqm: float | None = None,
    offset: int = 0,
    limit: int = 200,
) -> list[Plot]:
    rows = await queries.fetch_plots(
        community=community,
        limit=limit,
        max_plot_area_sqm=max_plot_area_sqm,
        min_plot_area_sqm=min_plot_area_sqm,
        offset=offset,
        q=q,
    )
    plots: list[Plot] = []
    for row in rows:
        land_use = _parse_land_use(row.get("land_use", ""))
        plots.append(
            Plot(
                plot_number=row["plot_number"],
                community_name=row.get("community_name") or "",
                project_name=row.get("project_name") or "",
                plot_area_sqm=float(row.get("plot_area_sqm") or 0),
                max_gfa_sqm=float(row.get("max_gfa_sqm") or 0),
                max_height=row.get("max_height") or "",
                max_coverage=row.get("max_coverage") or "",
                gfa_type=row.get("gfa_type") or "",
                is_verified=bool(row.get("is_verified")),
                land_use_summary=[f"{item.type}: {item.use}" for item in land_use[:3]],
            )
        )
    return plots


async def list_communities(limit: int = 5000) -> list[str]:
    rows = await queries.fetch_communities({"limit": limit})
    return [r["community_name"] for r in rows]


async def get_plot_details(plot_number: str) -> PlotDetails | None:
    rows = await queries.fetch_plot_details({"plot_number": plot_number})
    if not rows:
        return None

    row = rows[0]
    plot_area_sqm = float(row.get("plot_area_sqm") or 0)
    max_gfa_sqm = float(row.get("max_gfa_sqm") or 0)
    far = round(max_gfa_sqm / plot_area_sqm, 2) if plot_area_sqm > 0 and max_gfa_sqm > 0 else None
    land_use = _parse_land_use(row.get("land_use", ""))
    notes = _parse_notes(row.get("general_notes", ""))
    inferred_usage = infer_usage_from_plot(
        [f"{item.type} {item.use}" for item in land_use],
        str(row.get("gfa_type") or ""),
        notes,
    )
    return PlotDetails(
        plot_number=row["plot_number"],
        community_name=row.get("community_name") or "",
        project_name=row.get("project_name") or "",
        master_developer=(row.get("master_developer") or None),
        plot_area_sqm=plot_area_sqm,
        max_gfa_sqm=max_gfa_sqm,
        max_gfa_sqft=float(row.get("max_gfa_sqft") or 0),
        max_height=row.get("max_height") or "",
        max_coverage=row.get("max_coverage") or "",
        gfa_type=row.get("gfa_type") or "",
        far=far,
        inferred_usage=inferred_usage,
        site_plan_issue_date=str(row.get("site_plan_issue_date"))
        if row.get("site_plan_issue_date")
        else None,
        site_plan_expiry_date=str(row.get("site_plan_expiry_date"))
        if row.get("site_plan_expiry_date")
        else None,
        is_verified=bool(row.get("is_verified")),
        verify_comments=(row.get("verify_comments") or None),
        land_use=land_use,
        general_notes=notes,
        setbacks=_build_setbacks(row),
        coordinates=_parse_coordinates(row.get("coordinates", "")),
        warnings=_build_plot_warnings(row, far, notes),
    )


async def get_land_plots(area: str, size_sqm: float, tolerance: float = 0.25) -> list[Plot]:
    min_plot_area_sqm = size_sqm * (1 - tolerance)
    max_plot_area_sqm = size_sqm * (1 + tolerance)
    return await search_plots(
        community=area,
        min_plot_area_sqm=min_plot_area_sqm,
        max_plot_area_sqm=max_plot_area_sqm,
        limit=50,
    )


async def search_land_plots(area: str, q: str, limit: int = 30) -> list[Plot]:
    return await search_plots(q=q, community=area, limit=limit)
