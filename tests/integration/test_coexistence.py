"""Integration test verifying concurrent MCP and A2A multi-protocol coexistence."""

import pytest
from litestar import Litestar, get
from litestar.testing import AsyncTestClient

from litestar_mcp.a2a.config import A2AConfig
from litestar_mcp.a2a.plugin import A2APlugin
from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.plugin import LitestarMCP


@get("/tools/multiply", opt={"mcp_tool": "multiply", "mcp_description": "Multiply two integers"})
def multiply_numbers(a: int, b: int) -> int:
    """Multiply handler exposed as MCP tool."""
    return a * b


@get("/skills/greet", opt={"a2a_skill": "greet", "a2a_description": "Greet someone"})
def greet_person(name: str) -> str:
    """Greet handler exposed as A2A skill."""
    return f"Hello, {name}!"


@pytest.fixture
def coexistence_app() -> Litestar:
    """Create a Litestar app with both MCP and A2A plugins mounted."""
    mcp_plugin = LitestarMCP(config=MCPConfig(base_path="/mcp"))
    a2a_plugin = A2APlugin(
        config=A2AConfig(
            name="Hybrid Agent",
            version="1.0.0",
            base_path="/a2a",
            auto_export_mcp_tools=True,
        )
    )
    return Litestar(
        route_handlers=[multiply_numbers, greet_person],
        plugins=[mcp_plugin, a2a_plugin],
    )


@pytest.mark.anyio
async def test_multi_protocol_coexistence(coexistence_app: Litestar) -> None:
    """Verify concurrent MCP and A2A operations and tool-as-skill auto export."""
    async with AsyncTestClient(app=coexistence_app) as client:
        card_resp = await client.get("/.well-known/agent-card.json")
        assert card_resp.status_code == 200
        card = card_resp.json()
        assert card["name"] == "Hybrid Agent"
        skill_ids = [s["id"] for s in card["skills"]]
        assert "greet" in skill_ids
        assert "multiply" in skill_ids

        mcp_list_resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert mcp_list_resp.status_code == 200
        mcp_tools = mcp_list_resp.json()["result"]["tools"]
        assert any(t["name"] == "multiply" for t in mcp_tools)

        mcp_call_resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "multiply", "arguments": {"a": 6, "b": 7}},
            },
        )
        assert mcp_call_resp.status_code == 200
        mcp_content = mcp_call_resp.json()["result"]["content"][0]
        assert "42" in mcp_content["text"]

        a2a_greet_resp = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tasks/send",
                "params": {
                    "skill": "greet",
                    "message": {
                        "role": "user",
                        "parts": [{"type": "data", "data": {"name": "Cody"}}],
                    },
                },
            },
        )
        assert a2a_greet_resp.status_code == 200
        greet_result = a2a_greet_resp.json()["result"]
        assert greet_result["status"]["state"] == "completed"
        assert greet_result["artifacts"][0]["parts"][0]["text"] == "Hello, Cody!"

        a2a_auto_exported_resp = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tasks/send",
                "params": {
                    "skill": "multiply",
                    "message": {
                        "role": "user",
                        "parts": [{"type": "data", "data": {"a": 8, "b": 9}}],
                    },
                },
            },
        )
        assert a2a_auto_exported_resp.status_code == 200
        auto_result = a2a_auto_exported_resp.json()["result"]
        assert auto_result["status"]["state"] == "completed"
        assert auto_result["artifacts"][0]["parts"][0]["data"] == 72
