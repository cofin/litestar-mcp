"""Litestar Channels SSE streaming bridge and QueuedAgentChatController for queued turns."""

from contextlib import suppress
from typing import TYPE_CHECKING, Annotated, Any, TypeVar
from uuid import uuid4

import anyio
import msgspec
from litestar import Controller, Request, Response, get, post
from litestar.exceptions import ImproperlyConfiguredException
from litestar.params import Dependency
from litestar.response import ServerSentEvent
from litestar.response.sse import ServerSentEventMessage
from litestar.serialization import decode_json
from litestar.status_codes import HTTP_202_ACCEPTED

from litestar_mcp.agent.streaming import TERMINAL_EVENTS, AgentStreamFrame
from litestar_mcp.core._streaming import StreamCleanupMiddleware
from litestar_mcp.core.serialization import to_json
from litestar_mcp.queues.tasks import PrincipalSnapshot, QueuedTurnManager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from litestar.types import TypeEncodersMap

T = TypeVar("T")
NamedDependency = Annotated[T, Dependency(skip_validation=True)]


def agent_turn_channel(turn_id: str) -> str:
    """Return the canonical Litestar Channels topic for a queued agent turn."""
    return f"litestar_mcp:agent:{turn_id}"


async def emit_agent_frame(
    channels: Any,
    turn_id: str,
    frame: AgentStreamFrame | dict[str, Any],
    *,
    seq: int | None = None,
    type_encoders: "TypeEncodersMap | None" = None,
) -> None:
    """Serialize and publish an AgentStreamFrame to the turn's Litestar Channel."""
    if channels is None:
        return
    if seq is not None and isinstance(frame, AgentStreamFrame):
        frame = msgspec.structs.replace(frame, seq=seq)
    payload = to_json(frame, as_bytes=False, type_encoders=type_encoders)
    channel_name = agent_turn_channel(turn_id)
    await channels.publish(payload, channels=[channel_name])


class QueuedAgentChatController(Controller):
    """Litestar controller enqueuing agent turns and streaming frames from Channels."""

    path = "/agent"
    replay_limit: int = 1000
    ping_interval: float = 15.0

    @post("/turns", status_code=HTTP_202_ACCEPTED)
    async def enqueue_turn(
        self,
        data: dict[str, Any],
        request: Request[Any, Any, Any],
        turn_manager: NamedDependency[QueuedTurnManager],
    ) -> Response[dict[str, Any]]:
        """Enqueue a background agent turn and return HTTP 202 Accepted with stream URL."""
        session_id = str(data.get("session_id") or uuid4().hex)
        turn_id = str(data.get("turn_id") or uuid4().hex)
        user_message = data.get("user_message") or data.get("message") or ""
        agent_name = str(data.get("agent_name") or "root")
        dynamic_context = data.get("dynamic_context")

        principal: PrincipalSnapshot | None = None
        user = None
        auth = None
        with suppress(Exception):
            user = getattr(request, "user", None)
        with suppress(Exception):
            auth = getattr(request, "auth", None)
        if user is not None or auth is not None:
            user_id = getattr(user, "user_id", None) or getattr(user, "id", None) or getattr(auth, "sub", None)
            tenant = getattr(request.state, "tenant", None) or getattr(auth, "tenant", None)
            roles = getattr(auth, "roles", None) or getattr(user, "roles", [])
            scopes = getattr(auth, "scopes", None) or getattr(user, "scopes", [])
            principal = PrincipalSnapshot(
                user_id=str(user_id) if user_id is not None else None,
                tenant=str(tenant) if tenant is not None else None,
                roles=list(roles) if isinstance(roles, (list, tuple)) else [],
                scopes=list(scopes) if isinstance(scopes, (list, tuple)) else [],
            )

        enqueued_turn_id = await turn_manager.enqueue_turn(
            session_id=session_id,
            user_message=user_message,
            turn_id=turn_id,
            agent_name=agent_name,
            dynamic_context=dynamic_context,
            principal=principal,
        )

        return Response(
            content={
                "session_id": session_id,
                "turn_id": enqueued_turn_id,
                "stream_url": f"{self.path}/turns/{enqueued_turn_id}/stream",
            },
            status_code=HTTP_202_ACCEPTED,
        )

    @get(
        "/turns/{turn_id:str}/stream",
        middleware=[StreamCleanupMiddleware()],
        opt={"skip_stream_cleanup": True},
    )
    async def stream_turn(
        self,
        turn_id: str,
        request: Request[Any, Any, Any],
        channels: NamedDependency[Any | None] = None,
    ) -> ServerSentEvent:
        """Stream real-time SSE frames for a queued agent turn from Litestar Channels."""
        if channels is None:
            msg = "ChannelsPlugin is required to stream queued agent turns"
            raise ImproperlyConfiguredException(msg)

        encoders = request.route_handler.resolve_type_encoders()
        channel_name = agent_turn_channel(turn_id)

        async def _generator() -> "AsyncIterator[ServerSentEventMessage]":
            last_seq = 0
            async with channels.start_subscription([channel_name], history=self.replay_limit) as subscriber:
                event_iter = subscriber.iter_events().__aiter__()
                done = False
                while not done:
                    try:
                        raw = None
                        with anyio.move_on_after(self.ping_interval) as cancel_scope:
                            raw = await event_iter.__anext__()
                        if cancel_scope.cancel_called or raw is None:
                            yield ServerSentEventMessage(event="ping", data="")
                            continue

                        frame = decode_json(raw, target_type=AgentStreamFrame)
                        if frame.seq <= last_seq:
                            continue
                        last_seq = frame.seq
                        yield frame.to_sse_message(type_encoders=encoders)
                        if frame.event_type in TERMINAL_EVENTS:
                            done = True
                    except StopAsyncIteration:
                        done = True

        return ServerSentEvent(_generator())


__all__ = (
    "NamedDependency",
    "QueuedAgentChatController",
    "agent_turn_channel",
    "emit_agent_frame",
)
