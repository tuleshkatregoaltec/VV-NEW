from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException
from sqlmodel.ext.asyncio.session import AsyncSession

from app.crm import opportunities, workspace_repository
from app.crm.identity import ParsedContact
from app.crm.import_models import (
    CrmOwnerBulkUpdate,
    CrmOwnerNote,
    CrmOwnerWorkspace,
    CrmOwnerWorkspaceSummary,
)
from app.crm.models import (
    CrmLead,
    CrmLeadNote,
    CrmLeadNoteResponse,
    CrmLeadWorkspace,
    CrmNoteCreate,
    CrmSavedLead,
    CrmSavedLeadResponse,
    CrmWorkspaceResponse,
    CrmWorkspaceSummary,
    CrmWorkspaceUpdate,
)
from app.crm.opportunities import get_lead_detail, resolve_candidate_key


async def _enrich_workspaces(
    db: AsyncSession,
    *,
    candidates: list[ParsedContact],
    user_id: str,
    organization_id: str,
    list_name: str,
) -> None:
    ready = [
        candidate
        for candidate in candidates
        if candidate.unit_candidate_key
        and candidate.lead_id
        and candidate.has_valid_contact
        and candidate.ownership_status in {"verified_current_owner", "probable_current_owner"}
    ]
    if not ready:
        return
    candidate_keys = sorted(
        {candidate.unit_candidate_key for candidate in ready if candidate.unit_candidate_key}
    )
    result = await workspace_repository.list_lead_workspaces_for_candidates(
        db, candidate_keys, organization_id, user_id
    )
    existing = {workspace.unit_candidate_key: workspace for workspace in result}
    now = datetime.now(timezone.utc)
    for candidate in ready:
        candidate_key = candidate.unit_candidate_key
        if not candidate_key:
            continue
        workspace = existing.get(candidate_key)
        if workspace is None:
            workspace = CrmLeadWorkspace(
                user_id=user_id,
                organization_id=organization_id,
                lead_id=candidate.lead_id or "",
                unit_candidate_key=candidate_key,
                pipeline_status="contact_ready",
                highlight_color="green",
                owner_name=candidate.contact_name,
                owner_email=candidate.email_primary,
                owner_phone=candidate.phone_primary or candidate.phone_alternate,
                tags=["Imported owner", list_name[:80]],
                created_at=now,
                updated_at=now,
            )
            workspace_repository.save(db, workspace)
            existing[candidate_key] = workspace
            continue
        workspace.pipeline_status = (
            "contact_ready" if workspace.pipeline_status == "new" else workspace.pipeline_status
        )
        workspace.owner_name = workspace.owner_name or candidate.contact_name
        workspace.owner_email = workspace.owner_email or candidate.email_primary
        workspace.owner_phone = (
            workspace.owner_phone or candidate.phone_primary or candidate.phone_alternate
        )
        tags = list(workspace.tags or [])
        for tag in ("Imported owner", list_name[:80]):
            if tag not in tags and len(tags) < 12:
                tags.append(tag)
        workspace.tags = tags
        workspace.updated_at = now
        workspace_repository.save(db, workspace)


def _owner_workspace_summaries(
    workspaces: list[CrmOwnerWorkspace],
    notes: list[CrmOwnerNote],
) -> list[CrmOwnerWorkspaceSummary]:
    notes_by_workspace: dict[int, list[CrmOwnerNote]] = {}
    for note in notes:
        notes_by_workspace.setdefault(note.workspace_id, []).append(note)
    summaries: list[CrmOwnerWorkspaceSummary] = []
    for workspace in workspaces:
        workspace_notes = notes_by_workspace.get(workspace.id or 0, [])
        workspace_notes.sort(key=lambda item: item.created_at, reverse=True)
        summaries.append(
            CrmOwnerWorkspaceSummary(
                owner_key=workspace.owner_key,
                pipeline_status=workspace.pipeline_status,  # type: ignore[arg-type]
                highlight_color=workspace.highlight_color,  # type: ignore[arg-type]
                next_follow_up=workspace.next_follow_up,
                tags=list(workspace.tags or []),
                note_count=len(workspace_notes),
                last_note=workspace_notes[0].body if workspace_notes else None,
                updated_at=workspace.updated_at,
            )
        )
    summaries.sort(key=lambda item: item.updated_at, reverse=True)
    return summaries


async def list_owner_workspaces(
    db: AsyncSession,
    *,
    user_id: str,
    organization_id: str,
) -> list[CrmOwnerWorkspaceSummary]:
    workspace_result = await workspace_repository.list_owner_workspaces(
        db, organization_id, user_id
    )
    workspaces = list(workspace_result)
    if not workspaces:
        return []
    workspace_ids = [item.id for item in workspaces if item.id is not None]
    note_result = await workspace_repository.list_owner_notes(
        db, organization_id, user_id, workspace_ids
    )
    return _owner_workspace_summaries(workspaces, list(note_result))


async def bulk_update_owner_workspaces(
    db: AsyncSession,
    *,
    payload: CrmOwnerBulkUpdate,
    user_id: str,
    organization_id: str,
) -> list[CrmOwnerWorkspaceSummary]:
    owner_keys = sorted({value.strip() for value in payload.owner_keys if value.strip()})
    if not owner_keys:
        raise HTTPException(status_code=422, detail="Select at least one owner")
    if not any((payload.pipeline_status, payload.highlight_color, payload.note)):
        raise HTTPException(status_code=422, detail="Choose a CRM update to apply")

    claim_result = await workspace_repository.list_existing_owner_keys(
        db, organization_id, owner_keys
    )
    valid_keys = set(claim_result)
    owner_keys = [key for key in owner_keys if key in valid_keys]
    if not owner_keys:
        raise HTTPException(status_code=404, detail="Selected owners were not found")

    workspace_result = await workspace_repository.list_owner_workspaces_for_keys(
        db, organization_id, owner_keys, user_id
    )
    existing = {workspace.owner_key: workspace for workspace in workspace_result}
    now = datetime.now(timezone.utc)
    touched: list[CrmOwnerWorkspace] = []
    note_body = (payload.note or "").strip()
    for owner_key in owner_keys:
        workspace = existing.get(owner_key)
        if workspace is None:
            workspace = CrmOwnerWorkspace(
                user_id=user_id,
                organization_id=organization_id,
                owner_key=owner_key,
                pipeline_status="new",
                highlight_color="none",
                tags=[],
                created_at=now,
                updated_at=now,
            )
        if payload.pipeline_status is not None:
            workspace.pipeline_status = payload.pipeline_status
        if payload.highlight_color is not None:
            workspace.highlight_color = payload.highlight_color
        workspace.updated_at = now
        workspace_repository.save(db, workspace)
        await db.flush()
        if note_body and workspace.id is not None:
            workspace_repository.save(
                db,
                CrmOwnerNote(
                    workspace_id=workspace.id,
                    user_id=user_id,
                    organization_id=organization_id,
                    body=note_body,
                    created_at=now,
                    updated_at=now,
                ),
            )
        touched.append(workspace)
    await db.commit()
    return await list_owner_workspaces(
        db,
        user_id=user_id,
        organization_id=organization_id,
    )


def _saved_lead_response(
    saved: CrmSavedLead,
    *,
    live_lead: CrmLead | None = None,
) -> CrmSavedLeadResponse:
    if saved.id is None:
        raise HTTPException(status_code=500, detail="Saved CRM lead is missing an id")
    snapshot = dict(saved.snapshot)
    property_type = str(snapshot.get("property_type") or "Property")
    if "asset_class" not in snapshot:
        snapshot["asset_class"] = (
            "residential"
            if property_type in {"Apartment", "Villa"}
            else "commercial"
            if property_type == "Commercial"
            else "other"
        )
    snapshot.setdefault("property_subtype", property_type)
    return CrmSavedLeadResponse(
        id=saved.id,
        lead=live_lead or CrmLead.model_validate(snapshot),
        created_at=saved.created_at,
        updated_at=saved.updated_at,
    )


async def list_saved_leads(
    db: AsyncSession,
    *,
    user_id: str,
    organization_id: str,
) -> list[CrmSavedLeadResponse]:
    result = await workspace_repository.list_saved_leads(db, organization_id, user_id)
    saved_rows = list(result)
    if not saved_rows:
        return []

    candidate_keys = [saved.unit_candidate_key for saved in saved_rows]
    live_by_saved_key = await opportunities.get_saved_lead_snapshots(candidate_keys)
    return [
        _saved_lead_response(
            saved,
            live_lead=live_by_saved_key.get(saved.unit_candidate_key),
        )
        for saved in saved_rows
    ]


async def save_lead(
    db: AsyncSession,
    *,
    lead_id: str,
    user_id: str,
    organization_id: str,
) -> CrmSavedLeadResponse:
    detail = await get_lead_detail(lead_id)
    now = datetime.now(timezone.utc)
    result = await workspace_repository.find_saved_lead_for_candidate(
        db, detail, organization_id, user_id
    )
    saved = result
    snapshot = detail.lead.model_dump(mode="json")
    if saved is None:
        saved = CrmSavedLead(
            user_id=user_id,
            organization_id=organization_id,
            lead_id=lead_id,
            unit_candidate_key=detail.lead.unit_candidate_key,
            snapshot=snapshot,
            created_at=now,
            updated_at=now,
        )
    else:
        saved.lead_id = lead_id
        saved.snapshot = snapshot
        saved.updated_at = now
    workspace_repository.save(db, saved)
    await db.commit()
    await db.refresh(saved)
    return _saved_lead_response(saved)


async def delete_saved_lead(
    db: AsyncSession,
    *,
    saved_id: int,
    user_id: str,
    organization_id: str,
) -> None:
    result = await workspace_repository.get_saved_lead(db, organization_id, saved_id, user_id)
    saved = result
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved CRM lead not found")
    await workspace_repository.delete_record(db, saved)
    await db.commit()


def _workspace_summary(
    workspace: CrmLeadWorkspace,
    *,
    note_count: int = 0,
) -> CrmWorkspaceSummary:
    if workspace.id is None:
        raise HTTPException(status_code=500, detail="CRM workspace is missing an id")
    return CrmWorkspaceSummary(
        id=workspace.id,
        lead_id=workspace.lead_id,
        unit_candidate_key=workspace.unit_candidate_key,
        pipeline_status=workspace.pipeline_status,
        highlight_color=workspace.highlight_color,
        owner_name=workspace.owner_name,
        owner_email=workspace.owner_email,
        owner_phone=workspace.owner_phone,
        next_follow_up=workspace.next_follow_up,
        tags=list(workspace.tags or []),
        note_count=note_count,
        updated_at=workspace.updated_at,
    )


def _note_response(note: CrmLeadNote) -> CrmLeadNoteResponse:
    if note.id is None:
        raise HTTPException(status_code=500, detail="CRM note is missing an id")
    return CrmLeadNoteResponse(
        id=note.id,
        body=note.body,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


async def _workspace_for_candidate(
    db: AsyncSession,
    *,
    unit_candidate_key: str,
    user_id: str,
    organization_id: str,
) -> CrmLeadWorkspace | None:
    result = await workspace_repository.find_lead_workspace(
        db, organization_id, unit_candidate_key, user_id
    )
    return result


async def _workspace_notes(
    db: AsyncSession,
    *,
    workspace_id: int,
    user_id: str,
    organization_id: str,
) -> list[CrmLeadNote]:
    result = await workspace_repository.list_workspace_notes(
        db, organization_id, user_id, workspace_id
    )
    return list(result)


async def list_lead_workspaces(
    db: AsyncSession,
    *,
    user_id: str,
    organization_id: str,
) -> list[CrmWorkspaceSummary]:
    result = await workspace_repository.list_lead_workspaces(db, organization_id, user_id)
    workspaces = list(result)
    if not workspaces:
        return []
    workspace_ids = [workspace.id for workspace in workspaces if workspace.id is not None]
    notes_result = await workspace_repository.list_lead_notes(
        db, organization_id, user_id, workspace_ids
    )
    note_counts: dict[int, int] = {}
    for note in notes_result:
        note_counts[note.workspace_id] = note_counts.get(note.workspace_id, 0) + 1
    return [
        _workspace_summary(
            workspace,
            note_count=note_counts.get(workspace.id or 0, 0),
        )
        for workspace in workspaces
    ]


async def get_lead_workspace(
    db: AsyncSession,
    *,
    lead_id: str,
    user_id: str,
    organization_id: str,
) -> CrmWorkspaceResponse:
    unit_candidate_key = await resolve_candidate_key(lead_id)
    workspace = await _workspace_for_candidate(
        db,
        unit_candidate_key=unit_candidate_key,
        user_id=user_id,
        organization_id=organization_id,
    )
    if workspace is None:
        return CrmWorkspaceResponse(
            id=None,
            lead_id=lead_id,
            unit_candidate_key=unit_candidate_key,
            pipeline_status="new",
            highlight_color="none",
            updated_at=datetime.now(timezone.utc),
            notes=[],
        )
    notes = await _workspace_notes(
        db,
        workspace_id=workspace.id or 0,
        user_id=user_id,
        organization_id=organization_id,
    )
    summary = _workspace_summary(workspace, note_count=len(notes))
    return CrmWorkspaceResponse(
        **summary.model_dump(),
        notes=[_note_response(note) for note in notes],
    )


def _clean_workspace_value(value: str | None) -> str | None:
    return str(value or "").strip() or None


async def update_lead_workspace(
    db: AsyncSession,
    *,
    lead_id: str,
    payload: CrmWorkspaceUpdate,
    user_id: str,
    organization_id: str,
) -> CrmWorkspaceResponse:
    unit_candidate_key = await resolve_candidate_key(lead_id)
    workspace = await _workspace_for_candidate(
        db,
        unit_candidate_key=unit_candidate_key,
        user_id=user_id,
        organization_id=organization_id,
    )
    now = datetime.now(timezone.utc)
    if workspace is None:
        workspace = CrmLeadWorkspace(
            user_id=user_id,
            organization_id=organization_id,
            lead_id=lead_id,
            unit_candidate_key=unit_candidate_key,
            created_at=now,
            updated_at=now,
        )
    changes = payload.model_dump(exclude_unset=True)
    for field in ("pipeline_status", "highlight_color", "next_follow_up"):
        if field in changes:
            setattr(workspace, field, changes[field])
    for field in ("owner_name", "owner_email", "owner_phone"):
        if field in changes:
            setattr(workspace, field, _clean_workspace_value(changes[field]))
    if "tags" in changes:
        normalized_tags: list[str] = []
        for tag in changes["tags"] or []:
            clean_tag = str(tag).strip()
            if clean_tag and clean_tag.casefold() not in {
                existing.casefold() for existing in normalized_tags
            }:
                normalized_tags.append(clean_tag[:40])
        workspace.tags = normalized_tags[:12]
    workspace.lead_id = lead_id
    workspace.updated_at = now
    workspace_repository.save(db, workspace)
    await db.commit()
    await db.refresh(workspace)
    notes = await _workspace_notes(
        db,
        workspace_id=workspace.id or 0,
        user_id=user_id,
        organization_id=organization_id,
    )
    summary = _workspace_summary(workspace, note_count=len(notes))
    return CrmWorkspaceResponse(
        **summary.model_dump(),
        notes=[_note_response(note) for note in notes],
    )


async def add_lead_note(
    db: AsyncSession,
    *,
    lead_id: str,
    payload: CrmNoteCreate,
    user_id: str,
    organization_id: str,
) -> CrmWorkspaceResponse:
    body = payload.body.strip()
    if not body:
        raise HTTPException(status_code=422, detail="Note cannot be empty")
    workspace_response = await update_lead_workspace(
        db,
        lead_id=lead_id,
        payload=CrmWorkspaceUpdate(),
        user_id=user_id,
        organization_id=organization_id,
    )
    if workspace_response.id is None:
        raise HTTPException(status_code=500, detail="CRM workspace was not created")
    now = datetime.now(timezone.utc)
    note = CrmLeadNote(
        workspace_id=workspace_response.id,
        user_id=user_id,
        organization_id=organization_id,
        body=body,
        created_at=now,
        updated_at=now,
    )
    workspace_repository.save(db, note)
    await db.commit()
    return await get_lead_workspace(
        db,
        lead_id=lead_id,
        user_id=user_id,
        organization_id=organization_id,
    )


async def delete_lead_note(
    db: AsyncSession,
    *,
    note_id: int,
    user_id: str,
    organization_id: str,
) -> None:
    result = await workspace_repository.get_lead_note(db, note_id, organization_id, user_id)
    note = result
    if note is None:
        raise HTTPException(status_code=404, detail="CRM note not found")
    await workspace_repository.delete_record(db, note)
    await db.commit()
