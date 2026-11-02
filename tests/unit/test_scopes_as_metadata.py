"""Scopes are discovery metadata; they do not gate ``tools/call`` anymore.

Ch2 of ``v0.5.0-consumer-readiness`` removes the inline scope-enforcement
block in ``routes.py``. Guards (``handler.resolve_guards()``) become the
canonical access-control surface, matching the HTTP path exactly.

Related integration coverage lives in
``tests/integration/test_guard_inheritance_mcp.py``.
"""

from typing import Any

import pytest
from litestar import Litestar, delete, get, post, put
from litestar.testing import AsyncTestClient

from litestar_mcp import MCP, LitestarMCP, MCPConfig
from litestar_mcp.core.tools import tool
from litestar_mcp.utils import mcp_tool
from tests.unit.conftest import mcp_post_async

pytestmark = pytest.mark.unit


async def _rpc(
    client: "AsyncTestClient[Any]", method: "str", params: "dict[str, Any] | None" = None
) -> "dict[str, Any]":
    data: dict[str, Any] = (await mcp_post_async(client, method, params)).json()
    return data


@pytest.mark.anyio
async def test_scoped_tool_not_gated_by_inline_check() -> "None":
    """``@mcp_tool(scopes=[...])`` no longer rejects anonymous callers."""

    @get("/x", sync_to_thread=False)
    @mcp_tool(name="t", scopes=["read:foo"])
    def handler() -> "dict[str, str]":
        return {"ok": "yes"}

    app = Litestar(route_handlers=[handler], plugins=[LitestarMCP(MCPConfig())])
    async with AsyncTestClient(app=app) as client:
        resp = await _rpc(client, "tools/call", {"name": "t", "arguments": {}})
        assert "result" in resp, f"expected success, got {resp}"
        assert resp["result"].get("isError") is not True, resp


@pytest.mark.anyio
async def test_scoped_tool_surfaces_annotations_scopes_on_tools_list() -> "None":
    """``scopes=[...]`` surfaces on ``tools/list`` under ``annotations.scopes``."""

    @get("/x", sync_to_thread=False)
    @mcp_tool(name="t", scopes=["read:foo", "write:foo"])
    def handler() -> "dict[str, str]":
        return {"ok": "yes"}

    app = Litestar(route_handlers=[handler], plugins=[LitestarMCP(MCPConfig())])
    async with AsyncTestClient(app=app) as client:
        resp = await _rpc(client, "tools/list")
        tool = next(t for t in resp["result"]["tools"] if t["name"] == "t")
        assert tool["annotations"]["scopes"] == ["read:foo", "write:foo"]


@pytest.mark.anyio
async def test_explicit_annotations_scopes_wins_over_decorator_scopes() -> "None":
    """Explicit ``annotations.scopes`` takes precedence over decorator ``scopes``."""

    @get("/x", sync_to_thread=False)
    @mcp_tool(
        name="t",
        scopes=["read:foo"],
        annotations={"scopes": ["write:foo"], "audience": ["user"]},
    )
    def handler() -> "dict[str, str]":
        return {"ok": "yes"}

    app = Litestar(route_handlers=[handler], plugins=[LitestarMCP(MCPConfig())])
    async with AsyncTestClient(app=app) as client:
        resp = await _rpc(client, "tools/list")
        tool = next(t for t in resp["result"]["tools"] if t["name"] == "t")
        assert tool["annotations"]["scopes"] == ["write:foo"]
        assert tool["annotations"]["audience"] == ["user"]


@pytest.mark.anyio
async def test_http_method_annotation_inference_and_opt_overrides() -> "None":
    """HTTP verbs infer MCP ToolAnnotations defaults, with opt overrides taking precedence."""

    @get("/items", opt={"mcp_tool": "list_items"}, sync_to_thread=False)
    def list_items() -> "list[str]":
        """List items."""
        return []

    @post(
        "/items/search",
        opt={"mcp_tool": "search_items", "mcp_read_only": True},
        sync_to_thread=False,
    )
    def search_items() -> "list[str]":
        """Search items via POST."""
        return []

    @put("/items/{item_id:str}", opt={"mcp_tool": "replace_item"}, sync_to_thread=False)
    def replace_item(item_id: "str") -> "dict[str, str]":
        """Replace item."""
        return {"id": item_id}

    @delete(
        "/items/{item_id:str}",
        opt={
            "mcp_tool": "delete_item",
            "mcp_annotations": {"openWorldHint": False},
        },
        sync_to_thread=False,
    )
    @mcp_tool("delete_item", scopes=["items:delete"])
    def delete_item(item_id: "str") -> "None":
        """Delete item."""

    @tool(name="standalone_plain")
    def standalone_plain(x: "int") -> "int":
        """Compute x."""
        return x

    @tool(name="standalone_ro", read_only=True)
    def standalone_ro(x: "int") -> "int":
        """Read x."""
        return x

    plugin = LitestarMCP(MCPConfig())
    plugin.register_tool(standalone_plain)
    plugin.register_tool(standalone_ro)
    mcp_hub = MCP(
        "hub",
        plugins=[plugin],
        route_handlers=[list_items, search_items, replace_item, delete_item],
    )

    @mcp_hub.tool(name="hub_plain")
    def hub_plain(x: "int") -> "int":
        """Hub compute."""
        return x

    async with AsyncTestClient(app=mcp_hub.app) as client:
        resp = await _rpc(client, "tools/list")
        by_name = {t["name"]: t for t in resp["result"]["tools"]}

        assert by_name["list_items"]["annotations"] == {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
        }
        assert by_name["search_items"]["annotations"] == {
            "readOnlyHint": True,
        }
        assert by_name["replace_item"]["annotations"] == {
            "readOnlyHint": False,
            "idempotentHint": True,
        }
        assert by_name["delete_item"]["annotations"] == {
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
            "scopes": ["items:delete"],
        }
        assert "annotations" not in by_name["standalone_plain"]
        assert by_name["standalone_ro"]["annotations"] == {"readOnlyHint": True}
        assert "annotations" not in by_name["hub_plain"]


@pytest.mark.anyio
async def test_disable_http_method_annotation_inference() -> "None":
    """Setting ``infer_http_method_annotations=False`` suppresses HTTP verb inference."""

    @get("/items", opt={"mcp_tool": "list_items"}, sync_to_thread=False)
    def list_items() -> "list[str]":
        """List items."""
        return []

    app = Litestar(
        route_handlers=[list_items],
        plugins=[LitestarMCP(MCPConfig(infer_http_method_annotations=False))],
    )
    async with AsyncTestClient(app=app) as client:
        resp = await _rpc(client, "tools/list")
        tool_entry = next(t for t in resp["result"]["tools"] if t["name"] == "list_items")
        assert "annotations" not in tool_entry
