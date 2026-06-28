"""Unit tests for JSONRPCRouter caching and invalidation."""

from __future__ import annotations

from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


def _ensure_session(client: TestClient[Any], base: str = "/mcp") -> str:
    key = f"_mcp_session::{base}"
    sid = getattr(client, key, None)
    if sid is not None:
        return sid  # type: ignore[no-any-return]
    init = client.post(
        base,
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    client.post(
        base,
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    setattr(client, key, sid)
    return str(sid)


def _rpc(
    client: TestClient[Any],
    method: str,
    params: dict[str, Any] | None = None,
    msg_id: int = 1,
    base: str = "/mcp",
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = _ensure_session(client, base)
        if sid:
            headers["Mcp-Session-Id"] = sid
    return client.post(base, json=body, headers=headers).json()  # type: ignore[no-any-return]


def test_router_caching_and_invalidation() -> None:
    @get("/users", opt={"mcp_tool": "list_users"})
    async def get_users() -> list[dict[str, Any]]:
        return [{"id": 1, "name": "Alice"}]

    @get("/dynamic", opt={"mcp_tool": "dynamic_tool"})
    async def dynamic_tool() -> dict[str, str]:
        return {"result": "dynamic"}

    plugin = LitestarMCP()
    app = Litestar(plugins=[plugin], route_handlers=[get_users])
    with TestClient(app=app) as client:
        # 1. Initialize and send first request to build and cache the router
        _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )

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
