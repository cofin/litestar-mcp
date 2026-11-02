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
from litestar_mcp.queues.dependencies import chain_task_dependencies
from litestar_mcp.queues.mcp_tasks import (
    RUN_TOOL_TASK,
    QueueTaskExecutor,
    run_mcp_tool_task,
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
from litestar_mcp.queues.workflow import (
    ADVANCE_TASK,
    EXECUTE_NODE_TASK,
    DurableWorkflowEngine,
    WorkflowRegistry,
    advance_workflow,
    execute_workflow_node,
)
from litestar_mcp.queues.workflow_store import (
    NodeRecord,
    RunStatus,
    WorkflowRun,
    WorkflowStore,
)

__all__ = (
    "ADVANCE_TASK",
    "EXECUTE_NODE_TASK",
    "QUEUES_INSTALLED",
    "RUN_TOOL_TASK",
    "AgentRegistry",
    "DurableWorkflowEngine",
    "NamedDependency",
    "NodeRecord",
    "PrincipalSnapshot",
    "QueueTaskExecutor",
    "QueuedAgentChatController",
    "QueuedTurn",
    "QueuedTurnManager",
    "RunStatus",
    "WorkflowRegistry",
    "WorkflowRun",
    "WorkflowStore",
    "advance_workflow",
    "agent_turn_channel",
    "chain_task_dependencies",
    "emit_agent_frame",
    "execute_workflow_node",
    "get_agent_registry",
    "get_channels_service",
    "get_queues_module",
    "run_agent_turn",
    "run_agent_turn_task",
    "run_mcp_tool_task",
    "set_agent_registry",
    "set_channels_service",
)
