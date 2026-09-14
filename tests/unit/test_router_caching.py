"""Unit tests for JSONRPCRouter caching and invalidation."""

from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP

PROTOCOL_VERSION = "2026-07-28"
_NAME_FIELDS = {"tools/call": "name", "resources/read": "uri", "prompts/get": "name"}


def _rpc(client: "TestClient[Any]", method: "str", params: "dict[str, Any] | None" = None) -> "dict[str, Any]":
    request_params = dict(params or {})
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    headers = {"MCP-Protocol-Version": PROTOCOL_VERSION, "Mcp-Method": method}
    name_field = _NAME_FIELDS.get(method)
    if name_field is not None:
        headers["Mcp-Name"] = str(request_params.get(name_field, ""))
    body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": request_params}
    data: dict[str, Any] = client.post("/mcp", json=body, headers=headers).json()
    return data


def test_router_caching_and_invalidation() -> "None":
    @get("/users", opt={"mcp_tool": "list_users"})
    async def get_users() -> "list[dict[str, Any]]":
        return [{"id": 1, "name": "Alice"}]

    @get("/dynamic", opt={"mcp_tool": "dynamic_tool"})
    async def dynamic_tool() -> "dict[str, str]":
        return {"result": "dynamic"}

    plugin = LitestarMCP()
    app = Litestar(plugins=[plugin], route_handlers=[get_users])
    with TestClient(app=app) as client:
        # 1. First request builds and caches the router
        result = _rpc(client, "tools/list")
        assert len(result["result"]["tools"]) == 1

        router_1 = getattr(app.state, "mcp_router", None)
        assert router_1 is not None

        # 2. Second request should reuse the same router instance
        _rpc(client, "tools/list")
        router_2 = getattr(app.state, "mcp_router", None)
        assert router_1 is router_2

        # 3. Modify registry dynamically
        plugin.registry.register_tool("dynamic_tool", dynamic_tool)

        # Invalidation callback should have deleted the cached router
        assert not hasattr(app.state, "mcp_router")

        # 4. Next request should rebuild the router
        result2 = _rpc(client, "tools/list")
        router_3 = getattr(app.state, "mcp_router", None)
        assert router_3 is not None
        assert router_3 is not router_1

        # Verify that the newly registered tool is accessible through the rebuilt router
        tools = result2["result"]["tools"]
        tool_names = [t["name"] for t in tools]
        assert "list_users" in tool_names
        assert "dynamic_tool" in tool_names
