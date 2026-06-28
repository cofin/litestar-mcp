from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litestar.config.app import AppConfig

from litestar import Litestar
from litestar.plugins import InitPluginProtocol
from litestar.testing import TestClient

from litestar_mcp import MCP
from litestar_mcp.config import MCPConfig
from litestar_mcp.plugin import LitestarMCP


def _rpc(client: TestClient[Any], method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = getattr(client, "_mcp_session", "")
        if not sid:
            init = _rpc(client, "initialize")
            sid = init.get("_session_id", "")
            if not sid:
                sid = getattr(client, "_mcp_session", "")
        if sid:
            headers["Mcp-Session-Id"] = str(sid)
    response = client.post("/mcp", json=body, headers=headers)
    result = response.json()
    sid = response.headers.get("mcp-session-id")
    if method == "initialize" and sid:
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": sid},
        )
        client._mcp_session = sid  # type: ignore[attr-defined]
        result["_session_id"] = sid
    return result  # type: ignore[no-any-return]


def test_mcp_init_defaults() -> None:
    mcp = MCP(name="test-mcp", instructions="test instructions")
    assert mcp.config.name == "test-mcp"
    assert mcp.config.instructions == "test instructions"
    assert isinstance(mcp.plugin, LitestarMCP)
    assert mcp.plugin in mcp._plugins


def test_mcp_init_custom_config() -> None:
    config = MCPConfig(base_path="/custom")
    mcp = MCP(name="test-mcp", config=config)
    assert mcp.config.base_path == "/custom"
    assert mcp.config.name == "test-mcp"


def test_mcp_init_uses_plugin() -> None:
    plugin = LitestarMCP()
    mcp = MCP(name="test-mcp", plugins=[plugin])
    assert mcp.plugin is plugin
    mcp_plugins = [p for p in mcp._plugins if isinstance(p, LitestarMCP)]
    assert len(mcp_plugins) == 1


def test_mcp_init_synchronizes_existing_plugin_metadata() -> None:
    plugin = LitestarMCP()
    mcp = MCP(name="existing-plugin", instructions="Follow these instructions", plugins=[plugin])

    assert mcp.plugin is plugin
    assert mcp.config is plugin.config
    assert plugin.config.name == "existing-plugin"
    assert plugin.config.instructions == "Follow these instructions"

    with TestClient(app=mcp.app) as client:
        response = _rpc(client, "initialize")

    assert response["result"]["serverInfo"]["name"] == "existing-plugin"
    assert response["result"]["instructions"] == "Follow these instructions"


def test_mcp_lazy_app() -> None:
    mcp = MCP(name="test-mcp")
    is_none = mcp._app is None
    assert is_none
    app = mcp.app
    assert isinstance(app, Litestar)
    assert mcp._app is app
    assert mcp.app is app


def test_mcp_decorators_registration() -> None:
    mcp = MCP(name="test-mcp")

    @mcp.tool(name="my_tool", description="tool desc")
    def tool_fn(x: int) -> int:
        """Doc desc"""
        return x + 1

    @mcp.resource(uri="app://my_resource", name="res_name", description="res desc")
    def resource_fn() -> str:
        return "data"

    @mcp.prompt(name="my_prompt", description="prompt desc")
    def prompt_fn() -> list[dict[str, Any]]:
        return []

    assert len(mcp.plugin._dynamic_handlers) == 3

    # Access app to trigger on_app_init on plugin
    _ = mcp.app

    registry = mcp.plugin._registry
    assert "my_tool" in registry.tools
    assert "res_name" in registry.resources
    assert "my_prompt" in registry.prompts

    tool_handler = registry.tools["my_tool"]
    assert tool_handler.opt["mcp_description"] == "tool desc"

    res_handler = registry.resources["res_name"]
    assert res_handler.opt["mcp_resource_description"] == "res desc"
    assert res_handler.opt["mcp_resource_template"] == "app://my_resource"

    prompt_reg = registry.prompts["my_prompt"]
    assert prompt_reg.description == "prompt desc"


def test_standalone_internal_tool_route_rejects_direct_http() -> None:
    mcp = MCP(name="test-mcp")

    @mcp.tool(name="echo", description="Echo")
    def echo(message: str) -> str:
        return message

    with TestClient(app=mcp.app) as client:
        direct = client.post("/mcp/internal/tools/echo", json={"message": "hello"})
        via_mcp = _rpc(client, "tools/call", {"name": "echo", "arguments": {"message": "hello"}})

    assert direct.status_code == 403
    assert via_mcp["result"]["isError"] is False
    assert via_mcp["result"]["content"][0]["text"] == "hello"


def test_standalone_plugin_coexistence() -> None:
    class DummyPlugin(InitPluginProtocol):
        def on_app_init(self, app_config: AppConfig) -> AppConfig:
            app_config.tags.append("dummy-tag")
            return app_config

    dummy_plugin = DummyPlugin()
    mcp = MCP("coexist-test", plugins=[dummy_plugin])

    app = mcp.app

    assert "dummy-tag" in app.tags
    assert any(isinstance(p, LitestarMCP) for p in app.plugins)
