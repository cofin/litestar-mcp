from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litestar.config.app import AppConfig

from litestar import Litestar
from litestar.plugins import InitPluginProtocol

from litestar_mcp import MCP
from litestar_mcp.config import MCPConfig
from litestar_mcp.plugin import LitestarMCP


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
