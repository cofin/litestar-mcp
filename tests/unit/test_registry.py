"""Tests for the MCP Registry."""

import json

import pytest
from litestar.handlers import get

from litestar_mcp.registry import Registry


@pytest.fixture
def registry() -> Registry:
    return Registry()


def test_registry_tool_registration(registry: Registry) -> None:
    @get("/")
    def my_handler() -> str:
        return "hello"

    registry.register_tool("my_tool", my_handler)
    assert "my_tool" in registry.tools
    assert registry.tools["my_tool"] == my_handler


def test_registry_resource_registration(registry: Registry) -> None:
    @get("/")
    def my_handler() -> str:
        return "hello"

    registry.register_resource("my_resource", my_handler)
    assert "my_resource" in registry.resources
    assert registry.resources["my_resource"] == my_handler


def test_registry_sse_manager_property_requires_configuration(registry: Registry) -> None:
    with pytest.raises(RuntimeError, match="SSE manager has not been configured"):
        _ = registry.sse_manager


@pytest.mark.asyncio
async def test_registry_notifications(registry: Registry) -> None:
    from litestar_mcp.sse import SSEManager

    sse_manager = SSEManager()
    registry.set_sse_manager(sse_manager)

    stream_id, stream = await sse_manager.open_stream(session_id="session1")
    await stream.__anext__()  # Prime event

    # Notify
    await registry.notify_resource_updated("test://res")

    # Check received
    msg = await stream.__anext__()
    data = json.loads(msg.data)
    assert data["method"] == "notifications/resources/updated"
    assert data["params"]["uri"] == "test://res"
    sse_manager.disconnect(stream_id)
