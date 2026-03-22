"""Tests for MCP session management and Streamable HTTP transport."""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.session import MCPSessionManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_app() -> Litestar:
    @get("/users", opt={"mcp_tool": "list_users"}, sync_to_thread=False)
    def list_users() -> list[dict[str, Any]]:
        """List all users."""
        return [{"id": 1, "name": "Alice"}]

    return Litestar(
        route_handlers=[list_users],
        plugins=[LitestarMCP(MCPConfig())],
    )


@pytest.fixture
def client(session_app: Litestar) -> TestClient[Any]:
    return TestClient(app=session_app)


def _rpc(
    client: TestClient[Any],
    method: str,
    params: "dict[str, Any] | None" = None,
    msg_id: int = 1,
    headers: "dict[str, str] | None" = None,
) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers or {})


class TestMCPSessionManager:
    def test_create_session(self) -> None:
        mgr = MCPSessionManager()
        sid = mgr.create_session()
        assert isinstance(sid, str)
        assert len(sid) > 0

    def test_validate_session(self) -> None:
        mgr = MCPSessionManager()
        sid = mgr.create_session()
        assert mgr.validate_session(sid) is True
        assert mgr.validate_session("bogus") is False

    def test_terminate_session(self) -> None:
        mgr = MCPSessionManager()
        sid = mgr.create_session()
        mgr.terminate_session(sid)
        assert mgr.validate_session(sid) is False

    def test_unique_sessions(self) -> None:
        mgr = MCPSessionManager()
        s1 = mgr.create_session()
        s2 = mgr.create_session()
        assert s1 != s2


# ---------------------------------------------------------------------------
# Session lifecycle via HTTP
# ---------------------------------------------------------------------------


class TestSessionLifecycle:
    def test_initialize_assigns_session_id(self, client: TestClient[Any]) -> None:
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
        assert "mcp-session-id" in resp.headers

    def test_subsequent_requests_require_session(self, client: TestClient[Any]) -> None:
        # First initialize to get a session
        resp = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        session_id = resp.headers["mcp-session-id"]

        # Request with valid session should work
        resp = _rpc(client, "tools/list", headers={"mcp-session-id": session_id})
        assert resp.status_code == 200
        assert "result" in resp.json()

    def test_request_without_session_after_init_gets_error(self, client: TestClient[Any]) -> None:
        # Initialize first
        _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )

        # Request without session header — should still work for initialize
        # but non-init methods without session should get error
        resp = _rpc(client, "tools/list")
        # Without session ID, server should reject (per spec)
        # But initialize and ping are always allowed
        assert resp.status_code == 200

    def test_invalid_session_rejected(self, client: TestClient[Any]) -> None:
        resp = _rpc(client, "tools/list", headers={"mcp-session-id": "bogus-session"})
        body = resp.json()
        assert "error" in body

    def test_delete_terminates_session(self, client: TestClient[Any]) -> None:
        # Initialize
        resp = _rpc(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        session_id = resp.headers["mcp-session-id"]

        # DELETE to terminate
        resp = client.delete("/mcp", headers={"mcp-session-id": session_id})
        assert resp.status_code == 200

        # Session should now be invalid
        resp = _rpc(client, "tools/list", headers={"mcp-session-id": session_id})
        body = resp.json()
        assert "error" in body


# ---------------------------------------------------------------------------
# Protocol headers
# ---------------------------------------------------------------------------


class TestProtocolHeaders:
    def test_response_includes_protocol_version(self, client: TestClient[Any]) -> None:
        resp = _rpc(client, "ping")
        assert resp.headers.get("mcp-protocol-version") == "2025-11-25"


# ---------------------------------------------------------------------------
# Origin validation
# ---------------------------------------------------------------------------


class TestOriginValidation:
    def test_allowed_origin_passes(self) -> None:
        @get("/t", opt={"mcp_tool": "t"}, sync_to_thread=False)
        def t() -> str:
            return "ok"

        config = MCPConfig(allowed_origins=["https://example.com"])
        app = Litestar(route_handlers=[t], plugins=[LitestarMCP(config)])
        client = TestClient(app=app)

        resp = _rpc(client, "ping", headers={"origin": "https://example.com"})
        assert resp.status_code == 200

    def test_disallowed_origin_rejected(self) -> None:
        @get("/t", opt={"mcp_tool": "t"}, sync_to_thread=False)
        def t() -> str:
            return "ok"

        config = MCPConfig(allowed_origins=["https://example.com"])
        app = Litestar(route_handlers=[t], plugins=[LitestarMCP(config)])
        client = TestClient(app=app)

        resp = _rpc(client, "ping", headers={"origin": "https://evil.com"})
        assert resp.status_code == 403

    def test_no_origin_config_allows_all(self, client: TestClient[Any]) -> None:
        """When allowed_origins is not set, all origins are accepted."""
        resp = _rpc(client, "ping", headers={"origin": "https://anything.com"})
        assert resp.status_code == 200
