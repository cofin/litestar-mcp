from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from litestar_mcp.core.exceptions import MissingDependencyError
from litestar_mcp.queues.tasks import QueuedTurnManager, get_queues_module


def test_get_queues_module_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "litestar_queues", None)
    with pytest.raises(MissingDependencyError) as exc_info:
        get_queues_module()
    assert exc_info.value.package == "litestar-queues"
    assert exc_info.value.extra == "queues"


@pytest.mark.anyio
async def test_queued_turn_manager_enqueue_and_publish() -> None:
    mock_queue_service = AsyncMock()
    mock_channels = AsyncMock()

    manager = QueuedTurnManager(
        queue_service=mock_queue_service,
        channels=mock_channels,
    )

    turn_id = await manager.enqueue_turn(
        session_id="session-1",
        user_message="Hello queues!",
        turn_id="turn-1",
        agent_name="barista",
    )
    assert turn_id == "turn-1"
    mock_queue_service.enqueue.assert_awaited_once()

    frame_payload = {"turn_id": "turn-1", "delta": "world"}
    await manager.publish_frame("agent-channel", frame_payload)
    mock_channels.publish.assert_awaited_once()
