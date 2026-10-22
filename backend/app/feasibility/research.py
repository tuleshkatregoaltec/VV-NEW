from datetime import datetime, timezone
from typing import Any

from app.feasibility import queries
from app.feasibility.models import (
    ComparableData,
    ComparableProject,
    MarketPricingRow,
    OffPlanPricingConfidence,
    OffPlanPricingRow,
    OffPlanUnitPricingBenchmark,
    RentalRateBySizeBand,
    UnitTypeData,
)
from app.feasibility.normalization import (
    COMMERCIAL_TYPES,
    RESIDENTIAL_TYPES,
    Stage,
    normalize_project_stage,
)
from app.plots.models import PlotDetails, Usage


def months_between(start: datetime, end: datetime) -> int:
    return max((end.year - start.year) * 12 + (end.month - start.month), 0)


def _label_confidence(score: float) -> str:
    if score >= 75:
        return "high"
    if score >= 45:
        return "medium"
    return "low"


def _parse_date(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def score_off_plan_row(
    row: OffPlanPricingRow,
    target_horizon_months: int,
    *,
    now: datetime | None = None,
) -> dict[str, float]:
    valuation_date = now or datetime.now(timezone.utc)
    horizon_distance = abs(row.avg_months_to_completion - target_horizon_months)
    sample_score = min(row.transaction_count / 15, 1.0)
    horizon_score = max(0.0, 1 - (horizon_distance / 24))
    last_transaction = _parse_date(row.last_transaction_date)
    if last_transaction is None:
        recency_score = 0.25
    else:
        months_since_trade = months_between(last_transaction, valuation_date)
        recency_score = max(0.0, 1 - (months_since_trade / 24))
    confidence_score = round(
        ((sample_score * 0.45) + (horizon_score * 0.35) + (recency_score * 0.20)) * 100,
        1,
    )
    return {
        "sample_score": round(sample_score * 100, 1),
        "horizon_score": round(horizon_score * 100, 1),
        "recency_score": round(recency_score * 100, 1),
        "confidence_score": confidence_score,
    }


def build_off_plan_pricing_confidence(
    rows: list[OffPlanPricingRow],
    target_horizon_months: int,
    *,
    now: datetime | None = None,
) -> OffPlanPricingConfidence | None:
    if not rows:
        return None
    scored_rows = [(row, score_off_plan_row(row, target_horizon_months, now=now)) for row in rows]
    scored_rows.sort(key=lambda item: (-item[1]["confidence_score"], -item[0].transaction_count))
    top_rows = scored_rows[: min(3, len(scored_rows))]
    weight_total = sum(max(row.transaction_count, 1) for row, _ in top_rows)
    sample_score = round(
        sum(scores["sample_score"] * max(row.transaction_count, 1) for row, scores in top_rows)
        / weight_total,
        1,
    )
    horizon_score = round(
        sum(scores["horizon_score"] * max(row.transaction_count, 1) for row, scores in top_rows)
        / weight_total,
        1,
    )
    recency_score = round(
        sum(scores["recency_score"] * max(row.transaction_count, 1) for row, scores in top_rows)
        / weight_total,
        1,
    )
    confidence_score = round(
        sum(scores["confidence_score"] * max(row.transaction_count, 1) for row, scores in top_rows)
        / weight_total,
        1,
    )
    matched_transactions = sum(row.transaction_count for row, _ in top_rows)
    return OffPlanPricingConfidence(
        target_horizon_months=target_horizon_months,
        matched_transactions=matched_transactions,
        confidence_score=confidence_score,
        confidence_label=_label_confidence(confidence_score),  # type: ignore[arg-type]
        sample_score=sample_score,
        horizon_score=horizon_score,
        recency_score=recency_score,
        note=(
            f"Best off-plan evidence covers {matched_transactions} transactions across "
            f"{len(top_rows)} bucket(s)."
        ),
    )


def build_off_plan_unit_benchmarks(
    rows: list[OffPlanPricingRow],
    target_horizon_months: int,
    fallback_prices: dict[str, float],
    *,
    now: datetime | None = None,
) -> list[OffPlanUnitPricingBenchmark]:
    benchmarks: list[OffPlanUnitPricingBenchmark] = []
    unit_types = sorted({row.unit_type for row in rows} | set(fallback_prices.keys()))
    for unit_type in unit_types:
        unit_rows = [row for row in rows if row.unit_type == unit_type]
        if unit_rows:
            scored = [
                (row, score_off_plan_row(row, target_horizon_months, now=now)) for row in unit_rows
            ]
            scored.sort(key=lambda item: (-item[1]["confidence_score"], -item[0].transaction_count))
            selected, scores = scored[0]
            benchmarks.append(
                OffPlanUnitPricingBenchmark(
                    unit_type=unit_type,
                    selected_horizon_bucket=selected.horizon_bucket,
                    avg_months_to_completion=selected.avg_months_to_completion,
                    transaction_count=selected.transaction_count,
                    selected_price_sqm=selected.median_price_sqm,
                    fallback_used=False,
                    confidence_score=scores["confidence_score"],
                    confidence_label=_label_confidence(scores["confidence_score"]),  # type: ignore[arg-type]
                    sample_score=scores["sample_score"],
                    horizon_score=scores["horizon_score"],
                    recency_score=scores["recency_score"],
                    last_transaction_date=selected.last_transaction_date,
                    note=(
                        f"Selected {selected.horizon_bucket} for {unit_type} with "
                        f"{selected.transaction_count} transactions."
                    ),
                )
            )
        elif unit_type in fallback_prices:
            benchmarks.append(
                OffPlanUnitPricingBenchmark(
                    unit_type=unit_type,
                    selected_horizon_bucket="fallback",
                    avg_months_to_completion=float(target_horizon_months),
                    transaction_count=0,
                    selected_price_sqm=fallback_prices[unit_type],
                    fallback_used=True,
                    confidence_score=20.0,
                    confidence_label="low",
                    sample_score=0.0,
                    horizon_score=0.0,
                    recency_score=0.0,
                    last_transaction_date=None,
                    note="No matching off-plan transactions for this unit type; existing-sales fallback was used.",
                )
            )
    return benchmarks


def _dominant_type(rows: list[dict[str, Any]]) -> str | None:
    if not rows:
        return None
    return str(rows[0].get("unit_type") or rows[0].get("dominant_unit_type") or "").strip() or None


async def get_areas() -> list[str]:
    """Get distinct transaction areas with sales activity."""

    rows = await queries.fetch_areas({})
    return [r["area_name_en"] for r in rows]


async def resolve_market_area(name: str) -> str:
    if not name:
        return name

    rows = await queries.fetch_market_area({"name": name, "pattern": f"%{name}%"})
    return rows[0]["area_name_en"] if rows else name


async def get_market_pricing(
    area_name: str,
    usage: Usage,
    stage: Stage = "existing",
    days: int = 365,
) -> list[MarketPricingRow]:
    rows = await queries.fetch_market_pricing(area_name, days, stage, usage)
    return [
        MarketPricingRow(
            unit_type=row["unit_type"],
            transaction_count=int(row["transaction_count"]),
            median_price_sqm=float(row["median_price_sqm"]),
            avg_area_sqm=float(row["avg_area_sqm"]) if row.get("avg_area_sqm") else None,
        )
        for row in rows
    ]


async def get_off_plan_pricing_curve(
    area_name: str,
    usage: Usage,
    target_handover_date: str | None,
    days: int = 1095,
) -> list[OffPlanPricingRow]:
    now = datetime.now(timezone.utc)
    try:
        target_handover = (
            datetime.fromisoformat(target_handover_date).replace(tzinfo=timezone.utc)
            if target_handover_date
            else now
        )
    except ValueError:
        target_handover = now
    target_horizon_months = max(months_between(now, target_handover), 0)
    rows = await queries.fetch_off_plan_pricing_curve(area_name, days, target_horizon_months, usage)
    return [
        OffPlanPricingRow(
            unit_type=row["unit_type"],
            horizon_bucket=row["horizon_bucket"],
            avg_months_to_completion=float(row.get("avg_months_to_completion") or 0),
            transaction_count=int(row["transaction_count"]),
            median_price_sqm=float(row["median_price_sqm"]),
            avg_area_sqm=float(row["avg_area_sqm"]) if row.get("avg_area_sqm") else None,
            last_transaction_date=row.get("last_transaction_date"),
        )
        for row in rows
    ]


async def get_rental_rates_by_size_band(
    area_name: str,
    days: int = 365,
) -> list[RentalRateBySizeBand]:
    size_bands = [
        ("studio", "< 55 sqm", 0, 55),
        ("1br", "55–90 sqm", 55, 90),
        ("2br", "90–140 sqm", 90, 140),
        ("3br", "140–200 sqm", 140, 200),
        ("4br+", "> 200 sqm", 200, 99999),
    ]
    results: list[RentalRateBySizeBand] = []
    for unit_type, label, lo, hi in size_bands:
        rows = await queries.fetch_rental_size_band(
            {"area_name": area_name, "lo": float(lo), "hi": float(hi), "days": days}
        )
        if rows and int(rows[0].get("contract_count") or 0) >= 5:
            results.append(
                RentalRateBySizeBand(
                    unit_type_proxy=unit_type,
                    size_band_label=label,
                    median_rent_psm_annual=float(rows[0]["median_rent_psm_annual"] or 0),
                    median_annual_rent=float(rows[0]["median_annual_rent"] or 0),
                    contract_count=int(rows[0]["contract_count"]),
                )
            )
    return results


async def _project_unit_mix(project_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not project_ids:
        return {}

    rows = await queries.fetch_project_unit_mix({"project_ids": project_ids})
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(int(row["project_id"]), []).append(row)
    return result


async def rank_plot_comparables(
    plot: PlotDetails,
    usage: Usage,
    comparison_type: str,
    limit: int = 5,
) -> list[ComparableProject]:
    market_area = await resolve_market_area(plot.community_name)
    params = {"market_area": market_area}

    project_rows = await queries.fetch_comparable_projects(params)
    project_ids = [int(row["project_id"]) for row in project_rows]
    unit_mix_by_project = await _project_unit_mix(project_ids)

    price_by_project: dict[int, float] = {}
    dominant_type_by_project: dict[int, str] = {}
    if comparison_type == "pricing":
        for row in await queries.fetch_comparable_prices({"market_area": market_area}, usage):
            price_by_project[int(row["project_id"])] = float(row["median_price_sqm"])
            dominant_type_by_project[int(row["project_id"])] = row.get("dominant_unit_type") or ""

    scored: list[ComparableProject] = []
    target_area = plot.plot_area_sqm
    for row in project_rows:
        project_id = int(row["project_id"])
        total_land_area = (
            float(row["total_land_area_sqm"]) if row.get("total_land_area_sqm") else None
        )
        if comparison_type == "pricing" and project_id not in price_by_project:
            continue

        stage = normalize_project_stage(str(row.get("project_status") or ""))
        if comparison_type == "pipeline" and stage not in {"off_plan", "upcoming"}:
            continue

        unit_rows = unit_mix_by_project.get(project_id, [])
        dominant_type = dominant_type_by_project.get(project_id) or _dominant_type(unit_rows)
        if usage == "residential" and dominant_type and dominant_type not in RESIDENTIAL_TYPES:
            continue
        if usage == "commercial" and dominant_type and dominant_type not in COMMERCIAL_TYPES:
            continue

        score = 100.0
        rationale = [f"Same market area: {market_area}."]
        delta_pct = None
        if total_land_area and target_area > 0:
            delta_pct = abs(total_land_area - target_area) / target_area
            score -= min(delta_pct * 40, 45)
            rationale.append(f"Land area delta {round(delta_pct * 100, 1)}%.")
        else:
            score -= 25
            rationale.append("Missing derived land area.")

        if comparison_type == "pricing":
            rationale.append("Has existing transaction evidence.")
        else:
            rationale.append(f"Pipeline status: {row.get('project_status') or 'Unknown'}.")
            if stage == "upcoming":
                score += 5

        if dominant_type:
            rationale.append(f"Dominant unit type: {dominant_type}.")

        scored.append(
            ComparableProject(
                project_id=project_id,
                project_name=row.get("project_name") or f"Project {project_id}",
                area_name=row.get("area_name") or market_area,
                project_status=row.get("project_status") or None,
                comparison_type="pricing" if comparison_type == "pricing" else "pipeline",
                score=round(score, 1),
                total_land_area_sqm=round(total_land_area, 0) if total_land_area else None,
                land_area_delta_pct=round(delta_pct * 100, 1) if delta_pct is not None else None,
                no_of_buildings=int(row["no_of_buildings"])
                if row.get("no_of_buildings") is not None
                else None,
                no_of_units=int(row["no_of_units"]) if row.get("no_of_units") is not None else None,
                median_price_sqm=price_by_project.get(project_id),
                dominant_unit_type=dominant_type,
                rationale=rationale,
            )
        )

    scored.sort(key=lambda item: (-item.score, item.land_area_delta_pct or 9999))
    return scored[:limit]


async def get_comparable_data(project_ids: list[int]) -> ComparableData | None:
    if not project_ids:
        return None

    buildings_rows = await queries.fetch_comparable_buildings({"project_ids": project_ids})
    b = buildings_rows[0] if buildings_rows else {}
    no_of_buildings = int(b.get("no_of_buildings") or 0)
    avg_floors = float(b["avg_floors"]) if b.get("avg_floors") else None

    project_rows = await queries.fetch_comparable_names({"project_ids": project_ids})
    project_names = [r["project_name_en"] for r in project_rows if r.get("project_name_en")]
    area_names = [r["area_name_en"] for r in project_rows if r.get("area_name_en")]

    units_rows = await queries.fetch_comparable_units({"project_ids": project_ids})
    total_units = sum(r["cnt"] for r in units_rows)

    price_rows = (
        await queries.fetch_comparable_unit_prices({"project_names": project_names})
        if project_names
        else []
    )
    price_by_type = {r["unit_type"]: float(r["median_price_sqm"]) for r in price_rows}

    unit_mix = [
        UnitTypeData(
            type=str(r["unit_type"]),
            count=int(r["cnt"]),
            percentage=round((r["cnt"] / total_units) * 100, 1) if total_units else 0,
            avg_size_sqm=float(r["avg_size_sqm"]),
            avg_price_sqm=price_by_type.get(r["unit_type"]),
        )
        for r in units_rows
    ]

    return ComparableData(
        project_id=project_ids[0],
        project_name=", ".join(project_names[:3]) or f"Project {project_ids[0]}",
        area_name=area_names[0] if area_names else "Unknown",
        no_of_buildings=no_of_buildings,
        avg_floors=avg_floors,
        unit_mix=unit_mix,
        price_per_sqm_by_type=price_by_type,
    )


async def _resolve_area(area: str) -> str:
    if not area:
        return area

    rows = await queries.fetch_chat_area({"area": area, "pattern": f"%{area}%"})
    return rows[0]["area_name_en"] if rows else area


async def fetch_chat_research(tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
    if tool_name == "show_transactions":
        area = await _resolve_area(args.get("area", ""))
        unit_type = args.get("unit_type", "")
        usage = args.get("usage", "residential")
        stage = args.get("stage", "existing")
        days = args.get("days", 365)
        limit = min(args.get("limit", 50), 100)
        rows = await queries.fetch_chat_transactions(area, days, limit, stage, unit_type, usage)
        return {"rows": rows, "filters": {"area": area, "unit_type": unit_type}, "count": len(rows)}
    if tool_name == "show_analytics":
        area = args.get("area", "")
        project_name = args.get("project_name", "")
        usage = args.get("usage", "residential")
        stage = args.get("stage", "existing")
        days = args.get("days", 365)
        if area and not project_name:
            area = await _resolve_area(area)
        trend_rows, rooms_rows = await queries.fetch_chat_analytics(
            area, days, project_name, stage, usage
        )
        return {
            "trend": trend_rows,
            "by_rooms": rooms_rows,
            "filters": {"area": area, "project_name": project_name},
        }
    return None


async def get_area_market_research(
    area: str, usage: Usage = "residential", stage: Stage = "existing", days: int = 365
) -> dict[str, Any]:
    resolved = await queries.fetch_tool_market_area({"area": area, "pattern": f"%{area}%"})
    resolved_area = resolved[0]["area_name_en"] if resolved else area
    rows = await get_market_pricing(resolved_area, usage=usage, stage=stage, days=days)
    return {"success": True, "area": resolved_area, "data": [row.model_dump() for row in rows]}
