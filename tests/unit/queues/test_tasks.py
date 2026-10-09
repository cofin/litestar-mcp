"""Unit tests for queued agent tasks and AgentRegistry."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from litestar_mcp.agent.runtime import AgentRuntime, TurnRequest
from litestar_mcp.agent.streaming import AgentStreamFrame
from litestar_mcp.core.exceptions import MissingDependencyError
from litestar_mcp.queues.tasks import (
    AgentRegistry,
    PrincipalSnapshot,
    QueuedTurnManager,
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
