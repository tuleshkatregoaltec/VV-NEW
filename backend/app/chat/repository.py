from typing import List, Optional

from sqlalchemy import delete
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.chat.models import Conversation, Message

# Every statement is scoped to the trusted (user, organization) pair from AuthContext. A user
# who moves to another organization keeps the same user_id, so user_id alone is not a boundary.


def _owned_conversation_ids(conversation_id: int, user_id: str, organization_id: str):
    return select(Conversation.id).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
        Conversation.organization_id == organization_id,
    )


async def get_user_conversations(
    db: AsyncSession, user_id: str, organization_id: str, limit: int = 50, offset: int = 0
) -> List[Conversation]:
    """Get the user's conversations in the current organization"""
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == user_id, Conversation.organization_id == organization_id)
        .order_by(Conversation.updated_at.desc())
        .limit(limit)
        .offset(offset)
    )
    result = await db.exec(stmt)
    return list(result.all())


async def get_conversation(
    db: AsyncSession, conversation_id: int, user_id: str, organization_id: str
) -> Optional[Conversation]:
    """Get a conversation by ID (with auth check)"""
    stmt = select(Conversation).where(
        Conversation.id == conversation_id,
        Conversation.user_id == user_id,
        Conversation.organization_id == organization_id,
    )
    result = await db.exec(stmt)
    return result.first()


async def get_conversation_messages(
    db: AsyncSession, conversation_id: int, user_id: str, organization_id: str, limit: int = 100
) -> List[Message]:
    """Get messages for a conversation the caller owns"""
    stmt = (
        select(Message)
        .where(
            Message.conversation_id.in_(
                _owned_conversation_ids(conversation_id, user_id, organization_id)
            )
        )
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    result = await db.exec(stmt)
    return list(result.all())


def save_conversation(db: AsyncSession, conversation: Conversation):
    db.add(conversation)


def save_message(db: AsyncSession, message: Message):
    db.add(message)


async def delete_conversation_records(
    db: AsyncSession, conversation_id: int, user_id: str, organization_id: str
):
    # Messages are deleted only through the owned-conversation predicate, never by ID alone.
    await db.execute(
        delete(Message)
        .where(
            Message.conversation_id.in_(
                _owned_conversation_ids(conversation_id, user_id, organization_id)
            )
        )
        .execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(Conversation)
        .where(
            Conversation.id == conversation_id,
            Conversation.user_id == user_id,
            Conversation.organization_id == organization_id,
        )
        .execution_options(synchronize_session=False)
    )
