"""Vitevue-supplied renewal leads: the lease-expiry dataset filtered to one agent's areas.

Assets › Renewals reads the whole dataset (/crm/renewals). Leads shows this view, computed on
every request from the agent's saved areas and windows, so it follows time, new lease data and
preference changes without storing copies of property data. One row per unit comes from the
lease query itself. When an agent opens a unit it becomes the agency's one shared lead for that
unit (open_renewal_lead), which keeps its stage, notes and owner after it leaves the window.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm import (
    inbound,
    inbound_repository,
    opportunity_queries,
    renewal_repository,
    workspace_repository,
)
from app.crm.inbound_models import InboundEnquiryResponse
from app.crm.models import (
    CrmAgentAreaPreference,
    CrmAreaPreferencesResponse,
    CrmAreaPreferencesUpdate,
    CrmRenewalArea,
    CrmRenewalLead,
    CrmRenewalLeadsResponse,
    CrmRenewalOwner,
    CrmRenewalsResponse,
)
from app.crm.opportunities import _lead_from_row, get_lead_detail

logger = logging.getLogger(__name__)


def _preferences_response(
    record: CrmAgentAreaPreference | None,
) -> CrmAreaPreferencesResponse:
    if record is None:
        return CrmAreaPreferencesResponse(areas=[])
    return CrmAreaPreferencesResponse(
        areas=list(record.areas),
        forward_days=record.forward_days,
        lookback_days=record.lookback_days,
        updated_at=record.updated_at,
    )


def _clean_areas(areas: list[str]) -> list[str]:
    return sorted({area.strip() for area in areas if area.strip()})


async def until_disconnected(is_disconnected, work):
    """Answer `work`, or None after stopping its lease queries when the agent abandons it."""
    return await opportunity_queries.run_until_disconnected(is_disconnected, work)


async def get_area_preferences(
    db: AsyncSession, *, user_id: str, organization_id: str
) -> CrmAreaPreferencesResponse:
    record = await renewal_repository.get_area_preference(db, user_id, organization_id)
    return _preferences_response(record)


async def update_area_preferences(
    db: AsyncSession,
    *,
    payload: CrmAreaPreferencesUpdate,
    user_id: str,
    organization_id: str,
) -> CrmAreaPreferencesResponse:
    now = datetime.now(timezone.utc)
    record = await renewal_repository.get_area_preference(db, user_id, organization_id)
    if record is None:
        record = CrmAgentAreaPreference(
            user_id=user_id,
            organization_id=organization_id,
            created_at=now,
            updated_at=now,
        )
    record.areas = _clean_areas(payload.areas)
    record.forward_days = payload.forward_days
    record.lookback_days = payload.lookback_days
    record.updated_at = now
    renewal_repository.save(db, record)
    await db.commit()
    await db.refresh(record)
    return _preferences_response(record)


async def list_renewal_areas(as_of: date | None = None) -> list[CrmRenewalArea]:
    anchor = as_of or date.today()
    if not await opportunity_queries._tables_exist():
        return []
    try:
        rows = await opportunity_queries.fetch_renewal_areas(anchor)
    except Exception as exc:
        logger.exception("CRM renewal areas query failed")
        raise HTTPException(
            status_code=503, detail="CRM lead data is temporarily unavailable"
        ) from exc
    return [
        CrmRenewalArea(area=str(row["area"]), upcoming_90_days=int(row["upcoming_90_days"]))
        for row in rows
    ]


# Vacant: the unit's lease ended this many days ago at most (Assets › Renewals default).
VACANT_LOOKBACK_DAYS_DEFAULT = 60


async def list_renewals(
    *,
    window: str | None,
    area: str | None,
    property_type: str | None,
    project: str | None,
    building: str | None,
    limit: int,
    offset: int,
    as_of: date | None = None,
) -> CrmRenewalsResponse:
    """Every lease in the dataset, whatever any agent's areas, narrowed by the tab's filters.

    window: vacant (ended in the last 60 days, no later lease), expired (last 12 months), one of
    the expiring windows, or None for all of them.
    """
    anchor = as_of or date.today()
    empty = CrmRenewalsResponse(leads=[], total=0, limit=limit, offset=offset, as_of=anchor)
    if not await opportunity_queries._tables_exist():
        return empty
    vacant = window == "vacant"
    try:
        rows, total, facets = await opportunity_queries.fetch_renewals(
            anchor,
            status="expired" if vacant else window,
            unlet_only=vacant,
            expired_days=VACANT_LOOKBACK_DAYS_DEFAULT if vacant else 365,
            area=area,
            property_type=property_type,
            project=project,
            building=building,
            limit=limit,
            offset=offset,
        )
    except Exception as exc:
        logger.exception("CRM renewals query failed")
        raise HTTPException(
            status_code=503, detail="CRM lead data is temporarily unavailable"
        ) from exc
    return empty.model_copy(
        update={
            "leads": [_lead_from_row(row) for row in rows],
            "total": int(total),
            "property_types": list(facets.get("property_types") or []),
            "projects": list(facets.get("projects") or []),
            "buildings": list(facets.get("buildings") or []),
        }
    )


async def _owners_by_candidate(
    db: AsyncSession,
    *,
    candidate_keys: list[str],
    user_id: str,
    organization_id: str,
) -> dict[str, CrmRenewalOwner]:
    """Known owners only: the agent's workspace first, then a current-owner import claim."""
    if not candidate_keys:
        return {}
    owners: dict[str, CrmRenewalOwner] = {}
    claims = await renewal_repository.list_current_owner_claims(db, organization_id, candidate_keys)
    for claim in claims:  # Highest confidence first.
        if claim.unit_candidate_key and claim.unit_candidate_key not in owners:
            owners[claim.unit_candidate_key] = CrmRenewalOwner(
                name=claim.contact_name or None,
                phone=claim.phone_primary or claim.phone_alternate,
                email=claim.email_primary,
                source="owner_import",
            )
    workspaces = await workspace_repository.list_lead_workspaces_for_candidates(
        db, candidate_keys, organization_id, user_id
    )
    for workspace in workspaces:
        if workspace.owner_name or workspace.owner_phone or workspace.owner_email:
            owners[workspace.unit_candidate_key] = CrmRenewalOwner(
                name=workspace.owner_name,
                phone=workspace.owner_phone,
                email=workspace.owner_email,
                source="workspace",
            )
    return owners


async def list_renewal_leads(
    db: AsyncSession,
    *,
    user_id: str,
    organization_id: str,
    limit: int,
    offset: int,
    as_of: date | None = None,
    kind: str | None = None,
) -> CrmRenewalLeadsResponse:
    anchor = as_of or date.today()
    preferences = await get_area_preferences(db, user_id=user_id, organization_id=organization_id)
    empty = CrmRenewalLeadsResponse(
        items=[],
        total=0,
        limit=limit,
        offset=offset,
        entered_today=0,
        preferences=preferences,
        as_of=anchor,
    )
    if not preferences.areas or not await opportunity_queries._tables_exist():
        return empty
    try:
        lead_rows, count_rows = await opportunity_queries.fetch_renewal_leads(
            anchor,
            preferences.areas,
            preferences.forward_days,
            preferences.lookback_days,
            limit,
            offset,
            kind=kind,
        )
    except Exception as exc:
        logger.exception("CRM renewal leads query failed")
        raise HTTPException(
            status_code=503, detail="CRM lead data is temporarily unavailable"
        ) from exc
    leads = [_lead_from_row(row) for row in lead_rows]
    owners = await _owners_by_candidate(
        db,
        candidate_keys=sorted({lead.unit_candidate_key for lead in leads}),
        user_id=user_id,
        organization_id=organization_id,
    )
    opened = {
        enquiry.source_lead_id: enquiry.id
        for enquiry in await inbound_repository.list_vitevue_lease_enquiries(
            db, organization_id, {lead.unit_candidate_key for lead in leads}
        )
    }
    counts = count_rows[0] if count_rows else {}
    return empty.model_copy(
        update={
            "items": [
                CrmRenewalLead(
                    lead=lead,
                    owner=owners.get(lead.unit_candidate_key),
                    lead_record_id=opened.get(lead.unit_candidate_key),
                )
                for lead in leads
            ],
            "total": int(counts.get("total") or 0),
            "entered_today": int(counts.get("entered_today") or 0),
        }
    )


async def open_renewal_lead(
    db: AsyncSession, *, lease_id: str, user_id: str, organization_id: str
) -> InboundEnquiryResponse:
    """Turn a Vitevue lease into the agency's shared lead for its unit, or return the one there.

    The lead starts undecided (seller or landlord) and carries the unit, its last tenancy and
    last purchase. The owner comes only from an owner import that tied a current owner to the
    unit; otherwise it stays blank for the agent to fill in.
    """
    detail = await get_lead_detail(lease_id)
    lead = detail.lead
    if not lead.unit_number:
        raise HTTPException(status_code=422, detail="This lease has no unit number")
    claims = await renewal_repository.list_current_owner_claims(
        db, organization_id, [lead.unit_candidate_key]
    )
    owner = claims[0] if claims else None  # Highest confidence first.
    criteria = {
        "building": lead.building_name or lead.project_name,
        "unit_number": lead.unit_number,
        "locations": [lead.area_name],
        "property_type": lead.property_subtype,
        "bedrooms": lead.bedrooms,
        "size_sqft": lead.size_sqft,
    }
    lease = {
        "lease_id": lead.lead_id,
        "lease_start": lead.lease_start.isoformat(),
        "lease_end": lead.lease_end.isoformat(),
        "annual_rent_aed": lead.annual_rent_aed,
        "vacant": lead.lease_evidence_state == "unlet_signal",
        "last_purchase_date": (
            lead.last_purchase_date.isoformat() if lead.last_purchase_date else None
        ),
        "last_purchase_price_aed": lead.latest_purchase_price_aed or lead.matched_sale_price_aed,
    }
    return await inbound.open_vitevue_lease_enquiry(
        db,
        organization_id=organization_id,
        user_id=user_id,
        unit_key=lead.unit_candidate_key,
        owner_name=(owner.contact_name or "") if owner else "",
        owner_phone=(owner.phone_primary or owner.phone_alternate) if owner else None,
        owner_email=owner.email_primary if owner else None,
        criteria={key: value for key, value in criteria.items() if value},
        lease=lease,
    )
