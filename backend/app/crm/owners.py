from __future__ import annotations

import logging
from datetime import date
from time import monotonic
from typing import Any

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm import owner_queries, owner_repository
from app.crm.identity import (
    _OWNERSHIP_PRIORITY,
    _claim_owner_key,
    _claim_preference,
    _claim_property_key,
    _dedupe_claims,
    _location_key,
    _location_similarity,
    _normalize_unit,
    _usable_project_name,
)
from app.crm.import_context import property_details
from app.crm.import_events import is_mortgage_review, matches_report_status
from app.crm.import_models import (
    CrmContactClaimResponse,
    CrmContactList,
    CrmContactListDetail,
    CrmContactListSummary,
    CrmLeadImportedContact,
    CrmOwnerProfile,
    CrmOwnerProperty,
    CrmOwnerRegistryResponse,
    CrmOwnerRegistrySummary,
    CrmPropertyContactClaim,
)
from app.crm.owner_repository import (
    _owner_page_keys_sql,
    _owner_properties_sql,
)

logger = logging.getLogger(__name__)

_OWNER_SUMMARY_CACHE_TTL_SECONDS = 300

_owner_summary_cache: dict[tuple[str, int | None], tuple[float, CrmOwnerRegistrySummary]] = {}


def _invalidate_owner_summary_cache(organization_id: str) -> None:
    for key in [key for key in _owner_summary_cache if key[0] == organization_id]:
        _owner_summary_cache.pop(key, None)


def _claim_source_rows(group: list[CrmPropertyContactClaim]) -> list[str]:
    values = {f"{claim.source_sheet}:{claim.source_row_number}" for claim in group}
    for claim in group:
        source_rows = claim.evidence.get("source_rows", []) if claim.evidence else []
        if isinstance(source_rows, list):
            values.update(str(item) for item in source_rows if item)
    return sorted(values)


def _claim_duplicate_count(group: list[CrmPropertyContactClaim]) -> int:
    persisted = [
        int(claim.evidence.get("duplicate_count", 1))
        for claim in group
        if claim.evidence and isinstance(claim.evidence.get("duplicate_count", 1), int)
    ]
    return max(len(group), max(persisted, default=1))


def _claim_response(
    claim: CrmPropertyContactClaim,
    group: list[CrmPropertyContactClaim] | None = None,
) -> CrmContactClaimResponse:
    if claim.id is None:
        raise HTTPException(status_code=500, detail="Imported contact is missing an id")
    grouped = group or [claim]
    return CrmContactClaimResponse(
        id=claim.id,
        source_sheet=claim.source_sheet,
        source_row_number=claim.source_row_number,
        contact_name=claim.contact_name,
        phone_primary=claim.phone_primary,
        phone_alternate=claim.phone_alternate,
        email_primary=claim.email_primary,
        area_name=claim.area_name,
        project_name=claim.project_name,
        building_name=claim.building_name,
        unit_number=claim.unit_number,
        property_type=claim.property_type,
        property_details=property_details(claim.evidence or {}),
        transaction_date=claim.transaction_date,
        transaction_price_aed=claim.transaction_price_aed,
        ownership_status=claim.ownership_status,  # type: ignore[arg-type]
        confidence_score=claim.confidence_score,
        match_reason=claim.match_reason,
        lead_id=claim.lead_id,
        unit_candidate_key=claim.unit_candidate_key,
        latest_sale_date=claim.latest_sale_date,
        latest_sale_price_aed=claim.latest_sale_price_aed,
        later_sale_date=claim.later_sale_date,
        lease_end=claim.lease_end,
        annual_rent_aed=claim.annual_rent_aed,
        owner_key=_claim_owner_key(claim),
        report_status="mortgage" if is_mortgage_review(claim) else None,
        duplicate_count=_claim_duplicate_count(grouped),
        source_rows=_claim_source_rows(grouped),
    )


def _list_summary(
    contact_list: CrmContactList,
    claims: list[CrmPropertyContactClaim],
) -> CrmContactListSummary:
    if contact_list.id is None:
        raise HTTPException(status_code=500, detail="CRM contact list is missing an id")
    unique_claims = [max(group, key=_claim_preference) for group in _dedupe_claims(claims)]
    return CrmContactListSummary(
        id=contact_list.id,
        source_import_id=contact_list.source_import_id,
        name=contact_list.name,
        description=contact_list.description,
        total_contacts=len(unique_claims),
        contact_ready=sum(
            bool(claim.lead_id)
            and bool(claim.phone_primary or claim.phone_alternate or claim.email_primary)
            and claim.ownership_status in {"verified_current_owner", "probable_current_owner"}
            for claim in unique_claims
        ),
        needs_review=sum(
            claim.ownership_status
            in {
                "unit_linked_unverified",
                "conflicting_claim",
                "insufficient_property_data",
                "registry_not_observed",
            }
            for claim in unique_claims
        ),
        former_owners=sum(claim.ownership_status == "former_owner" for claim in unique_claims),
        created_at=contact_list.created_at,
        updated_at=contact_list.updated_at,
    )


async def list_contact_lists(
    db: AsyncSession,
    *,
    organization_id: str,
) -> list[CrmContactListSummary]:
    list_result = await owner_repository.list_contact_lists(db, organization_id)
    contact_lists = list(list_result)
    if not contact_lists:
        return []
    ids = [item.id for item in contact_lists if item.id is not None]
    claim_result = await owner_repository.list_claims_for_lists(db, ids, organization_id)
    grouped: dict[int, list[CrmPropertyContactClaim]] = {}
    for claim in claim_result:
        grouped.setdefault(claim.list_id, []).append(claim)
    return [_list_summary(item, grouped.get(item.id or 0, [])) for item in contact_lists]


async def get_contact_list(
    db: AsyncSession,
    *,
    list_id: int,
    organization_id: str,
    status: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> CrmContactListDetail:
    list_result = await owner_repository.get_contact_list(db, list_id, organization_id)
    contact_list = list_result
    if not contact_list:
        raise HTTPException(status_code=404, detail="CRM contact list not found")
    all_result = await owner_repository.find_contact_list_claims(
        db, list_id, organization_id, search
    )
    matching_groups = _dedupe_claims(list(all_result))
    if status:
        matching_groups = [
            group
            for group in matching_groups
            if matches_report_status(max(group, key=_claim_preference), status)
        ]
    visible = matching_groups[offset : offset + limit]
    all_list_result = await owner_repository.list_claims_for_list(db, list_id, organization_id)
    all_list_claims = list(all_list_result)
    return CrmContactListDetail(
        list=_list_summary(contact_list, all_list_claims),
        contacts=[_claim_response(max(group, key=_claim_preference), group) for group in visible],
        total=len(matching_groups),
        limit=limit,
        offset=offset,
    )


async def list_lead_imported_contacts(
    db: AsyncSession,
    *,
    lead_id: str,
    organization_id: str,
) -> list[CrmLeadImportedContact]:
    result = await owner_repository.list_lead_claims_with_lists(db, lead_id, organization_id)
    rows = list(result)
    grouped: dict[tuple[str, str], list[tuple[CrmPropertyContactClaim, CrmContactList]]] = {}
    for claim, contact_list in rows:
        grouped.setdefault(
            (_claim_owner_key(claim), _claim_property_key(claim)),
            [],
        ).append((claim, contact_list))

    # A lead drawer only needs portfolio counts for the owners attached to that
    # lead. Loading and de-duplicating every imported claim made this endpoint
    # progressively slower as an organization's owner registry grew.
    owner_keys = {_claim_owner_key(claim) for claim, _ in rows}
    property_counts: dict[str, int] = {}
    if owner_keys:
        property_count_result = await owner_repository.count_owner_properties(
            db, organization_id, owner_keys
        )
        property_counts = {
            str(owner_key): int(property_count)
            for owner_key, property_count in property_count_result
        }

    contacts: list[CrmLeadImportedContact] = []
    for group_rows in grouped.values():
        claim_group = [claim for claim, _ in group_rows]
        claim = max(claim_group, key=_claim_preference)
        contact_list = next(
            contact_list for candidate, contact_list in group_rows if candidate.id == claim.id
        )
        if claim.id is None or contact_list.id is None:
            continue
        owner_key = _claim_owner_key(claim)
        contacts.append(
            CrmLeadImportedContact(
                claim_id=claim.id,
                list_id=contact_list.id,
                list_name=contact_list.name,
                contact_name=claim.contact_name,
                phone_primary=claim.phone_primary,
                phone_alternate=claim.phone_alternate,
                email_primary=claim.email_primary,
                ownership_status=claim.ownership_status,  # type: ignore[arg-type]
                confidence_score=claim.confidence_score,
                match_reason=claim.match_reason,
                source_sheet=claim.source_sheet,
                imported_at=claim.created_at,
                owner_key=owner_key,
                duplicate_count=_claim_duplicate_count(claim_group),
                property_count=property_counts.get(owner_key, 1),
                source_lists=sorted({item.name for _, item in group_rows}),
            )
        )
    contacts.sort(key=lambda item: (item.confidence_score, item.property_count), reverse=True)
    return contacts


def _group_claim_rows(
    rows: list[tuple[CrmPropertyContactClaim, CrmContactList]],
) -> dict[str, list[list[tuple[CrmPropertyContactClaim, CrmContactList]]]]:
    properties: dict[
        tuple[str, str],
        list[tuple[CrmPropertyContactClaim, CrmContactList]],
    ] = {}
    for claim, contact_list in rows:
        properties.setdefault(
            (_claim_owner_key(claim), _claim_property_key(claim)),
            [],
        ).append((claim, contact_list))
    owners: dict[str, list[list[tuple[CrmPropertyContactClaim, CrmContactList]]]] = {}
    for (owner_key, _), property_rows in properties.items():
        owners.setdefault(owner_key, []).append(property_rows)
    return owners


def _is_rental_ready_claim(claim: CrmPropertyContactClaim) -> bool:
    return bool(claim.lead_id) and claim.ownership_status in {
        "verified_current_owner",
        "probable_current_owner",
    }


def _property_from_rows(
    rows: list[tuple[CrmPropertyContactClaim, CrmContactList]],
) -> CrmOwnerProperty:
    claims = [claim for claim, _ in rows]
    best = max(claims, key=_claim_preference)
    return CrmOwnerProperty(
        property_key=_claim_property_key(best),
        area_name=best.area_name,
        project_name=best.project_name,
        building_name=best.building_name,
        unit_number=best.unit_number,
        property_type=best.property_type,
        ownership_status=best.ownership_status,  # type: ignore[arg-type]
        confidence_score=best.confidence_score,
        match_reason=best.match_reason,
        lead_id=best.lead_id,
        unit_candidate_key=best.unit_candidate_key,
        imported_transaction_date=best.transaction_date,
        imported_transaction_price_aed=best.transaction_price_aed,
        imported_property_type=best.property_type,
        imported_party_role=best.party_role,
        latest_sale_date=best.latest_sale_date,
        latest_sale_price_aed=best.latest_sale_price_aed,
        registry_sale_date=best.latest_sale_date,
        registry_sale_price_aed=best.latest_sale_price_aed,
        later_sale_date=best.later_sale_date,
        lease_end=best.lease_end,
        annual_rent_aed=best.annual_rent_aed,
        duplicate_count=_claim_duplicate_count(claims),
        source_lists=sorted({contact_list.name for _, contact_list in rows}),
        source_rows=_claim_source_rows(claims),
    )


def _owner_profile(
    owner_key: str,
    property_groups: list[list[tuple[CrmPropertyContactClaim, CrmContactList]]],
) -> CrmOwnerProfile:
    claims = [claim for group in property_groups for claim, _ in group]
    best = max(claims, key=_claim_preference)
    properties = [_property_from_rows(group) for group in property_groups]
    properties.sort(
        key=lambda item: (
            1 if item.lead_id else 0,
            item.confidence_score,
            -(item.lease_end.toordinal() if item.lease_end else 0),
        ),
        reverse=True,
    )
    phones = list(
        dict.fromkeys(
            value
            for claim in claims
            for value in (claim.phone_primary, claim.phone_alternate)
            if value
        )
    )
    emails = list(dict.fromkeys(claim.email_primary for claim in claims if claim.email_primary))
    rental_properties = [item for item in properties if item.lead_id]
    contact_ready = [
        item
        for item in properties
        if item.lead_id
        and item.ownership_status in {"verified_current_owner", "probable_current_owner"}
    ]
    lease_dates = [item.lease_end for item in rental_properties if item.lease_end]
    return CrmOwnerProfile(
        owner_key=owner_key,
        contact_name=best.contact_name,
        phone_primary=phones[0] if phones else None,
        phone_alternate=phones[1] if len(phones) > 1 else None,
        email_primary=emails[0] if emails else None,
        highest_ownership_status=best.ownership_status,  # type: ignore[arg-type]
        highest_confidence_score=best.confidence_score,
        property_count=len(properties),
        rental_linked_count=len(rental_properties),
        contact_ready_count=len(contact_ready),
        annual_rent_aed=sum(item.annual_rent_aed or 0 for item in rental_properties),
        next_lease_end=min(lease_dates) if lease_dates else None,
        properties=properties,
    )


def _profile_matches_scope(profile: CrmOwnerProfile, scope: str) -> bool:
    ready_properties = [
        item
        for item in profile.properties
        if item.lead_id
        and item.ownership_status in {"verified_current_owner", "probable_current_owner"}
    ]
    if scope == "rental_ready":
        return bool(ready_properties)
    if scope == "registry":
        return len(ready_properties) < len(profile.properties)
    return True


def _profile_for_scope(profile: CrmOwnerProfile, scope: str) -> CrmOwnerProfile:
    if scope == "all":
        return profile
    properties = [
        item
        for item in profile.properties
        if (
            item.lead_id
            and item.ownership_status in {"verified_current_owner", "probable_current_owner"}
        )
        == (scope == "rental_ready")
    ]
    if not properties:
        return profile
    best = max(
        properties,
        key=lambda item: (
            _OWNERSHIP_PRIORITY.get(item.ownership_status, 0),
            item.confidence_score,
        ),
    )
    rental_properties = [item for item in properties if item.lead_id]
    lease_dates = [item.lease_end for item in rental_properties if item.lease_end]
    return profile.model_copy(
        update={
            "highest_ownership_status": best.ownership_status,
            "highest_confidence_score": best.confidence_score,
            "property_count": len(properties),
            "rental_linked_count": len(rental_properties),
            "contact_ready_count": sum(
                bool(item.lead_id)
                and item.ownership_status in {"verified_current_owner", "probable_current_owner"}
                for item in properties
            ),
            "annual_rent_aed": sum(item.annual_rent_aed or 0 for item in rental_properties),
            "next_lease_end": min(lease_dates) if lease_dates else None,
            "properties": properties,
        }
    )


async def _all_owner_profiles(
    db: AsyncSession,
    *,
    organization_id: str,
) -> list[CrmOwnerProfile]:
    result = await owner_repository.list_claims_with_lists(db, organization_id)
    owners = _group_claim_rows(list(result))
    return [
        _owner_profile(owner_key, property_groups) for owner_key, property_groups in owners.items()
    ]


def _owner_registry_summary(
    profiles: list[CrmOwnerProfile],
) -> CrmOwnerRegistrySummary:
    return CrmOwnerRegistrySummary(
        rental_owner_profiles=sum(
            any(
                item.lead_id
                and item.ownership_status in {"verified_current_owner", "probable_current_owner"}
                for item in profile.properties
            )
            for profile in profiles
        ),
        registry_owner_profiles=sum(
            any(
                not (
                    item.lead_id
                    and item.ownership_status
                    in {"verified_current_owner", "probable_current_owner"}
                )
                and item.ownership_status != "former_owner"
                for item in profile.properties
            )
            for profile in profiles
        ),
        prior_owner_profiles=sum(
            any(item.ownership_status == "former_owner" for item in profile.properties)
            for profile in profiles
        ),
        needs_review_profiles=sum(
            any(
                item.ownership_status
                in {
                    "unit_linked_unverified",
                    "conflicting_claim",
                    "insufficient_property_data",
                    "registry_not_observed",
                    "unmatched",
                }
                for item in profile.properties
            )
            for profile in profiles
        ),
        multi_property_owners=sum(profile.property_count > 1 for profile in profiles),
    )


async def _owner_registry_summary_sql(
    db: AsyncSession,
    *,
    organization_id: str,
    list_id: int | None,
) -> CrmOwnerRegistrySummary:
    cache_key = (organization_id, list_id)
    cached = _owner_summary_cache.get(cache_key)
    if cached and monotonic() - cached[0] < _OWNER_SUMMARY_CACHE_TTL_SECONDS:
        return cached[1]
    row = await owner_repository.fetch_owner_registry_totals(db, list_id, organization_id)
    summary = CrmOwnerRegistrySummary(
        rental_owner_profiles=int(row["rental_owner_profiles"] or 0),
        registry_owner_profiles=int(row["registry_owner_profiles"] or 0),
        prior_owner_profiles=int(row["prior_owner_profiles"] or 0),
        needs_review_profiles=int(row["needs_review_profiles"] or 0),
        multi_property_owners=int(row["multi_property_owners"] or 0),
    )
    _owner_summary_cache[cache_key] = (monotonic(), summary)
    return summary


def _sql_owner_property(row: dict[str, Any]) -> CrmOwnerProperty:
    return CrmOwnerProperty(
        property_key=str(row["property_key_sql"]),
        area_name=str(row["area_name"] or ""),
        project_name=str(row["project_name"] or ""),
        building_name=str(row["building_name"] or ""),
        unit_number=str(row["unit_number"] or ""),
        property_type=str(row["property_type"] or ""),
        ownership_status=str(row["ownership_status"]),  # type: ignore[arg-type]
        confidence_score=int(row["confidence_score"] or 0),
        match_reason=str(row["match_reason"] or ""),
        lead_id=str(row["lead_id"]) if row["lead_id"] else None,
        unit_candidate_key=(str(row["unit_candidate_key"]) if row["unit_candidate_key"] else None),
        imported_transaction_date=row["transaction_date"],
        imported_transaction_price_aed=(
            int(row["transaction_price_aed"]) if row["transaction_price_aed"] else None
        ),
        imported_property_type=str(row["property_type"] or ""),
        imported_party_role=str(row.get("party_role") or ""),
        latest_sale_date=row["latest_sale_date"],
        latest_sale_price_aed=(
            int(row["latest_sale_price_aed"]) if row["latest_sale_price_aed"] else None
        ),
        registry_sale_date=row["latest_sale_date"],
        registry_sale_price_aed=(
            int(row["latest_sale_price_aed"]) if row["latest_sale_price_aed"] else None
        ),
        later_sale_date=row["later_sale_date"],
        lease_end=row["lease_end"],
        annual_rent_aed=(int(row["annual_rent_aed"]) if row["annual_rent_aed"] else None),
        duplicate_count=int(row["duplicate_count"] or 1),
        source_lists=list(row["source_lists"] or []),
        source_rows=list(row["source_rows"] or []),
    )


def _sql_owner_profile(
    owner_key: str,
    rows: list[dict[str, Any]],
) -> CrmOwnerProfile:
    best = max(
        rows,
        key=lambda row: (
            int(row["status_rank"] or 0),
            int(row["confidence_score"] or 0),
        ),
    )
    properties = [_sql_owner_property(row) for row in rows]
    phones = list(
        dict.fromkeys(
            str(value)
            for row in rows
            for value in (row["phone_primary"], row["phone_alternate"])
            if value
        )
    )
    emails = list(dict.fromkeys(str(row["email_primary"]) for row in rows if row["email_primary"]))
    rental_properties = [item for item in properties if item.lead_id]
    contact_ready = [
        item
        for item in properties
        if item.lead_id
        and item.ownership_status in {"verified_current_owner", "probable_current_owner"}
    ]
    lease_dates = [item.lease_end for item in rental_properties if item.lease_end]
    return CrmOwnerProfile(
        owner_key=owner_key,
        contact_name=str(best["contact_name"]),
        phone_primary=phones[0] if phones else None,
        phone_alternate=phones[1] if len(phones) > 1 else None,
        email_primary=emails[0] if emails else None,
        highest_ownership_status=str(best["ownership_status"]),  # type: ignore[arg-type]
        highest_confidence_score=int(best["confidence_score"] or 0),
        property_count=len(properties),
        rental_linked_count=len(rental_properties),
        contact_ready_count=len(contact_ready),
        annual_rent_aed=sum(item.annual_rent_aed or 0 for item in rental_properties),
        next_lease_end=min(lease_dates) if lease_dates else None,
        properties=properties,
    )


async def get_owner_registry(
    db: AsyncSession,
    *,
    organization_id: str,
    list_id: int | None,
    scope: str,
    status: str | None,
    search: str | None,
    lease_window: str | None,
    portfolio_only: bool,
    min_rent_aed: int | None,
    max_rent_aed: int | None,
    min_value_aed: int | None,
    max_value_aed: int | None,
    limit: int,
    offset: int,
    min_properties: int | None = None,
    next_event_days: int | None = None,
) -> CrmOwnerRegistryResponse:
    summary = await _owner_registry_summary_sql(
        db,
        organization_id=organization_id,
        list_id=list_id,
    )
    owner_keys, total = await _owner_page_keys_sql(
        db,
        organization_id=organization_id,
        list_id=list_id,
        scope=scope,
        status=status,
        search=search,
        lease_window=lease_window,
        portfolio_only=portfolio_only,
        min_rent_aed=min_rent_aed,
        max_rent_aed=max_rent_aed,
        min_value_aed=min_value_aed,
        max_value_aed=max_value_aed,
        limit=limit,
        offset=offset,
        min_properties=min_properties,
        next_event_days=next_event_days,
    )
    property_rows = await _owner_properties_sql(
        db,
        organization_id=organization_id,
        list_id=list_id,
        owner_keys=owner_keys,
        scope=scope,
        status=status,
        search=search,
        lease_window=lease_window,
        min_rent_aed=min_rent_aed,
        max_rent_aed=max_rent_aed,
        min_value_aed=min_value_aed,
        max_value_aed=max_value_aed,
    )
    profiles = [
        _sql_owner_profile(owner_key, property_rows[owner_key])
        for owner_key in owner_keys
        if owner_key in property_rows
    ]
    return CrmOwnerRegistryResponse(
        scope=scope,  # type: ignore[arg-type]
        owners=profiles,
        total=total,
        limit=limit,
        offset=offset,
        summary=summary,
    )


def _owner_property_registry_score(
    owner_property: CrmOwnerProperty,
    row: dict[str, Any],
) -> int:
    owner_building = _usable_project_name(owner_property.building_name)
    owner_project = _usable_project_name(owner_property.project_name)
    owner_area = _usable_project_name(owner_property.area_name)
    sale_building = str(row.get("building_name") or "")
    sale_project = str(row.get("project_name") or "")
    sale_area = str(row.get("area_name") or "")
    combined_sale = " ".join(value for value in (sale_building, sale_project) if value)
    building_score = max(
        (_location_similarity(owner_building, value) for value in (sale_building, combined_sale)),
        default=0,
    )
    project_score = max(
        (
            _location_similarity(owner_project, value)
            for value in (sale_project, sale_building, combined_sale)
        ),
        default=0,
    )
    area_score = _location_similarity(owner_area, sale_area)
    if owner_building:
        return round(0.70 * building_score + 0.25 * project_score + 0.05 * area_score)
    if owner_project:
        return round(0.90 * project_score + 0.10 * area_score)
    return area_score


def _positive_float(value: Any) -> float | None:
    parsed = float(value or 0)
    return parsed if parsed > 0 else None


async def _enrich_owner_profile_registry(profile: CrmOwnerProfile) -> CrmOwnerProfile:
    """Attach authoritative DLD registry attributes to a clicked owner profile.

    Claims already retain the selected property's latest sale signature. Restricting
    the registry query to those signatures avoids scanning every Dubai property that
    happens to use a generic unit number such as 101 or 204.
    """
    eligible = [
        owner_property
        for owner_property in profile.properties
        if owner_property.unit_number
        and owner_property.latest_sale_date
        and owner_property.latest_sale_price_aed
    ]
    if not eligible:
        return profile
    signatures = {
        (
            owner_property.latest_sale_date,
            int(owner_property.latest_sale_price_aed or 0),
        )
        for owner_property in eligible
    }
    rows = await owner_queries.fetch_owner_property_signatures(
        {
            "dates": sorted({signature[0] for signature in signatures}),
            "prices": sorted({signature[1] for signature in signatures}),
        }
    )
    rows_by_signature: dict[tuple[date, int], list[dict[str, Any]]] = {}
    for row in rows:
        signature = (
            row["transaction_date"],
            int(row.get("sale_amount_aed") or 0),
        )
        if signature in signatures:
            rows_by_signature.setdefault(signature, []).append(row)

    for owner_property in eligible:
        signature = (
            owner_property.latest_sale_date,
            int(owner_property.latest_sale_price_aed or 0),
        )
        unit_key = _normalize_unit(owner_property.unit_number).lower()
        property_candidates: dict[tuple[str, str, str], dict[str, Any]] = {}
        for row in rows_by_signature.get(signature, []):
            if str(row.get("unit_key") or "") != unit_key:
                continue
            building_key = str(row.get("building_key") or "") or _location_key(
                str(row.get("building_name") or row.get("project_name") or "")
            )
            identity = (
                str(row.get("area_key") or ""),
                building_key,
                str(row.get("unit_key") or ""),
            )
            property_candidates.setdefault(identity, row)
        if not property_candidates:
            continue
        ranked = sorted(
            (
                (_owner_property_registry_score(owner_property, row), row)
                for row in property_candidates.values()
            ),
            key=lambda item: item[0],
            reverse=True,
        )
        score, selected = ranked[0]
        runner_up = ranked[1][0] if len(ranked) > 1 else 0
        if score < 55 or (len(ranked) > 1 and score - runner_up < 12):
            continue

        owner_property.registry_sale_date = selected["transaction_date"]
        owner_property.registry_sale_price_aed = int(selected.get("sale_amount_aed") or 0) or None
        owner_property.registry_property_type = (
            str(selected.get("property_type") or "").strip() or None
        )
        owner_property.registry_bedrooms = str(selected.get("bedrooms") or "").strip() or None
        owner_property.registry_size_sqft = _positive_float(selected.get("size_sqft"))
        owner_property.registry_built_up_area_sqft = _positive_float(
            selected.get("built_up_area_sqft")
        )
        owner_property.registry_price_per_sqft_aed = _positive_float(
            selected.get("price_per_sqft_aed")
        )
        owner_property.registry_market_status = (
            str(selected.get("market_status") or "").strip() or None
        )
        owner_property.registry_unit_number = str(selected.get("unit_number") or "").strip() or None
        owner_property.registry_building_name = (
            str(selected.get("building_name") or selected.get("project_name") or "").strip() or None
        )
        owner_property.registry_area_name = str(selected.get("area_name") or "").strip() or None
    return profile


async def get_owner_profile(
    db: AsyncSession,
    *,
    organization_id: str,
    owner_key: str,
) -> CrmOwnerProfile:
    property_rows = await _owner_properties_sql(
        db,
        organization_id=organization_id,
        list_id=None,
        owner_keys=[owner_key],
        scope="all",
    )
    rows = property_rows.get(owner_key)
    if not rows:
        raise HTTPException(status_code=404, detail="Owner profile not found")
    profile = _sql_owner_profile(owner_key, rows)
    try:
        return await _enrich_owner_profile_registry(profile)
    except Exception:
        logger.exception(
            "Owner profile registry enrichment failed",
            extra={"owner_key": owner_key},
        )
        return profile
