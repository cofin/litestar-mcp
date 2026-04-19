"""End-to-end tests for :class:`MCPOptKeys` — renamed opt keys work across
discovery (``plugin.py``) and description rendering (routes + manifests).
"""

from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.config import MCPConfig, MCPOptKeys


def _init(client: TestClient[Any]) -> str:
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers["mcp-session-id"]
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    return sid


def _rpc(client: TestClient[Any], method: str, sid: str) -> dict[str, Any]:
    resp = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method},
        headers={"Mcp-Session-Id": sid},
    )
    return resp.json()  # type: ignore[no-any-return]


def test_renamed_tool_and_resource_opt_keys_drive_discovery() -> None:
    opt_keys = MCPOptKeys(tool="x_tool", resource="x_resource")

    @get("/users", opt={"x_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> list[dict[str, Any]]:
        """List users."""
        return []

    @get("/config", opt={"x_resource": "app_config"}, sync_to_thread=False)
    def get_config() -> dict[str, Any]:
        """App config."""
        return {}

    # Handlers using the *default* opt keys must NOT be discovered under the renamed config.
    @get("/ignored-tool", opt={"mcp_tool": "nope_tool"}, sync_to_thread=False)
    def ignored_tool() -> dict[str, Any]:
        """Should not be registered."""
        return {}

    plugin = LitestarMCP(MCPConfig(opt_keys=opt_keys))
    app = Litestar(route_handlers=[list_users, get_config, ignored_tool], plugins=[plugin])

    assert "list_users" in plugin.discovered_tools
    assert "app_config" in plugin.discovered_resources
    assert "nope_tool" not in plugin.discovered_tools

    with TestClient(app=app) as client:
        sid = _init(client)
        tools = _rpc(client, "tools/list", sid)["result"]["tools"]
        resources = _rpc(client, "resources/list", sid)["result"]["resources"]

        tool_names = {t["name"] for t in tools}
        assert "list_users" in tool_names
        assert "nope_tool" not in tool_names
        resource_names = {r["name"] for r in resources}
        assert "app_config" in resource_names


def test_renamed_description_opt_keys_render_through_endpoints() -> None:
    opt_keys = MCPOptKeys(
        tool="x_tool",
        description="x_description",
        when_to_use="x_when_to_use",
    )

    @get(
        "/users",
        opt={
            "x_tool": "list_users",
            "x_description": "LLM prose for users.",
            "x_when_to_use": "Asked for users.",
        },
        sync_to_thread=False,
    )
    def list_users() -> list[dict[str, Any]]:
        """ignored-docstring."""
        return []

    app = Litestar(
        route_handlers=[list_users],
        plugins=[LitestarMCP(MCPConfig(opt_keys=opt_keys))],
    )

    with TestClient(app=app) as client:
        sid = _init(client)
        tools = _rpc(client, "tools/list", sid)["result"]["tools"]
        descr = next(t["description"] for t in tools if t["name"] == "list_users")
        assert descr.startswith("LLM prose for users.")
        assert "## When to use\nAsked for users." in descr
        assert "ignored-docstring" not in descr

        # Well-known manifests mirror the rendered description.
        agent_card = client.get("/.well-known/agent-card.json").json()
        ac_descr = next(s["description"] for s in agent_card["skills"] if s["id"] == "list_users")
        assert ac_descr == descr

        mcp_manifest = client.get("/.well-known/mcp-server.json").json()
        mm_descr = next(t["description"] for t in mcp_manifest["tools"] if t["name"] == "list_users")
        assert mm_descr == descr


def test_default_opt_keys_unchanged_when_config_omits_opt_keys() -> None:
    """Regression guard: apps that don't set opt_keys still use the ``mcp_*`` defaults."""

    @get("/users", opt={"mcp_tool": "list_users", "mcp_description": "prose"}, sync_to_thread=False)
    def list_users() -> list[dict[str, Any]]:
        """Docstring."""
        return []

    plugin = LitestarMCP(MCPConfig())  # No opt_keys override.
    app = Litestar(route_handlers=[list_users], plugins=[plugin])
    assert plugin.config.opt_keys == MCPOptKeys()

    with TestClient(app=app) as client:
        sid = _init(client)
        tools = _rpc(client, "tools/list", sid)["result"]["tools"]
        descr = next(t["description"] for t in tools if t["name"] == "list_users")
        assert descr == "prose"
