"""Serialization tests for the MCP JSON-RPC route: request body parsing."""

from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


def _make_app() -> Litestar:
    @get("/users", opt={"mcp_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> list[dict[str, Any]]:
        return [{"id": 1, "name": "Alice"}]

    return Litestar(route_handlers=[list_users], plugins=[LitestarMCP()])


def test_valid_jsonrpc_body_is_decoded_via_litestar_serializer() -> None:
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


def test_malformed_body_returns_parse_error() -> None:
    """Malformed JSON should produce a JSON-RPC ParseError (-32700)."""
    app = _make_app()
    with TestClient(app=app) as client:
        resp = client.post(
            "/mcp",
            content=b"{not valid json",
            headers={"content-type": "application/json"},
        )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["error"]["code"] == -32700
    assert payload["error"]["message"] == "Parse error"
