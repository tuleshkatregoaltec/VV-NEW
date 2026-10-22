from __future__ import annotations

from sqlalchemy import or_
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm.import_models import CrmPropertyContactClaim
from app.crm.inbound_models import (
    CrmInboundContact,
    CrmInboundEnquiry,
    CrmInboundMatch,
)
from app.crm.inbound_policy import (
    _QUALIFIED_STATUSES,
    _utcnow,
)


async def find_owner_candidates(db: AsyncSession, organization_id, requested_locations):
    claims_statement = select(CrmPropertyContactClaim).where(
        CrmPropertyContactClaim.organization_id == organization_id,
        CrmPropertyContactClaim.ownership_status.in_(  # type: ignore[union-attr]
            ["verified_current_owner", "probable_current_owner"]
        ),
    )
    if requested_locations:
        location_pattern = f"%{requested_locations[0]}%"
        claims_statement = claims_statement.where(
            or_(
                CrmPropertyContactClaim.area_name.ilike(location_pattern),  # type: ignore[union-attr]
                CrmPropertyContactClaim.project_name.ilike(location_pattern),  # type: ignore[union-attr]
                CrmPropertyContactClaim.building_name.ilike(location_pattern),  # type: ignore[union-attr]
            )
        )
    claims_result = await db.exec(
        claims_statement.order_by(CrmPropertyContactClaim.confidence_score.desc()).limit(80)
    )
    return (claims_result).all()


def save(db: AsyncSession, record):
    db.add(record)


async def find_target_match(db: AsyncSession, enquiry, target_key, target_type):
    return (
        await db.exec(
            select(CrmInboundMatch).where(
                CrmInboundMatch.enquiry_id == enquiry.id,
                CrmInboundMatch.target_type == target_type,
                CrmInboundMatch.target_key == target_key,
            )
        )
    ).first()


async def get_crm_inbound_enquiry(db: AsyncSession, key):
    return await db.get(CrmInboundEnquiry, key)


async def mark_refreshable_matches_stale(db: AsyncSession, enquiry_id):
    return await db.exec(
        CrmInboundMatch.__table__.update()
        .where(
            CrmInboundMatch.enquiry_id == enquiry_id,
            CrmInboundMatch.state.notin_(["saved", "dismissed"]),
        )
        .values(state="stale", updated_at=_utcnow())
    )


async def list_matches_by_score(db: AsyncSession, enquiry_id):
    return (
        await db.exec(
            select(CrmInboundMatch)
            .where(CrmInboundMatch.enquiry_id == enquiry_id)
            .order_by(CrmInboundMatch.score.desc())
        )
    ).all()


async def list_reverse_demand(db: AsyncSession, enquiry, organization_id, reverse_intent):
    return (
        await db.exec(
            select(CrmInboundEnquiry).where(
                CrmInboundEnquiry.organization_id == organization_id,
                CrmInboundEnquiry.intent == reverse_intent,
                CrmInboundEnquiry.id != enquiry.id,
                CrmInboundEnquiry.record_kind == "opportunity",
                CrmInboundEnquiry.status.in_(list(_QUALIFIED_STATUSES)),  # type: ignore[union-attr]
                CrmInboundEnquiry.status.notin_(["closed_lost", "won"]),  # type: ignore[union-attr]
            )
        )
    ).all()


async def get_crm_inbound_contact(db: AsyncSession, key):
    return await db.get(CrmInboundContact, key)


async def get_crm_inbound_match(db: AsyncSession, key):
    return await db.get(CrmInboundMatch, key)
