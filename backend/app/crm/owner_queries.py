from __future__ import annotations

from app.clickhouse.client import query


async def fetch_import_locations():
    return await query(
        """SELECT DISTINCT arrayJoin([area_name, project_name]) AS name,
                           area_name, project_name
           FROM dxbi_sales_unit_events WHERE area_name != '' OR project_name != ''"""
    )


async def fetch_import_context_sales(parameters):
    return await query(
        r"""SELECT transaction_date, sale_amount_aed, unit_number,
                   replaceRegexpAll(lowerUTF8(unit_number), '\\s+', '') AS unit_key,
                   building_name, building_key, project_name, area_name, area_key,
                   property_type, market_status, sale_key
            FROM dxbi_sales_unit_events FINAL
            WHERE (area_name IN {locations:Array(String)}
                   OR project_name IN {locations:Array(String)})
              AND transaction_date BETWEEN {start_date:Date} AND {end_date:Date}
            SETTINGS max_threads=2, max_memory_usage=2000000000""",
        parameters,
    )


async def fetch_cadastral_lands(parameters: dict) -> list[dict]:
    return await query(
        """SELECT DISTINCT area_id, area_en, master_project_en, project_en,
                  land_number, land_sub_number, pre_registration_number
           FROM dld_od_lands_bronze
           WHERE land_number IN {land_numbers:Array(String)}""",
        parameters,
    )


async def fetch_transaction_signatures(parameters):
    return await query(
        """
            SELECT
                transaction_date,
                sale_amount_aed,
                replaceRegexpAll(lowerUTF8(unit_number), '\\s+', '') AS unit_key,
                unit_number,
                building_name,
                building_key,
                project_name,
                area_name,
                area_key,
                sale_key,
                detail_url
            FROM dxbi_sales_unit_events FINAL
            WHERE transaction_date IN {dates:Array(Date)}
              AND sale_amount_aed IN {prices:Array(UInt64)}
            """,
        parameters,
    )


async def fetch_near_transaction_signatures(parameters):
    return await query(
        """
            SELECT
                transaction_date,
                sale_amount_aed,
                replaceRegexpAll(lowerUTF8(unit_number), '\\s+', '') AS unit_key,
                unit_number,
                building_name,
                building_key,
                project_name,
                area_name,
                area_key,
                sale_key,
                detail_url
            FROM dxbi_sales_unit_events FINAL
            WHERE transaction_date IN {dates:Array(Date)}
              AND sale_amount_aed IN {prices:Array(UInt64)}
            """,
        parameters,
    )


async def fetch_unit_sales(parameters):
    return await query(
        """
            SELECT
                replaceRegexpAll(lowerUTF8(unit_number), '\\s+', '') AS unit_key,
                unit_number,
                building_name,
                building_key,
                project_name,
                area_name,
                area_key,
                transaction_date,
                sale_amount_aed,
                property_type,
                market_status,
                sale_key,
                detail_url
            FROM dxbi_sales_unit_events FINAL
            WHERE replaceRegexpAll(lowerUTF8(unit_number), '\\s+', '')
                IN {unit_keys:Array(String)}
            ORDER BY unit_key, transaction_date
            """,
        parameters,
    )


async def fetch_rental_source_columns():
    return await query(
        "SELECT count() AS has_unit FROM system.columns WHERE database=currentDatabase() "
        "AND table='dxbi_rental_unit_candidates' AND name='unit_number'"
    )


async def fetch_rental_evidence(parameters, has_source_unit: bool):
    source_unit = "c.unit_number" if has_source_unit else "''"
    return await query(
        rf"""
            WITH
                if({source_unit} != '', {source_unit}, m.unit_number) AS observed_unit,
                replaceRegexpAll(upperUTF8(replaceAll(observed_unit, '–', '-')), '\\s+', '') AS compact_unit,
                if(match(compact_unit, '^\\d+\\.0$'), substring(compact_unit, 1, length(compact_unit)-2), compact_unit) AS numeric_unit,
                if(startsWith(numeric_unit, 'TAG-'), arrayElement(splitByChar('-', numeric_unit), -1), numeric_unit) AS normalized_unit
            SELECT
                c.event_key AS event_key,
                c.unit_candidate_key AS unit_candidate_key,
                c.building_name AS building_name,
                c.project_name AS project_name,
                c.area_name AS area_name,
                c.property_type AS property_type,
                c.lease_end AS lease_end,
                c.annual_rent_aed AS annual_rent_aed,
                observed_unit AS unit_number,
                normalized_unit AS unit_key,
                if({source_unit} != '', 'source_unit', 'sale_inference') AS rental_match_method
            FROM dxbi_rental_unit_candidates AS c FINAL
            LEFT JOIN dxbi_rental_sale_matches AS m FINAL USING (unit_candidate_key)
            WHERE
                ({source_unit} != '' OR m.match_status = 'unique')
                AND normalized_unit IN {{units:Array(String)}}
                AND c.lease_end >= {{window_start:Date}}
                AND c.lease_end <= {{window_end:Date}}
            ORDER BY ({source_unit} != '') DESC, c.lease_end DESC, c.unit_candidate_key, c.event_key
            SETTINGS max_threads=2, max_memory_usage=2000000000
            """,
        parameters,
    )


async def fetch_owner_property_signatures(parameters):
    return await query(
        """
        SELECT
            transaction_date,
            sale_amount_aed,
            replaceRegexpAll(lowerUTF8(unit_number), '\\s+', '') AS unit_key,
            unit_number,
            building_name,
            building_key,
            project_name,
            area_name,
            area_key,
            property_type,
            market_status,
            bedrooms,
            size_sqft,
            built_up_area_sqft,
            price_per_sqft_aed,
            sale_key
        FROM dxbi_sales_unit_events FINAL
        WHERE transaction_date IN {dates:Array(Date)}
          AND sale_amount_aed IN {prices:Array(UInt64)}
        """,
        parameters,
    )


async def fetch_registry_sales_evidence(parameters):
    return await query(
        """
                SELECT
                    building_name,
                    unit_number,
                    transaction_date,
                    sale_amount_aed,
                    ltv_pct,
                    seller_type,
                    detail_url
                FROM dxbi_sales_unit_events FINAL
                WHERE
                    lowerUTF8(building_name) IN {buildings:Array(String)}
                    AND lowerUTF8(unit_number) IN {units:Array(String)}
                ORDER BY transaction_date DESC
                LIMIT 500
                """,
        parameters,
    )


async def fetch_registry_lease_evidence(parameters):
    return await query(
        """
                SELECT
                    c.unit_candidate_key,
                    c.lease_start,
                    c.lease_end,
                    c.annual_rent_aed,
                    c.contract_state,
                    c.observed_contracts
                FROM dxbi_rental_unit_candidates AS c FINAL
                WHERE c.unit_candidate_key IN {candidate_keys:Array(String)}
                LIMIT 200
                """,
        parameters,
    )


async def fetch_sales_by_keys(parameters):
    return await query(
        """
            SELECT
                replaceRegexpAll(lowerUTF8(unit_number), '\\s+', '') AS unit_key,
                unit_number,
                building_name,
                building_key,
                project_name,
                area_name,
                area_key,
                sale_key
            FROM dxbi_sales_unit_events FINAL
            WHERE sale_key IN {sale_keys:Array(String)}
            """,
        parameters,
    )


async def fetch_unit_sales_by_short_key(parameters):
    # short_unit_key applies the import-side unit normalization (TAG-AL2-237 -> 237, 101.0 -> 101)
    # to registry units, so both sides are compared in the same form.
    return await query(
        r"""
            WITH
                replaceRegexpAll(upperUTF8(replaceAll(unit_number, '–', '-')), '\\s+', '') AS compact_unit,
                if(match(compact_unit, '^\\d+\\.0$'), substring(compact_unit, 1, length(compact_unit)-2), compact_unit) AS numeric_unit,
                lowerUTF8(if(startsWith(numeric_unit, 'TAG-'), arrayElement(splitByChar('-', numeric_unit), -1), numeric_unit)) AS short_unit_key
            SELECT
                replaceRegexpAll(lowerUTF8(unit_number), '\\s+', '') AS unit_key,
                short_unit_key,
                unit_number,
                building_name,
                building_key,
                project_name,
                area_name,
                area_key,
                transaction_date,
                sale_amount_aed,
                sale_key,
                detail_url
            FROM dxbi_sales_unit_events FINAL
            WHERE short_unit_key IN {unit_keys:Array(String)}
            ORDER BY unit_key, transaction_date
            """,
        parameters,
    )
