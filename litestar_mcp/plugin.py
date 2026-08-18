"""Litestar MCP Plugin implementation."""

import logging
from typing import TYPE_CHECKING, Any

from litestar import Litestar, Request, Router
from litestar import get as litestar_get
from litestar.di import Provide
from litestar.handlers import BaseRouteHandler
from litestar.plugins import CLIPlugin, InitPluginProtocol

from litestar_mcp.cli import mcp_group
from litestar_mcp.config import MCPConfig
from litestar_mcp.manifests import build_agent_card, build_oauth_protected_resource
from litestar_mcp.registry import PromptRegistration, Registry
from litestar_mcp.routes import MCPController
from litestar_mcp.schema_builder import generate_schema_for_handler, validate_mcp_header_schema
from litestar_mcp.sse import SubscriptionManager
from litestar_mcp.tasks import MCPTaskStore, TaskRecord
from litestar_mcp.utils import get_handler_function, get_mcp_metadata

_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from click import Group
    from litestar.config.app import AppConfig


class LitestarMCP(InitPluginProtocol, CLIPlugin):
    """Litestar plugin for Model Context Protocol integration."""

    def __init__(
        self,
        config: "MCPConfig | None" = None,
        prompts: "Sequence[Callable[..., Any]] | None" = None,
    ) -> "None":
        """Initialize the MCP plugin.

        Args:
            config: Plugin configuration. Defaults to ``MCPConfig()``.
            prompts: Optional sequence of standalone prompt functions
                decorated with ``@mcp_prompt``. These are registered
                immediately and made available via ``prompts/list`` and
                ``prompts/get``.
        """
        self._config = config or MCPConfig()
        self._registry = Registry()
        self._dynamic_handlers: list[BaseRouteHandler] = []
        if prompts:
            for fn in prompts:
                metadata = get_mcp_metadata(fn) or {}
                if metadata.get("type") != "prompt":
                    msg = f"Function {fn!r} is not decorated with @mcp_prompt"
                    raise ValueError(msg)
                self._registry.register_prompt(
                    name=metadata["name"],
                    fn=fn,
                    title=metadata.get("title"),
                    description=metadata.get("description"),
                    arguments=metadata.get("arguments"),
                    icons=metadata.get("icons"),
                )
        self._subscription_manager = SubscriptionManager(
            max_streams=self._config.subscription_max_streams,
            channels=self._config.subscription_channels,
        )
        self._task_store: MCPTaskStore | None = None
        if self._config.task_config is not None:
            task_config = self._config.task_config
            self._task_store = MCPTaskStore(
                store=task_config.store,
                default_ttl_ms=task_config.default_ttl_ms,
                max_ttl_ms=task_config.max_ttl_ms,
                poll_interval_ms=task_config.poll_interval_ms,
            )

    @property
    def config(self) -> "MCPConfig":
        """Get the plugin configuration."""
        return self._config

    @property
    def registry(self) -> "Registry":
        """Get the central registry."""
        return self._registry

    @property
    def task_store(self) -> "MCPTaskStore | None":
        """Get the task store."""
        return self._task_store

    @property
    def discovered_tools(self) -> "dict[str, BaseRouteHandler]":
        """Get discovered MCP tools."""
        return self._registry.tools

    @property
    def discovered_resources(self) -> "dict[str, BaseRouteHandler]":
        """Get discovered MCP resources."""
        return self._registry.resources

    @property
    def discovered_prompts(self) -> "dict[str, PromptRegistration]":
        """Get discovered MCP prompts."""
        return self._registry.prompts

    def register_dynamic_handler(self, handler: "BaseRouteHandler") -> "None":
        """Register a dynamic route handler on the plugin.

        This is typically used by the wrapper class to register decorated
        tools and resources.
        """
        self._dynamic_handlers.append(handler)

    def on_cli_init(self, cli: "Group") -> "None":
        """Configure CLI commands for MCP operations."""
        cli.add_command(mcp_group)

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        """Initialize the MCP integration when the Litestar app starts."""
        app_config.route_handlers.extend(self._dynamic_handlers)
        self._discover_mcp_routes(app_config.route_handlers)
        self._registry.set_subscription_manager(self._subscription_manager)

        if self._task_store is not None:

            async def publish_task_status(record: "TaskRecord") -> "None":
                await self._registry.publish_notification(
                    "notifications/tasks",
                    record.to_dict(),
                )

            self._task_store.set_status_callback(publish_task_status)

        def provide_mcp_config() -> "MCPConfig":
            return self._config

        def provide_registry() -> "Registry":
            return self._registry

        def provide_task_store() -> "MCPTaskStore | None":
            return self._task_store

        router_kwargs: dict[str, Any] = {
            "path": self._config.base_path,
            "route_handlers": [MCPController],
            "tags": ["mcp"],
            "include_in_schema": self._config.include_in_schema,
            "dependencies": {
                "config": Provide(provide_mcp_config, sync_to_thread=False),
                "registry": Provide(provide_registry, sync_to_thread=False),
                "task_store": Provide(provide_task_store, sync_to_thread=False),
                "discovered_tools": Provide(lambda: self._registry.tools, sync_to_thread=False),
                "discovered_resources": Provide(lambda: self._registry.resources, sync_to_thread=False),
                "discovered_prompts": Provide(lambda: self._registry.prompts, sync_to_thread=False),
            },
        }
        if self._config.guards is not None:
            router_kwargs["guards"] = self._config.guards
        if self._config.route_opt is not None:
            router_kwargs["opt"] = dict(self._config.route_opt)

        mcp_router = Router(**router_kwargs)
        app_config.route_handlers.append(mcp_router)
        app_config.on_startup.append(self.on_startup)
        app_config.on_shutdown.append(self.on_shutdown)

        @litestar_get(
            "/.well-known/oauth-protected-resource",
            sync_to_thread=False,
            include_in_schema=self._config.include_in_schema,
            opt={"exclude_from_auth": True},
        )
        def oauth_protected_resource(request: "Request[Any, Any, Any]") -> "dict[str, Any]":
            return build_oauth_protected_resource(self._config.auth, request.app)

        @litestar_get(
            "/.well-known/agent-card.json",
            sync_to_thread=False,
            include_in_schema=self._config.include_in_schema,
            opt={"exclude_from_auth": True},
        )
        def agent_card(request: "Request[Any, Any, Any]") -> "dict[str, Any]":
            return build_agent_card(
                base_url=str(request.base_url),
                config=self._config,
                app=request.app,
                discovered_tools=self._registry.tools,
            )

        app_config.route_handlers.extend([oauth_protected_resource, agent_card])
        return app_config

    def on_startup(self, app: "Litestar") -> "None":
        """Perform discovery after app is fully initialized and routes are built."""
        all_handlers: list[BaseRouteHandler] = []
        for route in app.routes:
            if hasattr(route, "route_handlers"):
                all_handlers.extend(route.route_handlers)  # pyright: ignore[reportAttributeAccessIssue]
        _logger.debug("Plugin on_startup executing...")
        self._subscription_manager.start()
        self._discover_mcp_routes(all_handlers)
        for handler in self._registry.tools.values():
            validate_mcp_header_schema(generate_schema_for_handler(handler))

        def invalidate_router() -> "None":
            _logger.debug("invalidate_router callback triggered")
            if hasattr(app.state, "mcp_router"):
                _logger.debug("Deleting mcp_router from app state")
                delattr(app.state, "mcp_router")

        self._registry.register_change_callback(invalidate_router)
        app.state.mcp_router_invalidation_callback = invalidate_router
        _logger.debug("Registered invalidate_router callback on registry: %s", id(self._registry))

    async def on_shutdown(self, app: "Litestar") -> "None":
        """Clean up resources on application shutdown."""
        _logger.debug("Plugin on_shutdown executing...")
        callback = getattr(app.state, "mcp_router_invalidation_callback", None)
        if callback is not None:
            self._registry.unregister_change_callback(callback)
            delattr(app.state, "mcp_router_invalidation_callback")
            _logger.debug("Unregistered invalidate_router callback from registry")
        await self._subscription_manager.close_all()
        if self._task_store is not None:
            await self._task_store.close()

    def _discover_mcp_routes(self, route_handlers: "Sequence[Any]") -> "None":
        """Discover routes marked for MCP exposure via opt attribute or decorators."""
        for handler in route_handlers:
            if isinstance(handler, BaseRouteHandler):
                metadata = get_mcp_metadata(handler)
                if not metadata:
                    metadata = get_mcp_metadata(get_handler_function(handler))

                if metadata:
                    if metadata["type"] == "tool":
                        self._registry.register_tool(metadata["name"], handler)
                    elif metadata["type"] == "resource":
                        self._registry.register_resource(metadata["name"], handler)
                        template = metadata.get("resource_template")
                        if template is not None:
                            self._registry.register_resource_template(metadata["name"], handler, template)
                    elif metadata["type"] == "prompt":
                        self._registry.register_prompt_handler(
                            metadata["name"],
                            handler,
                            title=metadata.get("title"),
                            description=metadata.get("description"),
                            arguments=metadata.get("arguments"),
                            icons=metadata.get("icons"),
                        )
                elif handler.opt:
                    tool_key = self._config.opt_keys.tool
                    resource_key = self._config.opt_keys.resource
                    template_key = self._config.opt_keys.resource_template
                    prompt_key = self._config.opt_keys.prompt
                    if tool_key in handler.opt:
                        self._registry.register_tool(handler.opt[tool_key], handler)
                    if resource_key in handler.opt:
                        resource_name = handler.opt[resource_key]
                        self._registry.register_resource(resource_name, handler)
                        opt_template = handler.opt.get(template_key)
                        if isinstance(opt_template, str):
                            self._registry.register_resource_template(resource_name, handler, opt_template)
                    if prompt_key in handler.opt:
                        opt_keys = self._config.opt_keys
                        self._registry.register_prompt_handler(
                            handler.opt[prompt_key],
                            handler,
                            title=handler.opt.get(opt_keys.prompt_title),
                            description=handler.opt.get(opt_keys.prompt_description),
                            arguments=handler.opt.get(opt_keys.prompt_arguments),
                            icons=handler.opt.get(opt_keys.prompt_icons),
                        )

            if getattr(handler, "route_handlers", None):
                self._discover_mcp_routes(handler.route_handlers)  # pyright: ignore[reportAttributeAccessIssue]
