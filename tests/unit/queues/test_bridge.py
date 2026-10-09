"""Unit tests for queued agent chat controller and Litestar Channels streaming bridge."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest
from litestar import Litestar
from litestar.di import Provide
from litestar.testing import AsyncTestClient

from litestar_mcp.agent.streaming import AgentStreamFrame
from litestar_mcp.core.serialization import to_json
from litestar_mcp.queues.bridge import (
    QueuedAgentChatController,
    emit_agent_frame,
)
from litestar_mcp.queues.tasks import QueuedTurnManager


@pytest.mark.anyio
async def test_emit_agent_frame() -> None:
    """emit_agent_frame publishes serialized frame with updated seq to canonical channel."""
    mock_channels = AsyncMock()
    frame = AgentStreamFrame(
        event_type="delta",
        text="Hello world",
        turn_id="turn-99",
        seq=1,
    )
    await emit_agent_frame(mock_channels, "turn-99", frame, seq=42)
    mock_channels.publish.assert_awaited_once()
    args, kwargs = mock_channels.publish.await_args
    assert kwargs["channels"] == ["litestar_mcp:agent:turn-99"]
    assert '"seq":42' in args[0]
    assert "Hello world" in args[0]


@pytest.mark.anyio
async def test_queued_agent_chat_controller_enqueue() -> None:
    """QueuedAgentChatController returns HTTP 202 with stream URL upon enqueuing."""
    mock_queue_service = AsyncMock()
    manager = QueuedTurnManager(queue_service=mock_queue_service)

    app = Litestar(
        route_handlers=[QueuedAgentChatController],
        dependencies={"turn_manager": Provide(lambda: manager, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.post(
            "/agent/turns",
            json={"session_id": "sess-xyz", "turn_id": "turn-xyz", "user_message": "Do work"},
        )
        assert res.status_code == 202
        body = res.json()
        assert body["session_id"] == "sess-xyz"
        assert body["turn_id"] == "turn-xyz"
        assert body["stream_url"] == "/agent/turns/turn-xyz/stream"
        mock_queue_service.enqueue.assert_awaited_once()


@pytest.mark.anyio
async def test_queued_agent_chat_controller_stream() -> None:
    """QueuedAgentChatController streams SSE frames from channels subscription."""

    class FakeSubscriber:
        async def iter_events(self) -> AsyncIterator[str]:
            f1 = AgentStreamFrame(event_type="delta", text="chunk 1", turn_id="t1", seq=1)
            f2 = AgentStreamFrame(event_type="complete", text="done", turn_id="t1", seq=2)
            yield to_json(f1, as_bytes=False)
            yield to_json(f2, as_bytes=False)

    class FakeChannels:
        """Mock channels service providing subscription context manager."""

        @asynccontextmanager
        async def start_subscription(
            self,
            channels: list[str],
            history: int = 1000,
        ) -> AsyncIterator[FakeSubscriber]:
            """Yield fake subscriber instance."""
            yield FakeSubscriber()

    app = Litestar(
        route_handlers=[QueuedAgentChatController],
        dependencies={
            "turn_manager": Provide(QueuedTurnManager, sync_to_thread=False),
            "channels": Provide(FakeChannels, sync_to_thread=False),
        },
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.get("/agent/turns/t1/stream")
        assert res.status_code == 200
        text = res.text
        assert "chunk 1" in text
        assert "done" in text
