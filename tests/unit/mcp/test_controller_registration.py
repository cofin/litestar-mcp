"""Unit tests for registering PromptController and SkillController on LitestarMCP and MCPConfig."""

import dataclasses
import inspect
import json
from typing import Any, cast

import pytest
from litestar import Controller, Litestar
from litestar.exceptions import ImproperlyConfiguredException
from litestar.testing import TestClient

from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.tools import tool
from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.prompt_controller import PromptController, prompt
from litestar_mcp.mcp.skill_controller import SkillController
from tests.unit.conftest import mcp_post

V0_14_MCP_CONFIG_FIELDS = [
    "base_path",
    "include_in_schema",
    "name",
    "instructions",
    "guards",
    "allowed_origins",
    "include_operations",
    "exclude_operations",
    "include_tags",
    "exclude_tags",
    "tasks",
    "skills",
    "opt_keys",
    "cache_ttl_ms",
    "cache_scope",
    "subscription_max_streams",
    "subscription_keepalive_seconds",
    "stream_queue_capacity",
    "stream_cleanup_timeout",
    "subscription_channels",
    "list_page_size",
    "before_tool_call",
    "after_tool_call",
    "max_blob_bytes",
    "route_opt",
]


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


class OwnerInstantiationError(AttributeError):
    """Raised when plugin instantiates user controller with owner=None."""

    def __init__(self) -> None:
        super().__init__("Plugin must not instantiate user Controller with owner=None")


class ExplodingController(Controller):
    """Controller whose __init__ explodes if instantiated directly by the plugin."""

    def __init__(self, owner: Any = None) -> None:
        if owner is None:
            raise OwnerInstantiationError
        super().__init__(owner)


def test_config_registration_dispatches_end_to_end() -> None:
    """Verify tools/list, tools/call, prompts/list, and prompts/get work end-to-end with controllers."""
    config = MCPConfig(
        prompt_controllers=[OpsPromptController],
        skill_controllers=[MathSkillController],
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
        assert len(messages) == 1
        assert messages[0]["role"] == "user"
        assert messages[0]["content"]["text"].startswith("Follow incident response protocol.\n\n")


def test_non_controller_registration_rejected() -> None:
    """Verify passing non-PromptController classes or instances to config raises ImproperlyConfiguredException."""

    class PlainClass:
        pass

    with pytest.raises(ImproperlyConfiguredException, match="is not a PromptController subclass"):
        MCPConfig(prompt_controllers=cast("Any", [PlainClass]))
        LitestarMCP(config=MCPConfig(prompt_controllers=cast("Any", [PlainClass])))

    with pytest.raises(ImproperlyConfiguredException, match="is not a PromptController subclass"):
        LitestarMCP(config=MCPConfig(skill_controllers=cast("Any", [OpsPromptController()])))


def test_plugin_never_instantiates_user_controllers() -> None:
    """Verify normal Litestar Controller subclasses are not instantiated by the plugin during discovery."""
    config = MCPConfig()
    plugin = LitestarMCP(config=config)
    app = Litestar(route_handlers=[ExplodingController], plugins=[plugin])
    with TestClient(app=app) as client:
        res = mcp_post(client, "tools/list", {}).json()
        assert "result" in res


def test_v0_14_constructor_signatures() -> None:
    """Verify LitestarMCP.__init__ parameters and MCPConfig leading field order match v0.14.0."""
    sig = inspect.signature(LitestarMCP.__init__)
    param_names = list(sig.parameters.keys())
    assert param_names == ["self", "config", "prompts"]

    field_names = [f.name for f in dataclasses.fields(MCPConfig)]
    leading_fields = field_names[: len(V0_14_MCP_CONFIG_FIELDS)]
    assert leading_fields == V0_14_MCP_CONFIG_FIELDS
