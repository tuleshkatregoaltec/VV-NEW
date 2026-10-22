from __future__ import annotations

import logging
from typing import Any, Iterable

from app.crm import owner_queries
from app.crm.identity import (
    ParsedContact,
    _location_key,
    _location_tokens,
    _normalize_unit,
    _transaction_location_score,
    _usable_project_name,
)

logger = logging.getLogger(__name__)

_QUERY_CHUNK = 4_000

PropertyIdentity = tuple[str, str, str]


def _chunks(values: list[str], size: int = _QUERY_CHUNK) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _registry_identity(row: dict[str, Any]) -> PropertyIdentity:
    building_key = str(row.get("building_key") or "") or _location_key(
        str(row.get("building_name") or row.get("project_name") or "")
    )
    return (str(row.get("area_key") or ""), building_key, str(row.get("unit_key") or ""))


def _resolved_property_key(row: dict[str, Any]) -> str:
    """The property_key a candidate gets once it takes this registry row's identity."""
    location = str(row.get("building_name") or row.get("project_name") or "")
    unit = _normalize_unit(row.get("unit_number"))
    if not location or not unit:
        return ""
    return f"{_location_key(location)}|{unit.lower()}"


def _identified_sale_key(candidate: ParsedContact) -> str:
    resolution = candidate.evidence.get("property_identity_resolution")
    if not isinstance(resolution, dict):
        return ""
    resolved = resolution.get("resolved")
    return str(resolved.get("sale_key") or "") if isinstance(resolved, dict) else ""


async def _resolve_by_identified_sale(
    candidates: list[ParsedContact],
) -> dict[int, tuple[str, int, list[dict[str, Any]], int]]:
    """Rows whose exact sale was already found by date and price keep that sale's property.

    The date/price step stores the sale it matched, but the unit lookup afterwards searches
    on the shortened unit (237) and cannot find a registry unit stored as TAG-AL2-237.
    """
    by_sale_key: dict[str, list[ParsedContact]] = {}
    for candidate in candidates:
        if key := _identified_sale_key(candidate):
            by_sale_key.setdefault(key, []).append(candidate)
    if not by_sale_key:
        return {}
    sale_rows: dict[str, dict[str, Any]] = {}
    for chunk in _chunks(sorted(by_sale_key)):
        for row in await owner_queries.fetch_sales_by_keys({"sale_keys": chunk}):
            sale_rows[str(row["sale_key"])] = row

    unit_keys = sorted({str(row.get("unit_key") or "") for row in sale_rows.values()} - {""})
    history: dict[PropertyIdentity, list[dict[str, Any]]] = {}
    for chunk in _chunks(unit_keys):
        for row in await owner_queries.fetch_unit_sales({"unit_keys": chunk}):
            history.setdefault(_registry_identity(row), []).append(row)

    resolutions: dict[int, tuple[str, int, list[dict[str, Any]], int]] = {}
    for sale_key, matched_candidates in by_sale_key.items():
        sale = sale_rows.get(sale_key)
        rows = history.get(_registry_identity(sale)) if sale else None
        if not rows:
            continue
        for candidate in matched_candidates:
            score = int(candidate.evidence["property_identity_resolution"].get("score") or 100)
            resolutions[id(candidate)] = ("identified_sale_registry_unit", score, rows, 1)
    return resolutions


def _source_location_tokens(candidate: ParsedContact) -> frozenset[str]:
    location = _usable_project_name(candidate.building_name) or _usable_project_name(
        candidate.project_name
    )
    return frozenset(_location_tokens(location))


def _row_tokens(row: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        token
        for field in ("building_name", "project_name", "area_name")
        for token in _location_tokens(str(row.get(field) or ""))
    )


async def _resolve_by_source_unit(
    candidates: list[ParsedContact],
) -> dict[int, tuple[str, int, list[dict[str, Any]], int]]:
    """Match the villa number exactly as the file gave it (TAG-HAR-2-006).

    The import shortens it to 006, which loses the cluster; the original text is kept in
    evidence and compared with the raw registry unit. Because the full id already names the
    cluster, location only has to fit, not score 55: every word of the file's building (or
    project) must appear in the registry location, so "Harmony" fits "Harmony 3" while "Maple"
    or "Harmony 2" do not. Several fitting properties (registry building-name variants) still
    need a 12-point lead.
    """
    by_candidate = {
        id(candidate): key
        for candidate in candidates
        if (key := str(candidate.evidence.get("source_unit_number") or "").lower())
    }
    if not by_candidate:
        return {}
    properties: dict[str, dict[PropertyIdentity, list[dict[str, Any]]]] = {}
    for chunk in _chunks(sorted(set(by_candidate.values()))):
        for row in await owner_queries.fetch_unit_sales({"unit_keys": chunk}):
            properties.setdefault(str(row["unit_key"]), {}).setdefault(
                _registry_identity(row), []
            ).append(row)

    resolutions: dict[int, tuple[str, int, list[dict[str, Any]], int]] = {}
    for candidate in candidates:
        grouped = properties.get(by_candidate.get(id(candidate), ""))
        if not grouped:
            continue
        source_tokens = _source_location_tokens(candidate)
        fitting = [
            rows
            for rows in grouped.values()
            if source_tokens and source_tokens <= _row_tokens(rows[0])
        ]
        if not fitting:
            continue
        ranked = sorted(
            ((_transaction_location_score(candidate, rows[0]), rows) for rows in fitting),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, rows = ranked[0]
        if len(ranked) > 1 and best_score - ranked[1][0] < 12:
            continue
        resolutions[id(candidate)] = (
            "source_unit_exact_registry_unit",
            best_score,
            rows,
            len(ranked),
        )
    return resolutions


async def _resolve_by_short_unit(
    candidates: list[ParsedContact],
) -> dict[int, tuple[str, int, list[dict[str, Any]], int]]:
    """Match the short unit against registry units normalized the same way.

    Uses the same location score and the same 55-point / 12-point acceptance rule as the
    primary unit + location step; a row it cannot separate stays unresolved.
    """
    unit_keys = sorted({candidate.unit_number.lower() for candidate in candidates})
    properties: dict[str, dict[PropertyIdentity, list[dict[str, Any]]]] = {}
    for chunk in _chunks(unit_keys):
        for row in await owner_queries.fetch_unit_sales_by_short_key({"unit_keys": chunk}):
            short_key = str(row.get("short_unit_key") or "")
            properties.setdefault(short_key, {}).setdefault(_registry_identity(row), []).append(row)

    resolutions: dict[int, tuple[str, int, list[dict[str, Any]], int]] = {}
    for candidate in candidates:
        grouped = properties.get(candidate.unit_number.lower())
        if not grouped:
            continue
        ranked = sorted(
            ((_transaction_location_score(candidate, rows[0]), rows) for rows in grouped.values()),
            key=lambda item: item[0],
            reverse=True,
        )
        best_score, rows = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0
        if best_score < 55 or (len(ranked) > 1 and best_score - runner_up < 12):
            continue
        resolutions[id(candidate)] = (
            "registry_short_unit_with_location",
            best_score,
            rows,
            len(ranked),
        )
    return resolutions


async def _apply_registry_unit_fallbacks(
    candidates: list[ParsedContact],
    sales: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    """Second-chance registry lookup for rows the unit + location step left without sales.

    DXBI stores some master-community units with a cluster prefix (TAG-AL2-237) while owner
    exports carry only the villa number (237). Runs only on rows with a property key and no
    sales evidence, adds to ``sales`` in place, and never changes a row that already resolved.
    """
    pending = [
        candidate
        for candidate in candidates
        if candidate.unit_number and candidate.property_key and candidate.property_key not in sales
    ]
    if not pending:
        return {"pending": 0, "resolved": 0, "collisions": 0}

    resolutions = await _resolve_by_identified_sale(pending)
    for step in (_resolve_by_source_unit, _resolve_by_short_unit):
        remaining = [candidate for candidate in pending if id(candidate) not in resolutions]
        resolutions.update(await step(remaining))

    # A property key may stand for one registry property only. Short units can collide
    # (Alaya TAG-AL2-237 and TAG-M1-237 both become "alaya|237"), and sales are keyed by
    # property key, so a row is left unresolved when its key is already taken: by an earlier
    # step for a different registry property, by another property resolved here, or by a row
    # that is still ambiguous (which would otherwise inherit this property's sales).
    unresolved_keys = {
        candidate.property_key for candidate in pending if id(candidate) not in resolutions
    }
    identities_by_key: dict[str, set[PropertyIdentity]] = {}
    for candidate in pending:
        resolution = resolutions.get(id(candidate))
        if resolution:
            target_key = _resolved_property_key(resolution[2][0])
            identities_by_key.setdefault(target_key, set()).add(
                _registry_identity(resolution[2][0])
            )

    resolved = 0
    collisions = 0
    for candidate in pending:
        resolution = resolutions.get(id(candidate))
        if not resolution:
            continue
        method, score, rows, candidate_count = resolution
        canonical = rows[0]
        target_key = _resolved_property_key(canonical)
        taken_by_other = bool(sales.get(target_key)) and _registry_identity(
            sales[target_key][0]
        ) != _registry_identity(canonical)
        if (
            not target_key
            or len(identities_by_key[target_key]) > 1
            or taken_by_other
            or target_key in unresolved_keys
        ):
            collisions += 1
            continue

        source_identity = {
            "area_name": candidate.area_name,
            "project_name": candidate.project_name,
            "building_name": candidate.building_name,
            "unit_number": candidate.unit_number,
        }
        candidate.area_name = str(canonical.get("area_name") or candidate.area_name)
        candidate.project_name = str(canonical.get("project_name") or candidate.project_name)
        candidate.building_name = str(
            canonical.get("building_name") or canonical.get("project_name") or ""
        )
        candidate.unit_number = _normalize_unit(canonical.get("unit_number"))
        candidate.evidence.pop("sales_lookup", None)
        candidate.evidence["registry_unit_fallback"] = {
            "method": method,
            "score": score,
            "registry_unit_number": str(canonical.get("unit_number") or ""),
            "registry_property_candidates": candidate_count,
            "source": source_identity,
        }

        selected: list[dict[str, Any]] = []
        seen_sale_keys: set[str] = set()
        for row in sorted(rows, key=lambda item: item["transaction_date"]):
            sale_key = str(row.get("sale_key") or "")
            if sale_key in seen_sale_keys:
                continue
            seen_sale_keys.add(sale_key)
            selected.append({**row, "_match_method": method})
        sales.setdefault(candidate.property_key, selected)
        resolved += 1

    counts = {"pending": len(pending), "resolved": resolved, "collisions": collisions}
    logger.info("Registry unit fallback: %s", counts)
    return counts
