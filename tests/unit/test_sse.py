"""Tests for MCP SSE transport."""

import asyncio
import json
import time

import pytest

from litestar_mcp.sse import SSEManager


@pytest.mark.asyncio
async def test_sse_manager_enqueue_per_session() -> None:
    manager = SSEManager()
    stream_id, stream = await manager.open_stream(session_id="session-a")
    await stream.__anext__()  # Prime event

    message = {"event": "test", "data": "hello"}
    await manager.publish(message, session_id="session-a")

    msg = await stream.__anext__()
    assert json.loads(msg.data) == message
    # Cleanup generator
    manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_sse_manager_multiple_sessions() -> None:
    manager = SSEManager()
    s1_id, s1 = await manager.open_stream(session_id="sess-1")
    s2_id, s2 = await manager.open_stream(session_id="sess-2")
    await s1.__anext__()  # Prime
    await s2.__anext__()  # Prime

    m1 = {"data": "msg1"}
    m2 = {"data": "msg2"}
    await manager.publish(m1, session_id="sess-1")
    await manager.publish(m2, session_id="sess-2")

    r1 = await s1.__anext__()
    r2 = await s2.__anext__()

    assert json.loads(r1.data) == m1
    assert json.loads(r2.data) == m2
    manager.disconnect(s1_id)
    manager.disconnect(s2_id)


@pytest.mark.asyncio
async def test_sse_manager_broadcast_to_all_sessions() -> None:
    manager = SSEManager()
    s1_id, s1 = await manager.open_stream(session_id="sess-1")
    s2_id, s2 = await manager.open_stream(session_id="sess-2")
    await s1.__anext__()
    await s2.__anext__()

    message = {"method": "notifications/resources/updated", "params": {"uri": "test://resource"}}
    await manager.publish(message)

    r1 = await asyncio.wait_for(s1.__anext__(), timeout=1.0)
    r2 = await asyncio.wait_for(s2.__anext__(), timeout=1.0)

    assert json.loads(r1.data) == message
    assert json.loads(r2.data) == message
    manager.disconnect(s1_id)
    manager.disconnect(s2_id)


@pytest.mark.asyncio
async def test_sse_manager_enqueue_direct() -> None:
    """Verify that enqueue targets a specific stream ID, skipping others in the same session."""
    manager = SSEManager()
    s1_id, s1 = await manager.open_stream(session_id="session-a")
    s2_id, s2 = await manager.open_stream(session_id="session-a")
    await s1.__anext__()  # Prime
    await s2.__anext__()  # Prime

    message = {"data": "direct-to-s1"}
    await manager.enqueue(s1_id, message)

    # s1 should receive it
    r1 = await asyncio.wait_for(s1.__anext__(), timeout=0.1)
    assert json.loads(r1.data) == message

    # s2 should NOT receive it
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(s2.__anext__(), timeout=0.1)

    manager.disconnect(s1_id)
    manager.disconnect(s2_id)


@pytest.mark.asyncio
async def test_sse_manager_prune_throttle(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = SSEManager(max_idle_seconds=10.0)

    current_time = 100.0
    monkeypatch.setattr(time, "monotonic", lambda: current_time)

    # First stream open triggers pruning unconditionally, setting _last_prune_time
    s1_id, _ = await manager.open_stream(session_id="sess-1")
    assert manager._last_prune_time == 100.0

    # Opening another stream within the 30-second throttle window (e.g. 15s later) skips pruning
    current_time = 115.0
    s2_id, _ = await manager.open_stream(session_id="sess-2")
    assert manager._last_prune_time == 100.0  # Unchanged

    # Opening a stream after the 30-second window (e.g. 35s later) executes pruning
    current_time = 135.0
    s3_id, _ = await manager.open_stream(session_id="sess-3")
    assert manager._last_prune_time == 135.0  # Updated

    # Cleanup
    manager.disconnect(s1_id)
    manager.disconnect(s2_id)
    manager.disconnect(s3_id)
