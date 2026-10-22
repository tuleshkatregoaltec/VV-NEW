import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

from app.feasibility import chat as chat_agent
from app.plots.models import PlotDetails


async def test_invalid_history_warns_and_provider_failure_terminates_without_done(monkeypatch):
    plot = PlotDetails(
        plot_number="fixture-plot",
        community_name="Fixture Area",
        project_name="Fixture Project",
        plot_area_sqm=1000,
        max_gfa_sqm=4000,
        max_gfa_sqft=43055,
        max_height="G+20",
        max_coverage="60%",
        gfa_type="Residential",
        inferred_usage="residential",
        is_verified=True,
    )
    calls = []

    @asynccontextmanager
    async def fail(message, **kwargs):
        calls.append((message, kwargs))
        raise RuntimeError("provider unavailable")
        yield  # pragma: no cover

    monkeypatch.setattr(chat_agent, "workspace_chat_agent", SimpleNamespace(iter=fail))
    events = [
        json.loads(item.removeprefix("data: ").strip())
        async for item in chat_agent.stream_feasibility_response(
            [{"role": "user", "content": "Use the latest request"}],
            plot,
            {"unitConfigs": []},
            history_json="invalid-json",
        )
    ]
    assert events == [
        {
            "type": "warning",
            "content": "Session context reset — conversation history could not be restored.",
        },
        {"type": "error", "error": "provider unavailable"},
    ]
    assert calls[0][0] == "Use the latest request"
    assert calls[0][1]["message_history"] == []
    assert calls[0][1]["usage_limits"].request_limit == 10
