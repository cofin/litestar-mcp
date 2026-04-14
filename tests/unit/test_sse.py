"""Tests for MCP SSE transport."""

import asyncio
import json
from collections.abc import AsyncGenerator

import pytest

from litestar_mcp.sse import SSEManager, SSEMessage


@pytest.mark.asyncio
async def test_sse_manager_queuing() -> None:
    manager = SSEManager()
    client_id = "test_client"
    stream = await manager.subscribe(client_id)
    await stream.__anext__()  # Prime event

    # Send a message to a client
    message = {"event": "test", "data": "hello"}
    await manager.enqueue_message(client_id, message)

    msg = await stream.__anext__()

    assert json.loads(msg.data) == message


@pytest.mark.asyncio
async def test_sse_manager_multiple_clients() -> None:
    manager = SSEManager()
    s1 = await manager.subscribe("client1")
    s2 = await manager.subscribe("client2")
    await s1.__anext__()  # Prime event
    await s2.__anext__()  # Prime event

    m1 = {"data": "msg1"}
    m2 = {"data": "msg2"}
    await manager.enqueue_message("client1", m1)
    await manager.enqueue_message("client2", m2)

    r1 = await s1.__anext__()
    r2 = await s2.__anext__()

    assert json.loads(r1.data) == m1
    assert json.loads(r2.data) == m2


@pytest.mark.asyncio
async def test_sse_manager_broadcast() -> None:
    manager = SSEManager()
    s1 = await manager.subscribe("client1")
    s2 = await manager.subscribe("client2")
    await s1.__anext__()  # Prime event
    await s2.__anext__()  # Prime event

    message = {"method": "notifications/resources/updated", "params": {"uri": "test://resource"}}

    # Broadcast after ensuring queues exist
    await manager.broadcast(message)

    async def get_msg(stream: AsyncGenerator[SSEMessage, None]) -> SSEMessage:
        return await stream.__anext__()

    r1 = await asyncio.wait_for(get_msg(s1), timeout=1.0)
    r2 = await asyncio.wait_for(get_msg(s2), timeout=1.0)

    assert json.loads(r1.data) == message
    assert json.loads(r2.data) == message
