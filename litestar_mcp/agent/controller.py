"""Litestar controller providing HTTP and SSE streaming endpoints for agents."""

from contextlib import suppress
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyio
import msgspec
from litestar import Controller, get, post
from litestar.connection import Request  # noqa: TC002
from litestar.di import NamedDependency  # noqa: TC002
from litestar.exceptions import HTTPException, NotFoundException
from litestar.response import ServerSentEvent
from litestar.status_codes import HTTP_200_OK

from litestar_mcp.agent.guards import BudgetExceededError
from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.spec import AgentMessage  # noqa: TC001
from litestar_mcp.agent.streaming import AgentStreamFrame
from litestar_mcp.core._streaming import (
    DEFAULT_STREAM_CLEANUP_TIMEOUT,
    StreamCleanupMiddleware,
    prefetch_stream,
    start_stream,
)
from litestar_mcp.core.context import ToolContext

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from anyio.streams.memory import MemoryObjectSendStream
    from litestar.response.sse import ServerSentEventMessage

    from litestar_mcp.core._streaming import StreamOwner

__all__ = ("AgentChatController", "ChatRequest", "SessionHistory", "SessionNotFoundError", "TurnResult")


class SessionNotFoundError(NotFoundException):
    """Exception raised when a requested session is not found."""

    def __init__(self, session_id: str) -> None:
        """Initialize with session_id."""
        super().__init__(detail=f"Session {session_id!r} not found")


class ChatRequest(msgspec.Struct, kw_only=True, forbid_unknown_fields=True):
    """Body payload for agent chat streaming and turn creation."""

    message: "str"
    session_id: "str | None" = None
    dynamic_context: "str | None" = None


class TurnResult(msgspec.Struct, kw_only=True):
    """Aggregated turn result returned by POST /agent/turns."""

    session_id: "str"
    turn_id: "str"
    agent_name: "str"
    output: "str"
    messages: "list[AgentMessage]"
    token_usage: "dict[str, int]"


class SessionHistory(msgspec.Struct, kw_only=True):
    """Conversation history payload returned by GET /agent/sessions/{session_id}."""

    session_id: "str"
    messages: "list[AgentMessage]"


class AgentChatController(Controller):
    """SSE chat, synchronous turns, and owner-scoped history over an injected AgentRuntime."""

    path = "/agent"
    ping_interval: "float" = 15.0

    def _turn(self, request: "Request[Any, Any, Any]", data: "ChatRequest") -> "TurnRequest":
        """Build TurnRequest from connection request and payload data."""
        return TurnRequest(
            user_message=data.message,
            session_id=data.session_id,
            dynamic_context=data.dynamic_context,
            context=ToolContext.from_connection(request),
        )

    @post("/chat", middleware=[StreamCleanupMiddleware()], status_code=HTTP_200_OK)
    async def chat(
        self,
        request: "Request[Any, Any, Any]",
        data: "ChatRequest",
        runtime: "NamedDependency[AgentRuntime]",
    ) -> "ServerSentEvent":
        """Stream an agent turn over Server-Sent Events from a typed chat payload."""
        turn_req = self._turn(request, data)
        encoders = request.route_handler.resolve_type_encoders()

        async def _producer(send: "MemoryObjectSendStream[AgentStreamFrame]") -> None:
            await runtime.produce(turn_req, send)

        owner: StreamOwner[AgentStreamFrame, None] = start_stream(
            request.scope,
            _producer,
            capacity=runtime.stream_buffer,
            cleanup_timeout=DEFAULT_STREAM_CLEANUP_TIMEOUT,
        )
        try:
            first = await prefetch_stream(request.scope, request.receive, owner)
        except anyio.EndOfStream:
            first = None

        turn_id = first.turn_id if first is not None else uuid4().hex

        async def events() -> "AsyncIterator[ServerSentEventMessage]":
            try:
                if first is not None:
                    yield first.to_sse_message(encoders)
                while True:
                    frame: AgentStreamFrame | None = None
                    with anyio.move_on_after(self.ping_interval) as idle:
                        try:
                            frame = await owner.receiver.receive()
                        except anyio.EndOfStream:
                            break
                    if idle.cancelled_caught or frame is None:
                        yield AgentStreamFrame(event_type="ping", turn_id=turn_id).to_sse_message(encoders)
                        continue
                    yield frame.to_sse_message(encoders)
                with suppress(Exception):
                    await owner.result()
            finally:
                await owner.close()

        return ServerSentEvent(events())

    @post("/turns", status_code=HTTP_200_OK)
    async def create_turn(
        self,
        request: "Request[Any, Any, Any]",
        data: "ChatRequest",
        runtime: "NamedDependency[AgentRuntime]",
    ) -> "TurnResult":
        """Execute an agent turn synchronously returning final TurnResult."""
        turn_req = self._turn(request, data)
        try:
            res = await runtime.run_turn(turn_req)
        except BudgetExceededError as exc:
            raise HTTPException(status_code=504, detail=str(exc)) from exc
        return TurnResult(
            session_id=res.session_id,
            turn_id=res.turn_id,
            agent_name=res.agent_name,
            output=res.output,
            messages=res.messages,
            token_usage=res.token_usage,
        )

    @get("/sessions/{session_id:str}")
    async def get_session(
        self,
        request: "Request[Any, Any, Any]",
        session_id: "str",
        runtime: "NamedDependency[AgentRuntime]",
    ) -> "SessionHistory":
        """Return conversation history for a session scoped to caller identity."""
        tool_ctx = ToolContext.from_connection(request)
        messages = await runtime.get_history(tool_ctx, session_id)
        if messages is None:
            raise SessionNotFoundError(session_id)
        return SessionHistory(
            session_id=session_id,
            messages=messages,
        )
