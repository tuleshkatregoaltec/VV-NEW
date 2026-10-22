from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.feasibility import generation, hbu_agent
from app.plots.models import PlotDetails


@pytest.mark.parametrize(
    "model,limit", [("build_to_sell_residential", 15), ("build_to_rent_residential", 3)]
)
async def test_generation_preserves_provider_path_limits_and_deterministic_fields(
    monkeypatch, model, limit
):
    plot = PlotDetails(
        plot_number="001",
        community_name="Test Area",
        project_name="Test",
        plot_area_sqm=1000,
        max_gfa_sqm=4000,
        max_gfa_sqft=43055,
        max_height="G+20",
        max_coverage="60%",
        gfa_type="Residential",
        inferred_usage="residential",
        is_verified=True,
    )
    monkeypatch.setattr(generation, "fetch_plot_details", AsyncMock(return_value=plot))
    monkeypatch.setattr(generation, "resolve_market_area", AsyncMock(return_value="Canonical Area"))
    for name in [
        "rank_plot_comparables",
        "get_land_sale_evidence",
        "get_market_pricing",
        "get_off_plan_pricing_curve",
        "get_rental_rates_by_size_band",
    ]:
        monkeypatch.setattr(generation, name, AsyncMock(return_value=[]))
    monkeypatch.setattr(generation, "get_area_detail", AsyncMock(side_effect=ValueError("no area")))
    calls = []

    async def run(prompt, **kwargs):
        calls.append((prompt, kwargs))
        scaffold = kwargs["deps"].scaffold
        return SimpleNamespace(
            output=hbu_agent.HBUSeedDraft(headline="Draft", assumptions=scaffold)
        )

    selected = (
        "hbu_seed_agent_btr"
        if model == "build_to_rent_residential"
        else "hbu_seed_agent_bts_prefetched"
    )
    other = (
        "hbu_seed_agent_bts_prefetched"
        if model == "build_to_rent_residential"
        else "hbu_seed_agent_btr"
    )
    monkeypatch.setattr(hbu_agent, selected, SimpleNamespace(run=run))
    unused = AsyncMock(side_effect=AssertionError("wrong underwriting path"))
    monkeypatch.setattr(hbu_agent, other, SimpleNamespace(run=unused))
    result = await generation.generate_hbu_study("001", development_model=model)
    assert result.plot_number == "001"
    assert result.market_area == "Canonical Area"
    assert result.assumptions.developmentModel == model
    assert result.assumptions.plotAreaSqm == 1000
    assert result.assumptions.maxGfaSqm == 4000
    assert calls[0][1]["usage_limits"].request_limit == limit
    assert calls[0][1]["deps"].plot_number == "001"
    assert model in calls[0][0]
    assert "Canonical Area as the canonical transaction area" in calls[0][0]
    unused.assert_not_awaited()


async def test_missing_plot_never_calls_generation_provider(monkeypatch):
    monkeypatch.setattr(generation, "fetch_plot_details", AsyncMock(return_value=None))
    run = AsyncMock(side_effect=AssertionError("must not generate without plot evidence"))
    monkeypatch.setattr(hbu_agent, "hbu_seed_agent_bts_prefetched", SimpleNamespace(run=run))
    assert await generation.generate_hbu_study("missing") is None
    run.assert_not_awaited()
