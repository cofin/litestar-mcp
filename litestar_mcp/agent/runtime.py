"""Guarded agent runtime managing multi-turn tool calling, delegation, and telemetry."""

import inspect
import logging
import math
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import anyio
import msgspec

from litestar_mcp.agent.guards import BudgetExceededError, TurnBudget
from litestar_mcp.agent.models import GoogleGenAIClient, ModelClient, ModelDelta
from litestar_mcp.agent.sessions import ANONYMOUS_OWNER, MemorySessionStore, SessionStore
from litestar_mcp.agent.spec import Agent, AgentGroup, AgentMessage
from litestar_mcp.agent.streaming import AgentStreamFrame
from litestar_mcp.core.observability import SpanManager
from litestar_mcp.core.tools import Tool, ToolCall, ToolResult, execute_tool_calls

if TYPE_CHECKING:
    from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream

    from litestar_mcp.core.context import ToolContext

CompactionHook = Callable[[list[AgentMessage]], Awaitable[list[AgentMessage]] | list[AgentMessage]]

__all__ = ("AgentRuntime", "CompactionHook", "TurnRequest", "TurnResponse")

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TurnRequest:
    """Input parameters for one agent turn execution."""

    user_message: "str" = ""
    session_id: "str | None" = None
    turn_id: "str | None" = None
    dynamic_context: "str | None" = None
    context: "ToolContext | None" = None


@dataclass(slots=True)
class TurnResponse:
    """Aggregated result of a completed agent turn."""

    session_id: "str"
    turn_id: "str"
    output: "str"
    agent_name: "str"
    messages: "list[AgentMessage]" = field(default_factory=list)
    token_usage: "dict[str, int]" = field(default_factory=dict)

    @property
    def content(self) -> "str":
        """Alias for output text produced by the turn."""
        return self.output


async def _next_before(
    iterator: "AsyncIterator[ModelDelta]",
    deadline: "float",
) -> "ModelDelta | None":
    """Await one model delta under the turn deadline without spanning a yield."""
    with anyio.fail_after(max(deadline - anyio.current_time(), 0.0)):
        return await anext(iterator, None)


def _process_delta(
    delta: "ModelDelta",
    active_agent: "Agent",
    turn_id: "str",
    turn_text: "list[str]",
    tool_calls: "list[dict[str, Any]]",
    token_usage: "dict[str, int]",
) -> "AgentStreamFrame | None":
    """Process an incremental model delta and update turn state."""
    if delta.event_type == "delta" and delta.text:
        turn_text.append(delta.text)
        return AgentStreamFrame(
            turn_id=turn_id,
            event_type="delta",
            text=delta.text,
            agent_name=active_agent.name,
        )
    if delta.event_type == "thought" and delta.text:
        return AgentStreamFrame(
            turn_id=turn_id,
            event_type="thought",
            text=delta.text,
            agent_name=active_agent.name,
        )
    if delta.event_type == "tool_call" and delta.tool_name:
        call_info = {
            "name": delta.tool_name,
            "call_id": delta.call_id or "",
            "arguments": delta.arguments or {},
            "provider_metadata": delta.provider_metadata,
        }
        tool_calls.append(call_info)
        return AgentStreamFrame(
            turn_id=turn_id,
            event_type="tool_call",
            tool_name=delta.tool_name,
            call_id=delta.call_id,
            agent_name=active_agent.name,
            payload=delta.arguments or {},
        )
    if delta.event_type == "usage":
        if delta.prompt_tokens is not None:
            token_usage["prompt_tokens"] = token_usage.get("prompt_tokens", 0) + delta.prompt_tokens
        if delta.completion_tokens is not None:
            token_usage["completion_tokens"] = token_usage.get("completion_tokens", 0) + delta.completion_tokens
        if delta.thought_tokens is not None:
            token_usage["thought_tokens"] = token_usage.get("thought_tokens", 0) + delta.thought_tokens
    return None


def _check_agent_transfer(
    target: "Agent | AgentGroup",
    tool_calls: "list[dict[str, Any]]",
    turn_id: "str",
) -> "tuple[Agent | None, AgentStreamFrame | None]":
    """Check if any tool calls triggered agent transfer within an AgentGroup."""
    if isinstance(target, AgentGroup):
        for call in tool_calls:
            tc = ToolCall(
                name=call.get("name", ""),
                call_id=call.get("call_id", ""),
                arguments=call.get("arguments", {}),
            )
            new_agent = target.resolve_transfer(tc)
            if new_agent is not None:
                frame = AgentStreamFrame(
                    turn_id=turn_id,
                    event_type="agent_transfer",
                    agent_name=new_agent.name,
                    payload={"reason": tc.arguments.get("reason", "")},
                )
                return new_agent, frame
    return None, None


class AgentRuntime:
    """Runs agent turns with budgets, owner-scoped sessions, and streamed frames."""

    def __init__(
        self,
        target: "Agent | AgentGroup",
        *,
        default_model: "ModelClient | None" = None,
        budget: "TurnBudget | None" = None,
        sessions: "SessionStore | None" = None,
        span_manager: "SpanManager | None" = None,
        compaction_hook: "CompactionHook | None" = None,
        stream_buffer: "int" = 64,
    ) -> "None":
        """Initialize runtime with target agent or group and execution policies."""
        self.target = target
        self.default_model = default_model
        self.budget = budget or TurnBudget()
        self.sessions = sessions or MemorySessionStore()
        self.span_manager = span_manager or SpanManager()
        self.compaction_hook = compaction_hook
        self.stream_buffer = stream_buffer
        self._model_cache: dict[str, ModelClient] = {}

    def _resolve_model(self, agent: "Agent") -> "ModelClient":
        """Resolve active model client for an agent."""
        if isinstance(agent.model, ModelClient):
            return agent.model
        if isinstance(agent.model, str):
            client = self._model_cache.get(agent.model)
            if client is None:
                client = GoogleGenAIClient(model=agent.model)
                self._model_cache[agent.model] = client
            return client
        if self.default_model is not None:
            return self.default_model
        default_client = self._model_cache.get("")
        if default_client is None:
            default_client = GoogleGenAIClient()
            self._model_cache[""] = default_client
        return default_client

    async def _resolve_session(self, turn: "TurnRequest") -> "tuple[str, str, str]":
        """Resolve store owner, session id, and turn id."""
        owner = turn.context.owner_key() if turn.context is not None else None
        if owner is not None:
            store_owner = owner
            sid = turn.session_id or uuid4().hex
        else:
            store_owner = ANONYMOUS_OWNER
            if turn.session_id:
                existing = await self.sessions.load(ANONYMOUS_OWNER, turn.session_id)
                sid = turn.session_id if existing is not None else uuid4().hex
            else:
                sid = uuid4().hex
        tid = turn.turn_id or uuid4().hex
        return store_owner, sid, tid

    async def get_history(
        self,
        context: "ToolContext | None",
        session_id: "str",
    ) -> "list[AgentMessage] | None":
        """Return conversation history scoped to connection owner."""
        owner = context.owner_key() if context is not None else None
        store_owner = owner if owner is not None else ANONYMOUS_OWNER
        return await self.sessions.load(store_owner, session_id)

    async def _apply_compaction(self, messages: "list[AgentMessage]") -> "list[AgentMessage]":
        """Run history compaction hook if configured."""
        if self.compaction_hook is None:
            return messages
        result = self.compaction_hook(messages)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _execute_tools(
        self,
        tool_calls: "list[dict[str, Any]]",
        tools_map: "dict[str, Tool]",
        context: "Any",
        turn_id: "str",
        active_agent: "Agent",
    ) -> "tuple[list[dict[str, Any]], list[AgentStreamFrame]]":
        """Execute tool calls in parallel and build corresponding stream frames."""
        calls = [
            ToolCall(
                name=c["name"],
                call_id=c.get("call_id") or "",
                arguments=c.get("arguments") or {},
                provider_metadata=c.get("provider_metadata") or {},
            )
            for c in tool_calls
        ]
        tool_spans = [
            self.span_manager.start_tool_span(
                tool_name=call.name,
                call_id=call.call_id,
            )
            for call in calls
        ]
        try:
            raw_results = await execute_tool_calls(
                calls,
                tools_map,
                context=context,
            )
            tool_results = [
                {
                    "call_id": res.call_id,
                    "name": res.name,
                    "content": res.content,
                    "is_error": res.is_error,
                }
                for res in raw_results
            ]
        finally:
            for s in tool_spans:
                self.span_manager.end_span(s)

        frames = [
            AgentStreamFrame(
                turn_id=turn_id,
                event_type="tool_result",
                tool_name=res.name,
                call_id=res.call_id,
                agent_name=active_agent.name,
                payload={
                    "call_id": res.call_id,
                    "name": res.name,
                    "content": res.content,
                    "is_error": res.is_error,
                },
            )
            for res in raw_results
        ]
        return tool_results, frames

    async def produce(
        self,
        turn: "TurnRequest",
        send: "MemoryObjectSendStream[AgentStreamFrame]",
    ) -> "TurnResponse":
        """Execute turn loop as a producer coroutine emitting frames to the send stream."""
        store_owner, sid, tid = await self._resolve_session(turn)
        seq_counter = 0

        async def emit(frame: "AgentStreamFrame") -> "None":
            nonlocal seq_counter
            seq_counter += 1
            stamped = msgspec.structs.replace(frame, seq=seq_counter)
            await send.send(stamped)

        active_agent = self.target.coordinator if isinstance(self.target, AgentGroup) else self.target
        group_name = self.target.coordinator.name if isinstance(self.target, AgentGroup) else None

        span = self.span_manager.start_agent_span(
            agent_name=active_agent.name,
            agent_group=group_name,
            session_id=sid,
            turn_id=tid,
        )

        try:
            with self.span_manager.use(span):
                await emit(
                    AgentStreamFrame(
                        event_type="session",
                        turn_id=tid,
                        agent_name=active_agent.name,
                        payload={"session_id": sid, "turn_id": tid},
                    )
                )

                deadline = anyio.current_time() + self.budget.timeout_seconds
                async with self.sessions.lock(store_owner, sid):
                    history = await self.sessions.load(store_owner, sid)
                    messages: list[AgentMessage] = list(history) if history is not None else []
                    if turn.user_message:
                        messages.append(AgentMessage(role="user", content=turn.user_message))

                    token_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
                    accumulated_text: list[str] = []

                    active_agent = await self._execute_turn_loop(
                        active_agent=active_agent,
                        messages=messages,
                        turn=turn,
                        tid=tid,
                        deadline=deadline,
                        emit=emit,
                        accumulated_text=accumulated_text,
                        token_usage=token_usage,
                    )

                    await self.sessions.save(store_owner, sid, messages)

                output_text = "".join(accumulated_text)
                await emit(
                    AgentStreamFrame(
                        event_type="complete",
                        turn_id=tid,
                        agent_name=active_agent.name,
                        payload={
                            "output": output_text,
                            "token_usage": token_usage,
                        },
                    )
                )

                self.span_manager.set_usage(
                    span,
                    token_usage.get("prompt_tokens", 0),
                    token_usage.get("completion_tokens", 0),
                )
                self.span_manager.end_span(span)
                return TurnResponse(
                    session_id=sid,
                    turn_id=tid,
                    output=output_text,
                    agent_name=active_agent.name,
                    messages=messages,
                    token_usage=token_usage,
                )
        except TimeoutError as exc:
            budget_err = BudgetExceededError(f"Turn timed out after {self.budget.timeout_seconds}s")
            self.span_manager.end_span(span, error=budget_err)
            seq_counter += 1
            err_frame = AgentStreamFrame(
                event_type="error",
                turn_id=tid,
                seq=seq_counter,
                agent_name=active_agent.name,
                payload={"error": str(budget_err)},
            )
            with suppress(anyio.WouldBlock, anyio.BrokenResourceError, anyio.ClosedResourceError):
                send.send_nowait(err_frame)
            raise budget_err from exc
        except Exception as exc:
            self.span_manager.end_span(span, error=exc)
            seq_counter += 1
            err_frame = AgentStreamFrame(
                event_type="error",
                turn_id=tid,
                seq=seq_counter,
                agent_name=active_agent.name,
                payload={"error": f"{type(exc).__name__}: {exc}"},
            )
            with suppress(anyio.WouldBlock, anyio.BrokenResourceError, anyio.ClosedResourceError):
                send.send_nowait(err_frame)
            raise

    @asynccontextmanager
    async def open_stream(
        self,
        turn: "TurnRequest",
    ) -> "AsyncIterator[MemoryObjectReceiveStream[AgentStreamFrame]]":
        """Open a background task group streaming frames to an in-memory receive stream."""
        send, receive = anyio.create_memory_object_stream[AgentStreamFrame](self.stream_buffer)
        async with anyio.create_task_group() as tg:

            async def _runner() -> None:
                async with send:
                    try:
                        await self.produce(turn, send)
                    except Exception:
                        _logger.debug("Exception in stream producer", exc_info=True)

            tg.start_soon(_runner)
            try:
                async with receive:
                    yield receive
            finally:
                tg.cancel_scope.cancel()

    async def stream(
        self,
        turn: "TurnRequest",
    ) -> "AsyncIterator[AgentStreamFrame]":
        """Yield stream frames sequentially from open_stream."""
        async with self.open_stream(turn) as stream_ctx:
            async for frame in stream_ctx:
                yield frame

    async def run_turn(self, turn: "TurnRequest") -> "TurnResponse":
        """Run turn to completion and return TurnResponse."""
        send, receive = anyio.create_memory_object_stream[AgentStreamFrame](math.inf)
        async with send, receive:
            return await self.produce(turn, send)

    async def _execute_turn_loop(
        self,
        active_agent: Agent,
        messages: list[AgentMessage],
        turn: "TurnRequest",
        tid: str,
        deadline: float,
        emit: Callable[[AgentStreamFrame], Awaitable[None]],
        accumulated_text: list[str],
        token_usage: dict[str, int],
    ) -> Agent:
        """Run the multi-turn model calling and tool execution loop."""
        completed_naturally = False
        total_tool_calls = 0

        for _ in range(self.budget.max_turns):
            messages[:] = await self._apply_compaction(messages)
            model_client = self._resolve_model(active_agent)
            active_tools = (
                self.target.tools_for(active_agent) if isinstance(self.target, AgentGroup) else active_agent.tool_set
            )
            tools_map: dict[str, Tool] = {t.name: t for t in active_tools}
            instructions = active_agent.combined_instructions
            if turn.dynamic_context:
                instructions = f"{turn.dynamic_context}\n\n{instructions}"

            turn_text: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            stream_iter = model_client.stream_turn(
                system_instruction=instructions,
                messages=messages,
                tools=list(tools_map.values()),
            )
            await self._drain_stream(stream_iter, deadline, active_agent, tid, turn_text, tool_calls, token_usage, emit)

            complete_turn_text = "".join(turn_text)
            if complete_turn_text:
                accumulated_text.append(complete_turn_text)

            if not tool_calls:
                messages.append(
                    AgentMessage(
                        role="assistant",
                        content=complete_turn_text,
                        agent_name=active_agent.name,
                    )
                )
                completed_naturally = True
                break

            total_tool_calls += len(tool_calls)
            if total_tool_calls > self.budget.max_tool_calls:
                msg = (
                    f"Tool call budget exceeded "
                    f"({total_tool_calls} > {self.budget.max_tool_calls}, "
                    f"max_tool_calls={self.budget.max_tool_calls})"
                )
                raise BudgetExceededError(msg)

            call_objects = [
                ToolCall(
                    name=c["name"],
                    call_id=c.get("call_id") or "",
                    arguments=c.get("arguments") or {},
                    provider_metadata=c.get("provider_metadata") or {},
                )
                for c in tool_calls
            ]
            messages.append(
                AgentMessage(
                    role="assistant",
                    content=complete_turn_text,
                    tool_calls=call_objects,
                    agent_name=active_agent.name,
                )
            )

            with anyio.fail_after(max(deadline - anyio.current_time(), 0.0)):
                tool_results, result_frames = await self._execute_tools(
                    tool_calls, tools_map, turn.context, tid, active_agent
                )
            for f in result_frames:
                await emit(f)

            result_objects = [
                ToolResult(
                    call_id=r.get("call_id") or "",
                    name=r.get("name") or "",
                    content=r.get("content"),
                    is_error=bool(r.get("is_error", False)),
                )
                for r in tool_results
            ]
            messages.append(
                AgentMessage(
                    role="tool",
                    content="",
                    tool_results=result_objects,
                    agent_name=active_agent.name,
                )
            )

            new_agent, transfer_frame = _check_agent_transfer(self.target, tool_calls, tid)
            if new_agent is not None:
                active_agent = new_agent
            if transfer_frame is not None:
                await emit(transfer_frame)

        if not completed_naturally:
            msg = f"Turn budget exceeded (max_turns={self.budget.max_turns})"
            raise BudgetExceededError(msg)

        return active_agent

    @staticmethod
    async def _drain_stream(
        stream_iter: AsyncIterator[ModelDelta],
        deadline: float,
        active_agent: Agent,
        tid: str,
        turn_text: list[str],
        tool_calls: list[dict[str, Any]],
        token_usage: dict[str, int],
        emit: Callable[[AgentStreamFrame], Awaitable[None]],
    ) -> None:
        """Drain model delta stream until completion or deadline."""
        try:
            while True:
                delta = await _next_before(stream_iter, deadline)
                if delta is None:
                    break
                frame = _process_delta(delta, active_agent, tid, turn_text, tool_calls, token_usage)
                if frame is not None:
                    await emit(frame)
        finally:
            if hasattr(stream_iter, "aclose"):
                aclose_fn = cast("Any", stream_iter).aclose
                res = aclose_fn()
                if inspect.isawaitable(res):
                    await res
