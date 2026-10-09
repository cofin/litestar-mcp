"""Unit tests for MCP Tasks extension and MCPTaskStore."""

import pytest

from litestar_mcp.mcp.config import MCPConfig, MCPTaskConfig
from litestar_mcp.mcp.tasks import MCPTaskStore


def test_mcp_task_config_validation() -> None:
    """MCPTaskConfig validates non-negative TTL and positive poll interval."""
    cfg = MCPConfig(tasks=MCPTaskConfig(poll_interval_ms=500))
    assert isinstance(cfg.tasks, MCPTaskConfig)
    assert cfg.tasks.poll_interval_ms == 500

    with pytest.raises(ValueError, match="default_ttl_ms must be non-negative"):
        MCPTaskConfig(default_ttl_ms=-1)

    with pytest.raises(ValueError, match="max_ttl_ms must be greater than or equal to default_ttl_ms"):
        MCPTaskConfig(default_ttl_ms=1000, max_ttl_ms=500)

    with pytest.raises(ValueError, match="poll_interval_ms must be positive"):
        MCPTaskConfig(poll_interval_ms=0)


@pytest.mark.anyio
async def test_mcp_task_store_lifecycle() -> None:
    """MCPTaskStore supports task creation, input requirement, updates, and cancellation."""
    store = MCPTaskStore()

    record = await store.create(owner_id="user-1")
    assert record.status == "working"
    assert record.owner_id == "user-1"

    paused = await store.require_input(
        record.task_id,
        input_requests={"confirm": {"type": "boolean"}},
        request_state="awaiting_confirmation",
    )
    assert paused.status == "input_required"
    assert paused.request_state == "awaiting_confirmation"

    await store.update(record.task_id, owner_id="user-1", input_responses={"confirm": True})
    resumed = await store.get(record.task_id, owner_id="user-1")
    assert resumed.status == "working"

    received_input = await store.wait_for_input(record.task_id)
    assert received_input == {"confirm": True}

    await store.cancel(record.task_id, owner_id="user-1")
    cancelled = await store.mark_cancelled(record.task_id)
    assert cancelled.status == "cancelled"
