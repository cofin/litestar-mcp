from __future__ import annotations

from typing import Any
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
            self.call_tool = AsyncMock(return_value="remote response")
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
    agent = Agent(name="test_agent")
    with pytest.raises(MissingDependencyError) as exc_info:
        agent_to_a2a(agent)
    assert exc_info.value.package == "a2a-sdk"


def test_agent_to_a2a_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    class MockAgentSkill:
        def __init__(self, name: str, description: str, tags: list[str], examples: list[str]) -> None:
            self.name = name
            self.description = description
            self.tags = tags
            self.examples = examples

    class MockAgentCard:
        def __init__(self, name: str, description: str, skills: list[Any], url: str) -> None:
            self.name = name
            self.description = description
            self.skills = skills
            self.url = url

    class MockA2ATypes:
        AgentCard = MockAgentCard
        AgentSkill = MockAgentSkill

    class MockA2AConfig:
        def __init__(self, path: str) -> None:
            self.path = path

    class MockLitestarA2A:
        def __init__(self, agent_card: Any, request_handler: Any, config: Any) -> None:
            self.agent_card = agent_card
            self.request_handler = request_handler
            self.config = config

    class MockLitestarA2AModule:
        A2AConfig = MockA2AConfig
        LitestarA2A = MockLitestarA2A

    import sys

    monkeypatch.setitem(sys.modules, "a2a", type("MockA2A", (), {}))
    monkeypatch.setitem(sys.modules, "a2a.types", MockA2ATypes)
    monkeypatch.setitem(sys.modules, "litestar_mcp.a2a", MockLitestarA2AModule)

    class DummySkill(SkillController):
        name = "dummy_skill"
        description = "A skill for testing"

    agent = Agent(name="test_agent", skills=[DummySkill()])
    plugin = agent_to_a2a(agent, path="/custom_a2a")
    assert isinstance(plugin, MockLitestarA2A)
    assert plugin.agent_card.name == "test_agent"
    assert len(plugin.agent_card.skills) == 1
    assert plugin.agent_card.skills[0].name == "dummy_skill"
