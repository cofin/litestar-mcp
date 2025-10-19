"""HTTP client lifecycle management for MCP tool execution.

Provides a managed httpx.AsyncClient with header forwarding, timeout configuration,
and connection pooling for downstream tool calls.
"""

from typing import Optional

import httpx

__all__ = ("MCPHttpClient",)


class MCPHttpClient:
    """Managed HTTP client for MCP tool execution.

    Handles header forwarding, timeouts, and connection pooling with proper
    lifecycle management via async context manager protocol.

    Example:
        >>> config = MCPConfig(
        ...     headers={"Authorization": "Bearer token"},
        ...     http_timeout=30.0,
        ...     http_max_connections=20
        ... )
        >>> client = MCPHttpClient(
        ...     headers=config.headers,
        ...     timeout=config.http_timeout,
        ...     max_connections=config.http_max_connections
        ... )
        >>> async with client as http:
        ...     response = await http.get("https://api.example.com/data")
    """

    def __init__(
        self,
        headers: Optional["dict[str, str]"] = None,
        timeout: float = 30.0,
        max_connections: int = 20,
        max_keepalive: int = 10,
    ) -> None:
        """Initialize the HTTP client manager.

        Args:
            headers: Optional headers to include in all requests
            timeout: Request timeout in seconds (default: 30.0)
            max_connections: Maximum concurrent connections (default: 20)
            max_keepalive: Maximum keep-alive connections (default: 10)
        """
        self._headers = headers or {}
        self._timeout = httpx.Timeout(timeout)
        self._pool_limits = httpx.Limits(
            max_keepalive_connections=max_keepalive,
            max_connections=max_connections,
        )
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self) -> httpx.AsyncClient:
        """Create client on first use.

        Returns:
            Configured httpx.AsyncClient instance
        """
        if self._client is None:
            self._client = httpx.AsyncClient(
                headers=self._headers,
                timeout=self._timeout,
                limits=self._pool_limits,
            )
        return self._client

    async def __aexit__(self, *args: object) -> None:
        """Close client on context exit.

        Args:
            args: Exception information (unused)
        """
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def shutdown(self) -> None:
        """Explicit shutdown for app lifecycle.

        Should be called during application shutdown to ensure
        all connections are properly closed.
        """
        if self._client is not None:
            await self._client.aclose()
            self._client = None
