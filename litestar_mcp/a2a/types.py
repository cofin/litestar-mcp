"""Canonical data models and schemas for the Agent-to-Agent (A2A) v1.0 protocol."""

from typing import Any, Literal

import msgspec

TaskState = Literal[
    "submitted",
    "working",
    "completed",
    "failed",
    "canceled",
    "rejected",
    "input_required",
    "auth_required",
]

TERMINAL_A2A_STATES: frozenset[str] = frozenset({"completed", "failed", "canceled", "rejected"})


class TextPart(msgspec.Struct, rename="camel", tag="text", tag_field="type"):
    """Text content part of a message or artifact."""

    text: str
    metadata: dict[str, Any] | None = None

    @property
    def type(self) -> str:
        """Return the discriminator type string."""
        return "text"


class DataPart(msgspec.Struct, rename="camel", tag="data", tag_field="type"):
    """Structured data (JSON) content part of a message or artifact."""

    data: Any
    metadata: dict[str, Any] | None = None

    @property
    def type(self) -> str:
        """Return the discriminator type string."""
        return "data"


class FilePart(msgspec.Struct, rename="camel", tag="file", tag_field="type"):
    """File/blob content part of a message or artifact."""

    mime_type: str
    uri: str | None = None
    data: str | None = None
    name: str | None = None
    metadata: dict[str, Any] | None = None

    @property
    def type(self) -> str:
        """Return the discriminator type string."""
        return "file"


class ThoughtPart(msgspec.Struct, rename="camel", tag="thought", tag_field="type"):
    """Internal reasoning or progress thought emitted by an agent."""

    thought: str
    metadata: dict[str, Any] | None = None

    @property
    def type(self) -> str:
        """Return the discriminator type string."""
        return "thought"


Part = TextPart | DataPart | FilePart | ThoughtPart


class TaskStatus(msgspec.Struct, rename="camel"):
    """Status snapshot of an A2A task."""

    state: TaskState
    message: str | None = None
    timestamp: str | None = None


class Artifact(msgspec.Struct, rename="camel"):
    """Output artifact generated during task execution."""

    name: str | None = None
    artifact_id: str | None = None
    description: str | None = None
    mime_type: str | None = None
    parts: list[Part] = []
    uri: str | None = None
    data: Any | None = None
    index: int | None = None
    append: bool = False
    last_chunk: bool = False
    metadata: dict[str, Any] | None = None


class Message(msgspec.Struct, rename="camel"):
    """A message exchanged between agents or users."""

    role: Literal["user", "agent", "system"]
    parts: list[Part]
    message_id: str | None = None
    task_id: str | None = None
    created_at: str | None = None
    metadata: dict[str, Any] | None = None


class AgentSkill(msgspec.Struct, rename="camel"):
    """Skill capability declared by an agent."""

    id: str
    name: str
    description: str | None = None
    input_schema: dict[str, Any] = {}
    output_schema: dict[str, Any] | None = None
    tags: list[str] = []
    examples: list[str] = []


class AgentCapabilities(msgspec.Struct, rename="camel"):
    """Agent runtime capabilities advertised in the Agent Card."""

    streaming: bool = True
    push_notifications: bool = False
    stateful: bool = True


class AgentProvider(msgspec.Struct, rename="camel"):
    """Identity information for the organization providing the agent."""

    name: str
    url: str | None = None
    organization: str | None = None


class SecurityScheme(msgspec.Struct, rename="camel"):
    """Security scheme specification per OpenAPI 3.1 / A2A standards."""

    type: str
    description: str | None = None
    name: str | None = None
    in_: str | None = None
    scheme: str | None = None
    bearer_format: str | None = None


SecurityRequirement = dict[str, list[str]]


class AgentCard(msgspec.Struct, rename="camel"):
    """Standard Agent Card metadata published at /.well-known/agent-card.json."""

    name: str
    description: str | None = None
    version: str = "1.0.0"
    protocol_version: str = "1.0"
    url: str = ""
    capabilities: AgentCapabilities = msgspec.field(default_factory=AgentCapabilities)
    default_input_modes: list[str] = msgspec.field(default_factory=lambda: ["text", "data"])
    default_output_modes: list[str] = msgspec.field(default_factory=lambda: ["text", "data"])
    skills: list[AgentSkill] = msgspec.field(default_factory=list)
    security_schemes: dict[str, SecurityScheme] = msgspec.field(default_factory=dict)
    security: list[SecurityRequirement] = msgspec.field(default_factory=list)
    provider: AgentProvider | None = None
    documentation_url: str | None = None


class Task(msgspec.Struct, rename="camel"):
    """A2A task container with execution state, artifacts, and history."""

    id: str
    status: TaskStatus = msgspec.field(default_factory=lambda: TaskStatus(state="submitted"))
    session_id: str | None = None
    artifacts: list[Artifact] = msgspec.field(default_factory=list)
    history: list[Message] = msgspec.field(default_factory=list)

    def is_terminal(self) -> bool:
        """Return True if the task is in a terminal state."""
        return self.status.state in TERMINAL_A2A_STATES


class TaskStatusUpdateEvent(msgspec.Struct, rename="camel"):
    """Real-time SSE event for task status transitions or thought progress."""

    task_id: str
    status: TaskStatus
    final: bool = False
    metadata: dict[str, Any] | None = None


class TaskArtifactUpdateEvent(msgspec.Struct, rename="camel"):
    """Real-time SSE event for streaming output artifacts."""

    task_id: str
    artifact: Artifact
    final: bool = False
    metadata: dict[str, Any] | None = None
