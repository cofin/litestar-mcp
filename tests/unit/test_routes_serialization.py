"""Serialization tests for the MCP JSON-RPC route: request body parsing."""

import json
from typing import Any

import msgspec
import pytest
from litestar import Litestar, get, post
from litestar.di import NamedDependency, Provide
from litestar.testing import TestClient

from litestar_mcp import MCP, LitestarMCP
from litestar_mcp.mcp.content import MCPBlobResource, MCPToolResult
from tests.unit.conftest import mcp_post


class _Money:
    def __init__(self, cents: int) -> None:
        self.cents = cents


class _CamelOut(msgspec.Struct, rename="camel"):
    line_item: str
    unit_qty: int = 1


def _make_app() -> "Litestar":
    @get("/users", opt={"mcp_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> "list[dict[str, Any]]":
        return [{"id": 1, "name": "Alice"}]

    return Litestar(route_handlers=[list_users], plugins=[LitestarMCP()])


def test_valid_jsonrpc_body_is_decoded_via_litestar_serializer() -> "None":
    """A well-formed JSON-RPC body should decode and dispatch correctly."""
    app = _make_app()
    with TestClient(app=app) as client:
        resp = mcp_post(client, "tools/list")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["jsonrpc"] == "2.0"
    assert "result" in payload


def test_malformed_body_returns_parse_error() -> "None":
    """Malformed JSON should produce a JSON-RPC ParseError (-32700)."""
    app = _make_app()
    with TestClient(app=app) as client:
        resp = client.post(
            "/mcp",
            content=b"{not valid json",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 400
    payload = resp.json()
    assert payload["error"]["code"] == -32700
    assert payload["error"]["message"] == "Parse error"


@pytest.mark.parametrize("progress", [False, True])
@pytest.mark.parametrize(
    "id_fields",
    [{}, {"id": None}, {"id": True}, {"id": False}, {"id": []}, {"id": {}}, {"id": 1.5}],
    ids=["missing", "null", "true", "false", "array", "object", "fractional"],
)
def test_invalid_request_id_is_rejected_before_tool_dependencies(id_fields: dict[str, Any], progress: bool) -> None:
    calls: list[str] = []

    async def dependency() -> str:
        calls.append("dependency")
        return "value"

    @get("/work", mcp_tool="work", dependencies={"value": Provide(dependency)})
    async def work(value: NamedDependency[str]) -> str:
        calls.append("tool")
        return value

    app = Litestar(route_handlers=[work], plugins=[LitestarMCP()])
    meta: dict[str, Any] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    if progress:
        meta["progressToken"] = "token"
    body = {
        "jsonrpc": "2.0",
        **id_fields,
        "method": "tools/call",
        "params": {"name": "work", "arguments": {}, "_meta": meta},
    }
    with TestClient(app=app) as client:
        response = client.post(
            "/mcp",
            content=json.dumps(body),
            headers={
                "content-type": "application/json",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/call",
                "Mcp-Name": "work",
            },
        )

    assert calls == []
    assert response.status_code == 400
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["id"] is None
    assert response.json()["error"]["code"] == -32600


@pytest.mark.parametrize("request_id", [0, -1, "", "request", 1.0])
@pytest.mark.parametrize("method", ["tools/list", "unknown"])
def test_valid_request_id_is_preserved_for_results_and_errors(request_id: Any, method: str) -> None:
    body = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            },
        },
    }
    with TestClient(app=_make_app()) as client:
        response = client.post(
            "/mcp",
            content=json.dumps(body),
            headers={
                "content-type": "application/json",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": method,
            },
        )

    assert response.json()["id"] == request_id
    if method == "tools/list":
        assert response.status_code == 200
        assert response.json()["result"]["tools"][0]["name"] == "list_users"
    else:
        assert response.status_code == 404
        assert response.json()["error"]["code"] == -32601


@pytest.mark.parametrize("surface", ["litestar_route", "standalone_mcp"])
def test_mcp_tool_result_applies_route_type_encoders_and_struct_rename(surface: str) -> None:
    encoders: dict[Any, Any] = {
        _Money: lambda money: f"${money.cents / 100:.2f}",
        bytes: lambda raw: raw.hex(),
    }

    def build_result() -> MCPToolResult:
        return MCPToolResult(
            content=[
                MCPBlobResource(uri="app://receipt", data=b"\x01\x02"),
                {"fee": _Money(250), "sig": b"\xca\xfe"},
            ],
            structured_content={
                "price": _Money(500),
                "raw": b"\xab\xcd",
                "item": _CamelOut(line_item="widget", unit_qty=2),
            },
        )

    if surface == "litestar_route":

        @post("/quote", mcp_tool="quote", type_encoders=encoders, sync_to_thread=False)
        def route_quote() -> MCPToolResult:
            return build_result()

        app = Litestar(route_handlers=[route_quote], plugins=[LitestarMCP()])
    else:
        mcp = MCP(name="quote-server")

        @mcp.tool(name="quote", type_encoders=encoders, sync_to_thread=False)
        def standalone_quote() -> MCPToolResult:
            return build_result()

        app = mcp.app

    with TestClient(app=app) as client:
        payload = mcp_post(client, "tools/call", {"name": "quote", "arguments": {}}).json()

    result = payload["result"]
    assert result["isError"] is False
    assert result["structuredContent"] == {
        "price": "$5.00",
        "raw": "abcd",
        "item": {"lineItem": "widget", "unitQty": 2},
    }
    assert result["content"][0] == {
        "type": "resource",
        "resource": {
            "uri": "app://receipt",
            "mimeType": "application/octet-stream",
            "blob": "AQI=",
        },
    }
    assert json.loads(result["content"][1]["text"]) == {"fee": "$2.50", "sig": "cafe"}


def test_mcp_tool_result_unset_structured_content_preserved_with_type_encoders() -> None:
    @post(
        "/fee",
        mcp_tool="fee",
        type_encoders={_Money: lambda money: f"${money.cents / 100:.2f}"},
        sync_to_thread=False,
    )
    def fee() -> MCPToolResult:
        return MCPToolResult(content={"fee": _Money(250)})

    app = Litestar(route_handlers=[fee], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        result = mcp_post(client, "tools/call", {"name": "fee", "arguments": {}}).json()["result"]

    assert result["isError"] is False
    assert "structuredContent" not in result
    assert json.loads(result["content"][0]["text"]) == {"fee": "$2.50"}


def test_mcp_tool_result_unregistered_custom_type_returns_tool_error() -> None:
    @post("/broken-quote", mcp_tool="broken_quote", sync_to_thread=False)
    def broken_quote() -> MCPToolResult:
        return MCPToolResult(content="ok", structured_content={"price": _Money(500)})

    app = Litestar(route_handlers=[broken_quote], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        result = mcp_post(client, "tools/call", {"name": "broken_quote", "arguments": {}}).json()["result"]

    assert result["isError"] is True
    assert "structuredContent" not in result
