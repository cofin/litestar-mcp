"""Litestar MCP Plugin implementation."""

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Optional

from litestar import Litestar, Router
from litestar.config.app import AppConfig
from litestar.di import Provide
from litestar.handlers import BaseRouteHandler
from litestar.plugins import CLIPlugin, InitPluginProtocol

from litestar_mcp.config import MCPConfig
from litestar_mcp.decorators import get_mcp_metadata
from litestar_mcp.registry import Registry
from litestar_mcp.routes import MCPController
from litestar_mcp.session import MCPSessionManager
from litestar_mcp.sse import SSEManager
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from click import Group


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
        self._session_manager = MCPSessionManager()

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

        def provide_session_manager() -> MCPSessionManager:
            return self._session_manager

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
                "session_manager": Provide(provide_session_manager, sync_to_thread=False),
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

        # Register .well-known/oauth-protected-resource endpoint when auth is configured
        if self._config.auth and self._config.auth.issuer:
            from litestar import get as litestar_get

            auth_config = self._config.auth

            @litestar_get("/.well-known/oauth-protected-resource", sync_to_thread=False)
            def oauth_protected_resource() -> dict[str, Any]:
                """RFC 9728 protected resource metadata."""
                return {
                    "resource": auth_config.audience or "",
                    "authorization_servers": [auth_config.issuer],
                    "scopes_supported": list(auth_config.scopes.keys()) if auth_config.scopes else [],
                }

            app_config.route_handlers.append(oauth_protected_resource)

        return app_config

    def on_startup(self, app: Litestar) -> None:
        """Perform discovery after app is fully initialized and routes are built.

        This captures handlers from Controllers and other dynamic sources.
        """
        import logging

        logger = logging.getLogger(__name__)
        logger.debug("Running MCP on_startup discovery")

        all_handlers: list[BaseRouteHandler] = []

        # Traverse routes deeply
        for route in app.routes:
            all_handlers.extend(route.route_handlers)
            # If it's a mount/router, it might have nested routes but app.routes is flattened usually

        logger.debug("Found %d total handlers in flattened app.routes", len(all_handlers))
        self._discover_mcp_routes(all_handlers)
