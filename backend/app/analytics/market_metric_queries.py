"""Warehouse SQL for database-grounded sales and rental metrics.

Every user value is a bound parameter. Metric, grouping and dataset names reach SQL only
through the fixed fragments below, selected by validated Literal names.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.clickhouse import query
from app.market.reference import SQM_TO_SQFT
from app.market.releases import pinned_table
from app.market.scope import LOCATION_PREDICATE


@dataclass(frozen=True, slots=True)
class Dataset:
    table: str
    release_table: str
    event_date: str
    value: str
    area_sqm: str
    eligible: str
    bedrooms: str
    building: str
    property_types: dict[str, str]


# Capability property types on the uniform property_type column (one list for both datasets
# and both sources; holocron.market.transforms.PROPERTY_TYPES).
UNIFORM_PROPERTY_TYPES = {
    "apartment": "property_type = 'Apartment'",
    "hotel_apartment": "property_type = 'Hotel Apartment'",
    "villa": "property_type = 'Villa'",
    "office": "property_type = 'Office'",
    "shop": "property_type = 'Shop'",
    "land": "property_type = 'Plot'",
}

SALES = Dataset(
    table="ch_transactions",
    release_table="market_sale_events",
    # DLD instance_date is the registration date as recorded (Dubai local time).
    event_date="toDate(instance_date)",
    value="toFloat64(actual_worth)",
    area_sqm="size_sqm",
    # Achieved sale prices only: mortgages, gifts and zero-value records are not sales.
    eligible="trans_group_en = 'Sales' AND actual_worth > 0",
    bedrooms="rooms_en",
    building="if(building_name_en != '', building_name_en, dxbi_building_name)",
    property_types=UNIFORM_PROPERTY_TYPES,
)

RENTALS = Dataset(
    table="ch_rent_contracts",
    release_table="market_rental_events",
    # Rentals are dated by contract start on every page (uniform layer); DXBI has no
    # registration date. Leases starting after the requested period are not in it.
    event_date="toDate(contract_start_date)",
    # DLD annual_amount is the annualised rent; contract_amount is the whole-term value.
    value="toFloat64(annual_amount)",
    area_sqm="size_sqm",
    eligible="annual_amount > 0 AND contract_end_date > contract_start_date",
    # Uniform label from the market layer: DXBI's count where it has one, Ejari "Studio";
    # DLD rentals carry no count, so those rows stay unknown ('') and are reported as coverage.
    bedrooms="rooms_en",
    # DLD rentals name the project (usually the building); DXBI-matched rows name the building.
    building="if(dxbi_building_name != '', dxbi_building_name, project_name_en)",
    property_types=UNIFORM_PROPERTY_TYPES,
)

DATASETS = {"sales": SALES, "rentals": RENTALS}


def _from(dataset: str) -> str:
    """The served rows to read: the pinned immutable release inside a pin, else the view."""
    table = pinned_table(dataset)
    return f"(SELECT * FROM `{table}` WHERE is_served = 1)" if table else DATASETS[dataset].table


def _rate(ds: Dataset) -> str:
    return f"{ds.value} / {ds.area_sqm}"


def metric_expressions(ds: Dataset) -> dict[str, str]:
    rated = f"{ds.area_sqm} > 0"
    expressions = {
        "count": "toFloat64(count())",
        "total_aed": f"sum({ds.value})",
        "mean_aed": f"avg({ds.value})",
        # Linear interpolation between order statistics (PERCENTILE.INC): the median of an
        # even count is the mean of the two middle values.
        "median_aed": f"quantileExactInclusive(0.5)({ds.value})",
        "p25_aed": f"quantileExactInclusive(0.25)({ds.value})",
        "p75_aed": f"quantileExactInclusive(0.75)({ds.value})",
        "rated_count": f"toFloat64(countIf({rated}))",
        "mean_aed_per_sqm": f"avgIf({_rate(ds)}, {rated})",
        "median_aed_per_sqm": f"quantileExactInclusiveIf(0.5)({_rate(ds)}, {rated})",
        "weighted_aed_per_sqm": f"sumIf({ds.value}, {rated}) / sumIf({ds.area_sqm}, {rated})",
    }
    for name in ("mean", "median", "weighted"):
        expressions[f"{name}_aed_per_sqft"] = (
            f"({expressions[f'{name}_aed_per_sqm']}) / {SQM_TO_SQFT}"
        )
    if ds is RENTALS:
        expressions["total_contract_aed"] = "sum(toFloat64(contract_amount))"
        expressions["median_contract_aed"] = (
            "quantileExactInclusive(0.5)(toFloat64(contract_amount))"
        )
    return expressions


def group_expressions(ds: Dataset) -> dict[str, tuple[str, str]]:
    """Group name -> (key expression, display label expression)."""
    return {
        # Official DLD district and evidence-placed community (canonical geography).
        "area": (
            "lowerUTF8(district_name)",
            "if(any(district_name) = '', '(no district)', any(district_name))",
        ),
        "community": (
            "community_key",
            "if(any(community_name) = '', '(no community)', any(community_name))",
        ),
        "master_project": ("lowerUTF8(master_project_en)", "any(master_project_en)"),
        "project": ("lowerUTF8(project_name_en)", "any(project_name_en)"),
        "building": (f"lowerUTF8({ds.building})", f"any({ds.building})"),
        "month": (f"toStartOfMonth({ds.event_date})", f"toString(toStartOfMonth({ds.event_date}))"),
        "bedrooms": (ds.bedrooms, f"any({ds.bedrooms})"),
        "property_type": (
            "property_sub_type_en" if ds is SALES else "ejari_property_sub_type_en",
            "any(property_sub_type_en)" if ds is SALES else "any(ejari_property_sub_type_en)",
        ),
    } | (
        {"registration": ("reg_type_en", "any(reg_type_en)")}
        if ds is SALES
        else {"contract_version": ("version_en", "any(version_en)")}
    )


@dataclass(frozen=True, slots=True)
class MetricFilters:
    start: date
    end: date
    location_communities: tuple[str, ...] = ()
    location_districts: tuple[str, ...] = ()
    building: str | None = None
    project: str | None = None
    property_type: str | None = None
    usage: str | None = None
    bedrooms: tuple[str, ...] = ()
    registration: str | None = None
    contract_version: str | None = None
    min_term_days: int | None = None
    max_term_days: int | None = None
    value_min: float | None = None
    value_max: float | None = None


def where_clause(
    ds: Dataset, filters: MetricFilters, *, include_bedrooms: bool = True
) -> tuple[str, dict[str, Any]]:
    clauses = [ds.eligible, f"{ds.event_date} BETWEEN {{start:Date}} AND {{end:Date}}"]
    params: dict[str, Any] = {"start": filters.start, "end": filters.end}
    if filters.location_communities or filters.location_districts:
        clauses.append(LOCATION_PREDICATE)
        params |= {
            "loc_communities": list(filters.location_communities),
            "loc_districts": list(filters.location_districts),
        }
    if filters.building:
        clauses.append(f"lowerUTF8({ds.building}) = lowerUTF8({{building:String}})")
        params["building"] = filters.building.strip()
    if filters.project:
        clauses.append("lowerUTF8(project_name_en) = lowerUTF8({project:String})")
        params["project"] = filters.project.strip()
    if filters.property_type:
        clauses.append(ds.property_types[filters.property_type])
    if filters.usage:
        clauses.append("lowerUTF8(property_usage_en) = {usage:String}")
        params["usage"] = filters.usage
    if include_bedrooms and filters.bedrooms:
        clauses.append(f"{ds.bedrooms} IN {{bedrooms:Array(String)}}")
        params["bedrooms"] = list(filters.bedrooms)
    if filters.registration:
        clauses.append("reg_type_en = {registration:String}")
        params["registration"] = filters.registration
    if filters.contract_version:
        clauses.append("version_en = {contract_version:String}")
        params["contract_version"] = filters.contract_version
    term = "dateDiff('day', toDate(contract_start_date), toDate(contract_end_date)) + 1"
    if filters.min_term_days is not None:
        clauses.append(f"{term} >= {{min_term:Int32}}")
        params["min_term"] = filters.min_term_days
    if filters.max_term_days is not None:
        clauses.append(f"{term} <= {{max_term:Int32}}")
        params["max_term"] = filters.max_term_days
    if filters.value_min is not None:
        clauses.append(f"{ds.value} >= {{value_min:Float64}}")
        params["value_min"] = filters.value_min
    if filters.value_max is not None:
        clauses.append(f"{ds.value} < {{value_max:Float64}}")
        params["value_max"] = filters.value_max
    return " AND ".join(clauses), params


def _band_expression(ds: Dataset, bands: tuple[float, ...]) -> tuple[str, str]:
    # Bands are [b0, b1), [b1, b2) ... ; boundaries are bound as parameters.
    conditions = ", ".join(
        f"{ds.value} < {{band_{index}:Float64}}, {index}" for index in range(len(bands))
    )
    key = f"multiIf({conditions}, {len(bands)})"
    return key, key


async def fetch_metric_rows(
    dataset: str,
    filters: MetricFilters,
    metrics: tuple[str, ...],
    *,
    group_by: str | None = None,
    price_bands: tuple[float, ...] = (),
    min_group_count: int = 1,
    order_by: str = "count",
    limit: int = 50,
) -> list[dict[str, Any]]:
    ds = DATASETS[dataset]
    expressions = metric_expressions(ds)
    where, params = where_clause(ds, filters)
    names = dict.fromkeys(("count", *metrics))
    selected = ", ".join(f"{expressions[name]} AS {name}" for name in names)
    if group_by is None:
        return await query(f"SELECT {selected} FROM {_from(dataset)} WHERE {where}", params)
    if group_by == "price_band":
        key, label = _band_expression(ds, price_bands)
        params |= {f"band_{index}": value for index, value in enumerate(price_bands)}
    else:
        key, label = group_expressions(ds)[group_by]
    params |= {"min_group_count": min_group_count, "limit": limit}
    return await query(
        f"""
        SELECT {key} AS group_key, {label} AS group_label, {selected}
        FROM {_from(dataset)}
        WHERE {where}
        GROUP BY group_key
        HAVING count() >= {{min_group_count:UInt32}}
        ORDER BY {order_by} DESC, group_key
        LIMIT {{limit:UInt32}}
        """,
        params,
    )


async def fetch_bedroom_coverage(dataset: str, filters: MetricFilters) -> dict[str, Any]:
    """Rows matching every filter except bedrooms, and how many have no bedroom count."""
    ds = DATASETS[dataset]
    where, params = where_clause(ds, filters, include_bedrooms=False)
    rows = await query(
        f"SELECT count() AS rows, countIf({ds.bedrooms} = '') AS unknown_bedrooms "
        f"FROM {_from(dataset)} WHERE {where}",
        params,
    )
    return rows[0] if rows else {"rows": 0, "unknown_bedrooms": 0}


async def fetch_matched_locations(dataset: str, filters: MetricFilters) -> list[dict[str, Any]]:
    """Raw source names behind the matched rows, with where the crosswalk placed them."""
    ds = DATASETS[dataset]
    where, params = where_clause(ds, filters)
    return await query(
        f"""
        SELECT area_name_en AS area, master_project_en AS master_project,
               district_name AS district, community_name AS community, geo_method AS method,
               count() AS records
        FROM {_from(dataset)}
        WHERE {where}
        GROUP BY area, master_project, district, community, method
        ORDER BY records DESC
        LIMIT 10
        """,
        params,
    )


async def fetch_district_only_rows(
    dataset: str, filters: MetricFilters, districts: tuple[str, ...]
) -> int:
    """Rows matching every other filter that DLD locates only by one of ``districts``.

    They may belong to the requested community, but no source evidence says so, so they are
    excluded from a community answer and disclosed instead.
    """
    ds = DATASETS[dataset]
    unlocated = MetricFilters(
        **{
            **{f: getattr(filters, f) for f in filters.__dataclass_fields__},
            "location_communities": (),
            "location_districts": (),
        }
    )
    where, params = where_clause(ds, unlocated)
    rows = await query(
        f"SELECT count() AS records FROM {_from(dataset)} WHERE {where} "
        "AND community_key = '' AND lowerUTF8(district_name) IN {coarse:Array(String)}",
        params | {"coarse": [district.lower() for district in districts]},
    )
    return int(rows[0]["records"]) if rows else 0


async def fetch_source_breakdown(dataset: str, filters: MetricFilters) -> list[dict[str, Any]]:
    ds = DATASETS[dataset]
    where, params = where_clause(ds, filters)
    return await query(
        f"""
        SELECT source_system, match_status, count() AS records
        FROM {_from(dataset)}
        WHERE {where}
        GROUP BY source_system, match_status
        ORDER BY records DESC
        """,
        params,
    )


async def fetch_release_id(dataset: str) -> str:
    """The release the served table holds right now ('' before releases had ids)."""
    rows = await query(f"SELECT any(release_id) AS release_id FROM {_from(dataset)}", {})
    return str(rows[0]["release_id"]) if rows else ""


async def fetch_release(dataset: str) -> dict[str, Any]:
    """Identity, record dates and load-evidence coverage of the served release."""
    ds = DATASETS[dataset]
    rows = await query(
        f"""
        SELECT any(release_id) AS release_id,
               min({ds.event_date}) AS data_from,
               max({ds.event_date}) AS data_to,
               max(built_at) AS built_at
        FROM {_from(dataset)}
        WHERE {ds.eligible} AND {ds.event_date} <= today()
        """,
        {},
    )
    release = dict(rows[0]) if rows else {}
    release["coverage"] = {}
    if release.get("release_id"):
        ledger = await query(
            "SELECT coverage_json FROM market_release_ledger FINAL "
            "WHERE dataset = {dataset:String} AND release_id = {release_id:String}",
            {"dataset": ds.release_table, "release_id": release["release_id"]},
        )
        if ledger:
            release["coverage"] = json.loads(ledger[0]["coverage_json"] or "{}")
    return release
