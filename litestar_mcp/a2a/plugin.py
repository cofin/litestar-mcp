"""Thin Litestar adapter around the official A2A SDK HTTP routes."""

from inspect import isawaitable
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from a2a.auth.user import User
from a2a.extensions.common import HTTP_EXTENSION_HEADER, get_requested_extensions
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import RequestHandler
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes.common import ServerCallContextBuilder
from a2a.server.routes.jsonrpc_routes import create_jsonrpc_routes
from a2a.types import AgentCard
from litestar import Litestar, asgi, get
from litestar.plugins import InitPluginProtocol
from starlette.applications import Starlette

from litestar_mcp.a2a.config import A2AConfig

if TYPE_CHECKING:
    from litestar.config.app import AppConfig
    from litestar.types import Receive, Scope, Send


class _LitestarUser(User):
    def __init__(self, value: Any) -> None:
        self.value = value

    @property
    def is_authenticated(self) -> bool:
        return self.value is not None

    @property
    def user_name(self) -> str:
        for name in ("id", "sub", "username", "display_name"):
            value = self.value.get(name) if isinstance(self.value, dict) else getattr(self.value, name, None)
            if value is not None:
                return str(value)
        return str(self.value)


class LitestarCallContextBuilder(ServerCallContextBuilder):
    """Map the outer Litestar ASGI scope into an official call context."""

    def build(self, request: Any) -> ServerCallContext:
        scope = request.scope
        headers = dict(request.headers)
        state = {
            "auth": scope.get("auth"),
            "headers": headers,
            "litestar_state": scope.get("state", {}),
        }
        tenant = scope.get("tenant") or state["litestar_state"].get("tenant", "")
        return ServerCallContext(
            state=state,
            user=_LitestarUser(scope.get("user")),
            tenant=str(tenant),
            requested_extensions=get_requested_extensions(request.headers.getlist(HTTP_EXTENSION_HEADER)),
        )


def _validate_card(card: AgentCard, path: str) -> None:
    for interface in card.supported_interfaces:
        if interface.protocol_version == "1.0" and interface.protocol_binding == "JSONRPC":
            url = urlparse(interface.url)
            if url.scheme and url.netloc and url.path.rstrip("/") == path.rstrip("/"):
                return
    msg = "AgentCard must advertise an absolute JSONRPC supported interface for protocol 1.0 matching the A2A path"
    raise ValueError(msg)


class LitestarA2A(InitPluginProtocol):
    """Mount an official A2A request handler in a Litestar application."""

    def __init__(self, agent_card: AgentCard, request_handler: RequestHandler, config: A2AConfig | None = None) -> None:
        self.agent_card = agent_card
        self.request_handler = request_handler
        self.config = config or A2AConfig()
        _validate_card(agent_card, self.config.path)

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        occupied = {path for handler in app_config.route_handlers for path in getattr(handler, "paths", ())}
        collisions = occupied.intersection((self.config.path, self.config.agent_card_path))
        if collisions:
            msg = f"A2A route collision: {', '.join(sorted(collisions))}"
            raise ValueError(msg)

        sdk_app = Starlette(
            routes=create_jsonrpc_routes(
                self.request_handler,
                "/",
                context_builder=self.config.context_builder or LitestarCallContextBuilder(),
                enable_v0_3_compat=self.config.enable_v0_3_compat,
            )
        )
        sdk_asgi: Any = sdk_app

        @asgi(
            self.config.path,
            is_mount=True,
            copy_scope=True,
            guards=self.config.guards,
            opt=self.config.route_opt,
        )
        async def a2a_endpoint(scope: "Scope", receive: "Receive", send: "Send") -> None:
            await sdk_asgi(scope, receive, send)

        @get(self.config.agent_card_path, include_in_schema=False, opt={"exclude_from_auth": True})
        async def agent_card() -> dict[str, Any]:
            return agent_card_to_dict(self.agent_card)

        app_config.route_handlers.extend((a2a_endpoint, agent_card))
        app_config.on_shutdown.append(self.on_shutdown)
        return app_config

    async def on_shutdown(self, app: Litestar) -> None:
        close = getattr(self.request_handler, "aclose", None)
        if close is not None:
            result = close()
            if isawaitable(result):
                await result
