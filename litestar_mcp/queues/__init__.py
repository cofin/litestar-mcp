"""Optional litestar-queues integration for distributed agent execution."""

try:
    import litestar_queues
except ImportError as exc:
    from litestar_mcp.core.exceptions import MissingDependencyError

    raise MissingDependencyError(
        package="litestar-queues",
        extra="queues",
    ) from exc

from litestar_mcp.queues.bridge import (
    NamedDependency,
    QueuedAgentChatController,
    agent_turn_channel,
    emit_agent_frame,
)
from litestar_mcp.queues.tasks import (
    QUEUES_INSTALLED,
    AgentRegistry,
    PrincipalSnapshot,
    QueuedTurn,
    QueuedTurnManager,
    get_agent_registry,
    get_channels_service,
    get_queues_module,
    run_agent_turn,
    run_agent_turn_task,
    set_agent_registry,
    set_channels_service,
)

__all__ = (
    "QUEUES_INSTALLED",
    "AgentRegistry",
    "NamedDependency",
    "PrincipalSnapshot",
    "QueuedAgentChatController",
    "QueuedTurn",
    "QueuedTurnManager",
    "agent_turn_channel",
    "emit_agent_frame",
    "get_agent_registry",
    "get_channels_service",
    "get_queues_module",
    "run_agent_turn",
    "run_agent_turn_task",
    "set_agent_registry",
    "set_channels_service",
)
