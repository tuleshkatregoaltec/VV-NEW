import math
import re
from bisect import bisect_left
from typing import Any

from app.market.reference import (
    SQM_TO_SQFT,
)
from app.plots.geometry import _float_or_none
from app.plots.queries import _dda_land_transaction_rows

DDA_VALUATION_MAX_PLOT_AREA_DELTA = 0.01

DDA_VALUATION_MIN_COMP_COUNT = 3

DDA_VALUATION_SIZE_BAND = (0.5, 1.75)


def _empty_dda_valuation(note: str) -> dict[str, Any]:
    return {
        "valuation_rate_min_aed_sqft": None,
        "valuation_rate_max_aed_sqft": None,
        "valuation_min_aed": None,
        "valuation_max_aed": None,
        "valuation_confidence": None,
        "valuation_note": note,
    }


def _community_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (value or "").lower()).strip()


def _plot_far(row: dict[str, Any]) -> float | None:
    plot_area_sqm = _float_or_none(row.get("plot_area_sqm"))
    max_gfa_sqm = _float_or_none(row.get("max_gfa_sqm"))
    if not plot_area_sqm or plot_area_sqm <= 0 or not max_gfa_sqm or max_gfa_sqm <= 0:
        return None
    return max_gfa_sqm / plot_area_sqm


def _far_band(value: float | None) -> str:
    if value is None:
        return "unknown"
    if value < 1.5:
        return "low"
    if value < 3.0:
        return "mid"
    return "development"


def _percentile(values: list[float], percentile: float) -> float | None:
    valid = sorted(value for value in values if math.isfinite(value))
    if not valid:
        return None
    if len(valid) == 1:
        return valid[0]
    position = (len(valid) - 1) * percentile
    lower_index = int(position)
    upper_index = min(lower_index + 1, len(valid) - 1)
    weight = position - lower_index
    return valid[lower_index] * (1 - weight) + valid[upper_index] * weight


def _valuation_rate_range(rates: list[float]) -> tuple[float, float] | None:
    valid = sorted(rate for rate in rates if rate > 0 and math.isfinite(rate))
    if not valid:
        return None
    if len(valid) == 1:
        rate = valid[0]
        return rate * 0.9, rate * 1.1
    if len(valid) < 4:
        return valid[0], valid[-1]
    low = _percentile(valid, 0.25)
    high = _percentile(valid, 0.75)
    if low is None or high is None:
        return None
    if low == high:
        return low * 0.95, high * 1.05
    return low, high


def _evidence_size_match(evidence: dict[str, Any], plot_area_sqm: float) -> bool:
    matched_area = _float_or_none(evidence.get("matched_plot_area_sqm"))
    if not matched_area or matched_area <= 0 or plot_area_sqm <= 0:
        return False
    min_ratio, max_ratio = DDA_VALUATION_SIZE_BAND
    ratio = matched_area / plot_area_sqm
    return min_ratio <= ratio <= max_ratio


def _dedupe_evidence(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        transaction_id = str(item.get("transaction_id") or "")
        if transaction_id and transaction_id in seen:
            continue
        if transaction_id:
            seen.add(transaction_id)
        deduped.append(item)
    return deduped


def _select_plot_valuation_evidence(
    row: dict[str, Any], community_evidence: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], str]:
    plot_number = str(row.get("plot_number") or "")
    plot_area_sqm = _float_or_none(row.get("plot_area_sqm")) or 0.0
    target_far_band = _far_band(_plot_far(row))

    same_plot = [
        item for item in community_evidence if str(item.get("plot_number") or "") == plot_number
    ]
    same_band = [
        item
        for item in community_evidence
        if item.get("far_band") == target_far_band and _evidence_size_match(item, plot_area_sqm)
    ]
    broader_band = [item for item in community_evidence if item.get("far_band") == target_far_band]

    candidates = _dedupe_evidence([*same_plot, *same_band])
    if same_plot and len(candidates) >= DDA_VALUATION_MIN_COMP_COUNT:
        return candidates, "matched plot and same-density comparable sales"
    if len(candidates) >= DDA_VALUATION_MIN_COMP_COUNT:
        return candidates, "same-community, same-density, size-matched sales"

    candidates = _dedupe_evidence([*candidates, *broader_band])
    if len(candidates) >= DDA_VALUATION_MIN_COMP_COUNT:
        return candidates, "same-community, same-density sales"

    candidates = _dedupe_evidence([*candidates, *community_evidence])
    if candidates:
        return candidates, "same-community land sales"
    return [], "no transaction evidence"


def _dda_plot_valuation(
    row: dict[str, Any],
    evidence_by_community: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    max_gfa_sqm = _float_or_none(row.get("max_gfa_sqm"))
    if not max_gfa_sqm or max_gfa_sqm <= 0:
        return _empty_dda_valuation("Max GFA not available.")

    community = _community_key(row.get("community_name"))
    community_evidence = (evidence_by_community or {}).get(community, [])
    evidence, basis = _select_plot_valuation_evidence(row, community_evidence)
    rate_range = _valuation_rate_range([float(item.get("rate_aed_sqft") or 0) for item in evidence])
    if not rate_range:
        return _empty_dda_valuation("DLD land-sale evidence unavailable for this community.")

    min_rate, max_rate = rate_range
    max_gfa_sqft = max_gfa_sqm * SQM_TO_SQFT
    confidence = "High" if len(evidence) >= 8 else "Medium" if len(evidence) >= 3 else "Low"
    note = f"{len(evidence)} DLD land sales; {basis}; rates are AED/sqft of matched DDA max GFA."
    return {
        "valuation_rate_min_aed_sqft": round(min_rate, 1),
        "valuation_rate_max_aed_sqft": round(max_rate, 1),
        "valuation_min_aed": round(max_gfa_sqft * min_rate),
        "valuation_max_aed": round(max_gfa_sqft * max_rate),
        "valuation_confidence": confidence,
        "valuation_note": note,
    }


def _dda_plot_indexes(
    rows: list[dict[str, Any]],
) -> dict[str, dict[str, list[Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        community = _community_key(row.get("community_name"))
        plot_area_sqm = _float_or_none(row.get("plot_area_sqm"))
        max_gfa_sqm = _float_or_none(row.get("max_gfa_sqm"))
        if not community or not plot_area_sqm or plot_area_sqm <= 0:
            continue
        if not max_gfa_sqm or max_gfa_sqm <= 0:
            continue
        plot = {
            "plot_number": row.get("plot_number"),
            "plot_area_sqm": plot_area_sqm,
            "max_gfa_sqm": max_gfa_sqm,
            "far": max_gfa_sqm / plot_area_sqm,
        }
        grouped.setdefault(community, []).append(plot)

    indexes: dict[str, dict[str, list[Any]]] = {}
    for community, plots in grouped.items():
        sorted_plots = sorted(plots, key=lambda item: item["plot_area_sqm"])
        indexes[community] = {
            "areas": [plot["plot_area_sqm"] for plot in sorted_plots],
            "plots": sorted_plots,
        }
    return indexes


def _nearest_dda_plot_by_area(
    index: dict[str, list[Any]], transaction_area_sqm: float
) -> dict[str, Any] | None:
    areas = index["areas"]
    plots = index["plots"]
    if not areas:
        return None
    insert_at = bisect_left(areas, transaction_area_sqm)
    candidates = []
    if insert_at < len(plots):
        candidates.append(plots[insert_at])
    if insert_at > 0:
        candidates.append(plots[insert_at - 1])
    if not candidates:
        return None
    return min(candidates, key=lambda plot: abs(plot["plot_area_sqm"] - transaction_area_sqm))


def _transaction_evidence_by_community(
    transaction_rows: list[dict[str, Any]],
    plot_indexes: dict[str, dict[str, list[Any]]],
) -> dict[str, list[dict[str, Any]]]:
    evidence_by_community: dict[str, list[dict[str, Any]]] = {}
    for transaction in transaction_rows:
        community = _community_key(transaction.get("area_name_en"))
        index = plot_indexes.get(community)
        transaction_area = _float_or_none(transaction.get("plot_area_sqm"))
        price_aed = _float_or_none(transaction.get("price_aed"))
        if not index or not transaction_area or transaction_area <= 0:
            continue
        if not price_aed or price_aed <= 0:
            continue

        matched_plot = _nearest_dda_plot_by_area(index, transaction_area)
        if not matched_plot:
            continue
        matched_area = float(matched_plot["plot_area_sqm"])
        area_delta = abs(matched_area - transaction_area) / transaction_area
        if area_delta > DDA_VALUATION_MAX_PLOT_AREA_DELTA:
            continue

        max_gfa_sqm = float(matched_plot["max_gfa_sqm"])
        rate = price_aed / (max_gfa_sqm * SQM_TO_SQFT)
        if not math.isfinite(rate) or rate <= 0:
            continue

        evidence_by_community.setdefault(community, []).append(
            {
                "transaction_id": transaction.get("transaction_id"),
                "transaction_date": transaction.get("instance_date"),
                "plot_number": matched_plot.get("plot_number"),
                "matched_plot_area_sqm": matched_area,
                "matched_max_gfa_sqm": max_gfa_sqm,
                "far": matched_plot["far"],
                "far_band": _far_band(matched_plot["far"]),
                "rate_aed_sqft": rate,
                "price_aed": price_aed,
                "project_name": transaction.get("project_name_en") or "",
                "procedure_name": transaction.get("procedure_name_en") or "",
                "area_delta_pct": area_delta * 100,
            }
        )
    return evidence_by_community


async def _dda_transaction_valuation_lookup(
    target_rows: list[dict[str, Any]], all_plot_rows: list[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    community_names = {
        str(row.get("community_name") or "").strip().lower()
        for row in target_rows
        if str(row.get("community_name") or "").strip()
    }
    if not community_names:
        return {}
    plot_indexes = _dda_plot_indexes(all_plot_rows)
    transaction_rows = await _dda_land_transaction_rows(community_names)
    evidence_by_community = _transaction_evidence_by_community(transaction_rows, plot_indexes)
    return {
        str(row.get("plot_number") or ""): _dda_plot_valuation(row, evidence_by_community)
        for row in target_rows
    }
