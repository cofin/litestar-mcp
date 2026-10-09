from __future__ import annotations

import json

import httpx2
import pytest
from litestar import Litestar
from litestar.testing import TestClient

from litestar_mcp.agent.bridges import (
    MCPHttpClient,
    agent_to_a2a,
    agent_to_mcp,
    discover_mcp_tools,
    mcp_to_tools,
)
from litestar_mcp.agent.spec import Agent, AgentGroup
from litestar_mcp.agent.tools import tool
from litestar_mcp.controllers.skill import SkillController
from litestar_mcp.core.exceptions import MissingDependencyError
from litestar_mcp.core.serialization import to_json
from tests.unit.conftest import mcp_post


class BridgeSkill(SkillController):
    name = "bridge_skill"
    description = "Skill exposed over MCP and A2A"

    @tool(description="Multiply two integers.")
    def multiply(self, x: int, y: int) -> dict[str, int]:
        return {"product": x * y}


@tool(description="Add two integers.")
def add(a: int, b: int) -> dict[str, int]:
    return {"sum": a + b}


def test_agent_to_mcp_end_to_end() -> None:
    coordinator = Agent(name="coord", tools=[add])
    specialist = Agent(name="spec", skills=[BridgeSkill()])
    group = AgentGroup(coordinator=coordinator, specialists=[specialist])

    plugin = agent_to_mcp(group, path="/mcp")
    app = Litestar(plugins=[plugin])

    with TestClient(app=app) as client:
        list_res = mcp_post(client, "tools/list", {}).json()
        tool_names = {t["name"] for t in list_res["result"]["tools"]}
        assert "add" in tool_names
        assert "multiply" in tool_names

        call_res = mcp_post(
            client,
            "tools/call",
            {"name": "add", "arguments": {"a": 7, "b": 8}},
        ).json()
        assert call_res["result"]["isError"] is False
        payload = json.loads(call_res["result"]["content"][0]["text"])
        assert payload == {"sum": 15}

    local_tools = mcp_to_tools(plugin)
    assert any(t.name == "add" for t in local_tools)


@pytest.mark.asyncio
async def test_mcp_http_client_and_discover_with_httpx2_sse() -> None:
    def mock_mcp_server(request: httpx2.Request) -> httpx2.Response:
        body = request.content.decode()
        if "tools/list" in body:
            rpc_payload = {
                "jsonrpc": "2.0",
                "id": "1",
                "result": {
                    "tools": [
                        {
                            "name": "remote_lookup",
                            "description": "Lookup item by key",
                            "inputSchema": {
                                "type": "object",
                                "properties": {"key": {"type": "string"}},
                            },
                        }
                    ]
                },
            }
            sse_data = f"event: message\ndata: {to_json(rpc_payload, as_bytes=False)}\n\n".encode()
            return httpx2.Response(
                200,
                headers={
                    "content-type": "text/event-stream",
                    "mcp-session-id": "sess-httpx2",
                },
                content=sse_data,
                request=request,
            )

        call_payload = {
            "jsonrpc": "2.0",
            "id": "2",
            "result": {"structuredContent": {"found": True, "key": "alpha"}},
        }
        return httpx2.Response(
            200,
            headers={"content-type": "application/json"},
            content=to_json(call_payload, as_bytes=True),
            request=request,
        )

    transport = httpx2.MockTransport(mock_mcp_server)
    async with httpx2.AsyncClient(transport=transport) as async_client:
        mcp_client = MCPHttpClient("http://mcp.local/mcp", client=async_client)
        tools = await discover_mcp_tools(mcp_client)
        assert mcp_client.session_id == "sess-httpx2"
        assert len(tools) == 1
        assert tools[0].name == "remote_lookup"

        result = await tools[0].execute({"key": "alpha"})
        assert result == {"found": True, "key": "alpha"}


def test_agent_to_a2a_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "a2a", None)
    monkeypatch.setitem(sys.modules, "a2a.types", None)
    agent = Agent(name="a2a_agent")
    with pytest.raises(MissingDependencyError) as exc_info:
        agent_to_a2a(agent)
    assert exc_info.value.package == "a2a-sdk"
