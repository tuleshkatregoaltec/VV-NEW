from datetime import date
from typing import Any

from app.supply.mapping import (
    _developer_logo_match_key,
    _float_or_none,
    _int_or_none,
    _parse_catalogue_date,
    _reelly_launch_date,
    _reelly_unit_total,
    _reelly_unsold_units,
    _token_overlap,
)
from app.supply.models import (
    DeveloperRankingCriterionResponse,
    DeveloperRankingResponse,
    SupplyCatalogueFinancialMetricResponse,
    SupplyCatalogueRiskIndicatorResponse,
)

DEVELOPER_SCORE_WEIGHTS = {
    "delivered_unit_scale": 20.0,
    "pipeline_unit_scale": 15.0,
    "on_time_delivery": 65.0,
}

DEVELOPER_SCORE_MIN_WEIGHT = 40.0

DEVELOPER_DELIVERED_UNIT_FULL_SCORE = 10_000

DEVELOPER_PIPELINE_UNIT_FULL_SCORE = 15_000

PROJECT_SCORE_MIN_COVERAGE = 35.0

PROJECT_RISK_WEIGHTS = {
    "developer": 35.0,
    "delivery": 25.0,
    "progress": 20.0,
    "sales": 20.0,
}


def _project_sales_inventory(
    data: dict[str, Any], dld_signal: dict[str, Any] | None
) -> dict[str, Any]:
    unsold_units = _reelly_unsold_units(data)
    registered_units = _int_or_none((dld_signal or {}).get("no_of_units"))
    reelly_total_units = _reelly_unit_total(data)
    total_units = (
        registered_units if registered_units and registered_units > 0 else reelly_total_units
    )
    sold_units = None
    absorption_pct = None

    if total_units is not None and unsold_units is not None:
        sold_units = max(total_units - unsold_units, 0)
        absorption_pct = round(min(sold_units / total_units * 100, 100.0), 1)

    if registered_units and unsold_units is not None:
        source = "Live inventory against DLD registered units."
    elif reelly_total_units and unsold_units is not None:
        source = "Live inventory against catalogue unit count."
    elif unsold_units is not None:
        source = "Live inventory count."
    elif registered_units:
        source = "DLD registered units; live inventory pending."
    else:
        source = None

    return {
        "units_sold": sold_units,
        "units_unsold": unsold_units,
        "total_units": total_units,
        "registered_units": registered_units,
        "sales_absorption_pct": absorption_pct,
        "source": source,
    }


def _format_indicator_pct(value: float | None) -> str | None:
    if value is None:
        return None
    return f"{round(value):.0f}%"


def _indicator_status(score: float | None, available: bool = True) -> str:
    if not available or score is None:
        return "Pending"
    if score >= 75:
        return "Low"
    if score >= 55:
        return "Medium"
    return "High"


def _risk_indicator(
    *,
    key: str,
    label: str,
    value: str | None,
    score: float | None,
    note: str | None = None,
    available: bool = True,
) -> SupplyCatalogueRiskIndicatorResponse:
    return SupplyCatalogueRiskIndicatorResponse(
        key=key,
        label=label,
        value=value,
        score=score,
        status=_indicator_status(score, available),
        available=available,
        note=note,
    )


def _project_risk_badge(score: float | None, coverage: float) -> str:
    if score is None or coverage < PROJECT_SCORE_MIN_COVERAGE:
        return "Review"
    if score >= 78:
        return "Low"
    if score >= 58:
        return "Medium"
    return "High"


def _score_weighted_indicators(
    indicators: list[SupplyCatalogueRiskIndicatorResponse],
) -> tuple[float | None, float]:
    weighted_score = 0.0
    available_weight = 0.0
    for indicator in indicators:
        weight = PROJECT_RISK_WEIGHTS.get(indicator.key)
        if weight is None or indicator.score is None or not indicator.available:
            continue
        weighted_score += indicator.score * weight
        available_weight += weight
    if available_weight <= 0:
        return None, 0.0
    return round(weighted_score / available_weight, 1), round(available_weight, 1)


def _launch_indicator(
    data: dict[str, Any], dld_signal: dict[str, Any] | None
) -> SupplyCatalogueRiskIndicatorResponse:
    launch_date = _reelly_launch_date(data)
    if not launch_date:
        return _risk_indicator(
            key="launch",
            label="Launch date",
            value="~",
            score=None,
            available=False,
            note="Launch date pending.",
        )

    anchor = dld_signal.get("anchor_date") if dld_signal else None
    anchor_date = anchor if isinstance(anchor, date) else date.today()
    days_since_launch = (anchor_date - launch_date).days
    if days_since_launch < -30:
        score = 72.0
        note = "Forward launch date."
    elif days_since_launch <= 180:
        score = 84.0
        note = "Recent launch window."
    elif days_since_launch <= 730:
        score = 78.0
        note = "Launch date is within the current sales cycle."
    else:
        score = 66.0
        note = "Older launch; absorption should be reviewed against remaining inventory."

    return _risk_indicator(
        key="launch",
        label="Launch date",
        value=launch_date.isoformat(),
        score=score,
        note=note,
    )


def _delivery_indicator(
    data: dict[str, Any], dld_signal: dict[str, Any] | None
) -> SupplyCatalogueRiskIndicatorResponse:
    delivery_date = _parse_catalogue_date(data.get("completion_date")) or _parse_catalogue_date(
        dld_signal.get("project_end_date") if dld_signal else None
    )
    anchor = dld_signal.get("anchor_date") if dld_signal else None
    anchor_date = anchor if isinstance(anchor, date) else date.today()
    if not delivery_date:
        return _risk_indicator(
            key="delivery",
            label="Delivery timing",
            value="~",
            score=None,
            available=False,
            note="Delivery date feed pending.",
        )
    months_left = (
        (delivery_date.year - anchor_date.year) * 12 + delivery_date.month - anchor_date.month
    )
    if months_left < 0:
        score = 48.0
        note = "Stated delivery is behind the current data window."
    elif months_left <= 6:
        score = 68.0
        note = "Near-term delivery window."
    elif months_left <= 36:
        score = 84.0
        note = "Delivery window aligns with active pipeline monitoring."
    else:
        score = 72.0
        note = "Longer-dated pipeline exposure."
    return _risk_indicator(
        key="delivery",
        label="Delivery timing",
        value=delivery_date.isoformat(),
        score=score,
        note=note,
    )


def _progress_indicator(dld_signal: dict[str, Any] | None) -> SupplyCatalogueRiskIndicatorResponse:
    progress = _float_or_none(dld_signal.get("percent_completed") if dld_signal else None)
    if progress is None:
        return _risk_indicator(
            key="progress",
            label="Construction progress",
            value="~",
            score=None,
            available=False,
            note="DLD project progress match pending.",
        )
    score = _score_value(55 + min(progress, 100) * 0.45)
    return _risk_indicator(
        key="progress",
        label="Construction progress",
        value=_format_indicator_pct(progress),
        score=score,
        note="Matched to DLD project progress where available.",
    )


def _sales_indicator(
    data: dict[str, Any], dld_signal: dict[str, Any] | None
) -> SupplyCatalogueRiskIndicatorResponse:
    inventory = _project_sales_inventory(data, dld_signal)
    sold_units = inventory.get("units_sold")
    unsold_units = inventory.get("units_unsold")
    total_units = inventory.get("total_units")
    absorption_pct = inventory.get("sales_absorption_pct")
    source = inventory.get("source")
    sale_status = str(data.get("sale_status") or "").lower()
    launch_date = _reelly_launch_date(data)

    if sold_units is None and unsold_units is None and total_units is None:
        return _risk_indicator(
            key="sales",
            label="Sales absorption",
            value="~",
            score=None,
            available=False,
            note="Sold/unsold unit feed pending.",
        )

    if sold_units is not None and unsold_units is not None and absorption_pct is not None:
        score = 50.0 + absorption_pct * 0.45
        if launch_date:
            anchor = dld_signal.get("anchor_date") if dld_signal else None
            anchor_date = anchor if isinstance(anchor, date) else date.today()
            days_since_launch = max((anchor_date - launch_date).days, 0)
            if days_since_launch <= 90 and absorption_pct < 25:
                score += 8.0
            elif days_since_launch >= 365 and absorption_pct < 35:
                score -= 12.0
        score = _score_value(score)
        value = f"{sold_units:,} sold / {unsold_units:,} unsold"
        note = source or "Uses live inventory and registered-unit fallback."
    elif unsold_units is not None:
        if "out of stock" in sale_status and unsold_units == 0:
            score = 86.0
            value = "Sold out"
            note = "Sale status marks the project as out of stock."
        else:
            score = 68.0 if unsold_units > 0 else 62.0
            value = f"{unsold_units:,} unsold"
            note = source or "Live inventory count."
    else:
        score = 58.0
        value = f"{total_units:,} registered units" if total_units else "~"
        note = source or "Registered-unit fallback until live inventory is available."

    return _risk_indicator(
        key="sales",
        label="Sales absorption",
        value=value,
        score=score,
        note=note,
    )


def _developer_indicator(
    data: dict[str, Any], developer_scores: dict[str, DeveloperRankingResponse]
) -> SupplyCatalogueRiskIndicatorResponse:
    developer = developer_scores.get(_developer_logo_match_key(data.get("developer_name")))
    if developer is None or developer.proprietary_score is None:
        return _risk_indicator(
            key="developer",
            label="Developer overlay",
            value="~",
            score=None,
            available=False,
            note="Developer score match pending.",
        )
    return _risk_indicator(
        key="developer",
        label="Developer overlay",
        value=f"{developer.proprietary_score:.1f}",
        score=developer.proprietary_score,
        note=f"{developer.risk_badge} developer risk badge.",
    )


def _financial_risk_metrics() -> list[SupplyCatalogueFinancialMetricResponse]:
    return [
        SupplyCatalogueFinancialMetricResponse(
            key="escrow_balance",
            label="Escrow balance",
            value="~",
            status="Pending",
            note="Escrow feed pending.",
        ),
        SupplyCatalogueFinancialMetricResponse(
            key="adequacy_ratio",
            label="Adequacy ratio",
            value="~",
            status="Pending",
            note="Requires balance and required-minimum feed.",
        ),
        SupplyCatalogueFinancialMetricResponse(
            key="authorised_withdrawals",
            label="Authorised withdrawals",
            value="~",
            status="Pending",
            note="Withdrawal ledger pending.",
        ),
        SupplyCatalogueFinancialMetricResponse(
            key="remaining_construction_cost",
            label="Remaining construction cost",
            value="~",
            status="Pending",
            note="Cost-to-complete feed pending.",
        ),
        SupplyCatalogueFinancialMetricResponse(
            key="compliance_record",
            label="Compliance record",
            value="~",
            status="Pending",
            note="Violation and approval feed pending.",
        ),
    ]


def _score_value(value: Any) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if parsed != parsed:
        return None
    return max(0.0, min(100.0, round(parsed, 1)))


def _ratio_score(numerator: Any, denominator: Any) -> float | None:
    denominator_value = float(denominator or 0)
    if denominator_value <= 0:
        return None
    return _score_value(float(numerator or 0) / denominator_value * 100)


def _unit_scale_score(value: Any, full_score_at: int) -> float | None:
    if value is None:
        return None
    parsed = float(value)
    if parsed != parsed:
        return None
    if parsed <= 0:
        return 0.0
    return _score_value((parsed / full_score_at) ** 0.5 * 100)


def _risk_badge(score: float | None, coverage: float) -> str:
    if score is None or coverage < DEVELOPER_SCORE_MIN_WEIGHT:
        return "Review"
    if score >= 75:
        return "Low"
    if score >= 55:
        return "Medium"
    return "High"


def _developer_rank_sort_value(row: DeveloperRankingResponse, metric: str) -> float:
    values = {
        "proprietary_score": row.proprietary_score,
        "units_sold": row.units_sold,
        "sales_volume": row.sales_volume_aed,
        "avg_price_sqft": row.avg_price_sqft_aed,
        "pipeline_units": row.pipeline_units,
        "active_projects": row.active_projects,
        "completed_projects": row.completed_projects,
        "avg_completion_pct": row.avg_completion_pct,
    }
    value = values.get(metric)
    return float(value) if value is not None else -1.0


def _developer_criteria(
    row: dict[str, Any],
) -> tuple[list[DeveloperRankingCriterionResponse], float | None, float]:
    delivered_unit_score = _unit_scale_score(
        row.get("completed_units"), DEVELOPER_DELIVERED_UNIT_FULL_SCORE
    )
    pipeline_unit_score = _unit_scale_score(
        row.get("pipeline_units"), DEVELOPER_PIPELINE_UNIT_FULL_SCORE
    )
    on_time_score = _score_value(row.get("on_time_delivery_score"))
    if on_time_score is None:
        on_time_score = _ratio_score(
            row.get("on_time_units"), row.get("completed_units_with_schedule")
        )
    definitions = [
        (
            "delivered_unit_scale",
            "Delivered unit scale",
            delivered_unit_score,
            "Completed registered units, scored with diminishing returns for proven scale.",
        ),
        (
            "pipeline_unit_scale",
            "Pipeline unit scale",
            pipeline_unit_score,
            "Active registered pipeline units, scored with diminishing returns for current depth.",
        ),
        (
            "on_time_delivery",
            "On-time delivery",
            on_time_score,
            "Completed projects delivered on or before declared end date, weighted by units.",
        ),
    ]

    criteria: list[DeveloperRankingCriterionResponse] = []
    available_weight = 0.0
    weighted_score = 0.0
    for key, label, score, note in definitions:
        weight = DEVELOPER_SCORE_WEIGHTS[key]
        available = score is not None
        if available:
            available_weight += weight
            weighted_score += score * weight
        criteria.append(
            DeveloperRankingCriterionResponse(
                key=key,
                label=label,
                weight_pct=weight,
                score=score,
                available=available,
                note=note,
            )
        )

    proprietary_score = (
        round(weighted_score / available_weight, 1)
        if available_weight >= DEVELOPER_SCORE_MIN_WEIGHT
        else None
    )
    return criteria, proprietary_score, round(available_weight, 1)


def _project_match_score(catalogue: dict[str, Any], dld_project: dict[str, Any]) -> float:
    project_score = _token_overlap(catalogue.get("project_name"), dld_project.get("project_name"))
    developer_score = _token_overlap(
        catalogue.get("developer_name"), dld_project.get("developer_name")
    )
    area_score = (
        1.0
        if _developer_logo_match_key(catalogue.get("area_name"))
        == _developer_logo_match_key(dld_project.get("area_name"))
        else _token_overlap(catalogue.get("area_name"), dld_project.get("area_name"))
    )
    return project_score * 0.6 + developer_score * 0.25 + area_score * 0.15
