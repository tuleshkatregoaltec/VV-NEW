from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from app.crm.inbound_policy import _locations


def _location_fit(criteria: dict[str, Any], *values: str) -> tuple[int, str]:
    requested = _locations(criteria)
    if not requested:
        return 0, "Preferred location is missing"
    haystack = " ".join(values).casefold()
    if any(item.casefold() in haystack or haystack in item.casefold() for item in requested):
        return 20, f"Matches {requested[0]}"
    return 0, "Outside preferred location"


def _budget_fit(criteria: dict[str, Any], value: int | None) -> tuple[int, str]:
    maximum = int(criteria.get("budget_max_aed") or 0)
    minimum = int(criteria.get("budget_min_aed") or 0)
    if not maximum and not minimum:
        return 0, "Budget is missing"
    if not value:
        return 5, "Price requires verification"
    if (not minimum or value >= minimum) and (not maximum or value <= maximum):
        return 15, "Within stated budget"
    if maximum and value <= maximum * 1.1 and value >= minimum * 0.9:
        return 9, "Within flexible budget margin"
    return 0, "Outside budget"


def _bedroom_fit(criteria: dict[str, Any], value: str) -> tuple[int, str]:
    requested = criteria.get("bedrooms_min")
    if requested is None:
        return 0, "Bedroom requirement is missing"
    match = re.search(r"\d+", value)
    observed = int(match.group()) if match else 0 if "studio" in value.lower() else None
    if observed == int(requested):
        return 10, "Exact bedroom match"
    if observed is not None and abs(observed - int(requested)) <= 1:
        return 6, "Within flexible bedroom range"
    return 0, "Bedroom mismatch"


def _property_fit(criteria: dict[str, Any], value: str) -> int:
    requested = _canonical_property_type(criteria.get("property_type"))
    if not requested:
        return 0
    observed = _canonical_property_type(value)
    return 10 if requested == observed else 0


def _canonical_property_type(value: Any) -> str:
    normalized = str(value or "").strip().casefold()
    aliases = {
        "flat": "apartment",
        "apartments": "apartment",
        "residential flat": "apartment",
        "residential flats": "apartment",
        "residential": "apartment",
        "unit": "apartment",
        "condo": "apartment",
        "condominium": "apartment",
        "hotel apartments": "apartment",
        "townhouses": "townhouse",
        "villas": "villa",
        "offices": "office",
    }
    return aliases.get(normalized, normalized)


def _listing_property_type(value: Any) -> str | None:
    normalized = _canonical_property_type(value)
    return normalized.title() if normalized else None


def _violates_strict_fields(
    strict_fields: list[str],
    *,
    location_points: int,
    budget_points: int,
    bedroom_points: int,
    property_points: int,
) -> bool:
    checks = {
        "locations": location_points,
        "budget_min_aed": budget_points,
        "budget_max_aed": budget_points,
        "bedrooms_min": bedroom_points,
        "bedrooms_max": bedroom_points,
        "property_type": property_points,
    }
    return any(checks.get(field, 1) == 0 for field in strict_fields)


def _json_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.isoformat() if isinstance(value, date | datetime) else value
        for key, value in row.items()
    }
