"""Tests for MCP tool/resource filtering."""

from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.utils import should_include_handler

# ---------------------------------------------------------------------------
# Unit tests for should_include_handler
# ---------------------------------------------------------------------------


class TestShouldIncludeHandler:
    def test_no_filters_includes_all(self) -> None:
        config = MCPConfig()
        assert should_include_handler("any_name", set(), config) is True

    def test_include_operations(self) -> None:
        config = MCPConfig(include_operations=["list_users", "get_user"])
        assert should_include_handler("list_users", set(), config) is True
        assert should_include_handler("delete_user", set(), config) is False

    def test_exclude_operations(self) -> None:
        config = MCPConfig(exclude_operations=["delete_user"])
        assert should_include_handler("list_users", set(), config) is True
        assert should_include_handler("delete_user", set(), config) is False

    def test_exclude_takes_precedence_over_include(self) -> None:
        config = MCPConfig(
            include_operations=["list_users", "delete_user"],
            exclude_operations=["delete_user"],
        )
        assert should_include_handler("list_users", set(), config) is True
        assert should_include_handler("delete_user", set(), config) is False

    def test_include_tags(self) -> None:
        config = MCPConfig(include_tags=["public"])
        assert should_include_handler("foo", {"public"}, config) is True
        assert should_include_handler("bar", {"internal"}, config) is False
        assert should_include_handler("baz", set(), config) is False

    def test_exclude_tags(self) -> None:
        config = MCPConfig(exclude_tags=["internal"])
        assert should_include_handler("foo", {"public"}, config) is True
        assert should_include_handler("bar", {"internal"}, config) is False

    def test_tags_take_precedence_over_operations(self) -> None:
        config = MCPConfig(
            include_operations=["admin_tool"],
            exclude_tags=["internal"],
        )
        # admin_tool matches include_operations, but "internal" tag should exclude
        assert should_include_handler("admin_tool", {"internal"}, config) is False

    def test_include_tags_and_include_operations(self) -> None:
        config = MCPConfig(
            include_operations=["list_users"],
            include_tags=["public"],
        )
        # Must match tag filter (tags > operations)
        assert should_include_handler("list_users", set(), config) is False
        assert should_include_handler("list_users", {"public"}, config) is True


# ---------------------------------------------------------------------------
# Integration tests via JSON-RPC
# ---------------------------------------------------------------------------


def _ensure_session(client: TestClient[Any]) -> str:
    sid = getattr(client, "_mcp_session", None)
    if sid is not None:
        return sid  # type: ignore[no-any-return]
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    client._mcp_session = sid  # type: ignore[attr-defined]
    return str(sid)


def _rpc(client: TestClient[Any], method: str, params: "dict[str, Any] | None" = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = _ensure_session(client)
        if sid:
            headers["Mcp-Session-Id"] = sid
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


class TestFilteringIntegration:
    def test_include_operations_filters_tools(self) -> None:
        @get("/a", opt={"mcp_tool": "tool_a"}, sync_to_thread=False)
        def tool_a() -> str:
            """Tool A."""
            return "a"

        @get("/b", opt={"mcp_tool": "tool_b"}, sync_to_thread=False)
        def tool_b() -> str:
            """Tool B."""
            return "b"

        config = MCPConfig(include_operations=["tool_a"])
        app = Litestar(route_handlers=[tool_a, tool_b], plugins=[LitestarMCP(config)])
        client = TestClient(app=app)

        result = _rpc(client, "tools/list")
        tool_names = [t["name"] for t in result["result"]["tools"]]
        assert "tool_a" in tool_names
        assert "tool_b" not in tool_names

    def test_exclude_operations_filters_tools(self) -> None:
        @get("/a", opt={"mcp_tool": "tool_a"}, sync_to_thread=False)
        def tool_a() -> str:
            """Tool A."""
            return "a"

        @get("/b", opt={"mcp_tool": "tool_b"}, sync_to_thread=False)
        def tool_b() -> str:
            """Tool B."""
            return "b"

        config = MCPConfig(exclude_operations=["tool_b"])
        app = Litestar(route_handlers=[tool_a, tool_b], plugins=[LitestarMCP(config)])
        client = TestClient(app=app)

        result = _rpc(client, "tools/list")
        tool_names = [t["name"] for t in result["result"]["tools"]]
        assert "tool_a" in tool_names
        assert "tool_b" not in tool_names

    def test_include_operations_filters_tools_call(self) -> None:
        @get("/public", mcp_tool="public_tool", sync_to_thread=False)
        def public_tool() -> str:
            """Public tool."""
            return "public"

        @get("/secret", mcp_tool="secret_tool", sync_to_thread=False)
        def secret_tool() -> str:
            """Secret tool."""
            return "secret"

        config = MCPConfig(include_operations=["public_tool"])
        app = Litestar(route_handlers=[public_tool, secret_tool], plugins=[LitestarMCP(config)])
        client = TestClient(app=app)

        result = _rpc(client, "tools/list")
        tool_names = [t["name"] for t in result["result"]["tools"]]
        assert "public_tool" in tool_names
        assert "secret_tool" not in tool_names

        call_result = _rpc(client, "tools/call", {"name": "secret_tool", "arguments": {}})
        assert call_result["error"]["code"] == -32602
        assert call_result["error"]["message"] == "Tool not found: secret_tool"

    def test_exclude_tags_filters_resource_read(self) -> None:
        @get("/public-resource", mcp_resource="public_resource", tags=["public"], sync_to_thread=False)
        def public_resource() -> dict[str, str]:
            return {"scope": "public"}

        @get("/secret-resource", mcp_resource="secret_resource", tags=["internal"], sync_to_thread=False)
        def secret_resource() -> dict[str, str]:
            return {"scope": "secret"}

        config = MCPConfig(exclude_tags=["internal"])
        app = Litestar(route_handlers=[public_resource, secret_resource], plugins=[LitestarMCP(config)])
        client = TestClient(app=app)

        result = _rpc(client, "resources/list")
        resource_names = [r["name"] for r in result["result"]["resources"]]
        assert "public_resource" in resource_names
        assert "secret_resource" not in resource_names

        read_result = _rpc(client, "resources/read", {"uri": "litestar://secret_resource"})
        assert read_result["error"]["code"] == -32002
        assert read_result["error"]["message"] == "Resource not found"
        assert read_result["error"]["data"] == {"uri": "litestar://secret_resource"}

    def test_exclude_tags_filters_resource_template_read(self) -> None:
        @get(
            "/public-template/{item_id:int}",
            mcp_resource="public_template",
            mcp_resource_template="app://public/{item_id}",
            tags=["public"],
            sync_to_thread=False,
        )
        def public_template(item_id: int) -> dict[str, int]:
            return {"item_id": item_id}

        @get(
            "/secret-template/{item_id:int}",
            mcp_resource="secret_template",
            mcp_resource_template="app://secret/{item_id}",
            tags=["internal"],
            sync_to_thread=False,
        )
        def secret_template(item_id: int) -> dict[str, int]:
            return {"item_id": item_id}

        config = MCPConfig(exclude_tags=["internal"])
        app = Litestar(route_handlers=[public_template, secret_template], plugins=[LitestarMCP(config)])
        client = TestClient(app=app)

        result = _rpc(client, "resources/templates/list")
        template_names = [r["name"] for r in result["result"]["resourceTemplates"]]
        assert "public_template" in template_names
        assert "secret_template" not in template_names

        read_result = _rpc(client, "resources/read", {"uri": "app://secret/1"})
        assert read_result["error"]["code"] == -32002
        assert read_result["error"]["message"] == "Resource not found"
        assert read_result["error"]["data"] == {"uri": "app://secret/1"}
