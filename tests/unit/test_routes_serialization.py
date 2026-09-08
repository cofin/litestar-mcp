"""Serialization tests for the MCP JSON-RPC route: request body parsing."""

import json
from typing import Any

import pytest
from litestar import Litestar, get
from litestar.di import NamedDependency, Provide
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


def _make_app() -> "Litestar":
    @get("/users", opt={"mcp_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> "list[dict[str, Any]]":
        return [{"id": 1, "name": "Alice"}]

    return Litestar(route_handlers=[list_users], plugins=[LitestarMCP()])


def test_valid_jsonrpc_body_is_decoded_via_litestar_serializer() -> "None":
    """A well-formed JSON-RPC body should decode and dispatch correctly."""
    app = _make_app()
    with TestClient(app=app) as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
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
