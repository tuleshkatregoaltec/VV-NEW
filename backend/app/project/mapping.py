import math
import re
from datetime import date
from typing import Any

from app.market.reference import (
    DEVELOPER_LEGAL_ENTITY_PATTERN,
    developer_brand_name,
)


def _room_sort_key(rooms: str) -> tuple:
    normalized = rooms.strip()
    if normalized.lower() == "studio":
        return (0, 0, normalized)
    match = re.match(r"^(\d+)", normalized)
    if match:
        return (1, int(match.group(1)), normalized)
    return (2, 0, normalized.lower())


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _date_or_none(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.date() if hasattr(value, "date") else value
    return None


def _int_value(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _contains_arabic(value: str) -> bool:
    return any("\u0600" <= char <= "\u06ff" for char in value)


def _developer_lookup(rows: list[dict[str, Any]]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for row in rows:
        english = str(row.get("developer_name_en") or "").strip()
        arabic = str(row.get("developer_name_ar") or "").strip()
        if english:
            lookup[english] = english
        if arabic and english:
            lookup[arabic] = english
    return lookup


def _developer_display_name(name: Any, lookup: dict[str, str]) -> str | None:
    label = str(name or "").strip()
    if not label:
        return None
    mapped = lookup.get(label)
    if mapped:
        return developer_brand_name(mapped)
    if _contains_arabic(label):
        return None
    branded = developer_brand_name(label)
    if branded == label and DEVELOPER_LEGAL_ENTITY_PATTERN.search(branded):
        return None
    return branded


def _developer_display_list(raw: Any, lookup: dict[str, str], *, limit: int = 5) -> list[str]:
    if not isinstance(raw, list):
        return []
    labels: list[str] = []
    for item in raw:
        label = _developer_display_name(item, lookup)
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= limit:
            break
    return labels


RADAR_YIELD_MIN_SAMPLE_COUNT = 5
