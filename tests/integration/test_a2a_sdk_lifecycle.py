"""Exercise the native transport with the official SDK task engine and client."""

import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import aclosing, asynccontextmanager
from typing import Any, cast
from uuid import uuid4

import anyio
import httpx
import pytest
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import Event, EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    PushNotificationEvent,
    PushNotificationSender,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
    Artifact,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    Message,
    Part,
    Role,
    SendMessageConfiguration,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import InvalidParamsError, PushNotificationNotSupportedError, UnsupportedOperationError
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
from litestar import Litestar

from litestar_mcp.a2a import LitestarA2A

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


def make_card(*, streaming: bool = True, push: bool = False, extended: bool = False) -> AgentCard:
    return AgentCard(
        name="Lifecycle agent",
        description="An executor controlled by test events",
        version="1.0.0",
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[AgentSkill(id="lifecycle", name="Lifecycle", description="Complete or continue a task", tags=["test"])],
        capabilities=AgentCapabilities(streaming=streaming, push_notifications=push, extended_agent_card=extended),
        supported_interfaces=[
            AgentInterface(url="http://testserver.local/a2a", protocol_binding="JSONRPC", protocol_version="1.0")
        ],
    )


def message_request(text: str = "complete", *, task: Task | None = None, immediate: bool = False) -> SendMessageRequest:
    message = Message(message_id=str(uuid4()), role=Role.ROLE_USER, parts=[Part(text=text)])
    if task is not None:
        message.task_id = task.id
        message.context_id = task.context_id
    return SendMessageRequest(message=message, configuration=SendMessageConfiguration(return_immediately=immediate))


class LifecycleExecutor(AgentExecutor):
    """Publish valid SDK events while exposing only execution and cancellation signals."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.finished = asyncio.Event()
        self.cancelled = asyncio.Event()
        self.requests: list[RequestContext] = []

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.requests.append(context)
        assert context.task_id is not None
        assert context.context_id is not None
        assert context.message is not None
        text = context.get_user_input()
        if text == "message":
            await event_queue.enqueue_event(
                Message(message_id=str(uuid4()), role=Role.ROLE_AGENT, parts=[Part(text="Hello")])
            )
            return
        if context.current_task is None:
            await event_queue.enqueue_event(
                Task(
                    id=context.task_id,
                    context_id=context.context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                    history=[context.message],
                )
            )
        else:
            await event_queue.enqueue_event(self.status(context, TaskState.TASK_STATE_WORKING))
        self.started.set()
        try:
            if text == "wait":
                await self.release.wait()
            if text == "ask":
                update = self.status(context, TaskState.TASK_STATE_INPUT_REQUIRED)
                update.status.message.CopyFrom(
                    Message(message_id=str(uuid4()), role=Role.ROLE_AGENT, parts=[Part(text="Which option?")])
                )
                await event_queue.enqueue_event(update)
                return
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    artifact=Artifact(artifact_id="answer", parts=[Part(text="Done")]),
                    last_chunk=True,
                )
            )
            await event_queue.enqueue_event(self.status(context, TaskState.TASK_STATE_COMPLETED))
        finally:
            self.finished.set()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        self.cancelled.set()
        await event_queue.enqueue_event(self.status(context, TaskState.TASK_STATE_CANCELED))

    @staticmethod
    def status(context: RequestContext, state: "TaskState") -> TaskStatusUpdateEvent:
        return TaskStatusUpdateEvent(
            task_id=context.task_id, context_id=context.context_id, status=TaskStatus(state=state)
        )


class RecordingPushSender(PushNotificationSender):
    def __init__(self) -> None:
        self.events: list[tuple[str, PushNotificationEvent]] = []

    async def send_notification(self, task_id: str, event: PushNotificationEvent) -> None:
        self.events.append((task_id, event))


class ObservedHandler(DefaultRequestHandler):
    """Observe public subscription and shutdown boundaries without replacing SDK behavior."""

    def __init__(self, executor: LifecycleExecutor, card: AgentCard) -> None:
        super().__init__(
            agent_executor=executor,
            task_store=InMemoryTaskStore(),
            agent_card=card,
            push_config_store=InMemoryPushNotificationConfigStore() if card.capabilities.push_notifications else None,
            push_sender=RecordingPushSender() if card.capabilities.push_notifications else None,
            extended_agent_card=card if card.capabilities.extended_agent_card else None,
        )
        self.subscribed = asyncio.Event()
        self.closed = asyncio.Event()

    async def on_subscribe_to_task(
        self, params: SubscribeToTaskRequest, context: ServerCallContext
    ) -> AsyncGenerator[Event, None]:
        async with aclosing(super().on_subscribe_to_task(params, context)) as stream:
            async for event in stream:
                self.subscribed.set()
                yield event

    async def aclose(self) -> None:
        await super().aclose()
        self.closed.set()


@asynccontextmanager
async def sdk_client(
    executor: LifecycleExecutor, card: AgentCard | None = None
) -> AsyncGenerator[tuple[JsonRpcTransport, ObservedHandler], None]:
    card = card or make_card()
    handler = ObservedHandler(executor, card)
    app = Litestar(plugins=[LitestarA2A(card, handler)])
    async with (
        app.lifespan(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cast("Any", app)), base_url="http://testserver.local"
        ) as client,
    ):
        client.headers["A2A-Version"] = "1.0"
        yield JsonRpcTransport(client, card, "http://testserver.local/a2a"), handler


async def test_sdk_immediate_message_and_blocking_task() -> None:
    executor = LifecycleExecutor()
    async with sdk_client(executor) as (client, _):
        reply = await client.send_message(message_request("message"))
        assert reply.WhichOneof("payload") == "message"
        assert reply.message.parts[0].text == "Hello"
        result = await client.send_message(message_request())
        assert result.task.status.state == TaskState.TASK_STATE_COMPLETED
        assert result.task.artifacts[0].parts[0].text == "Done"
        stored = await client.get_task(GetTaskRequest(id=result.task.id))
        assert stored == result.task


async def test_sdk_streams_task_artifact_and_terminal_status() -> None:
    async with sdk_client(LifecycleExecutor()) as (client, _):
        events = [event async for event in client.send_message_streaming(message_request())]
        assert [event.WhichOneof("payload") for event in events] == ["task", "artifact_update", "status_update"]
        task_id = events[0].task.id
        assert events[1].artifact_update.task_id == task_id
        assert events[1].artifact_update.last_chunk
        assert events[2].status_update.task_id == task_id
        assert events[2].status_update.status.state == TaskState.TASK_STATE_COMPLETED


async def test_sdk_input_required_continuation_and_history_presence() -> None:
    executor = LifecycleExecutor()
    async with sdk_client(executor) as (client, _):
        initial = (await client.send_message(message_request("ask"))).task
        assert initial.status.state == TaskState.TASK_STATE_INPUT_REQUIRED
        completed = (await client.send_message(message_request("answer", task=initial))).task
        assert completed.id == initial.id
        assert completed.context_id == initial.context_id
        assert completed.status.state == TaskState.TASK_STATE_COMPLETED
        assert executor.requests[-1].current_task is not None
        full = await client.get_task(GetTaskRequest(id=initial.id))
        empty = await client.get_task(GetTaskRequest(id=initial.id, history_length=0))
        recent = await client.get_task(GetTaskRequest(id=initial.id, history_length=1))
        assert [message.parts[0].text for message in full.history] == ["ask", "Which option?", "answer"]
        assert not empty.history
        assert list(recent.history) == list(full.history[-1:])
        assert len((await client.get_task(GetTaskRequest(id=initial.id))).history) == 3


async def test_sdk_list_filters_pagination_and_artifact_projection() -> None:
    async with sdk_client(LifecycleExecutor()) as (client, _):
        completed = [(await client.send_message(message_request())).task for _ in range(2)]
        interrupted = (await client.send_message(message_request("ask"))).task
        first = await client.list_tasks(ListTasksRequest(status=TaskState.TASK_STATE_COMPLETED, page_size=1))
        second = await client.list_tasks(
            ListTasksRequest(status=TaskState.TASK_STATE_COMPLETED, page_size=1, page_token=first.next_page_token)
        )
        assert first.page_size == second.page_size == 1
        assert first.total_size == second.total_size == 2
        assert first.next_page_token and not second.next_page_token
        assert {first.tasks[0].id, second.tasks[0].id} == {task.id for task in completed}
        assert not first.tasks[0].artifacts
        selected = await client.list_tasks(
            ListTasksRequest(context_id=completed[0].context_id, history_length=0, include_artifacts=True)
        )
        assert [task.id for task in selected.tasks] == [completed[0].id]
        assert selected.tasks[0].artifacts and not selected.tasks[0].history
        waiting = await client.list_tasks(ListTasksRequest(status=TaskState.TASK_STATE_INPUT_REQUIRED))
        assert [task.id for task in waiting.tasks] == [interrupted.id]


@pytest.mark.parametrize("cancel", [False, True])
async def test_sdk_return_immediately_and_live_subscription(cancel: bool) -> None:
    executor = LifecycleExecutor()
    async with sdk_client(executor) as (client, handler):
        with anyio.fail_after(3):
            sent = (await client.send_message(message_request("wait", immediate=True))).task
            assert sent.status.state == TaskState.TASK_STATE_WORKING
            assert executor.started.is_set() and not executor.finished.is_set()
            events: list[Any] = []

            async def subscribe() -> None:
                events.extend([event async for event in client.subscribe(SubscribeToTaskRequest(id=sent.id))])

            async with anyio.create_task_group() as group:
                group.start_soon(subscribe)
                await handler.subscribed.wait()
                if cancel:
                    cancelled = await client.cancel_task(CancelTaskRequest(id=sent.id))
                    assert cancelled.status.state == TaskState.TASK_STATE_CANCELED
                else:
                    executor.release.set()
            expected = TaskState.TASK_STATE_CANCELED if cancel else TaskState.TASK_STATE_COMPLETED
            assert events[0].task.id == sent.id
            assert events[-1].status_update.status.state == expected
            assert executor.cancelled.is_set() == cancel
            assert executor.finished.is_set()
            assert (await client.get_task(GetTaskRequest(id=sent.id))).status.state == expected


async def test_sdk_shutdown_drains_ongoing_executor_without_explicit_cancel() -> None:
    executor = LifecycleExecutor()
    with anyio.fail_after(3):
        async with sdk_client(executor) as (client, handler):
            await client.send_message(message_request("wait", immediate=True))
            assert not executor.finished.is_set()
        assert handler.closed.is_set()
        assert executor.finished.is_set()
        assert not executor.cancelled.is_set()


@pytest.mark.parametrize("operation", ["stream", "subscribe", "push", "extended"])
async def test_sdk_refuses_unadvertised_capabilities(operation: str) -> None:
    executor = LifecycleExecutor()
    async with sdk_client(executor, make_card(streaming=False)) as (client, _):
        if operation == "extended":
            response = await client.httpx_client.post(
                "/a2a", json={"jsonrpc": "2.0", "id": 1, "method": "GetExtendedAgentCard", "params": {}}
            )
            assert response.json()["error"]["code"] == -32004
            assert not executor.requests
            return
        error = PushNotificationNotSupportedError if operation == "push" else UnsupportedOperationError
        with pytest.raises(error):
            if operation == "stream":
                await anext(client.send_message_streaming(message_request()))
            elif operation == "subscribe":
                await anext(client.subscribe(SubscribeToTaskRequest(id="missing")))
            elif operation == "push":
                await client.create_task_push_notification_config(
                    TaskPushNotificationConfig(task_id="missing", id="config", url="https://example.com/hook")
                )
        assert not executor.requests


async def test_sdk_rejects_missing_required_fields_and_invalid_limits() -> None:
    executor = LifecycleExecutor()
    async with sdk_client(executor) as (client, _):
        with pytest.raises(InvalidParamsError):
            await client.send_message(SendMessageRequest())
        with pytest.raises(InvalidParamsError):
            await client.get_task(GetTaskRequest())
        with pytest.raises(InvalidParamsError):
            await client.list_tasks(ListTasksRequest(page_size=0))
        with pytest.raises(InvalidParamsError):
            await client.list_tasks(ListTasksRequest(history_length=-1))
        assert not executor.requests


async def test_sdk_configured_push_crud_and_extended_card() -> None:
    card = make_card(push=True, extended=True)
    async with sdk_client(LifecycleExecutor(), card) as (client, _):
        task = (await client.send_message(message_request())).task
        config = TaskPushNotificationConfig(task_id=task.id, id="config", url="https://example.com/hook")
        assert await client.create_task_push_notification_config(config) == config
        assert (
            await client.get_task_push_notification_config(
                GetTaskPushNotificationConfigRequest(task_id=task.id, id="config")
            )
            == config
        )
        assert list(
            (
                await client.list_task_push_notification_configs(
                    ListTaskPushNotificationConfigsRequest(task_id=task.id)
                )
            ).configs
        ) == [config]
        await client.delete_task_push_notification_config(
            DeleteTaskPushNotificationConfigRequest(task_id=task.id, id="config")
        )
        assert not (
            await client.list_task_push_notification_configs(ListTaskPushNotificationConfigsRequest(task_id=task.id))
        ).configs
        assert await client.get_extended_agent_card(GetExtendedAgentCardRequest()) == card


async def test_sdk_executor_continues_after_stream_disconnect() -> None:
    executor = LifecycleExecutor()
    card = make_card()
    handler = ObservedHandler(executor, card)
    app = Litestar(plugins=[LitestarA2A(card, handler)])
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "SendStreamingMessage", "params": MessageToDict(message_request("wait"))}
    ).encode()
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
        "headers": [(b"host", b"testserver"), (b"content-type", b"application/json"), (b"a2a-version", b"1.0")],
    }
    first_sent = asyncio.Event()
    requested = False
    task_id = ""

    async def receive() -> dict[str, Any]:
        nonlocal requested
        if not requested:
            requested = True
            return {"type": "http.request", "body": body}
        await first_sent.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        nonlocal task_id
        if message["type"] == "http.response.body" and message.get("body"):
            task_id = json.loads(message["body"].decode().removeprefix("data: ").strip())["result"]["task"]["id"]
            first_sent.set()

    async with (
        app.lifespan(),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=cast("Any", app)), base_url="http://testserver.local"
        ) as http_client,
    ):
        http_client.headers["A2A-Version"] = "1.0"
        client = JsonRpcTransport(http_client, card, "http://testserver.local/a2a")
        with anyio.fail_after(3):
            await app(cast("Any", scope), cast("Any", receive), cast("Any", send))
            assert task_id and not executor.finished.is_set() and not executor.cancelled.is_set()
            events: list[Any] = []

            async def subscribe() -> None:
                events.extend([event async for event in client.subscribe(SubscribeToTaskRequest(id=task_id))])

            async with anyio.create_task_group() as group:
                group.start_soon(subscribe)
                await handler.subscribed.wait()
                executor.release.set()
            assert events[-1].status_update.status.state == TaskState.TASK_STATE_COMPLETED
            assert executor.finished.is_set() and not executor.cancelled.is_set()
