from typing import Optional

from app.clickhouse.client import query
from app.market.scope import project_filter_clause as _project_filter


async def fetch_sales_transactions_rows(
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
):
    where_clauses = ["trans_group_en = 'Sales'"]
    params: dict = {}

    if property_usage:
        where_clauses.append("property_usage_en = {property_usage:String}")
        params["property_usage"] = property_usage

    if property_type:
        where_clauses.append("property_type = {property_type:String}")
        params["property_type"] = property_type

    if property_sub_type:
        where_clauses.append("property_sub_type_en = {property_sub_type:String}")
        params["property_sub_type"] = property_sub_type

    if rooms:
        where_clauses.append("rooms_en = {rooms:String}")
        params["rooms"] = rooms

    if registration_type:
        where_clauses.append("reg_type_en = {registration_type:String}")
        params["registration_type"] = registration_type

    if area:
        where_clauses.append("location_name = {area:String}")
        params["area"] = area

    if project:
        where_clauses.append(_project_filter(filter_type))
        params["project_name"] = project + " - %" if filter_type == "virtual_master" else project

    if building:
        where_clauses.append("building_name_en = {building:String}")
        params["building"] = building

    if timeframe_days is not None:
        where_clauses.append("instance_date BETWEEN today() - {timeframe_days:Int32} AND today()")
        params["timeframe_days"] = timeframe_days

    if area_min is not None:
        where_clauses.append("size_sqm >= {area_min:Decimal64(2)}")
        params["area_min"] = area_min

    if area_max is not None:
        where_clauses.append("size_sqm <= {area_max:Decimal64(2)}")
        params["area_max"] = area_max

    if price_min is not None:
        where_clauses.append("actual_worth >= {price_min:Decimal64(2)}")
        params["price_min"] = price_min

    if price_max is not None:
        where_clauses.append("actual_worth <= {price_max:Decimal64(2)}")
        params["price_max"] = price_max

    if price_per_sqm_min is not None:
        where_clauses.append("meter_sale_price >= {price_per_sqm_min:Decimal64(2)}")
        params["price_per_sqm_min"] = price_per_sqm_min

    if price_per_sqm_max is not None:
        where_clauses.append("meter_sale_price <= {price_per_sqm_max:Decimal64(2)}")
        params["price_per_sqm_max"] = price_per_sqm_max

    where_clause = " AND ".join(where_clauses)

    # Count total matching transactions
    count_sql = f"""
        SELECT count() as total
        FROM ch_transactions
        WHERE {where_clause}
    """
    count_result = await query(count_sql, params)
    total = count_result[0]["total"] if count_result else 0

    # Fetch transactions
    params["limit"] = limit
    params["offset"] = offset

    transactions_sql = f"""
        SELECT
            transaction_id,
            instance_date,
            property_usage_en,
            trans_group_en,
            procedure_name_en,
            property_type AS property_type_en,
            property_sub_type_en,
            rooms_en,
            procedure_area,
            actual_worth,
            meter_sale_price,
            reg_type_en,
            location_name AS area_name_en,
            project_name_en,
            building_name_en,
            nearest_landmark_en,
            nearest_metro_en,
            nearest_mall_en
        FROM ch_transactions
        WHERE {where_clause}
        ORDER BY instance_date DESC, transaction_id
        LIMIT {{limit:Int32}}
        OFFSET {{offset:Int32}}
    """
    transactions_result = await query(transactions_sql, params)
    return transactions_result, total


async def fetch_rental_contracts_rows(
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
):
    where_clauses = []
    params = {}

    if property_usage:
        where_clauses.append("property_usage_en = {property_usage:String}")
        params["property_usage"] = property_usage

    if property_type:
        where_clauses.append("property_type = {property_type:String}")
        params["property_type"] = property_type

    if property_sub_type:
        where_clauses.append("ejari_property_sub_type_en = {property_sub_type:String}")
        params["property_sub_type"] = property_sub_type

    if tenant_type:
        where_clauses.append("tenant_type_en = {tenant_type:String}")
        params["tenant_type"] = tenant_type

    if contract_reg_type:
        where_clauses.append("contract_reg_type_en = {contract_reg_type:String}")
        params["contract_reg_type"] = contract_reg_type

    if area:
        where_clauses.append("location_name = {area:String}")
        params["area"] = area

    if project:
        where_clauses.append(_project_filter(filter_type))
        params["project_name"] = project + " - %" if filter_type == "virtual_master" else project

    if timeframe_days is not None:
        where_clauses.append(
            "contract_start_date BETWEEN today() - {timeframe_days:Int32} AND today()"
        )
        params["timeframe_days"] = timeframe_days

    if area_min is not None:
        where_clauses.append("size_sqm >= {area_min:Decimal64(2)}")
        params["area_min"] = area_min

    if area_max is not None:
        where_clauses.append("size_sqm <= {area_max:Decimal64(2)}")
        params["area_max"] = area_max

    if rent_min is not None:
        where_clauses.append("annual_amount >= {rent_min:Decimal64(2)}")
        params["rent_min"] = rent_min

    if rent_max is not None:
        where_clauses.append("annual_amount <= {rent_max:Decimal64(2)}")
        params["rent_max"] = rent_max

    where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Count total matching contracts
    count_sql = f"""
        SELECT count() as total
        FROM ch_rent_contracts
        WHERE {where_clause}
    """
    count_result = await query(count_sql, params)
    total = count_result[0]["total"] if count_result else 0

    # Fetch rental contracts
    params["limit"] = limit
    params["offset"] = offset

    contracts_sql = f"""
        SELECT
            contract_id,
            contract_start_date,
            contract_end_date,
            property_usage_en,
            contract_reg_type_en,
            property_type AS ejari_property_type_en,
            ejari_property_sub_type_en,
            tenant_type_en,
            actual_area,
            annual_amount,
            contract_amount,
            location_name AS area_name_en,
            project_name_en,
            nearest_landmark_en,
            nearest_metro_en,
            nearest_mall_en,
            no_of_prop,
            is_free_hold
        FROM ch_rent_contracts
        WHERE {where_clause}
        ORDER BY contract_start_date DESC, contract_id
        LIMIT {{limit:Int32}}
        OFFSET {{offset:Int32}}
    """
    contracts_result = await query(contracts_sql, params)
    return contracts_result, total


async def export_sales_transactions_rows(
    export_limit,
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
    transaction_ids,
):
    params = {}
    if transaction_ids:
        ids_list = [tid.strip() for tid in transaction_ids.split(",")]
        # Build IN clause with parameter binding
        placeholders = ", ".join([f"{{id_{i}:String}}" for i in range(len(ids_list))])
        where_clause = f"transaction_id IN ({placeholders})"
        for i, tid in enumerate(ids_list):
            params[f"id_{i}"] = tid
    else:
        # Build WHERE clause from filters — always enforce Sales only
        where_clauses = ["trans_group_en = 'Sales'"]

        if property_usage:
            where_clauses.append("property_usage_en = {property_usage:String}")
            params["property_usage"] = property_usage

        if property_type:
            where_clauses.append("property_type = {property_type:String}")
            params["property_type"] = property_type

        if property_sub_type:
            where_clauses.append("property_sub_type_en = {property_sub_type:String}")
            params["property_sub_type"] = property_sub_type

        if rooms:
            where_clauses.append("rooms_en = {rooms:String}")
            params["rooms"] = rooms

        if registration_type:
            where_clauses.append("reg_type_en = {registration_type:String}")
            params["registration_type"] = registration_type

        if area:
            where_clauses.append("location_name = {area:String}")
            params["area"] = area

        if project:
            where_clauses.append(_project_filter(filter_type))
            params["project_name"] = (
                project + " - %" if filter_type == "virtual_master" else project
            )

        if building:
            where_clauses.append("building_name_en = {building:String}")
            params["building"] = building

        if timeframe_days is not None:
            where_clauses.append(
                "instance_date BETWEEN today() - {timeframe_days:Int32} AND today()"
            )
            params["timeframe_days"] = timeframe_days

        if area_min is not None:
            where_clauses.append("size_sqm >= {area_min:Decimal64(2)}")
            params["area_min"] = area_min

        if area_max is not None:
            where_clauses.append("size_sqm <= {area_max:Decimal64(2)}")
            params["area_max"] = area_max

        if price_min is not None:
            where_clauses.append("actual_worth >= {price_min:Decimal64(2)}")
            params["price_min"] = price_min

        if price_max is not None:
            where_clauses.append("actual_worth <= {price_max:Decimal64(2)}")
            params["price_max"] = price_max

        if price_per_sqm_min is not None:
            where_clauses.append("meter_sale_price >= {price_per_sqm_min:Decimal64(2)}")
            params["price_per_sqm_min"] = price_per_sqm_min

        if price_per_sqm_max is not None:
            where_clauses.append("meter_sale_price <= {price_per_sqm_max:Decimal64(2)}")
            params["price_per_sqm_max"] = price_per_sqm_max

        where_clause = " AND ".join(where_clauses)

    # Fetch transactions (limit 50,000 if using filters)
    if transaction_ids:
        limit_clause = ""
    else:
        params["export_limit"] = export_limit
        limit_clause = "LIMIT {export_limit:Int32}"

    export_sql = f"""
        SELECT
            transaction_id,
            instance_date,
            property_usage_en,
            trans_group_en,
            procedure_name_en,
            property_type AS property_type_en,
            property_sub_type_en,
            rooms_en,
            procedure_area,
            actual_worth,
            meter_sale_price,
            reg_type_en,
            location_name AS area_name_en,
            project_name_en,
            building_name_en,
            nearest_landmark_en,
            nearest_metro_en,
            nearest_mall_en
        FROM ch_transactions
        WHERE {where_clause}
        ORDER BY instance_date DESC, transaction_id
        {limit_clause}
    """
    results = await query(export_sql, params)
    return results


async def export_rental_contracts_rows(
    export_limit,
    area,
    area_max,
    area_min,
    contract_ids,
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
):
    params = {}
    if contract_ids:
        ids_list = [cid.strip() for cid in contract_ids.split(",")]
        # Build IN clause with parameter binding
        placeholders = ", ".join([f"{{id_{i}:String}}" for i in range(len(ids_list))])
        where_clause = f"contract_id IN ({placeholders})"
        for i, cid in enumerate(ids_list):
            params[f"id_{i}"] = cid
    else:
        # Build WHERE clause from filters
        where_clauses = []

        if property_usage:
            where_clauses.append("property_usage_en = {property_usage:String}")
            params["property_usage"] = property_usage

        if property_type:
            where_clauses.append("property_type = {property_type:String}")
            params["property_type"] = property_type

        if property_sub_type:
            where_clauses.append("ejari_property_sub_type_en = {property_sub_type:String}")
            params["property_sub_type"] = property_sub_type

        if tenant_type:
            where_clauses.append("tenant_type_en = {tenant_type:String}")
            params["tenant_type"] = tenant_type

        if contract_reg_type:
            where_clauses.append("contract_reg_type_en = {contract_reg_type:String}")
            params["contract_reg_type"] = contract_reg_type

        if area:
            where_clauses.append("location_name = {area:String}")
            params["area"] = area

        if project:
            where_clauses.append(_project_filter(filter_type))
            params["project_name"] = (
                project + " - %" if filter_type == "virtual_master" else project
            )

        if timeframe_days is not None:
            where_clauses.append(
                "contract_start_date BETWEEN today() - {timeframe_days:Int32} AND today()"
            )
            params["timeframe_days"] = timeframe_days

        if area_min is not None:
            where_clauses.append("size_sqm >= {area_min:Decimal64(2)}")
            params["area_min"] = area_min

        if area_max is not None:
            where_clauses.append("size_sqm <= {area_max:Decimal64(2)}")
            params["area_max"] = area_max

        if rent_min is not None:
            where_clauses.append("annual_amount >= {rent_min:Decimal64(2)}")
            params["rent_min"] = rent_min

        if rent_max is not None:
            where_clauses.append("annual_amount <= {rent_max:Decimal64(2)}")
            params["rent_max"] = rent_max

        where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

    # Fetch contracts (limit 50,000 if using filters)
    if contract_ids:
        limit_clause = ""
    else:
        params["export_limit"] = export_limit
        limit_clause = "LIMIT {export_limit:Int32}"

    export_sql = f"""
        SELECT
            contract_id,
            contract_start_date,
            contract_end_date,
            property_usage_en,
            contract_reg_type_en,
            property_type AS ejari_property_type_en,
            ejari_property_sub_type_en,
            tenant_type_en,
            actual_area,
            annual_amount,
            contract_amount,
            location_name AS area_name_en,
            project_name_en,
            nearest_landmark_en,
            nearest_metro_en,
            nearest_mall_en,
            no_of_prop,
            is_free_hold
        FROM ch_rent_contracts
        WHERE {where_clause}
        ORDER BY contract_start_date DESC, contract_id
        {limit_clause}
    """
    results = await query(export_sql, params)
    return results


def _append_common_transaction_filters(
    where_clauses: list[str],
    params: dict,
    *,
    property_usage: Optional[str] = None,
    project: Optional[str] = None,
    filter_type: str = "project",
    property_type_field: str,
    property_type: Optional[str] = None,
    property_sub_type_field: str,
    property_sub_type: Optional[str] = None,
    area: Optional[str] = None,
    timeframe_field: str,
    timeframe_days: Optional[int] = None,
    area_field: str,
    area_min: Optional[float] = None,
    area_max: Optional[float] = None,
) -> None:
    if property_usage:
        where_clauses.append("property_usage_en = {property_usage:String}")
        params["property_usage"] = property_usage

    if project:
        where_clauses.append(_project_filter(filter_type))
        params["project_name"] = project + " - %" if filter_type == "virtual_master" else project

    if property_type:
        where_clauses.append(f"{property_type_field} = {{property_type:String}}")
        params["property_type"] = property_type

    if property_sub_type:
        where_clauses.append(f"{property_sub_type_field} = {{property_sub_type:String}}")
        params["property_sub_type"] = property_sub_type

    if area:
        where_clauses.append("location_name = {area:String}")
        params["area"] = area

    if timeframe_days is not None:
        where_clauses.append(
            f"{timeframe_field} BETWEEN today() - {{timeframe_days:Int32}} AND today()"
        )
        params["timeframe_days"] = timeframe_days

    if area_min is not None:
        where_clauses.append(f"{area_field} >= {{area_min:Decimal64(2)}}")
        params["area_min"] = area_min

    if area_max is not None:
        where_clauses.append(f"{area_field} <= {{area_max:Decimal64(2)}}")
        params["area_max"] = area_max


async def fetch_sales_facets(
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
):
    where_clauses = ["trans_group_en = 'Sales'"]
    params: dict = {}

    _append_common_transaction_filters(
        where_clauses,
        params,
        property_usage=property_usage,
        project=project,
        filter_type=filter_type,
        property_type_field="property_type",
        property_type=property_type,
        property_sub_type_field="property_sub_type_en",
        property_sub_type=property_sub_type,
        area=area,
        timeframe_field="instance_date",
        timeframe_days=timeframe_days,
        area_field="size_sqm",
        area_min=area_min,
        area_max=area_max,
    )

    if rooms:
        where_clauses.append("rooms_en = {rooms:String}")
        params["rooms"] = rooms

    if registration_type:
        where_clauses.append("reg_type_en = {registration_type:String}")
        params["registration_type"] = registration_type

    if building:
        where_clauses.append("building_name_en = {building:String}")
        params["building"] = building

    if price_min is not None:
        where_clauses.append("actual_worth >= {price_min:Decimal64(2)}")
        params["price_min"] = price_min

    if price_max is not None:
        where_clauses.append("actual_worth <= {price_max:Decimal64(2)}")
        params["price_max"] = price_max

    if price_per_sqm_min is not None:
        where_clauses.append("meter_sale_price >= {price_per_sqm_min:Decimal64(2)}")
        params["price_per_sqm_min"] = price_per_sqm_min

    if price_per_sqm_max is not None:
        where_clauses.append("meter_sale_price <= {price_per_sqm_max:Decimal64(2)}")
        params["price_per_sqm_max"] = price_per_sqm_max

    where_clause = " AND ".join(where_clauses)

    property_types_result = await query(
        f"""
        SELECT DISTINCT property_type AS property_type_en
        FROM ch_transactions
        WHERE {where_clause} AND property_type != ''
        ORDER BY property_type_en
        """,
        params,
    )
    property_sub_types_result = await query(
        f"""
        SELECT DISTINCT property_sub_type_en
        FROM ch_transactions
        WHERE {where_clause} AND property_sub_type_en != ''
        ORDER BY property_sub_type_en
        """,
        params,
    )
    rooms_result = await query(
        f"""
        SELECT DISTINCT rooms_en
        FROM ch_transactions
        WHERE {where_clause} AND rooms_en != ''
        """,
        params,
    )
    reg_types_result = await query(
        f"""
        SELECT DISTINCT reg_type_en
        FROM ch_transactions
        WHERE {where_clause} AND reg_type_en != ''
        ORDER BY reg_type_en
        """,
        params,
    )
    areas_result = await query(
        f"""
        SELECT DISTINCT location_name AS area_name_en
        FROM ch_transactions
        WHERE {where_clause} AND location_name != ''
        ORDER BY area_name_en
        """,
        params,
    )
    projects_result = await query(
        f"""
        SELECT DISTINCT project_name_en
        FROM ch_transactions
        WHERE {where_clause} AND project_name_en != ''
        ORDER BY project_name_en
        """,
        params,
    )
    buildings_result = await query(
        f"""
        SELECT DISTINCT building_name_en
        FROM ch_transactions
        WHERE {where_clause} AND building_name_en != ''
        ORDER BY building_name_en
        LIMIT 1000
        """,
        params,
    )
    return (
        property_types_result,
        property_sub_types_result,
        rooms_result,
        reg_types_result,
        areas_result,
        projects_result,
        buildings_result,
    )


async def fetch_rental_facets(
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
):
    where_clauses: list[str] = []
    params: dict = {}

    _append_common_transaction_filters(
        where_clauses,
        params,
        property_usage=property_usage,
        project=project,
        filter_type=filter_type,
        property_type_field="property_type",
        property_type=property_type,
        property_sub_type_field="ejari_property_sub_type_en",
        property_sub_type=property_sub_type,
        area=area,
        timeframe_field="contract_start_date",
        timeframe_days=timeframe_days,
        area_field="size_sqm",
        area_min=area_min,
        area_max=area_max,
    )

    if tenant_type:
        where_clauses.append("tenant_type_en = {tenant_type:String}")
        params["tenant_type"] = tenant_type

    if contract_reg_type:
        where_clauses.append("contract_reg_type_en = {contract_reg_type:String}")
        params["contract_reg_type"] = contract_reg_type

    if rent_min is not None:
        where_clauses.append("annual_amount >= {rent_min:Decimal64(2)}")
        params["rent_min"] = rent_min

    if rent_max is not None:
        where_clauses.append("annual_amount <= {rent_max:Decimal64(2)}")
        params["rent_max"] = rent_max

    where_clause = " AND ".join(where_clauses) if where_clauses else "1=1"

    property_types_result = await query(
        f"""
        SELECT DISTINCT property_type AS ejari_property_type_en
        FROM ch_rent_contracts
        WHERE {where_clause} AND property_type != ''
        ORDER BY ejari_property_type_en
        """,
        params,
    )
    property_sub_types_result = await query(
        f"""
        SELECT DISTINCT ejari_property_sub_type_en
        FROM ch_rent_contracts
        WHERE {where_clause} AND ejari_property_sub_type_en != ''
        ORDER BY ejari_property_sub_type_en
        """,
        params,
    )
    tenant_types_result = await query(
        f"""
        SELECT DISTINCT tenant_type_en
        FROM ch_rent_contracts
        WHERE {where_clause} AND tenant_type_en != ''
        ORDER BY tenant_type_en
        """,
        params,
    )
    contract_reg_types_result = await query(
        f"""
        SELECT DISTINCT contract_reg_type_en
        FROM ch_rent_contracts
        WHERE {where_clause} AND contract_reg_type_en != ''
        ORDER BY contract_reg_type_en
        """,
        params,
    )
    areas_result = await query(
        f"""
        SELECT DISTINCT location_name AS area_name_en
        FROM ch_rent_contracts
        WHERE {where_clause} AND location_name != ''
        ORDER BY area_name_en
        """,
        params,
    )
    projects_result = await query(
        f"""
        SELECT DISTINCT project_name_en
        FROM ch_rent_contracts
        WHERE {where_clause} AND project_name_en != ''
        ORDER BY project_name_en
        """,
        params,
    )
    return (
        property_types_result,
        property_sub_types_result,
        tenant_types_result,
        contract_reg_types_result,
        areas_result,
        projects_result,
    )
