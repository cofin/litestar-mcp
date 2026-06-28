"""Tests for MCP SSE transport."""

import asyncio
import json
import time

import pytest
from litestar.serialization import decode_json

from litestar_mcp.sse import SSEManager, StreamLimitExceeded


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


@pytest.mark.asyncio
async def test_max_streams_raises() -> None:
    manager = SSEManager(max_streams=2, max_idle_seconds=3600.0)
    sid1, _ = await manager.open_stream(session_id="s1")
    sid2, _ = await manager.open_stream(session_id="s2")
    with pytest.raises(StreamLimitExceeded):
        await manager.open_stream(session_id="s3")
    # Cleanup
    manager.disconnect(sid1)
    manager.disconnect(sid2)


@pytest.mark.asyncio
async def test_last_activity_bumps_on_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = SSEManager(max_streams=10, max_idle_seconds=3600.0)
    stream_id, gen = await manager.open_stream(session_id="s1")
    await gen.__anext__()  # prime
    state = manager._streams[stream_id]
    before = state.last_activity
    # Advance the clock read by publish/consumer
    monkeypatch.setattr("litestar_mcp.sse.time.monotonic", lambda: before + 5.0)
    await manager.publish({"ping": True}, session_id="s1")
    await gen.__anext__()
    assert state.last_activity > before
    manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_idle_pruning_admits_new_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = SSEManager(max_streams=1, max_idle_seconds=0.05)
    sid1, gen1 = await manager.open_stream(session_id="s1")
    await gen1.__anext__()

    # Simulate time moving forward past the idle window
    now = time.monotonic() + 10.0
    monkeypatch.setattr("litestar_mcp.sse.time.monotonic", lambda: now)

    # With only 1 slot and idle over cutoff, the old stream should be pruned
    sid2, _ = await manager.open_stream(session_id="s2")
    assert sid2 != sid1
    assert sid1 not in manager._streams
    manager.disconnect(sid2)


@pytest.mark.asyncio
async def test_replay_from_returns_history_slice() -> None:
    manager = SSEManager()
    stream_id, gen = await manager.open_stream(session_id="s1")
    # prime
    await gen.__anext__()
    await manager.publish({"n": 1}, session_id="s1")
    await manager.publish({"n": 2}, session_id="s1")
    # drain
    m1 = await gen.__anext__()
    await gen.__anext__()
    remaining = await manager.replay_from(stream_id, last_event_id=m1.id or f"{stream_id}:0")
    assert len(remaining) >= 1
    manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_close_session_streams_removes_all() -> None:
    manager = SSEManager()
    sid1, _ = await manager.open_stream(session_id="sA")
    sid2, _ = await manager.open_stream(session_id="sA")
    closed = manager.close_session_streams("sA")
    assert set(closed) == {sid1, sid2}
    assert sid1 not in manager._streams
    assert sid2 not in manager._streams
    assert "sA" not in manager._session_streams


@pytest.mark.asyncio
async def test_published_payload_is_valid_json_decodable_by_litestar() -> None:
    """An enqueued message should be encoded as UTF-8 JSON that Litestar can decode."""
    manager = SSEManager()
    stream_id, gen = await manager.open_stream(session_id="session-a")
    primer = await gen.__anext__()
    assert primer.data == ""
    assert primer.id == f"{stream_id}:0"

    payload = {"jsonrpc": "2.0", "method": "ping", "params": {"n": 1, "ok": True}}
    await manager.publish(payload, session_id="session-a")
    message = await gen.__anext__()

    assert isinstance(message.data, str)
    decoded = decode_json(message.data.encode("utf-8"))
    assert decoded == payload
    manager.disconnect(stream_id)
