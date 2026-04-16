"""Tests for MCP Streamable HTTP session lifecycle."""

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


def test_initialize_returns_session_header() -> None:
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
        assert resp.headers.get("mcp-session-id")


def test_post_without_session_after_initialize_rejected() -> None:
    app = _make_transport_app()
    with TestClient(app=app) as client:
        resp = _rpc(client, "tools/list")
        assert resp.status_code == 400


def test_post_with_unknown_session_returns_404() -> None:
    app = _make_transport_app()
    with TestClient(app=app) as client:
        resp = _rpc(client, "tools/list", headers={"Mcp-Session-Id": "does-not-exist"})
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == -32000


def test_delete_session_endpoint_registered() -> None:
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
    assert "DELETE" in methods


def test_full_post_only_flow() -> None:
    """Initialize → notifications/initialized → tools/list → DELETE round-trip."""
    app = _make_transport_app()
    with TestClient(app=app) as client:
        init = _rpc(
            client,
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        )
        session_id = init.headers["mcp-session-id"]

        # notifications/initialized (notification: no id)
        notif = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": session_id},
        )
        assert notif.status_code in {200, 204}

        # tools/list with session header should succeed
        listed = _rpc(client, "tools/list", headers={"Mcp-Session-Id": session_id})
        assert listed.status_code == 200
        assert "result" in listed.json()
        assert listed.headers.get("mcp-session-id") == session_id

        # DELETE terminates
        deleted = client.delete("/mcp", headers={"Mcp-Session-Id": session_id})
        assert deleted.status_code == 204

        # Subsequent POST should 404
        after = _rpc(client, "tools/list", headers={"Mcp-Session-Id": session_id})
        assert after.status_code == 404
