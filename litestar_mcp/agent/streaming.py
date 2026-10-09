"""Typed SSE streaming frames and httpx2 EventSource consumer for agent execution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx2
from litestar.response.sse import ServerSentEventMessage

from litestar_mcp.core.serialization import from_json, to_json

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from litestar.types import TypeEncodersMap


class _DualAttributeFactory:
    """Descriptor returning an instance attribute on instances and a factory function on the class."""

    def __init__(self, slot_name: str, factory: Callable[..., AgentStreamFrame]) -> None:
        self.slot_name = slot_name
        self.factory = factory

    def __get__(self, instance: AgentStreamFrame | None, owner: type[AgentStreamFrame]) -> Any:
        if instance is None:

            def _bound(*args: Any, **kwargs: Any) -> AgentStreamFrame:
                return self.factory(owner, *args, **kwargs)

            return _bound
        return getattr(instance, self.slot_name)

    def __set__(self, instance: AgentStreamFrame, value: Any) -> None:
        setattr(instance, self.slot_name, value)


def _create_delta_frame(
    cls: type[AgentStreamFrame],
    text: str,
    *,
    turn_id: str = "",
    agent_name: str | None = None,
    seq: int = 0,
) -> AgentStreamFrame:
    """Create a text delta streaming frame."""
    return cls(
        turn_id=turn_id,
        event_type="delta",
        delta=text,
        agent_name=agent_name,
        seq=seq,
    )


def _create_thought_frame(
    cls: type[AgentStreamFrame],
    thought: str,
    *,
    turn_id: str = "",
    agent_name: str | None = None,
    seq: int = 0,
) -> AgentStreamFrame:
    """Create a reasoning thought streaming frame."""
    return cls(
        turn_id=turn_id,
        event_type="thought",
        thought=thought,
        agent_name=agent_name,
        seq=seq,
    )


class AgentStreamFrame:
    """Standardized streaming event frame emitted during agent execution."""

    __slots__ = (
        "_data_override",
        "_delta",
        "_thought",
        "agent_name",
        "call_id",
        "event_type",
        "payload",
        "seq",
        "tool_name",
        "turn_id",
    )

    delta: Any = _DualAttributeFactory("_delta", _create_delta_frame)
    thought: Any = _DualAttributeFactory("_thought", _create_thought_frame)

    def __init__(
        self,
        turn_id: str = "",
        event_type: str = "",
        delta: str | None = None,
        thought: str | None = None,
        tool_name: str | None = None,
        call_id: str | None = None,
        agent_name: str | None = None,
        payload: dict[str, Any] | None = None,
        *,
        event: str | None = None,
        data: dict[str, Any] | None = None,
        seq: int = 0,
    ) -> None:
        resolved_event = event or event_type or "message"
        self.event_type = resolved_event
        self.seq = seq
        self._data_override = data

        if data is not None:
            self.turn_id = str(data.get("turn_id", turn_id))
            self._delta = data.get("delta", delta)
            self._thought = data.get("thought", thought)
            self.tool_name = data.get("tool_name", tool_name)
            self.call_id = data.get("call_id", call_id)
            self.agent_name = data.get("agent_name", agent_name)
            self.payload = data.get("payload", payload)
        else:
            self.turn_id = turn_id
            self._delta = delta
            self._thought = thought
            self.tool_name = tool_name
            self.call_id = call_id
            self.agent_name = agent_name
            self.payload = payload

    @property
    def event(self) -> str:
        """Return the SSE event name."""
        return self.event_type

    @property
    def data(self) -> dict[str, Any]:
        """Return the dictionary payload for the frame."""
        if self._data_override is not None:
            return dict(self._data_override)
        return self.to_dict()

    @classmethod
    def session(
        cls,
        session_id: str,
        *,
        turn_id: str = "",
        seq: int = 0,
    ) -> AgentStreamFrame:
        """Create a session initialization frame."""
        return cls(
            turn_id=turn_id,
            event_type="session",
            payload={"session_id": session_id, "turn_id": turn_id},
            seq=seq,
        )

    @classmethod
    def tool_call(
        cls,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        call_id: str | None = None,
        turn_id: str = "",
        agent_name: str | None = None,
        seq: int = 0,
    ) -> AgentStreamFrame:
        """Create a tool call streaming frame."""
        return cls(
            turn_id=turn_id,
            event_type="tool_call",
            tool_name=tool_name,
            call_id=call_id,
            agent_name=agent_name,
            payload=arguments or {},
            seq=seq,
        )

    @classmethod
    def tool_result(
        cls,
        tool_name: str,
        result: dict[str, Any],
        *,
        call_id: str | None = None,
        turn_id: str = "",
        agent_name: str | None = None,
        seq: int = 0,
    ) -> AgentStreamFrame:
        """Create a tool result streaming frame."""
        return cls(
            turn_id=turn_id,
            event_type="tool_result",
            tool_name=tool_name,
            call_id=call_id,
            agent_name=agent_name,
            payload=result,
            seq=seq,
        )

    @classmethod
    def complete(
        cls,
        output: str = "",
        *,
        token_usage: dict[str, int] | None = None,
        turn_id: str = "",
        agent_name: str | None = None,
        seq: int = 0,
    ) -> AgentStreamFrame:
        """Create a turn completion streaming frame."""
        return cls(
            turn_id=turn_id,
            event_type="complete",
            agent_name=agent_name,
            payload={"output": output, "token_usage": token_usage or {}},
            seq=seq,
        )

    @classmethod
    def error(
        cls,
        message: str,
        *,
        turn_id: str = "",
        agent_name: str | None = None,
        seq: int = 0,
    ) -> AgentStreamFrame:
        """Create an error streaming frame."""
        return cls(
            turn_id=turn_id,
            event_type="error",
            agent_name=agent_name,
            payload={"error": message},
            seq=seq,
        )

    @classmethod
    def from_sse(cls, event: httpx2.ServerSentEvent) -> AgentStreamFrame:
        """Parse an httpx2.ServerSentEvent into an AgentStreamFrame."""
        parsed: dict[str, Any] = {}
        if event.data:
            raw = from_json(event.data)
            parsed = raw if isinstance(raw, dict) else {"value": raw}

        seq_num = 0
        if event.id and event.id.isdigit():
            seq_num = int(event.id)

        event_name = str(parsed.get("event_type") or event.event or "message")
        return cls(
            turn_id=str(parsed.get("turn_id", "")),
            event_type=event_name,
            delta=parsed.get("delta"),
            thought=parsed.get("thought"),
            tool_name=parsed.get("tool_name"),
            call_id=parsed.get("call_id"),
            agent_name=parsed.get("agent_name"),
            payload=parsed.get("payload"),
            data=parsed or None,
            seq=seq_num,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert frame to dictionary representation."""
        if self._data_override is not None:
            base = dict(self._data_override)
            base.setdefault("turn_id", self.turn_id)
            base.setdefault("event_type", self.event_type)
            return base
        data: dict[str, Any] = {
            "turn_id": self.turn_id,
            "event_type": self.event_type,
        }
        if self._delta is not None:
            data["delta"] = self._delta
        if self._thought is not None:
            data["thought"] = self._thought
        if self.tool_name is not None:
            data["tool_name"] = self.tool_name
        if self.call_id is not None:
            data["call_id"] = self.call_id
        if self.agent_name is not None:
            data["agent_name"] = self.agent_name
        if self.payload is not None:
            data["payload"] = self.payload
        return data

    def to_json(self, type_encoders: TypeEncodersMap | None = None) -> str:
        """Serialize frame to JSON string using Litestar's serialization pipeline."""
        return to_json(self.to_dict(), as_bytes=False, type_encoders=type_encoders)

    def to_sse_message(self, type_encoders: TypeEncodersMap | None = None) -> ServerSentEventMessage:
        """Convert frame to a Litestar ServerSentEventMessage."""
        payload = self.to_json(type_encoders=type_encoders)
        return ServerSentEventMessage(
            id=str(self.seq) if self.seq else None,
            event=self.event_type,
            data=payload,
        )


async def iter_agent_stream(
    response: httpx2.Response,
    *,
    max_event_size: int | None = 1048576,
) -> AsyncIterator[AgentStreamFrame]:
    """Consume a streaming httpx2.Response using httpx2.EventSource and yield AgentStreamFrame objects."""
    event_source = httpx2.EventSource(response, max_event_size=max_event_size)
    async for sse in event_source:
        if not sse.data:
            continue
        yield AgentStreamFrame.from_sse(sse)


__all__ = (
    "AgentStreamFrame",
    "iter_agent_stream",
)
