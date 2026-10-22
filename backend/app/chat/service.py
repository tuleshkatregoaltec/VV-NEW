import json
import logging
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, List, Optional

from pydantic_ai import Agent as PydanticAgent
from pydantic_ai.messages import (
    FunctionToolResultEvent,
    ToolCallPart,
)
from pydantic_ai.models.openrouter import OpenRouterModelSettings
from sqlmodel.ext.asyncio.session import AsyncSession

from app.chat import repository
from app.chat.agent import _convert_to_pydantic_messages, agent
from app.chat.artifacts import _artifact_shell, _fetch_artifact
from app.chat.events import encode_sse as _sse
from app.chat.models import Conversation, Message
from app.token_usage.service import token_usage_service

logger = logging.getLogger(__name__)


async def create_conversation(
    db: AsyncSession, user_id: str, organization_id: str, title: Optional[str] = None
) -> Conversation:
    """Create a new conversation"""
    now = datetime.now(timezone.utc)
    conversation = Conversation(
        user_id=user_id,
        organization_id=organization_id,
        title=title or "New Conversation",
        created_at=now,
        updated_at=now,
    )
    repository.save_conversation(db, conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def get_user_conversations(
    db: AsyncSession, user_id: str, organization_id: str, limit: int = 50, offset: int = 0
) -> List[Conversation]:
    return await repository.get_user_conversations(db, user_id, organization_id, limit, offset)


async def get_conversation(
    db: AsyncSession, conversation_id: int, user_id: str, organization_id: str
) -> Optional[Conversation]:
    return await repository.get_conversation(db, conversation_id, user_id, organization_id)


async def update_conversation(
    db: AsyncSession, conversation_id: int, user_id: str, organization_id: str, title: str
) -> Optional[Conversation]:
    """Update a conversation's title"""
    conversation = await get_conversation(db, conversation_id, user_id, organization_id)
    if not conversation:
        return None

    conversation.title = title
    conversation.updated_at = datetime.now(timezone.utc)
    repository.save_conversation(db, conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation


async def delete_conversation(
    db: AsyncSession, conversation_id: int, user_id: str, organization_id: str
) -> bool:
    """Delete a conversation and all its messages"""
    conversation = await get_conversation(db, conversation_id, user_id, organization_id)
    if not conversation:
        return False

    await repository.delete_conversation_records(db, conversation_id, user_id, organization_id)
    await db.commit()
    return True


async def get_conversation_messages(
    db: AsyncSession, conversation_id: int, user_id: str, organization_id: str, limit: int = 100
) -> List[Message]:
    return await repository.get_conversation_messages(
        db, conversation_id, user_id, organization_id, limit
    )


async def add_message(
    db: AsyncSession,
    conversation_id: int,
    role: str,
    content: str,
    meta_data: Optional[dict[str, Any]] = None,
    *,
    user_id: str,
    organization_id: str,
) -> Message:
    """Add a message to a conversation the caller owns; nothing is written otherwise."""
    conversation = await get_conversation(db, conversation_id, user_id, organization_id)
    if conversation is None:
        raise LookupError("Conversation not found")

    message = Message(
        conversation_id=conversation_id,
        role=role,
        content=content,
        meta_data=meta_data or {},
        created_at=datetime.now(timezone.utc),
    )
    repository.save_message(db, message)

    # Update conversation timestamp
    conversation.updated_at = datetime.now(timezone.utc)
    # Auto-generate title from first user message
    if not conversation.title or conversation.title == "New Conversation":
        conversation.title = content[:50] + ("..." if len(content) > 50 else "")

    await db.commit()
    await db.refresh(message)
    return message


async def stream_chat_response(
    db: AsyncSession,
    conversation_id: int,
    user_message: str,
    user_id: str,
    organization_id: str,
) -> AsyncGenerator[str, None]:
    """Stream chat response using Pydantic AI agent via SSE"""
    # Verify conversation ownership
    conversation = await get_conversation(db, conversation_id, user_id, organization_id)
    if not conversation:
        yield f"data: {json.dumps({'type': 'error', 'error': 'Conversation not found'})}\n\n"
        return
    scope = {"user_id": user_id, "organization_id": organization_id}

    # Save user message
    await add_message(db, conversation_id, "user", user_message, **scope)

    # Get conversation history
    messages = await get_conversation_messages(db, conversation_id, **scope)
    message_history = _convert_to_pydantic_messages(messages[:-1])  # Exclude the just-added message

    assistant_message = ""
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0
    artifacts: list[dict[str, Any]] = []
    tool_names_by_call_id: dict[str, str] = {}

    try:
        yield _sse({"type": "thinking"})

        async with agent.iter(
            user_message,
            message_history=message_history,
            model_settings=OpenRouterModelSettings(openrouter_reasoning={"effort": "low"}),
        ) as run:
            async for node in run:
                if PydanticAgent.is_model_request_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for chunk_text in stream.stream_text(delta=True, debounce_by=0.01):
                            assistant_message += chunk_text
                            yield _sse({"type": "content", "content": chunk_text})
                elif PydanticAgent.is_call_tools_node(node):
                    for part in node.model_response.parts:
                        if not isinstance(part, ToolCallPart):
                            continue
                        tool_names_by_call_id[part.tool_call_id] = part.tool_name
                        yield _sse(
                            {
                                "type": "tool_call_start",
                                "tool": part.tool_name,
                                "tool_call_id": part.tool_call_id,
                            }
                        )
                        if part.tool_name in {"show_listings", "show_market_trend", "show_chart"}:
                            yield _sse(
                                {
                                    "type": "artifact_opened",
                                    "artifact": _artifact_shell(
                                        part.tool_name, part.tool_call_id, part.args_as_dict()
                                    ),
                                }
                            )
                        artifact = await _fetch_artifact(
                            db,
                            part.tool_name,
                            part.tool_call_id,
                            part.args_as_dict(),
                            user_message,
                        )
                        if artifact is not None:
                            artifacts = [item for item in artifacts if item["id"] != artifact["id"]]
                            artifacts.append(artifact)
                            yield _sse({"type": "artifact_updated", "artifact": artifact})

                    async with node.stream(run.ctx) as tool_stream:
                        async for event in tool_stream:
                            if isinstance(event, FunctionToolResultEvent):
                                tool_call_id = getattr(event.result, "tool_call_id", "")
                                tool_name = getattr(event.result, "tool_name", None)
                                yield _sse(
                                    {
                                        "type": "tool_call_result",
                                        "tool_call_id": tool_call_id,
                                        "tool": tool_name
                                        or tool_names_by_call_id.get(tool_call_id, "tool"),
                                    }
                                )

            usage = run.usage()
            total_tokens = getattr(usage, "total_tokens", 0)
            prompt_tokens = getattr(usage, "request_tokens", 0)
            completion_tokens = getattr(usage, "response_tokens", 0)

            if total_tokens:
                logger.info(
                    f"Token usage: prompt={prompt_tokens}, "
                    f"completion={completion_tokens}, "
                    f"total={total_tokens}"
                )

                await token_usage_service.record_usage(
                    db=db,
                    user_id=user_id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                )

        # Save assistant message with metadata
        if assistant_message or artifacts:
            meta_data = {}
            if total_tokens:
                meta_data["total_tokens"] = total_tokens
            if artifacts:
                meta_data["artifacts"] = artifacts
            if not assistant_message:
                assistant_message = "I added the requested view below."

            await add_message(
                db, conversation_id, "assistant", assistant_message, meta_data, **scope
            )

        yield _sse({"type": "done"})

    except Exception as e:
        logger.exception("Error during chat streaming")
        yield _sse({"type": "error", "error": str(e)})
