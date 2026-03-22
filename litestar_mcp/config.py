"""Configuration for Litestar MCP Plugin."""

from dataclasses import dataclass, field
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
        guards: Optional list of guards to protect MCP endpoints.
        allowed_origins: List of allowed Origin header values. If empty/None, all origins
            are accepted. When set, requests with a non-matching Origin are rejected with 403.
    """

    base_path: str = "/mcp"
    include_in_schema: bool = False
    name: Optional[str] = None
    guards: Optional[list[Any]] = None
    allowed_origins: Optional[list[str]] = None
