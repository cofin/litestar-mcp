"""Tests for MCP protocol version."""

from litestar import Litestar
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP


def test_protocol_version() -> None:
    plugin = LitestarMCP()
    app = Litestar(plugins=[plugin])

    with TestClient(app=app) as client:
        response = client.get("/mcp")
        assert response.status_code == 200
        assert response.json()["protocol_version"] == "2024-11-05"
