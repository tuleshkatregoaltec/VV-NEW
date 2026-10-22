from __future__ import annotations

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm.import_models import (
    CrmOwnerNote,
    CrmOwnerWorkspace,
    CrmPropertyContactClaim,
)
from app.crm.models import (
    CrmLeadNote,
    CrmLeadWorkspace,
    CrmSavedLead,
)


async def list_lead_workspaces_for_candidates(
    db: AsyncSession, candidate_keys, organization_id, user_id
):
    return (
        await db.exec(
            select(CrmLeadWorkspace).where(
                CrmLeadWorkspace.user_id == user_id,
                CrmLeadWorkspace.organization_id == organization_id,
                CrmLeadWorkspace.unit_candidate_key.in_(candidate_keys),  # type: ignore[union-attr]
            )
        )
    ).all()


def save(db: AsyncSession, record):
    db.add(record)


async def list_owner_workspaces(db: AsyncSession, organization_id, user_id):
    return (
        await db.exec(
            select(CrmOwnerWorkspace).where(
                CrmOwnerWorkspace.user_id == user_id,
                CrmOwnerWorkspace.organization_id == organization_id,
            )
        )
    ).all()


async def list_owner_notes(db: AsyncSession, organization_id, user_id, workspace_ids):
    return (
        await db.exec(
            select(CrmOwnerNote).where(
                CrmOwnerNote.user_id == user_id,
                CrmOwnerNote.organization_id == organization_id,
                CrmOwnerNote.workspace_id.in_(workspace_ids),  # type: ignore[union-attr]
            )
        )
    ).all()


async def list_existing_owner_keys(db: AsyncSession, organization_id, owner_keys):
    return (
        await db.exec(
            select(CrmPropertyContactClaim.owner_key).where(
                CrmPropertyContactClaim.organization_id == organization_id,
                CrmPropertyContactClaim.owner_key.in_(owner_keys),  # type: ignore[union-attr]
            )
        )
    ).all()


async def list_owner_workspaces_for_keys(db: AsyncSession, organization_id, owner_keys, user_id):
    return (
        await db.exec(
            select(CrmOwnerWorkspace).where(
                CrmOwnerWorkspace.user_id == user_id,
                CrmOwnerWorkspace.organization_id == organization_id,
                CrmOwnerWorkspace.owner_key.in_(owner_keys),  # type: ignore[union-attr]
            )
        )
    ).all()


async def list_saved_leads(db: AsyncSession, organization_id, user_id):
    return (
        await db.exec(
            select(CrmSavedLead)
            .where(
                CrmSavedLead.user_id == user_id,
                CrmSavedLead.organization_id == organization_id,
            )
            .order_by(CrmSavedLead.updated_at.desc())
        )
    ).all()


async def find_saved_lead_for_candidate(db: AsyncSession, detail, organization_id, user_id):
    return (
        await db.exec(
            select(CrmSavedLead).where(
                CrmSavedLead.user_id == user_id,
                CrmSavedLead.organization_id == organization_id,
                CrmSavedLead.unit_candidate_key == detail.lead.unit_candidate_key,
            )
        )
    ).first()


async def get_saved_lead(db: AsyncSession, organization_id, saved_id, user_id):
    return (
        await db.exec(
            select(CrmSavedLead).where(
                CrmSavedLead.id == saved_id,
                CrmSavedLead.user_id == user_id,
                CrmSavedLead.organization_id == organization_id,
            )
        )
    ).first()


async def delete_record(db: AsyncSession, record):
    await db.delete(record)


async def find_lead_workspace(db: AsyncSession, organization_id, unit_candidate_key, user_id):
    return (
        await db.exec(
            select(CrmLeadWorkspace).where(
                CrmLeadWorkspace.user_id == user_id,
                CrmLeadWorkspace.organization_id == organization_id,
                CrmLeadWorkspace.unit_candidate_key == unit_candidate_key,
            )
        )
    ).first()


async def list_workspace_notes(db: AsyncSession, organization_id, user_id, workspace_id):
    return (
        await db.exec(
            select(CrmLeadNote)
            .where(
                CrmLeadNote.workspace_id == workspace_id,
                CrmLeadNote.user_id == user_id,
                CrmLeadNote.organization_id == organization_id,
            )
            .order_by(CrmLeadNote.created_at.desc())
        )
    ).all()


async def list_lead_workspaces(db: AsyncSession, organization_id, user_id):
    return (
        await db.exec(
            select(CrmLeadWorkspace)
            .where(
                CrmLeadWorkspace.user_id == user_id,
                CrmLeadWorkspace.organization_id == organization_id,
            )
            .order_by(CrmLeadWorkspace.updated_at.desc())
        )
    ).all()


async def list_lead_notes(db: AsyncSession, organization_id, user_id, workspace_ids):
    return (
        await db.exec(
            select(CrmLeadNote).where(
                CrmLeadNote.workspace_id.in_(workspace_ids),  # type: ignore[union-attr]
                CrmLeadNote.user_id == user_id,
                CrmLeadNote.organization_id == organization_id,
            )
        )
    ).all()


async def get_lead_note(db: AsyncSession, note_id, organization_id, user_id):
    return (
        await db.exec(
            select(CrmLeadNote).where(
                CrmLeadNote.id == note_id,
                CrmLeadNote.user_id == user_id,
                CrmLeadNote.organization_id == organization_id,
            )
        )
    ).first()
