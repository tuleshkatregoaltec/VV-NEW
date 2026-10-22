import io
from datetime import datetime, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException, UploadFile
from sqlmodel import select

from app.crm import imports
from app.crm.import_models import CrmContactImport, CrmOwnerWorkspace, CrmPropertyContactClaim


async def test_replay_is_atomic_idempotent_and_preserves_agent_work(pg_session, monkeypatch):
    async def identity(profile):
        return profile

    monkeypatch.setattr(imports, "_llm_mapping", identity)
    for method in (
        "_recover_property_identity_from_transaction",
        "_recover_property_identity_from_near_transaction",
        "_apply_registry_unit_fallbacks",
        "resolve_cadastral_plots",
        "_enrich_workspaces",
    ):
        monkeypatch.setattr(imports, method, AsyncMock())
    monkeypatch.setattr(imports, "_sales_evidence", AsyncMock(return_value={}))
    monkeypatch.setattr(imports, "_rental_evidence", AsyncMock(return_value={}))
    monkeypatch.setattr(imports, "save_drop_log", AsyncMock(return_value=True))
    org, user = str(uuid4()), str(uuid4())
    data = (
        b"NameEn,Project,UnitNumber,LandNumber,ProcedureNameEn\n"
        b"Example,Cluster,101,12,Sale\nExample,Cluster,,13,Mortgage Registration\n"
    )

    async def upload(replay=None, payload=data):
        return await imports.process_contact_import(
            pg_session,
            upload=UploadFile(file=io.BytesIO(payload), filename="owners.csv"),
            name="Example",
            user_id=user,
            organization_id=org,
            reprocess_import_id=replay,
        )

    initial = await upload()
    record = await pg_session.get(CrmContactImport, initial.id)
    claims = list(
        (
            await pg_session.exec(
                select(CrmPropertyContactClaim).where(
                    CrmPropertyContactClaim.import_id == initial.id
                )
            )
        ).all()
    )
    original_id = claims[0].id
    original_owner = claims[0].owner_key
    # Simulate a legacy import that discarded its second row.
    await pg_session.delete(claims[1])
    record.mapping = {}
    record.summary = {"total_rows": 2, "candidate_rows": 1}
    now = datetime.now(timezone.utc)
    workspace = CrmOwnerWorkspace(
        user_id=user,
        organization_id=org,
        owner_key=original_owner,
        pipeline_status="contacted",
        tags=["Keep me"],
        created_at=now,
        updated_at=now,
    )
    pg_session.add(workspace)
    await pg_session.commit()

    with pytest.raises(HTTPException, match="409"):
        await upload(initial.id, payload=data + b"Other,Other,,,\n")
    await pg_session.rollback()

    summary = imports._summary

    def fail_after_writes(*args):
        raise RuntimeError("Simulated publication failure")

    monkeypatch.setattr(imports, "_summary", fail_after_writes)
    with pytest.raises(HTTPException, match="previous import results were preserved"):
        await upload(initial.id)
    remaining = (
        await pg_session.exec(
            select(CrmPropertyContactClaim).where(CrmPropertyContactClaim.import_id == initial.id)
        )
    ).all()
    assert len(remaining) == 1
    assert remaining[0].id == original_id
    monkeypatch.setattr(imports, "_summary", summary)
    result = await upload(initial.id)
    assert result.id == initial.id and result.list_id == initial.list_id
    assert result.summary.candidate_rows == 2
    assert result.summary.dropped_no_unit == 0
    claims = (
        await pg_session.exec(
            select(CrmPropertyContactClaim)
            .where(CrmPropertyContactClaim.import_id == initial.id)
            .order_by(CrmPropertyContactClaim.source_row_number)
        )
    ).all()
    assert claims[0].id == original_id and claims[0].owner_key == original_owner
    assert claims[1].evidence["event_family"] == "financing"
    ids = [c.id for c in claims]
    await upload(initial.id)
    assert [
        c.id
        for c in (
            await pg_session.exec(
                select(CrmPropertyContactClaim)
                .where(CrmPropertyContactClaim.import_id == initial.id)
                .order_by(CrmPropertyContactClaim.source_row_number)
            )
        ).all()
    ] == ids
    saved_workspace = (
        await pg_session.exec(
            select(CrmOwnerWorkspace).where(CrmOwnerWorkspace.organization_id == org)
        )
    ).one()
    assert saved_workspace.owner_key == original_owner
    assert saved_workspace.pipeline_status == "contacted" and saved_workspace.tags == ["Keep me"]
