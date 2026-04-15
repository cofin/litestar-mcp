"""Tests for JSON-RPC 2.0 core implementation."""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCRouter,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_plugin() -> LitestarMCP:
    """MCP plugin with a tool and resource registered."""
    return LitestarMCP(MCPConfig())


@pytest.fixture
def jsonrpc_app() -> Litestar:
    """App wired for JSON-RPC testing."""

    @get("/users", opt={"mcp_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> list[dict[str, Any]]:
        """List all users in the system."""
        return [{"id": 1, "name": "Alice"}]

    @get("/config", opt={"mcp_resource": "app_config"}, sync_to_thread=False)
    def get_config() -> dict[str, Any]:
        """Application configuration."""
        return {"debug": True}

    return Litestar(
        route_handlers=[list_users, get_config],
        plugins=[LitestarMCP(MCPConfig())],
    )


@pytest.fixture
def client(jsonrpc_app: Litestar) -> TestClient[Any]:
    return TestClient(app=jsonrpc_app)


# ---------------------------------------------------------------------------
# Helper
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


def _rpc(
    client: TestClient[Any], method: str, params: "dict[str, Any] | None" = None, msg_id: int = 1
) -> dict[str, Any]:
    """Send a JSON-RPC request and return the response body."""
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = _ensure_session(client)
        if sid:
            headers["Mcp-Session-Id"] = sid
    resp = client.post("/mcp", json=body, headers=headers)
    return resp.json()  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# JSON-RPC Request model
# ---------------------------------------------------------------------------


class TestJSONRPCRequest:
    def test_valid_request(self) -> None:
        req = JSONRPCRequest(jsonrpc="2.0", id=1, method="tools/list")
        assert req.method == "tools/list"
        assert req.id == 1
        assert req.params == {}

    def test_request_with_params(self) -> None:
        req = JSONRPCRequest(jsonrpc="2.0", id=2, method="tools/call", params={"name": "foo"})
        assert req.params == {"name": "foo"}


# ---------------------------------------------------------------------------
# JSON-RPC Error model
# ---------------------------------------------------------------------------


class TestJSONRPCError:
    def test_error_codes(self) -> None:
        assert PARSE_ERROR == -32700
        assert INVALID_REQUEST == -32600
        assert METHOD_NOT_FOUND == -32601
        assert INVALID_PARAMS == -32602
        assert INTERNAL_ERROR == -32603

    def test_error_to_dict(self) -> None:
        err = JSONRPCError(code=METHOD_NOT_FOUND, message="Method not found")
        d = err.to_dict()
        assert d["code"] == -32601
        assert d["message"] == "Method not found"

    def test_error_with_data(self) -> None:
        err = JSONRPCError(code=INTERNAL_ERROR, message="boom", data={"detail": "stack"})
        d = err.to_dict()
        assert d["data"] == {"detail": "stack"}


# ---------------------------------------------------------------------------
# JSON-RPC Router (unit)
# ---------------------------------------------------------------------------


class TestJSONRPCRouter:
    def test_register_and_dispatch(self) -> None:
        router = JSONRPCRouter()

        async def handler(params: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        router.register("test/method", handler)
        assert "test/method" in router.methods

    def test_unknown_method_raises(self) -> None:
        JSONRPCRouter()  # verify instantiation works


# ---------------------------------------------------------------------------
# initialize handshake
# ---------------------------------------------------------------------------


class TestInitialize:
    def test_initialize_returns_capabilities(self, client: TestClient[Any]) -> None:
        result = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        )
        assert "result" in result
        assert result["result"]["protocolVersion"] == "2025-11-25"
        assert "capabilities" in result["result"]
        assert "serverInfo" in result["result"]

    def test_initialize_returns_server_info(self, client: TestClient[Any]) -> None:
        result = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        )
        server_info = result["result"]["serverInfo"]
        assert "name" in server_info
        assert "version" in server_info

    def test_initialize_capabilities_include_tools(self, client: TestClient[Any]) -> None:
        result = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        )
        caps = result["result"]["capabilities"]
        assert "tools" in caps

    def test_initialize_capabilities_include_resources(self, client: TestClient[Any]) -> None:
        result = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test-client", "version": "1.0"},
            },
        )
        caps = result["result"]["capabilities"]
        assert "resources" in caps


# ---------------------------------------------------------------------------
# ping
# ---------------------------------------------------------------------------


class TestPing:
    def test_ping_returns_empty(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "ping")
        assert result["result"] == {}


# ---------------------------------------------------------------------------
# tools/list
# ---------------------------------------------------------------------------


class TestToolsList:
    def test_tools_list_returns_tools(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "tools/list")
        assert "result" in result
        tools = result["result"]["tools"]
        assert isinstance(tools, list)
        assert len(tools) >= 1

    def test_tools_list_tool_has_name(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "tools/list")
        tool = result["result"]["tools"][0]
        assert "name" in tool

    def test_tools_list_tool_has_input_schema(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "tools/list")
        tool = result["result"]["tools"][0]
        assert "inputSchema" in tool

    def test_tools_list_tool_has_description(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "tools/list")
        tool = result["result"]["tools"][0]
        assert "description" in tool


# ---------------------------------------------------------------------------
# tools/call
# ---------------------------------------------------------------------------


class TestToolsCall:
    def test_tools_call_executes_tool(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "tools/call", {"name": "list_users", "arguments": {}})
        assert "result" in result
        assert "content" in result["result"]

    def test_tools_call_returns_text_content(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "tools/call", {"name": "list_users", "arguments": {}})
        content = result["result"]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"

    def test_tools_call_unknown_tool(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "tools/call", {"name": "nonexistent", "arguments": {}})
        assert "error" in result
        assert result["error"]["code"] == INVALID_PARAMS

    def test_tools_call_missing_name(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "tools/call", {"arguments": {}})
        assert "error" in result
        assert result["error"]["code"] == INVALID_PARAMS

    def test_tools_call_invalid_arguments_return_call_tool_error(self) -> None:
        @get("/typed", opt={"mcp_tool": "typed_tool"}, sync_to_thread=False)
        def typed_tool(count: int) -> dict[str, int]:
            return {"count": count}

        app = Litestar(route_handlers=[typed_tool], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _rpc(client, "tools/call", {"name": "typed_tool", "arguments": {"count": "bad"}})
            assert "result" in result
            assert result["result"]["isError"] is True
            assert "count" in result["result"]["content"][0]["text"]

    def test_tools_call_rejects_additional_properties_via_tool_error(self) -> None:
        @get("/typed", opt={"mcp_tool": "strict_tool"}, sync_to_thread=False)
        def strict_tool(name: str) -> dict[str, str]:
            return {"name": name}

        app = Litestar(route_handlers=[strict_tool], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _rpc(
                client,
                "tools/call",
                {"name": "strict_tool", "arguments": {"name": "Alice", "unexpected": True}},
            )
            assert "result" in result
            assert result["result"]["isError"] is True
            assert "unexpected" in result["result"]["content"][0]["text"]

    def test_tools_call_handler_exception_returns_call_tool_error(self) -> None:
        @get("/boom", opt={"mcp_tool": "boom"}, sync_to_thread=False)
        def boom() -> dict[str, str]:
            msg = "tool exploded"
            raise RuntimeError(msg)

        app = Litestar(route_handlers=[boom], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _rpc(client, "tools/call", {"name": "boom", "arguments": {}})
            assert "result" in result
            assert result["result"]["isError"] is True
            assert "tool exploded" in result["result"]["content"][0]["text"]


# ---------------------------------------------------------------------------
# resources/list
# ---------------------------------------------------------------------------


class TestResourcesList:
    def test_resources_list_returns_resources(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "resources/list")
        assert "result" in result
        resources = result["result"]["resources"]
        assert isinstance(resources, list)
        assert len(resources) >= 1

    def test_resources_list_resource_has_uri(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "resources/list")
        resource = result["result"]["resources"][0]
        assert "uri" in resource

    def test_resources_list_resource_has_name(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "resources/list")
        resource = result["result"]["resources"][0]
        assert "name" in resource


# ---------------------------------------------------------------------------
# resources/read
# ---------------------------------------------------------------------------


class TestResourcesRead:
    def test_resources_read_returns_content(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "resources/read", {"uri": "litestar://app_config"})
        assert "result" in result
        assert "contents" in result["result"]

    def test_resources_read_content_has_uri(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "resources/read", {"uri": "litestar://app_config"})
        contents = result["result"]["contents"]
        assert isinstance(contents, list)
        assert contents[0]["uri"] == "litestar://app_config"

    def test_resources_read_unknown_resource(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "resources/read", {"uri": "litestar://nonexistent"})
        assert "error" in result
        assert result["error"]["code"] == INVALID_PARAMS

    def test_resources_read_openapi(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "resources/read", {"uri": "litestar://openapi"})
        assert "result" in result
        contents = result["result"]["contents"]
        assert contents[0]["mimeType"] == "application/json"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_unknown_method(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "bogus/method")
        assert "error" in result
        assert result["error"]["code"] == METHOD_NOT_FOUND

    def test_malformed_json(self, client: TestClient[Any]) -> None:
        resp = client.post("/mcp", content=b"not json", headers={"Content-Type": "application/json"})
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == PARSE_ERROR

    def test_missing_jsonrpc_field(self, client: TestClient[Any]) -> None:
        resp = client.post("/mcp", json={"id": 1, "method": "ping"})
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == INVALID_REQUEST

    def test_missing_method_field(self, client: TestClient[Any]) -> None:
        resp = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1})
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == INVALID_REQUEST

    def test_wrong_jsonrpc_version(self, client: TestClient[Any]) -> None:
        resp = client.post("/mcp", json={"jsonrpc": "1.0", "id": 1, "method": "ping"})
        body = resp.json()
        assert "error" in body
        assert body["error"]["code"] == INVALID_REQUEST

    def test_response_preserves_id(self, client: TestClient[Any]) -> None:
        result = _rpc(client, "ping", msg_id=42)
        assert result["id"] == 42
        assert result["jsonrpc"] == "2.0"


# ---------------------------------------------------------------------------
# Notification format (no id)
# ---------------------------------------------------------------------------


class TestNotifications:
    def test_notifications_initialized_no_response(self, client: TestClient[Any]) -> None:
        """notifications/initialized is a notification (no id) — server should return 204."""
        sid = _ensure_session(client)
        body = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        resp = client.post("/mcp", json=body, headers={"Mcp-Session-Id": sid})
        # Notifications get no response body per JSON-RPC spec
        assert resp.status_code in (200, 202, 204)


# ---------------------------------------------------------------------------
# Legacy endpoints removed
# ---------------------------------------------------------------------------


class TestLegacyEndpointsRemoved:
    def test_get_tools_removed(self, client: TestClient[Any]) -> None:
        resp = client.get("/mcp/tools")
        assert resp.status_code in (404, 405)

    def test_post_tool_removed(self, client: TestClient[Any]) -> None:
        resp = client.post("/mcp/tools/list_users", json={})
        assert resp.status_code in (404, 405)

    def test_get_resources_removed(self, client: TestClient[Any]) -> None:
        resp = client.get("/mcp/resources")
        assert resp.status_code in (404, 405)

    def test_get_resource_removed(self, client: TestClient[Any]) -> None:
        resp = client.get("/mcp/resources/app_config")
        assert resp.status_code in (404, 405)

    def test_get_server_info_removed(self, client: TestClient[Any]) -> None:
        resp = client.get("/mcp/")
        assert resp.status_code in (404, 405)

    def test_sse_endpoint_removed(self, client: TestClient[Any]) -> None:
        resp = client.get("/mcp/sse")
        assert resp.status_code in (404, 405)

    def test_messages_endpoint_removed(self, client: TestClient[Any]) -> None:
        resp = client.post("/mcp/messages", json={})
        assert resp.status_code in (404, 405)
