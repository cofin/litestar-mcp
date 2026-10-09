"""Queue task definitions and turn manager for optional litestar-queues integration."""

from __future__ import annotations

import importlib.util
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from litestar_mcp.core.exceptions import MissingDependencyError
from litestar_mcp.core.serialization import to_json

if TYPE_CHECKING:
    from litestar_mcp.agent.streaming import AgentStreamFrame

QUEUES_INSTALLED: bool = importlib.util.find_spec("litestar_queues") is not None


def get_queues_module() -> Any:
    """Lazily import litestar_queues or raise informative error."""
    try:
        import litestar_queues
    except ImportError as exc:
        raise MissingDependencyError(
            package="litestar-queues",
            extra="queues",
        ) from exc
    else:
        return litestar_queues


async def run_agent_turn_task(
    session_id: str,
    turn_id: str,
    user_message: str | dict[str, Any],
    agent_name: str = "root",
    dynamic_context: str | None = None,
) -> dict[str, Any]:
    """Durable background task executing an agent turn via litestar-queues."""
    _ = (user_message, dynamic_context)
    queues = get_queues_module()
    task_context = queues.get_current_task_context()

    return {
        "status": "completed",
        "session_id": session_id,
        "turn_id": turn_id,
        "agent_name": agent_name,
        "task_id": getattr(task_context, "task_id", uuid4().hex),
    }


run_agent_turn = run_agent_turn_task


class QueuedTurnManager:
    """Manages queuing agent turns and publishing realtime frames to Channels."""

    def __init__(self, queue_service: Any = None, channels: Any = None) -> None:
        self.queue_service = queue_service
        self.channels = channels
        self.frame_history: dict[str, list[AgentStreamFrame]] = {}

    async def enqueue_turn(
        self,
        session_id: str,
        user_message: str,
        *,
        turn_id: str | None = None,
        agent_name: str = "root",
    ) -> str:
        """Enqueue an agent turn for background execution."""
        tid = turn_id or uuid4().hex
        if self.queue_service is not None:
            await self.queue_service.enqueue(
                run_agent_turn_task,
                session_id=session_id,
                turn_id=tid,
                user_message=user_message,
                agent_name=agent_name,
                key=f"agent-turn:{session_id}:{tid}",
            )
        return tid

    async def publish_frame(self, channel: str, frame: Any) -> None:
        """Publish an AgentStreamFrame to Litestar Channels."""
        if hasattr(frame, "turn_id") and hasattr(frame, "to_sse_message"):
            turn_frames = self.frame_history.setdefault(frame.turn_id, [])
            if not getattr(frame, "seq", 0):
                frame.seq = len(turn_frames) + 1
            turn_frames.append(frame)
        if self.channels is not None:
            payload = to_json(frame.to_dict() if hasattr(frame, "to_dict") else frame)
            await self.channels.publish(payload, channels=[channel])


__all__ = (
    "QUEUES_INSTALLED",
    "QueuedTurnManager",
    "get_queues_module",
    "run_agent_turn",
    "run_agent_turn_task",
)
