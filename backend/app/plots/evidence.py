from datetime import date
from typing import Any

from app.plots import queries
from app.plots.geometry import _float_or_none, _int_value
from app.plots.models import LandSaleEvidence, Usage

DDA_VILLA_MAX_PLOT_AREA_SQM = 2_500

DDA_VILLA_SUBDIVISION_MIN_PLOTS = 5


def _dda_land_use_text(row: dict[str, Any]) -> str:
    labels = row.get("land_use_summary") or []
    return " ".join(str(label) for label in labels).lower()


def _is_small_villa_plot(row: dict[str, Any]) -> bool:
    land_use = _dda_land_use_text(row)
    return (
        0 < (_float_or_none(row.get("plot_area_sqm")) or 0) <= DDA_VILLA_MAX_PLOT_AREA_SQM
        and "residential" in land_use
        and any(token in land_use for token in ("villa", "townhouse", "attached"))
    )


def _is_non_developable_plot(row: dict[str, Any]) -> bool:
    land_use = _dda_land_use_text(row)
    buildable = any(
        token in land_use
        for token in ("residential", "commercial", "office", "retail", "hotel", "hospitality")
    )
    non_developable = any(
        token in land_use
        for token in (
            "open space",
            "landscape",
            "park",
            "utilities",
            "substation",
            "feeder pillar",
            "utility corridor",
            "transport",
            "access road",
            "sikka",
        )
    )
    return non_developable and not buildable


def _dda_subdivision_key(row: dict[str, Any]) -> str:
    project_name = str(row.get("project_name") or "").strip().lower()
    community_name = str(row.get("community_name") or "").strip().lower()
    return f"project:{project_name}" if project_name else f"community:{community_name}"


def _annotate_dda_development_evidence(rows: list[dict[str, Any]]) -> None:
    """Attach conservative development evidence, including villa-subdivision inference.

    DLD building parcel ids directly verify many DDA plots. Small villa plots are
    frequently individual cadastral parcels with no point coordinate of their own,
    so a separate probable state is used when confirmed siblings show that the
    enclosing carved-out subdivision is developed.
    """
    subdivision_stats: dict[str, dict[str, int]] = {}
    for row in rows:
        if not _is_small_villa_plot(row):
            continue
        stats = subdivision_stats.setdefault(
            _dda_subdivision_key(row),
            {"plot_count": 0, "confirmed_count": 0},
        )
        stats["plot_count"] += 1
        if _int_value(row.get("linked_physical_asset_count")) > 0:
            stats["confirmed_count"] += 1

    today_date = date.today()
    for row in rows:
        linked_assets = _int_value(row.get("linked_asset_count"))
        physical_assets = _int_value(row.get("linked_physical_asset_count"))
        subdivision = subdivision_stats.get(_dda_subdivision_key(row), {})
        subdivision_plot_count = int(subdivision.get("plot_count") or 0)
        subdivision_confirmed_count = int(subdivision.get("confirmed_count") or 0)
        row["subdivision_plot_count"] = subdivision_plot_count
        row["subdivision_confirmed_count"] = subdivision_confirmed_count

        if _is_non_developable_plot(row):
            row.update(
                development_status="non_developable",
                development_confidence="high",
                development_method="planning_land_use",
                development_note="Planning use is infrastructure, utility, transport, or open space.",
            )
        elif physical_assets > 0:
            row.update(
                development_status="developed_confirmed",
                development_confidence="high",
                development_method="parcel_id_exact",
                development_note=f"{physical_assets:,} physical DLD asset record(s) match this parcel.",
            )
        elif linked_assets > 0:
            row.update(
                development_status="developed_probable",
                development_confidence="medium",
                development_method="parcel_id_asset_record",
                development_note=f"{linked_assets:,} DLD asset record(s) match this parcel without complete physical fields.",
            )
        elif (
            _is_small_villa_plot(row)
            and subdivision_plot_count >= DDA_VILLA_SUBDIVISION_MIN_PLOTS
            and subdivision_confirmed_count > 0
        ):
            row.update(
                development_status="developed_probable",
                development_confidence="medium",
                development_method="villa_subdivision_inference",
                development_note=(
                    "Individual villa parcel inferred from its carved-out subdivision: "
                    f"{subdivision_confirmed_count:,} of {subdivision_plot_count:,} sibling plots "
                    "have confirmed development evidence."
                ),
            )
        else:
            site_plan_issue_date = row.get("site_plan_issue_date")
            site_plan_expiry_date = row.get("site_plan_expiry_date")
            active_site_plan = bool(
                site_plan_issue_date
                and (site_plan_expiry_date is None or site_plan_expiry_date >= today_date)
            )
            if active_site_plan:
                row.update(
                    development_status="planned_unbuilt_candidate",
                    development_confidence="low",
                    development_method="active_site_plan_without_asset",
                    development_note="An active site plan exists, but no developed asset is currently linked.",
                )
            else:
                row.update(
                    development_status="no_observed_asset",
                    development_confidence="low",
                    development_method="no_observed_asset",
                    development_note="No direct or subdivision-level developed asset evidence was observed.",
                )


async def get_land_sale_evidence(
    area_name: str,
    plot_area_sqm: float | None = None,
    usage: Usage | None = None,
    target_far: float | None = None,
    days: int = 1825,
    limit: int = 12,
) -> list[LandSaleEvidence]:
    rows = await queries.fetch_land_sale_evidence(
        area_name=area_name, days=days, limit=limit, plot_area_sqm=plot_area_sqm, usage=usage
    )

    def _price_per_gfa(price_aed: float, comp_plot_sqm: float) -> float | None:
        if target_far and target_far > 0 and comp_plot_sqm > 0 and price_aed > 0:
            return round(price_aed / (comp_plot_sqm * target_far), 0)
        return None

    return [
        LandSaleEvidence(
            transaction_id=row["transaction_id"],
            instance_date=str(row["instance_date"]),
            area_name=row["area_name_en"],
            project_name=row.get("project_name_en") or "",
            procedure_name=row.get("procedure_name_en") or "",
            property_usage=row.get("property_usage_en") or "",
            plot_area_sqm=float(row.get("plot_area_sqm") or 0),
            price_aed=float(row.get("price_aed") or 0),
            price_sqm=float(row.get("price_sqm") or 0),
            price_per_gfa_sqm=_price_per_gfa(
                float(row.get("price_aed") or 0),
                float(row.get("plot_area_sqm") or 0),
            ),
            nearest_landmark=row.get("nearest_landmark"),
        )
        for row in rows
    ]
