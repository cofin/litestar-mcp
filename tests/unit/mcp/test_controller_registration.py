"""Unit tests for registering PromptController and SkillController on LitestarMCP and MCP."""

from __future__ import annotations

import json
from typing import Any

from litestar import Litestar
from litestar.testing import TestClient

from litestar_mcp import (
    MCP,
    LitestarMCP,
    MCPConfig,
    PromptController,
    SkillController,
    ToolContext,
    prompt,
    tool,
)
from tests.unit.conftest import mcp_post


class OpsPromptController(PromptController):
    """Operational runbook prompts."""

    prefix = "ops"
    instructions = "Follow incident response protocol."

    @prompt(title="Triage Alert", description="Triage an incoming alert.")
    def triage(self, service: str) -> str:
        """Format alert triage prompt."""
        return f"Triage alert for service: {service}"


class MathSkillController(SkillController):
    """Arithmetic skill controller."""

    name = "math"
    description = "Perform arithmetic operations."
    instructions = "Return exact integer results."

    @tool(description="Add two integers.")
    def add(self, a: int, b: int, ctx: ToolContext) -> dict[str, Any]:
        """Add two integers and include session context."""
        return {"sum": a + b, "tenant": ctx.tenant_id}

    @prompt(title="Explain Addition", description="Explain addition step by step.")
    def explain_add(self, a: str, b: str) -> str:
        """Build addition explanation prompt."""
        return f"Explain how to add {a} and {b}"


def test_litestar_mcp_controller_jsonrpc_dispatch() -> None:
    """Verify tools/list, tools/call, prompts/list, and prompts/get work end-to-end with controllers."""
    config = MCPConfig(
        prompt_controllers=[OpsPromptController],
        skills=[MathSkillController],
    )
    plugin = LitestarMCP(config=config)
    app = Litestar(plugins=[plugin])

    with TestClient(app=app) as client:
        tools_res = mcp_post(client, "tools/list", {}).json()
        tools = tools_res["result"]["tools"]
        tool_names = {t["name"] for t in tools}
        assert "add" in tool_names

        add_entry = next(t for t in tools if t["name"] == "add")
        assert "a" in add_entry["inputSchema"]["properties"]
        assert "b" in add_entry["inputSchema"]["properties"]
        assert "ctx" not in add_entry["inputSchema"]["properties"]
        assert "request" not in add_entry["inputSchema"]["properties"]

        call_res = mcp_post(
            client,
            "tools/call",
            {"name": "add", "arguments": {"a": 10, "b": 25}},
        ).json()
        assert call_res["result"]["isError"] is False
        payload = json.loads(call_res["result"]["content"][0]["text"])
        assert payload["sum"] == 35

        prompts_res = mcp_post(client, "prompts/list", {}).json()
        prompt_names = {p["name"] for p in prompts_res["result"]["prompts"]}
        assert "ops/triage" in prompt_names
        assert "math/explain_add" in prompt_names

        get_prompt_res = mcp_post(
            client,
            "prompts/get",
            {"name": "ops/triage", "arguments": {"service": "billing"}},
        ).json()
        messages = get_prompt_res["result"]["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"]["text"] == "Follow incident response protocol."
        assert messages[1]["role"] == "user"
        assert messages[1]["content"]["text"] == "Triage alert for service: billing"


def test_standalone_mcp_wrapper_registers_controllers() -> None:
    """Verify standalone MCP wrapper forwards prompt_controllers and skills to LitestarMCP."""
    mcp_app = MCP(
        "controller-server",
        prompt_controllers=[OpsPromptController],
        skills=[MathSkillController],
    )
    with TestClient(app=mcp_app.app) as client:
        call_res = mcp_post(
            client,
            "tools/call",
            {"name": "add", "arguments": {"a": 3, "b": 4}},
        ).json()
        payload = json.loads(call_res["result"]["content"][0]["text"])
        assert payload["sum"] == 7
