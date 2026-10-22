"""Typed Reelly fields for the serving tables, parsed from the project payload.

Field names and their interpretation follow the backend catalogue (app/supply/mapping.py):
list item first, then project detail; ``Starting_price`` entries pair by position with
``Units_types``; ``Payment_plans`` steps carry ``Payment_time``, ``Percent_of_payment`` and
``Order``. Anything the payload does not carry stays empty or NULL (no area, bedroom or unit
count is derived when Reelly gives none).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

PROJECT_FIELDS = (
    "project_name", "developer_name", "sale_status", "region", "area_name", "units_in_sale",
    "completion_date", "completion_time", "min_price_aed", "max_price_aed", "latitude",
    "longitude", "website_url", "overview",
)  # fmt: skip
UNIT_TYPE_COLUMNS = (
    "unit_type_key", "run_id", "scraped_at", "project_key", "project_id", "unit_type_index",
    "unit_type", "name", "price_from_aed", "price_to_aed", "source_system", "source_file",
    "raw_manifest_s3_key",
)  # fmt: skip
PAYMENT_COLUMNS = (
    "payment_plan_key", "run_id", "scraped_at", "project_key", "project_id",
    "payment_plan_index", "payment_step_index", "name", "order_index", "percent_of_payment",
    "payment_time", "source_system", "source_file", "raw_manifest_s3_key",
)  # fmt: skip
SOURCE_FILE = "reelly_supply_project_details.jsonl"


def _first(*sources: dict[str, Any], key: str) -> Any:
    for source in sources:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _number(value: Any) -> float | None:
    try:
        number = float(str(value).replace(",", "")) if value not in (None, "") else None
    except ValueError:
        return None
    return number


def _positive(value: Any) -> float | None:
    number = _number(value)
    return number if number and number > 0 else None


def _flatten(value: Any) -> Iterator[Any]:
    if isinstance(value, list):
        for item in value:
            yield from _flatten(item)
    elif value not in (None, ""):
        yield value


def _completion_time(value: Any) -> str:
    """ISO text from Reelly's ISO string or epoch milliseconds."""
    if isinstance(value, (int, float)) and value > 0:
        return datetime.fromtimestamp(value / 1000, UTC).isoformat()
    return _text(value)


def _coordinates(value: Any) -> tuple[float | None, float | None]:
    parts = [part.strip() for part in _text(value).replace(";", ",").split(",")]
    if len(parts) < 2:
        return None, None
    return _number(parts[0]), _number(parts[1])


def unit_prices(list_item: dict[str, Any], raw: dict[str, Any]) -> list[dict[str, Any]]:
    names: list[str] = []
    for item in _flatten(_first(raw, list_item, key="Units_types")):
        name = _text(item)
        if name and name.lower() not in {n.lower() for n in names}:
            names.append(name)
    prices = [
        item
        for item in _flatten(_first(list_item, raw, key="Starting_price"))
        if isinstance(item, dict)
    ]
    return [
        {
            "unit_type_index": index + 1,
            "unit_type": names[index] if index < len(names) else "",
            "price_from_aed": _positive(price.get("Price_from_AED")),
            "price_to_aed": _positive(price.get("Price_to_AED")),
        }
        for index, price in enumerate(prices)
    ]


def payment_steps(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Steps of each plan; nested lists are separate plans, a flat list is one plan."""
    value = raw.get("Payment_plans")
    plans = (
        value if isinstance(value, list) and any(isinstance(v, list) for v in value) else [value]
    )
    steps = []
    for plan_index, plan in enumerate(plans, start=1):
        for step_index, step in enumerate(
            (s for s in _flatten(plan) if isinstance(s, dict)), start=1
        ):
            name = _text(step.get("Name") or step.get("The"))
            payment_time = _text(step.get("Payment_time"))
            percent = _number(step.get("Percent_of_payment"))
            if not (name or payment_time or percent is not None):
                continue
            order = _number(step.get("Order"))
            steps.append(
                {
                    "payment_plan_index": plan_index,
                    "payment_step_index": step_index,
                    "name": name,
                    "order_index": int(order) if order is not None else step_index,
                    "percent_of_payment": percent,
                    "payment_time": payment_time,
                }
            )
    return steps


def typed_project(list_item: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    latitude, longitude = _coordinates(_first(raw, list_item, key="Coordinates"))
    prices = unit_prices(list_item, raw)
    min_price = _positive(_first(list_item, raw, key="min_price")) or min(
        (p["price_from_aed"] for p in prices if p["price_from_aed"]), default=None
    )
    units = _number(_first(list_item, raw, key="units_in_sale"))
    return {
        "project_name": _text(_first(list_item, raw, key="Project_name")),
        "developer_name": _text(_first(list_item, raw, key="Developers_name")),
        "sale_status": _text(_first(list_item, raw, key="sale_status")),
        "region": _text(
            _first(raw, list_item, key="Region") or _first(raw, list_item, key="region")
        ),
        "area_name": _text(_first(list_item, raw, key="Area_name")),
        "units_in_sale": max(int(units or 0), 0),
        "completion_date": _text(_first(list_item, raw, key="Completion_date")),
        "completion_time": _completion_time(_first(list_item, raw, key="Completion_time")),
        "min_price_aed": min_price,
        "max_price_aed": _positive(_first(list_item, raw, key="max_price")),
        "latitude": latitude,
        "longitude": longitude,
        "website_url": _text(_first(raw, list_item, key="Website")),
        "overview": _text(raw.get("Overview")),
    }


def key(*parts: Any) -> str:
    return hashlib.sha1("|".join(map(str, parts)).encode(), usedforsecurity=False).hexdigest()


def as_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    except ValueError:
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None) if parsed and parsed.tzinfo else parsed
