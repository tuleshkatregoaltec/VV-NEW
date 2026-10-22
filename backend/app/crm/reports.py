from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.crm import report_queries
from app.crm.models import (
    CrmLead,
    CrmPriceIndex,
    CrmPriceIndexPoint,
    CrmPropertyReportResponse,
    CrmRentalComparable,
    CrmReportAnalysis,
    CrmSaleComparable,
)
from app.crm.opportunities import _agent_signals, _location_key, get_lead_detail


def _unit_series(value: str | None) -> str | None:
    """Return an explicit unit designator such as the S in 604-S or S-602."""
    normalized = str(value or "").strip().upper()
    if not normalized:
        return None
    parts = [part for part in re.split(r"[^A-Z0-9]+", normalized) if part]
    for part in reversed(parts):
        if part.isalpha() and len(part) <= 3:
            return part
    compact_match = re.match(r"^([A-Z]{1,3})\d", normalized)
    return compact_match.group(1) if compact_match else None


def _same_building(target: str, candidate: str) -> bool:
    """Tolerate DXB building labels with a person/developer prefix or minor spelling drift."""
    target_key = _location_key(target)
    candidate_key = _location_key(candidate)
    if not target_key or not candidate_key:
        return False
    return target_key == candidate_key or (
        min(len(target_key), len(candidate_key)) >= 12
        and (target_key in candidate_key or candidate_key in target_key)
    )


def _within_size(row: dict[str, Any], target_size: float, tolerance: float) -> bool:
    candidate_size = float(row.get("size_sqft") or 0)
    return bool(
        target_size
        and candidate_size
        and abs(candidate_size - target_size) <= target_size * tolerance
    )


def _select_sales_comparables(
    rows: list[dict[str, Any]],
    *,
    building_name: str,
    bedrooms: str,
    unit_number: str | None,
    size: float,
) -> tuple[list[tuple[dict[str, Any], str]], str]:
    target_series = _unit_series(unit_number)
    layout_rows = [
        row
        for row in rows
        if bedrooms in ("", "Not stated") or str(row.get("bedrooms") or "") == bedrooms
    ]
    same_building_rows = [
        row
        for row in layout_rows
        if _same_building(building_name, str(row.get("building_name") or ""))
    ]
    same_building_series = [
        row
        for row in same_building_rows
        if target_series and _unit_series(str(row.get("unit_number") or "")) == target_series
    ]
    same_series_tight = [
        row
        for row in layout_rows
        if target_series
        and _unit_series(str(row.get("unit_number") or "")) == target_series
        and _within_size(row, size, 0.10)
    ]
    same_building_tight = [row for row in same_building_rows if _within_size(row, size, 0.10)]
    layout_tight = [row for row in layout_rows if _within_size(row, size, 0.10)]

    if target_series and same_building_series and len(same_series_tight) >= 2:
        pool = same_series_tight
        basis = (
            f"{target_series}-designated units with the same bedroom layout and within "
            "10% of size; same-building evidence ranked first."
        )
        reason = lambda row: (  # noqa: E731
            f"Same building · {target_series}-series"
            if _same_building(building_name, str(row.get("building_name") or ""))
            else f"{target_series}-series peer"
        )
    elif len(same_building_tight) >= 2:
        pool = same_building_tight
        basis = "Same building and bedroom layout, within 10% of unit size."
        reason = lambda row: "Same building · close size"  # noqa: E731
    elif len(same_building_rows) >= 2:
        pool = same_building_rows
        basis = "Same building and bedroom layout, within 20% of unit size."
        reason = lambda row: "Same building"  # noqa: E731
    elif len(layout_tight) >= 3:
        pool = layout_tight
        basis = "Same area and bedroom layout, within 10% of unit size."
        reason = lambda row: "Same area · close size"  # noqa: E731
    else:
        pool = layout_rows or rows
        basis = "Same area and property type, within 20% of unit size."
        reason = lambda row: "Area fallback"  # noqa: E731

    ranked = sorted(
        pool,
        key=lambda row: (
            not _same_building(building_name, str(row.get("building_name") or "")),
            target_series is None
            or _unit_series(str(row.get("unit_number") or "")) != target_series,
            abs(float(row.get("size_sqft") or 0) - size),
            -row["transaction_date"].toordinal(),
        ),
    )[:10]
    return [(row, reason(row)) for row in ranked], basis


def _select_rental_comparables(
    rows: list[dict[str, Any]],
    *,
    building_name: str,
    bedrooms: str,
    unit_number: str | None,
    size: float,
) -> tuple[list[tuple[dict[str, Any], str]], str]:
    target_series = _unit_series(unit_number)
    layout_rows = [
        row
        for row in rows
        if bedrooms in ("", "Not stated") or str(row.get("bedrooms") or "") == bedrooms
    ]
    same_building_rows = [
        row
        for row in layout_rows
        if _same_building(building_name, str(row.get("building_name") or ""))
    ]
    same_building_tight = [row for row in same_building_rows if _within_size(row, size, 0.10)]
    same_building_series = [
        row
        for row in same_building_rows
        if target_series
        and _unit_series(str(row.get("unit_number") or "")) == target_series
        and _within_size(row, size, 0.20)
    ]
    layout_tight = [row for row in layout_rows if _within_size(row, size, 0.10)]

    if len(same_building_tight) >= 2:
        pool = same_building_tight
        basis = "Same building and bedroom layout, within 10% of unit size."
        reason = lambda row: (  # noqa: E731
            f"Same building · {target_series}-series"
            if target_series and _unit_series(str(row.get("unit_number") or "")) == target_series
            else "Same building · close size"
        )
    elif len(same_building_series) >= 2:
        pool = same_building_series
        basis = f"Same-building {target_series}-designated leases with the same bedroom layout."
        reason = lambda row: f"Same building · {target_series}-series"  # noqa: E731
    elif len(same_building_rows) >= 2:
        pool = same_building_rows
        basis = "Same building and bedroom layout, within 20% of unit size."
        reason = lambda row: "Same building"  # noqa: E731
    elif len(layout_tight) >= 3:
        pool = layout_tight
        basis = "Same area and bedroom layout, within 10% of unit size."
        reason = lambda row: "Same area · close size"  # noqa: E731
    else:
        pool = layout_rows or rows
        basis = "Same area and property type, within 20% of unit size."
        reason = lambda row: "Area fallback"  # noqa: E731

    ranked = sorted(
        pool,
        key=lambda row: (
            not _same_building(building_name, str(row.get("building_name") or "")),
            target_series is None
            or _unit_series(str(row.get("unit_number") or "")) != target_series,
            abs(float(row.get("size_sqft") or 0) - size),
            -row["lease_start"].toordinal(),
        ),
    )[:10]
    return [(row, reason(row)) for row in ranked], basis


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _quarter_start(value: date) -> date:
    return date(value.year, ((value.month - 1) // 3) * 3 + 1, 1)


def _shift_quarters(value: date, quarters: int) -> date:
    quarter_number = value.year * 4 + (value.month - 1) // 3 + quarters
    year, quarter = divmod(quarter_number, 4)
    return date(year, quarter * 3 + 1, 1)


def _quarter_label(value: date) -> str:
    return f"Q{((value.month - 1) // 3) + 1} {value.year}"


def _fixed_mix_series(
    rows: list[dict[str, Any]],
) -> tuple[dict[date, float], dict[date, int]]:
    """Create a stable 75% apartment / 25% villa quarterly AED/sqft series."""
    grouped: dict[date, dict[str, tuple[float, int]]] = {}
    for row in rows:
        quarter = row["quarter"]
        segment = str(row.get("segment") or "")
        median_psf = float(row.get("median_psf") or 0)
        transactions = int(row.get("transactions") or 0)
        if median_psf <= 0 or segment not in {"Apartment", "Villa"}:
            continue
        grouped.setdefault(quarter, {})[segment] = (median_psf, transactions)

    values: dict[date, float] = {}
    counts: dict[date, int] = {}
    for quarter, segments in grouped.items():
        if "Apartment" not in segments or "Villa" not in segments:
            continue
        apartment, apartment_count = segments["Apartment"]
        villa, villa_count = segments["Villa"]
        values[quarter] = apartment * 0.75 + villa * 0.25
        counts[quarter] = apartment_count + villa_count
    return values, counts


def _commercial_mix_series(
    rows: list[dict[str, Any]],
) -> tuple[dict[date, float], dict[date, int]]:
    """Create a stable 80% office / 20% shop quarterly AED/sqft series."""
    grouped: dict[date, dict[str, tuple[float, int]]] = {}
    for row in rows:
        quarter = row["quarter"]
        segment = str(row.get("segment") or "")
        median_psf = float(row.get("median_psf") or 0)
        transactions = int(row.get("transactions") or 0)
        if median_psf <= 0 or segment not in {"Office", "Shop"}:
            continue
        grouped.setdefault(quarter, {})[segment] = (median_psf, transactions)

    values: dict[date, float] = {}
    counts: dict[date, int] = {}
    for quarter, segments in grouped.items():
        if "Office" not in segments or "Shop" not in segments:
            continue
        office, office_count = segments["Office"]
        shop, shop_count = segments["Shop"]
        values[quarter] = office * 0.8 + shop * 0.2
        counts[quarter] = office_count + shop_count
    return values, counts


def _single_series(
    rows: list[dict[str, Any]],
) -> tuple[dict[date, float], dict[date, int]]:
    values: dict[date, float] = {}
    counts: dict[date, int] = {}
    for row in rows:
        median_psf = float(row.get("median_psf") or 0)
        if median_psf <= 0:
            continue
        quarter = row["quarter"]
        values[quarter] = median_psf
        counts[quarter] = int(row.get("transactions") or 0)
    return values, counts


def _rebase_series(values: dict[date, float]) -> dict[date, float]:
    if not values:
        return {}
    base_value = values[min(values)]
    if base_value <= 0:
        return {}
    return {quarter: round(value / base_value * 100, 2) for quarter, value in values.items()}


def _change_pct(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return round((current / previous - 1) * 100, 2)


async def _build_price_index(
    lead: CrmLead,
    *,
    anchor: date,
) -> CrmPriceIndex:
    current_quarter = _quarter_start(anchor)
    index_start = _shift_quarters(current_quarter, -20)
    is_commercial = lead.asset_class == "commercial"
    segment_type = (
        lead.property_subtype
        if lead.property_subtype in {"Apartment", "Villa", "Office", "Shop"}
        else ""
    )
    params = {
        "index_start": index_start.isoformat(),
        "current_quarter": current_quarter.isoformat(),
        "area_key": _location_key(lead.area_name),
        "segment_type": segment_type,
        "is_commercial": int(is_commercial),
    }
    dld_rows, transaction_rows, segment_rows = await asyncio.gather(
        report_queries.fetch_dld_price_index(params),
        report_queries.fetch_registry_price_index(params),
        report_queries.fetch_segment_price_index(params),
    )

    mix_series = _commercial_mix_series if is_commercial else _fixed_mix_series
    dld_psf, dld_counts = mix_series(dld_rows)
    transaction_psf, transaction_counts = mix_series(transaction_rows)
    segment_psf, segment_counts = _single_series(segment_rows)
    dld_index = _rebase_series(dld_psf)
    transaction_index = _rebase_series(transaction_psf)
    segment_index = _rebase_series(segment_psf)

    points: list[CrmPriceIndexPoint] = []
    quarter = index_start
    while quarter < current_quarter:
        source_indexes = [
            value
            for value in (dld_index.get(quarter), transaction_index.get(quarter))
            if value is not None
        ]
        source_psf = [
            value
            for value in (dld_psf.get(quarter), transaction_psf.get(quarter))
            if value is not None
        ]
        if source_indexes:
            points.append(
                CrmPriceIndexPoint(
                    quarter=quarter,
                    label=_quarter_label(quarter),
                    dubai_index=round(sum(source_indexes) / len(source_indexes), 2),
                    dld_index=dld_index.get(quarter),
                    transaction_index=transaction_index.get(quarter),
                    segment_index=segment_index.get(quarter),
                    dubai_median_psf_aed=(
                        int(round(sum(source_psf) / len(source_psf))) if source_psf else None
                    ),
                    segment_median_psf_aed=(
                        int(round(segment_psf[quarter])) if quarter in segment_psf else None
                    ),
                    dld_transactions=dld_counts.get(quarter, 0),
                    transaction_count=transaction_counts.get(quarter, 0),
                    segment_transactions=segment_counts.get(quarter, 0),
                )
            )
        quarter = _shift_quarters(quarter, 1)

    if not points:
        points = [
            CrmPriceIndexPoint(
                quarter=index_start,
                label=_quarter_label(index_start),
                dubai_index=100,
            )
        ]
    latest = points[-1]
    year_ago_quarter = _shift_quarters(latest.quarter, -4)
    year_ago = next(
        (point for point in points if point.quarter == year_ago_quarter),
        None,
    )
    latest_segment_point = next(
        (point for point in reversed(points) if point.segment_index is not None),
        None,
    )
    segment_year_ago = (
        next(
            (
                point
                for point in points
                if point.quarter == _shift_quarters(latest_segment_point.quarter, -4)
            ),
            None,
        )
        if latest_segment_point
        else None
    )
    segment_name = f"{lead.area_name} · {lead.property_subtype}"
    return CrmPriceIndex(
        name=(
            "Vitevue Dubai Commercial Price Index"
            if is_commercial
            else "Vitevue Dubai Residential Price Index"
        ),
        base_period=points[0].quarter,
        latest_period=latest.quarter,
        current_index=latest.dubai_index,
        change_since_base_pct=round(latest.dubai_index - points[0].dubai_index, 2),
        year_over_year_pct=_change_pct(
            latest.dubai_index,
            year_ago.dubai_index if year_ago else None,
        ),
        current_median_psf_aed=latest.dubai_median_psf_aed,
        segment_name=segment_name,
        segment_current_index=(
            latest_segment_point.segment_index if latest_segment_point else None
        ),
        segment_year_over_year_pct=_change_pct(
            latest_segment_point.segment_index if latest_segment_point else None,
            segment_year_ago.segment_index if segment_year_ago else None,
        ),
        segment_current_median_psf_aed=(
            latest_segment_point.segment_median_psf_aed if latest_segment_point else None
        ),
        points=points,
        methodology=(
            (
                "Quarterly commercial AED/sqft, rebased to 100 at the first displayed quarter. "
                "Dubai uses a fixed 80% office / 20% shop mix and averages independently "
            )
            if is_commercial
            else (
                "Quarterly residential AED/sqft, rebased to 100 at the first displayed quarter. "
                "Dubai uses a fixed 75% apartment / 25% villa mix and averages independently "
            )
        )
        + (
            "rebased DLD registry and transaction-platform series when both are complete; "
            "the transaction series fills quarters where a DLD stratum is incomplete. "
            "The local segment uses "
            "sales for the subject area and property type. Current partial quarters are excluded. "
            "This is an internal analytical index, not an official government index or appraisal."
        ),
    )


async def get_property_report(
    lead_id: str,
    *,
    as_of: date | None = None,
) -> CrmPropertyReportResponse:
    anchor = as_of or date.today()
    period_start = anchor - timedelta(days=365)
    detail = await get_lead_detail(lead_id, as_of=anchor)
    lead = detail.lead
    size = float(lead.size_sqft or 0)
    low_size = size * 0.8 if size else 0
    high_size = size * 1.2 if size else 1_000_000

    params = {
        "period_start": period_start.isoformat(),
        "period_end": anchor.isoformat(),
        "area_key": _location_key(lead.area_name),
        "area_name": lead.area_name,
        "building_key": _location_key(lead.building_name),
        "building_name": lead.building_name,
        "sales_property_type": (
            lead.property_subtype if lead.property_subtype != "Commercial" else lead.property_type
        ),
        "rental_property_type": lead.property_type,
        "bedrooms": "" if lead.bedrooms == "Not stated" else lead.bedrooms,
        "low_size": low_size,
        "high_size": high_size,
        "size": size,
        "lead_id": lead.lead_id,
    }
    sale_rows, rental_rows, price_index = await asyncio.gather(
        report_queries.fetch_sale_comparables(params),
        report_queries.fetch_rental_comparables(params),
        _build_price_index(lead, anchor=anchor),
    )
    selected_sale_rows, sales_basis = _select_sales_comparables(
        sale_rows,
        building_name=lead.building_name,
        bedrooms=lead.bedrooms,
        unit_number=lead.unit_number,
        size=size,
    )
    selected_rental_rows, rentals_basis = _select_rental_comparables(
        rental_rows,
        building_name=lead.building_name,
        bedrooms=lead.bedrooms,
        unit_number=lead.unit_number,
        size=size,
    )
    sale_comps = [
        CrmSaleComparable(
            transaction_date=row["transaction_date"],
            building_name=str(row.get("building_name") or ""),
            unit_number=str(row.get("unit_number") or ""),
            unit_series=_unit_series(str(row.get("unit_number") or "")),
            property_type=str(row.get("property_type") or ""),
            bedrooms=str(row.get("bedrooms") or ""),
            size_sqft=float(row.get("size_sqft") or 0),
            sale_amount_aed=int(row.get("sale_amount_aed") or 0),
            price_per_sqft_aed=(
                float(row["price_per_sqft_aed"])
                if row.get("price_per_sqft_aed") is not None
                else None
            ),
            selection_reason=selection_reason,
        )
        for row, selection_reason in selected_sale_rows
    ]
    rental_comps = [
        CrmRentalComparable(
            lease_start=row["lease_start"],
            building_name=str(row.get("building_name") or ""),
            unit_number=str(row.get("unit_number") or "").strip() or None,
            unit_series=_unit_series(str(row.get("unit_number") or "")),
            unit_match_status=str(row.get("unit_match_status") or "unresolved"),
            property_type=str(row.get("property_type") or ""),
            bedrooms=str(row.get("bedrooms") or ""),
            size_sqft=float(row.get("size_sqft") or 0),
            annual_rent_aed=int(row.get("annual_rent_aed") or 0),
            rent_per_sqft_aed=(
                round(
                    int(row.get("annual_rent_aed") or 0) / float(row.get("size_sqft") or 1),
                    2,
                )
                if row.get("size_sqft")
                else None
            ),
            selection_reason=selection_reason,
        )
        for row, selection_reason in selected_rental_rows
    ]

    sale_psf = [
        float(comp.price_per_sqft_aed or comp.sale_amount_aed / comp.size_sqft)
        for comp in sale_comps
        if comp.size_sqft
    ]
    rent_psf = [
        float(comp.rent_per_sqft_aed) for comp in rental_comps if comp.rent_per_sqft_aed is not None
    ]
    median_sale_psf = _percentile(sale_psf, 0.5)
    low_sale_psf = _percentile(sale_psf, 0.25)
    high_sale_psf = _percentile(sale_psf, 0.75)
    median_rent_psf = _percentile(rent_psf, 0.5)
    estimated_value = int(round(median_sale_psf * size)) if median_sale_psf and size else None
    estimated_low = int(round(low_sale_psf * size)) if low_sale_psf and size else None
    estimated_high = int(round(high_sale_psf * size)) if high_sale_psf and size else None
    estimated_rent = int(round(median_rent_psf * size)) if median_rent_psf and size else None
    purchase_price = lead.matched_sale_price_aed
    capital_change = (
        estimated_value - purchase_price if estimated_value and purchase_price else None
    )
    capital_change_pct = (
        round(capital_change / purchase_price * 100, 2)
        if capital_change is not None and purchase_price
        else None
    )
    selling_costs = int(round(estimated_value * 0.021 + 5_000)) if estimated_value else None
    proceeds_before_debt = (
        estimated_value - selling_costs
        if estimated_value is not None and selling_costs is not None
        else None
    )
    confidence = (
        "high"
        if len(sale_comps) >= 3 and len(rental_comps) >= 3
        else "medium"
        if len(sale_comps) >= 2 and len(rental_comps) >= 2
        else "low"
    )
    return CrmPropertyReportResponse(
        lead=lead,
        period_start=period_start,
        period_end=anchor,
        sales_comparables=sale_comps,
        rental_comparables=rental_comps,
        analysis=CrmReportAnalysis(
            estimated_market_value_aed=estimated_value,
            estimated_value_low_aed=estimated_low,
            estimated_value_high_aed=estimated_high,
            estimated_market_rent_aed=estimated_rent,
            indicated_capital_change_aed=capital_change,
            indicated_capital_change_pct=capital_change_pct,
            indicative_selling_costs_aed=selling_costs,
            indicative_proceeds_before_debt_aed=proceeds_before_debt,
            estimated_market_gross_yield_pct=(
                round(estimated_rent / estimated_value * 100, 2)
                if estimated_rent and estimated_value
                else None
            ),
            current_rent_vs_market_pct=(
                round((lead.annual_rent_aed - estimated_rent) / estimated_rent * 100, 2)
                if estimated_rent
                else None
            ),
            target_unit_series=_unit_series(lead.unit_number),
            sales_selection_basis=sales_basis,
            rental_selection_basis=rentals_basis,
            sales_comparables_used=len(sale_comps),
            rental_comparables_used=len(rental_comps),
            confidence=confidence,
        ),
        price_index=price_index,
        agent_signals=_agent_signals(lead),
        methodology=[
            "Comparable window: the 365 days ending on the report date.",
            f"Sales cohort: {sales_basis}",
            f"Rental cohort: {rentals_basis}",
            "Indicated value uses median comparable sale price per sqft; the range uses "
            "the 25th and 75th percentiles.",
            "Indicative selling costs assume 2.1% of value plus AED 5,000 administration. "
            "Mortgage settlement, NOC, conveyancing, and property-specific costs are excluded.",
            "Any LTV shown is the ratio reported at purchase and is not a current loan balance.",
            price_index.methodology,
        ],
        generated_at=datetime.now(timezone.utc),
    )
