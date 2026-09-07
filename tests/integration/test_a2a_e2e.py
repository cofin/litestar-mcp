"""End-to-end coverage of the A2A adapter through the official client, raw JSON-RPC, and raw ASGI."""

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator, Iterator
from typing import Any, cast

import anyio
import anyio.lowlevel
import pytest
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import RequestHandler
from a2a.server.request_handlers.response_helpers import build_error_response
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTaskPushNotificationConfigsResponse,
    ListTasksRequest,
    ListTasksResponse,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import (
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    MethodNotFoundError,
    TaskNotFoundError,
)
from litestar import Litestar
from litestar.exceptions import ImproperlyConfiguredException, LitestarException
from litestar.plugins import InitPluginProtocol
from litestar.testing import AsyncTestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.a2a import A2AConfig, LitestarA2A
from litestar_mcp.mcp.routes import MCP_PROTOCOL_VERSION

pytestmark = pytest.mark.integration


def make_card(path: str = "/a2a", name: str = "Test agent") -> AgentCard:
    """Build an agent card whose JSONRPC interface matches the mounted path."""
    return AgentCard(
        name=name,
        description="Test agent",
        version="1.0.0",
        capabilities=AgentCapabilities(extended_agent_card=True),
        supported_interfaces=[
            AgentInterface(url=f"https://example.com{path}", protocol_binding="JSONRPC", protocol_version="1.0")
        ],
    )


class RecordingHandler(RequestHandler):
    """Stateful request handler that stores tasks and push configs in memory."""

    def __init__(self) -> None:
        self.tasks: dict[str, Task] = {}
        self.push_configs: dict[tuple[str, str], TaskPushNotificationConfig] = {}
        self.contexts: list[ServerCallContext] = []
        self.closed = False

    def _record(self, context: ServerCallContext | None) -> None:
        if context is not None:
            self.contexts.append(context)

    async def aclose(self) -> None:
        self.closed = True

    async def on_message_send(self, params: Any, context: ServerCallContext | None = None) -> Any:
        self._record(context)
        task = Task(id="task-1", context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))
        self.tasks[task.id] = task
        return task

    async def on_message_send_stream(
        self, params: Any, context: ServerCallContext | None = None
    ) -> AsyncGenerator[Any, None]:
        self._record(context)
        yield Message(message_id="chunk", role=Role.ROLE_AGENT, parts=[Part(text="one")])

    async def on_get_task(self, params: Any, context: ServerCallContext | None = None) -> Any:
        self._record(context)
        return self.tasks.get(params.id)

    async def on_list_tasks(self, params: Any, context: ServerCallContext | None = None) -> Any:
        self._record(context)
        tasks = list(self.tasks.values())
        return ListTasksResponse(tasks=tasks, page_size=len(tasks), total_size=len(tasks))

    async def on_cancel_task(self, params: Any, context: ServerCallContext | None = None) -> Any:
        self._record(context)
        task = self.tasks.get(params.id)
        if task is None:
            return None
        task.status.state = TaskState.TASK_STATE_CANCELED
        return task

    async def on_subscribe_to_task(
        self, params: Any, context: ServerCallContext | None = None
    ) -> AsyncGenerator[Any, None]:
        self._record(context)
        yield TaskStatusUpdateEvent(
            task_id=params.id, context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_WORKING)
        )
        yield TaskStatusUpdateEvent(
            task_id=params.id, context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)
        )

    async def on_create_task_push_notification_config(
        self, params: Any, context: ServerCallContext | None = None
    ) -> Any:
        self._record(context)
        self.push_configs[(params.task_id, params.id)] = params
        return params

    async def on_get_task_push_notification_config(self, params: Any, context: ServerCallContext | None = None) -> Any:
        self._record(context)
        config = self.push_configs.get((params.task_id, params.id))
        if config is None:
            raise TaskNotFoundError
        return config

    async def on_list_task_push_notification_configs(
        self, params: Any, context: ServerCallContext | None = None
    ) -> Any:
        self._record(context)
        configs = [config for (task_id, _), config in self.push_configs.items() if task_id == params.task_id]
        return ListTaskPushNotificationConfigsResponse(configs=configs)

    async def on_delete_task_push_notification_config(
        self, params: Any, context: ServerCallContext | None = None
    ) -> Any:
        self._record(context)
        self.push_configs.pop((params.task_id, params.id), None)

    async def on_get_extended_agent_card(self, params: Any, context: ServerCallContext | None = None) -> Any:
        self._record(context)
        return make_card(name="Extended agent")


class RaisingHandler(RecordingHandler):
    """Handler whose task lookup fails with an unexpected exception."""

    async def on_get_task(self, params: Any, context: ServerCallContext | None = None) -> Any:
        msg = "boom"
        raise RuntimeError(msg)


def _transport(http_client: AsyncTestClient[Any], card: AgentCard) -> JsonRpcTransport:
    """Bind the official JSON-RPC client to a Litestar test client."""
    http_client.headers["A2A-Version"] = "1.0"
    return JsonRpcTransport(http_client, card, "http://testserver.local/a2a")


def _rpc_body(method: str, params: dict[str, Any], request_id: int = 1) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}


def send_payload(method: str = "SendMessage") -> dict[str, Any]:
    """Build a JSON-RPC envelope carrying a user message."""
    return _rpc_body(method, {"message": {"messageId": "request-1", "role": "ROLE_USER", "parts": [{"text": "hello"}]}})


async def _post_a2a(client: AsyncTestClient[Any], body: Any) -> Any:
    response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=body)
    return response.json()


async def _mcp_request(client: AsyncTestClient[Any], method: str, params: dict[str, Any]) -> Any:
    """Post an MCP 2026-07-28 request with the required ``_meta`` and headers."""
    request_params = dict(params)
    request_params["_meta"] = {
        "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {"name": "tests", "version": "1"},
    }
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
        "Mcp-Method": method,
    }
    return await client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": request_params}, headers=headers
    )


def _user_message() -> Message:
    return Message(message_id="official-request", role=Role.ROLE_USER, parts=[Part(text="hello")])


@pytest.mark.anyio
async def test_task_lifecycle_over_official_client() -> None:
    card = make_card()
    app = Litestar(plugins=[LitestarA2A(card, RecordingHandler())])

    async with AsyncTestClient(app=app) as http_client:
        transport = _transport(http_client, card)
        sent = await transport.send_message(SendMessageRequest(message=_user_message()))
        fetched = await transport.get_task(GetTaskRequest(id=sent.task.id))
        listed = await transport.list_tasks(ListTasksRequest())
        cancelled = await transport.cancel_task(CancelTaskRequest(id=sent.task.id))

    assert sent.WhichOneof("payload") == "task"
    assert fetched.id == sent.task.id
    assert [task.id for task in listed.tasks] == [sent.task.id]
    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED


@pytest.mark.anyio
async def test_subscribe_streams_task_updates() -> None:
    card = make_card()
    app = Litestar(plugins=[LitestarA2A(card, RecordingHandler())])

    async with AsyncTestClient(app=app) as http_client:
        transport = _transport(http_client, card)
        events = [event async for event in transport.subscribe(SubscribeToTaskRequest(id="task-1"))]

    assert [event.WhichOneof("payload") for event in events] == ["status_update", "status_update"]
    assert [event.status_update.status.state for event in events] == [
        TaskState.TASK_STATE_WORKING,
        TaskState.TASK_STATE_COMPLETED,
    ]


@pytest.mark.anyio
async def test_push_notification_config_crud() -> None:
    card = make_card()
    app = Litestar(plugins=[LitestarA2A(card, RecordingHandler())])
    config = TaskPushNotificationConfig(id="cfg-1", task_id="task-1", url="https://hooks.example/notify")

    async with AsyncTestClient(app=app) as http_client:
        transport = _transport(http_client, card)
        created = await transport.create_task_push_notification_config(config)
        fetched = await transport.get_task_push_notification_config(
            GetTaskPushNotificationConfigRequest(task_id="task-1", id="cfg-1")
        )
        listed = await transport.list_task_push_notification_configs(
            ListTaskPushNotificationConfigsRequest(task_id="task-1")
        )
        await transport.delete_task_push_notification_config(
            DeleteTaskPushNotificationConfigRequest(task_id="task-1", id="cfg-1")
        )
        after_delete = await _post_a2a(
            http_client, _rpc_body("GetTaskPushNotificationConfig", {"taskId": "task-1", "id": "cfg-1"})
        )

    assert created.url == config.url
    assert fetched.id == "cfg-1"
    assert [item.id for item in listed.configs] == ["cfg-1"]
    assert after_delete["error"]["code"] == build_error_response(1, TaskNotFoundError())["error"]["code"]


@pytest.mark.anyio
async def test_extended_agent_card_via_official_client() -> None:
    card = make_card()
    app = Litestar(plugins=[LitestarA2A(card, RecordingHandler())])

    async with AsyncTestClient(app=app) as http_client:
        transport = _transport(http_client, card)
        extended = await transport.get_extended_agent_card(GetExtendedAgentCardRequest())

    assert extended.name == "Extended agent"
    assert extended.name != card.name


@pytest.mark.anyio
async def test_unknown_task_maps_to_task_not_found() -> None:
    app = Litestar(plugins=[LitestarA2A(make_card(), RecordingHandler())])

    async with AsyncTestClient(app=app) as client:
        envelope = await _post_a2a(client, _rpc_body("GetTask", {"id": "missing"}))

    assert envelope == build_error_response(1, TaskNotFoundError())


@pytest.mark.anyio
async def test_invalid_params_are_rejected() -> None:
    app = Litestar(plugins=[LitestarA2A(make_card(), RecordingHandler())])

    async with AsyncTestClient(app=app) as client:
        envelope = await _post_a2a(client, _rpc_body("GetTask", {"id": 5}))

    assert envelope["error"]["code"] == build_error_response(1, InvalidParamsError())["error"]["code"]
    assert envelope["error"]["data"]


@pytest.mark.anyio
async def test_unknown_method_and_batch_are_rejected() -> None:
    app = Litestar(plugins=[LitestarA2A(make_card(), RecordingHandler())])

    async with AsyncTestClient(app=app) as client:
        unknown = await _post_a2a(client, _rpc_body("DoesNotExist", {}))
        batch = await _post_a2a(client, [send_payload()])

    assert unknown["error"]["code"] == build_error_response(1, MethodNotFoundError())["error"]["code"]
    assert batch["error"]["code"] == build_error_response(1, InvalidRequestError())["error"]["code"]


@pytest.mark.anyio
async def test_handler_exception_maps_to_internal_error() -> None:
    app = Litestar(plugins=[LitestarA2A(make_card(), RaisingHandler())])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=_rpc_body("GetTask", {"id": "x"}))

    envelope = response.json()
    assert envelope["error"]["code"] == build_error_response(1, InternalError())["error"]["code"]
    assert "boom" not in response.text
    assert "Traceback" not in response.text


@pytest.mark.anyio
async def test_stream_disconnect_closes_handler_generator() -> None:
    release = asyncio.Event()
    closed_generator = asyncio.Event()

    class BlockingHandler(RecordingHandler):
        """Handler whose stream blocks after its first event until released."""

        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            try:
                yield Message(message_id="chunk", role=Role.ROLE_AGENT, parts=[Part(text="one")])
                await release.wait()
            finally:
                closed_generator.set()

    app = Litestar(plugins=[LitestarA2A(make_card(), BlockingHandler())])
    body = json.dumps(send_payload("SendStreamingMessage")).encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/a2a",
        "raw_path": b"/a2a",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1),
        "headers": [
            (b"host", b"testserver"),
            (b"content-type", b"application/json"),
            (b"a2a-version", b"1.0"),
            (b"content-length", str(len(body)).encode()),
        ],
    }
    first_chunk = asyncio.Event()
    messages: list[dict[str, Any]] = []

    async def receive() -> "dict[str, Any]":
        if not messages:
            messages.append({"type": "http.request"})
            return {"type": "http.request", "body": body, "more_body": False}
        await first_chunk.wait()
        return {"type": "http.disconnect"}

    async def send(message: "dict[str, Any]") -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_chunk.set()

    with anyio.fail_after(2):
        await app(cast("Any", scope), cast("Any", receive), cast("Any", send))

    assert closed_generator.is_set()


@pytest.mark.anyio
async def test_tenant_from_params_reaches_context() -> None:
    card = make_card()
    handler = RecordingHandler()
    app = Litestar(plugins=[LitestarA2A(card, handler)])

    async with AsyncTestClient(app=app) as http_client:
        transport = _transport(http_client, card)
        await transport.send_message(SendMessageRequest(message=_user_message()))
        await transport.get_task(GetTaskRequest(tenant="acme", id="task-1"))
        await transport.get_task(GetTaskRequest(id="task-1"))

    assert [context.tenant for context in handler.contexts] == ["", "acme", ""]


def _plugin_orders(card: AgentCard, handler: RequestHandler, mcp: LitestarMCP) -> dict[str, list[InitPluginProtocol]]:
    a2a = LitestarA2A(card, handler)
    return {"mcp_first": [mcp, a2a], "a2a_first": [a2a, mcp]}


@pytest.mark.anyio
@pytest.mark.parametrize("order", ["mcp_first", "a2a_first"])
async def test_mcp_and_a2a_coexist_in_both_plugin_orders(order: str) -> None:
    card = make_card()
    app = Litestar(plugins=_plugin_orders(card, RecordingHandler(), LitestarMCP())[order])

    async with AsyncTestClient(app=app) as client:
        discover = await _mcp_request(client, "server/discover", {})
        card_response = await client.get("/.well-known/agent-card.json")
        sent = await _post_a2a(client, send_payload())

    assert discover.status_code == 200
    assert "result" in discover.json()
    assert card_response.status_code == 200
    assert card_response.json()["name"] == card.name
    assert "result" in sent
    paths = app.openapi_schema.paths
    assert paths is not None
    assert "/mcp" not in paths
    assert "/a2a" not in paths


@pytest.mark.parametrize("order", ["mcp_first", "a2a_first"])
def test_mcp_route_does_not_collide_with_a2a_path(order: str) -> None:
    plugins = _plugin_orders(make_card(), RecordingHandler(), LitestarMCP(MCPConfig(base_path="/a2a")))[order]

    if order == "mcp_first":
        with pytest.raises(ValueError, match="A2A route collision: /a2a"):
            Litestar(plugins=plugins)
    else:
        with pytest.raises(ImproperlyConfiguredException, match="Handler already registered"):
            Litestar(plugins=plugins)


def _stream_scope() -> dict[str, Any]:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "POST",
        "path": "/a2a",
        "raw_path": b"/a2a",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "server": ("testserver", 80),
        "client": ("testclient", 1),
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"), (b"a2a-version", b"1.0")],
    }


@pytest.mark.anyio
@pytest.mark.parametrize("position", ["prefetch", "next", "send"])
async def test_disconnect_awaits_handler_finalization(position: str) -> None:
    entered = asyncio.Event()
    next_started = asyncio.Event()
    first_sent = asyncio.Event()
    finalized = asyncio.Event()
    tasks: list[asyncio.Task[Any] | None] = []

    class Handler(RecordingHandler):
        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            tasks.append(asyncio.current_task())
            try:
                entered.set()
                if position == "prefetch":
                    await asyncio.Event().wait()
                yield Message(message_id="chunk", role=Role.ROLE_AGENT, parts=[Part(text="one")])
                tasks.append(asyncio.current_task())
                next_started.set()
                await asyncio.Event().wait()
            finally:
                await anyio.lowlevel.checkpoint()
                await anyio.lowlevel.checkpoint()
                tasks.append(asyncio.current_task())
                finalized.set()

    app = Litestar(plugins=[LitestarA2A(make_card(), Handler())])
    requested = False
    messages: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": json.dumps(send_payload("SendStreamingMessage")).encode()}
        await {"prefetch": entered, "next": next_started, "send": first_sent}[position].wait()
        if position == "next":
            await first_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        messages.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            first_sent.set()
            if position == "send":
                await asyncio.Event().wait()

    with anyio.fail_after(1):
        await app(cast("Any", _stream_scope()), cast("Any", receive), cast("Any", send))

    assert finalized.is_set()
    assert len(set(tasks)) == 1
    assert all(task is not None and task.done() for task in tasks)
    if position == "prefetch":
        assert not messages


@pytest.mark.anyio
@pytest.mark.parametrize(
    "terminal_message",
    [{"type": "http.disconnect"}, {"type": "http.request", "body": b"unexpected"}, {"type": "websocket.disconnect"}],
)
async def test_prefetch_disconnect_wins_ready_first_event(
    terminal_message: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    yielded = asyncio.Event()
    finalized = asyncio.Event()

    class Handler(RecordingHandler):
        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            try:
                yielded.set()
                yield Message(message_id="first", role=Role.ROLE_AGENT, parts=[Part(text="first")])
                await asyncio.Event().wait()
            finally:
                await anyio.lowlevel.checkpoint()
                await anyio.lowlevel.checkpoint()
                finalized.set()

    app = Litestar(plugins=[LitestarA2A(make_card(), Handler())], logging_config=None)
    messages: Iterator[dict[str, Any]] = iter(
        [
            {"type": "http.request", "body": json.dumps(send_payload("SendStreamingMessage")).encode()},
            {"type": "http.request", "body": b"", "more_body": False},
            terminal_message,
        ]
    )
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return next(messages)

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    with anyio.fail_after(1):
        await app(cast("Any", _stream_scope()), cast("Any", receive), cast("Any", send))

    assert yielded.is_set()
    assert finalized.is_set()
    assert not sent
    if terminal_message["type"] != "http.disconnect":
        assert "Unexpected ASGI message" in caplog.text


@pytest.mark.anyio
async def test_send_failure_completes_async_handler_cleanup() -> None:
    finalized = asyncio.Event()

    class Handler(RecordingHandler):
        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            try:
                yield Message(message_id="first", role=Role.ROLE_AGENT, parts=[Part(text="first")])
                await asyncio.Event().wait()
            finally:
                await anyio.lowlevel.checkpoint()
                await anyio.lowlevel.checkpoint()
                finalized.set()

    app = Litestar(plugins=[LitestarA2A(make_card(), Handler())])
    requested = False

    async def receive() -> dict[str, Any]:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": json.dumps(send_payload("SendStreamingMessage")).encode()}
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body":
            msg = "client write failed"
            raise OSError(msg)

    with anyio.fail_after(1), pytest.raises(LitestarException, match="Exception caught after response started"):
        await app(cast("Any", _stream_scope()), cast("Any", receive), cast("Any", send))

    assert finalized.is_set()


@pytest.mark.anyio
async def test_stream_cleanup_deadline_reports_incomplete_finalization(caplog: pytest.LogCaptureFixture) -> None:
    first_sent = asyncio.Event()
    release_cleanup = asyncio.Event()
    finalized = asyncio.Event()
    producers: list[asyncio.Task[Any]] = []

    class Handler(RecordingHandler):
        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            current = asyncio.current_task()
            assert current is not None
            producers.append(current)
            try:
                yield Message(message_id="first", role=Role.ROLE_AGENT, parts=[Part(text="first")])
                await asyncio.Event().wait()
            finally:
                while not release_cleanup.is_set():
                    with contextlib.suppress(asyncio.CancelledError):
                        await release_cleanup.wait()
                finalized.set()

    config = A2AConfig(stream_cleanup_timeout=0.01)
    app = Litestar(plugins=[LitestarA2A(make_card(), Handler(), config)], logging_config=None)
    requested = False

    async def receive() -> dict[str, Any]:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": json.dumps(send_payload("SendStreamingMessage")).encode()}
        await first_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_sent.set()
            await asyncio.Event().wait()

    try:
        with anyio.fail_after(1):
            await app(cast("Any", _stream_scope()), cast("Any", receive), cast("Any", send))
        assert "Stream producer cleanup incomplete" in caplog.text
        assert not finalized.is_set()
    finally:
        release_cleanup.set()
        with anyio.fail_after(1):
            await asyncio.gather(*producers, return_exceptions=True)
    assert finalized.is_set()


@pytest.mark.anyio
async def test_external_cancellation_during_prefetch_propagates_after_cleanup() -> None:
    entered = asyncio.Event()
    finalized = asyncio.Event()
    watcher_finalized = asyncio.Event()

    class Handler(RecordingHandler):
        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            try:
                entered.set()
                await asyncio.Event().wait()
                yield
            finally:
                await anyio.lowlevel.checkpoint()
                await anyio.lowlevel.checkpoint()
                finalized.set()

    app = Litestar(plugins=[LitestarA2A(make_card(), Handler())])
    requested = False
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": json.dumps(send_payload("SendStreamingMessage")).encode()}
        try:
            await asyncio.Event().wait()
            return {"type": "http.disconnect"}
        finally:
            watcher_finalized.set()

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    task = asyncio.create_task(app(cast("Any", _stream_scope()), cast("Any", receive), cast("Any", send)))
    with anyio.fail_after(1):
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert finalized.is_set()
    assert watcher_finalized.is_set()
    assert not sent


@pytest.mark.anyio
async def test_prefetch_watcher_exits_before_native_sse_reads_disconnect() -> None:
    watcher_entered = asyncio.Event()
    watcher_exited = asyncio.Event()
    first_sent = asyncio.Event()
    calls = 0
    active_reads = 0

    class Handler(RecordingHandler):
        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            await watcher_entered.wait()
            yield Message(message_id="first", role=Role.ROLE_AGENT, parts=[Part(text="first")])
            await asyncio.Event().wait()

    app = Litestar(plugins=[LitestarA2A(make_card(), Handler())])

    async def receive() -> dict[str, Any]:
        nonlocal calls, active_reads
        active_reads += 1
        assert active_reads == 1
        calls += 1
        try:
            if calls == 1:
                return {"type": "http.request", "body": json.dumps(send_payload("SendStreamingMessage")).encode()}
            if calls == 2:
                watcher_entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    watcher_exited.set()
            assert watcher_exited.is_set()
            await first_sent.wait()
            return {"type": "http.disconnect"}
        finally:
            active_reads -= 1

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            first_sent.set()

    with anyio.fail_after(1):
        await app(cast("Any", _stream_scope()), cast("Any", receive), cast("Any", send))
    assert calls == 3
    assert active_reads == 0
