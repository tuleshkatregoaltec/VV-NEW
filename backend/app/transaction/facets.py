import re
from typing import Optional

from app.transaction import queries
from app.transaction.models import FilterOptionsResponse


def _rooms_sort_key(room: str) -> tuple:
    """Studio first, then numeric bedroom counts in order, then anything else."""
    normalized = room.strip()
    if normalized.lower() == "studio":
        return (0, 0, normalized)
    m = re.match(r"^(\d+)", normalized)
    if m:
        return (1, int(m.group(1)), normalized)
    return (2, 0, normalized.lower())


def _clean_option(value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    return normalized or None


def _normalize_options(rows: list[dict], field: str) -> list[str]:
    values = {_clean_option(row.get(field)) for row in rows}
    return sorted(value for value in values if value is not None)


def _normalize_room_options(rows: list[dict], field: str) -> list[str]:
    values = {_clean_option(row.get(field)) for row in rows}
    return sorted((value for value in values if value is not None), key=_rooms_sort_key)


async def get_sales_filter_options(
    property_usage: Optional[str] = None,
    project: Optional[str] = None,
    filter_type: str = "project",
    property_type: Optional[str] = None,
    property_sub_type: Optional[str] = None,
    rooms: Optional[str] = None,
    registration_type: Optional[str] = None,
    area: Optional[str] = None,
    building: Optional[str] = None,
    timeframe_days: Optional[int] = None,
    area_min: Optional[float] = None,
    area_max: Optional[float] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    price_per_sqm_min: Optional[float] = None,
    price_per_sqm_max: Optional[float] = None,
) -> FilterOptionsResponse:
    """Get dynamically narrowed filter options for sales transactions."""

    (
        property_types_result,
        property_sub_types_result,
        rooms_result,
        reg_types_result,
        areas_result,
        projects_result,
        buildings_result,
    ) = await queries.fetch_sales_facets(
        area,
        area_max,
        area_min,
        building,
        filter_type,
        price_max,
        price_min,
        price_per_sqm_max,
        price_per_sqm_min,
        project,
        property_sub_type,
        property_type,
        property_usage,
        registration_type,
        rooms,
        timeframe_days,
    )

    return FilterOptionsResponse(
        property_types=_normalize_options(property_types_result, "property_type_en"),
        property_sub_types=_normalize_options(property_sub_types_result, "property_sub_type_en"),
        rooms=_normalize_room_options(rooms_result, "rooms_en"),
        registration_types=_normalize_options(reg_types_result, "reg_type_en"),
        areas=_normalize_options(areas_result, "area_name_en"),
        projects=_normalize_options(projects_result, "project_name_en"),
        buildings=_normalize_options(buildings_result, "building_name_en"),
    )


async def get_rental_filter_options(
    property_usage: Optional[str] = None,
    project: Optional[str] = None,
    filter_type: str = "project",
    property_type: Optional[str] = None,
    property_sub_type: Optional[str] = None,
    tenant_type: Optional[str] = None,
    contract_reg_type: Optional[str] = None,
    area: Optional[str] = None,
    timeframe_days: Optional[int] = None,
    area_min: Optional[float] = None,
    area_max: Optional[float] = None,
    rent_min: Optional[float] = None,
    rent_max: Optional[float] = None,
) -> FilterOptionsResponse:
    """Get dynamically narrowed filter options for rental contracts."""

    (
        property_types_result,
        property_sub_types_result,
        tenant_types_result,
        contract_reg_types_result,
        areas_result,
        projects_result,
    ) = await queries.fetch_rental_facets(
        area,
        area_max,
        area_min,
        contract_reg_type,
        filter_type,
        project,
        property_sub_type,
        property_type,
        property_usage,
        rent_max,
        rent_min,
        tenant_type,
        timeframe_days,
    )

    return FilterOptionsResponse(
        property_types=_normalize_options(property_types_result, "ejari_property_type_en"),
        property_sub_types=_normalize_options(
            property_sub_types_result, "ejari_property_sub_type_en"
        ),
        tenant_types=_normalize_options(tenant_types_result, "tenant_type_en"),
        contract_reg_types=_normalize_options(contract_reg_types_result, "contract_reg_type_en"),
        areas=_normalize_options(areas_result, "area_name_en"),
        projects=_normalize_options(projects_result, "project_name_en"),
        buildings=[],
    )
