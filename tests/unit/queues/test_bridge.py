"""Unit tests for queued agent chat controller and Litestar Channels streaming bridge."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("litestar_queues")

from litestar import Litestar, Request
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


@pytest.mark.anyio
async def test_enqueue_turn_extracts_dict_principal_and_dynamic_context() -> None:
    """QueuedAgentChatController.enqueue_turn uses ToolContext.from_connection for dict principals."""
    mock_queue_service = AsyncMock()
    manager = QueuedTurnManager(queue_service=mock_queue_service)

    async def inject_principal(request: Request[Any, Any, Any]) -> None:
        request.scope["user"] = {"id": "u-1", "roles": ["admin"], "scopes": ["read"]}
        request.state.tenant_id = "acme"

    app = Litestar(
        route_handlers=[QueuedAgentChatController],
        dependencies={"turn_manager": Provide(lambda: manager, sync_to_thread=False)},
        before_request=inject_principal,
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.post(
            "/agent/turns",
            json={
                "session_id": "sess-dict",
                "turn_id": "turn-dict",
                "user_message": "Run with dict user",
                "dynamic_context": {"region": "us-central1"},
            },
        )
        assert res.status_code == 202
        mock_queue_service.enqueue.assert_awaited_once()
        envelope = mock_queue_service.enqueue.await_args.kwargs["envelope"]
        assert envelope["dynamic_context"] == {"region": "us-central1"}
        assert envelope["principal"] == {
            "user_id": "u-1",
            "tenant": "acme",
            "roles": ["admin"],
            "scopes": ["read"],
        }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("last_event_id", "expected_present", "expected_absent"),
    [
        ("1", ("chunk 2", "done"), ("chunk 1",)),
        ("2", ("done",), ("chunk 1", "chunk 2")),
        ("invalid", ("chunk 1", "chunk 2", "done"), ()),
        ("-5", ("chunk 1", "chunk 2", "done"), ()),
    ],
)
async def test_stream_turn_resumes_after_last_event_id(
    last_event_id: str,
    expected_present: tuple[str, ...],
    expected_absent: tuple[str, ...],
) -> None:
    """QueuedAgentChatController.stream_turn skips frames up to Last-Event-ID."""

    class FakeSubscriber:
        async def iter_events(self) -> AsyncIterator[str]:
            f1 = AgentStreamFrame(event_type="delta", text="chunk 1", turn_id="t1", seq=1)
            f2 = AgentStreamFrame(event_type="delta", text="chunk 2", turn_id="t1", seq=2)
            f3 = AgentStreamFrame(event_type="complete", text="done", turn_id="t1", seq=3)
            yield to_json(f1, as_bytes=False)
            yield to_json(f2, as_bytes=False)
            yield to_json(f3, as_bytes=False)

    class FakeChannels:
        @asynccontextmanager
        async def start_subscription(
            self,
            channels: list[str],
            history: int = 1000,
        ) -> AsyncIterator[FakeSubscriber]:
            yield FakeSubscriber()

    app = Litestar(
        route_handlers=[QueuedAgentChatController],
        dependencies={
            "turn_manager": Provide(QueuedTurnManager, sync_to_thread=False),
            "channels": Provide(FakeChannels, sync_to_thread=False),
        },
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.get("/agent/turns/t1/stream", headers={"Last-Event-ID": last_event_id})
        assert res.status_code == 200
        text = res.text
        assert all(token in text for token in expected_present)
        assert all(token not in text for token in expected_absent)
