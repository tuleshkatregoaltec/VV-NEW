"""Calendar period resolution for market answers.

Business dates are Dubai dates: "last month" asked at 02:00 on 1 October in Dubai is
September, even though it is still 30 September in UTC. A period is always an explicit,
inclusive date range; nothing here anchors a period to the latest record in the warehouse.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

BUSINESS_TIMEZONE = ZoneInfo("Asia/Dubai")

PeriodType = Literal[
    "previous_month",
    "previous_quarter",
    "previous_months",
    "month",
    "quarter",
    "rolling_days",
    "range",
]


@dataclass(frozen=True, slots=True)
class ResolvedPeriod:
    start: date  # inclusive
    end: date  # inclusive
    label: str
    type: PeriodType
    reference_date: date


def business_today(now: datetime | None = None) -> date:
    return (now or datetime.now(BUSINESS_TIMEZONE)).astimezone(BUSINESS_TIMEZONE).date()


def _month_start(value: date, months_back: int = 0) -> date:
    index = value.year * 12 + value.month - 1 - months_back
    return date(index // 12, index % 12 + 1, 1)


def _month_end(start: date) -> date:
    return _month_start(start, -1) - timedelta(days=1)


def _quarter_start(value: date) -> date:
    return date(value.year, 3 * ((value.month - 1) // 3) + 1, 1)


def resolve_period(
    period_type: PeriodType,
    *,
    reference_date: date,
    months: int | None = None,
    days: int | None = None,
    month: str | None = None,
    quarter: str | None = None,
    start: date | None = None,
    end: date | None = None,
) -> ResolvedPeriod:
    """Resolve a requested period to inclusive Dubai calendar dates.

    previous_month    the calendar month before reference_date's month
    previous_quarter  the calendar quarter before reference_date's quarter
    previous_months   the `months` complete calendar months ending with previous_month
    month / quarter   an explicit "YYYY-MM" / "YYYY-Qn"
    rolling_days      the `days` calendar days ending on reference_date, inclusive
    range             explicit inclusive start/end
    """
    if period_type == "previous_month":
        first = _month_start(reference_date, 1)
        return ResolvedPeriod(
            first, _month_end(first), first.strftime("%B %Y"), period_type, reference_date
        )
    if period_type == "previous_quarter":
        first = _month_start(_quarter_start(reference_date), 3)
        last = _month_end(_month_start(first, -2))
        label = f"Q{(first.month - 1) // 3 + 1} {first.year}"
        return ResolvedPeriod(first, last, label, period_type, reference_date)
    if period_type == "previous_months":
        if not months or months < 1 or months > 120:
            raise ValueError("previous_months needs months between 1 and 120")
        first = _month_start(reference_date, months)
        last = _month_start(reference_date) - timedelta(days=1)
        label = f"{first.strftime('%B %Y')} to {last.strftime('%B %Y')}"
        return ResolvedPeriod(first, last, label, period_type, reference_date)
    if period_type == "month":
        try:
            first = datetime.strptime(month or "", "%Y-%m").date()
        except ValueError as error:
            raise ValueError("month must be YYYY-MM") from error
        return ResolvedPeriod(
            first, _month_end(first), first.strftime("%B %Y"), period_type, reference_date
        )
    if period_type == "quarter":
        try:
            year, number = (quarter or "").upper().split("-Q")
            first = date(int(year), 3 * (int(number) - 1) + 1, 1)
        except ValueError as error:
            raise ValueError("quarter must be YYYY-Qn with n from 1 to 4") from error
        last = _month_end(_month_start(first, -2))
        return ResolvedPeriod(first, last, f"Q{number} {year}", period_type, reference_date)
    if period_type == "rolling_days":
        if not days or days < 1 or days > 3660:
            raise ValueError("rolling_days needs days between 1 and 3660")
        first = reference_date - timedelta(days=days - 1)
        label = f"{days} days to {reference_date.isoformat()}"
        return ResolvedPeriod(first, reference_date, label, period_type, reference_date)
    if period_type == "range":
        if start is None or end is None or start > end:
            raise ValueError("range needs start <= end")
        label = f"{start.isoformat()} to {end.isoformat()}"
        return ResolvedPeriod(start, end, label, period_type, reference_date)
    raise ValueError(f"unknown period type {period_type!r}")


def previous_period(period: ResolvedPeriod) -> ResolvedPeriod:
    """The equally sized period immediately before `period` (whole months stay whole months)."""
    if period.start.day == 1 and _month_end(_month_start(period.end)) == period.end:
        months = (
            (period.end.year - period.start.year) * 12 + period.end.month - period.start.month + 1
        )
        first = _month_start(period.start, months)
        last = period.start - timedelta(days=1)
        label = (
            f"{first.strftime('%B %Y')} to {last.strftime('%B %Y')}"
            if months > 1
            else first.strftime("%B %Y")
        )
    else:
        length = period.end - period.start
        last = period.start - timedelta(days=1)
        first = last - length
        label = f"{first.isoformat()} to {last.isoformat()}"
    return ResolvedPeriod(first, last, label, "range", period.reference_date)
