from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm.import_models import CrmPropertyContactClaim
from app.crm.models import CrmAgentAreaPreference

# Claims an owner import has tied to the unit as its current owner.
CURRENT_OWNER_STATUSES = ("verified_current_owner", "probable_current_owner")


async def get_area_preference(db: AsyncSession, user_id, organization_id):
    return (
        await db.exec(
            select(CrmAgentAreaPreference).where(
                CrmAgentAreaPreference.user_id == user_id,
                CrmAgentAreaPreference.organization_id == organization_id,
            )
        )
    ).first()


async def list_current_owner_claims(db: AsyncSession, organization_id, candidate_keys):
    return (
        await db.exec(
            select(CrmPropertyContactClaim)
            .where(
                CrmPropertyContactClaim.organization_id == organization_id,
                CrmPropertyContactClaim.unit_candidate_key.in_(candidate_keys),  # type: ignore[union-attr]
                CrmPropertyContactClaim.ownership_status.in_(CURRENT_OWNER_STATUSES),  # type: ignore[attr-defined]
            )
            .order_by(CrmPropertyContactClaim.confidence_score.desc())  # type: ignore[attr-defined]
        )
    ).all()


def save(db: AsyncSession, record):
    db.add(record)
