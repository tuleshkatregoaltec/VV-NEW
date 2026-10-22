"""Calendar period rules for market answers (Dubai business dates, explicit ranges)."""

from datetime import date, datetime, timezone

import pytest

from app.market.periods import business_today, previous_period, resolve_period


def test_last_month_uses_the_dubai_date_not_utc():
    # 22:30 UTC on 30 September is 02:30 on 1 October in Dubai: "last month" is September.
    today = business_today(datetime(2026, 9, 30, 22, 30, tzinfo=timezone.utc))
    assert today == date(2026, 10, 1)
    period = resolve_period("previous_month", reference_date=today)
    assert (period.start, period.end, period.label) == (
        date(2026, 9, 1),
        date(2026, 9, 30),
        "September 2026",
    )


def test_previous_month_and_quarter_cross_year_boundaries():
    january = date(2027, 1, 15)
    month = resolve_period("previous_month", reference_date=january)
    assert (month.start, month.end) == (date(2026, 12, 1), date(2026, 12, 31))
    quarter = resolve_period("previous_quarter", reference_date=january)
    assert (quarter.start, quarter.end, quarter.label) == (
        date(2026, 10, 1),
        date(2026, 12, 31),
        "Q4 2026",
    )


def test_last_month_is_never_a_rolling_thirty_days():
    period = resolve_period("previous_month", reference_date=date(2028, 3, 1))
    # February of a leap year: 29 days, not the 30 days before 1 March.
    assert (period.start, period.end) == (date(2028, 2, 1), date(2028, 2, 29))


def test_six_complete_months_exclude_the_current_month():
    period = resolve_period("previous_months", reference_date=date(2026, 10, 3), months=6)
    assert (period.start, period.end) == (date(2026, 4, 1), date(2026, 9, 30))


def test_rolling_days_end_on_the_reference_date_inclusive():
    period = resolve_period("rolling_days", reference_date=date(2026, 10, 3), days=90)
    assert (period.start, period.end) == (date(2026, 7, 6), date(2026, 10, 3))
    assert (period.end - period.start).days + 1 == 90


def test_explicit_month_quarter_and_invalid_inputs():
    assert resolve_period("month", reference_date=date(2026, 1, 1), month="2024-02").end == date(
        2024, 2, 29
    )
    quarter = resolve_period("quarter", reference_date=date(2026, 1, 1), quarter="2026-Q2")
    assert (quarter.start, quarter.end) == (date(2026, 4, 1), date(2026, 6, 30))
    with pytest.raises(ValueError):
        resolve_period("previous_months", reference_date=date(2026, 1, 1))
    with pytest.raises(ValueError):
        resolve_period(
            "range", reference_date=date(2026, 1, 1), start=date(2026, 2, 1), end=date(2026, 1, 1)
        )


def test_previous_period_keeps_whole_months_and_equal_day_counts():
    september = resolve_period("previous_month", reference_date=date(2026, 10, 3))
    assert (previous_period(september).start, previous_period(september).end) == (
        date(2026, 8, 1),
        date(2026, 8, 31),
    )
    quarter = resolve_period("previous_quarter", reference_date=date(2026, 10, 3))
    before = previous_period(quarter)
    assert (before.start, before.end) == (date(2026, 4, 1), date(2026, 6, 30))
    rolling = resolve_period("rolling_days", reference_date=date(2026, 10, 3), days=90)
    earlier = previous_period(rolling)
    assert earlier.end == date(2026, 7, 5)
    assert (earlier.end - earlier.start).days + 1 == 90
