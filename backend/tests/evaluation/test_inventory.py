from scripts.evaluation.inventory import build_inventory


def test_actual_chat_registration_links_to_public_capabilities_and_warehouse():
    inventory = build_inventory()
    chat = next(agent for agent in inventory["agents"] if agent["id"] == "app.chat.agent.agent")
    assert {tool["function"] for tool in chat["tools"]} == {
        "app.chat.tools.get_market_pulse",
        "app.chat.tools.rank_areas",
        "app.chat.tools.get_similar_projects",
        "app.chat.tools.query_market_metrics",
        "app.chat.tools.get_listing_inventory",
        "app.chat.tools.get_project_delivery_summary",
    }
    reachable = set(chat["possible_reachable_functions"])
    assert {
        "app.analytics.service.get_market_pulse",
        "app.analytics.queries.fetch_market_pulse",
        "app.project.service.get_similar_projects",
        "app.project.queries.fetch_similar_projects",
        "app.clickhouse.client.query",
        "app.analytics.market_metrics.get_market_metrics",
        "app.analytics.market_metric_queries.fetch_metric_rows",
        "app.project.delivery.get_delivery_summary",
        "app.listings.service.listing_analytics_from_clickhouse",
    } <= reachable
    assert "app.listings.service.list_listings_from_clickhouse" not in reachable
    pulse = inventory["functions"]["app.analytics.queries.fetch_market_pulse"]
    assert pulse["sql_source_candidates"] == ["ch_transactions"]


def test_declared_legacy_hbu_tools_are_not_assigned_to_selected_production_agents():
    agents = {agent["id"]: agent for agent in build_inventory()["agents"]}
    assert len(agents["app.feasibility.hbu_agent.hbu_seed_agent"]["tools"]) == 7
    assert agents["app.feasibility.hbu_agent.hbu_seed_agent_bts_prefetched"]["tools"] == []
    assert agents["app.feasibility.hbu_agent.hbu_seed_agent_btr"]["tools"] == []
    assert len(agents["app.feasibility.chat_agent.workspace_chat_agent"]["tools"]) == 12


def test_inventory_follows_source_changes_without_importing_application(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    source = app / "agent.py"
    source.write_text("""
from pydantic_ai import Agent
raise RuntimeError("Discovery must not import this module")
PROMPT = "Original prompt"
agent = Agent("provider:model", system_prompt=PROMPT)
async def capability():
    return 1
@agent.tool_plain
async def tool():
    return await capability()
""")
    before = build_inventory(app)
    assert before["agents"][0]["possible_reachable_functions"] == [
        "app.agent.capability",
        "app.agent.tool",
    ]
    source.write_text(
        source.read_text()
        .replace("Original prompt", "Changed prompt")
        .replace("@agent.tool_plain", "# registration removed")
    )
    after = build_inventory(app)
    assert after["agents"][0]["tools"] == []
    assert before["agents"][0]["prompt_sha256"] != after["agents"][0]["prompt_sha256"]


def test_dynamic_dispatch_is_visible_as_uncertainty_not_a_fabricated_edge(tmp_path):
    app = tmp_path / "app"
    app.mkdir()
    (app / "dynamic.py").write_text("""
async def invoke(choose):
    agent = choose()
    return await agent.run("question")
""")
    inventory = build_inventory(app)
    assert "agent.run" in inventory["functions"]["app.dynamic.invoke"]["unresolved_calls"]
    assert inventory["invocation_candidates"][0]["resolved_callable"] is None


def test_direct_crm_model_calls_are_visible_outside_agent_instrumentation():
    functions = build_inventory()["functions"]
    for name in ("_llm_mapping", "_ai_extract", "_ai_header_mapping"):
        capability = functions[f"app.crm.ai.{name}"]
        assert capability["model_configuration_references"] == ["settings.DEFAULT_CHAT_MODEL"]
        assert capability["provider_endpoints"] == ["https://openrouter.ai/api/v1/chat/completions"]
    assert functions["app.core.openrouter.create_openrouter_model"][
        "model_configuration_references"
    ] == ["settings.DEFAULT_CHAT_MODEL"]
