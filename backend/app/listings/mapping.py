from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timezone
from typing import Any

from app.listings.models import (
    ListingCardResponse,
    ListingPartyResponse,
)
from app.listings.policy import (
    NON_BUILDING_PROPERTY_TYPES,
    _asset_class,
    _benchmark_level,
    _listing_mode,
    _market_position,
    _urgency_terms,
)

LISTINGS_SOURCE_LABEL = "ph_box ClickHouse pf_listings_bronze"


def _row_to_dict(row: Any) -> dict[str, Any]:
    if isinstance(row, Mapping):
        return dict(row)
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row)


def _json(value: Any) -> Any:
    if value in (None, ""):
        return None
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _as_list(value: Any) -> list[Any]:
    parsed = _json(value)
    return parsed if isinstance(parsed, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    parsed = _json(value)
    return parsed if isinstance(parsed, dict) else {}


def _location_hierarchy(
    names: list[str], property_type: str | None
) -> tuple[str | None, str | None, str | None]:
    """Normalize variable-depth source locations into area, project, and building.

    The source path always starts with city and community. Apartment and office paths
    end in a building, while landed and industrial asset paths end in a project/phase.
    """
    area = names[1] if len(names) > 1 else None
    tail = names[2:]
    if not tail:
        return area, None, None

    normalized_type = str(property_type or "").strip().lower()
    if normalized_type in NON_BUILDING_PROPERTY_TYPES:
        return area, tail[-1], None

    building = tail[-1]
    project = tail[-2] if len(tail) > 1 else None
    return area, project, building


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _image_url(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _images(value: Any) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for image in _as_list(value):
        if not isinstance(image, dict):
            continue
        normalized = {
            "small_url": _image_url(image.get("small_url") or image.get("small")),
            "medium_url": _image_url(image.get("medium_url") or image.get("medium")),
            "original_url": _image_url(
                image.get("original_url") or image.get("original") or image.get("large")
            ),
            "url": _image_url(image.get("url")),
            "label": image.get("classification_label") or image.get("label"),
        }
        if not any(
            normalized.get(key) for key in ("small_url", "medium_url", "original_url", "url")
        ):
            continue
        images.append(normalized)
    return images


def _primary_image(images: list[dict[str, Any]]) -> str | None:
    for image in images:
        for key in ("small_url", "medium_url", "original_url", "url"):
            if image.get(key):
                return str(image[key])
    return None


def _party(value: Any) -> ListingPartyResponse | None:
    data = _as_dict(value)
    if not data:
        return None
    normalized = {
        "id": None if data.get("id") is None else str(data.get("id")),
        "name": data.get("name"),
        "email": data.get("email"),
        "phone": data.get("phone"),
        "whatsapp": data.get("whatsapp"),
        "image_url": data.get("image_url") or data.get("image"),
        "logo_url": data.get("logo_url") or data.get("logo"),
        "address": data.get("address"),
        "slug": data.get("slug"),
        "is_super_agent": data.get("is_super_agent"),
        "languages": _as_list(data.get("languages")),
    }
    return ListingPartyResponse.model_validate(normalized)


def _floorplans(raw_property: dict[str, Any]) -> list[dict[str, Any]]:
    floorplans: list[dict[str, Any]] = []
    for item in _as_list(raw_property.get("floor_plans")):
        if not isinstance(item, dict):
            continue
        floorplans.append(
            {
                "title": item.get("title") or item.get("name"),
                "image_url": item.get("image_url") or item.get("url"),
                "area_sqft": _float_or_none(item.get("area")),
                "floor_number": _int_or_none(item.get("floor_number")),
                "unit_number": item.get("unit_number"),
                "dimension": item.get("dimension"),
            }
        )
    return floorplans


def _location_names(row: dict[str, Any]) -> list[str]:
    return [str(item) for item in _as_list(row.get("listing_location_names_json")) if item]


def _card_from_row(row: Any) -> ListingCardResponse:
    row = _row_to_dict(row)
    category_id = _int_or_none(row.get("category_id"))
    raw_property = _as_dict(row.get("raw_property_json"))
    price = _as_dict(row.get("price_json"))
    size = _as_dict(row.get("size_json"))
    names = _location_names(row)
    images = _images(row.get("images_json", row.get("images")))
    agent = _party(row.get("agent_json", row.get("agent")))
    broker = _party(row.get("broker_json", row.get("broker")))
    property_type = (
        raw_property.get("property_type")
        or row.get("property_type_name")
        or (str(row.get("property_type_id")) if row.get("property_type_id") is not None else None)
    )
    area_name, project_name, building_name = _location_hierarchy(names, property_type)
    listed_date = _parse_datetime(row.get("listed_at")) or _parse_datetime(
        raw_property.get("listed_date")
    )
    price_per_sqft = _float_or_none(row.get("price_per_sqft_aed"))
    market_psf = _float_or_none(row.get("market_median_price_per_sqft_aed"))
    market_estimate = _float_or_none(row.get("market_estimate_aed"))
    market_delta = _float_or_none(row.get("market_delta_pct"))
    comparable_count = _int_or_none(row.get("comparable_count")) or 0
    title = str(row.get("title") or raw_property.get("title") or "Untitled listing")
    listing_mode = _listing_mode(category_id)
    urgency_signal, urgency_terms = _urgency_terms(
        listing_mode,
        title,
        str(raw_property.get("description") or ""),
    )

    return ListingCardResponse(
        listing_id=str(row["listing_id"]),
        listing_mode=listing_mode,
        asset_class=_asset_class(category_id),
        title=title,
        reference=row.get("reference") or raw_property.get("reference"),
        property_type=property_type,
        price_value=_int_or_none(price.get("value")),
        price_currency=price.get("currency"),
        price_period=price.get("period"),
        size_value=_float_or_none(size.get("value")),
        size_unit=size.get("unit"),
        bedrooms=str(row.get("bedrooms")) if row.get("bedrooms") not in (None, "") else None,
        bathrooms=str(row.get("bathrooms")) if row.get("bathrooms") not in (None, "") else None,
        city_name=names[0] if len(names) > 0 else None,
        area_name=area_name,
        subcommunity_name=project_name,
        tower_name=building_name,
        location_name=", ".join(names) if names else None,
        latitude=_float_or_none(row.get("lat")),
        longitude=_float_or_none(row.get("lng")),
        primary_image_url=_primary_image(images),
        image_count=len(images),
        floorplan_count=len(_floorplans(raw_property)),
        agent_name=agent.name if agent else None,
        agent_image_url=agent.image_url if agent else None,
        broker_name=broker.name if broker else None,
        broker_logo_url=broker.logo_url if broker else None,
        listed_date=listed_date,
        listing_age_days=_int_or_none(row.get("listing_age_days")),
        price_per_sqft_aed=price_per_sqft,
        market_median_price_per_sqft_aed=market_psf,
        market_estimate_aed=market_estimate,
        market_delta_pct=market_delta,
        market_position=_market_position(market_delta, comparable_count),
        comparable_count=comparable_count,
        benchmark_level=_benchmark_level(property_type, comparable_count),
        urgency_signal=urgency_signal,
        urgency_terms=urgency_terms,
        is_available=_bool_or_none(row.get("is_available")),
        is_verified=_bool_or_none(row.get("is_verified")),
        is_featured=_bool_or_none(row.get("is_featured")),
        is_premium=_bool_or_none(row.get("is_premium")),
        share_url=row.get("share_url"),
    )


def _freshness_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    raw = "" if value is None else str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
