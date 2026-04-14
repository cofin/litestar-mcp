"""Tests for MCP protocol version (2025-11-25)."""

from typing import Any

from litestar import Litestar
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


def _rpc_resp(client: TestClient[Any], method: str, params: "dict[str, Any] | None" = None) -> Any:
    """Return raw response (not just json) for header inspection."""
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body)


def test_protocol_version_in_initialize() -> None:
    app = Litestar(plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _rpc_resp(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        assert resp.json()["result"]["protocolVersion"] == "2025-11-25"


def test_protocol_version_header_on_responses() -> None:
    app = Litestar(plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _rpc_resp(client, "ping")
        assert resp.headers.get("mcp-protocol-version") == "2025-11-25"


def test_capabilities_tools_list_changed() -> None:
    app = Litestar(plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _rpc_resp(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        caps = resp.json()["result"]["capabilities"]
        assert caps["tools"]["listChanged"] is True


def test_capabilities_resources() -> None:
    app = Litestar(plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _rpc_resp(
            client,
            "initialize",
            {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1.0"},
            },
        )
        caps = resp.json()["result"]["capabilities"]
        assert caps["resources"]["subscribe"] is True
        assert caps["resources"]["listChanged"] is True


def test_all_legacy_rest_endpoints_removed() -> None:
    """Confirm clean break — no REST endpoints exist."""
    app = Litestar(plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        for path in ["/mcp/tools", "/mcp/resources", "/mcp/sse", "/mcp/messages"]:
            resp = client.get(path)
            assert resp.status_code in (404, 405), f"{path} should be gone"
