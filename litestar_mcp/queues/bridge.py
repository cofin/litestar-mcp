"""Litestar Channels SSE streaming bridge and QueuedAgentChatController for queued turns."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from litestar import Controller, Response, get, post
from litestar.response import ServerSentEvent
from litestar.status_codes import HTTP_202_ACCEPTED

from litestar_mcp.agent.streaming import AgentStreamFrame
from litestar_mcp.core.serialization import from_json, to_json
from litestar_mcp.queues.tasks import QueuedTurnManager

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from litestar.connection import Request
    from litestar.response.sse import ServerSentEventMessage
    from litestar.types import TypeEncodersMap


def agent_turn_channel(turn_id: str) -> str:
    """Return the canonical Litestar Channels topic for a queued agent turn."""
    return f"litestar_mcp:agent:{turn_id}"


async def emit_agent_frame(
    channels: Any,
    turn_id: str,
    frame: AgentStreamFrame | dict[str, Any],
    *,
    type_encoders: TypeEncodersMap | None = None,
) -> None:
    """Serialize and publish an AgentStreamFrame to the turn's Litestar Channel."""
    if channels is None:
        return
    frame_dict = frame.to_dict() if isinstance(frame, AgentStreamFrame) else dict(frame)
    if isinstance(frame, AgentStreamFrame) and frame.seq:
        frame_dict["seq"] = frame.seq
    payload = to_json(frame_dict, as_bytes=False, type_encoders=type_encoders)
    channel_name = agent_turn_channel(turn_id)
    await channels.publish(payload, channels=[channel_name])


def _parse_channel_frame(raw: bytes | str | dict[str, Any], fallback_seq: int) -> AgentStreamFrame:
    """Decode a raw channel message into an AgentStreamFrame."""
    if isinstance(raw, dict):
        parsed = raw
    else:
        decoded = from_json(raw)
        parsed = decoded if isinstance(decoded, dict) else {"value": decoded}

    seq = int(parsed.get("seq") or fallback_seq)
    event_type = str(parsed.get("event_type") or parsed.get("event") or "message")
    return AgentStreamFrame(
        turn_id=str(parsed.get("turn_id", "")),
        event_type=event_type,
        delta=parsed.get("delta"),
        thought=parsed.get("thought"),
        tool_name=parsed.get("tool_name"),
        call_id=parsed.get("call_id"),
        agent_name=parsed.get("agent_name"),
        payload=parsed.get("payload"),
        data=parsed,
        seq=seq,
    )


class QueuedAgentChatController(Controller):
    """Litestar controller enqueuing agent turns and streaming frames from Channels."""

    path = "/agent"
    turn_manager: QueuedTurnManager | None = None
    frame_history: dict[str, list[AgentStreamFrame]] | None = None

    def __init__(
        self,
        owner: Any = None,
        turn_manager: QueuedTurnManager | None = None,
    ) -> None:
        """Initialize the queued agent chat controller."""
        super().__init__(owner if owner is not None else cast("Any", None))
        if not hasattr(self, "path"):
            self.path = "/agent"
        if turn_manager is not None:
            self.turn_manager = turn_manager

    def _resolve_manager(
        self,
        turn_manager: QueuedTurnManager | None = None,
        queue_service: Any = None,
        channels: Any = None,
    ) -> QueuedTurnManager:
        """Resolve QueuedTurnManager from dependencies or controller attributes."""
        if turn_manager is not None:
            return turn_manager
        if self.turn_manager is not None:
            return self.turn_manager
        return QueuedTurnManager(queue_service=queue_service, channels=channels)

    @post("/turns", status_code=HTTP_202_ACCEPTED)
    async def enqueue_turn(
        self,
        data: dict[str, Any],
        turn_manager: QueuedTurnManager | None = None,
        queue_service: Any = None,
        channels: Any = None,
    ) -> Response[dict[str, Any]]:
        """Enqueue a background agent turn and return HTTP 202 Accepted with stream URL."""
        manager = self._resolve_manager(turn_manager, queue_service, channels)
        session_id = str(data.get("session_id") or uuid4().hex)
        turn_id = str(data.get("turn_id") or uuid4().hex)
        user_message = str(data.get("user_message") or data.get("message") or "")
        agent_name = str(data.get("agent_name") or "root")

        resolved_tid = await manager.enqueue_turn(
            session_id=session_id,
            user_message=user_message,
            turn_id=turn_id,
            agent_name=agent_name,
        )
        return Response(
            content={
                "session_id": session_id,
                "turn_id": resolved_tid,
                "status": "accepted",
                "stream_url": f"{self.path}/turns/{resolved_tid}/stream",
            },
            status_code=HTTP_202_ACCEPTED,
        )

    @get("/turns/{turn_id:str}/stream")
    async def stream_queued_turn(
        self,
        request: Request[Any, Any, Any],
        turn_id: str,
        turn_manager: QueuedTurnManager | None = None,
        channels: Any = None,
    ) -> ServerSentEvent:
        """Stream queued turn events with Last-Event-ID replay support."""
        manager = self._resolve_manager(turn_manager, channels=channels)
        last_event_id_header = request.headers.get("last-event-id")
        last_seq = int(last_event_id_header) if last_event_id_header and last_event_id_header.isdigit() else 0
        type_encoders = getattr(request.route_handler, "type_encoders", None)
        history_map = self.frame_history if self.frame_history is not None else manager.frame_history

        async def _generator() -> AsyncIterator[ServerSentEventMessage]:
            seq = last_seq
            buffered = history_map.get(turn_id, [])
            for frame in buffered:
                if frame.seq > last_seq:
                    seq = max(seq, frame.seq)
                    yield frame.to_sse_message(type_encoders=type_encoders)
                    if frame.event_type in {"complete", "error"}:
                        return

            active_channels = channels or manager.channels
            if active_channels is not None and hasattr(active_channels, "start_subscription"):
                channel_name = agent_turn_channel(turn_id)
                async with active_channels.start_subscription([channel_name]) as subscriber:
                    async for raw_msg in subscriber.iter_events():
                        seq += 1
                        frame = _parse_channel_frame(raw_msg, seq)
                        if frame.seq <= last_seq:
                            continue
                        yield frame.to_sse_message(type_encoders=type_encoders)
                        if frame.event_type in {"complete", "error"}:
                            return

        return ServerSentEvent(content=_generator())


__all__ = (
    "QueuedAgentChatController",
    "agent_turn_channel",
    "emit_agent_frame",
)
