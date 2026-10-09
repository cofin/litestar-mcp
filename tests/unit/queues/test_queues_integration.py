from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from litestar import Litestar
from litestar.di import Provide
from litestar.testing import AsyncTestClient

from litestar_mcp.agent.streaming import AgentStreamFrame
from litestar_mcp.queues import (
    QUEUES_INSTALLED,
    QueuedAgentChatController,
    QueuedTurnManager,
    agent_turn_channel,
    emit_agent_frame,
)


@pytest.mark.asyncio
async def test_emit_agent_frame_and_channel_naming() -> None:
    assert isinstance(QUEUES_INSTALLED, bool)
    assert agent_turn_channel("turn-42") == "litestar_mcp:agent:turn-42"

    mock_channels = AsyncMock()
    frame = AgentStreamFrame.delta("Streaming chunk", turn_id="turn-42", seq=1)
    await emit_agent_frame(mock_channels, "turn-42", frame)
    mock_channels.publish.assert_awaited_once()
    call_args = mock_channels.publish.await_args
    assert call_args.kwargs["channels"] == ["litestar_mcp:agent:turn-42"]
    assert "Streaming chunk" in call_args.args[0]


@pytest.mark.asyncio
async def test_queued_agent_chat_controller_202_and_last_event_id_replay() -> None:
    mock_queue_service = AsyncMock()
    mock_channels = AsyncMock()
    manager = QueuedTurnManager(queue_service=mock_queue_service, channels=mock_channels)

    await manager.publish_frame(
        agent_turn_channel("turn-replay"),
        AgentStreamFrame.delta("First chunk", turn_id="turn-replay", seq=1),
    )
    await manager.publish_frame(
        agent_turn_channel("turn-replay"),
        AgentStreamFrame.delta("Second chunk", turn_id="turn-replay", seq=2),
    )
    await manager.publish_frame(
        agent_turn_channel("turn-replay"),
        AgentStreamFrame.complete("Final summary", turn_id="turn-replay", seq=3),
    )

    app = Litestar(
        route_handlers=[QueuedAgentChatController],
        dependencies={"turn_manager": Provide(lambda: manager, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res_202 = await client.post(
            "/agent/turns",
            json={"session_id": "s-q", "turn_id": "turn-new", "message": "Run background"},
        )
        assert res_202.status_code == 202
        body = res_202.json()
        assert body["status"] == "accepted"
        assert body["turn_id"] == "turn-new"
        assert body["stream_url"] == "/agent/turns/turn-new/stream"
        mock_queue_service.enqueue.assert_awaited_once()

        stream_res = await client.get(
            "/agent/turns/turn-replay/stream",
            headers={"Last-Event-ID": "1"},
        )
        assert stream_res.status_code == 200
        text = stream_res.text
        assert "First chunk" not in text
        assert "Second chunk" in text
        assert "event: complete" in text
