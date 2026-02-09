"""Tests for MCP SSE endpoints."""

from collections.abc import AsyncGenerator
from unittest.mock import MagicMock

from litestar import Litestar
from litestar.status_codes import HTTP_200_OK, HTTP_202_ACCEPTED
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.sse import SSEMessage


def test_sse_handshake() -> None:
    plugin = LitestarMCP()

    # Mock subscribe to return an empty list/generator so it finishes
    async def mock_subscribe(client_id: str) -> AsyncGenerator[SSEMessage, None]:
        if False:
            yield SSEMessage(data="")  # make it an async generator

    plugin.sse_manager.subscribe = MagicMock(side_effect=mock_subscribe)

    app = Litestar(plugins=[plugin])

    with TestClient(app=app) as client:
        # GET /mcp/sse returns a stream.
        response = client.get("/mcp/sse")
        assert response.status_code == HTTP_200_OK
        assert "text/event-stream" in response.headers["content-type"]

        # TestClient.get() should aggregate the stream if it's finite
        assert "event: endpoint" in response.text
        assert "data: http://testserver.local/mcp/messages" in response.text


def test_sse_messages_endpoint() -> None:
    plugin = LitestarMCP()
    app = Litestar(plugins=[plugin])

    with TestClient(app=app) as client:
        # POST /mcp/messages should accept protocol messages
        payload = {"jsonrpc": "2.0", "method": "initialize", "id": 1, "params": {}}
        response = client.post("/mcp/messages", json=payload)
        assert response.status_code == HTTP_202_ACCEPTED
