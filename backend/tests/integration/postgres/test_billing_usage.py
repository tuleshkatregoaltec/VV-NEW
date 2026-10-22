from datetime import date
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.billing import service as billing
from app.organization import service as organizations
from app.token_usage.service import token_usage_service


class ProviderSubscription(SimpleNamespace):
    def __getitem__(self, key):
        return getattr(self, key)


async def test_subscription_sync_updates_existing_record_and_prefers_active(
    pg_session, monkeypatch
):
    org = await organizations.create_organization(pg_session, "Billing migration", 1)
    org_id = org.id
    customer_id = "cus_" + uuid4().hex
    org.stripe_customer_id = customer_id
    await pg_session.commit()
    monkeypatch.setattr(billing.settings, "STRIPE_PRICE_ID_TIER1", "price_migration")
    active = ProviderSubscription(
        id="sub_" + uuid4().hex,
        status="active",
        created=1,
        cancel_at_period_end=False,
        items={
            "data": [
                {
                    "price": {"id": "price_migration"},
                    "quantity": 3,
                    "current_period_start": 1700000000,
                    "current_period_end": 1800000000,
                }
            ]
        },
    )
    canceled = ProviderSubscription(id="sub_" + uuid4().hex, status="canceled", created=2)

    def subscriptions(**parameters):
        assert parameters == {"customer": customer_id, "limit": 10, "status": "all"}
        return SimpleNamespace(data=[canceled, active])

    monkeypatch.setattr(billing.stripe_gateway, "list_subscriptions", subscriptions)
    assert await billing.sync_stripe_to_postgres(customer_id, pg_session) == {
        "status": "active",
        "organization_id": org_id,
        "seats": 3,
    }
    first = await billing.get_subscription_by_org(org_id, pg_session)
    record_id = first.id
    active.items["data"][0]["quantity"] = 4
    await billing.sync_stripe_to_postgres(customer_id, pg_session)
    pg_session.expire_all()
    second = await billing.get_subscription_by_org(org_id, pg_session)
    assert second.id == record_id
    assert second.seats == 4
    assert second.stripe_subscription_id == active.id
    stored = await organizations.get_organization_by_id(pg_session, org_id)
    assert stored.max_users == 4
    assert stored.subscription_status == "active"


async def test_checkout_failure_preserves_already_committed_seat_change(pg_session, monkeypatch):
    org = await organizations.create_organization(pg_session, "Checkout failure", 1)
    org_id = org.id
    owner = str(uuid4())
    await organizations.add_user_to_organization(pg_session, org_id, owner, "owner")

    def fail(**parameters):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(billing.stripe_gateway, "create_customer", fail)
    with pytest.raises(HTTPException) as error:
        await billing.create_checkout_session(
            owner, "owner@example.com", org_id, 4, "First", "Last", "tier1", pg_session
        )
    assert error.value.status_code == 500
    assert "Failed to create Stripe customer" in error.value.detail
    pg_session.expire_all()
    stored = await organizations.get_organization_by_id(pg_session, org_id)
    assert stored.max_users == 4
    assert stored.subscription_status == "failed"
    assert stored.stripe_customer_id is None


async def test_usage_accumulates_for_one_user_and_empty_report_scope_stays_empty(pg_session):
    user_id = str(uuid4())
    await token_usage_service.record_usage(pg_session, user_id, 10, 20, 30)
    await token_usage_service.record_usage(pg_session, user_id, 3, 4, 7)
    pg_session.expire_all()
    usage = await token_usage_service.get_today_usage(pg_session, user_id)
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (13, 24, 37)
    assert await token_usage_service.get_today_usage(pg_session, str(uuid4())) is None
    assert await token_usage_service.get_usage_records(pg_session, date.today(), []) == []
    records = await token_usage_service.get_usage_records(pg_session, date.today(), [user_id])
    assert len(records) == 1
    assert records[0].total_tokens == 37
