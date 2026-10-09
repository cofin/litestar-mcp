"""Agent abstractions and multi-model runtime."""

from __future__ import annotations

from litestar_mcp.agent.bridges.a2a import agent_to_a2a
from litestar_mcp.agent.bridges.mcp import (
    MCPHttpClient,
    agent_to_mcp,
    discover_mcp_tools,
    mcp_to_tools,
)
from litestar_mcp.agent.context import ToolContext, resolve_tool_context
from litestar_mcp.agent.controller import AgentChatController
from litestar_mcp.agent.guards import BudgetExceededError, TurnBudget
from litestar_mcp.agent.models import (
    GoogleGenAIClient,
    MockModelClient,
    ModelClient,
    ModelDelta,
)
from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest, TurnResponse
from litestar_mcp.agent.spec import Agent, AgentGroup, AgentMessage
from litestar_mcp.agent.streaming import AgentStreamFrame, iter_agent_stream
from litestar_mcp.agent.tools import (
    RunContextRegistry,
    Tool,
    execute_parallel,
    execute_tools_in_parallel,
    get_current_tool_context,
    set_current_tool_context,
    tool,
)
from litestar_mcp.agent.workflow import (
    DynamicWorkflow,
    StepResult,
    WorkflowContext,
    WorkflowEngine,
    WorkflowNode,
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
    "MCPHttpClient",
    "MockModelClient",
    "ModelClient",
    "ModelDelta",
    "RunContextRegistry",
    "StepResult",
    "Tool",
    "ToolContext",
    "TurnBudget",
    "TurnRequest",
    "TurnResponse",
    "WorkflowContext",
    "WorkflowEngine",
    "WorkflowNode",
    "agent_to_a2a",
    "agent_to_mcp",
    "discover_mcp_tools",
    "execute_parallel",
    "execute_tools_in_parallel",
    "get_current_tool_context",
    "iter_agent_stream",
    "mcp_to_tools",
    "resolve_tool_context",
    "set_current_tool_context",
    "tool",
)
