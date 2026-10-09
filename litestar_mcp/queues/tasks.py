"""Queue task definitions and turn manager for optional litestar-queues integration."""

import importlib.util
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

import msgspec

from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.exceptions import MissingDependencyError

if TYPE_CHECKING:
    from litestar_mcp.agent.runtime import AgentRuntime

_missing_queues_exc: ImportError | None = None

try:
    import litestar_queues

    task: Any = getattr(litestar_queues, "task", None)
except ImportError as exc:
    _missing_queues_exc = exc
    litestar_queues = None  # type: ignore[assignment]
    task = None

QUEUES_INSTALLED: bool = importlib.util.find_spec("litestar_queues") is not None


def get_queues_module() -> Any:
    """Lazily import litestar_queues or raise informative error."""
    if _missing_queues_exc is not None or litestar_queues is None:
        raise MissingDependencyError(
            package="litestar-queues",
            extra="queues",
        ) from _missing_queues_exc
    return litestar_queues


class PrincipalSnapshot(msgspec.Struct, kw_only=True):
    """Snapshot of security principal attributes for queued execution."""

    user_id: str | None = None
    tenant: str | None = None
    roles: list[str] = msgspec.field(default_factory=list)
    scopes: list[str] = msgspec.field(default_factory=list)


class QueuedTurn(msgspec.Struct, kw_only=True):
    """Serializable envelope for background turn execution."""

    session_id: str
    turn_id: str
    user_message: str | dict[str, Any]
    agent_name: str = "root"
    dynamic_context: str | None = None
    principal: PrincipalSnapshot | None = None


class AgentRegistry:
    """Registry mapping agent names to AgentRuntime instances for worker tasks."""

    def __init__(self, runtimes: dict[str, "AgentRuntime"] | None = None) -> None:
        """Initialize the AgentRegistry with an optional mapping of runtimes."""
        self._runtimes: dict[str, AgentRuntime] = dict(runtimes or {})

    def register(self, name: str, runtime: "AgentRuntime") -> None:
        """Register an AgentRuntime under an agent name."""
        self._runtimes[name] = runtime

    def get(self, name: str) -> "AgentRuntime | None":
        """Get an AgentRuntime by agent name."""
        return self._runtimes.get(name)


class _WorkerState:
    """Holder for worker runtime dependencies."""

    registry: AgentRegistry = AgentRegistry()
    channels: Any = None


def set_agent_registry(registry: AgentRegistry) -> None:
    """Set the global AgentRegistry for queued turn workers."""
    _WorkerState.registry = registry


def get_agent_registry() -> AgentRegistry:
    """Get the global AgentRegistry for queued turn workers."""
    return _WorkerState.registry


def set_channels_service(channels: Any) -> None:
    """Set the global channels service for queued turn workers."""
    _WorkerState.channels = channels


def get_channels_service() -> Any:
    """Get the global channels service for queued turn workers."""
    return _WorkerState.channels


async def _run_agent_turn_body(envelope: dict[str, Any]) -> dict[str, Any]:
    """Execute the core logic of an agent turn."""
    from litestar_mcp.agent.runtime import TurnRequest
    from litestar_mcp.queues.bridge import emit_agent_frame

    queued_turn = msgspec.convert(envelope, QueuedTurn, strict=False)
    registry = get_agent_registry()
    runtime = registry.get(queued_turn.agent_name)
    if runtime is None:
        msg = f"No AgentRuntime registered for agent {queued_turn.agent_name!r}"
        raise RuntimeError(msg)

    user_obj = None
    tenant_id = None
    if queued_turn.principal is not None:
        if queued_turn.principal.user_id:
            user_obj = {"id": queued_turn.principal.user_id}
        tenant_id = queued_turn.principal.tenant

    tool_context = ToolContext(
        user=user_obj,
        tenant_id=tenant_id,
        session_id=queued_turn.session_id,
        turn_id=queued_turn.turn_id,
    )
    user_msg = queued_turn.user_message if isinstance(queued_turn.user_message, str) else str(queued_turn.user_message)
    turn_request = TurnRequest(
        user_message=user_msg,
        session_id=queued_turn.session_id,
        turn_id=queued_turn.turn_id,
        context=tool_context,
    )

    channels = get_channels_service()
    seq = 1
    async for frame in runtime.stream(turn_request):
        if channels is not None:
            await emit_agent_frame(channels, queued_turn.turn_id, frame, seq=seq)
        seq += 1

    return {
        "status": "completed",
        "session_id": queued_turn.session_id,
        "turn_id": queued_turn.turn_id,
        "agent_name": queued_turn.agent_name,
    }


if task is not None:
    run_agent_turn = task(
        "litestar_mcp.agent.run_turn",
        queue="agent",
        retries=0,
        timeout=1800,
    )(_run_agent_turn_body)
else:
    run_agent_turn = _run_agent_turn_body

run_agent_turn_task = run_agent_turn


class QueuedTurnManager:
    """Manages queuing agent turns and publishing realtime frames to Channels."""

    def __init__(self, queue_service: Any = None, channels: Any = None) -> None:
        """Initialize QueuedTurnManager with queue_service and channels dependencies."""
        self.queue_service = queue_service
        self.channels = channels

    async def enqueue_turn(
        self,
        session_id: str,
        user_message: str | dict[str, Any],
        *,
        turn_id: str | None = None,
        agent_name: str = "root",
        dynamic_context: str | None = None,
        principal: PrincipalSnapshot | None = None,
    ) -> str:
        """Enqueue an agent turn for background execution."""
        tid = turn_id or uuid4().hex
        envelope = QueuedTurn(
            session_id=session_id,
            turn_id=tid,
            user_message=user_message,
            agent_name=agent_name,
            dynamic_context=dynamic_context,
            principal=principal,
        )
        envelope_dict = msgspec.to_builtins(envelope)

        if self.queue_service is not None:
            await self.queue_service.enqueue(
                run_agent_turn,
                envelope=envelope_dict,
                key=f"agent-turn:{session_id}:{tid}",
                queue="agent",
            )
        else:
            enqueue_fn = getattr(run_agent_turn, "enqueue", None)
            if callable(enqueue_fn):
                await cast("Any", enqueue_fn)(
                    envelope=envelope_dict,
                    key=f"agent-turn:{session_id}:{tid}",
                    queue="agent",
                )
        return tid


__all__ = (
    "QUEUES_INSTALLED",
    "AgentRegistry",
    "PrincipalSnapshot",
    "QueuedTurn",
    "QueuedTurnManager",
    "get_agent_registry",
    "get_channels_service",
    "get_queues_module",
    "run_agent_turn",
    "run_agent_turn_task",
    "set_agent_registry",
    "set_channels_service",
)
