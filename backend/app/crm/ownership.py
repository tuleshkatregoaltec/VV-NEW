from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm import import_repository, owner_queries
from app.crm.cadastral import resolve_cadastral_plots, villa_sales_for_plots
from app.crm.identity import (
    ParsedContact,
    _candidate_owner_key,
    _location_key,
    _location_number_markers,
    _location_similarity,
    _location_tokens,
    _normalize_unit,
    _recover_embedded_property_identity,
    _transaction_location_score,
    _usable_project_name,
)
from app.crm.import_context import in_file_context, prepare_file_context, recover_from_file_context
from app.crm.import_events import has_sale_evidence, is_owner_list_entry
from app.crm.import_models import (
    CrmContactImportSummary,
)
from app.crm.owners import _invalidate_owner_summary_cache
from app.crm.registry_fallback import _apply_registry_unit_fallbacks

logger = logging.getLogger(__name__)

_QUERY_CHUNK = 4_000

_SALES_EVIDENCE_UNIT_CHUNK = 20_000

_TRANSACTION_SIGNATURE_CHUNK = 1_500

_EXCEL_ERROR = re.compile(
    r"#(REF!|N/A|VALUE!|NAME\?|DIV/0!|NULL!|NUM!|SPILL!|CALC!)", re.IGNORECASE
)

_STATUS_SUMMARY_FIELDS = {
    "verified_current_owner": "verified_current_owners",
    "probable_current_owner": "probable_current_owners",
    "unit_linked_unverified": "unit_linked_unverified",
    "former_owner": "former_owners",
    "conflicting_claim": "conflicting_claims",
    "insufficient_property_data": "insufficient_property_data",
    "registry_not_observed": "registry_not_observed",
    "unmatched": "unmatched",
}


def _chunks(values: list[str], size: int = _QUERY_CHUNK) -> Iterable[list[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _unique_unit_on_signature(
    candidate: ParsedContact,
    properties: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Fallback for a date+price signature the location rules rejected or could not decide.

    Only one of the sales on that exact date and price may carry the row's unit, and the row's
    location has to fit that sale without contradicting it: at least one of its building,
    project or area must match the sale (score >= 55, or one name's words contained in the
    other's, e.g. "GR Panorama" and "Panorama"), and building numbers must not differ
    ("Tower 1" never matches "Tower 2"). Excel error values such as #REF! are not locations.
    A row with no location at all is decided by the unit.
    """
    unit = _normalize_unit(candidate.unit_number)
    if not unit:
        return None
    matches = [row for row in properties if _normalize_unit(row.get("unit_number")) == unit]
    if len(matches) != 1:
        return None
    sale = matches[0]
    owner_locations = [
        value
        for value in (
            _fallback_location(candidate.building_name),
            _fallback_location(candidate.project_name),
            _fallback_location(candidate.area_name),
        )
        if value
    ]
    if not owner_locations:
        return sale
    sale_building = str(sale.get("building_name") or "")
    sale_locations = [
        value
        for value in (
            sale_building,
            str(sale.get("project_name") or ""),
            str(sale.get("area_name") or ""),
        )
        if value
    ]
    owner_numbers = _location_number_markers(_fallback_location(candidate.building_name))
    sale_numbers = _location_number_markers(sale_building)
    if owner_numbers and sale_numbers and owner_numbers != sale_numbers:
        return None
    fits = any(
        _location_fits(owner, observed) for owner in owner_locations for observed in sale_locations
    )
    return sale if fits else None


def _fallback_location(value: str) -> str:
    location = _usable_project_name(value)
    return "" if _EXCEL_ERROR.fullmatch(location) else location


def _location_fits(owner: str, observed: str) -> bool:
    if _location_similarity(owner, observed) >= 55:
        return True
    owner_tokens = frozenset(_location_tokens(owner))
    observed_tokens = frozenset(_location_tokens(observed))
    return bool(owner_tokens and observed_tokens) and (
        owner_tokens <= observed_tokens or observed_tokens <= owner_tokens
    )


def _unique_property_in_master_project_area(
    candidate: ParsedContact,
    properties: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Ignore a long registry number appended to a master-project area label."""
    if candidate.evidence.get("source_area_field") != "master_project":
        return None
    source_area = _fallback_location(candidate.area_name)
    if not re.search(r"\b\d{3,}$", source_area):
        return None
    source_tokens = {token for token in _location_tokens(source_area) if not token.isdigit()}
    if len(source_tokens) < 2:
        return None

    matches = [
        row
        for row in properties
        if {
            token
            for token in _location_tokens(str(row.get("area_name") or ""))
            if not token.isdigit()
        }
        == source_tokens
    ]
    return matches[0] if len(matches) == 1 else None


async def _recover_property_identity_from_transaction(
    candidates: list[ParsedContact],
) -> dict[str, int]:
    """Resolve a canonical DXBI property from an exact sale date and price.

    Transaction-party imports usually contain authoritative DLD date/price
    evidence even when their project fields use a different naming scheme.
    A globally unique signature is sufficient to recover the property. When a
    signature occurs on multiple properties, location context must select one
    candidate with a clear score margin.
    """
    eligible = [
        candidate
        for candidate in candidates
        if has_sale_evidence(candidate)
        and candidate.transaction_date
        and candidate.transaction_price_aed
    ]
    unique_resolutions = 0
    contextual_resolutions = 0
    unit_resolutions = 0
    master_project_area_resolutions = 0
    ambiguous = 0
    for start in range(0, len(eligible), _TRANSACTION_SIGNATURE_CHUNK):
        batch = eligible[start : start + _TRANSACTION_SIGNATURE_CHUNK]
        wanted = {
            (candidate.transaction_date, int(candidate.transaction_price_aed or 0))
            for candidate in batch
        }
        rows = await owner_queries.fetch_transaction_signatures(
            {
                "dates": sorted({signature[0] for signature in wanted}),
                "prices": sorted({signature[1] for signature in wanted}),
            }
        )
        by_signature: dict[
            tuple[date, int],
            dict[tuple[str, str, str], dict[str, Any]],
        ] = {}
        for row in rows:
            signature = (
                row["transaction_date"],
                int(row.get("sale_amount_aed") or 0),
            )
            if signature not in wanted:
                continue
            unit_key = str(row.get("unit_key") or "")
            building_key = str(row.get("building_key") or "") or _location_key(
                str(row.get("building_name") or row.get("project_name") or "")
            )
            if not unit_key or not building_key:
                continue
            property_identity = (
                str(row.get("area_key") or ""),
                building_key,
                unit_key,
            )
            by_signature.setdefault(signature, {}).setdefault(property_identity, row)

        for candidate in batch:
            signature = (
                candidate.transaction_date,
                int(candidate.transaction_price_aed or 0),
            )
            properties = [
                row
                for row in by_signature.get(signature, {}).values()
                if in_file_context(candidate, row)
            ]
            if not properties:
                continue

            method = "unique_transaction_signature"
            score = 100
            selected: dict[str, Any] | None = None
            if len(properties) == 1:
                selected = properties[0]
                location_score = _transaction_location_score(candidate, selected)
                has_source_location = bool(
                    _usable_project_name(candidate.building_name)
                    or _usable_project_name(candidate.project_name)
                    or _usable_project_name(candidate.area_name)
                )
                if has_source_location and location_score < 55:
                    selected = _unique_unit_on_signature(candidate, properties)
                    if selected is None:
                        selected = _unique_property_in_master_project_area(candidate, properties)
                        if selected is None:
                            ambiguous += 1
                            continue
                        method = "transaction_signature_with_master_project_area"
                        master_project_area_resolutions += 1
                    else:
                        method = "unique_transaction_unit_signature"
                        unit_resolutions += 1
                else:
                    unique_resolutions += 1
                score = (
                    location_score
                    if method == "transaction_signature_with_master_project_area"
                    else location_score or 100
                )
            else:
                scored = sorted(
                    ((_transaction_location_score(candidate, row), row) for row in properties),
                    key=lambda item: item[0],
                    reverse=True,
                )
                score, selected = scored[0]
                runner_up = scored[1][0]
                if score < 55 or score - runner_up < 12:
                    selected = _unique_unit_on_signature(candidate, properties)
                    if selected is None:
                        selected = _unique_property_in_master_project_area(candidate, properties)
                        if selected is None:
                            ambiguous += 1
                            continue
                        method = "transaction_signature_with_master_project_area"
                        master_project_area_resolutions += 1
                    else:
                        method = "unique_transaction_unit_signature"
                        unit_resolutions += 1
                    score = _transaction_location_score(candidate, selected)
                else:
                    method = "transaction_signature_with_location"
                    contextual_resolutions += 1

            if not selected:
                continue
            source_identity = {
                "area_name": candidate.area_name,
                "project_name": candidate.project_name,
                "building_name": candidate.building_name,
                "unit_number": candidate.unit_number,
            }
            candidate.area_name = str(selected.get("area_name") or candidate.area_name)
            candidate.project_name = str(selected.get("project_name") or candidate.project_name)
            candidate.building_name = str(
                selected.get("building_name") or selected.get("project_name") or ""
            )
            candidate.unit_number = _normalize_unit(selected.get("unit_number"))
            candidate.evidence["property_identity_resolution"] = {
                "method": method,
                "score": score,
                "transaction_candidate_count": len(properties),
                "source": source_identity,
                "resolved": {
                    "area_name": candidate.area_name,
                    "project_name": candidate.project_name,
                    "building_name": candidate.building_name,
                    "unit_number": candidate.unit_number,
                    "sale_key": str(selected.get("sale_key") or ""),
                },
            }

    return {
        "eligible": len(eligible),
        "unique": unique_resolutions,
        "contextual": contextual_resolutions,
        "unit": unit_resolutions,
        "master_project_area": master_project_area_resolutions,
        "ambiguous": ambiguous,
    }


async def _recover_property_identity_from_near_transaction(
    candidates: list[ParsedContact],
    *,
    window_days: int = 14,
) -> dict[str, int]:
    """Recover property identity when registry/display dates differ slightly.

    The DLD registration date in owner exports can differ from DXBI's displayed
    transaction date. Price remains exact, and a project/building hierarchy
    must identify one property within the bounded window.
    """
    eligible = [
        candidate
        for candidate in candidates
        if has_sale_evidence(candidate)
        and candidate.transaction_date
        and candidate.transaction_price_aed
        and "property_identity_resolution" not in candidate.evidence
    ]
    resolved = 0
    ambiguous = 0
    for start in range(0, len(eligible), 500):
        batch = eligible[start : start + 500]
        dates = {
            candidate.transaction_date + timedelta(days=offset)
            for candidate in batch
            for offset in range(-window_days, window_days + 1)
            if candidate.transaction_date
        }
        prices = sorted({int(candidate.transaction_price_aed or 0) for candidate in batch})
        rows = await owner_queries.fetch_near_transaction_signatures(
            {"dates": sorted(dates), "prices": prices}
        )
        rows_by_price: dict[int, list[dict[str, Any]]] = {}
        for row in rows:
            rows_by_price.setdefault(int(row.get("sale_amount_aed") or 0), []).append(row)

        for candidate in batch:
            source_date = candidate.transaction_date
            if not source_date:
                continue
            candidate_rows = [
                row
                for row in rows_by_price.get(int(candidate.transaction_price_aed or 0), [])
                if abs((row["transaction_date"] - source_date).days) <= window_days
                and in_file_context(candidate, row)
            ]
            properties: dict[tuple[str, str, str], dict[str, Any]] = {}
            for row in candidate_rows:
                unit_key = str(row.get("unit_key") or "")
                building_key = str(row.get("building_key") or "") or _location_key(
                    str(row.get("building_name") or row.get("project_name") or "")
                )
                if not unit_key or not building_key:
                    continue
                identity = (
                    str(row.get("area_key") or ""),
                    building_key,
                    unit_key,
                )
                current = properties.get(identity)
                if current is None or abs((row["transaction_date"] - source_date).days) < abs(
                    (current["transaction_date"] - source_date).days
                ):
                    properties[identity] = row
            if not properties:
                continue

            ranked = sorted(
                (
                    (
                        _transaction_location_score(candidate, row),
                        abs((row["transaction_date"] - source_date).days),
                        row,
                    )
                    for row in properties.values()
                ),
                key=lambda item: (-item[0], item[1]),
            )
            score, date_delta, selected = ranked[0]
            if score < 55:
                continue
            if len(ranked) > 1:
                runner_score, runner_delta, _ = ranked[1]
                clear_location = score - runner_score >= 12
                clear_date = score >= 80 and date_delta + 2 <= runner_delta
                if not (clear_location or clear_date):
                    ambiguous += 1
                    continue

            source_identity = {
                "area_name": candidate.area_name,
                "project_name": candidate.project_name,
                "building_name": candidate.building_name,
                "unit_number": candidate.unit_number,
            }
            candidate.area_name = str(selected.get("area_name") or candidate.area_name)
            candidate.project_name = str(selected.get("project_name") or candidate.project_name)
            candidate.building_name = str(
                selected.get("building_name") or selected.get("project_name") or ""
            )
            candidate.unit_number = _normalize_unit(selected.get("unit_number"))
            candidate.evidence["property_identity_resolution"] = {
                "method": "near_transaction_signature_with_location",
                "score": score,
                "date_delta_days": date_delta,
                "transaction_candidate_count": len(ranked),
                "source": source_identity,
                "resolved": {
                    "area_name": candidate.area_name,
                    "project_name": candidate.project_name,
                    "building_name": candidate.building_name,
                    "unit_number": candidate.unit_number,
                    "sale_key": str(selected.get("sale_key") or ""),
                },
            }
            resolved += 1

    return {"eligible": len(eligible), "resolved": resolved, "ambiguous": ambiguous}


async def _sales_evidence(
    candidates: list[ParsedContact],
) -> dict[str, list[dict[str, Any]]]:
    """Find DXBI sales history through a canonical project/building hierarchy."""
    unit_keys = sorted(
        {
            candidate.unit_number.lower()
            for candidate in candidates
            if candidate.property_key and candidate.unit_number
        }
    )
    evidence: dict[str, list[dict[str, Any]]] = {}
    sales_by_unit: dict[str, list[dict[str, Any]]] = {}
    for chunk in _chunks(unit_keys, size=_SALES_EVIDENCE_UNIT_CHUNK):
        rows = await owner_queries.fetch_unit_sales({"unit_keys": chunk})
        for row in rows:
            sales_by_unit.setdefault(str(row["unit_key"]), []).append(row)

    properties_by_unit: dict[
        str,
        list[tuple[tuple[str, str, str], list[dict[str, Any]]]],
    ] = {}
    for unit_key, unit_rows in sales_by_unit.items():
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
        for row in unit_rows:
            building_key = str(row.get("building_key") or "") or _location_key(
                str(row.get("building_name") or row.get("project_name") or "")
            )
            identity = (
                str(row.get("area_key") or ""),
                building_key,
                str(row.get("unit_key") or ""),
            )
            grouped.setdefault(identity, []).append(row)
        properties_by_unit[unit_key] = list(grouped.items())

    resolution_cache: dict[
        tuple,
        tuple[int, str, list[dict[str, Any]], int] | None,
    ] = {}
    for candidate in candidates:
        source_property_key = candidate.property_key
        if not source_property_key or not candidate.unit_number:
            continue
        rows_for_unit = sales_by_unit.get(candidate.unit_number.lower(), [])
        if not rows_for_unit:
            candidate.evidence["sales_lookup"] = {
                "status": "unit_not_observed",
                "normalized_unit_number": candidate.unit_number.lower(),
            }
            continue

        cache_key = (
            _location_key(candidate.area_name),
            _location_key(candidate.project_name),
            _location_key(candidate.building_name),
            candidate.unit_number.lower(),
            tuple(candidate.evidence.get("file_context", {}).get("locations", [])),
            candidate.evidence.get("property_identity_resolution", {})
            .get("resolved", {})
            .get("sale_key"),
        )
        if cache_key in resolution_cache:
            cached = resolution_cache[cache_key]
            if cached is None:
                continue
            best_score, method, selected_rows, registry_candidate_count = cached
        else:
            identified_sale = (
                candidate.evidence.get("property_identity_resolution", {})
                .get("resolved", {})
                .get("sale_key")
            )
            ranked = sorted(
                (
                    (
                        _transaction_location_score(candidate, property_rows[0]),
                        identity,
                        property_rows,
                    )
                    for identity, property_rows in properties_by_unit[candidate.unit_number.lower()]
                    if in_file_context(candidate, property_rows[0])
                    and (
                        not identified_sale
                        or any(row.get("sale_key") == identified_sale for row in property_rows)
                    )
                ),
                key=lambda item: item[0],
                reverse=True,
            )
            if not ranked:
                candidate.evidence["sales_lookup"] = {"status": "location_not_observed"}
                resolution_cache[cache_key] = None
                continue
            best_score, _, selected_rows = ranked[0]
            runner_up = ranked[1][0] if len(ranked) > 1 else 0
            distinctive_unit = len(candidate.unit_number) >= 5 and any(
                character.isalpha() for character in candidate.unit_number
            )
            unique_distinctive_unit = distinctive_unit and len(ranked) == 1
            if not unique_distinctive_unit and (
                best_score < 55 or (len(ranked) > 1 and best_score - runner_up < 12)
            ):
                candidate.evidence["sales_lookup"] = {
                    "status": "ambiguous_location",
                    "registry_property_candidates": len(ranked),
                    "best_location_score": best_score,
                    "runner_up_location_score": runner_up,
                }
                resolution_cache[cache_key] = None
                continue

            method = (
                "unique_registry_unit_identifier"
                if unique_distinctive_unit
                else "unit_with_location_hierarchy"
            )
            registry_candidate_count = len(ranked)
            resolution_cache[cache_key] = (
                best_score,
                method,
                selected_rows,
                registry_candidate_count,
            )

        canonical = selected_rows[0]
        candidate.evidence.pop("sales_lookup", None)
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
        if candidate.property_key != source_property_key:
            candidate.evidence["unit_hierarchy_resolution"] = {
                "method": method,
                "score": best_score,
                "registry_property_candidates": registry_candidate_count,
                "source": source_identity,
                "resolved": {
                    "area_name": candidate.area_name,
                    "project_name": candidate.project_name,
                    "building_name": candidate.building_name,
                    "unit_number": candidate.unit_number,
                },
            }

        selected: list[dict[str, Any]] = []
        seen_sale_keys: set[str] = set()
        for row in selected_rows:
            sale_key = str(row.get("sale_key") or "")
            if sale_key in seen_sale_keys:
                continue
            seen_sale_keys.add(sale_key)
            selected.append({**row, "_match_method": method})
        evidence[candidate.property_key] = selected
    return evidence


async def _rental_evidence(
    candidates: list[ParsedContact],
) -> dict[str, dict[str, Any]]:
    units = sorted({candidate.unit_number for candidate in candidates if candidate.unit_number})
    return await _rental_evidence_for_units(units)


async def _rental_evidence_for_units(units: list[str]) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    units = sorted({_normalize_unit(unit) for unit in units if _normalize_unit(unit)})
    if not units:
        return evidence
    columns = await owner_queries.fetch_rental_source_columns()
    has_source_unit = bool(columns[0]["has_unit"])
    anchor = date.today()
    for chunk in _chunks(units):
        rows = await owner_queries.fetch_rental_evidence(
            {
                "units": chunk,
                "window_start": (anchor - timedelta(days=365)).isoformat(),
                "window_end": (anchor + timedelta(days=90)).isoformat(),
            },
            has_source_unit,
        )
        for row in rows:
            location = str(row.get("building_name") or row.get("project_name") or "")
            key = f"{_location_key(location)}|{_normalize_unit(row['unit_number']).lower()}"
            evidence.setdefault(key, row)
    return evidence


async def refresh_property_contact_rentals(
    db: AsyncSession,
    *,
    organization_id: str,
    import_id: int | None = None,
    commit: bool = True,
) -> dict[str, int]:
    """Refresh rental links for every imported claim without changing owner identities.

    Missing current evidence clears an old rental link. Contact/source data,
    ownership assessments, sales evidence and agent workspaces remain intact.
    """
    claims = await import_repository.list_rental_refresh_claims(db, import_id, organization_id)
    units = sorted({claim.unit_number for claim in claims if claim.unit_number})
    rentals = await _rental_evidence_for_units(units)
    counts = {
        "claims": len(claims),
        "before_hits": 0,
        "after_hits": 0,
        "added_hits": 0,
        "removed_hits": 0,
        "changed_claims": 0,
    }
    now = datetime.now(timezone.utc)
    for claim in claims:
        before = (claim.lead_id, claim.unit_candidate_key, claim.lease_end, claim.annual_rent_aed)
        rental = rentals.get(claim.property_key)
        after = (
            (
                str(rental["event_key"]),
                str(rental["unit_candidate_key"]),
                rental.get("lease_end"),
                int(rental.get("annual_rent_aed") or 0) or None,
            )
            if rental
            else (None, None, None, None)
        )
        counts["before_hits"] += bool(before[0])
        counts["after_hits"] += bool(after[0])
        counts["added_hits"] += bool(after[0]) and not before[0]
        counts["removed_hits"] += bool(before[0]) and not after[0]
        if before != after:
            (claim.lead_id, claim.unit_candidate_key, claim.lease_end, claim.annual_rent_aed) = (
                after
            )
            claim.updated_at = now
            import_repository.save(db, claim)
            counts["changed_claims"] += 1
    await db.flush()
    import_ids = {claim.import_id for claim in claims}
    if import_id is not None:
        import_ids.add(import_id)
    await _refresh_contact_import_match_summaries(
        db,
        organization_id=organization_id,
        import_ids=import_ids,
    )
    if commit:
        await db.commit()
    _invalidate_owner_summary_cache(organization_id)
    return counts


def _price_matches(imported: int | None, observed: int) -> bool:
    if not imported or not observed:
        return True
    return abs(imported - observed) / observed <= 0.02


def _apply_plot_evidence(candidate: ParsedContact, villa_sales: list[dict[str, Any]]) -> None:
    """Judge a villa/plot row against the DLD sales of the plot's villa code."""
    candidate.ownership_status = "unit_linked_unverified"
    candidate.confidence_score = 50
    candidate.match_reason = (
        "The land number and location match a DLD plot. The source record is retained, "
        "but an individual villa/unit and current ownership have not been verified."
    )
    if villa_sales:
        latest = villa_sales[-1]
        candidate.latest_sale_date = latest["transaction_date"]
        candidate.latest_sale_price_aed = int(latest.get("sale_amount_aed") or 0) or None
        candidate.evidence["villa_sales"] = {
            "villa_code": candidate.evidence["cadastral_identity"].get("villa_code", ""),
            "matched_sales": len(villa_sales),
            "sale_dates": [str(sale["transaction_date"]) for sale in villa_sales],
        }

    if has_sale_evidence(candidate) and candidate.party_role == "Seller":
        candidate.ownership_status = "former_owner"
        candidate.confidence_score = 100
        candidate.match_reason = (
            "The uploaded record identifies this contact as the seller, not the current buyer."
        )
        candidate.later_sale_date = candidate.transaction_date
        return
    if not villa_sales or not candidate.transaction_date:
        return

    later = [
        sale
        for sale in villa_sales
        if sale["transaction_date"] > candidate.transaction_date + timedelta(days=2)
    ]
    if later:
        candidate.ownership_status = "former_owner"
        candidate.confidence_score = 99
        candidate.later_sale_date = later[-1]["transaction_date"]
        candidate.match_reason = (
            f"The plot's villa was sold again on {later[-1]['transaction_date']:%d %b %Y}; "
            "this contact is not presented as the current owner."
        )
        return

    acquirer = (
        has_sale_evidence(candidate) and candidate.party_role == "Buyer"
    ) or is_owner_list_entry(candidate)
    if not acquirer:
        return
    same_date = [
        sale
        for sale in villa_sales
        if abs((sale["transaction_date"] - candidate.transaction_date).days) <= 2
    ]
    if same_date and not any(
        _price_matches(candidate.transaction_price_aed, int(sale.get("sale_amount_aed") or 0))
        for sale in same_date
    ):
        candidate.ownership_status = "conflicting_claim"
        candidate.confidence_score = 45
        candidate.match_reason = (
            "The plot's villa sale on this date differs by more than 2% from the uploaded price."
        )
        return
    if same_date and candidate.transaction_price_aed:
        candidate.ownership_status = "verified_current_owner"
        candidate.confidence_score = 98
        candidate.match_reason = (
            "The plot's villa sale matches the acquisition date and price, with no later "
            "transfer observed."
        )
        return
    candidate.ownership_status = "probable_current_owner"
    candidate.confidence_score = 72
    candidate.match_reason = (
        "No sale of the plot's villa is recorded after this acquisition. The acquisition "
        "itself (e.g. a completed delayed sale) is not in the sales registry; review before "
        "relying on this owner."
    )


def _apply_evidence(
    candidate: ParsedContact,
    *,
    sales: dict[str, list[dict[str, Any]]],
    rentals: dict[str, dict[str, Any]],
) -> None:
    key = candidate.property_key
    if not candidate.unit_number and candidate.evidence.get("cadastral_identity"):
        _apply_plot_evidence(candidate, sales.get(key, []))
        return
    sale_events = sales.get(key, [])
    rental = rentals.get(key)
    if rental:
        candidate.lead_id = str(rental["event_key"])
        candidate.unit_candidate_key = str(rental["unit_candidate_key"])
        candidate.lease_end = rental.get("lease_end")
        candidate.annual_rent_aed = int(rental.get("annual_rent_aed") or 0) or None

    latest = sale_events[-1] if sale_events else None
    if latest:
        candidate.latest_sale_date = latest["transaction_date"]
        candidate.latest_sale_price_aed = int(latest.get("sale_amount_aed") or 0) or None
        methods = sorted(
            {str(event.get("_match_method") or "exact_building_key") for event in sale_events}
        )
        candidate.evidence["sales_match"] = {
            "property_type": str(latest.get("property_type") or ""),
            "market_status": str(latest.get("market_status") or ""),
            "methods": methods,
            "matched_buildings": sorted(
                {
                    str(event.get("building_name") or "")
                    for event in sale_events
                    if event.get("building_name")
                }
            ),
            "matched_sales": len(sale_events),
        }

    if not key:
        candidate.ownership_status = "insufficient_property_data"
        candidate.confidence_score = 0
        candidate.match_reason = candidate.evidence.get("context_lookup", {}).get("reason") or (
            "Source record retained. The land/building/property identifiers require "
            "resolution before this contact can be linked to a registry property."
        )
        return

    owner_list_sale = (
        next(
            (
                event
                for event in sale_events
                if abs((event["transaction_date"] - candidate.transaction_date).days) <= 2
                and _price_matches(
                    candidate.transaction_price_aed, int(event.get("sale_amount_aed") or 0)
                )
            ),
            None,
        )
        if is_owner_list_entry(candidate)
        else None
    )
    if owner_list_sale:
        # The owner list gives no role; the matching DLD sale is the acquisition.
        candidate.evidence["acquisition_inferred"] = True
    elif not has_sale_evidence(candidate):
        candidate.ownership_status = "unit_linked_unverified" if latest or rental else "unmatched"
        candidate.confidence_score = 50 if latest or rental else 20
        candidate.match_reason = (
            "Source event retained for review. This procedure or party role does not "
            "establish a sale acquisition or disposal; its date and amount were not "
            "used to infer current ownership from sales history."
        )
        return

    if candidate.party_role == "Seller":
        candidate.ownership_status = "former_owner"
        candidate.confidence_score = 100
        candidate.match_reason = (
            "The uploaded record identifies this contact as the seller, not the current buyer."
        )
        if candidate.transaction_date:
            candidate.later_sale_date = candidate.transaction_date
        return

    if not key:
        candidate.ownership_status = "insufficient_property_data"
        candidate.confidence_score = 0
        candidate.match_reason = (
            "The source row has transaction evidence but no recoverable unit/property "
            "identifier; registry matching requires source enrichment."
        )
        return

    if not latest:
        if rental:
            candidate.ownership_status = "unit_linked_unverified"
            candidate.confidence_score = 60
            candidate.match_reason = (
                "The contact links to an exact lease-opportunity unit, but acquisition "
                "evidence was not found in the sales registry."
            )
        else:
            lookup_status = str((candidate.evidence.get("sales_lookup") or {}).get("status") or "")
            if lookup_status == "unit_not_observed":
                candidate.ownership_status = "registry_not_observed"
                candidate.confidence_score = 10
                candidate.match_reason = (
                    "The source contains a usable unit identifier, but that unit is not "
                    "present in current DXBI sales-registry coverage."
                )
            else:
                candidate.match_reason = (
                    "Registry candidates exist for the unit, but the property location "
                    "could not be resolved unambiguously."
                )
        return

    identity_label = (
        "canonical building identity and unit"
        if any(str(event.get("_match_method")) != "exact_building_key" for event in sale_events)
        else "exact building and unit"
    )

    if not candidate.transaction_date:
        candidate.ownership_status = "unit_linked_unverified"
        candidate.confidence_score = 62 if rental else 55
        candidate.match_reason = (
            "The unit is exact, but the uploaded contact list does not provide an "
            "acquisition date or price."
        )
        return

    resolution = candidate.evidence.get("property_identity_resolution", {})
    identified_key = resolution.get("resolved", {}).get("sale_key")
    matched_event = next(
        (
            event
            for event in sale_events
            if identified_key and event.get("sale_key") == identified_key
        ),
        None,
    ) or next(
        (
            event
            for event in sale_events
            if abs((event["transaction_date"] - candidate.transaction_date).days) <= 2
        ),
        None,
    )
    acquisition_date = (
        matched_event["transaction_date"]
        if matched_event
        else candidate.transaction_date + timedelta(days=2)
    )
    if latest["transaction_date"] > acquisition_date:
        candidate.ownership_status = "former_owner"
        candidate.confidence_score = 99
        candidate.later_sale_date = latest["transaction_date"]
        candidate.match_reason = (
            f"A later sale was recorded on {latest['transaction_date']:%d %b %Y}; "
            "this contact is not presented as the current owner."
        )
        if resolution.get("method") == "file_context_transaction_signature":
            candidate.match_reason += (
                f" Property inferred from {', '.join(candidate.evidence.get('file_context', {}).get('locations', []))}, "
                f"the source amount and acquisition dated {acquisition_date:%d %b %Y}."
            )
            candidate.confidence_score = 90 if not resolution.get("date_delta_days") else 80
        return

    if matched_event and not _price_matches(
        candidate.transaction_price_aed,
        int(matched_event.get("sale_amount_aed") or 0),
    ):
        candidate.ownership_status = "conflicting_claim"
        candidate.confidence_score = 45
        candidate.match_reason = (
            "The building, unit, and date match, but the uploaded price differs by more "
            "than 2% from the sales registry."
        )
        return

    if matched_event and candidate.transaction_price_aed:
        shifted_days = abs((matched_event["transaction_date"] - candidate.transaction_date).days)
        if shifted_days:
            candidate.ownership_status = "probable_current_owner"
            candidate.confidence_score = 80
            candidate.match_reason = (
                f"One property matches the location, amount and supplied details. Its sale "
                f"date ({matched_event['transaction_date']:%d %b %Y}) differs from the file "
                f"by {shifted_days} days. No later sale is observed; review the date before relying on this owner."
            )
            return
        candidate.ownership_status = "verified_current_owner"
        candidate.confidence_score = 98
        candidate.match_reason = (
            f"{identity_label.title()}; acquisition date and price match the latest sale, "
            "with no later transfer observed."
        )
        return

    if matched_event:
        candidate.ownership_status = "probable_current_owner"
        candidate.confidence_score = 86
        candidate.match_reason = (
            f"{identity_label.title()}; acquisition date matches the latest sale and no "
            "later transfer is observed. Price evidence was unavailable."
        )
        return

    if candidate.transaction_date > latest["transaction_date"]:
        candidate.ownership_status = "probable_current_owner"
        candidate.confidence_score = 72
        candidate.match_reason = (
            "The exact unit matches, but the uploaded acquisition is newer than available "
            "sales-registry coverage and requires review."
        )
        return

    candidate.ownership_status = "conflicting_claim"
    candidate.confidence_score = 40
    candidate.match_reason = (
        "The unit is exact, but the uploaded acquisition date does not match the observed "
        "sales history."
    )


async def rematch_property_contact_claims(
    db: AsyncSession,
    *,
    organization_id: str,
    import_id: int | None = None,
    only_unmatched: bool = True,
    audit_resolution_methods: set[str] | None = None,
) -> dict[str, int]:
    """Re-evaluate saved owner claims against the current DXBI sales registry.

    Imports are immutable source records, but their evidence changes when either
    DXBI coverage improves or the building-identity resolver improves. This
    updates only calculated matching fields, never the uploaded contact data.
    """
    claims = await import_repository.list_claims_for_rematch(
        db, audit_resolution_methods, import_id, only_unmatched, organization_id
    )
    candidates: list[ParsedContact] = []
    for claim in claims:
        claim_evidence = dict(claim.evidence or {})
        source_identity: dict[str, Any] = {}
        if audit_resolution_methods:
            prior_resolution = claim_evidence.get("property_identity_resolution")
            if (
                isinstance(prior_resolution, dict)
                and prior_resolution.get("method") in audit_resolution_methods
            ):
                raw_source = prior_resolution.get("source")
                if isinstance(raw_source, dict):
                    source_identity = raw_source
            for calculated_key in (
                "property_identity_resolution",
                "unit_hierarchy_resolution",
                "sales_lookup",
                "sales_match",
            ):
                claim_evidence.pop(calculated_key, None)

        def identity_field(field_name: str, current: str) -> str:
            value = source_identity.get(field_name)
            return str(value) if value is not None else current

        candidate = ParsedContact(
            source_sheet=claim.source_sheet,
            source_row_number=claim.source_row_number,
            contact_name=claim.contact_name,
            phone_primary=claim.phone_primary,
            phone_alternate=claim.phone_alternate,
            email_primary=claim.email_primary,
            area_name=identity_field("area_name", claim.area_name),
            project_name=identity_field("project_name", claim.project_name),
            building_name=identity_field("building_name", claim.building_name),
            unit_number=identity_field("unit_number", claim.unit_number),
            property_type=claim.property_type,
            transaction_date=claim.transaction_date,
            transaction_price_aed=claim.transaction_price_aed,
            party_role=claim.party_role,
            procedure_type=claim.procedure_type,
            row_fingerprint=claim.row_fingerprint,
            evidence={key: value for key, value in claim_evidence.items() if key != "sales_match"},
        )
        _recover_embedded_property_identity(candidate)
        candidates.append(candidate)
    await prepare_file_context(candidates)
    resolution_counts = await _recover_property_identity_from_transaction(candidates)
    near_resolution_counts = await _recover_property_identity_from_near_transaction(candidates)
    await recover_from_file_context(candidates)
    sales = await _sales_evidence(candidates)
    await _apply_registry_unit_fallbacks(candidates, sales)
    rentals = await _rental_evidence(candidates)
    await resolve_cadastral_plots(candidates)
    sales.update(await villa_sales_for_plots(candidates))
    now = datetime.now(timezone.utc)
    status_counts: dict[str, int] = {}
    for claim, candidate in zip(claims, candidates, strict=True):
        _apply_evidence(candidate, sales=sales, rentals=rentals)
        claim.area_name = candidate.area_name
        claim.project_name = candidate.project_name
        claim.building_name = candidate.building_name
        claim.unit_number = candidate.unit_number
        claim.property_key = candidate.property_key or f"source:{candidate.row_fingerprint}"
        claim.owner_key = _candidate_owner_key(candidate)
        claim.ownership_status = candidate.ownership_status
        claim.confidence_score = candidate.confidence_score
        claim.match_reason = candidate.match_reason
        claim.lead_id = candidate.lead_id
        claim.unit_candidate_key = candidate.unit_candidate_key
        claim.latest_sale_date = candidate.latest_sale_date
        claim.latest_sale_price_aed = candidate.latest_sale_price_aed
        claim.later_sale_date = candidate.later_sale_date
        claim.lease_end = candidate.lease_end
        claim.annual_rent_aed = candidate.annual_rent_aed
        claim.evidence = candidate.evidence
        claim.updated_at = now
        import_repository.save(db, claim)
        status_counts[candidate.ownership_status] = (
            status_counts.get(candidate.ownership_status, 0) + 1
        )
    await db.flush()
    affected_import_ids = {claim.import_id for claim in claims}
    if import_id is not None:
        affected_import_ids.add(import_id)
    await _refresh_contact_import_match_summaries(
        db,
        organization_id=organization_id,
        import_ids=affected_import_ids,
    )
    await db.commit()
    _invalidate_owner_summary_cache(organization_id)
    return {
        "claims": len(claims),
        **{f"transaction_{key}": value for key, value in resolution_counts.items()},
        **{f"near_transaction_{key}": value for key, value in near_resolution_counts.items()},
        **status_counts,
    }


async def _refresh_contact_import_match_summaries(
    db: AsyncSession,
    *,
    organization_id: str,
    import_ids: set[int],
) -> dict[int, dict[str, int]]:
    """Synchronize persisted import cards with current claim evidence."""
    refreshed: dict[int, dict[str, int]] = {}
    if not import_ids:
        return refreshed

    import_result = await import_repository.list_imports_by_ids(db, import_ids, organization_id)
    for record in import_result:
        record_id = record.id
        if record_id is None:
            continue
        status_result = await import_repository.count_claims_by_status(
            db, organization_id, record_id
        )
        status_counts = {str(status): int(count) for status, count in status_result}
        (
            candidate_rows,
            valid_contacts,
            rental_opportunities,
            contact_ready,
            rows_with_sales_history,
            plot_only_rows,
        ) = await import_repository.count_import_claim_metrics(db, organization_id, record_id)
        distinct_properties = await import_repository.count_import_distinct_properties(
            db, organization_id, record_id
        )
        summary = dict(record.summary or {})
        summary.update(
            {
                "candidate_rows": candidate_rows,
                "distinct_properties": distinct_properties,
                "rows_with_sales_history": rows_with_sales_history,
                "plot_only_rows": plot_only_rows,
                "valid_contacts": valid_contacts,
                "rental_opportunities": rental_opportunities,
                "contact_ready": contact_ready,
            }
        )
        for summary_field in _STATUS_SUMMARY_FIELDS.values():
            summary[summary_field] = 0
        for status, count in status_counts.items():
            summary_field = _STATUS_SUMMARY_FIELDS.get(status)
            if summary_field:
                summary[summary_field] = count
        validated = CrmContactImportSummary.model_validate(summary).model_dump()
        record.summary = validated
        import_repository.save(db, record)
        refreshed[record_id] = {
            "candidate_rows": candidate_rows,
            **status_counts,
        }
    return refreshed


async def refresh_contact_import_match_summaries(
    db: AsyncSession,
    *,
    organization_id: str,
    import_id: int | None = None,
) -> dict[int, dict[str, int]]:
    """Refresh import-card summaries without re-running registry matching."""
    if import_id is not None:
        import_ids = {import_id}
    else:
        result = await import_repository.list_import_ids(db, organization_id)
        import_ids = {record_id for record_id in result if record_id is not None}
    refreshed = await _refresh_contact_import_match_summaries(
        db,
        organization_id=organization_id,
        import_ids=import_ids,
    )
    await db.commit()
    _invalidate_owner_summary_cache(organization_id)
    return refreshed
