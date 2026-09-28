"""Configuration for the optional A2A adapter."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from litestar_mcp.core._streaming import DEFAULT_STREAM_CLEANUP_TIMEOUT, validate_stream_cleanup_timeout

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from a2a.server.context import ServerCallContext
    from litestar.connection import Request
    from litestar.types import Guard


@dataclass(frozen=True)
class A2AConfig:
    """Configure the Litestar routes wrapping the official A2A SDK.

    The context builder receives the request and prepared SDK context, including
    the requested tenant and protocol metadata. It may asynchronously authorize
    that tenant and return an authorized context; request values never overwrite
    its result.

    Handlers report extension activation through the context state's
    ``a2a_activated_extensions`` set of requested, advertised URI strings. Set it
    before returning a result or yielding the first stream event: response
    headers cannot reflect later activation.

    ``stream_cleanup_timeout`` bounds the wait for cooperative producer cleanup
    after a response ends. It must be positive and finite; expiry is logged as
    incomplete cleanup. Application finalizers must themselves tolerate cancellation.

    Attributes:
        path: Mount path of the A2A JSON-RPC route. Must start with ``/``; a
            trailing slash is removed.
        agent_card_path: Path that serves the agent card. Must start with
            ``/``; a trailing slash is removed.
        guards: Litestar guards applied to the JSON-RPC route. The agent card
            route is public.
        route_opt: Extra ``opt`` entries merged into the JSON-RPC route, for
            example an authentication policy.
        context_builder: Optional ``(request, context)`` callback that
            authorizes the prepared SDK call context and returns it, directly
            or as an awaitable.
        include_in_schema: Include both routes in the OpenAPI schema.
        agent_card_max_age: ``Cache-Control`` ``max-age`` for the agent card,
            in seconds. Must not be negative.
        stream_cleanup_timeout: Seconds to wait for producer cleanup after a
            response ends.
    """

    path: str = "/a2a"
    agent_card_path: str = "/.well-known/agent-card.json"
    guards: "Sequence[Guard]" = ()
    route_opt: dict[str, Any] = field(default_factory=dict)
    context_builder: "Callable[[Request[Any, Any, Any], ServerCallContext], ServerCallContext | Awaitable[ServerCallContext]] | None" = None
    include_in_schema: bool = False
    agent_card_max_age: int = 300
    stream_cleanup_timeout: float = DEFAULT_STREAM_CLEANUP_TIMEOUT

    def __post_init__(self) -> None:
        validate_stream_cleanup_timeout(self.stream_cleanup_timeout)
        for name, value in (("path", self.path), ("agent_card_path", self.agent_card_path)):
            if not value.startswith("/"):
                msg = f"{name} must start with '/'"
                raise ValueError(msg)
        object.__setattr__(self, "path", self.path.rstrip("/") or "/")
        object.__setattr__(self, "agent_card_path", self.agent_card_path.rstrip("/") or "/")
        if self.agent_card_max_age < 0:
            msg = f"agent_card_max_age must not be negative, got {self.agent_card_max_age}"
            raise ValueError(msg)
