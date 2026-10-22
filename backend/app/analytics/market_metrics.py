"""Database-grounded sales and rental metrics shared by the API and the chat tools.

The served canonical tables (ch_transactions / ch_rent_contracts over the market layer) are
the authority. A period is resolved to Dubai calendar dates first. Coverage then comes from
the served release's load evidence, day by day (see MarketCoverage), not from the first and
last record dates: records at both ends of a month do not show the middle was loaded.
Missing data stays missing: a day without evidence is unknown, never a zero, and only a
fully verified period is complete. One operation answers from one release: if the release
changes while it runs, the operation is recomputed.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

from app.analytics import market_metric_queries as queries
from app.analytics.models import (
    MarketCoverage,
    MarketCoverageRange,
    MarketLocationMatch,
    MarketMetricDefinition,
    MarketMetricRow,
    MarketMetricsRequest,
    MarketMetricsResponse,
    MarketResolvedPeriod,
)
from app.core.observability import operation
from app.market.periods import (
    BUSINESS_TIMEZONE,
    ResolvedPeriod,
    business_today,
    previous_period,
    resolve_period,
)
from app.market.reference import community_location

SMALL_SAMPLE = 10

ELIGIBILITY = {
    "sales": (
        "Served canonical sales (one row per DLD transaction after corrections and "
        "de-duplication, plus DXBI sales DLD does not hold): transaction group 'Sales' with a "
        "price above zero. Mortgages, gifts, probable duplicates and superseded versions are "
        "excluded. Date: DLD registration date (instance_date), Dubai calendar."
    ),
    "rentals": (
        "Served canonical Ejari contracts (de-duplicated DLD rows plus DXBI contracts DLD does "
        "not hold) with an annual amount above zero and an end date after the start date. "
        "Date: contract registration date, Dubai calendar."
    ),
}

_VALUE = {"sales": "sale price", "rentals": "annual rent (DLD annualised amount)"}
_AREA = {"sales": "procedure area", "rentals": "contract area"}


def _definitions(dataset: str, names: list[str]) -> list[MarketMetricDefinition]:
    value, area = _VALUE[dataset], _AREA[dataset]
    rate = f"{value} ÷ {area}, records with {area} > 0 (see rated_count)"
    text = {
        "count": ("records", "Eligible records in the period."),
        "total_aed": ("AED", f"Sum of {value}."),
        "mean_aed": ("AED", f"Arithmetic mean {value}."),
        "median_aed": (
            "AED",
            f"Median {value}; an even count averages the two middle values.",
        ),
        "p25_aed": ("AED", f"25th percentile {value}, linear interpolation."),
        "p75_aed": ("AED", f"75th percentile {value}, linear interpolation."),
        "rated_count": ("records", f"Records with {area} > 0, the per-area denominator."),
        "mean_aed_per_sqm": ("AED/sqm", f"Mean of each record's {rate}."),
        "median_aed_per_sqm": ("AED/sqm", f"Median of each record's {rate}."),
        "weighted_aed_per_sqm": ("AED/sqm", f"Area-weighted: sum of {value} ÷ sum of {area}."),
        "mean_aed_per_sqft": ("AED/sqft", "mean_aed_per_sqm ÷ 10.7639 (1 sqm = 10.7639 sqft)."),
        "median_aed_per_sqft": ("AED/sqft", "median_aed_per_sqm ÷ 10.7639."),
        "weighted_aed_per_sqft": ("AED/sqft", "weighted_aed_per_sqm ÷ 10.7639."),
        "total_contract_aed": ("AED", "Sum of whole-term contract amounts (not annualised)."),
        "median_contract_aed": ("AED", "Median whole-term contract amount (not annualised)."),
    }
    return [
        MarketMetricDefinition(name=name, unit=text[name][0], definition=text[name][1])
        for name in names
    ]


def _resolve(request: MarketMetricsRequest) -> ResolvedPeriod:
    spec = request.period
    return resolve_period(
        spec.type,
        reference_date=request.reference_date or business_today(),
        months=spec.months,
        days=spec.days,
        month=spec.month,
        quarter=spec.quarter,
        start=spec.start,
        end=spec.end,
    )


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def _day_status(release: dict[str, Any]) -> dict[date, str]:
    status: dict[date, str] = {}
    for item in (release.get("coverage") or {}).get("ranges", []):
        day, last = date.fromisoformat(item["from"]), date.fromisoformat(item["to"])
        while day <= last:
            status[day] = item["status"]
            day += timedelta(days=1)
    return status


def _coverage(period: ResolvedPeriod, release: dict[str, Any]) -> MarketCoverage:
    data_from, data_to = _as_date(release.get("data_from")), _as_date(release.get("data_to"))
    known = _day_status(release)
    days, day = [], period.start
    while day <= period.end:
        days.append((day, known.get(day, "unverified")))
        day += timedelta(days=1)
    ranges: list[MarketCoverageRange] = []
    for day, state in days:
        if ranges and ranges[-1].status == state:
            ranges[-1].end = day
        else:
            ranges.append(MarketCoverageRange(start=day, end=day, status=state))
    counts = {state: sum(1 for _, s in days if s == state) for state in ("verified", "partial")}
    verified = [day for day, state in days if state == "verified"]
    has_records = (
        data_to is not None
        and data_to >= period.start
        and (data_from is None or data_from <= period.end)
    )
    if counts["verified"] == len(days):
        status, note = "complete", f"Every day of {period.label} is verified as fully loaded."
    elif counts["verified"] or counts["partial"]:
        gaps = "; ".join(
            f"{r.start} to {r.end} {r.status}" for r in ranges if r.status != "verified"
        )
        status = "partial"
        note = f"Not every day of {period.label} is verified as fully loaded: {gaps}."
    elif has_records:
        status = "unverified"
        note = (
            f"{period.label}: records exist but no load evidence verifies these days, so the "
            "values are observed records, not established totals."
        )
    else:
        status = "unavailable"
        note = f"No load evidence and no records for {period.label}."
    return MarketCoverage(
        status=status,
        data_from=data_from,
        data_to=data_to,
        covered_from=verified[0] if verified else None,
        covered_to=verified[-1] if verified else None,
        verified_days=counts["verified"],
        partial_days=counts["partial"],
        unverified_days=len(days) - counts["verified"] - counts["partial"],
        ranges=ranges,
        evidence_loaded_at=(release.get("coverage") or {}).get("last_loaded_at"),
        note=note,
    )


def _period_model(period: ResolvedPeriod) -> MarketResolvedPeriod:
    return MarketResolvedPeriod(
        start=period.start,
        end=period.end,
        label=period.label,
        type=period.type,
        reference_date=period.reference_date,
        timezone=str(BUSINESS_TIMEZONE),
    )


def _clean(value: Any, digits: int = 2) -> float | None:
    if value is None:
        return None
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else round(number, digits)


def _row(raw: dict[str, Any], names: list[str], group: str | None = None) -> MarketMetricRow:
    count = int(raw.get("count") or 0)
    values = {}
    for name in names:
        if name == "count":
            continue
        values[name] = _clean(raw.get(name)) if count else (0.0 if name == "total_aed" else None)
    return MarketMetricRow(group=group, count=count, values=values)


def _filters(request: MarketMetricsRequest, start: date, end: date) -> queries.MetricFilters:
    location = community_location(request.location) if request.location else None
    bedrooms = tuple(
        "Studio" if item == "studio" else f"{item} B/R" for item in (request.bedrooms or [])
    )
    return queries.MetricFilters(
        start=start,
        end=end,
        location_communities=location.communities if location else (),
        location_districts=location.districts if location else (),
        building=request.building,
        project=request.project,
        property_type=request.property_type,
        usage=request.usage,
        bedrooms=bedrooms,
        registration={"off_plan": "Off-Plan Properties", "ready": "Existing Properties"}.get(
            request.registration or ""
        ),
        contract_version={"new": "New", "renewed": "Renewed"}.get(request.contract_version or ""),
        min_term_days=request.min_term_days,
        max_term_days=request.max_term_days,
        value_min=request.min_value_aed,
        value_max=request.max_value_aed,
    )


def _band_label(index: int, bands: list[float]) -> str:
    if index == 0:
        return f"under {bands[0]:,.0f}"
    if index == len(bands):
        return f"{bands[-1]:,.0f} and above"
    return f"{bands[index - 1]:,.0f} to under {bands[index]:,.0f}"


def _month_starts(start: date, end: date) -> list[date]:
    months, current = [], start.replace(day=1)
    while current <= end:
        months.append(current)
        current = (current + timedelta(days=32)).replace(day=1)
    return months


def _unknown(names: list[str]) -> MarketMetricRow:
    return MarketMetricRow(count=None, values={name: None for name in names if name != "count"})


async def _total(request, names, period, coverage) -> MarketMetricRow:
    if coverage.status == "unavailable":
        return _unknown(names)
    rows = await queries.fetch_metric_rows(
        request.dataset, _filters(request, period.start, period.end), tuple(names)
    )
    total = _row(rows[0] if rows else {}, names)
    # No records without load evidence is unknown, not zero.
    return _unknown(names) if coverage.status == "unverified" and not total.count else total


async def _groups(request, names, period, coverage, total, release) -> list[MarketMetricRow]:
    filters = _filters(request, period.start, period.end)
    bands = tuple(request.price_bands or ())
    raw = await queries.fetch_metric_rows(
        request.dataset,
        filters,
        tuple(names),
        group_by=request.group_by,
        price_bands=bands,
        min_group_count=request.min_group_count,
        order_by=request.sort_by,
        limit=request.limit if request.group_by not in {"month", "price_band"} else 500,
    )
    rows: list[MarketMetricRow] = []
    if request.group_by == "month":
        found = {_as_date(item["group_key"]): item for item in raw}
        known = _day_status(release)
        for month in _month_starts(period.start, period.end):
            last = min(period.end, (month + timedelta(days=32)).replace(day=1) - timedelta(days=1))
            states = set()
            day = max(month, period.start)
            while day <= last:
                states.add(known.get(day, "unverified"))
                day += timedelta(days=1)
            state = (
                "verified"
                if states == {"verified"}
                else ("unverified" if states == {"unverified"} else "partial")
            )
            item = found.get(month)
            if item is None and state == "unverified":
                # No records and no evidence: unknown, not zero.
                rows.append(MarketMetricRow(group=month.isoformat()[:7], coverage=state))
            else:
                row = _row(item or {}, names, month.isoformat()[:7])
                row.coverage = state
                rows.append(row)
    elif request.group_by == "price_band":
        found = {int(item["group_key"]): item for item in raw}
        rows = [
            _row(found.get(index, {}), names, _band_label(index, list(bands)))
            for index in range(len(bands) + 1)
        ]
    else:
        rows = [_row(item, names, str(item["group_label"])) for item in raw]
    for row in rows:
        if row.count is not None and total.count:
            row.share_of_count = round(row.count / total.count, 4)
    return rows


def _change(current: MarketMetricRow, previous: MarketMetricRow) -> dict[str, float | None]:
    pairs = {"count": (current.count, previous.count)} | {
        name: (value, previous.values.get(name)) for name, value in current.values.items()
    }
    return {
        name: round((now - before) / before * 100, 2) if now is not None and before else None
        for name, (now, before) in pairs.items()
    }


@operation
async def get_market_metrics(request: MarketMetricsRequest) -> MarketMetricsResponse:
    period = _resolve(request)  # an unresolvable period fails before any warehouse query
    for _ in range(2):
        release = await queries.fetch_release(request.dataset)
        response = await _compute(request, period, release)
        # A publication between the first and last query would mix releases: recompute.
        # ponytail: before/after identity check; an exchange-and-rollback inside one request
        # would go unseen. Per-query release ids would close that if it ever matters.
        if await queries.fetch_release_id(request.dataset) == (release.get("release_id") or ""):
            return response
    response.limitations.append(
        "The served release changed while this answer was computed; repeat the request."
    )
    return response


async def _compute(
    request: MarketMetricsRequest, period: ResolvedPeriod, release: dict[str, Any]
) -> MarketMetricsResponse:
    names = list(dict.fromkeys(["count", *request.metrics]))
    if any("per_sq" in name for name in names):
        names.append("rated_count")
    coverage = _coverage(period, release)
    total = await _total(request, names, period, coverage)
    response = MarketMetricsResponse(
        dataset=request.dataset,
        period=_period_model(period),
        coverage=coverage,
        filters=request.model_dump(
            mode="json", exclude_none=True, exclude={"metrics", "period", "reference_date"}
        ),
        eligibility=ELIGIBILITY[request.dataset],
        definitions=_definitions(request.dataset, names),
        total=total,
        release_id=release.get("release_id") or None,
        release_built_at=str(release.get("built_at")) if release.get("built_at") else None,
    )
    limitations = response.limitations
    if coverage.status != "complete":
        limitations.append(coverage.note)
    if coverage.status == "unavailable":
        return response

    filters = _filters(request, period.start, period.end)
    if request.group_by:
        response.rows = await _groups(request, names, period, coverage, total, release)
    if request.compare_with_previous_period:
        before = previous_period(period)
        before_coverage = _coverage(before, release)
        response.comparison_period = _period_model(before)
        response.comparison_coverage = before_coverage
        response.comparison = await _total(request, names, before, before_coverage)
        if before_coverage.status == "complete" and coverage.status == "complete":
            response.change_pct = _change(total, response.comparison)
        else:
            limitations.append(
                "Change is not computed because a compared period is not fully covered."
            )
    if request.location:
        location = community_location(request.location)
        matched = await queries.fetch_matched_locations(request.dataset, filters)
        response.matched_locations = [MarketLocationMatch(**item) for item in matched]
        containing = tuple(
            sorted({m.district for m in response.matched_locations if m.community and m.district})
        )
        if location.communities and not location.districts and containing:
            coarse = await queries.fetch_district_only_rows(request.dataset, filters, containing)
            if coarse:
                limitations.append(
                    f"{coarse} records matching the other filters are located by DLD only at "
                    f"district level ({', '.join(containing)}); they may include "
                    f"{location.name} but are not counted, because no source places them there."
                )
    sources = await queries.fetch_source_breakdown(request.dataset, filters)
    response.sources = {
        f"{item['source_system']}/{item['match_status']}": int(item["records"]) for item in sources
    }
    if request.bedrooms:
        known = await queries.fetch_bedroom_coverage(request.dataset, filters)
        if int(known["unknown_bedrooms"]):
            limitations.append(
                f"{int(known['unknown_bedrooms'])} of {int(known['rows'])} records matching the "
                "other filters have no bedroom count in the source and are excluded."
            )
    if total.count is not None and total.count < SMALL_SAMPLE:
        limitations.append(f"Small sample: {total.count} eligible records.")
    return response
