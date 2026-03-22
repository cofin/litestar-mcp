"""Tests for the MCP Registry."""

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


def test_registry_metadata_storage(registry: Registry) -> None:
    @get("/")
    def my_handler() -> str:
        return "hello"

    metadata = {"type": "tool", "name": "test"}
    registry.set_metadata(my_handler, metadata)
    assert registry.get_metadata(my_handler) == metadata


@pytest.mark.asyncio
async def test_registry_notifications(registry: Registry) -> None:
    import json

    from litestar_mcp.sse import SSEManager

    sse_manager = SSEManager()
    registry.set_sse_manager(sse_manager)

    # Subscribe a client
    sse_manager.register_client("client1")
    stream = sse_manager.subscribe("client1")

    # Notify
    await registry.notify_resource_updated("test://res")

    # Check received
    msg = await stream.__anext__()
    data = json.loads(msg.data)
    assert data["method"] == "notifications/resources/updated"
    assert data["params"]["uri"] == "test://res"
