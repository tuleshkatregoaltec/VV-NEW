import logging
from dataclasses import dataclass

from pydantic import BaseModel, Field
from pydantic_ai import Agent, RunContext
from pydantic_ai.usage import UsageLimits

from app.area.service import get_area_detail
from app.core.openrouter import create_openrouter_model
from app.feasibility.models import (
    DevelopmentModel,
    FeasibilityAssumptions,
    RentalComparableProject,
    RentalMarketSummary,
)
from app.feasibility.research import (
    get_market_pricing,
    get_off_plan_pricing_curve,
    rank_plot_comparables,
)
from app.plots.evidence import get_land_sale_evidence
from app.plots.models import PlotDetails, Usage
from app.project.service import get_project_market_snapshot

logger = logging.getLogger(__name__)


@dataclass
class HBUSeedDeps:
    plot_number: str
    plot_details: PlotDetails
    market_area: str
    scaffold: FeasibilityAssumptions
    development_model: DevelopmentModel
    usage_override: Usage | None = None


class HBUSeedDraft(BaseModel):
    headline: str
    summary: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    assumptions: FeasibilityAssumptions


def _tool_usage(ctx: RunContext[HBUSeedDeps], usage: Usage) -> Usage:
    return (
        usage
        if usage != "unknown"
        else ctx.deps.usage_override or ctx.deps.plot_details.inferred_usage
    )


model = create_openrouter_model()

_SYSTEM_PROMPT = """\
You are a Dubai real estate development advisor producing an initial highest-and-best-use (HBU) \
feasibility study for a specific GIS plot. Your output seeds a developer's financial workbook.

The plot plot data and a default assumption scaffold are included in the user message. \
Use them as your starting point — you do not need tools to retrieve them.

First inspect `development_model` in the user message. The underwriting path depends on it:
  • `build_to_sell_residential` means BTS and uses sales evidence.
  • `build_to_rent_residential` means BTR and uses rental evidence.
These models are separate. Never reuse BTS pricing logic for BTR.

━━━ PHASE 1 — RESEARCH ━━━
If the model is BTS, call these tools in one parallel batch:
  • get_existing_market_pricing   — secondary-market median prices by unit type (pricing floor)
  • get_off_plan_market_pricing   — off-plan prices segmented by handover horizon bucket (primary)
  • get_pricing_comparables       — completed/selling projects ranked by similarity to this plot
  • get_pipeline_comparables      — planned/under-construction projects in the same area (supply risk)
  • get_plot_land_sales           — recent land transactions near this plot size (land cost anchor)

If the model is BTR, call these tools in one parallel batch:
  • get_area_rental_market        — master-community rental averages and gross yield context
  • get_rental_comparables        — rental comps with achieved rent psqm and annual rents
  • get_pipeline_comparables      — planned/under-construction projects in the same area (supply risk)
  • get_plot_land_sales           — recent land transactions near this plot size (land cost anchor)

━━━ PHASE 2 — FORM ASSUMPTIONS ━━━
Using the research evidence, decide on the market and product assumptions for this plot. Use the \
scaffold as context, but focus your judgement on land cost, hard-cost rate, unit mix, sales rates, \
and BTR rents where applicable. Use the massing scaffold for target coverage, building count, and \
efficient floorplate unless plot or comparable scale supports a better physical test-fit. The workbook \
will control deterministic mechanics such as financing, fees, VAT, payment plan, and BTR operating \
defaults after your draft.

Key things to reason through:
  • Land cost: anchor to comparable land sales AND the per-GFA-sqm sanity check. The LAND COST ANCHOR
    section shows both AED/plot-sqm (from DLD data) and AED/GFA-sqm (normalised to this plot's FAR).
    Cross-check both: if the DLD evidence implies a per-GFA-sqm rate far below Dubai norms, weight
    towards the Dubai market reference range instead. Typical Dubai residential land cost ranges
    (AED per sqft of max GFA, as a market reality check — use the relevant bracket for this location):
      Downtown / DIFC / Palm Jumeirah: AED 500–900/sqft GFA
      Business Bay / JLT / JBR / Marina: AED 300–500/sqft GFA
      Dubai Hills / City Walk / Bluewaters: AED 250–400/sqft GFA
      Jumeirah / Umm Suqeim / Al Barsha: AED 200–350/sqft GFA
      JVC / JVT / Dubai Silicon Oasis: AED 100–200/sqft GFA
      Meydan / MBR City / Dubai South: AED 120–250/sqft GFA
    If land evidence is absent or the implied per-GFA rate is below the bracket floor, use the floor
    as your anchor and flag it as a warning. Do not set landPricePsqm below the p25 DLD evidence floor.
  • Project tier and unit mix: match the scale and product type to what the location and GFA support.
    Design the unit mix to maximise GFA utilisation and appeal to the likely buyer profile.
    Consider supply pipeline when choosing unit types to avoid saturated segments.
  • BTS saleable efficiency: calculate GSA/GFA as sum(unitConfigs.units × avgSizeSqm) / maxGfaSqm.
    A normal Dubai apartment feasibility should be roughly 79-80%. It cannot be above 82%.
    Do not seed an 86%+ saleable/GFA ratio; reduce unit counts or increase average sizes instead.
  • For BTS selling rates: off-plan pricing is primary; existing stock sets a floor. Apply a conservative
    haircut to reflect launch uncertainty, absorption depth, and the number of similar units released.
    For large high-FAR schemes, the highest-volume unit types usually need a wider discount to the
    selected off-plan benchmark than boutique schemes; do not mechanically copy the benchmark.
  • For BTR rents: use the size-band rental rates in the market context as your primary anchor — each band
    gives you a per-sqm/yr rate matched to unit size. Convert to monthly rent per config using:
    monthlyRentAed = median_rent_psm_annual × avgSizeSqm / 12. Fall back to the blended area rate only
    if no band data exists. For a new, professionally managed purpose-built rental product, a modest
    5-15% premium to current-stock size-band medians can be reasonable when location and amenities
    support it. Do not use sales evidence, GDV, or gross sales to justify BTR pricing.
  • BTR unit mix labels: always use standard Dubai unit type names — "Studio", "1BR", "2BR", "3BR", "4BR+".
    Never use generic names like "Rental 1". Every btrUnitConfig must have a non-zero avgSizeSqm and
    a non-zero units count — configs with zero units or zero size are invalid. Match avgSizeSqm to the
    typical Dubai size for that type: Studio ~42 sqm, 1BR ~70 sqm, 2BR ~110 sqm, 3BR ~160 sqm.
  • Construction cost: use the usage/storey scaffold rate and construction-cost guidance as the base-case
    benchmark. Only adjust it when product complexity supports the adjustment. Do not inflate hard cost
    to premium/luxury levels just because the plot is in a high-value area.
  • Return sanity: do not force a result, but a normal initial residential BTS/BTR study should usually
    land with unlevered and levered IRR in the broad 0–30% range. If returns would be much higher, the
    usual problem is understated land or construction cost. If returns would be deeply negative, first
    check that construction cost was not set above the guidance without a clear reason; sale/rent pricing
    being too low is a less common cause.
  • Timeline: flag schedule or site-plan-expiry risks in warnings, but do not tune workbook schedule dates.
  • Funding for BTS: discuss funding risk in the summary/warnings when relevant. Do not tune debtFundingAed,
    equityFundingAed, debt spreads, LTV/LTC, fees, VAT, or payment plan.
  • Funding for BTR: think like CRE underwriting. Focus on stabilized occupancy, rental rate, NOI, exit cap,
    and permanent debt constraints in your narrative, but do not tune the workbook's BTR operating defaults,
    permanent debt defaults, hold period, debtFundingAed, or equityFundingAed.

━━━ PHASE 3 — SINGLE-PASS SANITY CHECK ━━━
Before returning, check the draft once:
  • Unit config saleable area should fit within the scaffold saleable-area target.
  • Land and construction assumptions should sit inside the broad scaffold ranges unless evidence explains why.
  • BTS unit pricing should be evidence-led and should not simply copy the highest off-plan benchmark.
  • BTR rents should be internally coherent by unit size and backed by size-band rental evidence.
  • Do not iterate with tools; return the best single-pass assumptions and flag unresolved risks in warnings.

━━━ OUTPUT ━━━
  headline:    one-line project description
  summary:     6–10 bullets — site, programme, pricing rationale, cost, return headline, key risks
  constraints: hard zoning/regulatory facts from the plot data
  warnings:    specific and actionable (not generic boilerplate)
  assumptions: final FeasibilityAssumptions

A loss-making result is acceptable to output — flag it clearly and name the sensitivity lever.
Never invent data. If evidence is absent, say so.
Zoning and GFA constraints from the plot data are absolute.\
"""

hbu_seed_agent = Agent(
    model,
    name="hbu_seed_agent",
    deps_type=HBUSeedDeps,
    output_type=HBUSeedDraft,
    model_settings={"temperature": 0.1},
    system_prompt=_SYSTEM_PROMPT,
)

_BTS_PREFETCHED_SYSTEM_PROMPT = """\
You are a Dubai real estate development advisor producing an initial build-to-sell (BTS) \
feasibility study for a specific GIS plot. Your output seeds a developer's financial workbook.

Use only the plot dossier, BTS market context, land cost anchor, construction cost anchor, and \
default assumption scaffold included in the user message. Do not call tools; all required BTS \
context has already been prefetched before this run.

This is a Build-to-Sell residential model:
  • Use off-plan pricing as the primary sales-rate anchor.
  • Use existing-stock pricing as a floor and sanity check.
  • Use comparable projects to calibrate product tier, unit mix, and supply risk.
  • Anchor land cost to comparable land sales and the per-GFA-sqm sanity check.
  • Set a realistic residential unit mix that fits the max GFA and target buyer profile.
    Calculate GSA/GFA as sum(unitConfigs.units × avgSizeSqm) / maxGfaSqm. It should usually be
    roughly 79-80% and must never exceed 82%. Do not seed an 86%+ saleable/GFA ratio.
  • Use the massing scaffold for targetCoveragePct, targetBuildingCount, floor count, and efficient
    floorplate assumptions. These are deterministic physical defaults; focus your judgement on unit
    mix, pricing, land, and construction rate.
  • Set the construction hard-cost rate from the construction-cost guidance. Treat the scaffold benchmark
    as the base case, not a placeholder; use the upper normal range for high-FAR, unlimited-height,
    deep-basement, heavy-podium, or otherwise complex tower schemes.
  • Selling rates should usually sit below the selected off-plan benchmark for a first-pass feasibility
    seed. A 5-10% launch/absorption haircut is normal for smaller schemes. For very large high-FAR
    schemes, the highest-volume unit types often need roughly an 8-12% haircut to the relevant
    off-plan benchmark, while still checking that pricing remains above existing-stock medians when a
    new-build premium is justified. Do not haircut every unit type mechanically.
  • Treat scaffold landPricePsqm as the bounded base-case anchor. For very high-FAR or unlimited-height
    plots with sparse/conflicting land evidence, use the upper half of the Dubai GFA land band.
  • Return sanity: do not force a result, but a normal initial residential BTS study should usually land
    with project and equity IRR in the broad 0–30% range. If returns are too high, revisit land cost and
    hard cost. If returns are too low, first check whether hard cost was set above the guidance without
    a clear project-specific reason; do not inflate selling rates without evidence.
  • Do not tune finance spreads, LTV/LTC, fees, VAT, payment plan, schedule dates, or debt/equity sizing;
    deterministic workbook defaults will control those after your draft.

Return:
  headline:    one-line project description
  summary:     6-10 bullets covering site, programme, pricing rationale, cost, returns, and risks
  constraints: hard zoning/regulatory facts from the plot data
  warnings:    specific actionable risks, including missing evidence or weak assumptions
  assumptions: final FeasibilityAssumptions

A weak or loss-making result is acceptable. Flag it clearly and identify the sensitivity lever.
Never invent evidence. If evidence is absent, say so. Zoning and GFA constraints from plot data \
are absolute.\
"""

hbu_seed_agent_bts_prefetched = Agent(
    model,
    name="hbu_seed_agent_bts_prefetched",
    deps_type=HBUSeedDeps,
    output_type=HBUSeedDraft,
    model_settings={"temperature": 0.1},
    system_prompt=_BTS_PREFETCHED_SYSTEM_PROMPT,
)

_BTR_SYSTEM_PROMPT = """\
You are a Dubai CRE build-to-rent underwriting advisor producing an initial HBU feasibility study \
for a specific GIS plot.

Use only the plot dossier, BTR rental market context, land cost anchor, construction cost anchor, \
and default assumption scaffold included in the user message. Do not call tools; all required BTR \
context has already been prefetched before this run.

This is a Build-to-Rent model, not Build-to-Sell:
  • Do not use off-plan sales pricing, GDV, presales, or escrow logic to justify rental assumptions.
  • Anchor rents to the rental size-band evidence when available. The size-band median often reflects
    heterogeneous current stock; for a new, professionally managed purpose-built rental product, use a
    modest 5-15% premium where location, amenities, and comparable quality support it.
  • Every `btrUnitConfig` must use standard Dubai labels: "Studio", "1BR", "2BR", "3BR", "4BR+".
  • Every `btrUnitConfig` must have non-zero units, non-zero avgSizeSqm, and non-zero monthlyRentAed.
  • Keep `unitConfigs` aligned with `btrUnitConfigs` for area and parking math, but do not assign sales rates.
    The sum of units × avgSizeSqm should be close to the scaffold saleable-area target, ideally
    about 95-102% of max GFA × NSA efficiency, and must not materially exceed that target.
  • Use the massing scaffold for targetCoveragePct, targetBuildingCount, floor count, and efficient
    floorplate assumptions. These are deterministic physical defaults; focus your judgement on unit
    mix, rents, land, and construction rate.
  • Set the construction hard-cost rate from the construction-cost guidance. Treat the scaffold benchmark
    as the base case, not a placeholder; use the upper normal range for high-FAR, unlimited-height,
    deep-basement, heavy-podium, or otherwise complex tower schemes.
  • Treat scaffold landPricePsqm as the bounded base-case anchor. For very high-FAR or unlimited-height
    plots with sparse/conflicting land evidence, use the upper half of the Dubai GFA land band.
  • Focus your judgement on unit mix and monthly rent. Discuss occupancy, OpEx, cap rates, permanent debt,
    and hold-period risks in the narrative, but deterministic workbook defaults will control those inputs.
  • Return sanity: do not force a result, but a normal initial residential BTR study should usually land
    with unlevered and levered IRR in the broad 0–30% range. If returns are too high, the likely issue is
    understated land or construction cost. If returns are deeply negative after land and hard cost are
    inside guidance, rents are probably too conservative for a new managed product.

Return:
  headline:    one-line project description
  summary:     6–10 bullets covering site, rental programme, rent evidence, cost, financing/returns, and risks
  constraints: hard zoning/regulatory facts from the plot data
  warnings:    specific actionable risks, including missing evidence or weak assumptions
  assumptions: final FeasibilityAssumptions

A weak or loss-making result is acceptable. Flag it clearly and identify the sensitivity lever.
Never invent evidence. If evidence is absent, say so. Zoning and GFA constraints from plot data are absolute.\
"""

hbu_seed_agent_btr = Agent(
    model,
    name="hbu_seed_agent_btr",
    deps_type=HBUSeedDeps,
    output_type=HBUSeedDraft,
    model_settings={"temperature": 0.1},
    system_prompt=_BTR_SYSTEM_PROMPT,
)


@hbu_seed_agent.tool
async def get_existing_market_pricing(
    ctx: RunContext[HBUSeedDeps], usage: Usage = "unknown"
) -> list[dict]:
    """Secondary-market (existing stock) median transaction prices by unit type for this plot's area.

    Returns: list of {unit_type, transaction_count, median_price_sqm, avg_area_sqm}.
    Use as a pricing floor — existing stock typically trades below off-plan launch prices.
    Returns an empty list if no data is available for this area.
    """
    rows = await get_market_pricing(
        ctx.deps.market_area,
        _tool_usage(ctx, usage),
        stage="existing",
    )
    return [row.model_dump() for row in rows]


@hbu_seed_agent.tool
async def get_off_plan_market_pricing(
    ctx: RunContext[HBUSeedDeps], usage: Usage = "unknown"
) -> list[dict]:
    """Off-plan transaction prices segmented by unit type and months-to-handover horizon bucket.

    Returns: list of {unit_type, horizon_bucket, avg_months_to_completion, transaction_count,
    median_price_sqm, avg_area_sqm, last_transaction_date}.
    Select the bucket whose avg_months_to_completion is closest to your projected handover horizon.
    This is the primary source for selling rate assumptions.
    Returns an empty list if no off-plan data is available for this area.
    """
    rows = await get_off_plan_pricing_curve(
        ctx.deps.market_area,
        _tool_usage(ctx, usage),
        ctx.deps.scaffold.handoverDate,
    )
    return [row.model_dump() for row in rows]


@hbu_seed_agent.tool
async def get_area_rental_market(ctx: RunContext[HBUSeedDeps]) -> dict:
    """Master-community rental averages for the selected plot area.

    Returns: {area_name, rental_contract_count_12m, median_annual_rent, avg_rent_price_sqm_12m,
    gross_yield_pct, top_developers, top_master_projects}.
    Use avg_rent_price_sqm_12m as the primary BTR rent anchor, then apply judgement from the
    comparable projects and plot positioning.
    """
    try:
        detail = await get_area_detail(ctx.deps.market_area)
    except ValueError:
        return {}

    return RentalMarketSummary(
        area_name=detail.area_name,
        rental_contract_count_12m=detail.rental_contract_count_12m,
        median_annual_rent=detail.median_annual_rent,
        avg_rent_price_sqm_12m=detail.avg_rent_price_sqm_12m,
        gross_yield_pct=detail.gross_yield_pct,
        top_developers=detail.top_developers,
        top_master_projects=detail.top_master_projects,
    ).model_dump()


@hbu_seed_agent.tool
async def get_rental_comparables(
    ctx: RunContext[HBUSeedDeps], usage: Usage = "unknown", limit: int = 6
) -> list[dict]:
    """Rental comparables ranked by similarity, enriched with achieved rent and yield metrics.

    Returns project name, score, achieved avg rent psqm, median annual rent, gross yield, and
    rental contract count. Use these comps to sanity-check the community rental average and decide
    a conservative BTR rental rate.
    """
    ranked = await rank_plot_comparables(
        ctx.deps.plot_details,
        _tool_usage(ctx, usage),
        "pricing",
        limit,
    )

    enriched: list[RentalComparableProject] = []
    for row in ranked:
        snapshot = await get_project_market_snapshot(row.project_id)
        enriched.append(
            RentalComparableProject(
                project_id=row.project_id,
                project_name=row.project_name,
                area_name=snapshot.area_name if snapshot else row.area_name,
                completion_status=snapshot.completion_status if snapshot else row.project_status,
                pipeline_status=snapshot.pipeline_status if snapshot else None,
                score=row.score,
                no_of_units=row.no_of_units,
                rental_contract_count_12m=snapshot.rental_contract_count_12m if snapshot else 0,
                median_annual_rent=snapshot.median_annual_rent if snapshot else None,
                avg_rent_price_sqm_12m=snapshot.avg_rent_price_sqm_12m if snapshot else None,
                gross_yield_pct=snapshot.gross_yield_pct if snapshot else None,
                rationale=list(row.rationale),
            )
        )
    return [row.model_dump() for row in enriched]


@hbu_seed_agent.tool
async def get_pricing_comparables(
    ctx: RunContext[HBUSeedDeps], usage: Usage = "unknown", limit: int = 6
) -> list[dict]:
    """Completed and actively selling projects ranked by similarity to this plot.

    Similarity scoring considers plot size, area, and usage. Returns project name, status,
    similarity score, median_price_sqm, dominant unit type, and total units.
    Use to cross-check selling rates and calibrate unit mix.
    """
    rows = await rank_plot_comparables(
        ctx.deps.plot_details,
        _tool_usage(ctx, usage),
        "pricing",
        limit,
    )
    return [row.model_dump() for row in rows]


@hbu_seed_agent.tool
async def get_pipeline_comparables(
    ctx: RunContext[HBUSeedDeps], usage: Usage = "unknown", limit: int = 6
) -> list[dict]:
    """Planned and under-construction projects in the same market area.

    Returns project name, status, similarity score, total units, and land area.
    Use to gauge supply risk and identify unit type saturation in the pipeline.
    """
    rows = await rank_plot_comparables(
        ctx.deps.plot_details,
        _tool_usage(ctx, usage),
        "pipeline",
        limit,
    )
    return [row.model_dump() for row in rows]


@hbu_seed_agent.tool
async def get_plot_land_sales(
    ctx: RunContext[HBUSeedDeps], days: int = 1825, limit: int = 12
) -> list[dict]:
    """Recent land sale transactions for plots of similar size in this area.

    Returns: list of {instance_date, area_name, plot_area_sqm, price_aed, price_sqm, price_per_gfa_sqm, project_name}.
    Use price_sqm (AED per plot sqm) to anchor landPricePsqm.
    Use price_per_gfa_sqm (AED per buildable GFA sqm, normalized to this plot's FAR) for FAR-adjusted comparison.
    A high price_per_gfa_sqm relative to AED/sqm GDV is a negative signal for viability.
    Consider the p25–p75 range as the credible window.
    An empty list means no land sales data is available — flag landPricePsqm as a critical warning.
    """
    plot = ctx.deps.plot_details
    t_far = (
        plot.far
        if plot.far and plot.far > 0
        else (
            plot.max_gfa_sqm / plot.plot_area_sqm
            if plot.plot_area_sqm > 0 and plot.max_gfa_sqm > 0
            else None
        )
    )
    rows = await get_land_sale_evidence(
        area_name=ctx.deps.market_area,
        plot_area_sqm=plot.plot_area_sqm,
        usage=ctx.deps.usage_override or plot.inferred_usage,
        target_far=t_far,
        days=days,
        limit=limit,
    )
    return [row.model_dump() for row in rows]


async def run_seed_draft(prompt: str, deps: HBUSeedDeps) -> HBUSeedDraft:
    development_model = deps.development_model
    agent = (
        hbu_seed_agent_btr
        if development_model == "build_to_rent_residential"
        else hbu_seed_agent_bts_prefetched
    )
    result = await agent.run(
        prompt,
        deps=deps,
        usage_limits=UsageLimits(
            request_limit=3 if development_model == "build_to_rent_residential" else 15
        ),
    )
    return result.output
