from app.chat.agent import agent


def test_registered_chat_tools_preserve_existing_capabilities():
    # Artifact dispatch recognizes more names; extraction must not enable them.
    assert set(agent._function_toolset.tools) == {
        "get_market_pulse",
        "rank_areas",
        "get_similar_projects",
        # Database-grounded market capabilities; each calls one public domain operation.
        "query_market_metrics",
        "get_listing_inventory",
        "get_project_delivery_summary",
    }


def test_chat_tool_argument_and_description_contract():
    import json
    from pathlib import Path

    expected = json.loads((Path(__file__).parent / "fixtures/chat_tools.json").read_text())
    actual = {
        name: {"description": tool.description, "schema": tool.function_schema.json_schema}
        for name, tool in agent._function_toolset.tools.items()
    }
    assert actual == expected
