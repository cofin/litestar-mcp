"""Tests for MCP 2026 subscription filtering and lifecycle."""

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any, cast

import httpx2
import pytest
from litestar import Litestar, get
from litestar.channels import ChannelsPlugin
from litestar.channels.backends.memory import MemoryChannelsBackend

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.core.jsonrpc import INVALID_PARAMS, METHOD_NOT_FOUND, JSONRPCErrorException
from litestar_mcp.core.serialization import encode_json, from_json
from litestar_mcp.core.sse import (
    LOG_LEVEL_SEVERITY,
    MCP_LOG_LEVELS,
    StreamLimitExceeded,
    SubscriptionManager,
    log_level_allows,
    validate_log_level,
)
from litestar_mcp.mcp.registry import Registry
from litestar_mcp.mcp.routes import (
    MCP_OWNER_ID_SCOPE_KEY,
    MCP_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION_HEADER,
    MCP_SESSION_ID_HEADER,
)
from litestar_mcp.mcp.service import MCPHandlerService, MCPRequestContext, get_session_store
from litestar_mcp.mcp.stdio import ASGIStreamingTransport

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Awaitable, Callable

    from litestar import Request
    from litestar.types import ASGIApp, Receive, Scope, Send


@pytest.mark.asyncio
async def test_acknowledgement_is_first_and_uses_request_id() -> None:
    manager = SubscriptionManager()
    stream_id, stream = await manager.open(
        "listen-1",
        {"toolsListChanged": True, "resourceSubscriptions": ["file:///a"]},
    )

    first = await stream.__anext__()

    assert first["method"] == "notifications/subscriptions/acknowledged"
    assert first["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "listen-1"
    assert first["params"]["notifications"] == {
        "toolsListChanged": True,
        "resourceSubscriptions": ["file:///a"],
    }
    await manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_notifications_are_filtered_and_tagged() -> None:
    manager = SubscriptionManager()
    tools_id, tools = await manager.open("tools", {"toolsListChanged": True})
    resource_id, resources = await manager.open(
        "resource",
        {"resourceSubscriptions": ["file:///selected"]},
    )
    await tools.__anext__()
    await resources.__anext__()

    await manager.publish("notifications/tools/list_changed", {})
    await manager.publish("notifications/resources/updated", {"uri": "file:///other"})
    await manager.publish("notifications/resources/updated", {"uri": "file:///selected"})

    tool_notification = await asyncio.wait_for(tools.__anext__(), 0.1)
    resource_notification = await asyncio.wait_for(resources.__anext__(), 0.1)

    assert tool_notification["method"] == "notifications/tools/list_changed"
    assert tool_notification["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "tools"
    assert resource_notification["params"]["uri"] == "file:///selected"
    assert resource_notification["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "resource"
    await manager.disconnect(tools_id)
    await manager.disconnect(resource_id)


@pytest.mark.asyncio
async def test_stream_limit_and_graceful_shutdown() -> None:
    manager = SubscriptionManager(max_streams=1)
    stream_id, stream = await manager.open(1, {"promptsListChanged": True})
    await stream.__anext__()

    with pytest.raises(StreamLimitExceeded):
        await manager.open(2, {})

    await manager.close_all()
    completion = await stream.__anext__()
    assert completion["id"] == 1
    assert completion["result"]["resultType"] == "complete"
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()
    await manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_channels_backend_fans_notifications_across_manager_instances() -> None:
    class Subscriber:
        def __init__(self) -> None:
            self.queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        async def iter_events(self) -> "AsyncGenerator[bytes, None]":
            while (event := await self.queue.get()) is not None:
                yield event

    class Channels:
        def __init__(self) -> None:
            self.subscribers: list[Subscriber] = []

        @asynccontextmanager
        async def start_subscription(self, _channel: str) -> "AsyncIterator[Subscriber]":
            subscriber = Subscriber()
            self.subscribers.append(subscriber)
            try:
                yield subscriber
            finally:
                await subscriber.queue.put(None)

        def publish(self, data: dict[str, Any], *, channels: str) -> None:
            assert channels == "litestar-mcp-subscriptions"
            for subscriber in self.subscribers:
                subscriber.queue.put_nowait(encode_json(data))

    channels = Channels()
    publisher = SubscriptionManager(channels=channels)
    receiver = SubscriptionManager(channels=channels)
    publisher.start()
    receiver.start()
    await asyncio.sleep(0)
    _stream_id, stream = await receiver.open("sub-1", {"toolsListChanged": True})
    await stream.__anext__()

    await publisher.publish("notifications/tools/list_changed", {})
    notification = await asyncio.wait_for(stream.__anext__(), timeout=1)

    assert notification["method"] == "notifications/tools/list_changed"
    await publisher.close_all()
    await receiver.close_all()


@pytest.mark.anyio
async def test_slow_subscriber_receives_completion_before_close() -> None:
    manager = SubscriptionManager(queue_capacity=1)
    _stream_id, stream = await manager.open("sub-1", {"toolsListChanged": True})

    await manager.publish("notifications/tools/list_changed", {})
    messages = [message async for message in stream]

    assert messages[-1] == {
        "jsonrpc": "2.0",
        "id": "sub-1",
        "result": {"resultType": "complete", "_meta": {"io.modelcontextprotocol/subscriptionId": "sub-1"}},
    }
    assert "method" not in messages[-1]


class _FlakyChannels:
    """Channels double whose first subscription fails and whose events can be injected."""

    def __init__(self) -> None:
        self.attempts = 0
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()

    @asynccontextmanager
    async def start_subscription(self, _channel: str) -> "AsyncIterator[Any]":
        self.attempts += 1
        if self.attempts == 1:
            msg = "broker unavailable"
            raise ConnectionError(msg)
        queue = self.queue

        class Subscriber:
            async def iter_events(self) -> "AsyncGenerator[bytes, None]":
                while True:
                    yield await queue.get()

        yield Subscriber()

    def publish(self, data: dict[str, Any], *, channels: str) -> None:
        assert channels == "litestar-mcp-subscriptions"
        self.queue.put_nowait(encode_json(data))


@pytest.mark.asyncio
async def test_channels_broker_survives_backend_errors_and_undecodable_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("litestar_mcp.core.sse._BROKER_RETRY_INITIAL", 0)
    channels = _FlakyChannels()
    manager = SubscriptionManager(channels=channels)
    manager.start()
    _stream_id, stream = await manager.open("sub-1", {"toolsListChanged": True})
    await stream.__anext__()

    channels.queue.put_nowait(b"{not json")
    await manager.publish("notifications/tools/list_changed", {})
    notification = await asyncio.wait_for(stream.__anext__(), timeout=1)

    assert notification["method"] == "notifications/tools/list_changed"
    assert channels.attempts == 2
    await manager.close_all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("notifications_filter", "expected_methods"),
    [
        (
            {"resourceSubscriptions": ["doc://1"]},
            [
                "notifications/tools/list_changed",
                "notifications/prompts/list_changed",
                "notifications/resources/list_changed",
                "notifications/resources/updated",
            ],
        ),
        (
            {"toolsListChanged": False},
            [
                "notifications/prompts/list_changed",
                "notifications/resources/list_changed",
            ],
        ),
        (
            None,
            [
                "notifications/tools/list_changed",
                "notifications/prompts/list_changed",
                "notifications/resources/list_changed",
            ],
        ),
    ],
)
async def test_open_session_stream_seeds_list_changed_defaults_and_merges_overrides(
    notifications_filter: dict[str, Any] | None,
    expected_methods: list[str],
) -> None:
    manager = SubscriptionManager()
    stream_id, stream = await manager.open_session_stream(
        session_id="sess-merge",
        owner_id="user:alice",
        notifications=notifications_filter,
    )
    state = manager._streams[stream_id]
    assert state.is_session_stream is True
    assert state.send_acknowledgement is False
    assert state.include_subscription_meta is False
    assert state.session_id == "sess-merge"
    assert state.owner_id == "user:alice"
    assert state.queue.empty()

    await manager.publish(
        "notifications/tools/list_changed",
        {"_meta": {"io.modelcontextprotocol/subscriptionId": "strip-only"}},
    )
    await manager.publish("notifications/prompts/list_changed", {})
    await manager.publish("notifications/resources/list_changed", {})
    await manager.publish("notifications/resources/updated", {"uri": "doc://other"})
    await manager.publish(
        "notifications/resources/updated",
        {
            "uri": "doc://1",
            "_meta": {"io.modelcontextprotocol/subscriptionId": "strip-me", "trace": "keep"},
        },
    )

    received: list[dict[str, Any]] = []
    while not state.queue.empty():
        received.append(await asyncio.wait_for(stream.__anext__(), 0.1))

    assert [item["method"] for item in received] == expected_methods
    assert all(item["method"] != "notifications/subscriptions/acknowledged" for item in received)
    for item in received:
        meta = item["params"].get("_meta")
        if meta is not None:
            assert "io.modelcontextprotocol/subscriptionId" not in meta
        if item["method"] == "notifications/tools/list_changed":
            assert "_meta" not in item["params"]
        if item["method"] == "notifications/resources/updated":
            assert item["params"]["uri"] == "doc://1"
            assert item["params"]["_meta"] == {"trace": "keep"}

    await manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_close_session_stream_limit_and_overflow_suppress_2026_complete_on_session_streams() -> None:
    capped = SubscriptionManager(max_streams=1)
    capped_id, _capped_stream = await capped.open_session_stream(session_id="capped")
    with pytest.raises(StreamLimitExceeded):
        await capped.open_session_stream(session_id="overflow-cap")
    await capped.disconnect(capped_id)

    manager = SubscriptionManager(queue_capacity=1)
    s1_id, s1_stream = await manager.open_session_stream(session_id="sess-1")
    s2_id, s2_stream = await manager.open_session_stream(session_id="sess-2")
    assert manager._streams[s1_id].is_session_stream is True
    assert manager._streams[s2_id].is_session_stream is True

    await manager.close_session("sess-1")
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(s1_stream.__anext__(), 0.1)

    await manager.publish("notifications/tools/list_changed", {})
    s2_event = await asyncio.wait_for(s2_stream.__anext__(), 0.1)
    assert s2_event["method"] == "notifications/tools/list_changed"

    await manager.close_all()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(s2_stream.__anext__(), 0.1)

    overflow_mgr = SubscriptionManager(queue_capacity=1)
    sess_id, sess_stream = await overflow_mgr.open_session_stream(session_id="slow-session")
    listen_id, listen_stream = await overflow_mgr.open(
        "sub-2026",
        {"toolsListChanged": True},
        owner_id="user:alice",
    )
    assert overflow_mgr._streams[sess_id].is_session_stream is True
    assert overflow_mgr._streams[listen_id].is_session_stream is False

    await overflow_mgr.publish("notifications/tools/list_changed", {})
    await overflow_mgr.publish("notifications/tools/list_changed", {})

    session_messages = [msg async for msg in sess_stream]
    listen_messages = [msg async for msg in listen_stream]

    assert len(session_messages) == 1
    assert session_messages[0]["method"] == "notifications/tools/list_changed"
    assert all(msg.get("result", {}).get("resultType") != "complete" for msg in session_messages)
    assert listen_messages[-1]["result"]["resultType"] == "complete"
    assert listen_messages[-1]["id"] == "sub-2026"


class _InMemoryChannels:
    """In-memory ChannelsPlugin double preserving serialized broker envelopes."""

    def __init__(self) -> None:
        self.envelopes: list[dict[str, Any]] = []
        self._queues: list[asyncio.Queue[bytes | None]] = []

    @asynccontextmanager
    async def start_subscription(self, _channel: str) -> "AsyncIterator[Any]":
        queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._queues.append(queue)

        class _Sub:
            async def iter_events(self) -> "AsyncGenerator[bytes, None]":
                while (raw := await queue.get()) is not None:
                    yield raw

        try:
            yield _Sub()
        finally:
            await queue.put(None)

    def publish(self, data: dict[str, Any], *, channels: str) -> None:
        assert channels == "litestar-mcp-subscriptions"
        self.envelopes.append(dict(data))
        encoded = encode_json(data)
        for queue in list(self._queues):
            queue.put_nowait(encoded)


def _with_owner_header(app: "ASGIApp") -> "ASGIApp":
    async def wrapper(scope: "Scope", receive: "Receive", send: "Send") -> None:
        if cast("str", scope["type"]) == "http":
            headers = {bytes(k).lower(): bytes(v).decode("latin-1") for k, v in scope.get("headers", ())}
            owner = headers.get(b"x-test-owner")
            if owner:
                cast("dict[str, Any]", scope)[MCP_OWNER_ID_SCOPE_KEY] = owner
        await app(scope, receive, send)

    return wrapper


@asynccontextmanager
async def _open_http_sse_queue(
    client: httpx2.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
) -> "AsyncIterator[asyncio.Queue[dict[str, Any]]]":
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    ready = asyncio.Event()

    async def _reader() -> None:
        try:
            async with client.stream("GET", url, headers=headers) as response:
                assert response.status_code == 200
                ready.set()
                async for event in httpx2.EventSource(response):
                    if event.data:
                        payload = from_json(event.data)
                        if isinstance(payload, dict):
                            await queue.put(payload)
        finally:
            ready.set()

    task = asyncio.create_task(_reader())
    try:
        await asyncio.wait_for(ready.wait(), timeout=2.0)
        yield queue
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


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
@pytest.mark.parametrize("via_channels", [False, True])
async def test_subscription_manager_owner_scoped_fanout(
    subscriber_owner: str | None,
    publish_owner: str | None,
    expect_delivered: bool,
    via_channels: bool,
) -> None:
    channels = _InMemoryChannels() if via_channels else None
    publisher = SubscriptionManager(channels=channels)
    receiver = SubscriptionManager(channels=channels) if via_channels else publisher
    publisher.start()
    if receiver is not publisher:
        receiver.start()
    await asyncio.sleep(0)

    stream_id, stream = await receiver.open_session_stream(
        session_id="sess-owner",
        owner_id=subscriber_owner,
        notifications={"resourceSubscriptions": ["doc://1"]},
    )

    await publisher.publish("notifications/resources/updated", {"uri": "doc://1"}, owner_id=publish_owner)
    await publisher.publish("notifications/tools/list_changed", {})

    first = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
    if expect_delivered:
        assert first == {"jsonrpc": "2.0", "method": "notifications/resources/updated", "params": {"uri": "doc://1"}}
        assert "owner_id" not in first["params"]
        second = await asyncio.wait_for(stream.__anext__(), timeout=1.0)
        assert second["method"] == "notifications/tools/list_changed"
    else:
        assert first["method"] == "notifications/tools/list_changed"

    if channels is not None:
        expected_envelope: dict[str, Any] = {
            "method": "notifications/resources/updated",
            "params": {"uri": "doc://1"},
        }
        if publish_owner is not None:
            expected_envelope["owner_id"] = publish_owner
        assert channels.envelopes[0] == expected_envelope

    await receiver.disconnect(stream_id)
    await publisher.close_all()
    if receiver is not publisher:
        await receiver.close_all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expect_target_delivered"),
    [
        ("manager_sync_false", False),
        ("manager_async_false", False),
        ("manager_raises", False),
        ("stream_sync_false", False),
        ("stream_async_false", False),
        ("stream_raises", False),
        ("both_allow", True),
    ],
)
async def test_subscription_manager_authorization_predicates_gate_delivery_fail_closed(
    mode: str,
    expect_target_delivered: bool,
) -> None:
    def _sync_manager_deny(_owner: str | None, method: str, params: dict[str, Any]) -> bool:
        return not (method == "notifications/resources/updated" and params.get("uri") == "secret://1")

    async def _async_manager_deny(owner: str | None, method: str, params: dict[str, Any]) -> bool:
        await asyncio.sleep(0)
        return owner == "user:admin" or method != "notifications/resources/updated" or params.get("uri") != "secret://1"

    def _raising_manager(owner: str | None, method: str, _params: dict[str, Any]) -> bool:
        if owner == "user:target" and method == "notifications/resources/updated":
            msg = "authorizer failure"
            raise RuntimeError(msg)
        return True

    def _sync_stream_deny(method: str, params: dict[str, Any]) -> bool:
        return method != "notifications/resources/updated" or params.get("uri") != "secret://1"

    async def _async_stream_deny(method: str, params: dict[str, Any]) -> bool:
        await asyncio.sleep(0)
        return method != "notifications/resources/updated" or params.get("uri") != "secret://1"

    def _raising_stream(method: str, _params: dict[str, Any]) -> bool:
        if method == "notifications/resources/updated":
            msg = "stream predicate failure"
            raise RuntimeError(msg)
        return True

    async def _async_allow_manager(_owner: str | None, _method: str, _params: dict[str, Any]) -> bool:
        await asyncio.sleep(0)
        return True

    async def _async_allow_stream(_method: str, _params: dict[str, Any]) -> bool:
        await asyncio.sleep(0)
        return True

    manager_predicates: dict[str, Callable[[str | None, str, dict[str, Any]], Awaitable[bool] | bool]] = {
        "manager_sync_false": _sync_manager_deny,
        "manager_async_false": _async_manager_deny,
        "manager_raises": _raising_manager,
        "both_allow": _async_allow_manager,
    }
    stream_predicates: dict[str, Callable[[str, dict[str, Any]], Awaitable[bool] | bool]] = {
        "stream_sync_false": _sync_stream_deny,
        "stream_async_false": _async_stream_deny,
        "stream_raises": _raising_stream,
        "both_allow": _async_allow_stream,
    }

    manager = SubscriptionManager(authorizer=manager_predicates.get(mode))
    target_id, target_stream = await manager.open_session_stream(
        session_id="s-target",
        owner_id="user:target",
        notifications={"resourceSubscriptions": ["secret://1"]},
        authorize=stream_predicates.get(mode),
    )
    control_id, control_stream = await manager.open_session_stream(
        session_id="s-control",
        owner_id="user:admin",
        notifications={"resourceSubscriptions": ["public://ok"]},
    )

    await manager.publish("notifications/resources/updated", {"uri": "secret://1"})
    await manager.publish("notifications/resources/updated", {"uri": "public://ok"})
    await manager.publish("notifications/tools/list_changed", {})

    target_first = await asyncio.wait_for(target_stream.__anext__(), timeout=0.5)
    if expect_target_delivered:
        assert target_first["method"] == "notifications/resources/updated"
        assert target_first["params"]["uri"] == "secret://1"
        target_second = await asyncio.wait_for(target_stream.__anext__(), timeout=0.5)
        assert target_second["method"] == "notifications/tools/list_changed"
    else:
        assert target_first["method"] == "notifications/tools/list_changed"

    control_first = await asyncio.wait_for(control_stream.__anext__(), timeout=0.5)
    assert control_first["params"]["uri"] == "public://ok"

    await manager.disconnect(target_id)
    await manager.disconnect(control_id)


@pytest.mark.asyncio
async def test_session_stream_strips_subscription_id_meta_while_2026_listen_keeps_it() -> None:
    manager = SubscriptionManager()
    sess_id, sess_stream = await manager.open_session_stream(
        session_id="sess-meta",
        notifications={"resourceSubscriptions": ["file:///a", "file:///b"]},
    )
    listen_id, listen_stream = await manager.open(
        "listen-42",
        {"resourceSubscriptions": ["file:///a", "file:///b"]},
    )
    await listen_stream.__anext__()

    await manager.publish(
        "notifications/resources/updated",
        {"uri": "file:///a", "_meta": {"io.modelcontextprotocol/subscriptionId": "x"}},
    )
    await manager.publish(
        "notifications/resources/updated",
        {"uri": "file:///b", "_meta": {"io.modelcontextprotocol/subscriptionId": "leak", "trace": "ok"}},
    )

    sess_a = await asyncio.wait_for(sess_stream.__anext__(), 0.5)
    sess_b = await asyncio.wait_for(sess_stream.__anext__(), 0.5)
    listen_a = await asyncio.wait_for(listen_stream.__anext__(), 0.5)
    listen_b = await asyncio.wait_for(listen_stream.__anext__(), 0.5)

    assert sess_a["params"] == {"uri": "file:///a"}
    assert "_meta" not in sess_a["params"]
    assert sess_b["params"] == {"uri": "file:///b", "_meta": {"trace": "ok"}}
    assert listen_a["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "listen-42"
    assert listen_b["params"]["_meta"] == {"io.modelcontextprotocol/subscriptionId": "listen-42", "trace": "ok"}

    await manager.disconnect(sess_id)
    await manager.disconnect(listen_id)


@pytest.mark.asyncio
async def test_subscription_manager_live_add_and_remove_resource_subscription() -> None:
    manager = SubscriptionManager()
    s1_id, s1_stream = await manager.open_session_stream(session_id="sess-1")
    s2_id, _s2_stream = await manager.open_session_stream(session_id="sess-2")

    await manager.add_resource_subscription("", "doc://ignored")
    await manager.add_resource_subscription("sess-1", "")
    await manager.add_resource_subscription("sess-1", "doc://live")
    await manager.add_resource_subscription("sess-1", "doc://live")
    assert manager._streams[s1_id].notifications["resourceSubscriptions"] == ["doc://live"]
    assert "resourceSubscriptions" not in manager._streams[s2_id].notifications

    await manager.publish("notifications/resources/updated", {"uri": "doc://live"})
    s1_msg = await asyncio.wait_for(s1_stream.__anext__(), 0.5)
    assert s1_msg["params"] == {"uri": "doc://live"}
    assert manager._streams[s2_id].queue.empty()

    await manager.remove_resource_subscription("sess-1", "doc://not-subscribed")
    await manager.remove_resource_subscription("sess-1", "doc://live")
    await manager.remove_resource_subscription("sess-1", "doc://live")
    assert "resourceSubscriptions" not in manager._streams[s1_id].notifications

    await manager.publish("notifications/resources/updated", {"uri": "doc://live"})
    await manager.publish("notifications/tools/list_changed", {})
    sentinel = await asyncio.wait_for(s1_stream.__anext__(), 0.5)
    assert sentinel["method"] == "notifications/tools/list_changed"

    await manager.disconnect(s1_id)
    await manager.disconnect(s2_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rpc_call", "protocol_version", "expected_resources"),
    [
        ("server_discover", MCP_PROTOCOL_VERSION, {"listChanged": True}),
        ("initialize", MCP_PROTOCOL_VERSION, {"listChanged": True}),
        ("initialize", "2025-11-25", {"subscribe": True, "listChanged": True}),
        ("initialize", "2025-06-18", {"subscribe": True, "listChanged": True}),
    ],
)
async def test_handler_service_capabilities_advertise_resource_subscribe_for_2025(
    rpc_call: str,
    protocol_version: str,
    expected_resources: dict[str, bool],
) -> None:
    plugin = LitestarMCP(MCPConfig())
    app = Litestar(plugins=[plugin])
    service = MCPHandlerService(
        config=plugin.config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=app,
        registry=plugin.registry,
    )
    ctx = MCPRequestContext(client_id="test", protocol_version=protocol_version, owner_id=None)
    if rpc_call == "server_discover":
        result = await service.server_discover({}, ctx)
    else:
        result = await service.initialize({"protocolVersion": protocol_version}, ctx)

    assert result["capabilities"]["resources"] == expected_resources


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_uri",
    [None, 42, "", "not a uri", "nocolon", ":missing", "scheme:", "doc://has space"],
)
@pytest.mark.parametrize("method_name", ["resources_subscribe", "resources_unsubscribe"])
async def test_resources_subscribe_and_unsubscribe_validate_uri_and_require_session(
    bad_uri: Any,
    method_name: str,
) -> None:
    plugin = LitestarMCP(MCPConfig())
    app = Litestar(plugins=[plugin])
    service = MCPHandlerService(
        config=plugin.config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=app,
        registry=plugin.registry,
    )
    session = await get_session_store(app, plugin.config).create(protocol_version="2025-11-25", owner_id=None)
    handler = getattr(service, method_name)

    with pytest.raises(JSONRPCErrorException) as invalid_uri_exc:
        await handler(
            {"uri": bad_uri},
            MCPRequestContext(
                client_id="c",
                session_id=session.session_id,
                protocol_version="2025-11-25",
                owner_id=None,
            ),
        )
    assert invalid_uri_exc.value.error.code == INVALID_PARAMS

    with pytest.raises(JSONRPCErrorException) as missing_session_exc:
        await handler(
            {"uri": "doc://1"},
            MCPRequestContext(client_id="c", session_id=None, protocol_version="2025-11-25", owner_id=None),
        )
    assert missing_session_exc.value.error.code == INVALID_PARAMS
    assert "Mcp-Session-Id" in missing_session_exc.value.error.message

    with pytest.raises(JSONRPCErrorException) as expired_session_exc:
        await handler(
            {"uri": "doc://1"},
            MCPRequestContext(
                client_id="c",
                session_id="nonexistent-session",
                protocol_version="2025-11-25",
                owner_id=None,
            ),
        )
    assert expired_session_exc.value.error.code == INVALID_PARAMS


@pytest.mark.asyncio
async def test_resources_subscribe_fails_closed_when_authorizer_lacks_http_request() -> None:
    config = MCPConfig(subscription_authorizer=lambda _req, _uri: True)
    plugin = LitestarMCP(config)
    app = Litestar(plugins=[plugin])
    service = MCPHandlerService(
        config=config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=app,
        registry=plugin.registry,
    )
    store = get_session_store(app, config)
    session = await store.create(protocol_version="2025-11-25", owner_id="user:alice")

    with pytest.raises(JSONRPCErrorException) as exc_info:
        await service.resources_subscribe(
            {"uri": "doc://1"},
            MCPRequestContext(
                client_id="c",
                owner_id="user:alice",
                session_id=session.session_id,
                request=None,
                protocol_version="2025-11-25",
            ),
        )

    assert exc_info.value.error.code == INVALID_PARAMS
    assert exc_info.value.error.message == "Subscription authorization requires an HTTP request context"
    reloaded = await store.get(session.session_id, "user:alice")
    assert reloaded is not None
    assert reloaded.subscriptions == []


@pytest.mark.anyio
async def test_http_resources_subscribe_unsubscribe_and_get_sse_live_delivery() -> None:
    @get("/docs/alpha", mcp_resource="alpha", sync_to_thread=False)
    def alpha_resource() -> dict[str, str]:
        return {"text": "alpha"}

    plugin = LitestarMCP(MCPConfig())
    app = Litestar(route_handlers=[alpha_resource], plugins=[plugin])

    async with (
        app.lifespan(),
        httpx2.AsyncClient(transport=ASGIStreamingTransport(app), base_url="http://testserver") as client,
    ):
        modern_sub = await client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                MCP_PROTOCOL_VERSION_HEADER: MCP_PROTOCOL_VERSION,
                "Mcp-Method": "resources/subscribe",
            },
            json={
                "jsonrpc": "2.0",
                "id": 99,
                "method": "resources/subscribe",
                "params": {
                    "uri": "doc://alpha",
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                        "io.modelcontextprotocol/clientCapabilities": {},
                    },
                },
            },
        )
        assert modern_sub.status_code == 404
        assert modern_sub.json()["error"]["code"] == METHOD_NOT_FOUND

        sessionless_sub = await client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                MCP_PROTOCOL_VERSION_HEADER: "2025-11-25",
            },
            json={"jsonrpc": "2.0", "id": 98, "method": "resources/subscribe", "params": {"uri": "doc://alpha"}},
        )
        assert sessionless_sub.status_code == 400
        assert sessionless_sub.json()["error"]["code"] == INVALID_PARAMS

        init_resp = await client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                MCP_PROTOCOL_VERSION_HEADER: "2025-11-25",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "sub-test", "version": "1.0"},
                },
            },
        )
        assert init_resp.status_code == 200
        init_body = init_resp.json()
        assert init_body["result"]["capabilities"]["resources"] == {"subscribe": True, "listChanged": True}
        session_id = init_resp.headers[MCP_SESSION_ID_HEADER]

        sse_headers = {
            "Accept": "text/event-stream",
            MCP_PROTOCOL_VERSION_HEADER: "2025-11-25",
            MCP_SESSION_ID_HEADER: session_id,
        }
        rpc_headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            MCP_PROTOCOL_VERSION_HEADER: "2025-11-25",
            MCP_SESSION_ID_HEADER: session_id,
        }

        async with _open_http_sse_queue(client, "/mcp", headers=sse_headers) as events:
            sub_resp = await client.post(
                "/mcp",
                headers=rpc_headers,
                json={"jsonrpc": "2.0", "id": 2, "method": "resources/subscribe", "params": {"uri": "doc://alpha"}},
            )
            assert sub_resp.status_code == 200
            assert "error" not in sub_resp.json()

            await plugin.registry.notify_resource_updated("doc://other")
            await plugin.registry.notify_resource_updated("doc://alpha")
            await plugin.registry.notify_resources_list_changed()

            updated = await asyncio.wait_for(events.get(), timeout=1.0)
            list_changed = await asyncio.wait_for(events.get(), timeout=1.0)
            assert updated == {
                "jsonrpc": "2.0",
                "method": "notifications/resources/updated",
                "params": {"uri": "doc://alpha"},
            }
            assert "_meta" not in updated["params"]
            assert list_changed["method"] == "notifications/resources/list_changed"

            unsub_resp = await client.post(
                "/mcp",
                headers=rpc_headers,
                json={"jsonrpc": "2.0", "id": 3, "method": "resources/unsubscribe", "params": {"uri": "doc://alpha"}},
            )
            assert unsub_resp.status_code == 200
            assert "error" not in unsub_resp.json()

            repeat_unsub = await client.post(
                "/mcp",
                headers=rpc_headers,
                json={"jsonrpc": "2.0", "id": 4, "method": "resources/unsubscribe", "params": {"uri": "doc://alpha"}},
            )
            assert repeat_unsub.status_code == 200
            assert "error" not in repeat_unsub.json()

            await plugin.registry.notify_resource_updated("doc://alpha")
            await plugin.registry.notify_tools_list_changed()
            after_unsub = await asyncio.wait_for(events.get(), timeout=1.0)
            assert after_unsub["method"] == "notifications/tools/list_changed"


@pytest.mark.anyio
@pytest.mark.parametrize("use_async_authorizer", [False, True])
async def test_http_subscription_authorizer_denies_subscribe_and_isolates_owner_fanout(
    use_async_authorizer: bool,
) -> None:
    def _sync_auth(request: "Request[Any, Any, Any]", uri: str) -> bool:
        if uri.startswith("secret://"):
            return False
        owner = request.scope.get(MCP_OWNER_ID_SCOPE_KEY)
        if uri.startswith("user://alice/"):
            return owner == "user:alice"
        return True

    async def _async_auth(request: "Request[Any, Any, Any]", uri: str) -> bool:
        await asyncio.sleep(0)
        return _sync_auth(request, uri)

    authorizer: Callable[[Request[Any, Any, Any], str], Awaitable[bool] | bool] = (
        _async_auth if use_async_authorizer else _sync_auth
    )
    plugin = LitestarMCP(MCPConfig(subscription_authorizer=authorizer))
    app = Litestar(plugins=[plugin])
    wrapped = _with_owner_header(app)

    async def _init_owner(client: httpx2.AsyncClient, owner: str) -> str:
        resp = await client.post(
            "/mcp",
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
                MCP_PROTOCOL_VERSION_HEADER: "2025-11-25",
                "X-Test-Owner": owner,
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            },
        )
        assert resp.status_code == 200
        return resp.headers[MCP_SESSION_ID_HEADER]

    async with (
        app.lifespan(),
        httpx2.AsyncClient(transport=ASGIStreamingTransport(wrapped), base_url="http://testserver") as client,
    ):
        alice_sid = await _init_owner(client, "user:alice")
        bob_sid = await _init_owner(client, "user:bob")

        alice_sse_headers = {
            "Accept": "text/event-stream",
            MCP_PROTOCOL_VERSION_HEADER: "2025-11-25",
            MCP_SESSION_ID_HEADER: alice_sid,
            "X-Test-Owner": "user:alice",
        }
        bob_sse_headers = {
            "Accept": "text/event-stream",
            MCP_PROTOCOL_VERSION_HEADER: "2025-11-25",
            MCP_SESSION_ID_HEADER: bob_sid,
            "X-Test-Owner": "user:bob",
        }

        async with (
            _open_http_sse_queue(client, "/mcp", headers=alice_sse_headers) as alice_events,
            _open_http_sse_queue(client, "/mcp", headers=bob_sse_headers) as bob_events,
        ):

            async def _subscribe(owner: str, sid: str, uri: str, req_id: int) -> httpx2.Response:
                return await client.post(
                    "/mcp",
                    headers={
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json",
                        MCP_PROTOCOL_VERSION_HEADER: "2025-11-25",
                        MCP_SESSION_ID_HEADER: sid,
                        "X-Test-Owner": owner,
                    },
                    json={"jsonrpc": "2.0", "id": req_id, "method": "resources/subscribe", "params": {"uri": uri}},
                )

            denied = await _subscribe("user:bob", bob_sid, "secret://vault", 2)
            assert denied.status_code == 400
            assert denied.json()["error"]["code"] == INVALID_PARAMS

            bob_cross = await _subscribe("user:bob", bob_sid, "user://alice/notes", 3)
            assert bob_cross.status_code == 400
            assert bob_cross.json()["error"]["code"] == INVALID_PARAMS

            alice_ok = await _subscribe("user:alice", alice_sid, "shared://doc", 4)
            bob_ok = await _subscribe("user:bob", bob_sid, "shared://doc", 5)
            assert alice_ok.status_code == 200
            assert bob_ok.status_code == 200

            await plugin.registry.notify_resource_updated("secret://vault")
            await plugin.registry.notify_resource_updated("shared://doc", owner_id="user:alice")
            await plugin.registry.notify_tools_list_changed()

            alice_first = await asyncio.wait_for(alice_events.get(), timeout=1.0)
            alice_second = await asyncio.wait_for(alice_events.get(), timeout=1.0)
            bob_first = await asyncio.wait_for(bob_events.get(), timeout=1.0)

            assert alice_first == {
                "jsonrpc": "2.0",
                "method": "notifications/resources/updated",
                "params": {"uri": "shared://doc"},
            }
            assert alice_second["method"] == "notifications/tools/list_changed"
            assert bob_first["method"] == "notifications/tools/list_changed"


@pytest.mark.parametrize(
    ("message_level", "threshold", "expected"),
    [
        ("debug", "info", False),
        ("warning", "warning", True),
        ("emergency", "alert", True),
        ("notice", "error", False),
        ("debug", "debug", True),
        ("critical", "warning", True),
    ],
)
def test_rfc5424_severity_order(message_level: str, threshold: str, expected: bool) -> None:
    assert MCP_LOG_LEVELS == (
        "debug",
        "info",
        "notice",
        "warning",
        "error",
        "critical",
        "alert",
        "emergency",
    )
    assert LOG_LEVEL_SEVERITY["debug"] == 0
    assert LOG_LEVEL_SEVERITY["emergency"] == 7
    assert log_level_allows(message_level, threshold) is expected


@pytest.mark.parametrize("bad_level", ["warn", "INFO", "fatal", "", 123, ["debug"]])
def test_validate_log_level_and_manager_reject_invalid_levels(bad_level: Any) -> None:
    with pytest.raises(ValueError, match="Invalid MCP log level"):
        validate_log_level(bad_level)
    with pytest.raises(ValueError, match="Invalid MCP log level"):
        log_level_allows("info", bad_level)
    with pytest.raises(ValueError, match="Invalid MCP log level"):
        log_level_allows(bad_level, "info")
    with pytest.raises(ValueError, match="Invalid MCP log level"):
        SubscriptionManager(default_log_level=bad_level)

    manager = SubscriptionManager()
    with pytest.raises(ValueError, match="Invalid MCP log level"):
        manager.set_default_log_level(bad_level)
    with pytest.raises(ValueError, match="Invalid MCP log level"):
        manager.set_session_log_level("sess-1", bad_level)


def test_subscription_manager_channels_and_session_level_helpers() -> None:
    sentinel = object()
    manager = SubscriptionManager(default_log_level="notice")
    assert manager.channels is None
    manager.set_channels(sentinel)
    assert manager.channels is sentinel
    manager.set_channels(None)
    assert manager.channels is None

    assert manager.get_session_log_level(None) == "notice"
    assert manager.get_session_log_level("sess-1") == "notice"
    assert manager.get_session_log_level("sess-1", default="warning") == "warning"
    assert manager.set_session_log_level("sess-1", "error") == "error"
    assert manager.get_session_log_level("sess-1", default="debug") == "error"
    assert manager.set_default_log_level("warning") == "warning"
    assert manager.get_session_log_level("sess-2") == "warning"
    manager.clear_session_log_level("sess-1")
    assert manager.get_session_log_level("sess-1") == "warning"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("listen_filter", "expect_tools_changed"),
    [
        ({"toolsListChanged": True}, True),
        ({"logging": False}, False),
        ({"toolsListChanged": True, "logging": False}, True),
    ],
)
async def test_listen_stream_no_log_leak(
    listen_filter: dict[str, Any],
    expect_tools_changed: bool,
) -> None:
    manager = SubscriptionManager()
    stream_id, stream = await manager.open("sub-no-log", listen_filter)
    ack = await stream.__anext__()
    assert "logLevel" not in ack["params"]["notifications"]

    await manager.publish("notifications/message", {"level": "emergency", "data": "urgent"})
    await manager.publish("notifications/message", {"level": "trace", "data": "invalid-level"})
    await manager.publish("notifications/tools/list_changed", {})

    if expect_tools_changed:
        received = await asyncio.wait_for(stream.__anext__(), timeout=0.5)
        assert received["method"] == "notifications/tools/list_changed"
    else:
        assert manager._streams[stream_id].queue.empty()

    await manager.disconnect(stream_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("listen_filter", "expected_level", "dropped_level", "accepted_level"),
    [
        ({"logLevel": "warning"}, "warning", "notice", "warning"),
        ({"logging": True}, "notice", "info", "notice"),
        ({"logging": {}}, "notice", "info", "error"),
        ({"logging": {"level": "error"}}, "error", "warning", "error"),
        ({"logging": {"level": "error"}, "logLevel": "warning"}, "warning", "notice", "warning"),
    ],
)
async def test_listen_stream_logging_variants(
    listen_filter: dict[str, Any],
    expected_level: str,
    dropped_level: str,
    accepted_level: str,
) -> None:
    manager = SubscriptionManager(default_log_level="notice")
    stream_id, stream = await manager.open("sub-log", listen_filter)
    ack = await stream.__anext__()
    assert ack["params"]["notifications"] == {"logLevel": expected_level}

    await manager.publish("notifications/message", {"level": dropped_level, "data": "drop"})
    await manager.publish(
        "notifications/message",
        {"level": "emergency", "data": "session-only"},
        session_id="sess-a",
    )
    await manager.publish("notifications/message", {"level": "trace", "data": "unknown"})
    await manager.publish(
        "notifications/message",
        {"level": accepted_level, "data": {"ok": True}, "logger": "audit"},
    )

    event = await asyncio.wait_for(stream.__anext__(), timeout=0.5)
    assert event == {
        "jsonrpc": "2.0",
        "method": "notifications/message",
        "params": {
            "level": accepted_level,
            "data": {"ok": True},
            "logger": "audit",
            "_meta": {"io.modelcontextprotocol/subscriptionId": "sub-log"},
        },
    }
    assert manager._streams[stream_id].queue.empty()
    await manager.disconnect(stream_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_filter",
    [
        {"logging": None},
        {"logging": "debug"},
        {"logging": 123},
        {"logging": {"other": "value"}},
        {"logging": {"level": "fatal"}},
        {"logging": {"level": 7}},
        {"logLevel": "bogus"},
        {"logLevel": None},
        {"logging": {"level": "bad"}, "logLevel": "warning"},
    ],
)
async def test_listen_stream_invalid_logging_filters_raise_value_error(bad_filter: dict[str, Any]) -> None:
    manager = SubscriptionManager()
    with pytest.raises(ValueError, match="log"):
        await manager.open("sub-invalid", bad_filter)
    assert manager._streams == {}


@pytest.mark.asyncio
async def test_session_owner_isolation_and_close_clears_log_level() -> None:
    manager = SubscriptionManager()
    manager.set_session_log_level("sess-a", "error")
    manager.set_session_log_level("sess-b", "debug")

    a_id, a_stream = await manager.open_session_stream(session_id="sess-a", owner_id="user:alice")
    b_id, b_stream = await manager.open_session_stream(session_id="sess-b", owner_id="user:bob")

    await manager.publish(
        "notifications/message",
        {"level": "warning", "data": "below-error"},
        owner_id="user:alice",
        session_id="sess-a",
    )
    await manager.publish(
        "notifications/message",
        {"level": "info", "data": "global-alice-below-error"},
        owner_id="user:alice",
    )
    await manager.publish(
        "notifications/message",
        {"level": "emergency", "data": "wrong-session"},
        owner_id="user:alice",
        session_id="sess-other",
    )
    await manager.publish(
        "notifications/message",
        {
            "level": "critical",
            "data": "deliver-to-a",
            "logger": "db",
            "_meta": {"io.modelcontextprotocol/subscriptionId": "strip-me"},
        },
        owner_id="user:alice",
        session_id="sess-a",
    )

    delivered = await asyncio.wait_for(a_stream.__anext__(), timeout=0.5)
    assert delivered == {
        "jsonrpc": "2.0",
        "method": "notifications/message",
        "params": {"level": "critical", "data": "deliver-to-a", "logger": "db"},
    }
    assert "_meta" not in delivered["params"]
    assert manager._streams[a_id].queue.empty()
    assert manager._streams[b_id].queue.empty()

    await manager.close_session("sess-a")
    assert "sess-a" not in manager._session_log_levels
    assert "sess-b" in manager._session_log_levels
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(a_stream.__anext__(), timeout=0.5)

    await manager.publish("notifications/tools/list_changed", {}, owner_id="user:bob")
    bob_sentinel = await asyncio.wait_for(b_stream.__anext__(), timeout=0.5)
    assert bob_sentinel["method"] == "notifications/tools/list_changed"
    await manager.disconnect(b_id)


@pytest.mark.asyncio
async def test_progress_never_leaks_to_listen_or_other_sessions() -> None:
    manager = SubscriptionManager()
    listen_id, listen_stream = await manager.open("sub-task", {"taskIds": ["t1"], "logLevel": "debug"})
    sess_a_id, sess_a_stream = await manager.open_session_stream(session_id="sess-a")
    sess_b_id, _sess_b_stream = await manager.open_session_stream(session_id="sess-b")
    await listen_stream.__anext__()

    await manager.publish("notifications/progress", {"progressToken": "p0", "progress": 1})
    await manager.publish(
        "notifications/progress",
        {"progressToken": "p1", "progress": 5, "total": 10},
        session_id="sess-a",
    )

    progress_frame = await asyncio.wait_for(sess_a_stream.__anext__(), timeout=0.5)
    assert progress_frame == {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": {"progressToken": "p1", "progress": 5, "total": 10},
    }
    assert manager._streams[listen_id].queue.empty()
    assert manager._streams[sess_b_id].queue.empty()

    await manager.disconnect(listen_id)
    await manager.disconnect(sess_a_id)
    await manager.disconnect(sess_b_id)


@pytest.mark.asyncio
async def test_channels_plugin_wait_published_session_fanout() -> None:
    channels = ChannelsPlugin(MemoryChannelsBackend(), channels=["litestar-mcp-subscriptions"])
    async with channels:
        publisher = SubscriptionManager(channels=channels)
        receiver = SubscriptionManager(channels=channels)
        publisher.start()
        receiver.start()
        for _ in range(400):
            if len(channels._channels["litestar-mcp-subscriptions"]) >= 2:
                break
            await asyncio.sleep(0.005)
        else:
            raise asyncio.TimeoutError

        s1_id, s1_stream = await receiver.open_session_stream(session_id="sess-1", owner_id="user:alice")
        s2_id, _s2_stream = await receiver.open_session_stream(session_id="sess-2", owner_id="user:alice")
        bob_id, _bob_stream = await receiver.open_session_stream(session_id="sess-1", owner_id="user:bob")

        await publisher.publish(
            "notifications/message",
            {"level": "error", "data": "boom"},
            owner_id="user:alice",
            session_id="sess-1",
        )

        event = await asyncio.wait_for(s1_stream.__anext__(), timeout=1.0)
        assert event == {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": {"level": "error", "data": "boom"},
        }
        assert "owner_id" not in event["params"]
        assert "session_id" not in event["params"]
        assert receiver._streams[s2_id].queue.empty()
        assert receiver._streams[bob_id].queue.empty()

        await receiver.disconnect(s1_id)
        await receiver.disconnect(s2_id)
        await receiver.disconnect(bob_id)
        await publisher.close_all()
        await receiver.close_all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("subscription_filter", "expected_delivered"),
    [
        ({"skillsListChanged": True}, True),
        ({"toolsListChanged": True}, False),
    ],
)
async def test_skills_list_changed_subscription_filter(
    subscription_filter: dict[str, Any],
    expected_delivered: bool,
) -> None:
    """Verify notifications/skills/list_changed requires skillsListChanged on listen streams."""
    manager = SubscriptionManager()
    stream_id, stream = await manager.open("sub-skills", subscription_filter)
    await stream.__anext__()

    await manager.publish("notifications/skills/list_changed", {})

    if expected_delivered:
        notification = await asyncio.wait_for(stream.__anext__(), 0.2)
        assert notification["method"] == "notifications/skills/list_changed"
        assert notification["params"]["_meta"]["io.modelcontextprotocol/subscriptionId"] == "sub-skills"
    else:
        assert manager._streams[stream_id].queue.empty()

    await manager.disconnect(stream_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("notifications_filter", "expected_delivered"),
    [
        (None, True),
        ({"skillsListChanged": False}, False),
    ],
)
async def test_open_session_stream_skills_list_changed_defaults_and_override(
    notifications_filter: dict[str, Any] | None,
    expected_delivered: bool,
) -> None:
    """Verify session streams seed skillsListChanged=True and respect explicit False override."""
    manager = SubscriptionManager()
    stream_id, stream = await manager.open_session_stream(
        session_id="sess-skills",
        owner_id="user:alice",
        notifications=notifications_filter,
    )
    state = manager._streams[stream_id]

    await manager.publish("notifications/skills/list_changed", {})

    if expected_delivered:
        event = await asyncio.wait_for(stream.__anext__(), 0.2)
        assert event == {"jsonrpc": "2.0", "method": "notifications/skills/list_changed", "params": {}}
    else:
        assert state.queue.empty()

    await manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_registry_notify_skills_list_changed_emits_skills_and_resources_notifications() -> None:
    """Verify Registry.notify_skills_list_changed emits both skills and resources list_changed events."""
    manager = SubscriptionManager()
    registry = Registry()
    registry.set_subscription_manager(manager)
    stream_id, stream = await manager.open_session_stream(session_id="sess-notify")

    await registry.notify_skills_list_changed()

    first = await asyncio.wait_for(stream.__anext__(), 0.2)
    second = await asyncio.wait_for(stream.__anext__(), 0.2)
    assert [first["method"], second["method"]] == [
        "notifications/skills/list_changed",
        "notifications/resources/list_changed",
    ]
    await manager.disconnect(stream_id)


@pytest.mark.asyncio
async def test_registry_notify_skills_list_changed_unbound_is_noop() -> None:
    """Verify Registry.notify_skills_list_changed returns cleanly when unbound."""
    registry = Registry()
    assert registry.has_subscription_manager is False
    await registry.notify_skills_list_changed()
