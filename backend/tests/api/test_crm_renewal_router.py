from datetime import date
from unittest.mock import AsyncMock

from app.crm.models import (
    CrmAreaPreferencesResponse,
    CrmRenewalLeadsResponse,
    CrmRenewalsResponse,
)


async def test_renewal_leads_are_scoped_to_the_signed_in_agent(client, monkeypatch):
    service = AsyncMock(
        return_value=CrmRenewalLeadsResponse(
            items=[],
            total=0,
            limit=200,
            offset=0,
            entered_today=0,
            preferences=CrmAreaPreferencesResponse(areas=[]),
            as_of=date(2026, 10, 9),
        )
    )
    monkeypatch.setattr("app.crm.router.list_renewal_leads", service)

    response = await client.get("/api/v1/crm/renewal-leads")

    assert response.status_code == 200
    assert service.await_args.kwargs["user_id"] == "test-user-id"
    assert service.await_args.kwargs["organization_id"] == "1"


async def test_forward_window_cannot_exceed_the_lease_dataset(client, monkeypatch):
    service = AsyncMock()
    monkeypatch.setattr("app.crm.router.update_area_preferences", service)

    # Leases are only loaded 90 days ahead; a longer window would silently show nothing extra.
    response = await client.put(
        "/api/v1/crm/area-preferences",
        json={"areas": ["Jumeirah Village Circle"], "forward_days": 91, "lookback_days": 30},
    )

    assert response.status_code == 422
    service.assert_not_awaited()


async def test_renewal_leads_require_an_active_subscription(client_no_auth):
    response = await client_no_auth.get("/api/v1/crm/renewal-leads")

    assert response.status_code in {401, 403}


async def test_renewals_tab_passes_its_filters_and_defaults_to_no_window(client, monkeypatch):
    service = AsyncMock(
        return_value=CrmRenewalsResponse(
            leads=[], total=0, limit=50, offset=0, as_of=date(2026, 10, 9)
        )
    )
    monkeypatch.setattr("app.crm.router.list_renewals", service)

    response = await client.get(
        "/api/v1/crm/renewals",
        params={"window": "vacant", "area": "Jumeirah Village Circle", "property_type": "Villa"},
    )

    assert response.status_code == 200
    kwargs = service.call_args.kwargs
    assert (kwargs["window"], kwargs["area"], kwargs["property_type"]) == (
        "vacant",
        "Jumeirah Village Circle",
        "Villa",
    )


async def test_renewals_tab_rejects_an_unknown_window(client):
    response = await client.get("/api/v1/crm/renewals", params={"window": "last_decade"})

    assert response.status_code == 422
