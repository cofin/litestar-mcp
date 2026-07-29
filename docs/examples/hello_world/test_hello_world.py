"""Pytest companion for ``docs/examples/hello_world/main.py``.

Exercises the marked snippet (``build_app``) end-to-end via
``litestar.testing.TestClient`` and the project-standard JSON-RPC helper.
"""

from typing import Any

from litestar.testing import TestClient

from docs.examples.hello_world.main import build_app


def _rpc(
    client: "TestClient[Any]",
    method: "str",
    params: "dict[str, Any] | None" = None,
) -> "dict[str, Any]":
    """Execute one stateless MCP request."""
    request_params = dict(params or {})
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
        "io.modelcontextprotocol/clientCapabilities": {},
    }
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method, "params": request_params}
    headers = {"MCP-Protocol-Version": "2026-07-28", "Mcp-Method": method}
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


def test_hello_endpoint_returns_200() -> "None":
    with TestClient(app=build_app()) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert resp.json() == {"message": "Hello from Litestar!"}


def test_status_endpoint_returns_200() -> "None":
    with TestClient(app=build_app()) as client:
        resp = client.get("/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "healthy"


def test_server_discover_returns_configured_server_name() -> "None":
    with TestClient(app=build_app()) as client:
        result = _rpc(
            client,
            "server/discover",
        )
        assert result["result"]["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "Hello World API"


def test_tools_list_is_empty_when_no_marked_tools() -> "None":
    with TestClient(app=build_app()) as client:
        result = _rpc(client, "tools/list", {})
        assert result["result"]["tools"] == []
