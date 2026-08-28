"""Tests for MCP 2026 subscription filtering and lifecycle."""

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest

from litestar_mcp.sse import StreamLimitExceeded, SubscriptionManager

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator


@pytest.mark.asyncio
async def test_acknowledgement_is_first_and_uses_request_id() -> None:
    manager = SubscriptionManager()
    stream_id, stream = await manager.open(
        "listen-1",
        {"toolsListChanged": True, "resourceSubscriptions": ["file:///a"]},
    )

    first = await stream.__anext__()

    assert first["method"] == "notifications/subscriptions/acknowledged"
    assert first["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "listen-1"
    assert first["params"]["notifications"] == {
        "toolsListChanged": True,
        "resourceSubscriptions": ["file:///a"],
    }
    await manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_notifications_are_filtered_and_tagged() -> None:
    manager = SubscriptionManager()
    tools_id, tools = await manager.open("tools", {"toolsListChanged": True})
    resource_id, resources = await manager.open(
        "resource",
        {"resourceSubscriptions": ["file:///selected"]},
    )
    await tools.__anext__()
    await resources.__anext__()

    await manager.publish("notifications/tools/list_changed", {})
    await manager.publish("notifications/resources/updated", {"uri": "file:///other"})
    await manager.publish("notifications/resources/updated", {"uri": "file:///selected"})

    tool_notification = await asyncio.wait_for(tools.__anext__(), 0.1)
    resource_notification = await asyncio.wait_for(resources.__anext__(), 0.1)

    assert tool_notification["method"] == "notifications/tools/list_changed"
    assert tool_notification["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "tools"
    assert resource_notification["params"]["uri"] == "file:///selected"
    assert resource_notification["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "resource"
    await manager.disconnect(tools_id)
    await manager.disconnect(resource_id)


@pytest.mark.asyncio
async def test_stream_limit_and_graceful_shutdown() -> None:
    manager = SubscriptionManager(max_streams=1)
    stream_id, stream = await manager.open(1, {"promptsListChanged": True})
    await stream.__anext__()

    with pytest.raises(StreamLimitExceeded):
        await manager.open(2, {})

    await manager.close_all()
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()
    await manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_channels_backend_fans_notifications_across_manager_instances() -> None:
    class Subscriber:
        def __init__(self) -> None:
            self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def iter_events(self) -> "AsyncGenerator[bytes, None]":
            while (event := await self.queue.get()) is not None:
                yield event

    class Channels:
        def __init__(self) -> None:
            self.subscribers: list[Subscriber] = []

        @asynccontextmanager
        async def start_subscription(self, _channel: str) -> "AsyncIterator[Subscriber]":
            subscriber = Subscriber()
            self.subscribers.append(subscriber)
            try:
                yield subscriber
            finally:
                await subscriber.queue.put(None)

        def publish(self, data: dict[str, Any], *, channels: str) -> None:
            from litestar.serialization import encode_json

            assert channels == "litestar-mcp-subscriptions"
            for subscriber in self.subscribers:
                subscriber.queue.put_nowait(encode_json(data))

    channels = Channels()
    publisher = SubscriptionManager(channels=channels)
    receiver = SubscriptionManager(channels=channels)
    publisher.start()
    receiver.start()
    await asyncio.sleep(0)
    _stream_id, stream = await receiver.open("sub-1", {"toolsListChanged": True})
    await stream.__anext__()

    await publisher.publish("notifications/tools/list_changed", {})
    notification = await asyncio.wait_for(stream.__anext__(), timeout=1)

    assert notification["method"] == "notifications/tools/list_changed"
    await publisher.close_all()
    await receiver.close_all()
