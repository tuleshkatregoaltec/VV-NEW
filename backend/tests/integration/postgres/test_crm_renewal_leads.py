"""Renewal leads: per-agent area preferences and the rule that owner fields are never inferred."""

from datetime import date, datetime, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.crm import inbound, opportunity_queries, renewal_leads
from app.crm.import_models import CrmContactImport, CrmContactList, CrmPropertyContactClaim
from app.crm.inbound_models import (
    VITEVUE_LEASE_SOURCE,
    CrmInboundContact,
    CrmInboundEnquiry,
    InboundEnquiryUpdate,
)
from app.crm.models import (
    CrmAgentAreaPreference,
    CrmAreaPreferencesUpdate,
    CrmLeadDetailResponse,
    CrmLeadWorkspace,
)

AS_OF = date(2026, 10, 9)
JVC = "Jumeirah Village Circle"


def _row(event_key, candidate):
    return {
        "event_key": event_key,
        "unit_candidate_key": candidate,
        "building_name": "Tower",
        "project_name": "",
        "area_name": JVC,
        "property_type": "Apartment",
        "bedrooms": "1 Bed",
        "annual_rent_aed": 80_000,
        "lease_start": date(2025, 11, 1),
        "lease_end": date(2026, 10, 31),
        "days_to_expiry": 22,
        "lead_status": "expiring_30",
        "priority": "high",
        "raw_score": 84,
        "match_confidence": "low",
    }


async def test_preferences_are_per_agent_and_saving_again_updates_in_place(pg_session):
    org, agent, colleague = str(uuid4()), str(uuid4()), str(uuid4())

    default = await renewal_leads.get_area_preferences(
        pg_session, user_id=agent, organization_id=org
    )
    assert (default.areas, default.forward_days, default.lookback_days) == ([], 60, 30)

    for areas, forward in (([JVC, " ", JVC], 60), (["Dubai Marina"], 45)):
        saved = await renewal_leads.update_area_preferences(
            pg_session,
            payload=CrmAreaPreferencesUpdate(areas=areas, forward_days=forward, lookback_days=30),
            user_id=agent,
            organization_id=org,
        )
    assert (saved.areas, saved.forward_days) == (["Dubai Marina"], 45)
    rows = (
        await pg_session.exec(
            CrmAgentAreaPreference.__table__.select().where(
                CrmAgentAreaPreference.organization_id == org
            )
        )
    ).all()
    assert len(rows) == 1
    # A colleague's inbox is not driven by this agent's areas.
    other = await renewal_leads.get_area_preferences(
        pg_session, user_id=colleague, organization_id=org
    )
    assert other.areas == []


async def test_agent_without_areas_gets_an_empty_inbox_without_querying(pg_session, monkeypatch):
    async def fail(*_args, **_kwargs):
        raise AssertionError("no areas means no lease query")

    monkeypatch.setattr(opportunity_queries, "_tables_exist", fail)

    result = await renewal_leads.list_renewal_leads(
        pg_session, user_id=str(uuid4()), organization_id=str(uuid4()), limit=50, offset=0
    )

    assert (result.items, result.total) == ([], 0)


async def test_owner_is_blank_unless_entered_or_import_matched_as_current_owner(
    pg_session, monkeypatch
):
    now = datetime.now(timezone.utc)
    org, agent = str(uuid4()), str(uuid4())
    await renewal_leads.update_area_preferences(
        pg_session,
        payload=CrmAreaPreferencesUpdate(areas=[JVC]),
        user_id=agent,
        organization_id=org,
    )
    record = CrmContactImport(
        organization_id=org,
        created_by_user_id=agent,
        name="Owners",
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
        created_by_user_id=agent,
        source_import_id=record.id,
        name="Owners",
        created_at=now,
        updated_at=now,
    )
    pg_session.add(contact_list)
    await pg_session.flush()
    claims = [
        ("unit-imported", "verified_current_owner", "Imported Owner"),
        ("unit-former", "former_owner", "Former Owner"),
        ("unit-typed", "probable_current_owner", "Imported But Overridden"),
    ]
    for row, (candidate, status, name) in enumerate(claims, 1):
        pg_session.add(
            CrmPropertyContactClaim(
                organization_id=org,
                import_id=record.id,
                list_id=contact_list.id,
                source_sheet="Owners",
                source_row_number=row,
                row_fingerprint=str(row),
                owner_key=f"owner-{row}",
                property_key=candidate,
                contact_name=name,
                phone_primary="+971500000000",
                ownership_status=status,
                confidence_score=90,
                match_reason="Registry evidence",
                unit_candidate_key=candidate,
                evidence={},
                created_at=now,
                updated_at=now,
            )
        )
    pg_session.add(
        CrmLeadWorkspace(
            user_id=agent,
            organization_id=org,
            lead_id="lease-typed",
            unit_candidate_key="unit-typed",
            owner_name="Typed By Agent",
            tags=[],
            created_at=now,
            updated_at=now,
        )
    )
    await pg_session.commit()

    async def tables_exist():
        return True

    async def fetch(anchor, areas, forward_days, lookback_days, limit, offset, kind=None):
        assert (areas, forward_days, lookback_days) == ([JVC], 60, 30)
        rows = [
            _row("lease-unknown", "unit-unknown"),
            _row("lease-imported", "unit-imported"),
            _row("lease-former", "unit-former"),
            _row("lease-typed", "unit-typed"),
        ]
        return rows, [{"total": 4, "entered_today": 0}]

    monkeypatch.setattr(opportunity_queries, "_tables_exist", tables_exist)
    monkeypatch.setattr(opportunity_queries, "fetch_renewal_leads", fetch)

    result = await renewal_leads.list_renewal_leads(
        pg_session, user_id=agent, organization_id=org, limit=50, offset=0, as_of=AS_OF
    )

    owners = {item.lead.lead_id: item.owner for item in result.items}
    assert owners["lease-unknown"] is None
    assert owners["lease-former"] is None  # A former owner is not the owner to call.
    assert (owners["lease-imported"].name, owners["lease-imported"].source) == (
        "Imported Owner",
        "owner_import",
    )
    assert (owners["lease-typed"].name, owners["lease-typed"].source) == (
        "Typed By Agent",
        "workspace",
    )


def _lease_detail(event_key, candidate, *, unit_number="216"):
    lead = renewal_leads._lead_from_row(
        {
            **_row(event_key, candidate),
            "unit_number": unit_number,
            "unit_match_status": "unique",
            "lead_status": "expired",
            "days_to_expiry": -12,
            "lease_end": date(2026, 9, 27),
            "last_purchase_date": date(2024, 3, 7),
            "matched_sale_price": 830_000,
        }
    )
    return CrmLeadDetailResponse(
        lead=lead,
        timeline=[],
        next_best_actions=[],
        match_explanation="",
        generated_at=datetime.now(timezone.utc),
    )


async def test_a_vitevue_lease_becomes_one_shared_undecided_lead_per_unit(pg_session, monkeypatch):
    org, agent, colleague = str(uuid4()), str(uuid4()), str(uuid4())

    async def detail(lease_id):
        return _lease_detail(lease_id, "unit-shared")

    monkeypatch.setattr(renewal_leads, "get_lead_detail", detail)

    first = await renewal_leads.open_renewal_lead(
        pg_session, lease_id="lease-1", user_id=agent, organization_id=org
    )
    # Shared: a colleague with the same area opens the same unit and gets the same lead.
    second = await renewal_leads.open_renewal_lead(
        pg_session, lease_id="lease-1", user_id=colleague, organization_id=org
    )

    assert second.id == first.id
    assert (first.source, first.intent, first.status) == (VITEVUE_LEASE_SOURCE, "unknown", "new")
    assert first.assigned_user_id is None
    # It was not an enquiry, so no reply deadline starts running.
    assert first.sla_state == "on_time" and first.first_response_due_at is None
    # The owner was never identified: blank, never a made-up name.
    assert (first.contact.full_name, first.contact.phone) == ("", None)
    lease = first.observed_data["vitevue_lease"]
    assert (lease["lease_end"], lease["last_purchase_price_aed"], lease["vacant"]) == (
        "2026-09-27",
        830_000,
        True,
    )
    assert first.criteria["unit_number"] == "216"


async def test_owner_import_fills_the_owner_and_the_agent_can_correct_it(pg_session, monkeypatch):
    now = datetime.now(timezone.utc)
    org, agent = str(uuid4()), str(uuid4())
    record = CrmContactImport(
        organization_id=org,
        created_by_user_id=agent,
        name="Owners",
        original_filename="owners.csv",
        sha256=uuid4().hex,
        size_bytes=1,
        status="completed",
        mapping={},
        created_at=now,
    )
    pg_session.add(record)
    await pg_session.flush()
    contact_list = CrmContactList(
        organization_id=org,
        created_by_user_id=agent,
        source_import_id=record.id,
        name="Owners",
        created_at=now,
        updated_at=now,
    )
    pg_session.add(contact_list)
    await pg_session.flush()
    pg_session.add(
        CrmPropertyContactClaim(
            organization_id=org,
            import_id=record.id,
            list_id=contact_list.id,
            source_sheet="Owners",
            source_row_number=1,
            row_fingerprint="1",
            owner_key="owner-1",
            property_key="unit-owned",
            contact_name="Huda Saeed",
            phone_primary="0501234567",
            ownership_status="verified_current_owner",
            confidence_score=95,
            match_reason="Registry evidence",
            unit_candidate_key="unit-owned",
            evidence={},
            created_at=now,
            updated_at=now,
        )
    )
    await pg_session.commit()

    async def detail(lease_id):
        return _lease_detail(lease_id, "unit-owned")

    monkeypatch.setattr(renewal_leads, "get_lead_detail", detail)
    opened = await renewal_leads.open_renewal_lead(
        pg_session, lease_id="lease-owned", user_id=agent, organization_id=org
    )
    assert opened.contact.full_name == "Huda Saeed"

    corrected = await inbound.update_inbound_enquiry(
        pg_session,
        enquiry_id=opened.id,
        organization_id=org,
        user_id=agent,
        payload=InboundEnquiryUpdate(full_name="Huda Al Saeed", email="Huda@Example.com"),
    )
    assert (corrected.contact.full_name, corrected.contact.email) == (
        "Huda Al Saeed",
        "huda@example.com",
    )


async def test_a_lease_without_a_unit_number_cannot_become_a_lead(pg_session, monkeypatch):
    async def detail(lease_id):
        return _lease_detail(lease_id, "unit-unknown", unit_number=None)

    monkeypatch.setattr(renewal_leads, "get_lead_detail", detail)

    with pytest.raises(HTTPException) as error:
        await renewal_leads.open_renewal_lead(
            pg_session, lease_id="lease-x", user_id=str(uuid4()), organization_id=str(uuid4())
        )
    assert error.value.status_code == 422


async def test_the_leads_list_knows_which_units_were_already_opened(pg_session, monkeypatch):
    org, agent = str(uuid4()), str(uuid4())
    await renewal_leads.update_area_preferences(
        pg_session,
        payload=CrmAreaPreferencesUpdate(areas=[JVC]),
        user_id=agent,
        organization_id=org,
    )

    async def detail(lease_id):
        return _lease_detail(lease_id, "unit-opened")

    async def tables_exist():
        return True

    async def fetch(anchor, areas, forward_days, lookback_days, limit, offset, kind=None):
        return [_row("lease-opened", "unit-opened"), _row("lease-new", "unit-new")], [
            {"total": 2, "entered_today": 0}
        ]

    monkeypatch.setattr(renewal_leads, "get_lead_detail", detail)
    monkeypatch.setattr(opportunity_queries, "_tables_exist", tables_exist)
    monkeypatch.setattr(opportunity_queries, "fetch_renewal_leads", fetch)
    opened = await renewal_leads.open_renewal_lead(
        pg_session, lease_id="lease-opened", user_id=agent, organization_id=org
    )

    result = await renewal_leads.list_renewal_leads(
        pg_session, user_id=agent, organization_id=org, limit=50, offset=0, as_of=AS_OF
    )

    ids = {item.lead.lead_id: item.lead_record_id for item in result.items}
    assert ids == {"lease-opened": opened.id, "lease-new": None}


async def test_the_database_refuses_a_second_vitevue_lead_for_the_same_unit(pg_session):
    # Two agents opening a unit at the same instant both try to insert; only one lead survives.
    now = datetime.now(timezone.utc)
    org = str(uuid4())
    contact = CrmInboundContact(organization_id=org, full_name="", created_at=now, updated_at=now)
    pg_session.add(contact)
    await pg_session.flush()
    for _ in range(2):
        pg_session.add(
            CrmInboundEnquiry(
                organization_id=org,
                contact_id=contact.id,
                created_by_user_id="agent",
                intent="unknown",
                source=VITEVUE_LEASE_SOURCE,
                source_lead_id="unit-race",
                enquiry_date=now,
                criteria={},
                strict_fields=[],
                observed_data={},
                inferred_data={},
                confirmed_data={},
                qualification={},
                extraction_evidence={},
                review_reasons=[],
                tags=[],
                created_at=now,
                updated_at=now,
            )
        )
    with pytest.raises(IntegrityError):
        await pg_session.commit()
    await pg_session.rollback()
