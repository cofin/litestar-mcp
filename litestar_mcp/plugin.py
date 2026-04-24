"""Litestar MCP Plugin implementation."""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from litestar import Litestar, Router
from litestar.config.app import AppConfig
from litestar.di import Provide
from litestar.handlers import BaseRouteHandler
from litestar.plugins import CLIPlugin, InitPluginProtocol
from litestar.stores.memory import MemoryStore

from litestar_mcp.config import MCPConfig
from litestar_mcp.manifests import build_agent_card, build_mcp_server_manifest, build_oauth_protected_resource
from litestar_mcp.registry import PromptRegistration, Registry
from litestar_mcp.routes import MCPController
from litestar_mcp.sessions import MCPSessionManager
from litestar_mcp.sse import SSEManager
from litestar_mcp.tasks import InMemoryTaskStore, TaskRecord
from litestar_mcp.utils import get_handler_function, get_mcp_metadata

if TYPE_CHECKING:
    from click import Group


class LitestarMCP(InitPluginProtocol, CLIPlugin):
    """Litestar plugin for Model Context Protocol integration."""

    def __init__(
        self,
        config: MCPConfig | None = None,
        prompts: Sequence[Callable[..., Any]] | None = None,
    ) -> None:
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
        self._sse_manager = SSEManager(
            max_streams=self._config.sse_max_streams,
            max_idle_seconds=self._config.sse_max_idle_seconds,
        )
        session_store = self._config.session_store or MemoryStore()
        self._session_manager = MCPSessionManager(
            session_store,
            max_idle_seconds=self._config.session_max_idle_seconds,
        )
        self._config._session_manager = self._session_manager  # noqa: SLF001
        self._task_store: InMemoryTaskStore | None = None
        if self._config.task_config is not None:
            task_config = self._config.task_config
            self._task_store = InMemoryTaskStore(
                default_ttl=task_config.default_ttl,
                max_ttl=task_config.max_ttl,
                poll_interval=task_config.poll_interval,
            )

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

    @property
    def discovered_prompts(self) -> dict[str, PromptRegistration]:
        """Get discovered MCP prompts."""
        return self._registry.prompts

    def on_cli_init(self, cli: "Group") -> None:
        """Configure CLI commands for MCP operations."""
        from litestar_mcp.cli import mcp_group

        cli.add_command(mcp_group)

    def _discover_mcp_routes(self, route_handlers: Sequence[Any]) -> None:
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
                        desc_key = self._config.opt_keys.prompt_description
                        self._registry.register_prompt_handler(
                            handler.opt[prompt_key],
                            handler,
                            description=handler.opt.get(desc_key),
                        )

            if getattr(handler, "route_handlers", None):
                self._discover_mcp_routes(handler.route_handlers)  # pyright: ignore[reportAttributeAccessIssue]

    def on_app_init(self, app_config: AppConfig) -> AppConfig:
        """Initialize the MCP integration when the Litestar app starts."""
        self._discover_mcp_routes(app_config.route_handlers)
        self._registry.set_sse_manager(self._sse_manager)

        if self._task_store is not None:

            async def publish_task_status(record: TaskRecord) -> None:
                await self._registry.publish_notification(
                    "notifications/tasks/status",
                    record.to_dict(),
                )

            self._task_store.set_status_callback(publish_task_status)

        def provide_mcp_config() -> MCPConfig:
            return self._config

        def provide_registry() -> Registry:
            return self._registry

        def provide_task_store() -> InMemoryTaskStore | None:
            return self._task_store

        def provide_session_manager() -> MCPSessionManager:
            return self._session_manager

        router_kwargs: dict[str, Any] = {
            "path": self._config.base_path,
            "route_handlers": [MCPController],
            "tags": ["mcp"],
            "include_in_schema": self._config.include_in_schema,
            "dependencies": {
                "config": Provide(provide_mcp_config, sync_to_thread=False),
                "registry": Provide(provide_registry, sync_to_thread=False),
                "task_store": Provide(provide_task_store, sync_to_thread=False),
                "session_manager": Provide(provide_session_manager, sync_to_thread=False),
                "discovered_tools": Provide(lambda: self._registry.tools, sync_to_thread=False),
                "discovered_resources": Provide(lambda: self._registry.resources, sync_to_thread=False),
                "discovered_prompts": Provide(lambda: self._registry.prompts, sync_to_thread=False),
            },
        }
        if self._config.guards is not None:
            router_kwargs["guards"] = self._config.guards

        mcp_router = Router(**router_kwargs)
        app_config.route_handlers.append(mcp_router)
        app_config.on_startup.append(self.on_startup)

        from litestar import Request
        from litestar import get as litestar_get

        @litestar_get("/.well-known/oauth-protected-resource", sync_to_thread=False, opt={"exclude_from_auth": True})
        def oauth_protected_resource(request: Request[Any, Any, Any]) -> dict[str, Any]:
            return build_oauth_protected_resource(self._config.auth, request.app)

        @litestar_get("/.well-known/agent-card.json", sync_to_thread=False, opt={"exclude_from_auth": True})
        def agent_card(request: Request[Any, Any, Any]) -> dict[str, Any]:
            return build_agent_card(
                base_url=str(request.base_url),
                config=self._config,
                app=request.app,
                discovered_tools=self._registry.tools,
            )

        @litestar_get("/.well-known/mcp-server.json", sync_to_thread=False, opt={"exclude_from_auth": True})
        def mcp_server_manifest(request: Request[Any, Any, Any]) -> dict[str, Any]:
            return build_mcp_server_manifest(
                base_url=str(request.base_url),
                config=self._config,
                app=request.app,
                discovered_tools=self._registry.tools,
                discovered_resources=self._registry.resources,
                discovered_prompts=self._registry.prompts,
            )

        app_config.route_handlers.extend([oauth_protected_resource, agent_card, mcp_server_manifest])
        return app_config

    def on_startup(self, app: Litestar) -> None:
        """Perform discovery after app is fully initialized and routes are built."""
        all_handlers: list[BaseRouteHandler] = []
        for route in app.routes:
            if hasattr(route, "route_handlers"):
                all_handlers.extend(route.route_handlers)  # pyright: ignore[reportAttributeAccessIssue]
        self._discover_mcp_routes(all_handlers)
