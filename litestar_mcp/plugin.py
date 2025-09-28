"""Litestar MCP Plugin implementation."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from click import Group

from litestar import Router
from litestar.config.app import AppConfig
from litestar.di import Provide
from litestar.handlers import BaseRouteHandler
from litestar.plugins import CLIPlugin, InitPluginProtocol

from litestar_mcp.config import MCPConfig
from litestar_mcp.routes import MCPController
from litestar_mcp.utils import get_handler_function, get_mcp_metadata


class LitestarMCP(InitPluginProtocol, CLIPlugin):
    """Litestar plugin for Model Context Protocol integration.

    This plugin discovers routes marked with 'mcp_tool' or 'mcp_resource' in their
    opt dictionary and exposes them through MCP-compatible REST API endpoints.

    Example:
        .. code-block:: python

            from litestar import Litestar, get, post
            from litestar.openapi.config import OpenAPIConfig
            from litestar_mcp import LitestarMCP

            @get("/users", mcp_tool="list_users")
            async def get_users() -> list[dict]:
                return [{"id": 1, "name": "Alice"}]

            @post("/analyze", mcp_tool="analyze_data")
            async def analyze(data: dict) -> dict:
                return {"result": "analyzed"}

            @get("/config", mcp_resource="app_config")
            async def get_config() -> dict:
                return {"debug": True}

            app = Litestar(
                plugins=[LitestarMCP()],
                route_handlers=[get_users, analyze, get_config],
                openapi_config=OpenAPIConfig(title="My API", version="1.0.0")
            )
    """

    def __init__(self, config: Optional[MCPConfig] = None) -> None:
        """Initialize the MCP plugin.

        Args:
            config: Plugin configuration. If not provided, uses default configuration.

        Note:
            Server name and version are automatically derived from the
            Litestar application's OpenAPI configuration unless overridden in config.
        """
        self._config = config or MCPConfig()
        self._discovered_tools: dict[str, BaseRouteHandler] = {}
        self._discovered_resources: dict[str, BaseRouteHandler] = {}

    @property
    def config(self) -> MCPConfig:
        """Get the plugin configuration."""
        return self._config

    @property
    def discovered_tools(self) -> dict[str, BaseRouteHandler]:
        """Get discovered MCP tools."""
        return self._discovered_tools

    @property
    def discovered_resources(self) -> dict[str, BaseRouteHandler]:
        """Get discovered MCP resources."""
        return self._discovered_resources

    def on_cli_init(self, cli: "Group") -> None:
        """Configure CLI commands for MCP operations.

        Args:
            cli: The Click command group to add commands to.
        """
        from litestar_mcp.cli import mcp_group

        cli.add_command(mcp_group)

    def _discover_mcp_routes(self, route_handlers: Sequence[Any]) -> None:
        """Discover routes marked for MCP exposure via opt attribute or decorators.

        Recursively traverses route handlers to find those marked with 'mcp_tool'
        or 'mcp_resource' in their opt dictionary or via @mcp_tool/@mcp_resource decorators.
        """
        for handler in route_handlers:
            if isinstance(handler, BaseRouteHandler):
                # Check for decorator-based metadata first (takes precedence)
                # Metadata can be on the handler object itself or the underlying function
                metadata = get_mcp_metadata(handler)

                # If not on handler, check the underlying function
                if not metadata:
                    fn = get_handler_function(handler)
                    metadata = get_mcp_metadata(fn)

                if metadata:
                    if metadata["type"] == "tool":
                        self._discovered_tools[metadata["name"]] = handler
                    elif metadata["type"] == "resource":
                        self._discovered_resources[metadata["name"]] = handler

                # Fallback to opt dictionary for backward compatibility
                elif handler.opt:
                    if "mcp_tool" in handler.opt:
                        tool_name = handler.opt["mcp_tool"]
                        self._discovered_tools[tool_name] = handler

                    if "mcp_resource" in handler.opt:
                        resource_name = handler.opt["mcp_resource"]
                        self._discovered_resources[resource_name] = handler

            # Check if this handler has nested route handlers (like routers)
            if getattr(handler, "route_handlers", None):
                self._discover_mcp_routes(handler.route_handlers)  # pyright: ignore

    def on_app_init(self, app_config: AppConfig) -> AppConfig:
        """Initialize the MCP integration when the Litestar app starts.

        This method discovers routes marked for MCP exposure and adds
        MCP-compatible REST API endpoints to expose them.

        Args:
            app_config: The Litestar application configuration

        Returns:
            The modified application configuration
        """
        self._discover_mcp_routes(app_config.route_handlers)

        def provide_mcp_config() -> MCPConfig:
            return self._config

        def provide_discovered_tools() -> dict[str, BaseRouteHandler]:
            return self._discovered_tools

        def provide_discovered_resources() -> dict[str, BaseRouteHandler]:
            return self._discovered_resources

        # Build router kwargs with conditional guards
        router_kwargs: dict[str, Any] = {
            "path": self._config.base_path,
            "route_handlers": [MCPController],
            "tags": ["mcp"],
            "include_in_schema": self._config.include_in_schema,
            "dependencies": {
                "config": Provide(provide_mcp_config, sync_to_thread=False),
                "discovered_tools": Provide(provide_discovered_tools, sync_to_thread=False),
                "discovered_resources": Provide(provide_discovered_resources, sync_to_thread=False),
            },
        }

        # Only add guards if they are provided
        if self._config.guards is not None:
            router_kwargs["guards"] = self._config.guards

        mcp_router = Router(**router_kwargs)

        app_config.route_handlers.append(mcp_router)

        return app_config
