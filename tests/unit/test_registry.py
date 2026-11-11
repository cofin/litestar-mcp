"""Tests for the MCP Registry."""

import asyncio
from typing import Any, cast

import pytest
from litestar.handlers import get

from litestar_mcp.core.sse import SubscriptionManager
from litestar_mcp.mcp.registry import Registry


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
    has_before = registry.has_subscription_manager
    assert has_before is False
    with pytest.raises(RuntimeError, match="Subscription manager has not been configured"):
        _ = registry.subscription_manager

    manager = SubscriptionManager()
    registry.set_subscription_manager(manager)
    has_after = registry.has_subscription_manager
    assert has_after is True
    assert registry.subscription_manager is manager


@pytest.mark.asyncio
async def test_registry_notifications(registry: "Registry") -> "None":
    subscription_manager = SubscriptionManager()
    registry.set_subscription_manager(subscription_manager)

    stream_id, stream = await subscription_manager.open("request-1", {"resourceSubscriptions": ["test://res"]})
    await stream.__anext__()

    await registry.notify_resource_updated("test://res")

    data = await stream.__anext__()
    assert data["method"] == "notifications/resources/updated"
    assert data["params"]["uri"] == "test://res"
    await subscription_manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_registry_notify_resources_list_changed(registry: "Registry") -> "None":
    await registry.notify_resources_list_changed()

    subscription_manager = SubscriptionManager()
    registry.set_subscription_manager(subscription_manager)

    res_id, res_stream = await subscription_manager.open("res-sub", {"resourcesListChanged": True})
    tools_id, tools_stream = await subscription_manager.open("tools-sub", {"toolsListChanged": True})
    await res_stream.__anext__()
    await tools_stream.__anext__()

    await registry.notify_resources_list_changed()
    await registry.notify_tools_list_changed()

    res_event = await asyncio.wait_for(res_stream.__anext__(), 0.5)
    tools_event = await asyncio.wait_for(tools_stream.__anext__(), 0.5)

    assert res_event["method"] == "notifications/resources/list_changed"
    assert tools_event["method"] == "notifications/tools/list_changed"

    await subscription_manager.disconnect(res_id)
    await subscription_manager.disconnect(tools_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subscriber_owner", "publish_owner", "expect_delivered"),
    [
        ("user:alice", "user:alice", True),
        ("user:bob", "user:alice", False),
        (None, "user:alice", False),
        ("user:alice", None, True),
    ],
)
async def test_registry_notify_resource_updated_owner_scoping(
    registry: "Registry",
    subscriber_owner: "str | None",
    publish_owner: "str | None",
    expect_delivered: "bool",
) -> "None":
    subscription_manager = SubscriptionManager()
    registry.set_subscription_manager(subscription_manager)

    stream_id, stream = await subscription_manager.open_session_stream(
        session_id="sess-1",
        owner_id=subscriber_owner,
        notifications={"resourceSubscriptions": ["doc://1"]},
    )

    await registry.notify_resource_updated("doc://1", owner_id=publish_owner)
    await registry.notify_tools_list_changed()

    first = await asyncio.wait_for(stream.__anext__(), 0.5)
    if expect_delivered:
        assert first == {"jsonrpc": "2.0", "method": "notifications/resources/updated", "params": {"uri": "doc://1"}}
        sentinel = await asyncio.wait_for(stream.__anext__(), 0.5)
        assert sentinel["method"] == "notifications/tools/list_changed"
    else:
        assert first["method"] == "notifications/tools/list_changed"

    await subscription_manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_registry_publish_notification_two_arg_double_compatibility(registry: "Registry") -> "None":
    recorded: list[tuple[str, dict[str, Any]]] = []

    class TwoArgManager:
        async def publish(self, method: str, params: dict[str, Any]) -> None:
            await asyncio.sleep(0)
            recorded.append((method, params))

    registry.set_subscription_manager(cast("Any", TwoArgManager()))
    await registry.notify_resource_updated("doc://legacy")
    await registry.notify_resources_list_changed()

    assert recorded == [
        ("notifications/resources/updated", {"uri": "doc://legacy"}),
        ("notifications/resources/list_changed", {}),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("target_owner", "target_session", "expect_s1_alice", "expect_s2_alice", "expect_s1_bob"),
    [
        ("user:alice", "sess-1", True, False, False),
        ("user:alice", None, True, True, False),
        (None, "sess-2", False, True, False),
    ],
)
async def test_registry_publish_notification_owner_and_session(
    registry: "Registry",
    target_owner: "str | None",
    target_session: "str | None",
    expect_s1_alice: "bool",
    expect_s2_alice: "bool",
    expect_s1_bob: "bool",
) -> "None":
    manager = SubscriptionManager()
    registry.set_subscription_manager(manager)

    s1_alice_id, s1_alice_stream = await manager.open_session_stream(session_id="sess-1", owner_id="user:alice")
    s2_alice_id, s2_alice_stream = await manager.open_session_stream(session_id="sess-2", owner_id="user:alice")
    s1_bob_id, s1_bob_stream = await manager.open_session_stream(session_id="sess-1", owner_id="user:bob")

    await registry.publish_notification(
        "notifications/message",
        {"level": "error", "data": "ok"},
        owner_id=target_owner,
        session_id=target_session,
    )

    if expect_s1_alice:
        event = await asyncio.wait_for(s1_alice_stream.__anext__(), timeout=0.5)
        assert event["params"] == {"level": "error", "data": "ok"}
    else:
        assert manager._streams[s1_alice_id].queue.empty()

    if expect_s2_alice:
        event = await asyncio.wait_for(s2_alice_stream.__anext__(), timeout=0.5)
        assert event["params"] == {"level": "error", "data": "ok"}
    else:
        assert manager._streams[s2_alice_id].queue.empty()

    if expect_s1_bob:
        event = await asyncio.wait_for(s1_bob_stream.__anext__(), timeout=0.5)
        assert event["params"] == {"level": "error", "data": "ok"}
    else:
        assert manager._streams[s1_bob_id].queue.empty()

    await manager.disconnect(s1_alice_id)
    await manager.disconnect(s2_alice_id)
    await manager.disconnect(s1_bob_id)
