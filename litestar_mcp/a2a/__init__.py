"""Agent-to-Agent (A2A) protocol implementation for Litestar."""

from litestar_mcp.a2a.config import A2AConfig, A2AOptKeys
from litestar_mcp.a2a.context import TaskContext
from litestar_mcp.a2a.manifest import build_agent_card
from litestar_mcp.a2a.plugin import A2APlugin
from litestar_mcp.a2a.registry import A2ARegistry, SkillRegistration
from litestar_mcp.a2a.routes import A2AController
from litestar_mcp.a2a.service import A2AHandlerService
from litestar_mcp.a2a.streaming import A2ASubscriptionManager, format_a2a_sse_event
from litestar_mcp.a2a.tasks import A2AMemoryTaskStore, A2ATaskStore
from litestar_mcp.a2a.types import (
    TERMINAL_A2A_STATES,
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    Artifact,
    DataPart,
    FilePart,
    Message,
    Part,
    SecurityRequirement,
    SecurityScheme,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
    TextPart,
    ThoughtPart,
)

__all__ = (
    "TERMINAL_A2A_STATES",
    "A2AConfig",
    "A2AController",
    "A2AHandlerService",
    "A2AMemoryTaskStore",
    "A2AOptKeys",
    "A2APlugin",
    "A2ARegistry",
    "A2ASubscriptionManager",
    "A2ATaskStore",
    "AgentCapabilities",
    "AgentCard",
    "AgentProvider",
    "AgentSkill",
    "Artifact",
    "DataPart",
    "FilePart",
    "Message",
    "Part",
    "SecurityRequirement",
    "SecurityScheme",
    "SkillRegistration",
    "Task",
    "TaskArtifactUpdateEvent",
    "TaskContext",
    "TaskState",
    "TaskStatus",
    "TaskStatusUpdateEvent",
    "TextPart",
    "ThoughtPart",
    "build_agent_card",
    "format_a2a_sse_event",
)
