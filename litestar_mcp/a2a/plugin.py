"""Litestar A2A Plugin implementation conforming to A2A v1.0."""

import logging
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from litestar import MediaType, Request, Response, Router, get
from litestar.di import Provide
from litestar.handlers import BaseRouteHandler
from litestar.plugins import InitPluginProtocol

from litestar_mcp.a2a.config import A2AConfig
from litestar_mcp.a2a.manifest import build_agent_card
from litestar_mcp.a2a.registry import A2ARegistry, SkillRegistration
from litestar_mcp.a2a.routes import A2AController
from litestar_mcp.a2a.service import A2AHandlerService
from litestar_mcp.a2a.streaming import A2ASubscriptionManager
from litestar_mcp.a2a.tasks import A2AMemoryTaskStore, A2ATaskStore
from litestar_mcp.core.signature import get_handler_function

if TYPE_CHECKING:
    from litestar.config.app import AppConfig

_logger = logging.getLogger(__name__)


class A2APlugin(InitPluginProtocol):
    """Litestar plugin for Agent-to-Agent (A2A) protocol integration."""

    def __init__(
        self,
        config: A2AConfig | None = None,
        skills: Sequence[Callable[..., Any]] | None = None,
        task_store: A2ATaskStore | None = None,
        subscription_manager: A2ASubscriptionManager | None = None,
    ) -> None:
        """Initialize the A2A plugin.

        Args:
            config: Optional plugin configuration. Defaults to ``A2AConfig()``.
            skills: Optional sequence of standalone callables.
            task_store: Optional task persistence store. Defaults to ``A2AMemoryTaskStore()``.
            subscription_manager: Optional SSE streaming manager. Defaults to ``A2ASubscriptionManager()``.
        """
        self._config = config or A2AConfig()
        self._registry = A2ARegistry()
        self._task_store = task_store or A2AMemoryTaskStore()
        self._subscription_manager = subscription_manager or A2ASubscriptionManager()
        self._service: A2AHandlerService | None = None

        if skills:
            for fn in skills:
                name = getattr(fn, "__name__", str(fn))
                self._registry.register(
                    SkillRegistration(
                        fn=fn,
                        id=name,
                        name=name,
                    )
                )

    @property
    def config(self) -> A2AConfig:
        """Get plugin configuration."""
        return self._config

    @property
    def registry(self) -> A2ARegistry:
        """Get the central skill registry."""
        return self._registry

    @property
    def task_store(self) -> A2ATaskStore:
        """Get the active task store."""
        return self._task_store

    @property
    def subscription_manager(self) -> A2ASubscriptionManager:
        """Get the subscription manager."""
        return self._subscription_manager

    @property
    def discovered_skills(self) -> dict[str, SkillRegistration]:
        """Dictionary of discovered skills by id."""
        return {s.id: s for s in self._registry.skills}

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        """Initialize A2A routes, dependencies, and discovery on application init."""
        self._discover_skills(app_config.route_handlers)

        self._service = A2AHandlerService(
            app=None,
            registry=self._registry,
            task_store=self._task_store,
            subscription_manager=self._subscription_manager,
        )

        def provide_config() -> A2AConfig:
            return self._config

        def provide_registry() -> A2ARegistry:
            return self._registry

        def provide_task_store() -> A2ATaskStore:
            return self._task_store

        def provide_service() -> A2AHandlerService:
            if self._service is None:
                msg = "A2AHandlerService has not been initialized"
                raise RuntimeError(msg)
            return self._service

        router_kwargs: dict[str, Any] = {
            "path": self._config.base_path,
            "route_handlers": [A2AController],
            "tags": ["a2a"],
            "include_in_schema": self._config.include_in_schema,
            "dependencies": {
                "config": Provide(provide_config, sync_to_thread=False),
                "registry": Provide(provide_registry, sync_to_thread=False),
                "task_store": Provide(provide_task_store, sync_to_thread=False),
                "service": Provide(provide_service, sync_to_thread=False),
            },
        }
        if self._config.guards is not None:
            router_kwargs["guards"] = self._config.guards
        if self._config.route_opt is not None:
            router_kwargs["opt"] = dict(self._config.route_opt)

        app_config.route_handlers.append(Router(**router_kwargs))

        if self._config.register_agent_card:
            existing_cards = [
                h
                for h in app_config.route_handlers
                if isinstance(h, BaseRouteHandler) and self._config.agent_card_path in getattr(h, "paths", set())
            ]
            for h in existing_cards:
                app_config.route_handlers.remove(h)

            @get(
                self._config.agent_card_path,
                sync_to_thread=False,
                include_in_schema=self._config.include_in_schema,
                opt={"exclude_from_auth": True},
            )
            def get_agent_card(request: Request[Any, Any, Any]) -> Response[Any]:
                card = build_agent_card(
                    app=request.app,
                    registry=self._registry,
                    name=self._config.name,
                    description=self._config.description,
                    version=self._config.version,
                    base_url=str(request.base_url),
                    auto_export_mcp_tools=self._config.auto_export_mcp_tools,
                    capabilities=self._config.capabilities,
                    provider=self._config.provider,
                    security_schemes=self._config.security_schemes,
                    security=self._config.security,
                    documentation_url=self._config.documentation_url,
                )
                return Response(content=card, media_type=MediaType.JSON)

            app_config.route_handlers.append(get_agent_card)

        return app_config

    def _discover_skills(self, route_handlers: Sequence[Any]) -> None:
        """Recursively discover route handlers marked with opt['a2a_skill']."""
        for handler in route_handlers:
            if isinstance(handler, BaseRouteHandler) and handler.opt:
                skill_key = self._config.opt_keys.skill
                if skill_key in handler.opt:
                    skill_id = handler.opt[skill_key]
                    desc = handler.opt.get(self._config.opt_keys.description)
                    tags = handler.opt.get(self._config.opt_keys.tags, [])
                    fn = get_handler_function(handler)
                    self._registry.register(
                        SkillRegistration(
                            fn=fn,
                            id=skill_id,
                            name=skill_id,
                            description=desc,
                            tags=tags if isinstance(tags, list) else [str(tags)],
                        )
                    )
            if getattr(handler, "route_handlers", None):
                self._discover_skills(handler.route_handlers)
