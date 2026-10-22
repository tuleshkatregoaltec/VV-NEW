import json
import logging
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext

from app.core.openrouter import create_openrouter_model
from app.feasibility.models import FeasibilityPaymentPlan
from app.feasibility.normalization import Stage
from app.feasibility.research import (
    get_area_market_research,
    rank_plot_comparables,
)
from app.plots.evidence import get_land_sale_evidence
from app.plots.models import Usage
from app.plots.service import get_plot_details

logger = logging.getLogger(__name__)


@dataclass
class FeasibilityChatDeps:
    plot_number: str
    area_name: str
    plot: dict[str, Any]
    study: dict[str, Any] | None
    assumptions: dict[str, Any]
    unit_type_keys: list[str] = field(default_factory=list)


class WorkspaceUnitMixUpdate(BaseModel):
    type: str
    count: int = Field(ge=0)
    avgSizeSqm: float | None = Field(default=None, gt=0)
    price_sqm: float | None = Field(default=None, gt=0)
    parkingRatio: float | None = Field(default=None, ge=0)


class WorkspaceUnitMixReplacement(BaseModel):
    type: str
    count: int = Field(ge=0)
    avgSizeSqm: float = Field(gt=0)
    price_sqm: float | None = Field(default=None, gt=0)
    parkingRatio: float | None = Field(default=None, ge=0)


model = create_openrouter_model()

workspace_chat_agent = Agent(
    model,
    name="feasibility_workspace",
    deps_type=FeasibilityChatDeps,
    model_settings={"temperature": 0.1},
    system_prompt=(
        "You are a Dubai real estate feasibility workspace assistant.\n\n"
        "The frontend owns the live workbook. You receive the current assumptions and workbook state each turn.\n"
        "Use tools for precise context or research, and use mutation tools when you want to suggest changes.\n"
        "For unit mix changes, use replace_unit_mix when proposing a new complete target mix; omitted unit "
        "types will be removed. Use update_unit_mix only for partial edits to listed existing unit types.\n"
        "Keep suggestions compact, practical, and evidence-led.\n"
        "When you call a mutation tool, do not write a long rationale. Use at most two short sentences "
        "that explain what the proposed change is and why it is being staged for approval."
    ),
)


@workspace_chat_agent.system_prompt
async def inject_workspace_context(ctx: RunContext[FeasibilityChatDeps]) -> str:
    return (
        "## Current feasibility context\n"
        f"Plot number: {ctx.deps.plot_number}\n"
        f"Area: {ctx.deps.area_name}\n\n"
        "### Plot\n"
        f"{json.dumps(ctx.deps.plot, indent=2)}\n\n"
        "### HBU study\n"
        f"{json.dumps(ctx.deps.study, indent=2)}\n\n"
        "### Current assumptions\n"
        f"{json.dumps(ctx.deps.assumptions, indent=2)}\n"
    )


@workspace_chat_agent.tool
async def get_current_plot(ctx: RunContext[FeasibilityChatDeps]) -> dict[str, Any]:
    return {"success": True, "plot": ctx.deps.plot}


@workspace_chat_agent.tool_plain
async def get_area_market_data(
    area: str, usage: Usage = "residential", stage: Stage = "existing", days: int = 365
) -> dict[str, Any]:
    return await get_area_market_research(area, usage, stage, days)


@workspace_chat_agent.tool
async def rank_pricing_comparables(
    ctx: RunContext[FeasibilityChatDeps], usage: Usage = "residential", limit: int = 5
) -> dict[str, Any]:
    details = await get_plot_details(ctx.deps.plot_number)
    if details is None:
        return {"success": False, "error": "Current plot was not found."}
    rows = await rank_plot_comparables(details, usage=usage, comparison_type="pricing", limit=limit)
    return {"success": True, "comparables": [row.model_dump() for row in rows]}


@workspace_chat_agent.tool
async def rank_pipeline_comparables(
    ctx: RunContext[FeasibilityChatDeps], usage: Usage = "residential", limit: int = 5
) -> dict[str, Any]:
    details = await get_plot_details(ctx.deps.plot_number)
    if details is None:
        return {"success": False, "error": "Current plot was not found."}
    rows = await rank_plot_comparables(
        details, usage=usage, comparison_type="pipeline", limit=limit
    )
    return {"success": True, "comparables": [row.model_dump() for row in rows]}


@workspace_chat_agent.tool
async def get_plot_land_sales(
    ctx: RunContext[FeasibilityChatDeps], days: int = 1825, limit: int = 12
) -> dict[str, Any]:
    details = await get_plot_details(ctx.deps.plot_number)
    if details is None:
        return {"success": False, "error": "Current plot was not found."}
    rows = await get_land_sale_evidence(
        area_name=details.community_name,
        plot_area_sqm=details.plot_area_sqm,
        days=days,
        limit=limit,
    )
    return {"success": True, "land_sales": [row.model_dump() for row in rows]}


@workspace_chat_agent.tool_plain
async def show_transactions(
    area: str,
    unit_type: str = "",
    usage: Usage = "residential",
    stage: Stage = "existing",
    days: int = 365,
    limit: int = 50,
) -> str:
    subject = (
        f"recent {unit_type} transactions in {area}"
        if unit_type
        else f"recent transactions in {area}"
    )
    return f"Pulling up {subject} ({usage}, {stage}, last {days} days, up to {limit}) in the Research panel."


@workspace_chat_agent.tool_plain
async def show_analytics(
    area: str = "",
    project_name: str = "",
    usage: Usage = "residential",
    stage: Stage = "existing",
    days: int = 365,
) -> str:
    subject = project_name or area or "current area"
    return f"Pulling up price analytics for {subject} ({usage}, {stage}, last {days} days) in the Research panel."


@workspace_chat_agent.tool
async def update_prices(ctx: RunContext[FeasibilityChatDeps], prices: dict[str, float]) -> str:
    if ctx.deps.unit_type_keys:
        invalid = [key for key in prices if key not in ctx.deps.unit_type_keys]
        if invalid:
            return f"Invalid unit type keys. Valid keys are: {json.dumps(ctx.deps.unit_type_keys)}."
    return f"Proposed price update for {len(prices)} unit type(s). Awaiting user approval."


@workspace_chat_agent.tool_plain
async def update_unit_mix(unit_mix: list[WorkspaceUnitMixUpdate]) -> str:
    """Patch only the listed unit types, keeping unmentioned unit types unchanged."""
    return f"Proposed partial unit mix update for {len(unit_mix)} unit type(s). Awaiting user approval."


@workspace_chat_agent.tool_plain
async def replace_unit_mix(unit_mix: list[WorkspaceUnitMixReplacement]) -> str:
    """Replace the full unit mix with exactly the listed unit types."""
    return f"Proposed full unit mix replacement with {len(unit_mix)} unit type(s). Awaiting user approval."


@workspace_chat_agent.tool_plain
async def update_assumptions(assumptions: dict[str, Any]) -> str:
    return (
        f"Proposed assumption update for {len(assumptions)} parameter(s). Awaiting user approval."
    )


@workspace_chat_agent.tool_plain
async def update_payment_plan(payment_plan: FeasibilityPaymentPlan) -> str:
    return f"Proposed payment plan update: deposit {payment_plan.depositPct}%, construction {payment_plan.constructionPct}%, handover {payment_plan.handoverPct}%. Awaiting user approval."
