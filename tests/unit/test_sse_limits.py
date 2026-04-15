"""Tests for SSEManager resource caps (max_streams, idle pruning)."""

import time

import pytest

from litestar_mcp.sse import SSEManager, StreamLimitExceeded


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
async def test_last_activity_bumps_on_publish() -> None:
    manager = SSEManager(max_streams=10, max_idle_seconds=3600.0)
    stream_id, gen = await manager.open_stream(session_id="s1")
    await gen.__anext__()  # prime
    state = manager._streams[stream_id]
    before = state.last_activity
    # Force a tiny gap
    time.sleep(0.01)
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
