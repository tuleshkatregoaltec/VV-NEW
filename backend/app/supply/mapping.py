import json
import re
from datetime import date, datetime, timezone
from typing import Any

from app.market.reference import (
    developer_brand_name,
    developer_match_key,
    is_rankable_developer_brand,
)
from app.supply.models import (
    SupplyCatalogueAssetResponse,
)

SUPPLY_CATALOGUE_GALLERY_ASSET_TYPES = (
    "building_image",
    "interior_image",
    "facility_image",
    "unit_image",
)

DEVELOPER_RANKING_HIDDEN_DEVELOPER_KEYS = {"properties"}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _row_data(row: Any) -> dict[str, Any]:
    return dict(row._mapping) if hasattr(row, "_mapping") else dict(row)


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _flatten_values(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        values: list[Any] = []
        for item in value:
            values.extend(_flatten_values(item))
        return values
    return [value]


def _text_or_none(value: Any) -> str | None:
    text_value = str(value or "").strip()
    return text_value or None


def _stable_asset_id(project_id: int, asset_type: str, index: int) -> int:
    namespace = {
        "cover_image": 1,
        "building_image": 2,
        "interior_image": 3,
        "facility_image": 4,
        "unit_image": 5,
        "floorplan": 6,
        "brochure": 7,
        "master_plan": 8,
    }.get(asset_type, 9)
    return project_id * 1000 + namespace * 100 + index


def _asset_from_url(
    project_id: int,
    asset_type: str,
    index: int,
    url: str,
    name: str | None = None,
    content_type: str | None = None,
    size_bytes: int | None = None,
) -> SupplyCatalogueAssetResponse:
    is_document = asset_type in {"brochure", "floorplan"}
    return SupplyCatalogueAssetResponse(
        id=_stable_asset_id(project_id, asset_type, index),
        asset_type=asset_type,
        name=name,
        url=url,
        download_url=url if is_document else None,
        content_type=content_type,
        size_bytes=size_bytes,
    )


def _assets_from_reelly_value(
    project_id: int, asset_type: str, value: Any
) -> list[SupplyCatalogueAssetResponse]:
    assets: list[SupplyCatalogueAssetResponse] = []
    seen_urls: set[str] = set()
    for item in _flatten_values(value):
        url: str | None = None
        name: str | None = None
        content_type: str | None = None
        size_bytes: int | None = None
        if isinstance(item, dict):
            url = _text_or_none(item.get("url"))
            name = _text_or_none(item.get("name"))
            content_type = _text_or_none(item.get("mime"))
            size_bytes = _int_or_none(item.get("size"))
        elif isinstance(item, str) and item.startswith(("http://", "https://")):
            url = item
            name = item.rsplit("/", 1)[-1] or None

        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        assets.append(
            _asset_from_url(
                project_id=project_id,
                asset_type=asset_type,
                index=len(assets) + 1,
                url=url,
                name=name,
                content_type=content_type,
                size_bytes=size_bytes,
            )
        )
    return assets


def _facility_assets(
    project_id: int, facilities: Any
) -> tuple[list[str], list[SupplyCatalogueAssetResponse]]:
    names: list[str] = []
    assets: list[SupplyCatalogueAssetResponse] = []
    seen_names: set[str] = set()
    seen_urls: set[str] = set()
    for item in _flatten_values(facilities):
        if not isinstance(item, dict):
            continue
        name = _text_or_none(item.get("Name"))
        if name and name.lower() not in seen_names:
            seen_names.add(name.lower())
            names.append(name)
        for image_asset in _assets_from_reelly_value(
            project_id, "facility_image", item.get("Image")
        ):
            if image_asset.url in seen_urls:
                continue
            seen_urls.add(image_asset.url)
            assets.append(
                image_asset.model_copy(
                    update={"id": _stable_asset_id(project_id, "facility_image", len(assets) + 1)}
                )
            )
    return names, assets


def _unit_type_names(value: Any) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for item in _flatten_values(value):
        name = _text_or_none(item)
        if not name:
            continue
        key = name.lower()
        if key not in seen:
            seen.add(key)
            names.append(name)
    return names


def _unit_responses(raw: dict[str, Any], list_item: dict[str, Any]) -> list[dict[str, Any]]:
    unit_types = _unit_type_names(raw.get("Units_types") or list_item.get("Units_types"))
    prices = [
        item for item in _flatten_values(list_item.get("Starting_price")) if isinstance(item, dict)
    ]
    units: list[dict[str, Any]] = []
    for index, price in enumerate(prices):
        label = unit_types[index] if index < len(unit_types) else f"Unit type {index + 1}"
        units.append(
            {
                "label": label,
                "unit_type": label,
                "price_from_aed": _int_or_none(price.get("Price_from_AED")),
                "price_to_aed": _int_or_none(price.get("Price_to_AED")),
            }
        )
    if not units:
        units = [{"label": item, "unit_type": item} for item in unit_types]
    return units


def _payment_plan(raw: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for item in _flatten_values(raw.get("Payment_plans")):
        if not isinstance(item, dict):
            continue
        label = _text_or_none(item.get("Payment_time") or item.get("Name") or item.get("The"))
        percent = _text_or_none(item.get("Percent_of_payment"))
        if not label and percent:
            label = f"{percent}% payment"
        if not label:
            continue
        steps.append(
            {
                "label": label,
                "percent": percent,
                "order": _int_or_none(item.get("Order")),
            }
        )
    return sorted(steps, key=lambda item: item.get("order") or 999)


def _coordinates(value: Any) -> dict[str, float] | None:
    text_value = _text_or_none(value)
    if not text_value or "," not in text_value:
        return None
    latitude, longitude = [part.strip() for part in text_value.split(",", 1)]
    lat = _float_or_none(latitude)
    lng = _float_or_none(longitude)
    if lat is None or lng is None:
        return None
    return {"latitude": lat, "longitude": lng}


def _catalogue_data_from_clickhouse_row(row: dict[str, Any]) -> dict[str, Any]:
    list_item = _json_dict(row.get("list_item_json"))
    raw = _json_dict(row.get("raw_json"))
    project_id = (
        _int_or_none(row.get("project_id"))
        or _int_or_none(list_item.get("id"))
        or _int_or_none(row.get("project_key"))
    )
    if project_id is None:
        raise ValueError("Reelly project row is missing a numeric project id")

    units = _unit_responses(raw, list_item)
    unit_types = _unit_type_names(raw.get("Units_types") or list_item.get("Units_types"))
    cover_image_url = _text_or_none(
        (list_item.get("cover") or {}).get("url")
        if isinstance(list_item.get("cover"), dict)
        else None
    )
    if not cover_image_url:
        cover_image_url = _text_or_none(raw.get("cover_url"))

    gallery = _assets_from_reelly_value(project_id, "building_image", raw.get("Architecture"))
    floorplans = _assets_from_reelly_value(
        project_id, "floorplan", raw.get("Layouts_preview_img") or raw.get("Units_layouts_PDF")
    )
    brochures = _assets_from_reelly_value(project_id, "brochure", raw.get("Brochure"))
    master_plans = _assets_from_reelly_value(project_id, "master_plan", raw.get("Master_plan"))
    facilities, facility_assets = _facility_assets(project_id, raw.get("Facilities"))
    gallery = (gallery + facility_assets)[:18]

    min_price = _int_or_none(list_item.get("min_price") or raw.get("min_price"))
    if min_price in (None, 0):
        price_candidates = [
            _int_or_none(item.get("Price_from_AED"))
            for item in _flatten_values(list_item.get("Starting_price"))
            if isinstance(item, dict)
        ]
        price_candidates = [item for item in price_candidates if item and item > 0]
        min_price = min(price_candidates) if price_candidates else None
    max_price = _int_or_none(list_item.get("max_price") or raw.get("max_price"))

    overview = _text_or_none(raw.get("Overview"))
    return {
        "project_id": project_id,
        "project_name": _text_or_none(list_item.get("Project_name") or raw.get("Project_name"))
        or f"Project {project_id}",
        "developer_name": _text_or_none(
            list_item.get("Developers_name") or raw.get("Developers_name")
        )
        or "Unknown developer",
        "developer_id": None,
        "area_name": _text_or_none(list_item.get("Area_name") or raw.get("Area_name")),
        "region": "Dubai",
        "status": _text_or_none(list_item.get("Status") or raw.get("Status")),
        "sale_status": _text_or_none(list_item.get("sale_status") or raw.get("sale_status")),
        "completion_date": _text_or_none(
            list_item.get("Completion_date") or raw.get("Completion_date")
        ),
        "completion_time_ms": _int_or_none(
            list_item.get("Completion_time") or raw.get("Completion_time")
        ),
        "min_price_aed": min_price,
        "max_price_aed": max_price,
        "units_in_sale": _int_or_none(list_item.get("units_in_sale") or raw.get("units_in_sale")),
        "unit_types": unit_types,
        "units": units,
        "payment_plan": _payment_plan(raw),
        "facilities": facilities,
        "buildings": [],
        "nearby_points": [],
        "overview": overview,
        "overview_excerpt": overview[:240] if overview else None,
        "coordinates": _coordinates(raw.get("Coordinates")),
        "cover_image_url": cover_image_url,
        "developer_logo_url": None,
        "image_count": len(gallery),
        "floorplan_count": len(floorplans),
        "brochure_count": len(brochures),
        "gallery": gallery,
        "floorplans": floorplans,
        "brochures": brochures,
        "master_plans": master_plans,
        "raw": raw,
        "source_run_id": row.get("run_id"),
        "scraped_at": row.get("scraped_at"),
    }


def _developer_logo_match_key(value: str | None) -> str:
    return developer_match_key(value)


def _developer_ranking_hidden(developer_name: str | None) -> bool:
    return _developer_logo_match_key(developer_name) in DEVELOPER_RANKING_HIDDEN_DEVELOPER_KEYS


def _developer_brand_name(developer_name: str) -> str:
    return developer_brand_name(developer_name)


def _developer_rankable_brand(developer_name: str) -> bool:
    return is_rankable_developer_brand(developer_name)


def _text_tokens(value: str | None) -> set[str]:
    normalized = re.sub(r"[^a-z0-9]+", " ", (value or "").lower())
    return {part for part in normalized.split() if len(part) > 2}


def _token_overlap(left: str | None, right: str | None) -> float:
    left_tokens = _text_tokens(left)
    right_tokens = _text_tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))


def _parse_catalogue_date(value: Any) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text_value = str(value or "").strip()
    if not text_value:
        return None
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%b %Y", "%B %Y"):
        try:
            parsed = datetime.strptime(text_value, pattern)
            return parsed.date()
        except ValueError:
            pass
    match = re.search(r"\b(20\d{2})\b", text_value)
    if match:
        return date(int(match.group(1)), 12, 31)
    return None


def _parse_epoch_date(value: Any) -> date | None:
    if value in (None, "", 0, "0"):
        return None
    try:
        timestamp = float(value)
    except (TypeError, ValueError):
        return None
    if timestamp <= 0:
        return None
    if timestamp > 10_000_000_000:
        timestamp = timestamp / 1000
    try:
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
    except (OSError, OverflowError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _raw_payload(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("raw")
    return raw if isinstance(raw, dict) else {}


def _reelly_launch_date(data: dict[str, Any]) -> date | None:
    raw = _raw_payload(data)
    return (
        _parse_epoch_date(raw.get("Launch_date"))
        or _parse_epoch_date(raw.get("launch_date"))
        or _parse_catalogue_date(raw.get("Launch_date"))
    )


def _reelly_unsold_units(data: dict[str, Any]) -> int | None:
    raw = _raw_payload(data)
    parsed = _int_or_none(data.get("units_in_sale"))
    if parsed is None:
        parsed = _int_or_none(raw.get("units_in_sale"))
    if parsed is None:
        return None
    sale_status = str(data.get("sale_status") or raw.get("sale_status") or "").lower()
    if parsed == 0 and "out of stock" not in sale_status:
        return None
    return max(parsed, 0)


def _reelly_unit_total(data: dict[str, Any]) -> int | None:
    total = 0
    for item in _as_list(data.get("units")):
        if not isinstance(item, dict):
            continue
        units_amount = _int_or_none(item.get("units_amount"))
        if units_amount is not None and units_amount > 0:
            total += units_amount
    return total if total > 0 else None


def _developer_identity(
    developer_name: str, logos: dict[str, tuple[str, str]]
) -> tuple[str, str | None]:
    key = _developer_logo_match_key(developer_name)
    if not key:
        return developer_name, None
    if key in logos:
        return logos[key]
    for logo_key, identity in logos.items():
        if len(logo_key) >= 4 and (logo_key in key or key in logo_key):
            return identity
    return developer_name, None


def _float_or_none(value: Any) -> float | None:
    return float(value) if value is not None else None


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
