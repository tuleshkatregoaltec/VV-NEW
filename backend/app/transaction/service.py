import logging
from datetime import date, datetime
from typing import Optional

from app.transaction import queries
from app.transaction.models import (
    RentalContractListResponse,
    RentalContractResponse,
    TransactionListResponse,
    TransactionResponse,
)

logger = logging.getLogger(__name__)


def _date_only(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return value


async def fetch_sales_transactions(
    limit: int,
    offset: int,
    property_usage: Optional[str] = None,
    property_type: Optional[str] = None,
    property_sub_type: Optional[str] = None,
    rooms: Optional[str] = None,
    registration_type: Optional[str] = None,
    area: Optional[str] = None,
    project: Optional[str] = None,
    filter_type: str = "project",
    building: Optional[str] = None,
    timeframe_days: Optional[int] = None,
    area_min: Optional[float] = None,
    area_max: Optional[float] = None,
    price_min: Optional[float] = None,
    price_max: Optional[float] = None,
    price_per_sqm_min: Optional[float] = None,
    price_per_sqm_max: Optional[float] = None,
) -> TransactionListResponse:
    """
    Fetch paginated sales transactions from ClickHouse.

    Optimized query using sort key (property_usage_en, instance_date, transaction_id).
    """
    # Build WHERE clause — always enforce Sales only (separate endpoint exists for mortgages/gifts)
    transactions_result, total = await queries.fetch_sales_transactions_rows(
        area,
        area_max,
        area_min,
        building,
        filter_type,
        limit,
        offset,
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

    # Convert to response models
    items = [
        TransactionResponse(
            transaction_id=row["transaction_id"],
            instance_date=_date_only(row["instance_date"]),
            trans_group_en=row["trans_group_en"],
            procedure_name_en=row["procedure_name_en"],
            property_type_en=row["property_type_en"],
            property_sub_type_en=row["property_sub_type_en"],
            rooms_en=row["rooms_en"],
            area_name_en=row["area_name_en"],
            project_name_en=row["project_name_en"],
            building_name_en=row["building_name_en"],
            procedure_area=float(row["procedure_area"]) if row["procedure_area"] else None,
            actual_worth=float(row["actual_worth"]) if row["actual_worth"] else None,
            meter_sale_price=float(row["meter_sale_price"]) if row["meter_sale_price"] else None,
            reg_type_en=row["reg_type_en"],
            nearest_landmark_en=row["nearest_landmark_en"],
            nearest_metro_en=row["nearest_metro_en"],
            nearest_mall_en=row["nearest_mall_en"],
        )
        for row in transactions_result
    ]

    return TransactionListResponse(transactions=items, total=total, limit=limit, offset=offset)


async def fetch_rental_contracts(
    limit: int,
    offset: int,
    property_usage: Optional[str] = None,
    property_type: Optional[str] = None,
    property_sub_type: Optional[str] = None,
    tenant_type: Optional[str] = None,
    contract_reg_type: Optional[str] = None,
    area: Optional[str] = None,
    project: Optional[str] = None,
    filter_type: str = "project",
    timeframe_days: Optional[int] = None,
    area_min: Optional[float] = None,
    area_max: Optional[float] = None,
    rent_min: Optional[float] = None,
    rent_max: Optional[float] = None,
) -> RentalContractListResponse:
    """
    Fetch paginated rental contracts from ClickHouse.

    Optimized query using sort key (property_usage_en, contract_start_date, contract_id).
    """
    # Build WHERE clause
    contracts_result, total = await queries.fetch_rental_contracts_rows(
        area,
        area_max,
        area_min,
        contract_reg_type,
        filter_type,
        limit,
        offset,
        project,
        property_sub_type,
        property_type,
        property_usage,
        rent_max,
        rent_min,
        tenant_type,
        timeframe_days,
    )

    # Convert to response models
    items = [
        RentalContractResponse(
            contract_id=row["contract_id"],
            contract_start_date=row["contract_start_date"],
            contract_end_date=row["contract_end_date"],
            ejari_property_type_en=row["ejari_property_type_en"],
            ejari_property_sub_type_en=row["ejari_property_sub_type_en"],
            tenant_type_en=row["tenant_type_en"],
            contract_reg_type_en=row["contract_reg_type_en"],
            area_name_en=row["area_name_en"],
            project_name_en=row["project_name_en"],
            actual_area=float(row["actual_area"]) if row["actual_area"] else None,
            annual_amount=float(row["annual_amount"]) if row["annual_amount"] else None,
            contract_amount=float(row["contract_amount"]) if row["contract_amount"] else None,
            no_of_prop=row["no_of_prop"],
            is_free_hold=bool(row["is_free_hold"]),
            nearest_landmark_en=row["nearest_landmark_en"],
            nearest_metro_en=row["nearest_metro_en"],
            nearest_mall_en=row["nearest_mall_en"],
        )
        for row in contracts_result
    ]

    return RentalContractListResponse(contracts=items, total=total, limit=limit, offset=offset)
