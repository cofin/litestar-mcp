from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from litestar_mcp.mcp.config import MCPConfig, MCPTaskConfig, MCPTasksConfig
from litestar_mcp.mcp.tasks import MCPTaskStore


def test_mcp_tasks_config_alias_and_capability() -> None:
    assert MCPTasksConfig is MCPTaskConfig
    cfg = MCPConfig(tasks=MCPTasksConfig(backend="queue", poll_interval_ms=500))
    assert isinstance(cfg.tasks, MCPTaskConfig)
    assert cfg.tasks.backend == "queue"
    assert cfg.tasks.poll_interval_ms == 500


@pytest.mark.asyncio
async def test_mcp_task_store_queue_dispatch_cancel_and_mrtr_suspension() -> None:
    mock_queue_service = AsyncMock()
    store = MCPTaskStore(queue_service=mock_queue_service)

    record = await store.create(owner_id="user-1")
    assert record.status == "working"

    async def dummy_tool(x: int, task_id: str = "") -> int:
        return x + 1

    await store.enqueue_tool(record.task_id, dummy_tool, x=10)
    mock_queue_service.enqueue.assert_awaited_once()

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
    mock_queue_service.cancel_task.assert_awaited_once_with(record.task_id, include_running=True)
