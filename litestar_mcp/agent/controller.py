"""Litestar controller providing HTTP and SSE streaming endpoints for agents."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from litestar import Controller, get, post
from litestar.response import ServerSentEvent

from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.security import resolve_tool_context
from litestar_mcp.agent.streaming import AgentStreamFrame

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from litestar.connection import Request
    from litestar.response.sse import ServerSentEventMessage

_RUNTIME_REQUIRED_ERROR = "AgentRuntime must be provided via dependency injection or controller attribute."


def _build_sse_stream(
    resolved_runtime: AgentRuntime,
    turn_req: TurnRequest,
    type_encoders: Any = None,
    *,
    emit_session_frame: bool = True,
) -> AsyncIterator[ServerSentEventMessage]:
    """Build an async generator yielding ServerSentEventMessage items for a turn."""

    async def _generator() -> AsyncIterator[ServerSentEventMessage]:
        seq = 0
        if emit_session_frame:
            seq += 1
            session_frame = AgentStreamFrame.session(
                turn_req.session_id,
                turn_id=turn_req.turn_id,
                seq=seq,
            )
            yield session_frame.to_sse_message(type_encoders=type_encoders)

        async for frame in resolved_runtime.stream_turn(turn_req):
            seq += 1
            frame.seq = seq
            yield frame.to_sse_message(type_encoders=type_encoders)

    return _generator()


class AgentChatController(Controller):
    """Litestar controller providing HTTP endpoints and SSE streaming for agents."""

    path = "/agent"
    runtime: AgentRuntime | None = None
    ping_interval: int = 15

    def __init__(self, owner: Any = None, runtime: AgentRuntime | None = None) -> None:
        """Initialize the agent chat controller."""
        super().__init__(owner if owner is not None else cast("Any", None))
        if not hasattr(self, "path"):
            self.path = "/agent"
        if runtime is not None:
            self.runtime = runtime

    def _resolve_runtime(self, runtime: AgentRuntime | None) -> AgentRuntime:
        """Resolve runtime from route dependency or controller attribute."""
        resolved = runtime or self.runtime
        if resolved is None:
            raise ValueError(_RUNTIME_REQUIRED_ERROR)
        return resolved

    @post("/chat")
    async def chat_stream(
        self,
        request: Request[Any, Any, Any],
        data: dict[str, Any],
        runtime: AgentRuntime | None = None,
    ) -> ServerSentEvent:
        """Stream an agent turn over Server-Sent Events from a JSON chat payload."""
        resolved_runtime = self._resolve_runtime(runtime)
        session_id = str(data.get("session_id") or uuid4().hex)
        turn_id = str(data.get("turn_id") or uuid4().hex)
        user_message = str(data.get("user_message") or data.get("message") or "")
        dynamic_context = data.get("dynamic_context")

        tool_ctx = resolve_tool_context(request)
        tool_ctx.session_id = session_id
        tool_ctx.turn_id = turn_id

        turn_req = TurnRequest(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            dynamic_context=dynamic_context,
            context=tool_ctx,
        )
        type_encoders = getattr(request.route_handler, "type_encoders", None)
        return ServerSentEvent(
            content=_build_sse_stream(resolved_runtime, turn_req, type_encoders=type_encoders),
        )

    @post("/turns")
    async def create_turn(
        self,
        request: Request[Any, Any, Any],
        data: dict[str, Any],
        runtime: AgentRuntime | None = None,
    ) -> dict[str, Any]:
        """Execute an agent turn synchronously returning final aggregated response."""
        resolved_runtime = self._resolve_runtime(runtime)
        session_id = str(data.get("session_id") or uuid4().hex)
        turn_id = str(data.get("turn_id") or uuid4().hex)
        user_message = str(data.get("user_message") or data.get("message") or "")
        dynamic_context = data.get("dynamic_context")

        tool_ctx = resolve_tool_context(request)
        tool_ctx.session_id = session_id
        tool_ctx.turn_id = turn_id

        turn_req = TurnRequest(
            session_id=session_id,
            turn_id=turn_id,
            user_message=user_message,
            dynamic_context=dynamic_context,
            context=tool_ctx,
        )

        response = await resolved_runtime.run_turn(turn_req)
        return {
            "session_id": response.session_id,
            "turn_id": response.turn_id,
            "output": response.output,
            "messages": [m.to_dict() for m in response.messages],
            "token_usage": response.token_usage,
        }

    @get("/turns/{turn_id:str}/stream")
    async def stream_turn(
        self,
        request: Request[Any, Any, Any],
        turn_id: str,
        runtime: AgentRuntime | None = None,
        session_id: str | None = None,
        message: str = "",
    ) -> ServerSentEvent:
        """Stream an agent turn as Server-Sent Events."""
        resolved_runtime = self._resolve_runtime(runtime)
        sid = session_id or uuid4().hex
        tool_ctx = resolve_tool_context(request)
        tool_ctx.session_id = sid
        tool_ctx.turn_id = turn_id

        turn_req = TurnRequest(
            session_id=sid,
            turn_id=turn_id,
            user_message=message,
            context=tool_ctx,
        )
        type_encoders = getattr(request.route_handler, "type_encoders", None)
        return ServerSentEvent(
            content=_build_sse_stream(
                resolved_runtime,
                turn_req,
                type_encoders=type_encoders,
                emit_session_frame=False,
            ),
        )

    @get("/sessions/{session_id:str}")
    async def get_session(
        self,
        session_id: str,
        runtime: AgentRuntime | None = None,
    ) -> dict[str, Any]:
        """Return conversation history for a session."""
        resolved_runtime = self._resolve_runtime(runtime)
        messages = resolved_runtime.get_session_messages(session_id)
        return {
            "session_id": session_id,
            "messages": [m.to_dict() for m in messages],
        }


__all__ = ("AgentChatController",)
