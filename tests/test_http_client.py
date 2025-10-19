"""Tests for HTTP client lifecycle management."""

import httpx
import pytest

from litestar_mcp.http_client import MCPHttpClient


class TestMCPHttpClient:
    """Tests for MCPHttpClient lifecycle."""

    @pytest.mark.asyncio
    async def test_client_creation(self) -> None:
        """Test that client can be created."""
        client = MCPHttpClient(timeout=10.0)
        assert client is not None

    @pytest.mark.asyncio
    async def test_client_context_manager(self) -> None:
        """Test client as async context manager."""
        client = MCPHttpClient(timeout=10.0)

        async with client as http:
            assert isinstance(http, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_client_headers_forwarded(self) -> None:
        """Test that configured headers are set on client."""
        headers = {"Authorization": "Bearer token", "X-Custom": "value"}
        client = MCPHttpClient(headers=headers)

        async with client as http:
            assert "Authorization" in http.headers
            assert http.headers["Authorization"] == "Bearer token"
            assert http.headers["X-Custom"] == "value"

    @pytest.mark.asyncio
    async def test_client_timeout_configuration(self) -> None:
        """Test that timeout is configured correctly."""
        client = MCPHttpClient(timeout=15.0)

        async with client as http:
            assert http.timeout.read == 15.0

    @pytest.mark.asyncio
    async def test_client_connection_limits(self) -> None:
        """Test that client can be configured with connection limits."""
        client = MCPHttpClient(max_connections=30, max_keepalive=15)

        async with client as http:
            assert isinstance(http, httpx.AsyncClient)

    @pytest.mark.asyncio
    async def test_client_shutdown(self) -> None:
        """Test explicit shutdown."""
        client = MCPHttpClient()

        async with client as http:
            assert http is not None

        await client.shutdown()

    @pytest.mark.asyncio
    async def test_client_reusable_across_contexts(self) -> None:
        """Test that client can be reused across multiple contexts."""
        client = MCPHttpClient()

        async with client as http1:
            client1_id = id(http1)

        async with client as http2:
            client2_id = id(http2)

        assert client1_id != client2_id

    @pytest.mark.asyncio
    async def test_client_default_values(self) -> None:
        """Test that client uses default timeout values."""
        client = MCPHttpClient()

        async with client as http:
            assert http.timeout.read == 30.0

    @pytest.mark.asyncio
    async def test_client_empty_headers(self) -> None:
        """Test client with no headers configured."""
        client = MCPHttpClient(headers=None)

        async with client as http:
            assert http is not None

    @pytest.mark.asyncio
    async def test_shutdown_idempotent(self) -> None:
        """Test that shutdown can be called multiple times safely."""
        client = MCPHttpClient()

        async with client as _:
            pass

        await client.shutdown()
        await client.shutdown()
