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
from litestar_mcp.decorators import get_mcp_metadata
from litestar_mcp.registry import Registry
from litestar_mcp.routes import MCPController
from litestar_mcp.sse import SSEManager
from litestar_mcp.utils import get_handler_function


class LitestarMCP(InitPluginProtocol, CLIPlugin):
    """Litestar plugin for Model Context Protocol integration.

    This plugin discovers routes marked for MCP exposure and exposes them through 
    MCP-compatible REST API endpoints.
    """

    def __init__(self, config: Optional[MCPConfig] = None) -> None:
        """Initialize the MCP plugin.

        Args:
            config: Plugin configuration. If not provided, uses default configuration.
        """
        self._config = config or MCPConfig()
        self._registry = Registry()
        self._sse_manager = SSEManager()

    @property
    def config(self) -> MCPConfig:
        """Get the plugin configuration."""
        return self._config

    @property
    def registry(self) -> Registry:
        """Get the central registry."""
        return self._registry

    @property
    def sse_manager(self) -> SSEManager:
        """Get the SSE manager."""
        return self._sse_manager

    @property
    def discovered_tools(self) -> dict[str, BaseRouteHandler]:
        """Get discovered MCP tools."""
        return self._registry.tools

    @property
    def discovered_resources(self) -> dict[str, BaseRouteHandler]:
        """Get discovered MCP resources."""
        return self._registry.resources

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
        import logging
        logger = logging.getLogger(__name__)

        for handler in route_handlers:
            if isinstance(handler, BaseRouteHandler):
                # Check for decorator-based metadata first (takes precedence)
                metadata = get_mcp_metadata(handler)

                # If not on handler, check the underlying function
                if not metadata:
                    fn = get_handler_function(handler)
                    metadata = get_mcp_metadata(fn)

                if metadata:
                    logger.debug("Found MCP metadata for %s: %s", handler, metadata)
                    if metadata["type"] == "tool":
                        self._registry.register_tool(metadata["name"], handler)
                    elif metadata["type"] == "resource":
                        self._registry.register_resource(metadata["name"], handler)

                # Fallback to opt dictionary for backward compatibility
                elif handler.opt:
                    if "mcp_tool" in handler.opt:
                        tool_name = handler.opt["mcp_tool"]
                        self._registry.register_tool(tool_name, handler)

                    if "mcp_resource" in handler.opt:
                        resource_name = handler.opt["mcp_resource"]
                        self._registry.register_resource(resource_name, handler)
            else:
                logger.debug("Not a BaseRouteHandler: %s (%s)", handler, type(handler))

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
        self._registry.set_sse_manager(self._sse_manager)

        def provide_mcp_config() -> MCPConfig:
            return self._config

        def provide_registry() -> Registry:
            return self._registry

        def provide_sse_manager() -> SSEManager:
            return self._sse_manager

        # Build router kwargs with conditional guards
        router_kwargs: dict[str, Any] = {
            "path": self._config.base_path,
            "route_handlers": [MCPController],
            "tags": ["mcp"],
            "include_in_schema": self._config.include_in_schema,
            "dependencies": {
                "config": Provide(provide_mcp_config, sync_to_thread=False),
                "registry": Provide(provide_registry, sync_to_thread=False),
                "sse_manager": Provide(provide_sse_manager, sync_to_thread=False),
                # Compatibility for existing controller
                "discovered_tools": Provide(lambda: self._registry.tools, sync_to_thread=False),
                "discovered_resources": Provide(lambda: self._registry.resources, sync_to_thread=False),
            },
        }

        # Only add guards if they are provided
        if self._config.guards is not None:
            router_kwargs["guards"] = self._config.guards

        mcp_router = Router(**router_kwargs)

        app_config.route_handlers.append(mcp_router)
        app_config.on_startup.append(self.on_startup)

        return app_config

    def on_startup(self, app: "Litestar") -> None:
        """Perform discovery after app is fully initialized and routes are built.
        
        This captures handlers from Controllers and other dynamic sources.
        """
        import logging
        logger = logging.getLogger(__name__)
        logger.debug("Running MCP on_startup discovery")

        all_handlers: list[BaseRouteHandler] = []
        for route in app.routes:
            all_handlers.extend(route.route_handlers)

        logger.debug("Found %d total handlers in app.routes", len(all_handlers))
        self._discover_mcp_routes(all_handlers)