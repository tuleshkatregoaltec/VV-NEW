import asyncio
import json
import logging
from datetime import date

from fastapi import HTTPException

from app.area.service import get_area_detail
from app.feasibility.assumption_defaults import (
    sanitize_generated_assumptions,
)
from app.feasibility.assumptions import (
    _apply_bts_acquisition_cost_defaults,
    _bts_pricing_guidance,
    _comparable_unit_mix_summary,
    _construction_cost_guidance,
    _effective_storeys,
    _enforce_land_price_floor,
    _ensure_btr_assumptions,
    _ensure_bts_assumptions,
    _land_anchor_summary,
    _land_price_anchor_psqm,
    _massing_guidance,
    _normalize_bts_saleable_efficiency,
    _plot_context,
    build_assumptions_scaffold,
)
from app.feasibility.hbu_agent import (
    HBUSeedDeps,
    run_seed_draft,
)
from app.feasibility.models import (
    DevelopmentModel,
    GenerateStudyRequest,
    GenerateStudyResponse,
    PlotResearch,
    RentalComparableProject,
    RentalMarketSummary,
    RentalRateBySizeBand,
    StudyContext,
)
from app.feasibility.research import (
    build_off_plan_pricing_confidence,
    build_off_plan_unit_benchmarks,
    get_comparable_data,
    get_market_pricing,
    get_off_plan_pricing_curve,
    get_rental_rates_by_size_band,
    rank_plot_comparables,
    resolve_market_area,
)
from app.feasibility.service import get_plot_details_or_404
from app.plots.evidence import get_land_sale_evidence
from app.plots.models import Usage
from app.plots.service import get_plot_details as fetch_plot_details
from app.project.service import get_project_market_snapshot

logger = logging.getLogger(__name__)


async def generate_hbu_study(
    plot_number: str,
    usage_override: Usage | None = None,
    development_model: DevelopmentModel = "build_to_sell_residential",
) -> PlotResearch | None:
    development_model = development_model or "build_to_sell_residential"
    generation_date = date.today()
    details = await fetch_plot_details(plot_number)
    if details is None:
        return None

    usage = usage_override or details.inferred_usage
    market_area = await resolve_market_area(details.community_name)

    target_far = (
        details.far
        if details.far and details.far > 0
        else (
            details.max_gfa_sqm / details.plot_area_sqm
            if details.plot_area_sqm > 0 and details.max_gfa_sqm > 0
            else None
        )
    )
    competition_evidence, land_sales_evidence = await asyncio.gather(
        rank_plot_comparables(details, usage, "pipeline"),
        get_land_sale_evidence(
            area_name=market_area,
            plot_area_sqm=details.plot_area_sqm,
            usage=usage,
            target_far=target_far,
            days=1825,
            limit=12,
        ),
    )

    land_price_anchor = _land_price_anchor_psqm(
        land_sales_evidence,
        area_name=market_area or "",
        far=target_far or 0.0,
    )
    scaffold = build_assumptions_scaffold(
        details,
        usage,
        land_price_anchor_psqm=land_price_anchor,
        development_model=development_model,
        generation_date=generation_date,
    )

    market_pricing = []
    pricing_evidence = []
    off_plan_market_pricing = []
    off_plan_benchmarks = []
    off_plan_confidence = None
    rental_market_summary: RentalMarketSummary | None = None
    rental_comparables: list[RentalComparableProject] = []
    size_band_rates: list[RentalRateBySizeBand] = []
    comparable_unit_mix = None

    if development_model == "build_to_rent_residential":
        # Fetch area summary, ranked comps, and size-band rates in parallel
        try:
            area_detail_result, ranked_rental_comps, size_band_rates = await asyncio.gather(
                get_area_detail(market_area),
                rank_plot_comparables(details, usage, "pricing"),
                get_rental_rates_by_size_band(market_area),
            )
            rental_market_summary = RentalMarketSummary(
                area_name=area_detail_result.area_name,
                rental_contract_count_12m=area_detail_result.rental_contract_count_12m,
                median_annual_rent=area_detail_result.median_annual_rent,
                avg_rent_price_sqm_12m=area_detail_result.avg_rent_price_sqm_12m,
                gross_yield_pct=area_detail_result.gross_yield_pct,
                top_developers=area_detail_result.top_developers,
                top_master_projects=area_detail_result.top_master_projects,
            )
        except ValueError:
            rental_market_summary = None
            ranked_rental_comps = await rank_plot_comparables(details, usage, "pricing")

        # Fetch all project snapshots in parallel (was serial — O(n) sequential awaits)
        snapshots = await asyncio.gather(
            *[get_project_market_snapshot(row.project_id) for row in ranked_rental_comps]
        )
        for row, snapshot in zip(ranked_rental_comps, snapshots):
            rental_comparables.append(
                RentalComparableProject(
                    project_id=row.project_id,
                    project_name=row.project_name,
                    area_name=snapshot.area_name if snapshot else row.area_name,
                    completion_status=snapshot.completion_status
                    if snapshot
                    else row.project_status,
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
        comparable_project_ids = [row.project_id for row in ranked_rental_comps[:5]]
        if comparable_project_ids:
            comparable_unit_mix = await get_comparable_data(comparable_project_ids)
    else:
        market_pricing, pricing_evidence, off_plan_market_pricing = await asyncio.gather(
            get_market_pricing(market_area, usage, stage="existing"),
            rank_plot_comparables(details, usage, "pricing", limit=10),
            get_off_plan_pricing_curve(market_area, usage, scaffold.handoverDate),
        )
        target_horizon_months = max(
            1, round((date.fromisoformat(scaffold.handoverDate) - date.today()).days / 30)
        )
        fallback_prices = {row.unit_type: row.median_price_sqm for row in market_pricing}
        off_plan_benchmarks = build_off_plan_unit_benchmarks(
            off_plan_market_pricing, target_horizon_months, fallback_prices
        )
        off_plan_confidence = build_off_plan_pricing_confidence(
            off_plan_market_pricing, target_horizon_months
        )
        comparable_project_ids = [row.project_id for row in pricing_evidence[:5]]
        if comparable_project_ids:
            comparable_unit_mix = await get_comparable_data(comparable_project_ids)

    market_context_block = (
        (
            "=== BTR RENTAL MARKET CONTEXT ===\n"
            f"Master community rental summary: {json.dumps(rental_market_summary.model_dump(mode='json'), indent=2) if rental_market_summary else 'No area rental summary available.'}\n\n"
            "Rental rates by unit size band (use these to seed monthlyRentAed per config — each config should use the band matching its avgSizeSqm):\n"
            + (
                "\n".join(
                    f"  {b.unit_type_proxy} ({b.size_band_label}): AED {b.median_rent_psm_annual:,.0f}/sqm/yr, "
                    f"AED {b.median_annual_rent:,.0f} median annual rent, {b.contract_count} contracts"
                    for b in size_band_rates
                )
                or "  No size-band rental rates available — use blended area rate."
            )
            + "\n\nRental comparables:\n"
            + (
                "\n".join(
                    f"  {comp.project_name}: AED {comp.avg_rent_price_sqm_12m or 0:,.0f}/sqm/yr, "
                    f"AED {comp.median_annual_rent or 0:,.0f} median annual rent, "
                    f"{comp.rental_contract_count_12m} rental contracts, "
                    f"gross yield {comp.gross_yield_pct or 0:.1f}%"
                    for comp in rental_comparables
                )
                or "  No rental comparables available."
            )
        )
        if development_model == "build_to_rent_residential"
        else (
            "=== BTS MARKET CONTEXT ===\n"
            "Off-plan pricing benchmarks (primary sales-rate anchor):\n"
            + (
                "\n".join(
                    f"  {b.unit_type}: AED {b.selected_price_sqm:.0f}/sqm "
                    f"({b.confidence_label} confidence, {b.transaction_count} txns, {b.selected_horizon_bucket})"
                    for b in off_plan_benchmarks
                )
                or "  No off-plan benchmarks available — use existing market pricing."
            )
            + "\n\nExisting stock pricing floor:\n"
            + (
                "\n".join(
                    f"  {row.unit_type}: AED {row.median_price_sqm:,.0f}/sqm, "
                    f"{row.transaction_count} txns"
                    + (f", avg area {row.avg_area_sqm:,.0f} sqm" if row.avg_area_sqm else "")
                    for row in market_pricing
                )
                or "  No segmented existing-stock pricing available."
            )
            + "\n\nPricing comparables:\n"
            + (
                "\n".join(
                    f"  {comp.project_name}: {comp.project_status or 'unknown status'}, "
                    f"score {comp.score:.0f}, median AED {comp.median_price_sqm or 0:,.0f}/sqm, "
                    f"dominant unit {comp.dominant_unit_type or 'unknown'}, units {comp.no_of_units or 0}"
                    for comp in pricing_evidence[:8]
                )
                or "  No pricing comparable projects available."
            )
            + "\n\nPipeline and supply-risk comparables:\n"
            + (
                "\n".join(
                    f"  {comp.project_name}: {comp.project_status or 'unknown status'}, "
                    f"score {comp.score:.0f}, units {comp.no_of_units or 0}, "
                    f"land area {comp.total_land_area_sqm or 0:,.0f} sqm"
                    for comp in competition_evidence[:8]
                )
                or "  No pipeline comparable projects available."
            )
        )
    )
    bts_pricing_guidance_block = (
        f"=== BTS PRICING GUIDANCE ===\n"
        f"{_bts_pricing_guidance(details, market_pricing, off_plan_benchmarks)}\n\n"
        if development_model != "build_to_rent_residential"
        else ""
    )

    prompt = (
        f"=== PLOT DOSSIER ===\n"
        f"{_plot_context(details)}\n\n"
        f"=== DEVELOPMENT MODEL ===\n"
        f"{development_model}\n\n"
        f"=== MARKET AREA ===\n"
        f"Use {market_area} as the canonical transaction area for pricing and land evidence.\n\n"
        f"{market_context_block}\n\n"
        f"{bts_pricing_guidance_block}"
        f"=== LAND COST ANCHOR ===\n"
        f"{_land_anchor_summary(land_sales_evidence, details.plot_area_sqm, market_area, target_far or 0.0)}\n\n"
        f"=== CONSTRUCTION COST ANCHOR ===\n"
        f"{_construction_cost_guidance(usage, _effective_storeys(details, usage))}\n\n"
        f"=== MASSING / UNIT MIX EVIDENCE ===\n"
        f"{_massing_guidance(details, usage)}\n\n"
        f"{_comparable_unit_mix_summary(comparable_unit_mix)}\n\n"
        f"=== DEFAULT ASSUMPTION SCAFFOLD ===\n"
        f"Computed from the plot's GFA, height, and inferred usage. "
        f"Use it as context for market/product judgement. "
        f"Seed unit configs, BTR configs when applicable, land cost, sales/rent rates, "
        f"and construction hard-cost rate. Workbook defaults will reset financing, fees, VAT, "
        f"payment plan, BTR operating defaults, and schedule dates. The backend will preserve "
        f"the scaffolded physical test-fit assumptions for target coverage, building count, "
        f"floor count, and efficient floorplate.\n"
        f"{json.dumps(scaffold.model_dump(mode='json'), indent=2)}\n\n"
        f"Research the market, form your assumptions, and return the HBU study for "
        f"plot {plot_number} (usage: {usage}, development model: {development_model})."
    )

    draft = await run_seed_draft(
        prompt,
        HBUSeedDeps(
            plot_number=plot_number,
            plot_details=details,
            market_area=market_area,
            scaffold=scaffold,
            development_model=development_model,
            usage_override=usage_override,
        ),
    )

    draft.assumptions = (
        _ensure_btr_assumptions(draft.assumptions, rental_market_summary, size_band_rates)
        if development_model == "build_to_rent_residential"
        else _ensure_bts_assumptions(draft.assumptions)
    )
    draft.assumptions = sanitize_generated_assumptions(
        draft.assumptions,
        scaffold=scaffold,
        generation_date=generation_date,
        development_model=development_model,
    )
    saleable_efficiency_warning = None
    if development_model != "build_to_rent_residential":
        draft.assumptions, saleable_efficiency_warning = _normalize_bts_saleable_efficiency(
            draft.assumptions
        )

    draft.assumptions, land_floor_warning = _enforce_land_price_floor(
        draft.assumptions,
        land_sales_evidence,
        details.plot_area_sqm,
        area_name=market_area or "",
        far=target_far or 0.0,
    )
    if development_model != "build_to_rent_residential":
        draft.assumptions = _apply_bts_acquisition_cost_defaults(draft.assumptions)

    warnings = list(details.warnings)
    warnings.extend(item for item in draft.warnings if item not in warnings)
    if land_floor_warning and land_floor_warning not in warnings:
        warnings.append(land_floor_warning)
    if saleable_efficiency_warning and saleable_efficiency_warning not in warnings:
        warnings.append(saleable_efficiency_warning)
    if development_model != "build_to_rent_residential" and not market_pricing:
        warnings.append("No segmented existing transaction pricing was found for this plot area.")
    if development_model != "build_to_rent_residential" and not off_plan_market_pricing:
        warnings.append("No off-plan pricing curve was found for this plot area.")
    if development_model == "build_to_rent_residential" and rental_market_summary is None:
        warnings.append("No master-community rental summary was found for this plot area.")
    if development_model == "build_to_rent_residential" and not rental_comparables:
        warnings.append("No rental comparable projects were found for this plot area.")

    land_rates = sorted(row.price_sqm for row in land_sales_evidence)
    land_cost_low = land_cost_mid = land_cost_high = None
    if land_rates and details.plot_area_sqm > 0:
        land_cost_low = round(
            land_rates[max(0, int((len(land_rates) - 1) * 0.25))] * details.plot_area_sqm, 2
        )
        land_cost_mid = round(land_rates[len(land_rates) // 2] * details.plot_area_sqm, 2)
        land_cost_high = round(
            land_rates[min(len(land_rates) - 1, int((len(land_rates) - 1) * 0.75))]
            * details.plot_area_sqm,
            2,
        )

    return PlotResearch(
        plot_number=details.plot_number,
        usage=usage,
        market_area=market_area,
        headline=draft.headline,
        summary=draft.summary,
        constraints=draft.constraints,
        pricing_evidence=pricing_evidence,
        competition_evidence=competition_evidence,
        land_sales_evidence=land_sales_evidence,
        market_pricing=market_pricing,
        off_plan_market_pricing=off_plan_market_pricing,
        off_plan_pricing_confidence=off_plan_confidence,
        off_plan_unit_benchmarks=off_plan_benchmarks,
        rental_market_summary=rental_market_summary,
        rental_comparables=rental_comparables,
        rental_rates_by_size_band=size_band_rates,
        assumptions=draft.assumptions,
        warnings=warnings,
        land_cost_estimate_aed_low=land_cost_low,
        land_cost_estimate_aed_mid=land_cost_mid,
        land_cost_estimate_aed_high=land_cost_high,
    )


def _build_study_context(research: PlotResearch) -> StudyContext:
    return StudyContext(
        headline=research.headline,
        project_tier="premium",
        summary_points=list(research.summary),
        market_summary=", ".join(research.constraints) if research.constraints else "",
    )


async def generate_hbu_for_plot(
    plot_number: str,
    usage_override: Usage | None = None,
    development_model: DevelopmentModel | None = None,
) -> PlotResearch:
    study = await generate_hbu_study(
        plot_number,
        usage_override=usage_override,
        development_model=development_model,
    )
    if study is None:
        raise HTTPException(status_code=404, detail="GIS plot not found")
    return study


async def generate_seeded_study(request: GenerateStudyRequest) -> GenerateStudyResponse:
    try:
        research = await generate_hbu_for_plot(
            request.plot_number,
            usage_override=request.usage_override,
            development_model=request.development_model,
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("generate_hbu_study failed for plot %s: %s", request.plot_number, e)
        raise HTTPException(status_code=500, detail=str(e)) from e
    plot = await get_plot_details_or_404(request.plot_number)
    return GenerateStudyResponse(
        plot_data=plot,
        assumptions=research.assumptions,
        research=research,
        study_context=_build_study_context(research),
    )
