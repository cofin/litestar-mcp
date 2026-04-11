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


def _build_protected_resource_from_openapi(app: Litestar) -> dict[str, Any]:
    """Build RFC 9728 protected resource metadata from Litestar's OpenAPI security schemes.

    Reads the app's OpenAPI schema to discover authorization servers (token URLs),
    supported scopes, and the resource identifier. This means apps using
    OAuth2PasswordBearerAuth or similar get automatic MCP auth discovery.

    Args:
        app: The Litestar application instance.

    Returns:
        Protected resource metadata dict, or empty metadata if no security schemes found.
    """
    openapi_config = app.openapi_config
    if not openapi_config:
        return {"resource": "", "authorization_servers": [], "scopes_supported": []}

    schema = app.openapi_schema
    if not schema.components or not schema.components.security_schemes:
        return {"resource": "", "authorization_servers": [], "scopes_supported": []}

    auth_servers: list[str] = []
    all_scopes: list[str] = []

    for scheme in schema.components.security_schemes.values():
        flows = getattr(scheme, "flows", None)
        if not flows:
            continue

        # Check all OAuth2 flow types for token/auth URLs
        for flow_attr in ("password", "authorization_code", "client_credentials", "implicit"):
            flow = getattr(flows, flow_attr, None)
            if flow is None:
                continue
            if hasattr(flow, "token_url") and flow.token_url:
                auth_servers.append(flow.token_url)
            if hasattr(flow, "authorization_url") and flow.authorization_url:
                auth_servers.append(flow.authorization_url)
            if hasattr(flow, "scopes") and flow.scopes:
                all_scopes.extend(flow.scopes.keys() if isinstance(flow.scopes, dict) else flow.scopes)

    resource_name = openapi_config.title or ""

    return {
        "resource": resource_name,
        "authorization_servers": list(dict.fromkeys(auth_servers)),  # dedupe, preserve order
        "scopes_supported": list(dict.fromkeys(all_scopes)),
    }


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
        self._startup_discovery_ran = False

    @property
    def config(self) -> MCPConfig:
        """Get the plugin configuration."""
        return self._config

    @property
    def registry(self) -> Registry:
        """Get the central registry."""
        return self._registry

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

        # Register .well-known/oauth-protected-resource endpoint.
        # Auto-discovers from Litestar's OpenAPI security schemes at request time.
        # Falls back to explicit MCPAuthConfig if provided.
        from litestar import Request
        from litestar import get as litestar_get

        auth_config = self._config.auth

        @litestar_get(
            "/.well-known/oauth-protected-resource",
            sync_to_thread=False,
            opt={"exclude_from_auth": True},
        )
        def oauth_protected_resource(request: Request[Any, Any, Any]) -> dict[str, Any]:
            """RFC 9728 protected resource metadata.

            Auto-discovers authorization server details from the app's OpenAPI
            security schemes when no explicit MCPAuthConfig is provided.
            """
            # Explicit MCPAuthConfig takes precedence
            if auth_config and auth_config.issuer:
                return {
                    "resource": auth_config.audience or "",
                    "authorization_servers": [auth_config.issuer],
                    "scopes_supported": list(auth_config.scopes.keys()) if auth_config.scopes else [],
                }

            # Auto-discover from OpenAPI security schemes
            return _build_protected_resource_from_openapi(request.app)

        app_config.route_handlers.append(oauth_protected_resource)

        return app_config

    def on_startup(self, app: Litestar) -> None:
        """Perform discovery after app is fully initialized and routes are built.

        This captures handlers from Controllers and other dynamic sources.
        Safe to invoke more than once: subsequent calls are no-ops, which
        matters because the CLI entrypoint runs this manually (the CLI
        never triggers the ASGI startup lifespan) and we don't want to
        re-scan the route tree every ``litestar mcp ...`` invocation.
        """
        if self._startup_discovery_ran:
            return

        import logging

        logger = logging.getLogger(__name__)
        logger.debug("Running MCP on_startup discovery")

        all_handlers: list[BaseRouteHandler] = []

        # Traverse routes deeply
        for route in app.routes:
            if hasattr(route, "route_handlers"):
                all_handlers.extend(route.route_handlers)  # pyright: ignore[reportAttributeAccessIssue]
            # If it's a mount/router, it might have nested routes but app.routes is flattened usually

        logger.debug("Found %d total handlers in flattened app.routes", len(all_handlers))
        self._discover_mcp_routes(all_handlers)
        self._startup_discovery_ran = True
