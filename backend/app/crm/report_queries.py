from app.clickhouse.client import query


async def fetch_dld_price_index(parameters):
    return await query(
        """
        SELECT
            toStartOfQuarter(instance_date) AS quarter,
            if(
                {is_commercial:UInt8} = 1,
                property_sub_type_en,
                if(property_type_en = 'Villa', 'Villa', 'Apartment')
            ) AS segment,
            uniqExact(transaction_id) AS transactions,
            median(trans_value / (procedure_area * 10.7639)) AS median_psf
        FROM ch_transactions
        WHERE
            trans_group_en = 'Sales'
            AND procedure_name_en IN (
                'Sell',
                'Sale',
                'Delayed Sell',
                'Sell - Pre registration',
                'Sale On Payment Plan'
            )
            AND (
                (
                    {is_commercial:UInt8} = 1
                    AND property_usage_en = 'Commercial'
                    AND property_type_en = 'Unit'
                    AND property_sub_type_en IN ('Office', 'Shop')
                )
                OR (
                    {is_commercial:UInt8} = 0
                    AND property_usage_en = 'Residential'
                    AND property_type_en IN ('Unit', 'Villa')
                )
            )
            AND instance_date >= {index_start:Date}
            AND instance_date < {current_quarter:Date}
            AND procedure_area > 0
            AND trans_value > 0
            AND trans_value / (procedure_area * 10.7639) BETWEEN 200 AND 12000
        GROUP BY quarter, segment
        ORDER BY quarter, segment
    """,
        parameters,
    )


async def fetch_registry_price_index(parameters):
    return await query(
        """
        SELECT
            toStartOfQuarter(transaction_date) AS quarter,
            property_type AS segment,
            uniqExact(sale_key) AS transactions,
            median(price_per_sqft_aed) AS median_psf
        FROM dxbi_sales_unit_events FINAL
        WHERE
            transaction_date >= {index_start:Date}
            AND transaction_date < {current_quarter:Date}
            AND (
                ({is_commercial:UInt8} = 1 AND property_type IN ('Office', 'Shop'))
                OR (
                    {is_commercial:UInt8} = 0
                    AND property_type IN ('Apartment', 'Villa')
                )
            )
            AND price_per_sqft_aed BETWEEN 200 AND 12000
        GROUP BY quarter, segment
        ORDER BY quarter, segment
    """,
        parameters,
    )


async def fetch_segment_price_index(parameters):
    return await query(
        """
        SELECT
            toStartOfQuarter(transaction_date) AS quarter,
            uniqExact(sale_key) AS transactions,
            median(price_per_sqft_aed) AS median_psf
        FROM dxbi_sales_unit_events FINAL
        WHERE
            transaction_date >= {index_start:Date}
            AND transaction_date < {current_quarter:Date}
            AND area_key = {area_key:String}
            AND ({segment_type:String} = '' OR property_type = {segment_type:String})
            AND price_per_sqft_aed BETWEEN 200 AND 12000
        GROUP BY quarter
        ORDER BY quarter
    """,
        parameters,
    )


async def fetch_sale_comparables(parameters):
    return await query(
        """
        SELECT
            transaction_date,
            building_name,
            unit_number,
            property_type,
            bedrooms,
            size_sqft,
            sale_amount_aed,
            price_per_sqft_aed,
            detail_url
        FROM dxbi_sales_unit_events FINAL
        WHERE
            transaction_date BETWEEN {period_start:Date} AND {period_end:Date}
            AND area_key = {area_key:String}
            AND (
                {sales_property_type:String} = ''
                OR property_type = {sales_property_type:String}
                OR (
                    {sales_property_type:String} = 'Commercial'
                    AND property_type IN (
                        'Office',
                        'Shop',
                        'Warehouse',
                        'Show Rooms',
                        'Workshop',
                        'Clinic',
                        'Commercial'
                    )
                )
            )
            AND size_sqft BETWEEN {low_size:Float64} AND {high_size:Float64}
        ORDER BY
            abs(size_sqft - {size:Float64}) ASC,
            transaction_date DESC
        LIMIT 250
    """,
        parameters,
    )


async def fetch_rental_comparables(parameters):
    return await query(
        """
        SELECT
            r.lease_start,
            r.building_name,
            m.unit_number,
            m.match_status AS unit_match_status,
            r.property_type,
            r.bedrooms,
            r.size_sqft,
            r.annual_rent_aed
        FROM dxbi_rental_events AS r FINAL
        LEFT JOIN dxbi_rental_sale_matches AS m FINAL USING (unit_candidate_key)
        WHERE
            r.lease_start BETWEEN {period_start:Date} AND {period_end:Date}
            AND r.event_key != {lead_id:String}
            AND r.area_name = {area_name:String}
            AND (
                {rental_property_type:String} = ''
                OR r.property_type = {rental_property_type:String}
            )
            AND ({bedrooms:String} = '' OR r.bedrooms = {bedrooms:String})
            AND r.size_sqft BETWEEN {low_size:Float64} AND {high_size:Float64}
        ORDER BY
            abs(r.size_sqft - {size:Float64}) ASC,
            r.lease_start DESC
        LIMIT 250
    """,
        parameters,
    )
