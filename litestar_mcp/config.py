"""Configuration for Litestar MCP Plugin."""

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class MCPConfig:
    """Configuration for the Litestar MCP Plugin.

    The plugin uses Litestar's opt attribute to discover routes marked for MCP exposure.
    Server name and version are derived from the Litestar app's OpenAPI configuration.

    Attributes:
        base_path: Base path for MCP API endpoints.
        include_in_schema: Whether to include MCP routes in OpenAPI schema generation.
        name: Optional override for server name. If not set, uses OpenAPI title.
        guards: Optional list of guards to protect MCP endpoints. Guards run after
            authentication to provide authorization control.

        include_operations: Optional list of operation_ids to include.
            If None, all operations are candidates (before exclusions).
            If empty list, no operations included.
        exclude_operations: Optional list of operation_ids to exclude.
            Takes precedence over include_operations.
        include_tags: Optional list of tags to include.
            Endpoint must have at least one matching tag.
            Applied before operation filters.
        exclude_tags: Optional list of tags to exclude.
            Endpoint with any matching tag is excluded.
            Applied before operation filters, after include_tags.

        describe_all_responses: Include all response schemas in tool descriptions.
        describe_full_response_schema: Include full response schema (not just 200).

        headers: Optional dict of headers to forward to downstream tool calls.

        sse_heartbeat_interval: SSE heartbeat interval in seconds.
        sse_connection_timeout: Maximum SSE connection duration in seconds.
        sse_batch_size: Maximum events per batch for backpressure handling.
        sse_flush_interval: Force flush interval in seconds for backpressure.

        http_timeout: HTTP client timeout in seconds.
        http_max_connections: Maximum concurrent HTTP connections.
        http_max_keepalive: Maximum keep-alive HTTP connections.
    """

    base_path: str = "/mcp"
    include_in_schema: bool = False
    name: Optional[str] = None
    guards: Optional["list[Any]"] = None
    include_operations: Optional["list[str]"] = None
    exclude_operations: Optional["list[str]"] = None
    include_tags: Optional["list[str]"] = None
    exclude_tags: Optional["list[str]"] = None
    describe_all_responses: bool = False
    describe_full_response_schema: bool = False
    headers: Optional["dict[str, str]"] = None
    sse_heartbeat_interval: int = 30
    sse_connection_timeout: int = 300
    sse_batch_size: int = 10
    sse_flush_interval: float = 1.0
    http_timeout: float = 30.0
    http_max_connections: int = 20
    http_max_keepalive: int = 10
