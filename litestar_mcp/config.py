"""Configuration for Litestar MCP Plugin."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from litestar.stores.base import Store  # noqa: TC002

from litestar_mcp.auth import MCPAuthConfig  # noqa: TC001

if TYPE_CHECKING:
    from litestar_mcp.sessions import MCPSessionManager


@dataclass
class MCPTaskConfig:
    """Configuration for experimental MCP task support."""

    enabled: bool = True
    list_enabled: bool = True
    cancel_enabled: bool = True
    default_ttl: int = 300_000
    max_ttl: int = 3_600_000
    poll_interval: int = 1_000


def normalize_task_config(value: "bool | MCPTaskConfig") -> "MCPTaskConfig | None":
    """Normalize task configuration into a concrete config object."""
    if value is False:
        return None
    if value is True:
        return MCPTaskConfig()
    return value


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
        auth: Optional OAuth 2.1 auth configuration. When set, bearer token validation
            is enforced on MCP endpoints.
        dependency_provider: Optional context-managed hook for request-scoped dependency
            injection during tool execution.
        tasks: Optional task configuration or ``True`` to enable the default
            experimental in-memory task implementation.
    """

    base_path: str = "/mcp"
    include_in_schema: bool = False
    name: str | None = None
    guards: list[Any] | None = None
    allowed_origins: list[str] | None = None
    include_operations: list[str] | None = None
    exclude_operations: list[str] | None = None
    include_tags: list[str] | None = None
    exclude_tags: list[str] | None = None
    auth: "MCPAuthConfig | None" = None
    dependency_provider: Any | None = None
    tasks: "bool | MCPTaskConfig" = False
    session_store: Store | None = None
    session_max_idle_seconds: float = 3600.0
    sse_max_streams: int = 10_000
    sse_max_idle_seconds: float = 3600.0
    _session_manager: Any = field(default=None, repr=False, compare=False)

    @property
    def task_config(self) -> "MCPTaskConfig | None":
        """Return the normalized task configuration, if task support is enabled."""
        return normalize_task_config(self.tasks)
