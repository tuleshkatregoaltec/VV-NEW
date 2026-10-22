from __future__ import annotations

from app.listings.models import (
    ListingAssetClass,
    ListingMarketPosition,
    ListingMode,
)

SALE_CATEGORY_IDS = (1, 3)

RENT_CATEGORY_IDS = (2, 4)

NON_BUILDING_PROPERTY_TYPES = {
    "bungalow",
    "compound",
    "factory",
    "farm",
    "land",
    "labor camp",
    "staff accommodation",
    "townhouse",
    "villa",
    "warehouse",
}

MIN_BENCHMARK_COMPARABLES = 5

MARKET_POSITION_THRESHOLD = 0.10

EXPLICIT_URGENCY_TERMS = (
    "distress",
    "motivated seller",
    "urgent sale",
    "must sell",
    "price reduced",
    "reduced price",
    "priced to sell",
    "quick sale",
)

VALUE_LANGUAGE_TERMS = (
    "below market",
    "lowest price",
    "best price",
)

ACHIEVED_SALE_TYPES = {
    "residential": ["Apartment", "Villa"],
    "commercial": ["Office", "Shop", "Warehouse", "Workshop", "Clinic", "Show Rooms"],
}


def _listing_mode(category_id: int | None) -> ListingMode:
    return "rent" if category_id in RENT_CATEGORY_IDS else "sale"


def _asset_class(category_id: int | None) -> ListingAssetClass:
    return "commercial" if category_id in (3, 4) else "residential"


def _category_ids(
    mode: ListingMode, asset_class: ListingAssetClass | None = None
) -> tuple[int, ...]:
    if asset_class == "residential":
        return (2,) if mode == "rent" else (1,)
    if asset_class == "commercial":
        return (4,) if mode == "rent" else (3,)
    return RENT_CATEGORY_IDS if mode == "rent" else SALE_CATEGORY_IDS


def _price_basis(value: str | None) -> str:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in {"annual", "monthly"} else "raw"


def _market_position(delta_pct: float | None, comparable_count: int) -> ListingMarketPosition:
    if delta_pct is None or comparable_count < MIN_BENCHMARK_COMPARABLES:
        return "insufficient_data"
    if delta_pct < -MARKET_POSITION_THRESHOLD:
        return "below_market"
    if delta_pct > MARKET_POSITION_THRESHOLD:
        return "above_market"
    return "near_market"


def _benchmark_level(property_type: str | None, comparable_count: int) -> str:
    if comparable_count < MIN_BENCHMARK_COMPARABLES:
        return "insufficient_data"
    normalized_type = str(property_type or "").strip().lower()
    return "project_layout" if normalized_type in NON_BUILDING_PROPERTY_TYPES else "building_layout"


def _urgency_terms(
    mode: ListingMode, title: str | None, description: str | None
) -> tuple[str, list[str]]:
    if mode != "sale":
        return "none", []
    copy = f"{title or ''} {description or ''}".casefold()
    explicit = [term for term in EXPLICIT_URGENCY_TERMS if term in copy]
    if explicit:
        return "explicit_urgency", explicit
    supporting = [term for term in VALUE_LANGUAGE_TERMS if term in copy]
    if supporting:
        return "value_language", supporting
    return "none", []


def _price_period(value: str | None) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized or None


def _achieved_property_types(
    mode: ListingMode,
    asset_class: ListingAssetClass,
    property_type: str | None,
) -> list[str]:
    normalized = str(property_type or "").strip().lower()
    if mode == "rent":
        if asset_class == "commercial":
            return ["Commercial"]
        if normalized == "apartment":
            return ["Apartment"]
        if normalized in {"villa", "townhouse"}:
            return ["Villa"]
        return ["Apartment", "Villa"]

    if normalized == "apartment":
        return ["Apartment"]
    if normalized in {"villa", "townhouse"}:
        return ["Villa"]
    if normalized:
        title_type = normalized.title()
        if title_type in ACHIEVED_SALE_TYPES[asset_class]:
            return [title_type]
    return ACHIEVED_SALE_TYPES[asset_class]
