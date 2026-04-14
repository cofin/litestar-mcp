"""Tests for stateless Streamable HTTP transport behavior."""

from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


def _rpc(
    client: TestClient[Any],
    method: str,
    params: "dict[str, Any] | None" = None,
    headers: "dict[str, str] | None" = None,
) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers or {})


def _make_transport_app() -> Litestar:
    @get("/users", opt={"mcp_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> list[dict[str, Any]]:
        return [{"id": 1, "name": "Alice"}]

    return Litestar(route_handlers=[list_users], plugins=[LitestarMCP()])


def test_initialize_does_not_assign_session_id() -> None:
    app = _make_transport_app()
    with TestClient(app=app) as client:
        resp = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        assert resp.status_code == 200
        assert "mcp-session-id" not in resp.headers


def test_invalid_session_header_is_ignored_in_stateless_mode() -> None:
    app = _make_transport_app()
    with TestClient(app=app) as client:
        resp = _rpc(client, "tools/list", headers={"mcp-session-id": "obsolete-session"})
        assert resp.status_code == 200
        assert "result" in resp.json()


def test_delete_session_endpoint_removed() -> None:
    app = _make_transport_app()
    with TestClient(app=app) as client:
        resp = client.delete("/mcp")
        assert resp.status_code == 405


def test_get_mcp_route_is_registered_without_delete() -> None:
    app = _make_transport_app()
    methods: set[str] = set()

    for route in app.routes:
        if getattr(route, "path", None) != "/mcp":
            continue
        if hasattr(route, "route_handlers"):
            for handler in route.route_handlers:  # pyright: ignore[reportAttributeAccessIssue]
                methods.update(handler.http_methods or [])

    assert "GET" in methods
    assert "POST" in methods
    assert "DELETE" not in methods
