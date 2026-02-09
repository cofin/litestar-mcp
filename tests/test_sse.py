"""Tests for MCP SSE transport."""

import pytest
import asyncio
import json
from litestar_mcp.sse import SSEManager

@pytest.mark.asyncio
async def test_sse_manager_queuing() -> None:
    manager = SSEManager()
    client_id = "test_client"
    
    # Send a message to a client
    message = {"event": "test", "data": "hello"}
    await manager.enqueue_message(client_id, message)
    
    # Consume the message
    stream = manager.subscribe(client_id)
    msg = await stream.__anext__()
    
    assert json.loads(msg.data) == message

@pytest.mark.asyncio
async def test_sse_manager_multiple_clients() -> None:
    manager = SSEManager()
    
    m1 = {"data": "msg1"}
    m2 = {"data": "msg2"}
    await manager.enqueue_message("client1", m1)
    await manager.enqueue_message("client2", m2)
    
    s1 = manager.subscribe("client1")
    s2 = manager.subscribe("client2")
    
    r1 = await s1.__anext__()
    r2 = await s2.__anext__()
    
    assert json.loads(r1.data) == m1
    assert json.loads(r2.data) == m2