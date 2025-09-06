"""Minimal configuration for Litestar MCP Plugin."""

from dataclasses import dataclass
from typing import Optional


@dataclass
class MCPConfig:
    """Minimal configuration for the Litestar MCP Plugin.

    The plugin uses Litestar's opt attribute to discover routes marked for MCP exposure.
    Server name and version are derived from the Litestar app's OpenAPI configuration.

    Attributes:
        base_path: Base path for MCP API endpoints.
        include_in_schema: Whether to include MCP routes in OpenAPI schema generation.
        name: Optional override for server name. If not set, uses OpenAPI title.
    """

    base_path: str = "/mcp"
    include_in_schema: bool = False
    name: Optional[str] = None
