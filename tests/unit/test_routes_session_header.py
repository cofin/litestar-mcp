"""Session header handling tests for POST/DELETE `/mcp`."""

from typing import Any

from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


def _make_app() -> Litestar:
    @get("/items", opt={"mcp_tool": "list_items"}, sync_to_thread=False)
    def list_items() -> list[dict[str, Any]]:
        return [{"id": 1}]

    return Litestar(route_handlers=[list_items], plugins=[LitestarMCP()])


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


def test_initialize_emits_session_header() -> None:
    with TestClient(app=_make_app()) as client:
        resp = _rpc(
            client,
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "x"}},
        )
        assert resp.status_code == 200
        assert resp.headers.get("mcp-session-id")


def test_post_without_header_returns_400() -> None:
    with TestClient(app=_make_app()) as client:
        resp = _rpc(client, "tools/list")
        assert resp.status_code == 400


def test_post_with_unknown_header_returns_404_with_rpc_error() -> None:
    with TestClient(app=_make_app()) as client:
        resp = _rpc(client, "tools/list", headers={"Mcp-Session-Id": "unknown"})
        assert resp.status_code == 404
        payload = resp.json()
        assert payload["error"]["code"] == -32000


def test_delete_without_header_returns_400() -> None:
    with TestClient(app=_make_app()) as client:
        resp = client.delete("/mcp")
        assert resp.status_code == 400


def test_delete_removes_session_and_404_afterwards() -> None:
    with TestClient(app=_make_app()) as client:
        init = _rpc(
            client,
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {}},
        )
        session_id = init.headers["mcp-session-id"]
        assert client.delete("/mcp", headers={"Mcp-Session-Id": session_id}).status_code == 204
        after = _rpc(client, "tools/list", headers={"Mcp-Session-Id": session_id})
        assert after.status_code == 404


def test_post_only_flow_across_multiple_calls() -> None:
    with TestClient(app=_make_app()) as client:
        init = _rpc(
            client,
            "initialize",
            {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {}},
        )
        session_id = init.headers["mcp-session-id"]

        # Mark initialized via notification
        client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": session_id},
        )

        # Many subsequent POSTs share the same session id
        for _ in range(3):
            r = _rpc(client, "tools/list", headers={"Mcp-Session-Id": session_id})
            assert r.status_code == 200
            assert r.headers.get("mcp-session-id") == session_id


def test_ping_with_no_session_is_allowed() -> None:
    with TestClient(app=_make_app()) as client:
        resp = _rpc(client, "ping")
        assert resp.status_code == 200
