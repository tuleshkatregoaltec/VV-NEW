import json
from pathlib import Path

from app.feasibility.chat_agent import workspace_chat_agent


def test_workspace_tool_contract_does_not_drift():
    expected = json.loads((Path(__file__).parent / "fixtures/feasibility_tools.json").read_text())
    actual = {
        name: {"description": tool.description, "schema": tool.function_schema.json_schema}
        for name, tool in workspace_chat_agent._function_toolset.tools.items()
    }
    assert actual == expected
