from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.crm import inbound, matching
from app.crm.inbound_models import (
    CrmInboundContact,
    CrmInboundEnquiry,
    CrmInboundMatch,
    InboundEnquiryCreate,
)


async def test_manual_enquiry_scope_history_and_match_state_survive_refresh(pg_session):
    org, user = str(uuid4()), str(uuid4())
    response = await inbound.create_inbound_enquiry(
        pg_session,
        payload=InboundEnquiryCreate(
            full_name="Migration buyer",
            intent="buy",
            record_kind="opportunity",
            criteria={"locations": ["JBR"], "budget_max_aed": 3000000, "bedrooms_min": 2},
        ),
        user_id=user,
        organization_id=org,
    )
    with pytest.raises(HTTPException) as error:
        await inbound.get_inbound_enquiry(pg_session, response.id, str(uuid4()))
    assert error.value.status_code == 404
    enquiry = await pg_session.get(CrmInboundEnquiry, response.id)
    arguments = dict(
        enquiry=enquiry,
        target_type="unit",
        target_key="test-unit",
        title="Unit",
        subtitle="Evidence",
        reasons=["Location"],
        breakdown={"requirements": 85},
        snapshot={"source": "registry"},
    )
    assert await matching._upsert_match(pg_session, score=49, **arguments) is None
    item = await matching._upsert_match(pg_session, score=85, **arguments)
    assert item.fit == "exact"
    await pg_session.commit()
    await matching.update_inbound_match_state(
        pg_session, match_id=item.id, organization_id=org, state="saved"
    )
    refreshed = await matching._upsert_match(pg_session, score=65, **arguments)
    assert refreshed.state == "saved"
    assert (refreshed.score, refreshed.fit) == (65, "near")
    await matching.update_inbound_match_state(
        pg_session, match_id=item.id, organization_id=org, state="dismissed"
    )
    dismissed = await matching._upsert_match(
        pg_session, score=99, **{**arguments, "title": "Changed"}
    )
    assert (dismissed.title, dismissed.score, dismissed.state) == ("Unit", 65, "dismissed")
    await pg_session.commit()
    item_id, criteria = item.id, dict(enquiry.criteria)
    pg_session.expire_all()
    persisted = await pg_session.get(CrmInboundMatch, item_id)
    assert persisted.snapshot == {"source": "registry"}
    loaded = await inbound.get_inbound_enquiry(pg_session, response.id, org)
    assert loaded.criteria == criteria
    assert loaded.activities[0].activity_type == "created"


async def test_high_score_without_requirements_cannot_be_exact(pg_session):
    org, user = str(uuid4()), str(uuid4())
    response = await inbound.create_inbound_enquiry(
        pg_session,
        payload=InboundEnquiryCreate(full_name="Unqualified contact"),
        user_id=user,
        organization_id=org,
    )
    enquiry = await pg_session.get(CrmInboundEnquiry, response.id)
    item = await matching._upsert_match(
        pg_session,
        enquiry=enquiry,
        target_type="unit",
        target_key="test",
        score=99,
        title="Unit",
        subtitle="",
        reasons=[],
        breakdown={},
        snapshot={},
    )
    assert item.fit == "near"
    assert item.missing_fields == ["location", "budget", "property details"]
    item.state = "stale"
    await pg_session.commit()
    refreshed = await matching._upsert_match(
        pg_session,
        enquiry=enquiry,
        target_type="unit",
        target_key="test",
        score=50,
        title="Unit",
        subtitle="",
        reasons=[],
        breakdown={},
        snapshot={},
    )
    assert (refreshed.state, refreshed.fit) == ("suggested", "review")


@pytest.mark.parametrize(
    ("intent", "criteria"),
    [
        (
            "buy",
            {
                "wants": "2 bed apartment",
                "budget_max_aed": 1500000,
                "financing": "Mortgage",
                "purpose": "Invest",
                "timeline": "3 months",
                "locations": ["JVC", "Dubai Marina"],
            },
        ),
        (
            "sell",
            {
                "building": "Bloom Towers A",
                "unit_number": "1204",
                "expected_price_aed": 2100000,
                "occupancy": "Vacant",
                "mortgage": "None",
                "timeline": "This month",
                "locations": ["JVC"],
            },
        ),
        (
            "rent",
            {
                "wants": "Studio",
                "budget_max_aed": 65000,
                "cheques": "4",
                "move_in": "December",
                "locations": ["JVC"],
            },
        ),
        (
            "let",
            {
                "building": "Marina Heights",
                "unit_number": "3301",
                "expected_rent_aed": 120000,
                "available": "January",
                "furnishing": "Furnished",
                "locations": ["JVC"],
            },
        ),
    ],
)
async def test_new_lead_form_fields_persist_for_every_lead_type(pg_session, intent, criteria):
    # Every field of the New Lead form must be readable after a real commit and reload;
    # a field dropped on the way to the database is part of the client's brief lost.
    org, user = str(uuid4()), str(uuid4())
    response = await inbound.create_inbound_enquiry(
        pg_session,
        payload=InboundEnquiryCreate(
            full_name="Test Lead",
            phone="050 123 4567",
            source="Property Finder",
            intent=intent,
            record_kind="opportunity",
            criteria=criteria,
        ),
        user_id=user,
        organization_id=org,
    )
    pg_session.expire_all()

    enquiry = await pg_session.get(CrmInboundEnquiry, response.id)
    contact = await pg_session.get(CrmInboundContact, enquiry.contact_id)
    assert (enquiry.intent, enquiry.record_kind, enquiry.status) == (intent, "opportunity", "new")
    assert enquiry.source == "Property Finder"
    assert enquiry.criteria == criteria
    assert (contact.full_name, contact.phone) == ("Test Lead", "+971501234567")
    loaded = await inbound.get_inbound_enquiry(pg_session, response.id, org)
    assert loaded.criteria == criteria
    assert loaded.activities[0].activity_type == "created"
