from datetime import datetime, timezone
from uuid import uuid4

from app.crm import imports, owners, workspaces
from app.crm.import_models import (
    CrmContactImport,
    CrmContactList,
    CrmOwnerBulkUpdate,
    CrmPropertyContactClaim,
)
from app.crm.models import CrmSavedLead


async def test_registry_deduplicates_evidence_and_import_delete_preserves_agent_work(pg_session):
    now = datetime.now(timezone.utc)
    org, user = str(uuid4()), str(uuid4())
    record = CrmContactImport(
        organization_id=org,
        created_by_user_id=user,
        name="Owner audit",
        original_filename="owners.csv",
        sha256=uuid4().hex,
        size_bytes=100,
        status="completed",
        mapping={"sheets": []},
        created_at=now,
    )
    pg_session.add(record)
    await pg_session.flush()
    contact_list = CrmContactList(
        organization_id=org,
        created_by_user_id=user,
        source_import_id=record.id,
        name="Owner audit",
        created_at=now,
        updated_at=now,
    )
    pg_session.add(contact_list)
    await pg_session.flush()
    for row, status in enumerate(["verified_current_owner", "former_owner"], 1):
        pg_session.add(
            CrmPropertyContactClaim(
                organization_id=org,
                import_id=record.id,
                list_id=contact_list.id,
                source_sheet="Owners",
                source_row_number=row,
                row_fingerprint=str(row),
                owner_key="owner",
                property_key="property",
                contact_name="Owner Example",
                ownership_status=status,
                confidence_score=95,
                match_reason="Registry evidence",
                evidence={"legacy": {"arbitrary": [1, None, "retained"]}},
                created_at=now,
                updated_at=now,
            )
        )
    saved = CrmSavedLead(
        user_id=user,
        organization_id=org,
        lead_id="lead",
        unit_candidate_key="candidate",
        snapshot={"legacy": {"retained": True}},
        created_at=now,
        updated_at=now,
    )
    pg_session.add(saved)
    await pg_session.commit()
    arguments = dict(
        organization_id=org,
        list_id=None,
        scope="all",
        status=None,
        search=None,
        lease_window=None,
        portfolio_only=False,
        min_rent_aed=None,
        max_rent_aed=None,
        min_value_aed=None,
        max_value_aed=None,
        limit=20,
        offset=0,
    )
    registry = await owners.get_owner_registry(pg_session, **arguments)
    assert registry.total == 1
    assert registry.owners[0].property_count == 1
    assert registry.owners[0].properties[0].ownership_status == "former_owner"
    assert registry.owners[0].properties[0].duplicate_count == 2
    assert registry.summary.prior_owner_profiles == 1
    assert (
        await owners.get_owner_registry(
            pg_session, **{**arguments, "organization_id": str(uuid4())}
        )
    ).total == 0
    await workspaces.bulk_update_owner_workspaces(
        pg_session,
        payload=CrmOwnerBulkUpdate(
            owner_keys=["owner"], pipeline_status="contacted", note="Keep agent note"
        ),
        user_id=user,
        organization_id=org,
    )
    renamed = await imports.rename_contact_import(
        pg_session, import_id=record.id, name="Renamed owner audit", organization_id=org
    )
    assert renamed.name == "Renamed owner audit"
    saved_id = saved.id
    await imports.delete_contact_import(pg_session, import_id=record.id, organization_id=org)
    pg_session.expire_all()
    assert await imports.list_contact_imports(pg_session, organization_id=org) == []
    assert (await owners.get_owner_registry(pg_session, **arguments)).total == 0
    remaining = await workspaces.list_owner_workspaces(
        pg_session, user_id=user, organization_id=org
    )
    assert remaining[0].pipeline_status == "contacted"
    assert remaining[0].last_note == "Keep agent note"
    assert (await pg_session.get(CrmSavedLead, saved_id)).snapshot == {"legacy": {"retained": True}}


async def test_registry_filters_owners_by_unit_count_and_next_lease_end(pg_session):
    """The Owners tabs '3+ units' and 'Next event ≤ 90 days' page through the whole file."""
    from datetime import date, timedelta

    now = datetime.now(timezone.utc)
    today = date.today()
    org, user = str(uuid4()), str(uuid4())
    record = CrmContactImport(
        organization_id=org,
        created_by_user_id=user,
        name="Tabs",
        original_filename="tabs.csv",
        sha256=uuid4().hex,
        size_bytes=100,
        status="completed",
        mapping={"sheets": []},
        created_at=now,
    )
    pg_session.add(record)
    await pg_session.flush()
    contact_list = CrmContactList(
        organization_id=org,
        created_by_user_id=user,
        source_import_id=record.id,
        name="Tabs",
        created_at=now,
        updated_at=now,
    )
    pg_session.add(contact_list)
    await pg_session.flush()
    rows = [
        # (owner, property, lease end)
        ("three_units", "p1", None),
        ("three_units", "p2", None),
        ("three_units", "p3", None),
        ("lease_soon", "p4", today + timedelta(days=30)),
        ("lease_later", "p5", today + timedelta(days=120)),
    ]
    for row, (owner_key, property_key, lease_end) in enumerate(rows, 1):
        pg_session.add(
            CrmPropertyContactClaim(
                organization_id=org,
                import_id=record.id,
                list_id=contact_list.id,
                source_sheet="Owners",
                source_row_number=row,
                row_fingerprint=str(row),
                owner_key=owner_key,
                property_key=property_key,
                contact_name=owner_key,
                ownership_status="verified_current_owner",
                confidence_score=95,
                match_reason="Registry evidence",
                lease_end=lease_end,
                evidence={},
                created_at=now,
                updated_at=now,
            )
        )
    await pg_session.commit()
    arguments = dict(
        organization_id=org,
        list_id=None,
        scope="all",
        status=None,
        search=None,
        lease_window=None,
        portfolio_only=False,
        min_rent_aed=None,
        max_rent_aed=None,
        min_value_aed=None,
        max_value_aed=None,
        limit=20,
        offset=0,
    )

    everyone = await owners.get_owner_registry(pg_session, **arguments)
    assert everyone.total == 3

    multi = await owners.get_owner_registry(pg_session, **arguments, min_properties=3)
    assert multi.total == 1
    assert [o.owner_key for o in multi.owners] == ["three_units"]

    soon = await owners.get_owner_registry(pg_session, **arguments, next_event_days=90)
    assert soon.total == 1
    assert [o.owner_key for o in soon.owners] == ["lease_soon"]
