"""Typed SSE streaming frames and httpx2 EventSource consumer for agent execution."""

from typing import TYPE_CHECKING, Any, Literal

import httpx2
import msgspec
from litestar.response.sse import ServerSentEventMessage
from litestar.serialization import decode_json

from litestar_mcp.core.serialization import to_json

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from litestar.types import TypeEncodersMap

__all__ = ("TERMINAL_EVENTS", "AgentStreamFrame", "StreamEventType", "iter_agent_stream")

StreamEventType = Literal[
    "session",
    "thought",
    "delta",
    "tool_call",
    "tool_result",
    "agent_transfer",
    "ping",
    "complete",
    "error",
]
TERMINAL_EVENTS: frozenset[str] = frozenset({"complete", "error"})


class AgentStreamFrame(msgspec.Struct, kw_only=True, omit_defaults=True):
    """Typed SSE frame emitted during agent execution."""

    event_type: StreamEventType
    turn_id: str
    seq: int = 0
    agent_name: str | None = None
    text: str | None = None
    tool_name: str | None = None
    call_id: str | None = None
    payload: dict[str, Any] | None = None

    def to_sse_message(self, type_encoders: "TypeEncodersMap | None" = None) -> ServerSentEventMessage:
        """Encode as an SSE message whose id is seq when non-zero and whose event is event_type."""
        return ServerSentEventMessage(
            id=str(self.seq) if self.seq else None,
            event=self.event_type,
            data=to_json(self, type_encoders=type_encoders),
        )

    @classmethod
    def from_sse(cls, event: httpx2.ServerSentEvent) -> "AgentStreamFrame":
        """Decode an SSE event, taking seq from the event id when the payload omits it."""
        frame = decode_json(event.data, target_type=cls)
        if not frame.seq and event.id and event.id.isdigit():
            return msgspec.structs.replace(frame, seq=int(event.id))
        return frame


async def iter_agent_stream(
    response: httpx2.Response,
    *,
    max_event_size: int | None = 1048576,
) -> "AsyncIterator[AgentStreamFrame]":
    """Consume a streaming httpx2.Response using httpx2.EventSource and yield AgentStreamFrame objects."""
    event_source = httpx2.EventSource(response, max_event_size=max_event_size)
    async for sse in event_source:
        if not sse.data:
            continue
        yield AgentStreamFrame.from_sse(sse)
