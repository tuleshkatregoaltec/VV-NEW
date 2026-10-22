from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm import inbound_repository, owner_queries
from app.crm.identity import _normalize_email, _normalize_phone
from app.crm.import_models import CrmPropertyContactClaim
from app.crm.inbound_models import (
    VITEVUE_LEASE_SOURCE,
    CrmInboundActivity,
    CrmInboundContact,
    CrmInboundEnquiry,
    CrmInboundNote,
    InboundActivityResponse,
    InboundContactResponse,
    InboundEnquiryCreate,
    InboundEnquiryListResponse,
    InboundEnquiryResponse,
    InboundEnquiryUpdate,
    InboundNoteResponse,
    InboundOwnershipEnrichmentResponse,
    InboundRegistryProfileResponse,
)
from app.crm.inbound_policy import (
    _FIRST_RESPONSE_MINUTES,
    _QUALIFIED_STATUSES,
    _amount,
    _belongs_in_inbox,
    _lead_priority,
    _locations,
    _qualification_complete,
    _sla_state,
    _text,
    _token,
    _utcnow,
)


async def _find_or_create_contact(
    db: AsyncSession,
    *,
    organization_id: str,
    full_name: str,
    phone: str | None,
    email: str | None,
    whatsapp: str | None,
    language: str | None,
    consent_status: str,
) -> CrmInboundContact:
    existing = await inbound_repository.find_contact(db, email, organization_id, phone)
    now = _utcnow()
    if existing:
        existing.full_name = full_name or existing.full_name
        existing.phone = phone or existing.phone
        existing.email = email or existing.email
        existing.whatsapp = whatsapp or existing.whatsapp
        existing.preferred_language = language or existing.preferred_language
        existing.updated_at = now
        inbound_repository.save(db, existing)
        return existing
    contact = CrmInboundContact(
        organization_id=organization_id,
        full_name=full_name or "Unnamed lead",
        phone=phone,
        email=email,
        whatsapp=whatsapp,
        preferred_language=language,
        consent_status=consent_status,
        created_at=now,
        updated_at=now,
    )
    inbound_repository.save(db, contact)
    await db.flush()
    return contact


def _claim_property_identity(claim: CrmPropertyContactClaim) -> str:
    return claim.property_key or "|".join(
        (
            _token(claim.building_name or claim.project_name),
            _token(claim.unit_number),
        )
    )


def _claim_observed_value(claim: CrmPropertyContactClaim) -> int:
    evidence = claim.evidence or {}
    candidates = (
        claim.latest_sale_price_aed,
        claim.transaction_price_aed,
        evidence.get("latest_sale_price_aed"),
        evidence.get("sale_amount_aed"),
        evidence.get("transaction_price_aed"),
        evidence.get("purchase_price_aed"),
    )
    return next((amount for value in candidates if (amount := _amount(value))), 0)


def _ownership_enrichment(
    matches: list[tuple[CrmPropertyContactClaim, str]],
) -> InboundOwnershipEnrichmentResponse:
    if not matches:
        return InboundOwnershipEnrichmentResponse()
    basis_order = {"phone": 3, "email": 2, "name": 1}
    match_basis = max((basis for _, basis in matches), key=basis_order.__getitem__)
    best_by_property: dict[str, CrmPropertyContactClaim] = {}
    status_order = {
        "verified_current_owner": 5,
        "probable_current_owner": 4,
        "former_owner": 3,
        "unit_linked_unverified": 2,
        "conflicting_claim": 1,
        "registry_not_observed": 0,
        "insufficient_property_data": 0,
        "unmatched": 0,
    }
    for claim, _ in matches:
        property_identity = _claim_property_identity(claim)
        current = best_by_property.get(property_identity)
        if current is None or (
            status_order.get(claim.ownership_status, 0),
            claim.confidence_score,
        ) > (status_order.get(current.ownership_status, 0), current.confidence_score):
            best_by_property[property_identity] = claim
    claims = list(best_by_property.values())
    current = [
        claim
        for claim in claims
        if claim.ownership_status in {"verified_current_owner", "probable_current_owner"}
    ]
    former = [claim for claim in claims if claim.ownership_status == "former_owner"]
    rental = [claim for claim in current if claim.lease_end or claim.lead_id]
    portfolio_value = sum(_claim_observed_value(claim) for claim in claims)
    reasons = [
        "Phone matched owner records"
        if match_basis == "phone"
        else "Email matched owner records"
        if match_basis == "email"
        else "Name matched owner records; verify identity"
    ]
    if current:
        reasons.append(
            f"{len(current)} current propert{'y' if len(current) == 1 else 'ies'} observed"
        )
    if former:
        reasons.append(f"{len(former)} prior propert{'y' if len(former) == 1 else 'ies'} observed")
    if rental:
        reasons.append(f"{len(rental)} rental propert{'y' if len(rental) == 1 else 'ies'} observed")
    if len(claims) >= 2:
        reasons.append(f"Known across {len(claims)} properties")
    if portfolio_value:
        reasons.append(f"AED {portfolio_value:,} in observed property-value evidence")
    return InboundOwnershipEnrichmentResponse(
        matched=True,
        match_basis=match_basis,  # type: ignore[arg-type]
        verification="contact_exact" if match_basis in {"phone", "email"} else "name_only",
        current_owner=bool(current),
        former_owner=bool(former),
        rental_owner=bool(rental),
        multiple_property_owner=len(claims) >= 2,
        current_property_count=len(current),
        prior_property_count=len(former),
        rental_property_count=len(rental),
        known_property_count=len(claims),
        observed_portfolio_value_aed=portfolio_value,
        reasons=reasons,
    )


async def _bulk_ownership_enrichments(
    db: AsyncSession,
    *,
    organization_id: str,
    contacts: list[CrmInboundContact],
) -> dict[int, InboundOwnershipEnrichmentResponse]:
    if not contacts:
        return {}
    name_to_ids: dict[str, set[int]] = {}
    phone_to_ids: dict[str, set[int]] = {}
    email_to_ids: dict[str, set[int]] = {}
    for contact in contacts:
        contact_id = contact.id or 0
        name = contact.full_name.strip().casefold()
        if name:
            name_to_ids.setdefault(name, set()).add(contact_id)
        if contact.phone:
            phone_to_ids.setdefault(contact.phone, set()).add(contact_id)
        if contact.email:
            email_to_ids.setdefault(contact.email.casefold(), set()).add(contact_id)
    result = await inbound_repository.find_identity_claims(
        db, email_to_ids, name_to_ids, organization_id, phone_to_ids
    )
    matches: dict[int, list[tuple[CrmPropertyContactClaim, str]]] = {}
    for claim in result:
        claim_phone_values = {claim.phone_primary, claim.phone_alternate} - {None, ""}
        phone_ids = {
            contact_id
            for value in claim_phone_values
            for contact_id in phone_to_ids.get(str(value), set())
        }
        email_ids = email_to_ids.get((claim.email_primary or "").casefold(), set())
        name_ids = name_to_ids.get(claim.contact_name.strip().casefold(), set())
        for contact_id in phone_ids | email_ids | name_ids:
            basis = (
                "phone"
                if contact_id in phone_ids
                else "email"
                if contact_id in email_ids
                else "name"
            )
            matches.setdefault(contact_id, []).append((claim, basis))
    return {
        contact_id: _ownership_enrichment(contact_matches)
        for contact_id, contact_matches in matches.items()
    }


async def _registry_transaction_evidence(
    claims: list[CrmPropertyContactClaim],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Hydrate owner-import claims with transaction and lease evidence in one batch."""
    buildings = sorted(
        {claim.building_name.strip().casefold() for claim in claims if claim.building_name.strip()}
    )
    units = sorted({claim.unit_number.strip().casefold() for claim in claims if claim.unit_number})
    candidate_keys = sorted(
        {claim.unit_candidate_key for claim in claims if claim.unit_candidate_key}
    )
    sales: dict[str, dict[str, Any]] = {}
    leases: dict[str, dict[str, Any]] = {}
    if buildings and units:
        try:
            rows = await owner_queries.fetch_registry_sales_evidence(
                {"buildings": buildings, "units": units}
            )
            for row in rows:
                key = f"{_token(row.get('building_name'))}|{_token(row.get('unit_number'))}"
                sales.setdefault(key, row)
        except Exception:
            sales = {}
    if candidate_keys:
        try:
            rows = await owner_queries.fetch_registry_lease_evidence(
                {"candidate_keys": candidate_keys}
            )
            leases = {str(row["unit_candidate_key"]): row for row in rows}
        except Exception:
            leases = {}
    return sales, leases


async def create_inbound_enquiry(
    db: AsyncSession,
    *,
    payload: InboundEnquiryCreate,
    user_id: str,
    organization_id: str,
) -> InboundEnquiryResponse:
    contact = await _find_or_create_contact(
        db,
        organization_id=organization_id,
        full_name=payload.full_name.strip(),
        phone=_normalize_phone(payload.phone),
        email=_normalize_email(payload.email),
        whatsapp=_normalize_phone(payload.whatsapp),
        language=None,
        consent_status=payload.consent_status,
    )
    now = _utcnow()
    record_kind = (
        "opportunity"
        if payload.record_kind == "opportunity" and payload.intent != "unknown"
        else "unqualified_contact"
    )
    enquiry = CrmInboundEnquiry(
        organization_id=organization_id,
        contact_id=contact.id or 0,
        created_by_user_id=user_id,
        assigned_user_id=payload.assigned_user_id or user_id,
        record_kind=record_kind,
        intent=payload.intent if record_kind == "opportunity" else "unknown",
        status="new",
        source=payload.source,
        campaign=payload.campaign,
        enquiry_date=now,
        criteria=payload.criteria,
        strict_fields=payload.strict_fields,
        raw_notes=payload.notes,
        observed_data={
            "intent": payload.intent if payload.intent != "unknown" else None,
            "message": payload.notes,
            "criteria": payload.criteria,
        },
        confirmed_data=(
            {"intent": payload.intent, "criteria": payload.criteria}
            if record_kind == "opportunity"
            else {}
        ),
        qualification={},
        extraction_confidence=1,
        first_response_due_at=now + timedelta(minutes=_FIRST_RESPONSE_MINUTES),
        created_at=now,
        updated_at=now,
    )
    inbound_repository.save(db, enquiry)
    await db.flush()
    inbound_repository.save(
        db,
        CrmInboundActivity(
            enquiry_id=enquiry.id or 0,
            organization_id=organization_id,
            user_id=user_id,
            activity_type="created",
            body="Manually added to the lead inbox",
            metadata_json={"source": payload.source},
            created_at=now,
        ),
    )
    await db.commit()
    await db.refresh(enquiry)
    return await _enquiry_response(db, enquiry, contact=contact, include_registry_profiles=True)


async def open_vitevue_lease_enquiry(
    db: AsyncSession,
    *,
    organization_id: str,
    user_id: str,
    unit_key: str,
    owner_name: str,
    owner_phone: str | None,
    owner_email: str | None,
    criteria: dict[str, Any],
    lease: dict[str, Any],
) -> InboundEnquiryResponse:
    """The agency's one shared lead for a unit Vitevue supplied, created on first open.

    It starts undecided (seller or landlord), assigned to nobody, with no reply deadline: it was
    not an enquiry. The owner is blank unless an owner import already identified them.
    """
    existing = await inbound_repository.list_vitevue_lease_enquiries(
        db, organization_id, [unit_key]
    )
    if existing:
        return await _enquiry_response(
            db, existing[0], include_registry_profiles=True, include_history=True
        )
    now = _utcnow()
    contact = CrmInboundContact(
        organization_id=organization_id,
        full_name=owner_name,
        phone=_normalize_phone(owner_phone),
        email=_normalize_email(owner_email),
        created_at=now,
        updated_at=now,
    )
    inbound_repository.save(db, contact)
    await db.flush()
    enquiry = CrmInboundEnquiry(
        organization_id=organization_id,
        contact_id=contact.id or 0,
        created_by_user_id=user_id,
        assigned_user_id=None,
        record_kind="unqualified_contact",
        intent="unknown",
        status="new",
        source=VITEVUE_LEASE_SOURCE,
        source_lead_id=unit_key,
        enquiry_date=now,
        criteria=criteria,
        strict_fields=[],
        observed_data={"vitevue_lease": lease},
        confirmed_data={},
        qualification={},
        extraction_confidence=1,
        created_at=now,
        updated_at=now,
    )
    inbound_repository.save(db, enquiry)
    await db.flush()
    inbound_repository.save(
        db,
        CrmInboundActivity(
            enquiry_id=enquiry.id or 0,
            organization_id=organization_id,
            user_id=user_id,
            activity_type="created",
            body="Opened from a Vitevue lease lead",
            metadata_json={"source": VITEVUE_LEASE_SOURCE, "lease_id": lease.get("lease_id")},
            created_at=now,
        ),
    )
    try:
        await db.commit()
    except IntegrityError:
        # Another agent opened the same unit a moment earlier: use that lead.
        await db.rollback()
        existing = await inbound_repository.list_vitevue_lease_enquiries(
            db, organization_id, [unit_key]
        )
        return await _enquiry_response(
            db, existing[0], include_registry_profiles=True, include_history=True
        )
    await db.refresh(enquiry)
    return await _enquiry_response(
        db, enquiry, contact=contact, include_registry_profiles=True, include_history=True
    )


async def _registry_profiles(
    db: AsyncSession,
    *,
    contact: CrmInboundContact,
    criteria: dict[str, Any],
) -> list[InboundRegistryProfileResponse]:
    claims = await inbound_repository.find_contact_claims(db, contact)
    sale_evidence, lease_evidence = await _registry_transaction_evidence(claims)
    building = _token(criteria.get("building"))
    unit_number = _token(criteria.get("unit_number"))
    location_tokens = [_token(item) for item in _locations(criteria)]
    profiles: list[InboundRegistryProfileResponse] = []
    seen: set[tuple[str, str]] = set()
    for claim in claims:
        property_identity = (claim.owner_key, claim.property_key)
        if property_identity in seen:
            continue
        seen.add(property_identity)
        phone_match = bool(
            contact.phone and contact.phone in {claim.phone_primary, claim.phone_alternate}
        )
        email_match = bool(
            contact.email
            and claim.email_primary
            and contact.email.casefold() == claim.email_primary.casefold()
        )
        claim_building = _token(claim.building_name or claim.project_name)
        exact_property = bool(
            building
            and unit_number
            and building in claim_building
            and unit_number == _token(claim.unit_number)
        )
        location_match = bool(
            location_tokens
            and any(
                location
                and location
                in _token(f"{claim.area_name} {claim.project_name} {claim.building_name}")
                for location in location_tokens
            )
        )
        if phone_match or email_match:
            identity_strength = "exact"
            identity_reason = "Contact detail matches the owner registry"
        elif exact_property:
            identity_strength = "exact"
            identity_reason = "Name, building, and unit match the owner registry"
        elif building and building in claim_building:
            identity_strength = "possible"
            identity_reason = "Name and building match; unit confirmation is still required"
        elif location_match:
            identity_strength = "possible"
            identity_reason = "Name and stated location match a registry profile"
        else:
            identity_strength = "weak"
            identity_reason = "Name-only registry match; verify identity before using it"
        sale = sale_evidence.get(f"{_token(claim.building_name)}|{_token(claim.unit_number)}", {})
        lease = lease_evidence.get(claim.unit_candidate_key or "", {})
        sale_date = sale.get("transaction_date")
        lease_start = lease.get("lease_start")
        lease_end = lease.get("lease_end") or claim.lease_end
        profiles.append(
            InboundRegistryProfileResponse(
                owner_key=claim.owner_key,
                contact_name=claim.contact_name,
                ownership_status=claim.ownership_status,
                identity_strength=identity_strength,  # type: ignore[arg-type]
                identity_reason=identity_reason,
                area_name=claim.area_name,
                project_name=claim.project_name,
                building_name=claim.building_name,
                unit_number=claim.unit_number,
                property_type=claim.property_type,
                transaction_date=claim.transaction_date,
                transaction_price_aed=claim.transaction_price_aed,
                latest_sale_date=claim.latest_sale_date
                or (sale_date if isinstance(sale_date, date) else None),
                latest_sale_price_aed=claim.latest_sale_price_aed
                or _amount(sale.get("sale_amount_aed")),
                later_sale_date=claim.later_sale_date,
                ltv_pct=float(sale["ltv_pct"]) if sale.get("ltv_pct") is not None else None,
                seller_type=_text(sale.get("seller_type")) or None,
                lease_start=lease_start if isinstance(lease_start, date) else None,
                lease_end=lease_end if isinstance(lease_end, date) else None,
                annual_rent_aed=_amount(lease.get("annual_rent_aed")) or claim.annual_rent_aed,
                contract_state=_text(lease.get("contract_state")) or None,
                lead_id=claim.lead_id,
            )
        )
    strength_order = {"exact": 3, "possible": 2, "weak": 1}
    profiles.sort(
        key=lambda item: (
            strength_order[item.identity_strength],
            item.latest_sale_date or date.min,
        ),
        reverse=True,
    )
    return profiles[:10]


def _contact_response(
    contact: CrmInboundContact,
    registry_profiles: list[InboundRegistryProfileResponse] | None = None,
) -> InboundContactResponse:
    return InboundContactResponse(
        id=contact.id or 0,
        full_name=contact.full_name,
        phone=contact.phone,
        email=contact.email,
        whatsapp=contact.whatsapp,
        preferred_language=contact.preferred_language,
        preferred_channel=contact.preferred_channel,
        consent_status=contact.consent_status,
        do_not_contact=contact.do_not_contact,
        registry_profiles=registry_profiles or [],
    )


async def _enquiry_response(
    db: AsyncSession,
    enquiry: CrmInboundEnquiry,
    *,
    contact: CrmInboundContact | None = None,
    include_registry_profiles: bool = False,
    include_history: bool = False,
    match_counts: tuple[int, int] | None = None,
    ownership_enrichment: InboundOwnershipEnrichmentResponse | None = None,
) -> InboundEnquiryResponse:
    contact = contact or await inbound_repository.get_crm_inbound_contact(db, enquiry.contact_id)
    if not contact or contact.organization_id != enquiry.organization_id:
        raise HTTPException(status_code=500, detail="Inbound contact is missing")
    notes = []
    activities = []
    if include_history:
        notes_result = await inbound_repository.list_enquiry_notes(db, enquiry)
        notes = list(notes_result)
        activities_result = await inbound_repository.list_enquiry_activities(db, enquiry)
        activities = list(activities_result)
    if match_counts is None:
        matches_result = await inbound_repository.list_enquiry_matches(db, enquiry)
        matches = matches_result
        match_counts = (
            len([item for item in matches if item.state not in {"dismissed", "stale"}]),
            len([item for item in matches if item.state == "saved"]),
        )
    registry_profiles = (
        await _registry_profiles(db, contact=contact, criteria=enquiry.criteria)
        if include_registry_profiles
        else []
    )
    if ownership_enrichment is None:
        ownership_enrichment = (
            await _bulk_ownership_enrichments(
                db,
                organization_id=enquiry.organization_id,
                contacts=[contact],
            )
        ).get(contact.id or 0, InboundOwnershipEnrichmentResponse())
    return InboundEnquiryResponse(
        id=enquiry.id or 0,
        contact=_contact_response(contact, registry_profiles),
        record_kind=enquiry.record_kind,  # type: ignore[arg-type]
        intent=enquiry.intent,  # type: ignore[arg-type]
        status=enquiry.status,  # type: ignore[arg-type]
        assigned_user_id=enquiry.assigned_user_id,
        source=enquiry.source,
        campaign=enquiry.campaign,
        source_import_id=enquiry.source_import_id,
        enquiry_date=enquiry.enquiry_date,
        criteria=enquiry.criteria,
        strict_fields=enquiry.strict_fields,
        raw_notes=enquiry.raw_notes,
        observed_data=enquiry.observed_data,
        inferred_data=enquiry.inferred_data,
        confirmed_data=enquiry.confirmed_data,
        qualification=enquiry.qualification,
        extraction_confidence=enquiry.extraction_confidence,
        review_reasons=enquiry.review_reasons,
        next_follow_up=enquiry.next_follow_up,
        first_response_due_at=enquiry.first_response_due_at,
        first_contact_at=enquiry.first_contact_at,
        last_contact_at=enquiry.last_contact_at,
        contact_attempt_count=enquiry.contact_attempt_count,
        lost_reason=enquiry.lost_reason,
        sla_state=_sla_state(enquiry),  # type: ignore[arg-type]
        qualification_complete=_qualification_complete(enquiry),
        ownership_enrichment=ownership_enrichment,
        priority=_lead_priority(enquiry, ownership_enrichment),
        tags=enquiry.tags,
        notes=[
            InboundNoteResponse(
                id=note.id or 0, body=note.body, user_id=note.user_id, created_at=note.created_at
            )
            for note in notes
        ],
        activities=[
            InboundActivityResponse(
                id=activity.id or 0,
                activity_type=activity.activity_type,
                body=activity.body,
                user_id=activity.user_id,
                metadata=activity.metadata_json,
                created_at=activity.created_at,
            )
            for activity in activities
        ],
        match_count=match_counts[0],
        saved_match_count=match_counts[1],
        created_at=enquiry.created_at,
        updated_at=enquiry.updated_at,
    )


async def list_inbound_enquiries(
    db: AsyncSession,
    *,
    organization_id: str,
    intent_group: str | None,
    workspace: str | None,
    status: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> InboundEnquiryListResponse:
    result = await inbound_repository.list_enquiries(db, organization_id)
    enquiries = list(result)
    all_enquiries = enquiries[:]
    all_contacts: dict[int, CrmInboundContact] = {}
    if all_enquiries:
        contacts_result = await inbound_repository.list_enquiry_contacts(db, all_enquiries)
        all_contacts = {item.id or 0: item for item in contacts_result}
    all_enrichments = await _bulk_ownership_enrichments(
        db,
        organization_id=organization_id,
        contacts=list(all_contacts.values()),
    )
    all_priorities = {
        item.id or 0: _lead_priority(
            item,
            all_enrichments.get(item.contact_id, InboundOwnershipEnrichmentResponse()),
        )
        for item in all_enquiries
    }
    if intent_group == "demand":
        enquiries = [item for item in enquiries if item.intent in {"buy", "rent"}]
    elif intent_group == "instructions":
        enquiries = [item for item in enquiries if item.intent in {"sell", "let"}]
    if workspace == "inbox":
        enquiries = [item for item in enquiries if _belongs_in_inbox(item)]
    elif workspace == "unqualified":
        enquiries = [item for item in enquiries if item.record_kind == "unqualified_contact"]
    elif workspace == "qualified":
        enquiries = [item for item in enquiries if item.status in _QUALIFIED_STATUSES]
    elif workspace == "closed":
        enquiries = [
            item for item in enquiries if item.status in {"nurture", "closed_lost", "disqualified"}
        ]
    if status:
        enquiries = [item for item in enquiries if item.status == status]
    contacts = {
        contact_id: contact
        for contact_id, contact in all_contacts.items()
        if contact_id in {item.contact_id for item in enquiries}
    }
    if search:
        term = search.casefold()
        enquiries = [
            item
            for item in enquiries
            if term
            in contacts.get(
                item.contact_id,
                CrmInboundContact(
                    organization_id="", full_name="", created_at=_utcnow(), updated_at=_utcnow()
                ),
            ).full_name.casefold()
            or term in json.dumps(item.criteria).casefold()
            or term in (item.campaign or "").casefold()
        ]
    enrichments = {
        contact_id: enrichment
        for contact_id, enrichment in all_enrichments.items()
        if contact_id in {item.contact_id for item in enquiries}
    }
    priorities = {
        item.id or 0: _lead_priority(
            item,
            enrichments.get(item.contact_id, InboundOwnershipEnrichmentResponse()),
        )
        for item in enquiries
    }
    enquiries.sort(
        key=lambda item: (
            priorities[item.id or 0].score,
            item.enquiry_date,
        ),
        reverse=True,
    )
    total = len(enquiries)
    page = enquiries[offset : offset + limit]
    count_map: dict[int, tuple[int, int]] = {}
    if page:
        counts_result = await inbound_repository.count_matches_by_state(db, page)
        aggregate: dict[int, list[int]] = {}
        for enquiry_id, match_state, count in counts_result:
            values = aggregate.setdefault(enquiry_id, [0, 0])
            if match_state not in {"dismissed", "stale"}:
                values[0] += count
            if match_state == "saved":
                values[1] += count
        count_map = {key: (value[0], value[1]) for key, value in aggregate.items()}
    responses = [
        await _enquiry_response(
            db,
            item,
            contact=contacts.get(item.contact_id),
            match_counts=count_map.get(item.id or 0, (0, 0)),
            ownership_enrichment=enrichments.get(
                item.contact_id, InboundOwnershipEnrichmentResponse()
            ),
        )
        for item in page
    ]
    return InboundEnquiryListResponse(
        enquiries=responses,
        total=total,
        summary={
            "total": len(all_enquiries),
            "demand": len([item for item in all_enquiries if item.intent in {"buy", "rent"}]),
            "instructions": len([item for item in all_enquiries if item.intent in {"sell", "let"}]),
            "needs_review": len([item for item in all_enquiries if item.status == "needs_review"]),
            "unassigned": len([item for item in all_enquiries if not item.assigned_user_id]),
            "unqualified": len(
                [item for item in all_enquiries if item.record_kind == "unqualified_contact"]
            ),
            "qualified": len(
                [item for item in all_enquiries if item.status in _QUALIFIED_STATUSES]
            ),
            "overdue": len([item for item in all_enquiries if _sla_state(item) == "overdue"]),
            "follow_up_due": len(
                [
                    item
                    for item in all_enquiries
                    if item.next_follow_up and item.next_follow_up <= _utcnow().date()
                ]
            ),
            "owner_matched": len(
                [
                    item
                    for item in all_enquiries
                    if all_enrichments.get(
                        item.contact_id, InboundOwnershipEnrichmentResponse()
                    ).matched
                ]
            ),
            "portfolio_owners": len(
                [
                    item
                    for item in all_enquiries
                    if all_enrichments.get(
                        item.contact_id, InboundOwnershipEnrichmentResponse()
                    ).multiple_property_owner
                ]
            ),
            "high_priority": len(
                [item for item in all_enquiries if all_priorities[item.id or 0].band == "high"]
            ),
        },
    )


async def get_inbound_enquiry(
    db: AsyncSession, enquiry_id: int, organization_id: str
) -> InboundEnquiryResponse:
    enquiry = await inbound_repository.get_crm_inbound_enquiry(db, enquiry_id)
    if not enquiry or enquiry.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Inbound enquiry not found")
    return await _enquiry_response(
        db, enquiry, include_registry_profiles=True, include_history=True
    )


async def update_inbound_enquiry(
    db: AsyncSession,
    *,
    enquiry_id: int,
    organization_id: str,
    user_id: str,
    payload: InboundEnquiryUpdate,
) -> InboundEnquiryResponse:
    enquiry = await inbound_repository.get_crm_inbound_enquiry(db, enquiry_id)
    if not enquiry or enquiry.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Inbound enquiry not found")
    changes = payload.model_dump(exclude_unset=True)
    contact_changes = {
        key: changes.pop(key)
        for key in (
            "consent_status",
            "do_not_contact",
            "preferred_channel",
            "full_name",
            "phone",
            "email",
        )
        if key in changes
    }
    details_changed = bool({"full_name", "phone", "email"} & contact_changes.keys())
    if "full_name" in contact_changes:
        contact_changes["full_name"] = (contact_changes["full_name"] or "").strip()
    if "phone" in contact_changes:
        contact_changes["phone"] = _normalize_phone(contact_changes["phone"])
    if "email" in contact_changes:
        contact_changes["email"] = _normalize_email(contact_changes["email"])
    previous_status = enquiry.status
    matching_changed = bool(
        {"intent", "record_kind", "criteria", "strict_fields", "confirmed_data"} & changes.keys()
    )
    for key, value in changes.items():
        if key == "notes":
            enquiry.raw_notes = value
        else:
            setattr(enquiry, key, value)
    if changes.get("intent") and changes["intent"] != "unknown":
        enquiry.record_kind = "opportunity"
        enquiry.confirmed_data = {
            **(enquiry.confirmed_data or {}),
            "intent": changes["intent"],
        }
    if changes.get("criteria") is not None:
        enquiry.confirmed_data = {
            **(enquiry.confirmed_data or {}),
            "criteria": changes["criteria"],
        }
    if enquiry.intent == "unknown":
        enquiry.record_kind = "unqualified_contact"
    enquiry.updated_at = _utcnow()
    inbound_repository.save(db, enquiry)
    if contact_changes:
        contact = await inbound_repository.get_crm_inbound_contact(db, enquiry.contact_id)
        if not contact or contact.organization_id != organization_id:
            raise HTTPException(status_code=500, detail="Inbound contact is missing")
        for key, value in contact_changes.items():
            setattr(contact, key, value)
        contact.updated_at = enquiry.updated_at
        inbound_repository.save(db, contact)
        inbound_repository.save(
            db,
            CrmInboundActivity(
                enquiry_id=enquiry_id,
                organization_id=organization_id,
                user_id=user_id,
                activity_type="contactability_updated",
                body=(
                    "Contact details updated"
                    if details_changed
                    else "Contact permissions or preferred channel updated"
                ),
                metadata_json=contact_changes,
                created_at=enquiry.updated_at,
            ),
        )
    if matching_changed:
        await inbound_repository.mark_enquiry_matches_stale(db, enquiry, enquiry_id)
    if enquiry.status != previous_status:
        inbound_repository.save(
            db,
            CrmInboundActivity(
                enquiry_id=enquiry_id,
                organization_id=organization_id,
                user_id=user_id,
                activity_type="status_changed",
                body=f"Stage changed from {previous_status} to {enquiry.status}",
                metadata_json={"from": previous_status, "to": enquiry.status},
                created_at=enquiry.updated_at,
            ),
        )
    await db.commit()
    await db.refresh(enquiry)
    return await _enquiry_response(
        db, enquiry, include_registry_profiles=True, include_history=True
    )


async def add_inbound_note(
    db: AsyncSession, *, enquiry_id: int, organization_id: str, user_id: str, body: str
) -> InboundEnquiryResponse:
    enquiry = await inbound_repository.get_crm_inbound_enquiry(db, enquiry_id)
    if not enquiry or enquiry.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Inbound enquiry not found")
    inbound_repository.save(
        db,
        CrmInboundNote(
            enquiry_id=enquiry_id,
            organization_id=organization_id,
            user_id=user_id,
            body=body.strip(),
            created_at=_utcnow(),
        ),
    )
    enquiry.updated_at = _utcnow()
    inbound_repository.save(db, enquiry)
    inbound_repository.save(
        db,
        CrmInboundActivity(
            enquiry_id=enquiry_id,
            organization_id=organization_id,
            user_id=user_id,
            activity_type="note",
            body=body.strip(),
            metadata_json={},
            created_at=enquiry.updated_at,
        ),
    )
    await db.commit()
    return await _enquiry_response(
        db, enquiry, include_registry_profiles=True, include_history=True
    )


async def add_inbound_activity(
    db: AsyncSession,
    *,
    enquiry_id: int,
    organization_id: str,
    user_id: str,
    activity_type: str,
    body: str | None,
) -> InboundEnquiryResponse:
    enquiry = await inbound_repository.get_crm_inbound_enquiry(db, enquiry_id)
    if not enquiry or enquiry.organization_id != organization_id:
        raise HTTPException(status_code=404, detail="Inbound enquiry not found")
    now = _utcnow()
    enquiry.contact_attempt_count += 1
    enquiry.last_contact_at = now
    enquiry.first_contact_at = enquiry.first_contact_at or now
    if enquiry.status in {"new", "assigned", "needs_review"}:
        enquiry.status = "attempted"
    enquiry.updated_at = now
    inbound_repository.save(db, enquiry)
    inbound_repository.save(
        db,
        CrmInboundActivity(
            enquiry_id=enquiry_id,
            organization_id=organization_id,
            user_id=user_id,
            activity_type=activity_type,
            body=body.strip() if body else None,
            metadata_json={"attempt": enquiry.contact_attempt_count},
            created_at=now,
        ),
    )
    await db.commit()
    await db.refresh(enquiry)
    return await _enquiry_response(
        db, enquiry, include_registry_profiles=True, include_history=True
    )
