"""Tests for the MCP conformance fixture server configuration and executor wiring."""

import importlib

import pytest
from litestar_queues import QueuePlugin

from litestar_mcp import MCPTaskConfig
from litestar_mcp.queues import QueueTaskExecutor
from tests.conformance import server


@pytest.mark.anyio
async def test_conformance_server_queue_mode_wires_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    """When MCP_CONFORMANCE_TASK_EXECUTOR is 'queue', QueuePlugin and QueueTaskExecutor are wired."""
    try:
        monkeypatch.setenv("MCP_CONFORMANCE_TASK_EXECUTOR", "queue")
        importlib.reload(server)

        assert any(isinstance(plugin, QueuePlugin) for plugin in server.app.plugins)
        task_config = server.plugin._config.tasks
        assert isinstance(task_config, MCPTaskConfig)
        assert isinstance(task_config.executor, QueueTaskExecutor)
    finally:
        monkeypatch.delenv("MCP_CONFORMANCE_TASK_EXECUTOR", raising=False)
        importlib.reload(server)


@pytest.mark.anyio
async def test_conformance_server_process_mode_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default process mode leaves executor unset and omits QueuePlugin."""
    try:
        monkeypatch.delenv("MCP_CONFORMANCE_TASK_EXECUTOR", raising=False)
        importlib.reload(server)

        assert not any(isinstance(plugin, QueuePlugin) for plugin in server.app.plugins)
        task_config = server.plugin._config.tasks
        assert isinstance(task_config, MCPTaskConfig)
        assert task_config.executor is None
    finally:
        importlib.reload(server)


def test_conformance_server_invalid_executor_mode_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unsupported MCP_CONFORMANCE_TASK_EXECUTOR value raises ValueError."""
    try:
        monkeypatch.setenv("MCP_CONFORMANCE_TASK_EXECUTOR", "invalid_mode")
        with pytest.raises(ValueError, match="Invalid MCP_CONFORMANCE_TASK_EXECUTOR"):
            importlib.reload(server)
    finally:
        monkeypatch.delenv("MCP_CONFORMANCE_TASK_EXECUTOR", raising=False)
        importlib.reload(server)
