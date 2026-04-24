"""Configuration for Litestar MCP Plugin."""

from dataclasses import dataclass, field
from typing import Any, Literal

from litestar.stores.base import Store

from litestar_mcp.auth import MCPAuthConfig  # noqa: TC001


@dataclass(frozen=True)
class MCPOptKeys:
    """Configurable names for the ``handler.opt`` keys read by the plugin.

    Downstream apps can rename any key to avoid collisions with other plugins
    or app-specific conventions. All fields default to ``mcp_<purpose>`` and
    the pattern mirrors ``litestar.security.jwt.auth.JWTAuth.exclude_opt_key``.

    Attributes:
        tool: Opt key that marks a route handler as an MCP tool
            (``handler.opt[tool] = "<tool-name>"``).
        resource: Opt key that marks a route handler as an MCP resource.
        resource_template: Opt key that carries an RFC 6570 Level 1 URI
            template for the resource (``handler.opt[resource_template] =
            "app://workspaces/{workspace_id}/files/{file_id}"``).
        prompt: Opt key that marks a route handler as an MCP prompt
            (``handler.opt[prompt] = "<prompt-name>"``).
        description: Opt key overriding the tool description
            (``handler.opt[description] = "LLM prose"``).
        resource_description: Opt key overriding the resource description.
            Kept distinct from ``description`` so a handler that exposes both
            a tool and a resource on the same route can target each.
        prompt_description: Opt key overriding the prompt description.
        agent_instructions: Opt key for the ``## Instructions`` section.
        when_to_use: Opt key for the ``## When to use`` section.
        returns: Opt key for the ``## Returns`` section.
    """

    tool: str = "mcp_tool"
    resource: str = "mcp_resource"
    resource_template: str = "mcp_resource_template"
    prompt: str = "mcp_prompt"
    description: str = "mcp_description"
    resource_description: str = "mcp_resource_description"
    prompt_description: str = "mcp_prompt_description"
    agent_instructions: str = "mcp_agent_instructions"
    when_to_use: str = "mcp_when_to_use"
    returns: str = "mcp_returns"

    def for_field(self, field_name: str, kind: Literal["tool", "resource", "prompt"]) -> str:
        """Return the opt key for ``(field_name, kind)``.

        The ``description`` field has kind-specific keys (``description`` for
        tools, ``resource_description`` for resources, ``prompt_description``
        for prompts) so a handler exposing multiple MCP roles on the same
        route can carry distinct override prose. All other fields are
        kind-agnostic.
        """
        if field_name == "description" and kind == "resource":
            return self.resource_description
        if field_name == "description" and kind == "prompt":
            return self.prompt_description
        value: str = getattr(self, field_name)
        return value


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
    tasks: "bool | MCPTaskConfig" = False
    opt_keys: MCPOptKeys = field(default_factory=MCPOptKeys)
    session_store: Store | None = None
    session_max_idle_seconds: float = 3600.0
    sse_max_streams: int = 10_000
    sse_max_idle_seconds: float = 3600.0
    _session_manager: Any = field(default=None, repr=False, compare=False)

    @property
    def task_config(self) -> "MCPTaskConfig | None":
        """Return the normalized task configuration, if task support is enabled."""
        return normalize_task_config(self.tasks)
