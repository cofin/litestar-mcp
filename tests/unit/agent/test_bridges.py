from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from litestar_mcp.agent.bridges import agent_to_a2a, agent_to_mcp, mcp_to_tools
from litestar_mcp.agent.spec import Agent
from litestar_mcp.agent.tools import tool
from litestar_mcp.controllers.skill import SkillController
from litestar_mcp.core.exceptions import MissingDependencyError
from litestar_mcp.mcp.plugin import LitestarMCP


@tool(description="Bridge test tool.")
def ping() -> str:
    return "pong"


def test_agent_to_mcp() -> None:
    agent = Agent(name="network_agent", tools=[ping])
    plugin = agent_to_mcp(agent, path="/mcp_bridge")
    assert isinstance(plugin, LitestarMCP)
    assert plugin.config.base_path == "/mcp_bridge"
    assert "ping" in plugin.registry.tools


@pytest.mark.asyncio
async def test_mcp_to_tools() -> None:
    class MockMCPClient:
        def __init__(self) -> None:
            self.call_tool = AsyncMock(returnvalue="remote response")
            self.call_tool.return_value = "remote response"
            self.tools = [
                type(
                    "RemoteTool",
                    (),
                    {
                        "name": "remote_echo",
                        "description": "Remote tool echo",
                        "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}},
                    },
                )()
            ]

    client = MockMCPClient()
    tools = mcp_to_tools(client)
    assert len(tools) == 1
    assert tools[0].name == "remote_echo"
    assert tools[0].description == "Remote tool echo"

    res = await tools[0](msg="hello")
    assert res == "remote response"


def test_agent_to_a2a_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "a2a", None)
    monkeypatch.setitem(sys.modules, "a2a.types", None)
    monkeypatch.setitem(sys.modules, "a2a.server.agent_execution", None)
    agent = Agent(name="test_agent")
    with pytest.raises(MissingDependencyError) as exc_info:
        agent_to_a2a(agent)
    assert exc_info.value.package == "a2a-sdk"


def test_agent_to_a2a_creates_valid_litestar_a2a_plugin() -> None:
    pytest.importorskip("a2a")
    from litestar_mcp.a2a import LitestarA2A

    class DummySkill(SkillController):
        name = "dummy_skill"
        description = "A skill for testing"

    agent = Agent(name="test_agent", skills=[DummySkill()])
    plugin = agent_to_a2a(agent, path="/custom_a2a")
    assert isinstance(plugin, LitestarA2A)
    assert plugin.agent_card.name == "test_agent"
    assert plugin.config.path == "/custom_a2a"
    assert len(plugin.agent_card.skills) == 1
    assert plugin.agent_card.skills[0].name == "dummy_skill"
