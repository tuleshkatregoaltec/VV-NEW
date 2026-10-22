from unittest.mock import AsyncMock

from app.billing import service


async def test_webhook_dispatch_keeps_tracked_event_set_and_customer_requirement(monkeypatch):
    sync = AsyncMock()
    monkeypatch.setattr(service, "sync_stripe_to_postgres", sync)
    db = object()
    for event_type in service.TRACKED_EVENTS:
        await service.process_webhook_event(
            {"type": event_type, "data": {"object": {"customer": "cus_1"}}}, db
        )
    assert sync.await_count == 7
    sync.assert_awaited_with("cus_1", db)
    sync.reset_mock()
    await service.process_webhook_event({"type": "invoice.paid", "data": {"object": {}}}, db)
    await service.process_webhook_event({"type": "untracked"}, db)
    sync.assert_not_awaited()
