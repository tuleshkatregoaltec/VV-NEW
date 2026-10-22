from __future__ import annotations

import asyncio
from typing import Any

from app.clickhouse.client import ClientDisconnected, query, run_cancellable


async def fetch_sales_inventory(parameters):
    return await query(
        """
                SELECT
                    sales.building_key,
                    lowerUTF8(sales.unit_number) AS normalized_unit,
                    argMax(sales.sale_key, sales.transaction_date) AS sale_key,
                    argMax(sales.building_name, sales.transaction_date) AS building_name,
                    argMax(sales.unit_number, sales.transaction_date) AS unit_number,
                    argMax(sales.area_name, sales.transaction_date) AS area_name,
                    argMax(sales.property_type, sales.transaction_date) AS property_type,
                    argMax(sales.bedrooms, sales.transaction_date) AS bedrooms,
                    argMax(sales.size_sqft, sales.transaction_date) AS size_sqft,
                    argMax(sales.sale_amount_aed, sales.transaction_date) AS sale_amount_aed,
                    max(sales.transaction_date) AS latest_sale_date
                FROM (
                    SELECT *
                    FROM dxbi_sales_unit_events FINAL
                    WHERE
                        unit_number != ''
                        AND (
                            {area:String} = ''
                            OR positionCaseInsensitiveUTF8(area_name, {area:String}) > 0
                            OR positionCaseInsensitiveUTF8(building_name, {area:String}) > 0
                        )
                ) AS sales
                GROUP BY sales.building_key, normalized_unit
                ORDER BY latest_sale_date DESC
                LIMIT 120
                """,
        parameters,
    )


async def fetch_instruction_evidence(parameters):
    return await query(
        """
                SELECT
                    sale_key,
                    transaction_date,
                    building_name,
                    unit_number,
                    area_name,
                    property_type,
                    bedrooms,
                    size_sqft,
                    sale_amount_aed,
                    ltv_pct,
                    seller_type,
                    detail_url
                FROM dxbi_sales_unit_events FINAL
                WHERE
                    positionCaseInsensitiveUTF8(building_name, {building:String}) > 0
                    AND lowerUTF8(unit_number) = lowerUTF8({unit_number:String})
                ORDER BY transaction_date DESC
                LIMIT 1
                """,
        parameters,
    )


async def fetch_matching_rentals(parameters):
    return await query(
        """
        SELECT
            c.unit_candidate_key,
            c.event_key,
            c.building_name,
            c.project_name,
            c.area_name,
            c.property_type,
            c.bedrooms,
            c.size_sqft,
            c.annual_rent_aed,
            c.purchase_price_aed,
            c.lease_start,
            c.lease_end,
            c.contract_state,
            c.observed_contracts,
            m.unit_number,
            m.last_purchase_date,
            m.matched_sale_price,
            m.match_status,
            m.latest_detail_url
        FROM dxbi_rental_unit_candidates AS c FINAL
        LEFT JOIN dxbi_rental_sale_matches AS m FINAL USING (unit_candidate_key)
        WHERE
            c.lease_end BETWEEN today() - 1825 AND today() + 90
            AND (
                {area:String} = ''
                OR positionCaseInsensitiveUTF8(c.area_name, {area:String}) > 0
                OR positionCaseInsensitiveUTF8(c.project_name, {area:String}) > 0
                OR positionCaseInsensitiveUTF8(c.building_name, {area:String}) > 0
            )
            AND (
                {property_type:String} = ''
                OR lowerUTF8(c.property_type) = lowerUTF8({property_type:String})
            )
        ORDER BY
            abs(dateDiff('day', today(), c.lease_end)) ASC,
            c.annual_rent_aed DESC
        LIMIT 40
        """,
        parameters,
    )


DXBI_TABLE = "dxbi_rental_events"

DXBI_CANDIDATE_TABLE = "dxbi_rental_unit_candidates"

_LEGACY_RENTAL_TABLES = (
    "dxbi_rental_events",
    "dxbi_rental_unit_candidates",
    "dxbi_rental_sale_matches",
)

VALID_BREAKDOWNS = {
    "area": "area_name",
    "project": "project_name",
    "building": "building_name",
}


async def _query_with_legacy_rentals(
    sql: str, params: dict[str, Any], *, saved_candidates: bool = False
) -> list[dict[str, Any]]:
    """Keep saved v1 identities readable without guessing a replacement v2 unit."""
    rows = await query(sql, params)
    legacy_params = dict(params)
    if saved_candidates:
        found = {str(row["saved_candidate_key"]) for row in rows}
        missing = [key for key in params["candidate_keys"] if key not in found]
        if not missing:
            return rows
        legacy_params["candidate_keys"] = missing
    elif rows:
        return rows
    required = [name for name in _LEGACY_RENTAL_TABLES if name in sql]
    available = await query(
        "SELECT count() AS tables FROM system.tables "
        "WHERE database=currentDatabase() AND name IN {names:Array(String)}",
        {"names": [name + "_legacy" for name in required]},
    )
    if not required or not available or int(available[0]["tables"]) != len(required):
        return rows
    legacy_sql = sql
    for name in required:
        legacy_sql = legacy_sql.replace(name, name + "_legacy")
    legacy_rows = await query(legacy_sql, legacy_params)
    return [*rows, *[{**row, "_legacy_identity": True} for row in legacy_rows]]


_LEAD_CTE = """
WITH coverage AS (
    SELECT max(source_shard_date) AS data_complete_through
    FROM dxbi_rental_events FINAL
),
latest_unit AS (
    SELECT
        c.event_key,
        c.unit_candidate_key AS unit_candidate_key,
        c.unit_cohort_key,
        c.location_name,
        if(
            c.property_type IN ('Villa', 'Townhouse'),
            '',
            coalesce(nullIf(g.display_building_name, ''), c.building_name)
        ) AS building_name,
        coalesce(nullIf(g.display_project_name, ''), c.project_name) AS project_name,
        coalesce(nullIf(g.display_area_name, ''), c.area_name) AS area_name,
        c.building_name AS registry_building_name,
        c.project_name AS registry_project_name,
        c.area_name AS registry_area_name,
        g.canonical_location_id,
        coalesce(nullIf(g.match_status, ''), 'unmapped') AS geography_match_status,
        coalesce(nullIf(g.coordinate_precision, ''), 'missing') AS coordinate_precision,
        g.latitude,
        g.longitude,
        c.property_type,
        c.bedrooms,
        c.size_sqft,
        c.contract_amount_aed,
        c.contract_term_days,
        c.contract_term_months,
        c.annual_rent_aed,
        c.rent_normalization_version,
        c.address_normalization_version,
        c.purchase_price_aed,
        c.lease_start,
        c.lease_end,
        c.contract_state,
        c.observed_contracts,
        c.last_scraped_at,
        c.loaded_at,
        coalesce(nullIf(c.unit_number, ''), m.unit_number) AS unit_number,
        m.identity_sale_date,
        m.identity_sale_price,
        m.last_purchase_date,
        m.matched_sale_price,
        m.latest_property_type,
        m.latest_price_per_sqft_aed,
        m.latest_capital_gain_pct,
        m.latest_ltv_pct,
        m.latest_seller_type,
        m.latest_seller_transaction_count,
        m.latest_market_status,
        m.latest_detail_url,
        m.matching_units,
        m.match_status AS unit_match_status
    FROM dxbi_rental_unit_candidates AS c FINAL
    LEFT JOIN geo_crm_rental_hierarchy AS g
        ON g.registry_area_name = c.area_name
        AND g.registry_project_name = c.project_name
        AND g.registry_building_name = c.building_name
        AND g.source_grain = multiIf(
            c.property_type IN ('Villa', 'Townhouse'), 'landed_phase',
            c.building_name != '', 'building',
            c.project_name != '', 'project',
            'area'
        )
    LEFT JOIN dxbi_rental_sale_matches AS m FINAL
        ON m.unit_candidate_key = c.unit_candidate_key
),
identified_unit AS (
    SELECT
        *,
        if(
            unit_match_status = 'unique' AND ifNull(unit_number, '') != '',
            concat(
                'unit:',
                replaceRegexpAll(
                    lowerUTF8(coalesce(nullIf(building_name, ''), nullIf(project_name, ''), location_name)),
                    '[^a-z0-9]+',
                    ''
                ),
                ':',
                replaceRegexpAll(lowerUTF8(area_name), '[^a-z0-9]+', ''),
                ':',
                upperUTF8(unit_number)
            ),
            concat('candidate:', unit_candidate_key)
        ) AS property_identity_key
    FROM latest_unit
),
ranked_unit AS (
    SELECT
        *,
        row_number() OVER (
            PARTITION BY property_identity_key
            ORDER BY lease_start DESC, lease_end DESC, last_scraped_at DESC, event_key DESC
        ) AS identity_rank
    FROM identified_unit
),
lead_evidence AS (
    SELECT
        r.*,
        c.data_complete_through,
        multiIf(
            property_type IN ('Apartment', 'Villa'), 'residential',
            property_type = 'Commercial', 'commercial',
            'other'
        ) AS asset_class,
        multiIf(
            property_type = 'Commercial'
                AND latest_property_type IN (
                    'Office',
                    'Shop',
                    'Warehouse',
                    'Show Rooms',
                    'Workshop',
                    'Clinic',
                    'Gymnasium',
                    'Hotel'
                ),
            latest_property_type,
            property_type != '',
            property_type,
            'Other'
        ) AS property_subtype,
        dateDiff('day', {as_of:Date}, lease_end) AS days_to_expiry,
        multiIf(
            unit_match_status != 'unique', 'unit_unresolved',
            last_purchase_date >= lease_start
                AND last_purchase_date < lease_end,
                'sale_during_lease',
            'stable_rental_history'
        ) AS ownership_timing,
        multiIf(
            lease_end >= {as_of:Date}, 'upcoming',
            unit_match_status != 'unique', 'unit_unresolved',
            last_purchase_date >= lease_start
                AND last_purchase_date < lease_end,
                'ownership_review',
            'unlet_signal'
        ) AS lease_evidence_state,
        multiIf(
            lease_end < {as_of:Date}, 'expired',
            lease_end <= addDays({as_of:Date}, 30), 'expiring_30',
            lease_end <= addDays({as_of:Date}, 60), 'expiring_60',
            'expiring_90'
        ) AS lead_status,
        if(
            unit_match_status = 'unique',
            'high',
            if(purchase_price_aed > 0, 'medium', 'low')
        )
            AS match_confidence
    FROM ranked_unit AS r
    CROSS JOIN coverage AS c
    WHERE
        identity_rank = 1
        AND
        lease_end >= addDays({as_of:Date}, -{expired_days:Int32})
        AND lease_end <= addDays({as_of:Date}, 90)
        AND NOT (
            unit_match_status = 'unique'
            AND last_purchase_date >= lease_end
        )
),
leads AS (
    SELECT
        *,
        greatest(
            0,
            least(
                100,
                multiIf(
                    lead_status = 'expired' AND lease_evidence_state = 'unlet_signal', 88,
                    lead_status = 'expiring_30', 84,
                    lead_status = 'expiring_60', 72,
                    60
                )
                + if(unit_match_status = 'unique', 6, 0)
                + if(contract_state = 'Renewed', 2, 0)
                - if(ownership_timing = 'sale_during_lease', 25, 0)
            )
        ) AS raw_score,
        if(raw_score >= 90, 'urgent', if(raw_score >= 75, 'high', 'medium')) AS priority
    FROM lead_evidence
)
"""


def _where_clause(
    *,
    status: str | None,
    priority: str | None,
    area: str | None,
    project: str | None,
    building: str | None,
    search: str | None,
    asset_class: str | None = None,
    property_type: str | None = None,
    unlet_only: bool = False,
) -> tuple[str, dict[str, Any]]:
    conditions = ["1 = 1"]
    params: dict[str, Any] = {}
    if unlet_only:
        # Vacant: the exact unit's lease ended, no later lease is registered and it was not sold.
        conditions.append("lease_evidence_state = 'unlet_signal'")
    if status:
        conditions.append("lead_status = {status:String}")
        params["status"] = status
    if priority:
        conditions.append("priority = {priority:String}")
        params["priority"] = priority
    if area:
        conditions.append("area_name = {area:String}")
        params["area"] = area
    if project:
        conditions.append("project_name = {project:String}")
        params["project"] = project
    if building:
        conditions.append("building_name = {building:String}")
        params["building"] = building
    if asset_class:
        conditions.append("asset_class = {asset_class:String}")
        params["asset_class"] = asset_class
    if property_type:
        conditions.append("property_subtype = {property_type:String}")
        params["property_type"] = property_type
    if search:
        conditions.append(
            """
            positionCaseInsensitiveUTF8(
                concat(
                    building_name,
                    ' ',
                    project_name,
                    ' ',
                    area_name,
                    ' ',
                    bedrooms,
                    ' ',
                    property_subtype,
                    ' ',
                    ifNull(unit_number, '')
                ),
                {search:String}
            ) > 0
            """
        )
        params["search"] = search
    return " AND ".join(conditions), params


def _filter_metadata_sql(facet_where: str) -> str:
    """Return live lead facets so every dropdown option can produce a result."""
    return (
        _LEAD_CTE
        + f"""
        SELECT
            (SELECT count() FROM dxbi_rental_events FINAL) AS rows,
            (
                SELECT countIf(match_status = 'unique')
                FROM dxbi_rental_sale_matches FINAL
            ) AS exact_unit_matches,
            min(lease_start) AS min_date,
            max(lease_start) AS max_date,
            (SELECT max(source_shard_date) FROM dxbi_rental_events FINAL)
                AS data_complete_through,
            arraySort(groupUniqArrayIf(area_name, area_name != '')) AS areas,
            arraySort(
                groupUniqArrayIf(
                    project_name,
                    project_name != ''
                    AND {{filter_area:String}} != ''
                    AND area_name = {{filter_area:String}}
                )
            ) AS projects,
            arraySort(
                groupUniqArrayIf(
                    building_name,
                    building_name != ''
                    AND {{filter_area:String}} != ''
                    AND area_name = {{filter_area:String}}
                    AND {{filter_project:String}} != ''
                    AND project_name = {{filter_project:String}}
                )
            ) AS buildings
        FROM leads
        WHERE {facet_where}
        """
    )


async def _tables_exist() -> bool:
    rows = await query(
        """
        SELECT count() AS count
        FROM system.tables
        WHERE
            database = currentDatabase()
            AND name IN (
                {events_table:String},
                {candidate_table:String},
                {sales_table:String},
                {match_table:String}
            )
        """,
        {
            "events_table": DXBI_TABLE,
            "candidate_table": DXBI_CANDIDATE_TABLE,
            "sales_table": "dxbi_sales_unit_events",
            "match_table": "dxbi_rental_sale_matches",
        },
    )
    return bool(rows and rows[0]["count"] == 4)


async def fetch_dashboard(
    anchor,
    area,
    asset_class,
    breakdown,
    building,
    expired_days,
    limit,
    offset,
    priority,
    project,
    property_type,
    search,
    status,
):
    where, filter_params = _where_clause(
        status=status,
        priority=priority,
        area=area,
        project=project,
        building=building,
        search=search,
        asset_class=asset_class,
        property_type=property_type,
    )
    asset_where, asset_filter_params = _where_clause(
        status=status,
        priority=priority,
        area=area,
        project=project,
        building=building,
        search=search,
    )
    facet_where, facet_filter_params = _where_clause(
        status=status,
        priority=priority,
        area=None,
        project=None,
        building=None,
        search=search,
        asset_class=asset_class,
        property_type=property_type,
    )
    params = {
        **filter_params,
        "as_of": anchor.isoformat(),
        "expired_days": expired_days,
        "limit": limit,
        "offset": offset,
    }
    dimension = VALID_BREAKDOWNS[breakdown]

    lead_sql = (
        _LEAD_CTE
        + f"""
        SELECT *
        FROM leads
        WHERE {where}
        ORDER BY raw_score DESC, lease_end ASC, annual_rent_aed DESC
        LIMIT {{limit:Int32}} OFFSET {{offset:Int32}}
        """
    )
    summary_sql = (
        _LEAD_CTE
        + f"""
        SELECT
            count() AS total,
            countIf(priority = 'urgent') AS urgent,
            countIf(
                lead_status = 'expired'
                AND lease_evidence_state = 'unlet_signal'
                AND ownership_timing = 'stable_rental_history'
                AND unit_match_status = 'unique'
            ) AS expired,
            countIf(lead_status = 'expiring_30') AS expiring_30,
            countIf(lead_status = 'expiring_60') AS expiring_60,
            countIf(lead_status = 'expiring_90') AS expiring_90,
            countIf(
                lead_status = 'expired'
                AND lease_evidence_state = 'unlet_signal'
                AND ownership_timing = 'stable_rental_history'
                AND unit_match_status = 'unique'
            ) AS sale_candidates,
            sumIf(
                annual_rent_aed,
                ownership_timing = 'stable_rental_history'
            ) AS annual_rent_at_risk
        FROM leads
        WHERE {where}
        """
    )
    breakdown_sql = (
        _LEAD_CTE
        + f"""
        SELECT
            if({dimension} = '', 'Unresolved hierarchy', {dimension}) AS label,
            count() AS total,
            countIf(priority = 'urgent') AS urgent,
            countIf(lead_status = 'expired') AS expired,
            countIf(lead_status = 'expiring_30') AS expiring_30,
            countIf(lead_status = 'expiring_60') AS expiring_60,
            countIf(lead_status = 'expiring_90') AS expiring_90,
            sumIf(
                annual_rent_aed,
                ownership_timing = 'stable_rental_history'
            ) AS potential_value
        FROM leads
        WHERE {where}
        GROUP BY label
        ORDER BY urgent DESC, total DESC
        LIMIT 12
        """
    )
    asset_breakdown_sql = (
        _LEAD_CTE
        + f"""
        SELECT
            asset_class,
            count() AS total,
            countIf(priority = 'urgent') AS urgent,
            countIf(unit_match_status = 'unique') AS exact_unit_matches,
            sumIf(
                annual_rent_aed,
                ownership_timing = 'stable_rental_history'
            ) AS annual_rent_at_risk,
            arraySort(groupUniqArray(property_subtype)) AS property_types
        FROM leads
        WHERE {asset_where}
        GROUP BY asset_class
        ORDER BY
            multiIf(asset_class = 'residential', 1, asset_class = 'commercial', 2, 3)
        """
    )
    metadata_sql = _filter_metadata_sql(facet_where)
    metadata_params = {
        **facet_filter_params,
        "as_of": anchor.isoformat(),
        "expired_days": expired_days,
        "filter_area": area or "",
        "filter_project": project or "",
    }

    lead_rows, summary_rows, breakdown_rows, asset_rows, metadata_rows = await asyncio.gather(
        query(lead_sql, params),
        query(summary_sql, params),
        query(breakdown_sql, params),
        query(
            asset_breakdown_sql,
            {
                **asset_filter_params,
                "as_of": anchor.isoformat(),
                "expired_days": expired_days,
            },
        ),
        query(metadata_sql, metadata_params),
    )
    return lead_rows, summary_rows, breakdown_rows, asset_rows, metadata_rows


_CANDIDATE_SOURCE = "FROM dxbi_rental_unit_candidates AS c FINAL"
assert _LEAD_CTE.count(_CANDIDATE_SOURCE) == 1
# The lead query for chosen areas only. Leases are narrowed to those areas before the joins and
# the per-unit ranking, so one area costs a fraction of all of Dubai. The registry area may be
# renamed by the geography crosswalk, so registry names that display as a chosen area are kept
# too; the exact display-area filter still runs on the result.
_AREA_LEAD_CTE = _LEAD_CTE.replace(
    _CANDIDATE_SOURCE,
    """FROM (
        SELECT *, event_key
        FROM dxbi_rental_unit_candidates FINAL
        WHERE
            has({areas:Array(String)}, area_name)
            OR area_name IN (
                SELECT registry_area_name
                FROM geo_crm_rental_hierarchy
                WHERE has({areas:Array(String)}, display_area_name)
            )
    ) AS c""",
)


async def run_until_disconnected(is_disconnected, work):
    """Run `work` with time-limited queries; None (queries stopped) if the caller went away."""
    try:
        return await run_cancellable(is_disconnected, work)
    except ClientDisconnected:
        return None


async def fetch_renewal_leads(anchor, areas, forward_days, lookback_days, limit, offset, kind=None):
    """Return one agent's lease-expiry leads: their areas, inside the forward/look-back windows.

    Only leases tied to a known unit number become leads. `kind` narrows them to vacant units
    (lease ended, no later lease registered) or to leases still running.
    """
    kind_condition = {
        None: "",
        "vacant": "AND lease_evidence_state = 'unlet_signal'",
        "upcoming": "AND lease_end >= {as_of:Date}",
    }[kind]
    params = {
        "as_of": anchor.isoformat(),
        "expired_days": lookback_days,
        "forward_days": forward_days,
        "areas": list(areas),
        "limit": limit,
        "offset": offset,
    }
    # One query: window totals are computed over every match before LIMIT applies.
    rows = await query(
        _AREA_LEAD_CTE
        + """
        SELECT
            *,
            count() OVER () AS _window_total,
            countIf(lease_end = addDays({as_of:Date}, {forward_days:Int32})) OVER ()
                AS _window_entered_today
        FROM leads
        WHERE
            has({areas:Array(String)}, area_name)
            AND lease_end <= addDays({as_of:Date}, {forward_days:Int32})
            AND ifNull(unit_number, '') != ''
            """
        + kind_condition
        + """
        ORDER BY lease_end ASC, raw_score DESC, event_key
        LIMIT {limit:Int32} OFFSET {offset:Int32}
        """,
        params,
    )
    # A page past the end has no rows to carry the totals; it reports zero.
    counts = {
        "total": rows[0]["_window_total"] if rows else 0,
        "entered_today": rows[0]["_window_entered_today"] if rows else 0,
    }
    return rows, [counts]


async def fetch_renewals(
    anchor,
    *,
    status,
    unlet_only,
    expired_days,
    area,
    property_type,
    project,
    building,
    limit,
    offset,
):
    """Assets › Renewals: one page of leases with its total, plus the options for its filters.

    Two queries instead of the dashboard's five. With an area picked, leases are narrowed to it
    before the per-unit work (the same narrowing as the agent's renewal leads).
    """
    cte = _AREA_LEAD_CTE if area else _LEAD_CTE
    where, filter_params = _where_clause(
        status=status,
        priority=None,
        area=area,
        project=project,
        building=building,
        search=None,
        property_type=property_type,
        unlet_only=unlet_only,
    )
    # The options list what the other filters allow, so picking one never empties its own list.
    facet_where, facet_params = _where_clause(
        status=status,
        priority=None,
        area=area,
        project=None,
        building=None,
        search=None,
        unlet_only=unlet_only,
    )
    base = {
        "as_of": anchor.isoformat(),
        "expired_days": expired_days,
        "areas": [area] if area else [],
    }
    rows_sql = (
        cte
        + f"""
        SELECT *, count() OVER () AS _window_total
        FROM leads
        WHERE {where}
        ORDER BY raw_score DESC, lease_end ASC, annual_rent_aed DESC
        LIMIT {{limit:Int32}} OFFSET {{offset:Int32}}
        """
    )
    facets_sql = (
        cte
        + f"""
        SELECT
            arraySort(groupUniqArray(property_subtype)) AS property_types,
            arraySort(
                groupUniqArrayIf(project_name, project_name != '' AND {{filter_area:String}} != '')
            ) AS projects,
            arraySort(
                groupUniqArrayIf(
                    building_name,
                    building_name != ''
                    AND {{filter_project:String}} != ''
                    AND project_name = {{filter_project:String}}
                )
            ) AS buildings
        FROM leads
        WHERE {facet_where}
        """
    )
    rows, facets = await asyncio.gather(
        query(rows_sql, {**base, **filter_params, "limit": limit, "offset": offset}),
        query(
            facets_sql,
            {
                **base,
                **facet_params,
                "filter_area": area or "",
                "filter_project": project or "",
            },
        ),
    )
    total = rows[0]["_window_total"] if rows else 0
    return rows, total, (facets[0] if facets else {})


async def fetch_renewal_areas(anchor):
    """Areas in the lease-expiry dataset, with their leases ending in the next 90 days."""
    return await query(
        _LEAD_CTE
        + """
        SELECT
            area_name AS area,
            countIf(lease_end >= {as_of:Date}) AS upcoming_90_days
        FROM leads
        WHERE area_name != ''
        GROUP BY area_name
        ORDER BY area_name
        """,
        {"as_of": anchor.isoformat(), "expired_days": 365},
    )


async def fetch_lead(parameters):
    return await _query_with_legacy_rentals(
        _LEAD_CTE + " SELECT * FROM leads WHERE event_key = {lead_id:String} LIMIT 1", parameters
    )


async def fetch_lead_rental_history(parameters):
    return await _query_with_legacy_rentals(
        """
        SELECT
            event_key,
            lease_start,
            lease_end,
            annual_rent_aed,
            contract_state
        FROM dxbi_rental_events FINAL
        WHERE unit_candidate_key = {candidate_key:String}
        ORDER BY lease_start DESC
        LIMIT 50
        """,
        parameters,
    )


async def fetch_exact_sale_history(parameters):
    return await query(
        """
                SELECT
                    sale_key,
                    transaction_date AS sale_date,
                    sale_amount_aed,
                    price_per_sqft_aed,
                    capital_gain_pct,
                    ltv_pct,
                    seller_type,
                    seller_transaction_count,
                    market_status,
                    detail_url
                FROM dxbi_sales_unit_events FINAL
                WHERE
                    unit_number = {unit_number:String}
                    AND building_key = {building_key:String}
                ORDER BY transaction_date DESC
                LIMIT 25
                """,
        parameters,
    )


async def fetch_dld_sale_history(parameters):
    return await query(
        """
                SELECT
                    transaction_id,
                    toDate(instance_date) AS sale_date,
                    trans_value,
                    procedure_name_en
                FROM ch_transactions
                WHERE
                    trans_group_en = 'Sales'
                    AND positionCaseInsensitiveUTF8(building_name_en, {building:String}) > 0
                    AND ({bedrooms:String} = '' OR rooms_en = {bedrooms:String})
                    AND (
                        {size_sqft:Float64} = 0
                        OR abs(procedure_area * 10.7639 - {size_sqft:Float64})
                            <= greatest(25, {size_sqft:Float64} * 0.03)
                    )
                ORDER BY instance_date DESC
                LIMIT 25
                """,
        parameters,
    )


async def fetch_candidate_key(parameters):
    return await _query_with_legacy_rentals(
        """
        SELECT unit_candidate_key
        FROM dxbi_rental_unit_candidates FINAL
        WHERE event_key = {lead_id:String}
        LIMIT 1
        """,
        parameters,
    )


async def fetch_saved_leads(parameters):
    return await _query_with_legacy_rentals(
        _LEAD_CTE
        + """
        SELECT
            i.unit_candidate_key AS saved_candidate_key,
            l.*
        FROM identified_unit AS i
        ANY INNER JOIN leads AS l USING (property_identity_key)
        WHERE i.unit_candidate_key IN {candidate_keys:Array(String)}
        """,
        parameters,
        saved_candidates=True,
    )
