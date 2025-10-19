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
from litestar_mcp.filters import should_include_handler
from litestar_mcp.http_client import MCPHttpClient
from litestar_mcp.registry import MCPToolRegistry
from litestar_mcp.routes import MCPController
from litestar_mcp.utils import get_handler_function


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
        self._registry = MCPToolRegistry()
        self._http_client = MCPHttpClient(
            headers=self._config.headers,
            timeout=self._config.http_timeout,
            max_connections=self._config.http_max_connections,
            max_keepalive=self._config.http_max_keepalive,
        )

    @property
    def config(self) -> MCPConfig:
        """Get the plugin configuration."""
        return self._config

    @property
    def registry(self) -> MCPToolRegistry:
        """Get the MCP registry."""
        return self._registry

    @property
    def discovered_tools(self) -> dict[str, BaseRouteHandler]:
        """Get discovered MCP tools (backward compat)."""
        return self._registry.list_tools()

    @property
    def discovered_resources(self) -> dict[str, BaseRouteHandler]:
        """Get discovered MCP resources (backward compat)."""
        return self._registry.list_resources()

    def on_cli_init(self, cli: "Group") -> None:
        """Configure CLI commands for MCP operations.

        Args:
            cli: The Click command group to add commands to.
        """
        from litestar_mcp.cli import mcp_group

        cli.add_command(mcp_group)

    def setup_server(self, route_handlers: Sequence[Any]) -> "tuple[set[str], set[str]]":
        """Re-register MCP routes (new API for dynamic updates).

        Clears the registry and re-scans all route handlers, applying current
        filtering configuration.

        Args:
            route_handlers: Litestar route handlers to scan and register.

        Returns:
            Tuple of (added_names, removed_names)
        """
        old_tools = set(self._registry.list_tools().keys())
        old_resources = set(self._registry.list_resources().keys())
        old_names = old_tools | old_resources

        self._registry._registry.clear()  # noqa: SLF001

        self._discover_mcp_routes(route_handlers)

        new_tools = set(self._registry.list_tools().keys())
        new_resources = set(self._registry.list_resources().keys())
        new_names = new_tools | new_resources

        added = new_names - old_names
        removed = old_names - new_names

        return (added, removed)

    def _discover_mcp_routes(self, route_handlers: Sequence[Any]) -> None:
        """Discover routes marked for MCP exposure and register with registry.

        Recursively traverses route handlers to find those marked with 'mcp_tool'
        or 'mcp_resource' via decorators or opt dictionary. Applies filtering
        based on configuration.
        """
        for handler in route_handlers:
            if isinstance(handler, BaseRouteHandler):
                if not should_include_handler(handler, self._config):
                    continue

                # Check handler first, then function for _mcp_pending
                pending = getattr(handler, "_mcp_pending", None)
                if not pending:
                    fn = get_handler_function(handler)
                    pending = getattr(fn, "_mcp_pending", None)

                if pending:
                    self._registry.register(
                        handler,
                        pending["type"],
                        pending["name"],
                        pending.get("description"),
                    )
                elif handler.opt:
                    if "mcp_tool" in handler.opt:
                        self._registry.register(handler, "tool", handler.opt["mcp_tool"])
                    if "mcp_resource" in handler.opt:
                        self._registry.register(handler, "resource", handler.opt["mcp_resource"])

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

        async def shutdown_http_client() -> None:
            """Shutdown hook to close HTTP client connections."""
            await self._http_client.shutdown()

        def provide_mcp_config() -> MCPConfig:
            return self._config

        def provide_discovered_tools() -> dict[str, BaseRouteHandler]:
            return self._registry.list_tools()

        def provide_discovered_resources() -> dict[str, BaseRouteHandler]:
            return self._registry.list_resources()

        def provide_http_client() -> MCPHttpClient:
            return self._http_client

        router_kwargs: dict[str, Any] = {
            "path": self._config.base_path,
            "route_handlers": [MCPController],
            "tags": ["mcp"],
            "include_in_schema": self._config.include_in_schema,
            "dependencies": {
                "config": Provide(provide_mcp_config, sync_to_thread=False),
                "discovered_tools": Provide(provide_discovered_tools, sync_to_thread=False),
                "discovered_resources": Provide(provide_discovered_resources, sync_to_thread=False),
                "http_client": Provide(provide_http_client, sync_to_thread=False),
            },
        }

        if self._config.guards is not None:
            router_kwargs["guards"] = self._config.guards

        mcp_router = Router(**router_kwargs)

        app_config.route_handlers.append(mcp_router)

        app_config.on_shutdown = list(app_config.on_shutdown or [])
        app_config.on_shutdown.append(shutdown_http_client)

        return app_config
