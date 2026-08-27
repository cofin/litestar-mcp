"""Unit tests for generic SSE stream subscription management."""

import pytest

from litestar_mcp.core import (
    BaseSubscriptionManager,
    StreamLimitExceeded,
    StreamSubscription,
)


@pytest.mark.asyncio
async def test_sse_subscription_lifecycle() -> None:
    """Verify subscription stream opening, initial messages, and clean disconnects."""
    manager = BaseSubscriptionManager(max_streams=2)
    stream_id, stream_gen = await manager.open_stream("sub-1", initial_message={"event": "connected"})
    assert isinstance(stream_id, str)
    assert stream_id in manager._streams
    sub = manager._streams[stream_id]
    assert isinstance(sub, StreamSubscription)
    assert sub.subscription_id == "sub-1"

    first_msg = await anext(stream_gen)
    assert first_msg == {"event": "connected"}

    await manager.disconnect(stream_id)
    assert stream_id not in manager._streams


@pytest.mark.asyncio
async def test_sse_stream_limit_enforcement() -> None:
    """Verify exceeding max_streams raises StreamLimitExceeded."""
    manager = BaseSubscriptionManager(max_streams=1)
    await manager.open_stream("sub-1")

    with pytest.raises(StreamLimitExceeded):
        await manager.open_stream("sub-2")

    await manager.close_all()
    assert len(manager._streams) == 0
