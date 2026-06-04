"""Primitive-specific MCP error mapping coverage."""

from typing import Any

import pytest
from litestar import Litestar, Response, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig

pytestmark = pytest.mark.unit


def _ensure_session(client: TestClient[Any]) -> str:
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
    return str(sid)


def _rpc(client: TestClient[Any], method: str, params: dict[str, Any] | None = None, *, sid: str) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Mcp-Session-Id": sid},
    )
    data: dict[str, Any] = response.json()
    return data


def test_tool_execution_error_stays_in_tool_result_envelope() -> None:
    @get("/tool-error", mcp_tool="tool_error", sync_to_thread=False)
    def tool_error() -> Response[dict[str, str]]:
        return Response({"error": "bad input"}, status_code=400)

    app = Litestar(route_handlers=[tool_error], plugins=[LitestarMCP(MCPConfig())])
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        response = _rpc(client, "tools/call", {"name": "tool_error", "arguments": {}}, sid=sid)
        assert "error" not in response
        assert response["result"]["isError"] is True
        assert "bad input" in response["result"]["content"][0]["text"]


def test_prompt_handler_execution_error_maps_to_internal_error_with_data() -> None:
    @get("/prompt-error", mcp_prompt="prompt_error", sync_to_thread=False)
    def prompt_error() -> Response[dict[str, str]]:
        return Response({"error": "bad input"}, status_code=400)

    app = Litestar(route_handlers=[prompt_error], plugins=[LitestarMCP(MCPConfig())])
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        response = _rpc(client, "prompts/get", {"name": "prompt_error"}, sid=sid)
        assert response["error"]["code"] == -32603
        assert response["error"]["message"] == "Prompt execution failed"
        assert response["error"]["data"] == {"statusCode": 400, "content": {"error": "bad input"}}


def test_resource_not_found_uses_mcp_resource_code_with_uri_data() -> None:
    app = Litestar(route_handlers=[], plugins=[LitestarMCP(MCPConfig())])
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        response = _rpc(client, "resources/read", {"uri": "litestar://missing"}, sid=sid)
        assert response["error"]["code"] == -32002
        assert response["error"]["message"] == "Resource not found"
        assert response["error"]["data"] == {"uri": "litestar://missing"}


def test_resource_read_failure_maps_to_internal_error_with_data() -> None:
    @get("/resource-error", mcp_resource="resource_error", sync_to_thread=False)
    def resource_error() -> Response[dict[str, str]]:
        return Response({"error": "failed read"}, status_code=503)

    app = Litestar(route_handlers=[resource_error], plugins=[LitestarMCP(MCPConfig())])
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        response = _rpc(client, "resources/read", {"uri": "litestar://resource_error"}, sid=sid)
        assert response["error"]["code"] == -32603
        assert response["error"]["message"] == "Resource read failed"
        assert response["error"]["data"] == {"statusCode": 503, "content": {"error": "failed read"}}
