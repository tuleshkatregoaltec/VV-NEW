import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.chat import service


def decode(events):
    return [json.loads(event.removeprefix("data: ").strip()) for event in events]


async def test_missing_conversation_emits_only_error(monkeypatch):
    lookup = AsyncMock(return_value=None)
    save = AsyncMock()
    monkeypatch.setattr(service, "get_conversation", lookup)
    monkeypatch.setattr(service, "add_message", save)
    events = [
        event async for event in service.stream_chat_response(None, 3, "hello", "other", "org")
    ]
    assert decode(events) == [{"type": "error", "error": "Conversation not found"}]
    save.assert_not_awaited()


async def test_provider_failure_keeps_user_message_and_emits_no_done(monkeypatch):
    monkeypatch.setattr(service, "get_conversation", AsyncMock(return_value=SimpleNamespace(id=3)))
    save = AsyncMock()
    monkeypatch.setattr(service, "add_message", save)
    monkeypatch.setattr(service, "get_conversation_messages", AsyncMock(return_value=[]))

    @asynccontextmanager
    async def fail(*args, **kwargs):
        raise RuntimeError("provider unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(service, "agent", SimpleNamespace(iter=fail))
    events = [
        event async for event in service.stream_chat_response(None, 3, "hello", "owner", "org")
    ]
    assert decode(events) == [
        {"type": "thinking"},
        {"type": "error", "error": "provider unavailable"},
    ]
    save.assert_awaited_once_with(None, 3, "user", "hello", user_id="owner", organization_id="org")


async def test_text_turn_records_usage_before_assistant_and_done(monkeypatch):
    from pydantic_ai.models.test import TestModel

    monkeypatch.setattr(service, "get_conversation", AsyncMock(return_value=SimpleNamespace(id=3)))
    monkeypatch.setattr(service, "get_conversation_messages", AsyncMock(return_value=[]))
    actions = []

    async def save(db, conversation_id, role, content, meta_data=None, **scope):
        actions.append((role, content, meta_data))

    async def usage(**kwargs):
        actions.append(("usage", kwargs["total_tokens"]))

    monkeypatch.setattr(service, "add_message", save)
    monkeypatch.setattr(service.token_usage_service, "record_usage", usage)
    with service.agent.override(model=TestModel(call_tools=[], custom_output_text="Known answer")):
        events = [
            event async for event in service.stream_chat_response(None, 3, "hello", "owner", "org")
        ]
    decoded = decode(events)
    assert decoded[0] == {"type": "thinking"}
    assert (
        "".join(item["content"] for item in decoded if item["type"] == "content") == "Known answer"
    )
    assert decoded[-1] == {"type": "done"}
    assert [action[0] for action in actions] == ["user", "usage", "assistant"]
    assert actions[2][1] == "Known answer"
    assert actions[2][2] == {"total_tokens": actions[1][1]}
