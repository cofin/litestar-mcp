"""Optional litestar-queues integration for distributed agent execution."""

from __future__ import annotations

from litestar_mcp.queues.bridge import (
    QueuedAgentChatController,
    agent_turn_channel,
    emit_agent_frame,
)
from litestar_mcp.queues.tasks import (
    QUEUES_INSTALLED,
    QueuedTurnManager,
    get_queues_module,
    run_agent_turn,
    run_agent_turn_task,
)

__all__ = (
    "QUEUES_INSTALLED",
    "QueuedAgentChatController",
    "QueuedTurnManager",
    "agent_turn_channel",
    "emit_agent_frame",
    "get_queues_module",
    "run_agent_turn",
    "run_agent_turn_task",
)
