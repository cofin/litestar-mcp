"""Unit tests for agent protocol bridges."""

from typing import Any, cast

import httpx2
import pytest
from litestar import Litestar

from litestar_mcp.agent.bridges.mcp import agent_to_mcp, discover_mcp_tools, mcp_to_tools
from litestar_mcp.agent.spec import Agent, AgentGroup
from litestar_mcp.core.tools import tool
from litestar_mcp.mcp.client import MCPStreamableHTTPClient
from litestar_mcp.mcp.plugin import LitestarMCP


@tool(description="Bridge test tool.")
def ping() -> str:
    """Return pong string."""
    return "pong"


def test_agent_to_mcp() -> None:
    """agent_to_mcp registers tools from an Agent into a LitestarMCP plugin."""
    agent = Agent(name="network_agent", tools=[ping])
    plugin = agent_to_mcp(agent, path="/mcp_bridge")
    assert isinstance(plugin, LitestarMCP)
    assert plugin.config.base_path == "/mcp_bridge"


def test_agent_to_mcp_group_excludes_transfer_tools() -> None:
    """agent_to_mcp registers specialist tools without synthetic transfer tools."""
    coord = Agent(name="coord", tools=[ping])
    spec = Agent(name="spec")
    group = AgentGroup(coordinator=coord, specialists=[spec])
    plugin = agent_to_mcp(group, path="/mcp_group")
    assert isinstance(plugin, LitestarMCP)


@pytest.mark.anyio
async def test_mcp_to_tools_from_client() -> None:
    """mcp_to_tools wraps client tools as Tool instances using from_schema."""

    class MockClient:
        def __init__(self) -> None:
            self.tools = [
                {
                    "name": "remote_echo",
                    "description": "Remote tool echo",
                    "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}},
                }
            ]

        async def call_tool(self, name: str, arguments: dict[str, str]) -> str:
            return f"echo: {arguments.get('msg', '')}"

    client = MockClient()
    tools = mcp_to_tools(client)
    assert len(tools) == 1
    assert tools[0].name == "remote_echo"
    assert tools[0].description == "Remote tool echo"

    res = await tools[0].execute({"msg": "hello"})
    assert res == "echo: hello"


@pytest.mark.anyio
async def test_discover_mcp_tools() -> None:
    """discover_mcp_tools fetches remote tools and converts them to Tools."""
    plugin = LitestarMCP()
    plugin.register_tool(ping)
    app = Litestar(plugins=[plugin])
    transport = httpx2.ASGITransport(app=cast("Any", app))
    client = MCPStreamableHTTPClient(endpoint="http://testserver/mcp", transport=transport)

    tools = await discover_mcp_tools(client)
    assert len(tools) >= 1
    tool_names = [t.name for t in tools]
    assert "ping" in tool_names
