"""Litestar Channels SSE streaming bridge and QueuedAgentChatController for queued turns."""

from collections.abc import Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyio
import msgspec
from litestar import Controller, Request, Response, get, post
from litestar.di import NamedDependency
from litestar.exceptions import ImproperlyConfiguredException
from litestar.params import FromPath, JSONBody
from litestar.response import ServerSentEvent
from litestar.response.sse import ServerSentEventMessage
from litestar.status_codes import HTTP_202_ACCEPTED

from litestar_mcp.agent.streaming import TERMINAL_EVENTS, AgentStreamFrame
from litestar_mcp.core._streaming import StreamCleanupMiddleware
from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.serialization import decode_json, to_json
from litestar_mcp.queues.tasks import PrincipalSnapshot, QueuedTurnManager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from litestar.types import TypeEncodersMap


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
        data: JSONBody[dict[str, Any]],
        request: Request[Any, Any, Any],
        turn_manager: NamedDependency[QueuedTurnManager],
    ) -> Response[dict[str, Any]]:
        """Enqueue a background agent turn and return HTTP 202 Accepted with stream URL."""
        session_id = str(data.get("session_id") or uuid4().hex)
        turn_id = str(data.get("turn_id") or uuid4().hex)
        user_message = data.get("user_message") or data.get("message") or ""
        agent_name = str(data.get("agent_name") or "root")
        dynamic_context = data.get("dynamic_context")

        ctx = ToolContext.from_connection(request)
        auth = None
        with suppress(Exception):
            auth = request.scope.get("auth") if "auth" in request.scope else getattr(request, "auth", None)
        user_id: Any = None
        if isinstance(ctx.user, Mapping):
            user_id = ctx.user.get("user_id") or ctx.user.get("id") or ctx.user.get("sub") or ctx.user.get("username")
        elif ctx.user is not None:
            user_id = (
                getattr(ctx.user, "user_id", None)
                or getattr(ctx.user, "id", None)
                or getattr(ctx.user, "sub", None)
                or getattr(ctx.user, "username", None)
            )
        if user_id is None and auth is not None:
            if isinstance(auth, Mapping):
                user_id = auth.get("sub") or auth.get("user_id") or auth.get("id")
            else:
                user_id = getattr(auth, "sub", None) or getattr(auth, "user_id", None) or getattr(auth, "id", None)

        principal: PrincipalSnapshot | None = None
        if ctx.user is not None or auth is not None or ctx.tenant_id is not None or ctx.roles or ctx.scopes:
            principal = PrincipalSnapshot(
                user_id=str(user_id) if user_id is not None else None,
                tenant=str(ctx.tenant_id) if ctx.tenant_id is not None else None,
                roles=list(ctx.roles),
                scopes=list(ctx.scopes),
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
        turn_id: FromPath[str],
        request: Request[Any, Any, Any],
        channels: NamedDependency[Any | None] = None,
    ) -> ServerSentEvent:
        """Stream real-time SSE frames for a queued agent turn from Litestar Channels."""
        if channels is None:
            msg = "ChannelsPlugin is required to stream queued agent turns"
            raise ImproperlyConfiguredException(msg)

        encoders = request.route_handler.resolve_type_encoders()
        channel_name = agent_turn_channel(turn_id)
        raw_last_event_id = request.headers.get("last-event-id", "").strip()
        try:
            initial_last_seq = max(0, int(raw_last_event_id)) if raw_last_event_id else 0
        except ValueError:
            initial_last_seq = 0

        async def _generator() -> "AsyncIterator[ServerSentEventMessage]":
            last_seq = initial_last_seq
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
