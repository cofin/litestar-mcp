"""Tests for the MCP conformance fixture server configuration and executor wiring."""

import importlib
import os
from typing import Any

import pytest
from litestar.testing import TestClient

from litestar_mcp import MCPTaskConfig
from tests.conformance import server


@pytest.mark.anyio
async def test_conformance_server_queue_mode_wires_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    """When MCP_CONFORMANCE_TASK_EXECUTOR is 'queue', QueuePlugin and QueueTaskExecutor are wired."""
    litestar_queues = pytest.importorskip("litestar_queues")
    mcp_queues = pytest.importorskip("litestar_mcp.queues")
    try:
        monkeypatch.setenv("MCP_CONFORMANCE_TASK_EXECUTOR", "queue")
        importlib.reload(server)

        assert any(isinstance(plugin, litestar_queues.QueuePlugin) for plugin in server.app.plugins)
        task_config = server.plugin._config.tasks
        assert isinstance(task_config, MCPTaskConfig)
        assert isinstance(task_config.executor, mcp_queues.QueueTaskExecutor)
    finally:
        monkeypatch.delenv("MCP_CONFORMANCE_TASK_EXECUTOR", raising=False)
        importlib.reload(server)


@pytest.mark.anyio
async def test_conformance_server_process_mode_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default process mode leaves the executor unset and registers only the MCP plugin."""
    try:
        monkeypatch.delenv("MCP_CONFORMANCE_TASK_EXECUTOR", raising=False)
        importlib.reload(server)

        assert server.queue_plugins == []
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


def _post_jsonrpc(client: "TestClient[Any]", method: str, request_id: int) -> dict[str, Any]:
    """Post a stateless 2026-07-28 JSON-RPC request against the conformance server."""
    response = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                }
            },
        },
        headers={
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2026-07-28",
            "Mcp-Method": method,
        },
    )
    assert response.status_code == 200
    payload: dict[str, Any] = response.json()
    return payload


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
def test_conformance_server_allowed_hosts_include_loopback(host: str) -> None:
    """Conformance app restricts AllowedHostsMiddleware to loopback hosts."""
    port = os.getenv("MCP_CONFORMANCE_PORT", "18765")
    assert server.app.allowed_hosts is not None
    assert f"{host}:{port}" in server.app.allowed_hosts.allowed_hosts


def test_conformance_server_registers_interop_tools_and_widget_resource() -> None:
    """Conformance fixture exposes logging, interop task, quote, and UI widget handlers."""
    port = os.getenv("MCP_CONFORMANCE_PORT", "18765")
    assert {"test_logging_tool", "interop_task_job", "test_structured_quote"} <= set(server.plugin.discovered_tools)
    assert "conformance_widget" in server.plugin.discovered_resources
    assert server.plugin.discovered_resources["conformance_widget"].opt["mcp_resource_uri"] == (
        "ui://conformance/widget.html"
    )

    with TestClient(app=server.app, base_url=f"http://localhost:{port}") as client:
        tools_payload = _post_jsonrpc(client, "tools/list", 1)
        tools_by_name = {tool["name"]: tool for tool in tools_payload["result"]["tools"]}
        assert {"test_logging_tool", "interop_task_job", "test_structured_quote"} <= set(tools_by_name)
        quote_tool = tools_by_name["test_structured_quote"]
        assert quote_tool["outputSchema"]["type"] == "object"
        assert quote_tool["_meta"]["ui"]["resourceUri"] == "ui://conformance/widget.html"

        resources_payload = _post_jsonrpc(client, "resources/list", 2)
        resources_by_name = {item["name"]: item for item in resources_payload["result"]["resources"]}
        assert "conformance_widget" in resources_by_name
        widget = resources_by_name["conformance_widget"]
        assert widget["uri"] == "ui://conformance/widget.html"
        assert widget["mimeType"] == "text/html;profile=mcp-app"
        assert widget["_meta"] == {"ui": {"prefersBorder": True}}
