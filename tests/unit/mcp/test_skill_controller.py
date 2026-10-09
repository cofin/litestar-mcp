"""Unit tests for SkillController tools, prompts, guards, DI, and metadata."""

from typing import Any, Literal

from litestar import Litestar
from litestar.connection import ASGIConnection
from litestar.di import Provide
from litestar.exceptions import PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from litestar.testing import TestClient

from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.tools import tool
from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.prompt_controller import prompt
from litestar_mcp.mcp.skill_controller import SkillController
from tests.unit.conftest import mcp_post


class DeniedByTestGuardError(PermissionDeniedException):
    """Raised when tool access is denied by test guard."""

    def __init__(self) -> None:
        super().__init__("Tool access denied")


def deny_all(connection: ASGIConnection[Any, Any, Any, Any], _: BaseRouteHandler) -> None:
    """Guard rejecting all requests."""
    raise DeniedByTestGuardError


class GuardedSkillController(SkillController):
    """Skill with controller-level guard."""

    name = "guarded_skill"
    guards = [deny_all]

    @tool(description="A guarded tool.")
    def secure_action(self, target: str) -> str:
        """Run a secure action."""
        return f"acted on {target}"


class StateAndDISkillController(SkillController):
    """Skill sharing instance state and DI dependencies."""

    name = "state_skill"
    description = "Demonstrates state sharing and DI."
    tags = ("state", "demo")
    examples = ("Run state test",)
    dependencies = {"service_token": Provide(lambda: "secret-token-123", sync_to_thread=False)}

    def __init__(self, owner: Any = None) -> None:
        super().__init__(owner)
        self.call_count = 0

    @tool(description="Increment and return count.")
    def bump(
        self,
        step: int,
        service_token: str,
        mode: Literal["fast", "slow"] = "fast",
        ctx: ToolContext | None = None,
    ) -> dict[str, Any]:
        """Increment count and return state."""
        self.call_count += step
        return {
            "count": self.call_count,
            "token": service_token,
            "mode": mode,
            "tenant": ctx.tenant_id if ctx else None,
        }

    @prompt(title="State Summary", description="Show current state.")
    def status(self) -> str:
        """Return status string with current count."""
        return f"Current count is {self.call_count}"


def test_to_agent_skill_projection() -> None:
    """Verify SkillController.to_agent_skill projects metadata attributes."""
    skill = StateAndDISkillController()
    proj = skill.to_agent_skill()
    assert proj["id"] == "state_skill"
    assert proj["name"] == "state_skill"
    assert proj["description"] == "Demonstrates state sharing and DI."
    assert proj["tags"] == ["state", "demo"]
    assert proj["examples"] == ["Run state test"]


def test_skill_tool_guards_apply() -> None:
    """Verify controller guards reject tools/call with isError=True and 403."""
    config = MCPConfig(skill_controllers=[GuardedSkillController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        res = mcp_post(
            client,
            "tools/call",
            {"name": "secure_action", "arguments": {"target": "prod"}},
        ).json()
        assert "result" in res
        assert res["result"]["isError"] is True
        error_text = res["result"]["content"][0]["text"]
        assert "Tool access denied" in error_text


def test_skill_tool_dependencies_and_schema_matching() -> None:
    """Verify controller dependencies are injected and inputSchema matches Tool.parameters."""
    config = MCPConfig(skill_controllers=[StateAndDISkillController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        list_res = mcp_post(client, "tools/list", {}).json()
        tools = list_res["result"]["tools"]
        bump_tool = next(t for t in tools if t["name"] == "bump")

        assert "step" in bump_tool["inputSchema"]["properties"]
        assert "mode" in bump_tool["inputSchema"]["properties"]
        assert "service_token" not in bump_tool["inputSchema"]["properties"]
        assert "ctx" not in bump_tool["inputSchema"]["properties"]
        assert "request" not in bump_tool["inputSchema"]["properties"]

        call_res = mcp_post(
            client,
            "tools/call",
            {"name": "bump", "arguments": {"step": 5, "mode": "slow"}},
        ).json()
        assert call_res["result"]["isError"] is False
        output = call_res["result"]["content"][0]["text"]
        assert "secret-token-123" in output
        assert "slow" in output


def test_skill_tool_shares_controller_instance_state() -> None:
    """Verify tools and prompts on the same controller share instance state."""
    config = MCPConfig(skill_controllers=[StateAndDISkillController])
    app = Litestar(plugins=[LitestarMCP(config=config)])

    with TestClient(app=app) as client:
        call_res = mcp_post(
            client,
            "tools/call",
            {"name": "bump", "arguments": {"step": 10}},
        ).json()
        assert call_res["result"]["isError"] is False

        prompt_res = mcp_post(
            client,
            "prompts/get",
            {"name": "state_skill/status", "arguments": {}},
        ).json()
        messages = prompt_res["result"]["messages"]
        assert "Current count is 10" in messages[0]["content"]["text"]
