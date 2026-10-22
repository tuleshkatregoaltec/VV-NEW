from uuid import uuid4

import pytest
from sqlalchemy import func
from sqlmodel import select

from app.chat.models import Conversation, Message
from app.chat.service import (
    add_message,
    create_conversation,
    delete_conversation,
    get_conversation,
    get_conversation_messages,
    get_user_conversations,
    stream_chat_response,
    update_conversation,
)


async def test_conversation_ownership_and_message_json_survive_real_commits(pg_session):
    owner = str(uuid4())
    organization = str(uuid4())
    scope = {"user_id": owner, "organization_id": organization}
    conversation = await create_conversation(pg_session, owner, organization)
    conversation_id = conversation.id
    payload = {
        "total_tokens": 12,
        "artifacts": [
            {"id": "tool-1", "type": "chart", "data": {"points": [], "legacy": {"x": 1}}}
        ],
    }
    await add_message(pg_session, conversation_id, "user", "Preserve this conversation", **scope)
    await add_message(pg_session, conversation_id, "assistant", "Result", payload, **scope)
    pg_session.expire_all()
    messages = await get_conversation_messages(pg_session, conversation_id, **scope)
    assert [item.role for item in messages] == ["user", "assistant"]
    assert messages[1].meta_data == payload
    assert await get_conversation(pg_session, conversation_id, "another-user", organization) is None
    assert (
        await delete_conversation(pg_session, conversation_id, "another-user", organization)
        is False
    )
    assert await delete_conversation(pg_session, conversation_id, owner, organization) is True
    assert await get_conversation_messages(pg_session, conversation_id, **scope) == []


async def _count_messages(session, conversation_id):
    statement = (
        select(func.count()).select_from(Message).where(Message.conversation_id == conversation_id)
    )
    return (await session.exec(statement)).one()


async def _snapshot(session, conversation_id):
    session.expire_all()
    conversation = await session.get(Conversation, conversation_id)
    return (
        conversation.title,
        conversation.updated_at,
        await _count_messages(session, conversation_id),
    )


async def test_conversations_are_invisible_outside_their_user_and_organization(pg_session):
    """A transferred user (same user_id, new organization), a colleague in the same
    organization and a forged conversation ID are all rejected without side effects."""
    owner, colleague = str(uuid4()), str(uuid4())
    old_org, new_org = str(uuid4()), str(uuid4())
    conversation = await create_conversation(pg_session, owner, old_org, title="Old org chat")
    conversation_id = conversation.id
    await add_message(
        pg_session, conversation_id, "user", "private", user_id=owner, organization_id=old_org
    )
    before = await _snapshot(pg_session, conversation_id)

    rejected = [
        (owner, new_org),  # transferred to another organization
        (colleague, old_org),  # same organization, different user
        (colleague, new_org),
    ]
    for user_id, organization_id in rejected:
        assert await get_conversation(pg_session, conversation_id, user_id, organization_id) is None
        assert await get_user_conversations(pg_session, user_id, organization_id) == []
        assert (
            await get_conversation_messages(pg_session, conversation_id, user_id, organization_id)
            == []
        )
        assert (
            await update_conversation(
                pg_session, conversation_id, user_id, organization_id, "hijacked"
            )
            is None
        )
        assert (
            await delete_conversation(pg_session, conversation_id, user_id, organization_id)
            is False
        )
        with pytest.raises(LookupError):
            await add_message(
                pg_session,
                conversation_id,
                "user",
                "injected",
                user_id=user_id,
                organization_id=organization_id,
            )
        events = [
            event
            async for event in stream_chat_response(
                pg_session, conversation_id, "injected", user_id, organization_id
            )
        ]
        assert events == ['data: {"type": "error", "error": "Conversation not found"}\n\n']
        assert await _snapshot(pg_session, conversation_id) == before

    # A forged ID owned by nobody is rejected the same way.
    assert await get_conversation(pg_session, conversation_id + 10_000, owner, old_org) is None

    # The owner in the original organization keeps full access.
    listed = await get_user_conversations(pg_session, owner, old_org)
    assert [item.id for item in listed] == [conversation_id]
    assert await delete_conversation(pg_session, conversation_id, owner, old_org) is True
    assert await _count_messages(pg_session, conversation_id) == 0
