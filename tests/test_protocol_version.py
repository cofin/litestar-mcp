"""Tests for MCP protocol version."""

from typing import Any

from litestar import Litestar
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


def _rpc(client: TestClient[Any], method: str, params: "dict[str, Any] | None" = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body).json()  # type: ignore[no-any-return]


def test_protocol_version() -> None:
    plugin = LitestarMCP()
    app = Litestar(plugins=[plugin])

    with TestClient(app=app) as client:
        result = _rpc(client, "initialize", {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        })
        assert result["result"]["protocolVersion"] == "2025-11-25"
