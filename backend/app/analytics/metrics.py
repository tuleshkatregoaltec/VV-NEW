import math
import re
from datetime import date
from typing import Any, Literal, Optional

from app.analytics.models import ProjectConfigurationRow, TrendInterval

_ROOM_ORDER = {"Studio": 0}


_CONFIG_PRICE_MIN_SAMPLE_SIZE = 3


_CONFIG_MOMENTUM_MIN_SAMPLE_SIZE = 20


_BEDROOM_RE = re.compile(r"^\d+\s+B/R$", re.IGNORECASE)


def _room_sort_key(rooms: str) -> tuple:
    if rooms in _ROOM_ORDER:
        return (0, 0)
    # Extract leading number e.g. "1 B/R" → 1
    parts = rooms.split()
    try:
        return (1, int(parts[0]))
    except (ValueError, IndexError):
        return (2, rooms)


def _normalize_label(value: object) -> Optional[str]:
    label = " ".join(str(value).split()) if value is not None else ""
    return label or None


def _unique_normalized_labels(values: list[object]) -> list[str]:
    seen: set[str] = set()
    labels: list[str] = []
    for value in values:
        label = _normalize_label(value)
        if label is None:
            continue
        key = label.casefold()
        if key in seen:
            continue
        seen.add(key)
        labels.append(label)
    return labels


def _is_residential_config_room(rooms: Optional[str]) -> bool:
    label = _normalize_label(rooms)
    if label is None:
        return False

    upper = label.upper()
    if upper in {"NA", "N/A", "NONE", "NULL"}:
        return False
    if upper == "STUDIO":
        return True
    if _BEDROOM_RE.match(label):
        return True
    return "PENTHOUSE" in upper


def _positive_float(value: object) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number <= 0:
        return None
    return number


def _pct_change(current: object, previous: object) -> Optional[float]:
    current_number = _positive_float(current)
    previous_number = _positive_float(previous)
    if current_number is None or previous_number is None:
        return None
    return round((current_number - previous_number) / previous_number * 100, 1)


def _sampled_count_pct_change(current: int, previous: int) -> Optional[float]:
    if current < _CONFIG_MOMENTUM_MIN_SAMPLE_SIZE or previous < _CONFIG_MOMENTUM_MIN_SAMPLE_SIZE:
        return None
    denominator = (current + previous) / 2
    if denominator <= 0:
        return None
    return round((current - previous) / denominator * 100, 1)


def _coalesce_delivery_date(row: dict) -> Optional[date]:
    return row.get("completion_date") or row.get("project_end_date")


def _quarter_start(dt: date) -> date:
    month = ((dt.month - 1) // 3) * 3 + 1
    return date(dt.year, month, 1)


def _add_months(dt: date, months: int) -> date:
    year = dt.year + (dt.month - 1 + months) // 12
    month = (dt.month - 1 + months) % 12 + 1
    return date(year, month, 1)


def _trend_interval_for_days(days: int) -> TrendInterval:
    if days <= 30:
        return "day"
    if days <= 180:
        return "week"
    return "month"


def build_configuration_rows(
    segment: Literal["all", "off_plan", "secondary"],
    sales_rows: list[dict],
    unit_rows_by_room: dict,
    rent_by_room: dict,
) -> list[ProjectConfigurationRow]:
    sales_lookup = {
        room: row
        for row in sales_rows
        if (room := _normalize_label(row.get("rooms_en"))) is not None
    }
    room_keys = {
        room
        for room in set(unit_rows_by_room) | set(sales_lookup)
        if _is_residential_config_room(room)
    }
    rows: list[ProjectConfigurationRow] = []

    for rooms in room_keys:
        sale_row = sales_lookup.get(rooms, {})
        unit_row = unit_rows_by_room.get(rooms, {})
        rent_row = rent_by_room.get(rooms, {}) if segment != "off_plan" else {}

        stock_units = int(unit_row.get("unit_count", 0) or 0)
        sales_last_12m = int(sale_row.get("sales_last_12m", 0) or 0)
        sales_prev_12m = int(sale_row.get("sales_prev_12m", 0) or 0)
        sales_last_90d = int(sale_row.get("sales_last_90d", 0) or 0)
        sales_prev_90d = int(sale_row.get("sales_prev_90d", 0) or 0)
        sale_count = int(sale_row.get("sale_count", 0) or 0)
        median_sale_price = float(sale_row.get("median_sale_price", 0) or 0)
        median_annual_rent = float(rent_row.get("median_annual_rent", 0) or 0)

        if stock_units <= 0 and sale_count <= 0 and int(rent_row.get("rental_count", 0) or 0) <= 0:
            continue
        if sales_last_12m < _CONFIG_PRICE_MIN_SAMPLE_SIZE:
            continue

        gross_yield_pct: Optional[float] = None
        if segment != "off_plan" and median_sale_price > 0 and median_annual_rent > 0:
            gross_yield_pct = round(median_annual_rent / median_sale_price * 100, 2)

        absorption_rate_pct: Optional[float] = None
        if stock_units > 0:
            absorption_rate_pct = round(sales_last_12m / stock_units * 100, 1)

        rows.append(
            ProjectConfigurationRow(
                market_segment=segment,
                rooms=rooms,
                stock_units=stock_units,
                sale_count=sale_count,
                sales_last_12m=sales_last_12m,
                avg_sale_price=float(sale_row.get("avg_sale_price", 0) or 0),
                median_sale_price=median_sale_price,
                avg_sale_price_sqm=float(sale_row.get("avg_sale_price_sqm", 0) or 0),
                avg_unit_size_sqm=float(unit_row.get("avg_unit_size_sqm", 0) or 0),
                rental_count=int(rent_row.get("rental_count", 0) or 0),
                median_annual_rent=median_annual_rent,
                median_rent_sqm=float(rent_row.get("median_rent_sqm", 0) or 0),
                gross_yield_pct=gross_yield_pct,
                price_change_yoy_pct=_pct_change(
                    sale_row.get("avg_price_sqm_last_12m"),
                    sale_row.get("avg_price_sqm_prev_12m"),
                )
                if sales_last_12m >= _CONFIG_PRICE_MIN_SAMPLE_SIZE
                and sales_prev_12m >= _CONFIG_PRICE_MIN_SAMPLE_SIZE
                else None,
                sales_momentum_pct=_sampled_count_pct_change(sales_last_90d, sales_prev_90d),
                absorption_rate_pct=absorption_rate_pct,
                price_p25=float(sale_row.get("price_p25", 0) or 0),
                price_p50=float(sale_row.get("price_p50", 0) or 0),
                price_p75=float(sale_row.get("price_p75", 0) or 0),
            )
        )

    rows.sort(key=lambda x: _room_sort_key(x.rooms))
    return rows


DEFAULT_CHART_DAYS = 365
MIN_CHART_DAYS = 30
MAX_CHART_DAYS = 3650
DAILY_INTERVAL_MAX_DAYS = 30
WEEKLY_INTERVAL_MAX_DAYS = 180


def clamp_int(value: Any, *, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(min_value, min(max_value, parsed))


def positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _chart_area_inputs(args: dict[str, Any]) -> list[str]:
    raw_areas = args.get("areas")
    if isinstance(raw_areas, list):
        areas: list[str] = []
        seen: set[str] = set()
        for raw_item in raw_areas:
            area = " ".join(str(raw_item).strip().split())
            key = area.lower()
            if area and key not in seen:
                seen.add(key)
                areas.append(area)
            if len(areas) >= 5:
                break
        if len(areas) >= 2:
            return areas

    area = clean_text(args.get("area"))
    return [area] if area else []


def _trend_interval(days: int) -> str:
    if days <= DAILY_INTERVAL_MAX_DAYS:
        return "day"
    if days <= WEEKLY_INTERVAL_MAX_DAYS:
        return "week"
    return "month"


def _chart_days(value: Any) -> int:
    return clamp_int(
        value,
        default=DEFAULT_CHART_DAYS,
        min_value=MIN_CHART_DAYS,
        max_value=MAX_CHART_DAYS,
    )


def _chart_type(value: Any, group_by: str) -> str:
    requested = str(value or "").lower().replace("-", "_")
    if requested in {"donut", "doughnut", "pie"}:
        return "doughnut"
    if requested in {"horizontal_bar", "bar_horizontal"}:
        return "horizontal_bar"
    if requested in {"line", "area"}:
        return "line"
    if group_by == "time":
        return "line"
    if requested == "bar":
        return "bar"
    return "horizontal_bar"


def _chart_sort_direction(value: Any) -> str:
    requested = str(value or "").lower().strip()
    if requested in {"asc", "ascending", "bottom", "lowest", "least", "cheap", "cheapest"}:
        return "asc"
    return "desc"


def _chart_min_records(args: dict[str, Any], *, group_by: str, metric: str) -> int:
    default = (
        25
        if group_by == "area"
        and metric
        in {
            "avg_price",
            "median_price",
            "avg_price_sqm",
            "median_price_sqm",
            "avg_price_sqft",
            "median_price_sqft",
            "avg_rent",
            "median_rent",
            "median_rent_sqm",
            "median_rent_sqft",
        }
        else 1
    )
    return clamp_int(args.get("min_records"), default=default, min_value=1, max_value=10_000)


def metric_label(metric: str) -> str:
    labels = {
        "transaction_count": "Transactions",
        "contract_count": "Contracts",
        "project_count": "Projects",
        "active_projects": "Active projects",
        "completed_projects": "Completed projects",
        "total_volume": "Total value",
        "total_annual_value": "Total annual rent",
        "total_sales_volume_12m": "12m sales value",
        "avg_price": "Average price",
        "median_price": "Median price",
        "avg_price_sqm": "Average price / sqm",
        "median_price_sqm": "Median price / sqm",
        "avg_price_sqft": "Average price / sqft",
        "median_price_sqft": "Median price / sqft",
        "avg_rent": "Average rent",
        "median_rent": "Median rent",
        "median_rent_sqm": "Median rent / sqm",
        "median_rent_sqft": "Median rent / sqft",
        "pipeline_units": "Pipeline units",
        "units_delivering_next_12_months": "Units delivering next 12m",
        "sales_transaction_count_12m": "12m sales transactions",
        "rental_contract_count_12m": "12m rental contracts",
        "gross_yield_pct": "Gross yield",
        "avg_completion_pct": "Average completion",
    }
    return labels.get(metric, metric.replace("_", " ").title())


def dimension_label(group_by: str) -> str:
    labels = {
        "time": "Time",
        "area": "Area",
        "property_type": "Property type",
        "bedrooms": "Bedrooms",
        "project": "Project",
        "master_project": "Master project",
        "transaction_type": "Transaction type",
        "tenant_type": "Tenant type",
        "developer": "Developer",
        "completion_status": "Completion status",
        "pipeline_status": "Pipeline status",
    }
    return labels.get(group_by, group_by.replace("_", " ").title())


def _chart_points(rows: list[dict[str, Any]], *, time_series: bool) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []
    for row in rows:
        raw_label = row.get("label")
        if raw_label is None or raw_label == "":
            raw_label = "Unknown"
        value = float(row.get("value") or 0)
        point = {
            "label": str(raw_label),
            "value": value,
            "record_count": clamp_int(
                row.get("record_count"), default=0, min_value=0, max_value=10_000_000
            ),
        }
        if time_series:
            point["date"] = str(raw_label)
        points.append(point)
    return points


def _chart_summary(
    points: list[dict[str, Any]], *, period_start: date | None, period_end: date | None
) -> dict[str, Any]:
    return {
        "point_count": len(points),
        "total_records": sum(int(point.get("record_count") or 0) for point in points),
        "period_start": str(period_start) if period_start else None,
        "period_end": str(period_end) if period_end else None,
    }


def _chart_series_summary(
    series: list[dict[str, Any]], *, period_start: date | None, period_end: date | None
) -> dict[str, Any]:
    points = [
        point
        for series_item in series
        for point in series_item.get("points", [])
        if isinstance(point, dict)
    ]
    return _chart_summary(points, period_start=period_start, period_end=period_end)


def _chart_series(
    rows: list[dict[str, Any]], *, time_series: bool, series_order: list[str]
) -> list[dict[str, Any]]:
    points_by_series = {label: [] for label in series_order}
    for row in rows:
        label = str(row.get("series_label") or "Other")
        points_by_series.setdefault(label, [])
        points_by_series[label].extend(_chart_points([row], time_series=time_series))

    return [
        {"label": label, "points": points} for label, points in points_by_series.items() if points
    ]


def _flatten_chart_series(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        point
        for series_item in series
        for point in series_item.get("points", [])
        if isinstance(point, dict)
    ]


def _optional_chart_filter(value: Any) -> str | None:
    text_value = clean_text(value)
    if not text_value or text_value.lower() in {"all", "any", "none"}:
        return None
    return text_value


def _rental_property_type(value: Any) -> str | None:
    property_type = _optional_chart_filter(value)
    if not property_type:
        return None
    normalized = property_type.lower()
    if normalized in {"unit", "apartment", "apartments", "flat", "flats"}:
        return "Flat"
    if normalized in {"villa", "villas"}:
        return "Villa"
    return property_type


def _sales_property_type(value: Any) -> str | None:
    property_type = _optional_chart_filter(value)
    if not property_type:
        return None
    normalized = property_type.lower()
    if normalized in {"unit", "apartment", "apartments", "flat", "flats"}:
        return "Unit"
    if normalized in {"villa", "villas"}:
        return "Villa"
    return property_type
