"""Guarded agent runtime managing multi-turn tool calling, delegation, and telemetry."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import anyio

from litestar_mcp.agent.guards import BudgetExceededError, TurnBudget
from litestar_mcp.agent.models import GoogleGenAIClient, ModelClient, ModelDelta
from litestar_mcp.agent.spec import Agent, AgentGroup, AgentMessage
from litestar_mcp.agent.streaming import AgentStreamFrame
from litestar_mcp.agent.tools import Tool, execute_tools_in_parallel, set_current_tool_context
from litestar_mcp.core.observability import SpanManager, TelemetryConfig

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from litestar_mcp.agent.context import ToolContext

    CompactionHook = Callable[[list[AgentMessage]], Awaitable[list[AgentMessage]] | list[AgentMessage]]


@dataclass(slots=True)
class TurnRequest:
    """Request payload for an agent turn."""

    session_id: str = "default"
    turn_id: str = "turn-1"
    user_message: str = ""
    dynamic_context: str | None = None
    context: ToolContext | None = None


@dataclass(slots=True)
class TurnResponse:
    """Aggregated response produced by an agent turn."""

    session_id: str
    turn_id: str
    output: str
    messages: list[AgentMessage] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    agent_name: str | None = None

    @property
    def content(self) -> str:
        """Alias for output text produced by the turn."""
        return self.output


def _process_delta(
    delta: ModelDelta,
    active_agent: Agent,
    turn_id: str,
    turn_text: list[str],
    tool_calls: list[dict[str, Any]],
    token_usage: dict[str, int],
) -> AgentStreamFrame | None:
    """Process an incremental model delta and update turn state."""
    if delta.event_type == "delta" and delta.text:
        turn_text.append(delta.text)
        return AgentStreamFrame(
            turn_id=turn_id,
            event_type="delta",
            delta=delta.text,
            agent_name=active_agent.name,
        )
    if delta.event_type == "thought" and delta.thought:
        return AgentStreamFrame(
            turn_id=turn_id,
            event_type="thought",
            thought=delta.thought,
            agent_name=active_agent.name,
        )
    if delta.event_type == "tool_call" and delta.tool_name:
        call_info = {
            "name": delta.tool_name,
            "call_id": delta.call_id,
            "arguments": delta.arguments or {},
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
            token_usage["prompt_tokens"] += delta.prompt_tokens
        if delta.completion_tokens is not None:
            token_usage["completion_tokens"] += delta.completion_tokens
    return None


def _check_agent_transfer(
    target: Any,
    tool_calls: list[dict[str, Any]],
    turn_id: str,
) -> tuple[Agent | None, AgentStreamFrame | None]:
    """Check if any tool calls triggered agent transfer within an AgentGroup."""
    if isinstance(target, AgentGroup):
        for call in tool_calls:
            if call.get("name") == "transfer_to_agent":
                args = call.get("arguments", {})
                target_name = args.get("target_agent")
                if target_name and target_name in target.specialists:
                    new_agent = target.specialists[target_name]
                    frame = AgentStreamFrame(
                        turn_id=turn_id,
                        event_type="agent_transfer",
                        agent_name=target_name,
                        payload={"reason": args.get("reason", "")},
                    )
                    return new_agent, frame
    return None, None


class AgentRuntime:
    """Guarded execution runtime managing multi-turn agent execution loops."""

    def __init__(
        self,
        target: Agent | AgentGroup | None = None,
        *,
        default_model: ModelClient | None = None,
        telemetry: TelemetryConfig | None = None,
        span_manager: SpanManager | None = None,
        budget: TurnBudget | None = None,
        max_turns: int = 15,
        max_tool_calls: int = 50,
        turn_timeout: float = 300.0,
        compaction_hook: CompactionHook | None = None,
    ) -> None:
        self.target = target
        self.default_model = default_model or GoogleGenAIClient()
        self.telemetry = telemetry or TelemetryConfig()
        self.span_manager = span_manager or SpanManager(self.telemetry)
        self.budget = budget or TurnBudget(
            max_turns=max_turns,
            max_tool_calls=max_tool_calls,
            timeout_seconds=turn_timeout,
        )
        self.max_turns = self.budget.max_turns
        self.max_tool_calls = self.budget.max_tool_calls
        self.turn_timeout = self.budget.timeout_seconds
        self.compaction_hook = compaction_hook
        self._sessions: dict[str, list[AgentMessage]] = {}

    def get_session_messages(self, session_id: str) -> list[AgentMessage]:
        """Return a copy of stored conversation messages for a session."""
        return list(self._sessions.get(session_id, []))

    def _resolve_target(self, override: Agent | AgentGroup | None) -> Agent | AgentGroup:
        """Resolve the target Agent or AgentGroup for a turn."""
        resolved = override or self.target
        if resolved is None:
            msg = "AgentRuntime requires a target Agent or AgentGroup"
            raise ValueError(msg)
        return resolved

    def _resolve_model(self, agent: Agent) -> ModelClient:
        """Resolve active model client for an agent."""
        if isinstance(agent.model, ModelClient):
            return agent.model
        if isinstance(agent.model, str):
            return GoogleGenAIClient(model=agent.model)
        return self.default_model

    async def _apply_compaction(self, messages: list[AgentMessage]) -> list[AgentMessage]:
        """Run the optional history compaction hook if configured."""
        if self.compaction_hook is None:
            return messages
        result = self.compaction_hook(messages)
        if inspect.isawaitable(result):
            return await result
        return result

    async def _execute_tools(
        self,
        tool_calls: list[dict[str, Any]],
        tools_map: dict[str, Tool],
        context: Any,
        turn_id: str,
        active_agent: Agent,
    ) -> tuple[list[dict[str, Any]], list[AgentStreamFrame]]:
        """Execute tool calls in parallel and build corresponding stream frames."""
        tool_span = self.span_manager.start_tool_span(
            tool_name=",".join([c["name"] for c in tool_calls]),
        )
        try:
            tool_results = await execute_tools_in_parallel(
                tool_calls,
                tools_map,
                context=context,
            )
        finally:
            self.span_manager.end_span(tool_span)

        frames = [
            AgentStreamFrame(
                turn_id=turn_id,
                event_type="tool_result",
                tool_name=result.get("name"),
                call_id=result.get("call_id"),
                agent_name=active_agent.name,
                payload=result,
            )
            for result in tool_results
        ]
        return tool_results, frames

    async def _run_turn_loop(
        self,
        target: Agent | AgentGroup,
        active_agent: Agent,
        request: TurnRequest,
        messages: list[AgentMessage],
        accumulated_text: list[str],
        token_usage: dict[str, int],
    ) -> AsyncIterator[AgentStreamFrame]:
        """Execute the inner multi-turn model and tool invocation loop."""
        turn_id = request.turn_id
        total_tool_calls = 0
        completed_naturally = False

        for _ in range(self.budget.max_turns):
            messages[:] = await self._apply_compaction(messages)
            model_client = self._resolve_model(active_agent)
            tools_map: dict[str, Tool] = {t.name: t for t in active_agent.get_all_tools()}
            instructions = active_agent.get_combined_instructions()
            if request.dynamic_context:
                instructions = f"{request.dynamic_context}\n\n{instructions}"

            turn_text: list[str] = []
            tool_calls: list[dict[str, Any]] = []

            async for delta in model_client.stream_turn(
                system_instruction=instructions,
                messages=messages,
                tools=list(tools_map.values()),
            ):
                frame = _process_delta(delta, active_agent, turn_id, turn_text, tool_calls, token_usage)
                if frame is not None:
                    yield frame

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

            messages.append(
                AgentMessage(
                    role="assistant",
                    content=complete_turn_text,
                    tool_calls=tool_calls,
                    agent_name=active_agent.name,
                )
            )

            tool_results, result_frames = await self._execute_tools(
                tool_calls, tools_map, request.context, turn_id, active_agent
            )
            for f in result_frames:
                yield f

            messages.append(
                AgentMessage(
                    role="tool",
                    content="",
                    tool_results=tool_results,
                    agent_name=active_agent.name,
                )
            )

            new_agent, transfer_frame = _check_agent_transfer(target, tool_calls, turn_id)
            if new_agent is not None:
                active_agent = new_agent
            if transfer_frame is not None:
                yield transfer_frame

        if not completed_naturally:
            msg = f"Turn budget exceeded (max_turns={self.budget.max_turns})"
            raise BudgetExceededError(msg)

        yield AgentStreamFrame(
            turn_id=turn_id,
            event_type="complete",
            agent_name=active_agent.name,
            payload={
                "output": "".join(accumulated_text),
                "token_usage": token_usage,
            },
        )

    async def stream_turn(
        self,
        request: TurnRequest,
        history: list[AgentMessage] | None = None,
        *,
        agent: Agent | AgentGroup | None = None,
        _out_messages: list[AgentMessage] | None = None,
    ) -> AsyncIterator[AgentStreamFrame]:
        """Stream an agent turn with multi-turn tool calling, budget guards, and delegation."""
        target = self._resolve_target(agent)
        base_history = history if history is not None else self._sessions.get(request.session_id, [])
        messages: list[AgentMessage] = list(base_history)
        if request.user_message:
            messages.append(AgentMessage(role="user", content=request.user_message))

        active_agent = target.coordinator if isinstance(target, AgentGroup) else target
        group_name = target.coordinator.name if isinstance(target, AgentGroup) else None

        span = self.span_manager.start_agent_span(
            agent_name=active_agent.name,
            agent_group=group_name,
        )

        token_usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        accumulated_text: list[str] = []
        turn_id = request.turn_id

        set_current_tool_context(request.context)
        try:
            with anyio.fail_after(self.budget.timeout_seconds):
                async for frame in self._run_turn_loop(
                    target,
                    active_agent,
                    request,
                    messages,
                    accumulated_text,
                    token_usage,
                ):
                    if frame.agent_name is not None and frame.event_type == "agent_transfer":
                        active_agent = (
                            target.get_agent(frame.agent_name) if isinstance(target, AgentGroup) else active_agent
                        )
                    yield frame

            self._sessions[request.session_id] = list(messages)
            if _out_messages is not None:
                _out_messages[:] = messages
        except TimeoutError as exc:
            budget_err = BudgetExceededError(f"Turn timed out after {self.budget.timeout_seconds}s")
            self.span_manager.end_span(span, error=budget_err)
            yield AgentStreamFrame(
                turn_id=turn_id,
                event_type="error",
                agent_name=active_agent.name,
                payload={"error": str(budget_err)},
            )
            raise budget_err from exc
        except Exception as exc:
            self.span_manager.end_span(span, error=exc)
            yield AgentStreamFrame(
                turn_id=turn_id,
                event_type="error",
                agent_name=active_agent.name,
                payload={"error": str(exc)},
            )
            raise
        else:
            self.span_manager.end_span(span)
        finally:
            set_current_tool_context(None)

    async def run_turn(
        self,
        request: TurnRequest,
        history: list[AgentMessage] | None = None,
        *,
        agent: Agent | AgentGroup | None = None,
    ) -> TurnResponse:
        """Run an agent turn to completion returning TurnResponse with full conversation history."""
        out_messages: list[AgentMessage] = []
        output_chunks: list[str] = []
        token_usage: dict[str, int] = {}
        final_agent_name: str | None = None

        async for frame in self.stream_turn(
            request,
            history=history,
            agent=agent,
            _out_messages=out_messages,
        ):
            if frame.agent_name is not None:
                final_agent_name = frame.agent_name
            if frame.event_type == "delta" and frame.delta:
                output_chunks.append(frame.delta)
            elif frame.event_type == "complete" and frame.payload:
                token_usage = frame.payload.get("token_usage", {})

        final_output = "".join(output_chunks)
        return TurnResponse(
            session_id=request.session_id,
            turn_id=request.turn_id,
            output=final_output,
            messages=out_messages,
            token_usage=token_usage,
            agent_name=final_agent_name,
        )


__all__ = (
    "AgentRuntime",
    "BudgetExceededError",
    "CompactionHook",
    "TurnBudget",
    "TurnRequest",
    "TurnResponse",
)
