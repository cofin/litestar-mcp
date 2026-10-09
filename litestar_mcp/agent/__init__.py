"""Agent abstractions and multi-model runtime."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litestar_mcp.agent.bridges.a2a import agent_to_a2a as agent_to_a2a
    from litestar_mcp.agent.bridges.mcp import (
        agent_to_mcp as agent_to_mcp,
    )
    from litestar_mcp.agent.bridges.mcp import (
        discover_mcp_tools as discover_mcp_tools,
    )
    from litestar_mcp.agent.bridges.mcp import (
        mcp_to_tools as mcp_to_tools,
    )
    from litestar_mcp.agent.controller import AgentChatController as AgentChatController
    from litestar_mcp.agent.guards import (
        BudgetExceededError as BudgetExceededError,
    )
    from litestar_mcp.agent.guards import (
        TurnBudget as TurnBudget,
    )
    from litestar_mcp.agent.models import (
        GoogleGenAIClient as GoogleGenAIClient,
    )
    from litestar_mcp.agent.models import (
        MockModelClient as MockModelClient,
    )
    from litestar_mcp.agent.models import (
        ModelClient as ModelClient,
    )
    from litestar_mcp.agent.models import (
        ModelDelta as ModelDelta,
    )
    from litestar_mcp.agent.runtime import (
        AgentRuntime as AgentRuntime,
    )
    from litestar_mcp.agent.runtime import (
        TurnRequest as TurnRequest,
    )
    from litestar_mcp.agent.runtime import (
        TurnResponse as TurnResponse,
    )
    from litestar_mcp.agent.spec import (
        Agent as Agent,
    )
    from litestar_mcp.agent.spec import (
        AgentGroup as AgentGroup,
    )
    from litestar_mcp.agent.spec import (
        AgentMessage as AgentMessage,
    )
    from litestar_mcp.agent.streaming import (
        AgentStreamFrame as AgentStreamFrame,
    )
    from litestar_mcp.agent.streaming import (
        iter_agent_stream as iter_agent_stream,
    )
    from litestar_mcp.agent.workflow import (
        DynamicWorkflow as DynamicWorkflow,
    )
    from litestar_mcp.agent.workflow import (
        StepResult as StepResult,
    )
    from litestar_mcp.agent.workflow import (
        WorkflowContext as WorkflowContext,
    )
    from litestar_mcp.agent.workflow import (
        WorkflowEngine as WorkflowEngine,
    )
    from litestar_mcp.agent.workflow import (
        WorkflowNode as WorkflowNode,
    )

__all__ = (
    "Agent",
    "AgentChatController",
    "AgentGroup",
    "AgentMessage",
    "AgentRuntime",
    "AgentStreamFrame",
    "BudgetExceededError",
    "DynamicWorkflow",
    "GoogleGenAIClient",
    "MockModelClient",
    "ModelClient",
    "ModelDelta",
    "StepResult",
    "TurnBudget",
    "TurnRequest",
    "TurnResponse",
    "WorkflowContext",
    "WorkflowEngine",
    "WorkflowNode",
    "agent_to_a2a",
    "agent_to_mcp",
    "discover_mcp_tools",
    "iter_agent_stream",
    "mcp_to_tools",
)


_MODULE_MAP: dict[str, str] = {
    "Agent": "litestar_mcp.agent.spec",
    "AgentGroup": "litestar_mcp.agent.spec",
    "AgentMessage": "litestar_mcp.agent.spec",
    "AgentRuntime": "litestar_mcp.agent.runtime",
    "TurnRequest": "litestar_mcp.agent.runtime",
    "TurnResponse": "litestar_mcp.agent.runtime",
    "AgentStreamFrame": "litestar_mcp.agent.streaming",
    "iter_agent_stream": "litestar_mcp.agent.streaming",
    "AgentChatController": "litestar_mcp.agent.controller",
    "BudgetExceededError": "litestar_mcp.agent.guards",
    "TurnBudget": "litestar_mcp.agent.guards",
    "GoogleGenAIClient": "litestar_mcp.agent.models",
    "MockModelClient": "litestar_mcp.agent.models",
    "ModelClient": "litestar_mcp.agent.models",
    "ModelDelta": "litestar_mcp.agent.models",
    "DynamicWorkflow": "litestar_mcp.agent.workflow",
    "StepResult": "litestar_mcp.agent.workflow",
    "WorkflowContext": "litestar_mcp.agent.workflow",
    "WorkflowEngine": "litestar_mcp.agent.workflow",
    "WorkflowNode": "litestar_mcp.agent.workflow",
    "agent_to_a2a": "litestar_mcp.agent.bridges",
    "agent_to_mcp": "litestar_mcp.agent.bridges",
    "discover_mcp_tools": "litestar_mcp.agent.bridges",
    "mcp_to_tools": "litestar_mcp.agent.bridges",
}


def __getattr__(name: str) -> Any:
    """Lazily import agent attributes on demand."""
    if mod_path := _MODULE_MAP.get(name):
        import importlib

        mod = importlib.import_module(mod_path)
        return getattr(mod, name)

    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    return sorted({*globals(), *__all__})
