import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.messages import ModelMessagesTypeAdapter, ToolCallPart
from pydantic_ai.usage import UsageLimits

from app.feasibility.chat_agent import (
    FeasibilityChatDeps,
    WorkspaceUnitMixReplacement,
    WorkspaceUnitMixUpdate,
    workspace_chat_agent,
)
from app.feasibility.models import (
    FeasibilityChatRequest,
    FeasibilityPaymentPlan,
)
from app.feasibility.research import fetch_chat_research
from app.plots.models import PlotDetails

logger = logging.getLogger(__name__)


async def stream_chat_events(request: FeasibilityChatRequest) -> AsyncGenerator[str, None]:
    try:
        async for chunk in stream_feasibility_response(
            request.messages,
            request.plot_data,
            request.assumptions,
            request.research,
            request.history_json,
        ):
            yield chunk
    except Exception:
        logger.exception("Error in feasibility chat stream service")
        yield f"data: {json.dumps({'type': 'error', 'error': 'Internal server error'})}\n\n"


_RESEARCH_TOOLS = {"show_transactions": "transactions", "show_analytics": "analytics"}

_MUTATION_TOOLS = {
    "update_prices": "set_price_per_sqm",
    "update_unit_mix": "update_unit_mix",
    "replace_unit_mix": "replace_unit_mix",
    "update_assumptions": "set_assumptions",
    "update_payment_plan": "set_payment_plan",
}


def _extract_payload(part: ToolCallPart) -> Any:
    args = part.args_as_dict()
    if part.tool_name == "update_prices":
        return args.get("prices", args)
    if part.tool_name == "update_unit_mix":
        raw = args.get("unit_mix", args)
        return [WorkspaceUnitMixUpdate(**row).model_dump(exclude_none=True) for row in raw]
    if part.tool_name == "replace_unit_mix":
        raw = args.get("unit_mix", args)
        return [WorkspaceUnitMixReplacement(**row).model_dump(exclude_none=True) for row in raw]
    if part.tool_name == "update_assumptions":
        return args.get("assumptions", args)
    if part.tool_name == "update_payment_plan":
        raw = args.get("payment_plan", args)
        return FeasibilityPaymentPlan(**raw).model_dump()
    return None


def _unit_type_keys_from_assumptions(assumptions: dict[str, Any]) -> list[str]:
    unit_configs = assumptions.get("unitConfigs")
    if not isinstance(unit_configs, list):
        return []

    keys: list[str] = []
    for row in unit_configs:
        if not isinstance(row, dict):
            continue
        unit_id = row.get("id")
        if isinstance(unit_id, str):
            keys.append(unit_id)
    return keys


async def stream_feasibility_response(
    messages: list[dict],
    plot_data: PlotDetails,
    assumptions: dict[str, Any],
    research: dict | None = None,
    history_json: str | None = None,
) -> AsyncGenerator[str, None]:
    deps = FeasibilityChatDeps(
        plot_number=plot_data.plot_number,
        area_name=plot_data.community_name,
        plot=plot_data.model_dump(mode="json"),
        study=research,
        assumptions=assumptions,
        unit_type_keys=_unit_type_keys_from_assumptions(assumptions),
    )

    history: list[Any] = []
    if history_json:
        try:
            history = ModelMessagesTypeAdapter.validate_json(history_json)
        except Exception:
            logger.exception("Failed to deserialize feasibility history_json")
            yield f"data: {json.dumps({'type': 'warning', 'content': 'Session context reset — conversation history could not be restored.'})}\n\n"

    user_turns = [message for message in messages if message.get("role") == "user"]
    user_message = user_turns[-1].get("content", "") if user_turns else ""

    try:
        async with workspace_chat_agent.iter(
            user_message,
            message_history=history,
            deps=deps,
            usage_limits=UsageLimits(request_limit=10),
        ) as run:
            async for node in run:
                if Agent.is_model_request_node(node):
                    async with node.stream(run.ctx) as stream:
                        async for chunk in stream.stream_text(delta=True, debounce_by=0.01):
                            yield f"data: {json.dumps({'type': 'text', 'content': chunk})}\n\n"
                elif Agent.is_call_tools_node(node):
                    for part in node.model_response.parts:
                        if not isinstance(part, ToolCallPart):
                            continue
                        if part.tool_name in _MUTATION_TOOLS:
                            payload = _extract_payload(part)
                            if payload is not None:
                                yield f"data: {json.dumps({'type': 'pending_action', 'action': _MUTATION_TOOLS[part.tool_name], 'payload': payload}, default=str)}\n\n"
                        elif part.tool_name in _RESEARCH_TOOLS:
                            payload = await fetch_chat_research(part.tool_name, part.args_as_dict())
                            if payload is not None:
                                yield f"data: {json.dumps({'type': 'show_research', 'tab': _RESEARCH_TOOLS[part.tool_name], 'data': payload}, default=str)}\n\n"
            new_messages_json = run.new_messages_json().decode()
            yield f"data: {json.dumps({'type': 'done', 'new_messages_json': new_messages_json})}\n\n"
    except Exception as exc:
        logger.exception("Error in feasibility chat stream")
        yield f"data: {json.dumps({'type': 'error', 'error': str(exc)})}\n\n"
