from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from app.clickhouse.client import query
from app.clickhouse.client import query as ch_query
from app.listings.models import (
    ListingAssetClass,
    ListingMode,
)
from app.listings.policy import (
    MARKET_POSITION_THRESHOLD,
    MIN_BENCHMARK_COMPARABLES,
    _achieved_property_types,
    _category_ids,
    _price_basis,
    _price_period,
)
from app.market.reference import listing_area_terms


async def resolve_listing_area(mode: ListingMode, area: str | None) -> str | None:
    if not area:
        return None

    terms = listing_area_terms(area)
    params: dict[str, Any] = {
        "category_ids": [2, 4] if mode == "rent" else [1, 3],
        "area": area,
        "primary": terms[0],
    }
    match_clauses = []
    for index, term in enumerate(terms):
        term_key = f"area_term_{index}"
        params[term_key] = term
        match_clauses.append(
            f"""
            lower(area_name) = lower({{{term_key}:String}})
            OR positionCaseInsensitive(listing_location_names_json, {{{term_key}:String}}) > 0
            """
        )

    rows = await ch_query(
        f"""
        SELECT JSONExtract(listing_location_names_json, 'Array(String)')[2] AS area_name
        FROM pf_listings_bronze
        WHERE category_id IN {{category_ids:Array(UInt16)}}
          AND is_available = 1
          AND area_name != ''
          AND ({" OR ".join(f"({clause})" for clause in match_clauses)})
        GROUP BY area_name
        ORDER BY
            (lower(area_name) = lower({{area:String}})) DESC,
            (lower(area_name) = lower({{primary:String}})) DESC,
            count() DESC,
            area_name ASC
        LIMIT 1
        """,
        params,
    )
    return rows[0]["area_name"] if rows else area


async def fetch_inventory_freshness(parameters):
    return await query(
        "SELECT max(parseDateTimeBestEffortOrNull(scraped_at)) AS refreshed FROM pf_listings_bronze",
        parameters,
    )


LISTINGS_TABLE = "pf_listings_bronze"

PRICE_VALUE_EXPR = "JSONExtractInt(price_json, 'value')"

PRICE_PERIOD_EXPR = "lower(JSON_VALUE(price_json, '$.period'))"

SIZE_VALUE_EXPR = "JSONExtractFloat(size_json, 'value')"

PROPERTY_TYPE_EXPR = "JSON_VALUE(raw_property_json, '$.property_type')"

AREA_NAME_EXPR = "JSONExtract(listing_location_names_json, 'Array(String)')[2]"

PEER_LOCATION_EXPR = "arrayElement(JSONExtract(listing_location_names_json, 'Array(String)'), -1)"

LISTED_AT_EXPR = "parseDateTimeBestEffortOrNull(JSON_VALUE(raw_property_json, '$.listed_date'))"

SCRAPED_AT_EXPR = "parseDateTimeBestEffortOrNull(scraped_at)"

MONTHLY_PRICE_EXPR = f"""
multiIf(
    {PRICE_PERIOD_EXPR} IN ('year', 'yearly', 'annual', 'annually'), {PRICE_VALUE_EXPR} / 12,
    {PRICE_PERIOD_EXPR} IN ('week', 'weekly'), {PRICE_VALUE_EXPR} * 52 / 12,
    {PRICE_PERIOD_EXPR} IN ('day', 'daily'), {PRICE_VALUE_EXPR} * 365 / 12,
    {PRICE_VALUE_EXPR}
)
"""

ANNUAL_PRICE_EXPR = f"""
multiIf(
    {PRICE_PERIOD_EXPR} IN ('month', 'monthly'), {PRICE_VALUE_EXPR} * 12,
    {PRICE_PERIOD_EXPR} IN ('week', 'weekly'), {PRICE_VALUE_EXPR} * 52,
    {PRICE_PERIOD_EXPR} IN ('day', 'daily'), {PRICE_VALUE_EXPR} * 365,
    {PRICE_VALUE_EXPR}
)
"""

SIZE_BUCKET_EXPR = f"""
toUInt32(round(
    {SIZE_VALUE_EXPR} / multiIf(
        lowerUTF8({PROPERTY_TYPE_EXPR}) IN ('apartment', 'office', 'shop'), 50,
        lowerUTF8({PROPERTY_TYPE_EXPR}) IN ('villa', 'townhouse'), 250,
        500
    )
) * multiIf(
    lowerUTF8({PROPERTY_TYPE_EXPR}) IN ('apartment', 'office', 'shop'), 50,
    lowerUTF8({PROPERTY_TYPE_EXPR}) IN ('villa', 'townhouse'), 250,
    500
))
"""

VALID_BENCHMARK_SIZE_EXPR = f"""
multiIf(
    lowerUTF8({PROPERTY_TYPE_EXPR}) = 'apartment' AND lowerUTF8(bedrooms) = 'studio',
        {SIZE_VALUE_EXPR} BETWEEN 150 AND 1500,
    lowerUTF8({PROPERTY_TYPE_EXPR}) = 'apartment' AND toInt32OrNull(bedrooms) = 1,
        {SIZE_VALUE_EXPR} BETWEEN 250 AND 3500,
    lowerUTF8({PROPERTY_TYPE_EXPR}) = 'apartment' AND toInt32OrNull(bedrooms) = 2,
        {SIZE_VALUE_EXPR} BETWEEN 350 AND 6000,
    lowerUTF8({PROPERTY_TYPE_EXPR}) = 'apartment' AND toInt32OrNull(bedrooms) = 3,
        {SIZE_VALUE_EXPR} BETWEEN 500 AND 10000,
    lowerUTF8({PROPERTY_TYPE_EXPR}) = 'apartment',
        {SIZE_VALUE_EXPR} BETWEEN 500 AND 20000,
    lowerUTF8({PROPERTY_TYPE_EXPR}) IN ('villa', 'townhouse'),
        {SIZE_VALUE_EXPR} BETWEEN 500 AND 50000,
    lowerUTF8({PROPERTY_TYPE_EXPR}) IN ('office', 'shop'),
        {SIZE_VALUE_EXPR} BETWEEN 100 AND 100000,
    {SIZE_VALUE_EXPR} BETWEEN 100 AND 250000
)
"""


def _price_expr(price_basis: str) -> str:
    if price_basis == "monthly":
        return MONTHLY_PRICE_EXPR
    if price_basis == "annual":
        return ANNUAL_PRICE_EXPR
    return PRICE_VALUE_EXPR


def _market_price_expr(mode: ListingMode) -> str:
    return ANNUAL_PRICE_EXPR if mode == "rent" else PRICE_VALUE_EXPR


def _listing_sql(sql: str) -> str:
    return sql.replace("{LISTINGS_TABLE}", LISTINGS_TABLE).replace(
        "{PRICE_VALUE_EXPR}", PRICE_VALUE_EXPR
    )


def _where_sql(
    mode: ListingMode,
    search: str | None,
    area: str | None,
    property_type: str | None,
    bedrooms: str | None,
    bedrooms_in: list[str] | None,
    price_min: int | None,
    price_max: int | None,
    asset_class: ListingAssetClass | None = None,
    price_period: str | None = None,
    price_basis: str = "raw",
) -> tuple[str, dict[str, Any]]:
    clauses = ["category_id IN {category_ids:Array(UInt16)}", "is_available = 1"]
    params: dict[str, Any] = {"category_ids": list(_category_ids(mode, asset_class))}
    price_filter_expr = _price_expr(_price_basis(price_basis))
    if search:
        clauses.append(
            """
            (
                positionCaseInsensitive(title, {search:String}) > 0
                OR positionCaseInsensitive(reference, {search:String}) > 0
                OR positionCaseInsensitive(listing_location_names_json, {search:String}) > 0
                OR positionCaseInsensitive(agent_json, {search:String}) > 0
                OR positionCaseInsensitive(broker_json, {search:String}) > 0
            )
            """
        )
        params["search"] = search.strip()
    if area:
        clauses.append(
            "JSONExtract(listing_location_names_json, 'Array(String)')[2] = {area:String}"
        )
        params["area"] = area
    if property_type:
        clauses.append("JSON_VALUE(raw_property_json, '$.property_type') = {property_type:String}")
        params["property_type"] = property_type
    if bedrooms_in:
        clauses.append("bedrooms IN {bedrooms:Array(String)}")
        params["bedrooms"] = bedrooms_in
    elif bedrooms:
        clauses.append("bedrooms = {bedrooms:String}")
        params["bedrooms"] = bedrooms
    if normalized_period := _price_period(price_period):
        clauses.append(f"{PRICE_PERIOD_EXPR} = {{price_period:String}}")
        params["price_period"] = normalized_period
    if price_min is not None:
        clauses.append(f"{price_filter_expr} >= {{price_min:Int64}}")
        params["price_min"] = price_min
    if price_max is not None:
        clauses.append(f"{price_filter_expr} <= {{price_max:Int64}}")
        params["price_max"] = price_max
    return " AND ".join(clauses), params


def _next_month(period: str) -> str:
    parsed = datetime.strptime(f"{period}-01", "%Y-%m-%d")
    if parsed.month == 12:
        return f"{parsed.year + 1}-01-01"
    return f"{parsed.year}-{parsed.month + 1:02d}-01"


def _location_match_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


async def _achieved_trend(
    *,
    mode: ListingMode,
    asset_class: ListingAssetClass,
    periods: list[str],
    area: str | None,
    property_type: str | None,
    bedrooms: str | None,
) -> list[dict[str, Any]]:
    if not periods:
        return []
    date_column = "lease_start" if mode == "rent" else "transaction_date"
    table = "dxbi_rental_events" if mode == "rent" else "dxbi_sales_unit_events"
    psf_expr = "annual_rent_aed / size_sqft" if mode == "rent" else "price_per_sqft_aed"
    psf_bounds = "BETWEEN 5 AND 2000" if mode == "rent" else "BETWEEN 200 AND 12000"
    clauses = [
        f"{date_column} >= {{achieved_start:Date}}",
        f"{date_column} < {{achieved_end:Date}}",
        "property_type IN {achieved_property_types:Array(String)}",
        "size_sqft > 0",
        f"{psf_expr} {psf_bounds}",
    ]
    params: dict[str, Any] = {
        "achieved_start": f"{periods[0]}-01",
        "achieved_end": _next_month(periods[-1]),
        "achieved_property_types": _achieved_property_types(mode, asset_class, property_type),
    }
    if area_key := _location_match_key(area):
        clauses.append(
            "startsWith(lowerUTF8(replaceRegexpAll(area_name, '[^a-zA-Z0-9]', '')), "
            "{achieved_area_key:String})"
        )
        params["achieved_area_key"] = area_key
    if bedrooms:
        clauses.append("startsWith(lowerUTF8(bedrooms), {achieved_bedrooms:String})")
        params["achieved_bedrooms"] = bedrooms.strip().lower()

    return await query(
        f"""
        SELECT
            formatDateTime(toStartOfMonth({date_column}), '%Y-%m') AS period,
            quantileTDigest(0.5)({psf_expr}) AS median_achieved_price_per_sqft_aed,
            uniqExact({"event_key" if mode == "rent" else "sale_key"})
                AS achieved_transaction_count
        FROM {table} FINAL
        WHERE {" AND ".join(clauses)}
        GROUP BY period
        ORDER BY period ASC
        """,
        params,
    )


async def fetch_filter_modes(parameters):
    return await query(
        _listing_sql(
            """
        SELECT DISTINCT if(category_id IN (2, 4), 'rent', 'sale') AS listing_mode
        FROM {LISTINGS_TABLE}
        WHERE is_available = 1
        ORDER BY listing_mode
        """
        ),
        parameters,
    )


async def fetch_filter_areas(parameters):
    return await query(
        _listing_sql(
            """
        SELECT JSONExtract(listing_location_names_json, 'Array(String)')[2] AS area_name, count() AS n
        FROM {LISTINGS_TABLE}
        WHERE category_id IN {category_ids:Array(UInt16)}
          AND is_available = 1
          AND area_name != ''
        GROUP BY area_name
        ORDER BY n DESC, area_name ASC
        LIMIT 60
        """
        ),
        parameters,
    )


async def fetch_filter_property_types(parameters):
    return await query(
        _listing_sql(
            """
        SELECT JSON_VALUE(raw_property_json, '$.property_type') AS property_type, count() AS n
        FROM {LISTINGS_TABLE}
        WHERE category_id IN {category_ids:Array(UInt16)}
          AND is_available = 1
          AND property_type != ''
        GROUP BY property_type
        ORDER BY n DESC, property_type ASC
        LIMIT 40
        """
        ),
        parameters,
    )


async def fetch_filter_bedrooms(parameters):
    return await query(
        _listing_sql(
            """
        SELECT bedrooms
        FROM {LISTINGS_TABLE}
        WHERE category_id IN {category_ids:Array(UInt16)}
          AND is_available = 1
          AND bedrooms != ''
        GROUP BY bedrooms
        ORDER BY toInt32OrNull(bedrooms) ASC NULLS FIRST, bedrooms ASC
        """
        ),
        parameters,
    )


async def fetch_filter_prices(parameters):
    return await query(
        _listing_sql(
            """
        SELECT
            minIf({PRICE_VALUE_EXPR}, {PRICE_VALUE_EXPR} > 0)
                AS price_min,
            max({PRICE_VALUE_EXPR}) AS price_max
        FROM {LISTINGS_TABLE}
        WHERE category_id IN {category_ids:Array(UInt16)}
          AND is_available = 1
        """
        ),
        parameters,
    )


async def fetch_listing_count(filters):
    where, parameters = _where_sql(**filters)
    return await query(
        f"""
            SELECT count() AS total
            FROM {LISTINGS_TABLE}
            WHERE {where}
            """,
        parameters,
    )


async def fetch_listing_rows(filters, *, limit: int, offset: int):
    where, parameters = _where_sql(**filters)
    parameters = {**parameters, "limit": limit, "offset": offset}
    market_price_expr = _market_price_expr(filters["mode"])
    price_sort_expr = _price_expr(_price_basis(filters.get("price_basis", "raw")))
    return await query(
        f"""
        SELECT
            listing_id,
            category_id,
            title,
            reference,
            property_type_id,
            price_json,
            size_json,
            bedrooms,
            bathrooms,
            listing_location_names_json,
            lat,
            lng,
            is_available,
            is_verified,
            is_featured,
            is_premium,
            share_url,
            agent_json,
            broker_json,
            client_json,
            images_json,
            raw_property_json,
            run_id,
            scraped_at,
            {AREA_NAME_EXPR} AS benchmark_area,
            {PROPERTY_TYPE_EXPR} AS benchmark_property_type,
            {PEER_LOCATION_EXPR} AS benchmark_peer_location,
            {SIZE_BUCKET_EXPR} AS benchmark_size_bucket,
            {VALID_BENCHMARK_SIZE_EXPR} AS has_valid_benchmark_size,
            {LISTED_AT_EXPR} AS listed_at,
            coalesce({SCRAPED_AT_EXPR}, now()) AS snapshot_at,
            {market_price_expr} AS market_price_aed,
            {SIZE_VALUE_EXPR} AS size_sqft,
            if(
                listed_at >= toDateTime('2010-01-01') AND listed_at <= snapshot_at,
                dateDiff('day', listed_at, snapshot_at),
                NULL
            ) AS listing_age_days,
            if(market_price_aed > 0 AND size_sqft > 0, market_price_aed / size_sqft, NULL)
                AS price_per_sqft_aed
        FROM {LISTINGS_TABLE}
        WHERE {where}
        ORDER BY
            is_featured DESC,
            is_premium DESC,
            snapshot_at DESC NULLS LAST,
            {price_sort_expr} ASC NULLS LAST
        LIMIT {{limit:Int32}} OFFSET {{offset:Int32}}
        """,
        parameters,
    )


async def fetch_listing_benchmarks(parameters, mode: ListingMode):
    market_price_expr = _market_price_expr(mode)
    return await query(
        f"""
            SELECT
                category_id,
                {PEER_LOCATION_EXPR} AS benchmark_peer_location,
                {PROPERTY_TYPE_EXPR} AS benchmark_property_type,
                bedrooms,
                {SIZE_BUCKET_EXPR} AS benchmark_size_bucket,
                quantileTDigest(0.5)({market_price_expr} / {SIZE_VALUE_EXPR})
                    AS market_median_price_per_sqft_aed,
                count() AS comparable_count
            FROM {LISTINGS_TABLE}
            WHERE category_id IN {{category_ids:Array(UInt16)}}
              AND is_available = 1
              AND {PEER_LOCATION_EXPR} IN {{benchmark_peer_locations:Array(String)}}
              AND {PROPERTY_TYPE_EXPR} IN {{benchmark_property_types:Array(String)}}
              AND bedrooms IN {{benchmark_bedrooms:Array(String)}}
              AND {SIZE_BUCKET_EXPR} IN {{benchmark_size_buckets:Array(UInt32)}}
              AND {VALID_BENCHMARK_SIZE_EXPR}
              AND {market_price_expr} > 0
              AND {SIZE_VALUE_EXPR} > 0
            GROUP BY
                category_id,
                benchmark_peer_location,
                benchmark_property_type,
                bedrooms,
                benchmark_size_bucket
            """,
        parameters,
    )


async def fetch_listing_analytics(filters):
    where, parameters = _where_sql(**filters)
    market_price_expr = _market_price_expr(filters["mode"])
    return await query(
        f"""
        SELECT
            count() AS total,
            avgIf(market_price_aed, market_price_aed > 0) AS average_price_aed,
            quantileTDigestIf(0.5)(market_price_aed, market_price_aed > 0)
                AS median_price_aed,
            quantileTDigestIf(0.5)(price_per_sqft_aed, price_per_sqft_aed > 0)
                AS median_price_per_sqft_aed,
            avgIf(listing_age_days, listing_age_days IS NOT NULL) AS average_listing_age_days,
            quantileTDigestIf(0.5)(listing_age_days, listing_age_days IS NOT NULL)
                AS median_listing_age_days,
            countIf(listing_age_days BETWEEN 0 AND 30) AS newly_listed_30d,
            countIf(
                comparable_count >= {MIN_BENCHMARK_COMPARABLES}
                AND market_delta_pct < {-MARKET_POSITION_THRESHOLD}
            ) AS below_market_count,
            countIf(
                comparable_count >= {MIN_BENCHMARK_COMPARABLES}
                AND market_delta_pct BETWEEN {-MARKET_POSITION_THRESHOLD}
                    AND {MARKET_POSITION_THRESHOLD}
            ) AS near_market_count,
            countIf(
                comparable_count >= {MIN_BENCHMARK_COMPARABLES}
                AND market_delta_pct > {MARKET_POSITION_THRESHOLD}
            ) AS above_market_count,
            countIf(comparable_count >= {MIN_BENCHMARK_COMPARABLES}) AS benchmarked_count,
            countIf(is_verified = 1) AS verified_count,
            uniqExact(benchmark_area) AS area_count,
            max(snapshot_at) AS snapshot_at
        FROM (
            SELECT
                inventory.*,
                if(
                    inventory.listed_at >= toDateTime('2010-01-01')
                    AND inventory.listed_at <= inventory.snapshot_at,
                    dateDiff('day', inventory.listed_at, inventory.snapshot_at),
                    NULL
                ) AS listing_age_days,
                if(
                    inventory.market_price_aed > 0 AND inventory.size_sqft > 0,
                    inventory.market_price_aed / inventory.size_sqft,
                    NULL
                ) AS price_per_sqft_aed,
                benchmarks.comparable_count,
                if(
                    inventory.has_valid_benchmark_size
                    AND inventory.benchmark_peer_location != ''
                    AND
                    benchmarks.comparable_count >= {MIN_BENCHMARK_COMPARABLES}
                    AND benchmarks.market_median_price_per_sqft_aed > 0
                    AND inventory.market_price_aed > 0
                    AND inventory.size_sqft > 0,
                    (inventory.market_price_aed / inventory.size_sqft)
                        / benchmarks.market_median_price_per_sqft_aed - 1,
                    NULL
                ) AS market_delta_pct
            FROM (
                SELECT
                    category_id,
                    is_verified,
                    {AREA_NAME_EXPR} AS benchmark_area,
                    {PEER_LOCATION_EXPR} AS benchmark_peer_location,
                    {PROPERTY_TYPE_EXPR} AS benchmark_property_type,
                    bedrooms,
                    {SIZE_BUCKET_EXPR} AS benchmark_size_bucket,
                    {VALID_BENCHMARK_SIZE_EXPR} AS has_valid_benchmark_size,
                    {LISTED_AT_EXPR} AS listed_at,
                    coalesce({SCRAPED_AT_EXPR}, now()) AS snapshot_at,
                    {market_price_expr} AS market_price_aed,
                    {SIZE_VALUE_EXPR} AS size_sqft
                FROM {LISTINGS_TABLE}
                WHERE {where}
            ) AS inventory
            LEFT JOIN (
                SELECT
                    category_id,
                    {PEER_LOCATION_EXPR} AS benchmark_peer_location,
                    {PROPERTY_TYPE_EXPR} AS benchmark_property_type,
                    bedrooms,
                    {SIZE_BUCKET_EXPR} AS benchmark_size_bucket,
                    quantileTDigest(0.5)({market_price_expr} / {SIZE_VALUE_EXPR})
                        AS market_median_price_per_sqft_aed,
                    count() AS comparable_count
                FROM {LISTINGS_TABLE}
                WHERE category_id IN {{category_ids:Array(UInt16)}}
                  AND is_available = 1
                  AND {PEER_LOCATION_EXPR} != ''
                  AND {VALID_BENCHMARK_SIZE_EXPR}
                  AND {market_price_expr} > 0
                  AND {SIZE_VALUE_EXPR} > 0
                GROUP BY
                    category_id,
                    benchmark_peer_location,
                    benchmark_property_type,
                    bedrooms,
                    benchmark_size_bucket
            ) AS benchmarks
              ON inventory.category_id = benchmarks.category_id
             AND inventory.benchmark_peer_location = benchmarks.benchmark_peer_location
             AND inventory.benchmark_property_type = benchmarks.benchmark_property_type
             AND inventory.bedrooms = benchmarks.bedrooms
             AND inventory.benchmark_size_bucket = benchmarks.benchmark_size_bucket
        )
        """,
        parameters,
    )


async def fetch_listing_trend(filters):
    where, parameters = _where_sql(**filters)
    market_price_expr = _market_price_expr(filters["mode"])
    return await query(
        f"""
        SELECT
            formatDateTime(toStartOfMonth(listed_at), '%Y-%m') AS period,
            avg(market_price_aed) AS average_asking_price_aed,
            quantileTDigest(0.5)(market_price_aed) AS median_asking_price_aed,
            quantileTDigestIf(0.5)(
                market_price_aed / size_sqft,
                size_sqft > 0
            ) AS median_price_per_sqft_aed,
            count() AS listing_count
        FROM (
            SELECT
                {LISTED_AT_EXPR} AS listed_at,
                coalesce({SCRAPED_AT_EXPR}, now()) AS snapshot_at,
                {market_price_expr} AS market_price_aed,
                {SIZE_VALUE_EXPR} AS size_sqft
            FROM {LISTINGS_TABLE}
            WHERE {where}
        )
        WHERE listed_at >= toDateTime('2010-01-01')
          AND listed_at <= snapshot_at
          AND listed_at >= addMonths(toStartOfMonth(snapshot_at), -11)
          AND market_price_aed > 0
        GROUP BY period
        ORDER BY period ASC
        """,
        parameters,
    )


async def fetch_listing_detail(parameters):
    return await query(
        _listing_sql(
            """
        SELECT
            listing_id,
            category_id,
            title,
            reference,
            property_type_id,
            price_json,
            size_json,
            bedrooms,
            bathrooms,
            listing_location_names_json,
            lat,
            lng,
            is_available,
            is_verified,
            is_featured,
            is_premium,
            share_url,
            agent_json,
            broker_json,
            client_json,
            images_json,
            raw_property_json,
            raw_wrapper_json,
            run_id,
            scraped_at
        FROM {LISTINGS_TABLE}
        WHERE listing_id = {listing_id:String}
        LIMIT 1
        """
        ),
        parameters,
    )
