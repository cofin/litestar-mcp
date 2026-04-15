"""Tests for MCP SSE transport."""

import asyncio
import json

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
