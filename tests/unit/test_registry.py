"""Tests for the MCP Registry."""

import pytest
from litestar.handlers import get

from litestar_mcp.registry import Registry


@pytest.fixture
def registry() -> "Registry":
    return Registry()


def test_registry_tool_registration(registry: "Registry") -> "None":
    @get("/")
    def my_handler() -> "str":
        return "hello"

    registry.register_tool("my_tool", my_handler)
    assert "my_tool" in registry.tools
    assert registry.tools["my_tool"] == my_handler


def test_registry_resource_registration(registry: "Registry") -> "None":
    @get("/")
    def my_handler() -> "str":
        return "hello"

    registry.register_resource("my_resource", my_handler)
    assert "my_resource" in registry.resources
    assert registry.resources["my_resource"] == my_handler


def test_registry_subscription_manager_property_requires_configuration(registry: "Registry") -> "None":
    with pytest.raises(RuntimeError, match="Subscription manager has not been configured"):
        _ = registry.subscription_manager


@pytest.mark.asyncio
async def test_registry_notifications(registry: "Registry") -> "None":
    from litestar_mcp.sse import SubscriptionManager

    subscription_manager = SubscriptionManager()
    registry.set_subscription_manager(subscription_manager)

    stream_id, stream = await subscription_manager.open("request-1", {"resourceSubscriptions": ["test://res"]})
    await stream.__anext__()

    # Notify
    await registry.notify_resource_updated("test://res")

    # Check received
    data = await stream.__anext__()
    assert data["method"] == "notifications/resources/updated"
    assert data["params"]["uri"] == "test://res"
    await subscription_manager.disconnect(stream_id)
