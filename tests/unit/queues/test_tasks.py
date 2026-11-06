"""Unit tests for queued agent tasks and AgentRegistry."""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("litestar_queues")

from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.streaming import AgentStreamFrame
from litestar_mcp.core.exceptions import MissingDependencyError
from litestar_mcp.queues.tasks import (
    AgentRegistry,
    PrincipalSnapshot,
    QueuedTurnManager,
    _run_agent_turn_body,
    get_channels_service,
    get_queues_module,
    run_agent_turn,
    set_agent_registry,
    set_channels_service,
)


def test_get_queues_module_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """get_queues_module raises MissingDependencyError when litestar_queues is absent."""
    from litestar_mcp.queues import tasks as tasks_mod

    monkeypatch.setattr(tasks_mod, "litestar_queues", None)
    monkeypatch.setattr(tasks_mod, "_missing_queues_exc", ImportError("No module named litestar_queues"))
    with pytest.raises(MissingDependencyError) as exc_info:
        get_queues_module()
    assert exc_info.value.package == "litestar-queues"
    assert exc_info.value.extra == "queues"


@pytest.mark.anyio
async def test_queued_turn_task_runs_inline() -> None:
    """Awaiting run_agent_turn runs the task body inline without queue hops."""
    mock_runtime = AsyncMock(spec=AgentRuntime)

    async def _mock_stream(req: TurnRequest) -> AsyncIterator[AgentStreamFrame]:
        """Yield mock stream frames."""
        yield AgentStreamFrame(event_type="delta", text="Working...", turn_id=req.turn_id or "t1", seq=1)
        yield AgentStreamFrame(event_type="complete", text="Done!", turn_id=req.turn_id or "t1", seq=2)

    mock_runtime.stream = _mock_stream

    registry = AgentRegistry()
    registry.register("worker", mock_runtime)
    set_agent_registry(registry)

    mock_channels = AsyncMock()
    set_channels_service(mock_channels)

    envelope = {
        "session_id": "sess-1",
        "turn_id": "turn-1",
        "user_message": "Execute task",
        "agent_name": "worker",
        "principal": {"user_id": "user-42", "tenant": "acme"},
    }

    result = await run_agent_turn(envelope)
    assert result["status"] == "completed"
    assert result["session_id"] == "sess-1"
    assert result["turn_id"] == "turn-1"
    assert result["agent_name"] == "worker"
    assert mock_channels.publish.call_count == 2


@pytest.mark.anyio
async def test_queued_turn_manager_enqueue() -> None:
    """QueuedTurnManager enqueues serialized envelope to queue service."""
    mock_queue_service = AsyncMock()
    manager = QueuedTurnManager(queue_service=mock_queue_service)

    turn_id = await manager.enqueue_turn(
        session_id="session-1",
        user_message="Hello queues!",
        turn_id="turn-abc",
        agent_name="support",
        principal=PrincipalSnapshot(user_id="u1"),
    )
    assert turn_id == "turn-abc"
    mock_queue_service.enqueue.assert_awaited_once()
    call_kwargs = mock_queue_service.enqueue.await_args.kwargs
    assert call_kwargs["queue"] == "agent"
    assert call_kwargs["key"] == "agent-turn:session-1:turn-abc"
    assert call_kwargs["envelope"]["session_id"] == "session-1"
    assert call_kwargs["envelope"]["turn_id"] == "turn-abc"


@pytest.mark.anyio
@pytest.mark.parametrize(
    "dynamic_context",
    [
        None,
        "extra system rules",
        {"region": "us-central1"},
    ],
)
async def test_run_agent_turn_body_forwards_dynamic_context_and_principal_roles_scopes(
    dynamic_context: dict[str, Any] | str | None,
) -> None:
    """_run_agent_turn_body forwards dynamic_context to TurnRequest and roles/scopes to ToolContext."""
    captured_requests: list[TurnRequest] = []
    mock_runtime = AsyncMock(spec=AgentRuntime)

    async def _mock_stream(req: TurnRequest) -> AsyncIterator[AgentStreamFrame]:
        """Record TurnRequest and yield completion frame."""
        captured_requests.append(req)
        yield AgentStreamFrame(event_type="complete", text="ok", turn_id=req.turn_id or "t1", seq=1)

    mock_runtime.stream = _mock_stream
    registry = AgentRegistry({"worker": mock_runtime})
    set_agent_registry(registry)
    set_channels_service(None)

    envelope: dict[str, Any] = {
        "session_id": "sess-ctx",
        "turn_id": "turn-ctx",
        "user_message": "Check context",
        "agent_name": "worker",
        "dynamic_context": dynamic_context,
        "principal": {
            "user_id": "user-7",
            "tenant": "acme",
            "roles": ["admin", "ops"],
            "scopes": ["read", "write"],
        },
    }

    await _run_agent_turn_body(envelope)
    assert len(captured_requests) == 1
    turn_req = captured_requests[0]
    assert turn_req.dynamic_context == dynamic_context
    if isinstance(dynamic_context, dict):
        assert turn_req.dynamic_context is not dynamic_context
    assert turn_req.context is not None
    assert turn_req.context.user == {"id": "user-7"}
    assert turn_req.context.tenant_id == "acme"
    assert turn_req.context.roles == ("admin", "ops")
    assert turn_req.context.scopes == ("read", "write")


def test_queued_turn_manager_binds_channels_when_unset() -> None:
    """QueuedTurnManager.__init__ sets global channels service only when currently unset."""
    set_channels_service(None)
    ch1 = object()
    ch2 = object()

    manager1 = QueuedTurnManager(channels=ch1)
    assert manager1.channels is ch1
    assert get_channels_service() is ch1

    manager2 = QueuedTurnManager(channels=ch2)
    assert manager2.channels is ch2
    assert get_channels_service() is ch1
    set_channels_service(None)
