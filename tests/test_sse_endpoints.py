"""Tests for SSEManager (unit tests only — SSE transport endpoints removed in v0.3.0)."""

import pytest

from litestar_mcp.sse import SSEManager, SSEMessage


@pytest.mark.anyio
async def test_sse_manager_broadcast() -> None:
    """SSEManager can still broadcast and subscribe (used by notifications)."""
    mgr = SSEManager()
    mgr.register_client("c1")
    await mgr.broadcast({"test": "msg"})
    msg = await mgr._queues["c1"].get()
    assert isinstance(msg, SSEMessage)
    assert "test" in msg.data


@pytest.mark.anyio
async def test_sse_manager_disconnect() -> None:
    mgr = SSEManager()
    mgr.register_client("c1")
    mgr.disconnect("c1")
    assert "c1" not in mgr._queues
