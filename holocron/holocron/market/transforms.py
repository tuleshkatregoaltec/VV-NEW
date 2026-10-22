"""Build market_sale_events / market_rental_events from DLD bronze and DXBI evidence.

Rules (measured in docs/data-pipelines/DXBI_RECONCILIATION.md):

- DLD is de-duplicated first: Pulse history before the open-data cut-over, open data from it
  (same rule as the silver layer), latest version of each corrected record.
- Each DXBI record is paired one-to-one with a DLD record. Sales: exact price, date within
  3 days, or within 30 days when the size also agrees within 1%. Rentals: exact start date
  with contract amount (tier 0) or annual amount (tier 1).
- Row status: ``matched`` (DLD row carrying DXBI unit identity), ``dld_only`` (DLD fills what
  DXBI lacks), ``dxbi_only`` (DXBI record with no DLD candidate at all — DXBI adds it),
  ``dxbi_probable_duplicate`` (DXBI record that had DLD candidates but lost the one-to-one
  pairing; kept for audit, not served, so nothing is double counted).
- Label drift is fixed here once: Mortgage -> Mortgages, Renew -> Renewed, off-plan status
  from either DLD field.
- Geography is resolved here once, through ``market_geo_crosswalk`` (built with each sales
  release from DLD's own records), into an official DLD district and, where the evidence
  supports it, a community. Raw source names stay in their original columns.

Statements name the served tables; ``holocron.market.release`` renders them against a run's
staging tables, so a build never writes to what the backend is reading.
"""

from __future__ import annotations

SQFT_PER_SQM = 10.7639

# DXBI is the primary source from these event dates; DLD is the row source before them
# (decision 2026-10-06, measured on the local copy: DXBI sales are 84-101% of DLD's monthly
# count every month since 2022 and 75-83% before; DXBI rentals start in January 2025).
# Inside the DXBI period a DXBI value wins whenever DXBI has one, DLD fills what DXBI lacks
# (empty or generic values, registry-only fields), and DLD rows no DXBI record accounts for
# are still served, labelled dld_only, so totals keep the records only DLD holds.
SALES_DXBI_FROM = "2022-01-01"
RENTALS_DXBI_FROM = "2025-01-01"

# One property-type vocabulary for both tables and both sources, in DXBI's words.
PROPERTY_TYPES = (
    "Apartment", "Villa", "Townhouse", "Plot", "Office", "Shop", "Warehouse", "Hotel Apartment",
    "Labour Accommodation", "Building", "Commercial", "Other",
)  # fmt: skip
# Values that name no specific type: a matched DLD row's specific type fills them.
_GENERIC_TYPES = "('', 'Commercial', 'Other')"
# Uniform columns on every event row (both tables), after GEO_COLUMNS. filled_from_dld /
# dld_disagrees name the uniform columns where, on a DXBI-primary row, DLD filled an empty
# DXBI value or held a different one: the audit trail of the precedence rule, never shown in
# the product.
UNIFORM_COLUMNS = ("property_type", "size_sqm", "location_name", "filled_from_dld", "dld_disagrees")
# Sales only: DLD's registration date where the row has a DLD record. DXBI dates 21% of matched
# sales one business day before DLD registers them (measured 2026-10-06); instance_date takes
# DXBI's date, this keeps DLD's so per-day completeness is still compared on one basis.
SALE_AUDIT_COLUMNS = ("dld_instance_date",)


# Source words (DXBI and DLD) for each uniform type; any other non-empty word is "Other".
_TYPE_WORDS: dict[str, tuple[str, ...]] = {
    "Apartment": ("Apartment", "Flat", "Studio", "Residential Flats"),
    "Villa": ("Villa", "Complex Villas", "Residential / Residential Villa"),
    "Townhouse": ("Townhouse", "Stacked Townhouses"),
    "Plot": ("Plot", "Land", "Open Land"),
    "Office": ("Office",),
    "Shop": ("Shop", "Showroom", "Show Rooms", "Kiosk", "Restaurant"),
    "Warehouse": (
        "Warehouse", "Warehouse complex", "Complex Warehouse", "Workshop", "Factory", "Store",
    ),
    "Hotel Apartment": ("Hotel Apartment", "Hotel apartments", "Hotel Rooms", "Hotel"),
    "Labour Accommodation": ("Labor Camps", "Staff Accommodation"),
    "Building": ("Building",),
    "Commercial": ("Commercial",),
}  # fmt: skip


def _uniform_type(word: str) -> str:
    """Map a property-type word from either source (DXBI or DLD) to PROPERTY_TYPES."""
    cases = ", ".join(
        f"{word} IN ({', '.join(repr(w) for w in words)}), '{name}'"
        for name, words in _TYPE_WORDS.items()
    )
    return f"multiIf({cases}, {word} = '', '', 'Other')"


# DLD keeps the useful type in the sub-type for units ("Unit" / "Office") and buildings.
_DLD_SALE_TYPE = f"""if(d.prop_type_en = 'Land', 'Plot', {
    _uniform_type(
        "if(d.prop_type_en IN ('Unit', 'Building') AND d.prop_sb_type_en NOT IN ('', 'Building'),"
        " d.prop_sb_type_en, d.prop_type_en)"
    )
})"""
_DLD_RENT_TYPE = _uniform_type(
    "if(d.prop_type_en IN ('Unit', '') AND d.prop_sub_type_en != '', d.prop_sub_type_en,"
    " d.prop_type_en)"
)


def _prefer(primary: str, fallback: str) -> str:
    """primary unless it names no specific type, then fallback (if that one does)."""
    return (
        f"if({primary} NOT IN {_GENERIC_TYPES} OR {fallback} IN {_GENERIC_TYPES}, "
        f"{primary}, {fallback})"
    )


def _audit(*entries: tuple[str, str]) -> str:
    """Array of the column names whose condition holds (empty array when none does)."""
    items = ", ".join(f"if({condition}, '{name}', '')" for name, condition in entries)
    return f"arrayFilter(c -> c != '', [{items}])"


# Large joins and sorts spill to disk instead of failing when memory is tight.
_HEAVY_QUERY_SETTINGS = """SETTINGS
    join_algorithm = 'grace_hash',
    max_bytes_before_external_sort = 2000000000,
    max_bytes_before_external_group_by = 2000000000"""

# Every column of the legacy ch_transactions / ch_rent_contracts views, in order. The backend
# reads these names; the canonical tables must keep all of them (tests enforce it).
SALE_CONTRACT_COLUMNS = (
    "row_key", "run_id", "scraped_at", "transaction_id", "transaction_number", "instance_date",
    "trans_group_id", "trans_group_en", "trans_group_ar", "procedure_id", "procedure_name_en",
    "procedure_name_ar", "procedure_area", "actual_area", "actual_worth", "trans_value",
    "total_buyer", "total_seller", "property_id", "property_type_id", "property_type_en",
    "property_type_ar", "property_sub_type_id", "property_sub_type_en", "property_sub_type_ar",
    "property_usage_id", "property_usage_en", "property_usage_ar", "rooms_en", "rooms_ar",
    "parking", "has_parking", "building_age", "parcel_id", "area_id", "area_name_en",
    "area_name_ar", "project_name_en", "project_name_ar", "master_project_en",
    "master_project_ar", "is_free_hold", "is_free_hold_en", "is_free_hold_ar", "is_offplan",
    "is_offplan_en", "is_offplan_ar", "nearest_metro_en", "nearest_metro_ar", "nearest_mall_en",
    "nearest_mall_ar", "nearest_landmark_en", "nearest_landmark_ar", "project_number",
    "building_name_en", "building_name_ar", "reg_type_id", "reg_type_en", "reg_type_ar",
    "meter_sale_price", "rent_value", "meter_rent_price", "no_of_parties_role_1",
    "no_of_parties_role_2", "no_of_parties_role_3",
)  # fmt: skip
SALE_EXTRA_COLUMNS = (
    "unit_number", "bedrooms", "market_status", "dxbi_sale_key", "dxbi_building_name",
    "dxbi_transaction_date", "source_system", "primary_source", "match_status", "is_served",
    "built_at",
)  # fmt: skip
RENTAL_CONTRACT_COLUMNS = (
    "row_key", "run_id", "scraped_at", "contract_id", "contract_number", "line_number",
    "registration_date", "contract_start_date", "contract_end_date", "property_id",
    "land_property_id", "ejari_property_type_id", "ejari_property_sub_type_id",
    "ejari_property_type_en", "ejari_property_type_ar", "ejari_property_sub_type_en",
    "ejari_property_sub_type_ar", "property_usage_id", "property_usage_en", "property_usage_ar",
    "rooms", "rooms_en", "parking", "actual_area", "annual_amount", "contract_amount",
    "no_of_prop", "version_number", "version_en", "version_ar", "parcel_id", "area_id",
    "area_name_en", "area_name_ar", "project_name_en", "project_name_ar", "master_project_en",
    "master_project_ar", "is_free_hold", "is_free_hold_en", "is_free_hold_ar",
    "nearest_metro_en", "nearest_metro_ar", "nearest_mall_en", "nearest_mall_ar",
    "nearest_landmark_en", "nearest_landmark_ar", "project_number", "tenant_type_id",
    "tenant_type_en", "tenant_type_ar", "contract_reg_type_id", "contract_reg_type_en",
    "contract_reg_type_ar", "ejari_bus_property_type_id", "ejari_bus_property_type_en",
    "ejari_bus_property_type_ar", "rent_per_sqm",
)  # fmt: skip
RENTAL_EXTRA_COLUMNS = (
    "unit_number", "bedrooms", "dxbi_contract_key", "dxbi_building_name", "source_system",
    "primary_source", "match_status", "is_served", "built_at",
)  # fmt: skip

SALE_COLUMNS = SALE_CONTRACT_COLUMNS + SALE_EXTRA_COLUMNS
RENTAL_COLUMNS = RENTAL_CONTRACT_COLUMNS + RENTAL_EXTRA_COLUMNS
# Canonical geography on every event row (both tables, after release_id).
GEO_COLUMNS = (
    "district_id", "district_name", "community_key", "community_name", "geo_resolution",
    "geo_method",
)  # fmt: skip


def _geo_key(expr: str) -> str:
    """Case- and spacing-insensitive name key ('DUBAI MARINA' and 'Dubai  Marina' agree)."""
    return f"replaceRegexpAll(lowerUTF8(trimBoth({expr})), '[[:space:]]+', ' ')"


# The crosswalk is evidence from DLD itself (see docs/data-pipelines/GEOGRAPHY_CROSSWALK.md):
# - district:  every area name DLD tags with an area_id (Dubai Pulse history and Ejari) is an
#              official district; the id is the stable identifier.
# - community: every Pulse master project; key = normalised name, display = commonest spelling;
#              its district when >= 80% of its rows sit in one district.
# - project:   a Pulse project name whose rows sit in one (district, master) >= 80% of >= 3
#              times locates any later row naming that project.
# - label:     an open-data area label that is not a district name (e.g. "DUBAI MARINA"):
#              the community of the same name if DLD has one, else the (district, community)
#              its project-located rows carry >= 80% of >= 20 times, else only the district if
#              that alone reaches 80% (JLT: one district, communities split 79/21).
# Nothing else is inferred: a row naming only a wide district keeps that district and no
# community, however many communities the district contains.
_GEO_CROSSWALK_INSERT = """
INSERT INTO `market_geo_crosswalk`
    (kind, source_key, district_id, district_name, community_key, community_name,
     evidence_rows, evidence_share, built_at)"""
_GEO_DISTRICTS = """(SELECT source_key, district_id, district_name FROM `market_geo_crosswalk`
     WHERE kind = 'district')"""
_PULSE_AND_EJARI_AREAS = " UNION ALL ".join(
    f"""SELECT {_geo_key("area_en")} AS k, area_id, area_en AS name, count() AS n
        FROM {table} WHERE area_id != 0 AND area_en != '' GROUP BY k, area_id, name"""
    for table in ("dld_od_transactions_bronze", "dld_od_rents_bronze")
)
_PULSE_MASTERS = " UNION ALL ".join(
    f"""SELECT {_geo_key("master_project_en")} AS ck, master_project_en AS name,
               {_geo_key("area_en")} AS dk, count() AS n
        FROM {table}
        WHERE source_system = 'dubai_pulse' AND master_project_en != '' AND area_id != 0
        GROUP BY ck, name, dk"""
    for table in ("dld_od_transactions_bronze", "dld_od_rents_bronze")
)
_PULSE_PROJECTS = " UNION ALL ".join(
    f"""SELECT {_geo_key("project_en")} AS pk, {_geo_key("area_en")} AS dk,
               {_geo_key("master_project_en")} AS ck, count() AS n
        FROM {table}
        WHERE source_system = 'dubai_pulse' AND project_en != '' AND master_project_en != ''
          AND area_id != 0
        GROUP BY pk, dk, ck"""
    for table in ("dld_od_transactions_bronze", "dld_od_rents_bronze")
)
_OPEN_DATA_LABELS = f"""(
    SELECT {_geo_key("area_en")} AS lk, {_geo_key("project_en")} AS pk, count() AS n
    FROM dld_od_transactions_bronze
    WHERE source_system = 'dld_open_data' AND area_en != ''
    GROUP BY lk, pk
)"""
_GEO_CROSSWALK_STATEMENTS = (
    f"""{_GEO_CROSSWALK_INSERT}
SELECT 'district', k, argMax(area_id, n), argMax(name, n), '', '', sum(n), max(n) / sum(n),
       now64(3)
FROM ({_PULSE_AND_EJARI_AREAS})
GROUP BY k""",
    f"""{_GEO_CROSSWALK_INSERT}
SELECT 'community', c.ck, if(c.share >= 0.8, d.district_id, 0),
       if(c.share >= 0.8, d.district_name, ''), c.ck, names.name, c.total, c.share, now64(3)
FROM (
    SELECT ck, argMax(dk, n) AS top_dk, sum(n) AS total, max(n) / sum(n) AS share
    FROM (SELECT ck, dk, sum(n) AS n FROM ({_PULSE_MASTERS}) GROUP BY ck, dk)
    GROUP BY ck
) AS c
INNER JOIN (
    SELECT ck, argMax(name, n) AS name
    FROM (SELECT ck, name, sum(n) AS n FROM ({_PULSE_MASTERS}) GROUP BY ck, name)
    GROUP BY ck
) AS names ON names.ck = c.ck
LEFT JOIN {_GEO_DISTRICTS} AS d ON d.source_key = c.top_dk""",
    f"""{_GEO_CROSSWALK_INSERT}
SELECT 'project', e.pk, d.district_id, d.district_name, c.source_key, c.community_name, e.total,
       e.share, now64(3)
FROM (
    SELECT pk, argMax((dk, ck), n) AS top, sum(n) AS total, max(n) / sum(n) AS share
    FROM (SELECT pk, dk, ck, sum(n) AS n FROM ({_PULSE_PROJECTS}) GROUP BY pk, dk, ck)
    GROUP BY pk
    HAVING total >= 3 AND share >= 0.8
) AS e
INNER JOIN {_GEO_DISTRICTS} AS d ON d.source_key = tupleElement(e.top, 1)
INNER JOIN (SELECT source_key, community_name FROM `market_geo_crosswalk`
            WHERE kind = 'community') AS c ON c.source_key = tupleElement(e.top, 2)""",
    # Labels that name a DLD community exactly.
    f"""{_GEO_CROSSWALK_INSERT}
SELECT 'label', o.lk, c.district_id, c.district_name, c.source_key, c.community_name,
       sum(o.n), 1, now64(3)
FROM {_OPEN_DATA_LABELS} AS o
INNER JOIN (SELECT source_key, district_id, district_name, community_name
            FROM `market_geo_crosswalk` WHERE kind = 'community') AS c ON c.source_key = o.lk
WHERE o.lk NOT IN (SELECT source_key FROM {_GEO_DISTRICTS})
GROUP BY o.lk, c.district_id, c.district_name, c.source_key, c.community_name""",
    # Other labels, from the rows whose project already locates them.
    f"""{_GEO_CROSSWALK_INSERT}
SELECT 'label', lk,
       tupleElement(district, 1), tupleElement(district, 2),
       if(pair_share >= 0.8, tupleElement(pair, 3), ''),
       if(pair_share >= 0.8, tupleElement(pair, 4), ''),
       total, if(pair_share >= 0.8, pair_share, district_share), now64(3)
FROM (
    SELECT lk,
           argMax(target, n) AS pair, max(n) / sum(n) AS pair_share,
           sum(n) AS total
    FROM (
        SELECT o.lk AS lk,
               (p.district_id, p.district_name, p.community_key, p.community_name) AS target,
               sum(o.n) AS n
        FROM {_OPEN_DATA_LABELS} AS o
        INNER JOIN (SELECT source_key, district_id, district_name, community_key, community_name
                    FROM `market_geo_crosswalk` WHERE kind = 'project') AS p
            ON p.source_key = o.pk
        WHERE o.lk NOT IN (SELECT source_key FROM `market_geo_crosswalk`
                           WHERE kind IN ('district', 'label'))
        GROUP BY lk, target
    )
    GROUP BY lk
    HAVING total >= 20
) AS pairs
INNER JOIN (
    SELECT lk, argMax(district, n) AS district, max(n) / sum(n) AS district_share
    FROM (
        SELECT o.lk AS lk, (p.district_id, p.district_name) AS district, sum(o.n) AS n
        FROM {_OPEN_DATA_LABELS} AS o
        INNER JOIN (SELECT source_key, district_id, district_name
                    FROM `market_geo_crosswalk` WHERE kind = 'project') AS p
            ON p.source_key = o.pk
        GROUP BY lk, district
    )
    GROUP BY lk
) AS districts USING lk
WHERE pair_share >= 0.8 OR district_share >= 0.8""",
)


def _geo_joins(*, area: str, project: str, master: str | None = None) -> str:
    """Crosswalk joins for one event insert; DXBI rows have no master project (master=None)."""
    entries = "source_key, district_id, district_name, community_key, community_name"
    communities = (
        ""
        if master is None
        else f"""
LEFT JOIN (SELECT source_key, community_name FROM market_geo_crosswalk
           WHERE kind = 'community') AS gc ON gc.source_key = {_geo_key(master)}"""
    )
    return f"""LEFT JOIN (SELECT source_key, district_id, district_name FROM market_geo_crosswalk
           WHERE kind = 'district') AS gd ON gd.source_key = {_geo_key(area)}
LEFT JOIN (SELECT {entries} FROM market_geo_crosswalk WHERE kind = 'project') AS gp
    ON gp.source_key = {_geo_key(project)}
LEFT JOIN (SELECT {entries} FROM market_geo_crosswalk WHERE kind = 'label') AS gl
    ON gl.source_key = {_geo_key(area)}{communities}"""


def _geo_values(*, source: str = "", master: str | None = None) -> str:
    """GEO_COLUMNS values, first evidence that applies: a Pulse row's own master project,
    then project evidence, then the row's own district name, then label evidence."""
    pulse_master = f"({source} = 'dubai_pulse' AND {master} != '')"
    order = (
        *(((pulse_master, "gd", "'pulse_master'"),) if master is not None else ()),
        ("gp.source_key != ''", "gp", "'project_evidence'"),
        ("gd.source_key != ''", "gd", "'district_name'"),
        ("gl.source_key != ''", "gl", "'label_evidence'"),
    )

    def pick(column: str, default: str, *, community: bool = False) -> str:
        parts = []
        for condition, alias, _ in order:
            if not community:
                value = f"{alias}.{column}"
            elif condition == pulse_master and master is not None:
                value = (
                    _geo_key(master)
                    if column == "community_key"
                    else f"if(gc.community_name != '', gc.community_name, {master})"
                )
            elif alias == "gd":
                value = "''"  # a district name alone never implies a community
            else:
                value = f"{alias}.{column}"
            parts.append(f"{condition}, {value}")
        return f"multiIf({', '.join(parts)}, {default})"

    method = f"multiIf({', '.join(f'{c}, {m}' for c, _, m in order)}, 'unresolved')"
    return f"""{pick("district_id", "toUInt32(0)")} AS _geo_district_id,
    {pick("district_name", "''")} AS _geo_district,
    {pick("community_key", "''", community=True)} AS _geo_community,
    {pick("community_name", "''", community=True)} AS _geo_community_name,
    multiIf(_geo_community != '', 'community', _geo_district != '', 'district', 'unresolved'),
    {method}"""


# DLD sales after cut-over and correction handling. A transaction number is not a line
# identity: open data registers several lines under one number (473 Sale + Mortgage pairs from
# "Lease to Own Registration", and multi-unit deals; 722 numbers / 3,614 lines measured on
# 2026-10-03). Every distinct line of a number's latest extract is kept; an older extract of
# the number is a superseded version; identical lines in one extract are one line.
# ponytail: two genuinely identical units in one deal collapse to one line (DLD open data has
# no unit identifier to tell them apart from a repeated page row).
_DLD_SALE_SCOPE = """(source_system = 'dld_open_data'
       OR toDate(instance_date) < ifNull(
            (SELECT minOrNull(toDate(instance_date)) FROM dld_od_transactions_bronze
             WHERE source_system = 'dld_open_data'),
            toDate('2149-01-01')))"""
_DLD_SALE_NUMBER = "if(transaction_number != '', transaction_number, row_key)"
_DLD_SALE_LINE = """cityHash64(group_en, procedure_id, procedure_en, trans_value, procedure_area,
        actual_area, property_id, prop_type_en, prop_sb_type_en, usage_en, rooms_en, area_en,
        project_en, master_project_en, building_name_en, instance_date, is_offplan_en,
        reg_type_en)"""
DLD_SALES_BASE = f"""
    SELECT *,
           if(count() OVER (PARTITION BY txn_key) > 1,
              concat(txn_key, '#', lower(hex(line_key))), txn_key) AS line_id
    FROM (
        SELECT *, {_DLD_SALE_NUMBER} AS txn_key, {_DLD_SALE_LINE} AS line_key
        FROM dld_od_transactions_bronze
        WHERE {_DLD_SALE_SCOPE}
          AND ({_DLD_SALE_NUMBER}, scraped_at) IN (
              SELECT {_DLD_SALE_NUMBER}, max(scraped_at) FROM dld_od_transactions_bronze
              WHERE {_DLD_SALE_SCOPE} GROUP BY 1)
        ORDER BY txn_key, line_key
        LIMIT 1 BY txn_key, line_key
    )
"""

# DLD rents after cut-over (by registration date, as the silver layer) and exact-duplicate
# collapse. Open-data rows carry no contract_number, so identity is the business content.
_DLD_RENT_KEY = """cityHash64(
        contract_number, line_number, registration_date, start_date, end_date, property_id,
        land_property_id, prop_type_en, prop_sub_type_en, usage_en, rooms, parking, actual_area,
        annual_amount, contract_amount, total_properties, version_number, version_en, parcel_id,
        area_id, project_en, master_project_en, tenant_type_en, contract_reg_type_en,
        ejari_bus_property_type_en, is_free_hold)"""
_DLD_RENTS_BASE_TEMPLATE = f"""
    SELECT *, {_DLD_RENT_KEY} AS dld_rent_key
    FROM dld_od_rents_bronze
    WHERE (source_system = 'dld_open_data'
       OR toDate(registration_date) < ifNull(
            (SELECT minOrNull(toDate(registration_date)) FROM dld_od_rents_bronze
             WHERE source_system = 'dld_open_data'),
            toDate('2149-01-01')))
       {{extra}}
    LIMIT 1 BY dld_rent_key
"""
DLD_RENTS_BASE = _DLD_RENTS_BASE_TEMPLATE.format(extra="")
# Matching only needs the DXBI period; the key is a per-row hash, so filtering first yields the
# same keys as the full base at a fraction of the memory.
_DLD_RENTS_MATCH_BASE = _DLD_RENTS_BASE_TEMPLATE.format(
    extra="AND start_date >= toDateTime('2024-12-01 00:00:00')"
)

# DXBI sales, one row per sale_key. The source table's FINAL collapses on its whole sort key
# (building/size keys included), so a re-normalised address leaves two versions of a sale;
# keep the latest load.
DXBI_SALES_BASE = """
    SELECT * FROM dxbi_sales_unit_events FINAL
    ORDER BY sale_key, loaded_at DESC
    LIMIT 1 BY sale_key
"""

_SALE_MATCHES_SQL = f"""
INSERT INTO `market_sale_matches`
    (dxbi_sale_key, dld_transaction_number, day_gap, size_gap, has_dld_candidate,
     loose_dld_key, built_at)
WITH
    dld_sales AS (
        SELECT line_id AS dld_key, toDate(instance_date) AS d,
               toUInt64(trans_value) AS price, procedure_area * {SQFT_PER_SQM} AS sqft
        FROM ({DLD_SALES_BASE})
        WHERE group_en = 'Sales' AND trans_value > 0
    ),
    dx AS (
        SELECT sale_key, transaction_date, sale_amount_aed, size_sqft
        FROM ({DXBI_SALES_BASE})
    ),
    -- Every DLD sale at the same price within 30 days: any of these makes a DXBI record a
    -- probable duplicate if it is not paired, so it is never served on top of DLD.
    same_price AS (
        SELECT x.sale_key AS sale_key, s.dld_key AS dld_key, toInt32(abs(x.offset_days)) AS day_gap,
               abs(x.size_sqft - s.sqft) / greatest(s.sqft, 1.0) AS size_gap
        FROM (
            SELECT sale_key, transaction_date + toIntervalDay(offset_days) AS probe_date,
                   offset_days, sale_amount_aed, size_sqft
            FROM dx
            ARRAY JOIN arrayMap(i -> toInt32(i) - 30, range(61)) AS offset_days
        ) AS x
        INNER JOIN dld_sales AS s ON s.d = x.probe_date AND s.price = x.sale_amount_aed
    ),
    -- Pairing needs more: within 3 days, or within 30 days with the same size (±1%).
    candidates AS (SELECT * FROM same_price WHERE day_gap <= 3 OR size_gap <= 0.01),
    best_per_dxbi AS (
        SELECT sale_key, dld_key, day_gap, size_gap FROM candidates
        ORDER BY sale_key, day_gap, size_gap, dld_key
        LIMIT 1 BY sale_key
    ),
    pairs AS (
        SELECT sale_key, dld_key, day_gap, size_gap FROM best_per_dxbi
        ORDER BY dld_key, day_gap, size_gap, sale_key
        LIMIT 1 BY dld_key
    ),
    with_candidate AS (SELECT DISTINCT sale_key FROM same_price),
    -- DXBI period only: a DXBI sale left unpaired that shares its price (within 30 days) with
    -- a DLD sale also left unpaired is very probably the same registration. It is paired
    -- loosely, one-to-one, only so the DLD row is not served beside it: values are never
    -- copied across a loose pair (the units may differ).
    loose_candidates AS (
        SELECT c.sale_key AS sale_key, c.dld_key AS dld_key, c.day_gap AS day_gap,
               c.size_gap AS size_gap
        FROM same_price AS c
        INNER JOIN dx AS lx ON lx.sale_key = c.sale_key
        WHERE lx.transaction_date >= toDate('{SALES_DXBI_FROM}')
          AND c.sale_key NOT IN (SELECT sale_key FROM pairs)
          AND c.dld_key NOT IN (SELECT dld_key FROM pairs)
    ),
    loose_pairs AS (
        SELECT sale_key, dld_key FROM (
            SELECT * FROM loose_candidates
            ORDER BY sale_key, day_gap, size_gap, dld_key
            LIMIT 1 BY sale_key
        )
        ORDER BY dld_key, day_gap, size_gap, sale_key
        LIMIT 1 BY dld_key
    )
SELECT
    dx.sale_key,
    p.dld_key,
    toInt16(if(p.dld_key != '', p.day_gap, -1)),
    if(p.dld_key != '', p.size_gap, -1.0),
    toUInt8(dx.sale_key IN (SELECT sale_key FROM with_candidate)),
    lp.dld_key,
    now64(3)
FROM dx
LEFT JOIN pairs AS p ON p.sale_key = dx.sale_key
LEFT JOIN loose_pairs AS lp ON lp.sale_key = dx.sale_key
"""

_ROOMS_FROM_BEDROOMS = """multiIf(
    {beds} = 'Studio', 'Studio',
    match({beds}, '^[0-9]+ Beds?$'), concat(extract({beds}, '^([0-9]+)'), ' B/R'),
    '')"""

_OFFPLAN_EN = """multiIf(
    d.is_offplan_en != '', d.is_offplan_en,
    d.reg_type_en = 'Off-Plan Properties', 'Off-Plan',
    d.reg_type_en = 'Existing Properties', 'Ready',
    '')"""
_REG_TYPE_EN = """multiIf(
    d.reg_type_en != '', d.reg_type_en,
    d.is_offplan_en = 'Off-Plan', 'Off-Plan Properties',
    d.is_offplan_en = 'Ready', 'Existing Properties',
    '')"""


# Uniform bedroom label ('Studio', 'N B/R', '' unknown). DLD's rooms column also holds
# 'Office', 'Shop', 'NA', 'PENTHOUSE': those are not bedroom counts and become unknown.
def _clean_rooms(expr: str) -> str:
    return f"if(match({expr}, '^([0-9]+ B/R|Studio)$'), {expr}, '')"


_DXBI_SALE_ROOMS = _ROOMS_FROM_BEDROOMS.format(beds="x.bedrooms")
_DLD_SALE_ROOMS = _clean_rooms("d.rooms_en")
_DXBI_SALE_SQM = f"if(x.size_sqft > 0, x.size_sqft / {SQFT_PER_SQM}, 0)"
_DXBI_OFFPLAN_EN = (
    "multiIf(x.market_status = 'Offplan', 'Off-Plan', x.market_status = 'Ready', 'Ready', '')"
)
_DXBI_SALE_TYPE = _uniform_type("x.property_type")


def _location_map(
    *, dld_base: str, matches_join: str, dxbi_base: str, dxbi_key: str, match_key: str
) -> str:
    """CTE: a DLD (area, master project) -> the DXBI community name its matched rows carry.

    DXBI's community names are the display vocabulary (DLD spells them several ways:
    'DownTown Dubai', 'TOWN SQUARE'); a DLD-only row takes the name DXBI uses for rows from
    the same DLD place when >= 80% of >= 10 matched rows agree.
    """
    return f"""dxbi_location_map AS (
        SELECT lk, mk, argMax(name, n) AS name
        FROM (
            SELECT lowerUTF8(d.area_en) AS lk, lowerUTF8(d.master_project_en) AS mk,
                   x.area_name AS name, count() AS n
            FROM ({dld_base}) AS d
            {matches_join}
            INNER JOIN ({dxbi_base}) AS x ON x.{dxbi_key} = m.{match_key}
            WHERE x.area_name != ''
            GROUP BY lk, mk, name
        )
        GROUP BY lk, mk
        HAVING sum(n) >= 10 AND max(n) / sum(n) >= 0.8
    )"""


def _name_key(expr: str) -> str:
    """Letters and digits only: 'DAMAC HILLS 2', 'Damac Hills 2' and 'Al Yufrah1' / 'Al Yufrah 1'
    share a key."""
    return f"replaceRegexpAll(lowerUTF8({expr}), '[^a-z0-9]', '')"


# WITH entry: DXBI's commonest spelling of each location key (sales and rentals together).
_LOCATION_SPELLING = f"""(
        SELECT (groupArray(k), groupArray(name)) FROM (
            SELECT k, argMax(name, n) AS name FROM (
                SELECT {_name_key("area_name")} AS k, area_name AS name, count() AS n
                FROM (SELECT area_name FROM dxbi_sales_unit_events
                      UNION ALL SELECT area_name FROM dxbi_rental_events)
                WHERE area_name != ''
                GROUP BY k, name
            )
            GROUP BY k
        )
    ) AS location_spelling"""


def _spelled(expr: str) -> str:
    """One spelling per location: DXBI's commonest form of the same letters and digits.

    A name DXBI never uses keeps its source spelling, except that DLD's all-capitals labels
    ('AL KHAWANEEJ DISTRICT') are title-cased so a filter list reads as one vocabulary.
    Single words stay as written (acronyms such as 'DIFC').
    """
    fallback = (
        f"if({expr} = upperUTF8({expr}) AND position({expr}, ' ') > 0, initcapUTF8({expr}), {expr})"
    )
    return f"transform({_name_key(expr)}, location_spelling.1, location_spelling.2, {fallback})"


_LOCATION_MAP_JOIN = """LEFT JOIN dxbi_location_map AS lm
    ON lm.lk = lowerUTF8(d.area_en) AND lm.mk = lowerUTF8(d.master_project_en)"""
# DXBI's community where DXBI places the row, else the learned DXBI name for the DLD place,
# else the canonical community, else the official district.
_DLD_ROW_LOCATION = _spelled(
    "multiIf(x.area_name != '', x.area_name, lm.name != '', lm.name, "
    "_geo_community_name != '', _geo_community_name, _geo_district)"
)
_DXBI_ROW_LOCATION = _spelled(
    "multiIf(x.area_name != '', x.area_name, _geo_community_name != '', _geo_community_name,"
    " _geo_district)"
)

_SALE_EVENTS_DLD_SQL = f"""
INSERT INTO `market_sale_events` ({
    ", ".join(SALE_COLUMNS + GEO_COLUMNS + UNIFORM_COLUMNS + SALE_AUDIT_COLUMNS)
})
WITH {
    _location_map(
        dld_base=DLD_SALES_BASE,
        matches_join="INNER JOIN market_sale_matches AS m ON m.dld_transaction_number = d.line_id",
        dxbi_base=DXBI_SALES_BASE,
        dxbi_key="sale_key",
        match_key="dxbi_sale_key",
    )
},
    {_LOCATION_SPELLING}
SELECT
    d.row_key, d.run_id, d.scraped_at,
    d.line_id,
    d.transaction_number,
    -- DXBI wins on a pair inside the DXBI period (by DXBI's date); DLD elsewhere.
    if((m.dxbi_sale_key != '' AND x.transaction_date >= toDate('{SALES_DXBI_FROM}')) AS _dxw,
       toDateTime(x.transaction_date), d.instance_date),
    d.group_id, if(d.group_en = 'Mortgage', 'Mortgages', d.group_en), d.group_ar,
    d.procedure_id, d.procedure_en, d.procedure_ar,
    if(_dxw AND {_DXBI_SALE_SQM} > 0, {_DXBI_SALE_SQM}, d.procedure_area) AS _size_sqm,
    d.actual_area,
    if(_dxw AND x.sale_amount_aed > 0, toInt64(x.sale_amount_aed), d.trans_value) AS _price,
    _price,
    d.total_buyer, d.total_seller, d.property_id, d.property_type_id,
    d.prop_type_en, d.prop_type_ar, d.property_sub_type_id, d.prop_sb_type_en, d.prop_sb_type_ar,
    d.usage_id, d.usage_en, d.usage_ar,
    multiIf(_dxw AND {_DXBI_SALE_ROOMS} != '', {_DXBI_SALE_ROOMS},
            {_DLD_SALE_ROOMS} != '', {_DLD_SALE_ROOMS},
            {_DXBI_SALE_ROOMS}) AS _rooms,
    d.rooms_ar, d.parking, d.parking != '', d.building_age, d.parcel_id, d.area_id,
    d.area_en, d.area_ar, d.project_en, d.project_ar, d.master_project_en, d.master_project_ar,
    d.is_free_hold, d.is_free_hold_en, d.is_free_hold_ar,
    toUInt8(_offplan = 'Off-Plan'),
    multiIf(_dxw AND {_DXBI_OFFPLAN_EN} != '', {_DXBI_OFFPLAN_EN},
            {_OFFPLAN_EN} != '', {_OFFPLAN_EN},
            {_DXBI_OFFPLAN_EN}) AS _offplan,
    d.is_offplan_ar,
    d.nearest_metro_en, d.nearest_metro_ar, d.nearest_mall_en, d.nearest_mall_ar,
    d.nearest_landmark_en, d.nearest_landmark_ar, d.project_number,
    multiIf(_dxw AND x.building_name != '', x.building_name,
            d.building_name_en != '', d.building_name_en, x.building_name) AS _building,
    d.building_name_ar,
    d.reg_type_id,
    multiIf(_offplan = 'Off-Plan', 'Off-Plan Properties', _offplan = 'Ready',
            'Existing Properties', {_REG_TYPE_EN}),
    d.reg_type_ar,
    _price / nullIf(_size_sqm, 0),
    d.rent_value, d.meter_rent_price,
    d.no_of_parties_role_1, d.no_of_parties_role_2, d.no_of_parties_role_3,
    x.unit_number, x.bedrooms, x.market_status, m.dxbi_sale_key, x.building_name,
    if(m.dxbi_sale_key != '', x.transaction_date, NULL),
    d.source_system,
    if(m.dxbi_sale_key != '', 'dxbi', 'dld'),
    multiIf(m.dxbi_sale_key != '', 'matched',
            lp.loose_dld_key != '', 'dld_probable_duplicate',
            'dld_only') AS _status,
    toUInt8(_status != 'dld_probable_duplicate'),
    now64(3),
    {_geo_values(source="d.source_system", master="d.master_project_en")},
    multiIf(_dxw, {_prefer(_DXBI_SALE_TYPE, _DLD_SALE_TYPE)},
            m.dxbi_sale_key != '', {_prefer(_DLD_SALE_TYPE, _DXBI_SALE_TYPE)},
            {_DLD_SALE_TYPE}) AS _type,
    _size_sqm,
    {_DLD_ROW_LOCATION},
    if(_dxw, {
    _audit(
        ("size_sqm", f"{_DXBI_SALE_SQM} = 0 AND d.procedure_area > 0"),
        (
            "property_type",
            f"{_DXBI_SALE_TYPE} IN {_GENERIC_TYPES} AND _type NOT IN {_GENERIC_TYPES}",
        ),
        ("rooms_en", f"{_DXBI_SALE_ROOMS} = '' AND _rooms != ''"),
        ("is_offplan_en", f"{_DXBI_OFFPLAN_EN} = '' AND _offplan != ''"),
        ("building_name_en", "x.building_name = '' AND _building != ''"),
    )
}, []),
    if(_dxw, {
    _audit(
        ("instance_date", "toDate(d.instance_date) != x.transaction_date"),
        ("actual_worth", "x.sale_amount_aed > 0 AND d.trans_value != toInt64(x.sale_amount_aed)"),
        (
            "size_sqm",
            f"{_DXBI_SALE_SQM} > 0 AND d.procedure_area > 0"
            f" AND abs({_DXBI_SALE_SQM} - d.procedure_area) / d.procedure_area > 0.01",
        ),
        (
            "property_type",
            f"{_DXBI_SALE_TYPE} NOT IN {_GENERIC_TYPES}"
            f" AND {_DLD_SALE_TYPE} NOT IN {_GENERIC_TYPES}"
            f" AND {_DXBI_SALE_TYPE} != {_DLD_SALE_TYPE}",
        ),
        (
            "rooms_en",
            f"{_DXBI_SALE_ROOMS} != '' AND {_DLD_SALE_ROOMS} != ''"
            f" AND {_DXBI_SALE_ROOMS} != {_DLD_SALE_ROOMS}",
        ),
        (
            "is_offplan_en",
            f"{_DXBI_OFFPLAN_EN} != '' AND {_OFFPLAN_EN} != ''"
            f" AND {_DXBI_OFFPLAN_EN} != {_OFFPLAN_EN}",
        ),
    )
}, []),
    d.instance_date
FROM ({DLD_SALES_BASE}) AS d
LEFT JOIN (SELECT dxbi_sale_key, dld_transaction_number FROM market_sale_matches
           WHERE dld_transaction_number != '') AS m
    ON m.dld_transaction_number = d.line_id
LEFT JOIN (SELECT loose_dld_key FROM market_sale_matches WHERE loose_dld_key != '') AS lp
    ON lp.loose_dld_key = d.line_id
LEFT JOIN ({DXBI_SALES_BASE}) AS x ON x.sale_key = m.dxbi_sale_key
{_geo_joins(area="d.area_en", master="d.master_project_en", project="d.project_en")}
{_LOCATION_MAP_JOIN}
"""


def _learned_name_maps(*, staging: str, dxbi_source: str, dxbi_key: str, staging_key: str) -> str:
    """CTEs mapping DXBI area/project names to the DLD vocabulary.

    Learned from the rows matched in this same build: a DXBI name maps to the DLD name its
    matched rows carry at least 80% of the time (e.g. "Dubai Marina" -> "Marsa Dubai"). Without
    it, DXBI-only rows add pseudo-areas to every area list.
    """
    maps = []
    for field, dld_field in (("area_name", "area_name_en"), ("project_name", "project_name_en")):
        maps.append(
            f"""{field}_map AS (
        SELECT dxbi_name, argMax(dld_name, n) AS dld_name
        FROM (
            SELECT src.{field} AS dxbi_name, s.{dld_field} AS dld_name, count() AS n
            FROM `{staging}` AS s
            INNER JOIN ({dxbi_source}) AS src ON src.{dxbi_key} = s.{staging_key}
            WHERE s.match_status = 'matched' AND s.{dld_field} != '' AND src.{field} != ''
            GROUP BY dxbi_name, dld_name
        )
        GROUP BY dxbi_name
        HAVING max(n) / sum(n) >= 0.8
    )"""
        )
    return "WITH\n    " + ",\n    ".join(maps)


_SALE_EVENTS_DXBI_SQL = f"""
INSERT INTO `market_sale_events` ({
    ", ".join(SALE_COLUMNS + GEO_COLUMNS + UNIFORM_COLUMNS + SALE_AUDIT_COLUMNS)
})
{
    _learned_name_maps(
        staging="market_sale_events",
        dxbi_source=DXBI_SALES_BASE,
        dxbi_key="sale_key",
        staging_key="dxbi_sale_key",
    )
},
    {_LOCATION_SPELLING}
SELECT
    concat('dxbi:', x.sale_key), '', '', concat('dxbi:', x.sale_key), '',
    toDateTime(x.transaction_date),
    0, 'Sales', '', 0, '', '',
    x.size_sqft / {SQFT_PER_SQM}, x.size_sqft / {SQFT_PER_SQM},
    toInt64(x.sale_amount_aed), toInt64(x.sale_amount_aed),
    0, 0, 0, 0,
    multiIf(x.property_type IN ('Villa', 'Townhouse'), 'Villa', x.property_type = 'Plot', 'Land',
            x.property_type = 'Building', 'Building', x.property_type = '', '', 'Unit'),
    '', 0,
    multiIf(x.property_type = 'Apartment', 'Flat', x.property_type IN ('Villa', 'Townhouse'), 'Villa',
            x.property_type = 'Plot', '', x.property_type),
    '', 0,
    multiIf(x.property_type IN ('Apartment', 'Villa', 'Townhouse'), 'Residential',
            x.property_type IN ('Office', 'Shop', 'Workshop', 'Warehouse'), 'Commercial', ''),
    '',
    {_ROOMS_FROM_BEDROOMS.format(beds="x.bedrooms")}, '', '', 0, 0, '', 0,
    if(am.dld_name != '', am.dld_name, x.area_name), '',
    if(pm.dld_name != '', pm.dld_name, x.project_name), '', '', '',
    0, '', '',
    toUInt8(x.market_status = 'Offplan'),
    multiIf(x.market_status = 'Offplan', 'Off-Plan', x.market_status = 'Ready', 'Ready', ''), '',
    '', '', '', '', '', '', '',
    x.building_name, '',
    0,
    multiIf(x.market_status = 'Offplan', 'Off-Plan Properties',
            x.market_status = 'Ready', 'Existing Properties', ''),
    '',
    x.sale_amount_aed / nullIf(x.size_sqft / {SQFT_PER_SQM}, 0),
    0, 0, 0, 0, 0,
    x.unit_number, x.bedrooms, x.market_status, x.sale_key, x.building_name, x.transaction_date,
    'dxbi', 'dxbi',
    -- A DXBI sale DLD may also hold (same price within 30 days): inside the DXBI period it
    -- is served when it has its own loose DLD partner, which is hidden instead (one for one).
    -- Without one, every DLD candidate already stands for another DXBI record, so serving
    -- it would count a sale twice (measured: +2-4% above the DLD registry per year).
    multiIf(m.has_dld_candidate = 0, 'dxbi_only',
            m.loose_dld_key != '', 'dxbi_preferred',
            'dxbi_probable_duplicate') AS _status,
    toUInt8(_status != 'dxbi_probable_duplicate'),
    now64(3),
    {_geo_values()},
    {_DXBI_SALE_TYPE},
    {_DXBI_SALE_SQM},
    {_DXBI_ROW_LOCATION},
    [], [],
    NULL
FROM market_sale_matches AS m
INNER JOIN ({DXBI_SALES_BASE}) AS x ON x.sale_key = m.dxbi_sale_key
LEFT JOIN area_name_map AS am ON am.dxbi_name = x.area_name
LEFT JOIN project_name_map AS pm ON pm.dxbi_name = x.project_name
{
    _geo_joins(
        area="if(am.dld_name != '', am.dld_name, x.area_name)",
        project="if(pm.dld_name != '', pm.dld_name, x.project_name)",
    )
}
WHERE m.dld_transaction_number = ''
"""


# Rent amounts and dates are not location-specific (thousands of leases are "AED 50,000 from
# the 1st"), so pairing on them alone matched contracts across areas. Both sides are keyed by
# location in the DLD area vocabulary. A DXBI area takes the DLD area its matched *sales* carry
# most often (sales pair on exact price, so that link is reliable; >= 50% of >= 20 sales), else
# a DLD area with the same name (old-Dubai areas with few sales, e.g. "Al Karama"). DXBI rows
# with neither stay unpaired and are only labelled. Requires market_sale_events built first.
def _area_name_key(expr: str) -> str:
    """Lower-case area name with DXBI's trailing numeral spelled as DLD writes it.

    DXBI "Jabal Ali Industrial 1" is DLD "Jabal Ali Industrial First".
    """
    low = f"lowerUTF8(trim({expr}))"
    return (
        f"if(match({low}, ' [1-6]$'), concat(substring({low}, 1, length({low}) - 1), "
        f"transform(right({low}, 1), ['1', '2', '3', '4', '5', '6'], "
        f"['first', 'second', 'third', 'fourth', 'fifth', 'sixth'], '')), {low})"
    )


# DXBI rental area -> DLD area, used both to key pairing and to name DXBI-only rows:
# the DLD area its matched *sales* carry most often (>= 50% of >= 20 sales), else the DLD area
# with the same (ordinal-normalised) name. Joined as `sm` and `n` on a rental row `r`/`x`.
_RENTAL_AREA_CTES = f"""
    sales_area_map AS (
        SELECT dxbi_area, argMax(dld_area, n) AS dld_area
        FROM (
            SELECT x.area_name AS dxbi_area, e.area_name_en AS dld_area, count() AS n
            FROM market_sale_events AS e
            INNER JOIN ({DXBI_SALES_BASE}) AS x ON x.sale_key = e.dxbi_sale_key
            WHERE e.match_status = 'matched' AND e.area_name_en != '' AND x.area_name != ''
            GROUP BY dxbi_area, dld_area
        )
        GROUP BY dxbi_area
        HAVING sum(n) >= 20 AND max(n) / sum(n) >= 0.5
    ),
    dld_area_names AS (
        SELECT lowerUTF8(area_en) AS name_key, any(area_en) AS dld_area
        FROM dld_od_rents_bronze WHERE area_en != '' GROUP BY name_key
    )"""


def _rental_area_joins(alias: str) -> str:
    return (
        f"LEFT JOIN sales_area_map AS sm ON sm.dxbi_area = {alias}.area_name\n"
        f"LEFT JOIN dld_area_names AS n ON n.name_key = {_area_name_key(f'{alias}.area_name')}"
    )


_RENTAL_DLD_AREA = "multiIf(sm.dld_area != '', sm.dld_area, n.dld_area != '', n.dld_area, '')"

_RENTAL_MATCH_INPUTS = f"""{_RENTAL_AREA_CTES},
    dld AS (
        SELECT dld_rent_key, toDate(start_date) AS s, toDate(end_date) AS e,
               toUInt64(greatest(contract_amount, 0)) AS contract_amt,
               toUInt64(greatest(annual_amount, 0)) AS annual_amt,
               actual_area * {SQFT_PER_SQM} AS sqft,
               toInt64(round(actual_area)) AS sqm,
               area_en AS loc
        FROM ({_DLD_RENTS_MATCH_BASE})
    ),
    dx AS (
        SELECT r.contract_key AS contract_key, r.lease_start AS lease_start, r.lease_end AS lease_end,
               r.contract_amount_aed AS contract_amount_aed, r.annual_rent_aed AS annual_rent_aed,
               r.size_sqft AS size_sqft, toInt64(round(r.size_sqft / {SQFT_PER_SQM})) AS sqm,
               {_RENTAL_DLD_AREA} AS loc
        FROM dxbi_rental_events AS r FINAL
        {_rental_area_joins("r")}
    )"""


def _rental_rank_pairing_sql(
    *,
    tier: int,
    dx_keys: tuple[str, ...],
    dld_keys: tuple[str, ...],
    dx_filter: str,
    dld_filter: str,
) -> str:
    """Pair still-unpaired DXBI and DLD rentals one-to-one inside each key group.

    Both sides are numbered by floor area within the group and row n pairs with row n, so a
    group of n DXBI and m DLD rows yields min(n, m) pairs with no fan-out; the rest fall
    through to the next tier. (A pairwise candidate join fanned out to ~198M pairs.)
    """
    dx_cols = ", ".join(f"{key} AS k{i}" for i, key in enumerate(dx_keys))
    dld_cols = ", ".join(f"{key} AS k{i}" for i, key in enumerate(dld_keys))
    partition = ", ".join(f"k{i}" for i in range(len(dx_keys)))
    join_on = " AND ".join(f"a.k{i} = b.k{i}" for i in range(len(dx_keys)))
    return f"""
INSERT INTO `market_rental_matches`
    (dxbi_contract_key, dld_rent_key, match_tier, end_day_gap, size_gap, has_dld_candidate, built_at)
WITH {_RENTAL_MATCH_INPUTS}
SELECT a.contract_key, b.dld_rent_key, {tier}, toInt32(abs(dateDiff('day', b.e, a.lease_end))),
       abs(a.size_sqft - b.sqft) / greatest(b.sqft, 1.0), 1, now64(3)
FROM (
    SELECT contract_key, lease_end, size_sqft, {dx_cols},
           row_number() OVER (PARTITION BY {partition} ORDER BY size_sqft, contract_key) AS rn
    FROM dx
    WHERE {dx_filter}
      AND contract_key NOT IN (SELECT dxbi_contract_key FROM `market_rental_matches`)
) AS a
INNER JOIN (
    SELECT dld_rent_key, e, sqft, {dld_cols},
           row_number() OVER (PARTITION BY {partition} ORDER BY sqft, dld_rent_key) AS rn
    FROM dld
    WHERE {dld_filter}
      AND dld_rent_key NOT IN (SELECT dld_rent_key FROM `market_rental_matches`)
) AS b ON {join_on} AND a.rn = b.rn
{_HEAVY_QUERY_SETTINGS}
"""


_RENTAL_MATCH_STATEMENTS = (
    # Tier 0: same lease period and contract amount (99% of pairs agree on the end date).
    _rental_rank_pairing_sql(
        tier=0,
        dx_keys=("loc", "lease_start", "lease_end", "contract_amount_aed"),
        dld_keys=("loc", "s", "e", "contract_amt"),
        dx_filter="loc != '' AND contract_amount_aed > 0",
        dld_filter="loc != '' AND contract_amt > 0",
    ),
    # Tier 1: same lease period and annual amount (DXBI annualises some contracts).
    _rental_rank_pairing_sql(
        tier=1,
        dx_keys=("loc", "lease_start", "lease_end", "annual_rent_aed"),
        dld_keys=("loc", "s", "e", "annual_amt"),
        dx_filter="loc != '' AND annual_rent_aed > 0",
        dld_filter="loc != '' AND annual_amt > 0",
    ),
    # Tier 2: same lease period and floor area with a different amount — the same Ejari
    # contract reported with a different rent (91% of the first build's DXBI-only rows).
    _rental_rank_pairing_sql(
        tier=2,
        dx_keys=("loc", "lease_start", "lease_end", "sqm"),
        dld_keys=("loc", "s", "e", "sqm"),
        dx_filter="loc != '' AND sqm > 0",
        dld_filter="loc != '' AND sqm > 0",
    ),
    # Tier 3: same start and contract amount where the end dates disagree.
    _rental_rank_pairing_sql(
        tier=3,
        dx_keys=("loc", "lease_start", "contract_amount_aed"),
        dld_keys=("loc", "s", "contract_amt"),
        dx_filter="loc != '' AND contract_amount_aed > 0",
        dld_filter="loc != '' AND contract_amt > 0",
    ),
    # Every DXBI rental left unpaired gets its ledger row. A DLD row with the same start and
    # amount, or the same period and floor area (±1 m²), means DLD already holds the contract.
    # Inside the DXBI period such a row is also paired loosely, one-to-one and regardless of
    # area, with a DLD row left unpaired that has the same start and contract amount, so that
    # DLD row is not served beside the DXBI one. Values are never copied across a loose pair.
    f"""
INSERT INTO `market_rental_matches`
    (dxbi_contract_key, dld_rent_key, match_tier, end_day_gap, size_gap, has_dld_candidate,
     loose_dld_rent_key, built_at)
WITH {_RENTAL_MATCH_INPUTS},
    loose_pairs AS (
        SELECT a.contract_key AS contract_key, b.dld_rent_key AS dld_rent_key
        FROM (
            SELECT contract_key, lease_start AS k0, contract_amount_aed AS k1,
                   row_number() OVER (PARTITION BY k0, k1 ORDER BY size_sqft, contract_key) AS rn
            FROM dx
            WHERE lease_start >= toDate('{RENTALS_DXBI_FROM}') AND contract_amount_aed > 0
              AND contract_key NOT IN (SELECT dxbi_contract_key FROM `market_rental_matches`)
        ) AS a
        INNER JOIN (
            SELECT dld_rent_key, s AS k0, contract_amt AS k1,
                   row_number() OVER (PARTITION BY k0, k1 ORDER BY sqft, dld_rent_key) AS rn
            FROM dld
            WHERE s >= toDate('{RENTALS_DXBI_FROM}') AND contract_amt > 0
              AND dld_rent_key NOT IN (SELECT dld_rent_key FROM `market_rental_matches`)
        ) AS b ON a.k0 = b.k0 AND a.k1 = b.k1 AND a.rn = b.rn
    )
SELECT dx.contract_key, 0, 255, -1, -1.0,
       toUInt8(
           (dx.contract_amount_aed > 0
            AND (dx.lease_start, dx.contract_amount_aed) IN (SELECT s, contract_amt FROM dld))
           OR (dx.annual_rent_aed > 0
               AND (dx.lease_start, dx.annual_rent_aed) IN (SELECT s, annual_amt FROM dld))
           OR (dx.sqm > 0 AND (
               (dx.lease_start, dx.lease_end, dx.sqm - 1) IN (SELECT s, e, sqm FROM dld)
               OR (dx.lease_start, dx.lease_end, dx.sqm) IN (SELECT s, e, sqm FROM dld)
               OR (dx.lease_start, dx.lease_end, dx.sqm + 1) IN (SELECT s, e, sqm FROM dld)))
       ),
       lp.dld_rent_key,
       now64(3)
FROM dx
LEFT JOIN loose_pairs AS lp ON lp.contract_key = dx.contract_key
WHERE dx.contract_key NOT IN (SELECT dxbi_contract_key FROM `market_rental_matches`)
{_HEAVY_QUERY_SETTINGS}
""",
)


# DLD puts one row per unit of a multi-unit contract and repeats the whole contract's rent on
# each (measured: 224,466 of 224,543 Pulse multi-unit contracts have exactly one row per unit;
# those rows held 74-80% of DLD's annual rent value). Each row is served its unit's share, so
# a contract's rows still add up to its rent. Floor area is already per row and is not split.
def _per_unit(amount: str) -> str:
    return f"toInt64(round({amount} / greatest(d.total_properties, 1)))"


_DLD_ANNUAL = _per_unit("d.annual_amount")
_DLD_CONTRACT = _per_unit("d.contract_amount")
_DXBI_RENT_ROOMS = _ROOMS_FROM_BEDROOMS.format(beds="x.bedrooms")
# DLD rent rows carry no bedroom count (its numeric "rooms" is set on 0.3% of rows and its
# meaning is undocumented); only an Ejari "Studio" type says it.
_DLD_RENT_ROOMS = "if(d.prop_type_en = 'Studio' OR d.prop_sub_type_en = 'Studio', 'Studio', '')"
_DXBI_RENT_SQM = f"if(x.size_sqft > 0, x.size_sqft / {SQFT_PER_SQM}, 0)"
_DXBI_RENT_TYPE = _uniform_type("x.property_type")

_RENTAL_EVENTS_DLD_SQL = f"""
INSERT INTO `market_rental_events` ({", ".join(RENTAL_COLUMNS + GEO_COLUMNS + UNIFORM_COLUMNS)})
WITH {
    _location_map(
        dld_base=DLD_RENTS_BASE,
        matches_join="INNER JOIN market_rental_matches AS m ON m.dld_rent_key = d.dld_rent_key"
        " AND m.dld_rent_key != 0",
        dxbi_base="SELECT * FROM dxbi_rental_events FINAL",
        dxbi_key="contract_key",
        match_key="dxbi_contract_key",
    )
},
    {_LOCATION_SPELLING}
SELECT
    d.row_key, d.run_id, d.scraped_at,
    if(d.contract_number != '', d.contract_number, d.row_key), d.contract_number, d.line_number,
    d.registration_date,
    -- DXBI wins on a pair inside the DXBI period (by DXBI's lease start); DLD elsewhere.
    if((m.dxbi_contract_key != '' AND x.lease_start >= toDate('{RENTALS_DXBI_FROM}')) AS _dxw,
       toDateTime(x.lease_start), d.start_date),
    if(_dxw, toDateTime(x.lease_end), d.end_date),
    d.property_id, d.land_property_id,
    d.ejari_property_type_id, d.ejari_property_sub_type_id,
    d.prop_type_en, d.prop_type_ar, d.prop_sub_type_en, d.prop_sub_type_ar,
    d.property_usage_id, d.usage_en, d.usage_ar,
    d.rooms,
    multiIf(_dxw AND {_DXBI_RENT_ROOMS} != '', {_DXBI_RENT_ROOMS},
            {_DLD_RENT_ROOMS} != '', {_DLD_RENT_ROOMS},
            {_DXBI_RENT_ROOMS}) AS _rooms,
    d.parking,
    if(_dxw AND {_DXBI_RENT_SQM} > 0, {_DXBI_RENT_SQM}, d.actual_area) AS _size_sqm,
    if(_dxw AND x.annual_rent_aed > 0, toInt64(x.annual_rent_aed), {_DLD_ANNUAL}) AS _annual,
    if(_dxw AND x.contract_amount_aed > 0, toInt64(x.contract_amount_aed), {_DLD_CONTRACT}),
    d.total_properties, d.version_number,
    multiIf(_dxw AND x.contract_state IN ('New', 'Renewed'), x.contract_state,
            d.version_en = 'Renew', 'Renewed', d.version_en) AS _version,
    d.version_ar, d.parcel_id, d.area_id, d.area_en, d.area_ar, d.project_en, d.project_ar,
    d.master_project_en, d.master_project_ar, d.is_free_hold, d.is_free_hold_en, d.is_free_hold_ar,
    d.nearest_metro_en, d.nearest_metro_ar, d.nearest_mall_en, d.nearest_mall_ar,
    d.nearest_landmark_en, d.nearest_landmark_ar, d.project_number,
    d.tenant_type_id, d.tenant_type_en, d.tenant_type_ar,
    d.contract_reg_type_id, d.contract_reg_type_en, d.contract_reg_type_ar,
    d.ejari_bus_property_type_id, d.ejari_bus_property_type_en, d.ejari_bus_property_type_ar,
    _annual / nullIf(_size_sqm, 0),
    x.unit_number, x.bedrooms, m.dxbi_contract_key, x.building_name,
    d.source_system,
    if(m.dxbi_contract_key != '', 'dxbi', 'dld'),
    multiIf(m.dxbi_contract_key != '', 'matched',
            lp.loose_dld_rent_key != 0, 'dld_probable_duplicate',
            'dld_only') AS _status,
    toUInt8(_status != 'dld_probable_duplicate'),
    now64(3),
    {_geo_values(source="d.source_system", master="d.master_project_en")},
    multiIf(_dxw, {_prefer(_DXBI_RENT_TYPE, _DLD_RENT_TYPE)},
            m.dxbi_contract_key != '', {_prefer(_DLD_RENT_TYPE, _DXBI_RENT_TYPE)},
            {_DLD_RENT_TYPE}) AS _type,
    _size_sqm,
    {_DLD_ROW_LOCATION},
    if(_dxw, {
    _audit(
        ("size_sqm", f"{_DXBI_RENT_SQM} = 0 AND d.actual_area > 0"),
        (
            "property_type",
            f"{_DXBI_RENT_TYPE} IN {_GENERIC_TYPES} AND _type NOT IN {_GENERIC_TYPES}",
        ),
        ("rooms_en", f"{_DXBI_RENT_ROOMS} = '' AND _rooms != ''"),
        ("annual_amount", "x.annual_rent_aed = 0 AND d.annual_amount > 0"),
    )
}, []),
    if(_dxw, {
    _audit(
        ("contract_start_date", "toDate(d.start_date) != x.lease_start"),
        ("contract_end_date", "toDate(d.end_date) != x.lease_end"),
        (
            "annual_amount",
            f"x.annual_rent_aed > 0 AND {_DLD_ANNUAL} != toInt64(x.annual_rent_aed)",
        ),
        (
            "size_sqm",
            f"{_DXBI_RENT_SQM} > 0 AND d.actual_area > 0"
            f" AND abs({_DXBI_RENT_SQM} - d.actual_area) / d.actual_area > 0.05",
        ),
        (
            "property_type",
            f"{_DXBI_RENT_TYPE} NOT IN {_GENERIC_TYPES}"
            f" AND {_DLD_RENT_TYPE} NOT IN {_GENERIC_TYPES}"
            f" AND {_DXBI_RENT_TYPE} != {_DLD_RENT_TYPE}",
        ),
    )
}, [])
FROM ({DLD_RENTS_BASE}) AS d
LEFT JOIN (SELECT dxbi_contract_key, dld_rent_key FROM market_rental_matches
           WHERE dld_rent_key != 0) AS m
    ON m.dld_rent_key = d.dld_rent_key
LEFT JOIN (SELECT loose_dld_rent_key FROM market_rental_matches
           WHERE loose_dld_rent_key != 0) AS lp
    ON lp.loose_dld_rent_key = d.dld_rent_key
LEFT JOIN (SELECT * FROM dxbi_rental_events FINAL) AS x ON x.contract_key = m.dxbi_contract_key
{_geo_joins(area="d.area_en", master="d.master_project_en", project="d.project_en")}
{_LOCATION_MAP_JOIN}
{_HEAVY_QUERY_SETTINGS}
"""

_RENTAL_EVENTS_DXBI_SQL = f"""
INSERT INTO `market_rental_events` ({", ".join(RENTAL_COLUMNS + GEO_COLUMNS + UNIFORM_COLUMNS)})
{
    _learned_name_maps(
        staging="market_rental_events",
        dxbi_source="SELECT * FROM dxbi_rental_events FINAL",
        dxbi_key="contract_key",
        staging_key="dxbi_contract_key",
    )
},{_RENTAL_AREA_CTES},
    {_LOCATION_SPELLING}
SELECT
    concat('dxbi:', x.contract_key), '', '', concat('dxbi:', x.contract_key),
    x.source_contract_id, 0,
    toDateTime(x.lease_start), toDateTime(x.lease_start), toDateTime(x.lease_end),
    0, 0, 0, 0,
    multiIf(x.property_type = 'Apartment', 'Flat', x.property_type IN ('Villa', 'Townhouse'), 'Villa',
            x.property_type),
    '', '', '', 0,
    multiIf(x.property_type IN ('Apartment', 'Villa', 'Townhouse'), 'Residential',
            x.property_type = 'Commercial', 'Commercial', ''),
    -- usage_ar, rooms, rooms_en (DXBI's bedroom count; it was left empty before), parking
    '', '', {_DXBI_RENT_ROOMS}, '',
    x.size_sqft / {SQFT_PER_SQM}, toInt64(x.annual_rent_aed), toInt64(x.contract_amount_aed),
    1, 0, x.contract_state, '', '', 0,
    -- Same area resolution as pairing, then the rental-learned map, else DXBI's own name.
    multiIf({_RENTAL_DLD_AREA} != '', {
    _RENTAL_DLD_AREA
}, am.dld_name != '', am.dld_name, x.area_name),
    '',
    if(pm.dld_name != '', pm.dld_name, x.project_name), '', '', '',
    0, '', '', '', '', '', '', '', '', '',
    0, '', '', 0, '', '', 0, '', '',
    x.annual_rent_aed / nullIf(x.size_sqft / {SQFT_PER_SQM}, 0),
    x.unit_number, x.bedrooms, x.contract_key, x.building_name,
    'dxbi', 'dxbi',
    -- Same rule as sales (loose pairs exist only inside the DXBI period). DXBI lists a
    -- multi-unit contract once per unit; serving unpartnered rows put 2026 rentals 27% above
    -- the whole DLD registry.
    multiIf(m.has_dld_candidate = 0, 'dxbi_only',
            m.loose_dld_rent_key != 0, 'dxbi_preferred',
            'dxbi_probable_duplicate') AS _status,
    toUInt8(_status != 'dxbi_probable_duplicate'),
    now64(3),
    {_geo_values()},
    {_DXBI_RENT_TYPE},
    {_DXBI_RENT_SQM},
    {_DXBI_ROW_LOCATION},
    [], []
FROM market_rental_matches AS m
INNER JOIN (SELECT * FROM dxbi_rental_events FINAL) AS x ON x.contract_key = m.dxbi_contract_key
LEFT JOIN area_name_map AS am ON am.dxbi_name = x.area_name
LEFT JOIN project_name_map AS pm ON pm.dxbi_name = x.project_name
{_rental_area_joins("x")}
{
    _geo_joins(
        area=f"multiIf({_RENTAL_DLD_AREA} != '', {_RENTAL_DLD_AREA}, am.dld_name != '', "
        "am.dld_name, x.area_name)",
        project="if(pm.dld_name != '', pm.dld_name, x.project_name)",
    )
}
WHERE m.dld_rent_key = 0
"""
