"""Scopes are discovery metadata; they do not gate ``tools/call`` anymore.

Ch2 of ``v0.5.0-consumer-readiness`` removes the inline scope-enforcement
block in ``routes.py``. Guards (``handler.resolve_guards()``) become the
canonical access-control surface, matching the HTTP path exactly.

Related integration coverage lives in
``tests/integration/test_guard_inheritance_mcp.py``.
"""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import AsyncTestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.utils import mcp_tool

pytestmark = pytest.mark.unit


async def _init_and_get_session(client: AsyncTestClient[Any]) -> str:
    init = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    return str(sid)


async def _rpc(
    client: AsyncTestClient[Any],
    method: str,
    params: dict[str, Any] | None = None,
    sid: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers = {"Mcp-Session-Id": sid} if sid else {}
    return (await client.post("/mcp", json=body, headers=headers)).json()  # type: ignore[no-any-return]


@pytest.mark.anyio
async def test_scoped_tool_not_gated_by_inline_check() -> None:
    """``@mcp_tool(scopes=[...])`` no longer rejects anonymous callers."""

    @get("/x", sync_to_thread=False)
    @mcp_tool(name="t", scopes=["read:foo"])
    def handler() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[handler], plugins=[LitestarMCP(MCPConfig())])
    async with AsyncTestClient(app=app) as client:
        sid = await _init_and_get_session(client)
        resp = await _rpc(client, "tools/call", {"name": "t", "arguments": {}}, sid=sid)
        assert "result" in resp, f"expected success, got {resp}"
        assert resp["result"].get("isError") is not True, resp


@pytest.mark.anyio
async def test_scoped_tool_surfaces_annotations_scopes_on_tools_list() -> None:
    """``scopes=[...]`` surfaces on ``tools/list`` under ``annotations.scopes``."""

    @get("/x", sync_to_thread=False)
    @mcp_tool(name="t", scopes=["read:foo", "write:foo"])
    def handler() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[handler], plugins=[LitestarMCP(MCPConfig())])
    async with AsyncTestClient(app=app) as client:
        sid = await _init_and_get_session(client)
        resp = await _rpc(client, "tools/list", sid=sid)
        tool = next(t for t in resp["result"]["tools"] if t["name"] == "t")
        assert tool["annotations"]["scopes"] == ["read:foo", "write:foo"]


@pytest.mark.anyio
async def test_explicit_annotations_scopes_wins_over_decorator_scopes() -> None:
    """Explicit ``annotations.scopes`` takes precedence over decorator ``scopes``."""

    @get("/x", sync_to_thread=False)
    @mcp_tool(
        name="t",
        scopes=["read:foo"],
        annotations={"scopes": ["write:foo"], "audience": ["user"]},
    )
    def handler() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[handler], plugins=[LitestarMCP(MCPConfig())])
    async with AsyncTestClient(app=app) as client:
        sid = await _init_and_get_session(client)
        resp = await _rpc(client, "tools/list", sid=sid)
        tool = next(t for t in resp["result"]["tools"] if t["name"] == "t")
        assert tool["annotations"]["scopes"] == ["write:foo"]  # explicit wins
        assert tool["annotations"]["audience"] == ["user"]
