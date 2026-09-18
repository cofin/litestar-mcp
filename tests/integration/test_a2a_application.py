"""Authenticated application acceptance against the real SDK and native ASGI app."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import AsyncExitStack, aclosing, asynccontextmanager
from typing import Any, cast
from unittest.mock import AsyncMock

import anyio
import httpx
import pytest
from a2a.auth.user import User
from a2a.client import (
    A2ACardResolver,
    AuthInterceptor,
    ClientCallContext,
    ClientConfig,
    ClientFactory,
    InMemoryContextCredentialStore,
)
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryPushNotificationConfigStore, InMemoryTaskStore, PushNotificationEvent
from a2a.types import (
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
    TaskState,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError, UnsupportedOperationError
from docs.examples.a2a_application.main import (
    EXTENSION_URI,
    TERMINAL_STATES,
    ApplicationService,
    RecordingPushSender,
    create_app,
    resolve_owner,
)
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
from litestar import Litestar

from litestar_mcp.a2a import LitestarA2A
from tests.integration.test_a2a_sdk_lifecycle import message_request

pytestmark = [pytest.mark.integration, pytest.mark.anyio]


class ControlledService(ApplicationService):
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.closed = asyncio.Event()

    async def answer(self, text: str) -> str:
        await self.release.wait()
        assert not self.closed.is_set()
        return await super().answer(text)


class ServiceFactory:
    def __init__(self) -> None:
        self.services: list[ControlledService] = []
        self.contexts: list[ServerCallContext] = []
        self.opened = asyncio.Event()

    @asynccontextmanager
    async def __call__(self, context: ServerCallContext) -> AsyncGenerator[ApplicationService, None]:
        service = ControlledService()
        self.services.append(service)
        self.contexts.append(context)
        self.opened.set()
        try:
            yield service
        finally:
            await asyncio.sleep(0)
            service.closed.set()


@asynccontextmanager
async def clients(app: Litestar) -> AsyncGenerator[dict[str, JsonRpcTransport], None]:
    card = app.plugins.get(LitestarA2A).agent_card
    async with AsyncExitStack() as stack:
        await stack.enter_async_context(app.lifespan())
        result = {}
        for user in ("alice", "bob"):
            http = await stack.enter_async_context(
                httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=cast("Any", app)),
                    base_url="http://localhost:8000",
                    headers={"A2A-Version": "1.0", "Authorization": f"Bearer demo-{user}"},
                )
            )
            result[user] = JsonRpcTransport(http, card, "http://localhost:8000/a2a")
        yield result


def request(*, workspace: str = "alpha", task: Task | None = None, immediate: bool = True) -> SendMessageRequest:
    params = message_request(task=task, immediate=immediate)
    params.tenant = workspace
    return params


async def test_live_task_and_push_access_are_scoped_to_principal_and_workspace() -> None:
    factory = ServiceFactory()
    app = create_app(factory)
    async with clients(app) as users:
        with anyio.fail_after(5):
            owner = users["alice"]
            task = (await owner.send_message(request())).task
            config = TaskPushNotificationConfig(
                task_id=task.id, id="hook", url="https://example.com/hook", tenant="alpha"
            )
            await owner.create_task_push_notification_config(config)
            assert (await owner.get_task(GetTaskRequest(id=task.id, tenant="alpha"))).id == task.id
            for user, workspace in (("bob", "alpha"), ("alice", "beta"), ("bob", "beta")):
                client = users[user]
                for task_id in (task.id, "nonexistent"):
                    with pytest.raises(TaskNotFoundError):
                        await client.get_task(GetTaskRequest(id=task_id, tenant=workspace))
                    continuation = request(workspace=workspace, task=task)
                    continuation.message.task_id = task_id
                    with pytest.raises(TaskNotFoundError):
                        await client.send_message(continuation)
                    with pytest.raises(TaskNotFoundError):
                        await client.cancel_task(CancelTaskRequest(id=task_id, tenant=workspace))
                    with pytest.raises(TaskNotFoundError):
                        async with aclosing(
                            client.subscribe(SubscribeToTaskRequest(id=task_id, tenant=workspace))
                        ) as stream:
                            await anext(stream)
                    with pytest.raises(TaskNotFoundError):
                        await client.create_task_push_notification_config(
                            TaskPushNotificationConfig(task_id=task_id, id="hook", url=config.url, tenant=workspace)
                        )
                    with pytest.raises(TaskNotFoundError):
                        await client.get_task_push_notification_config(
                            GetTaskPushNotificationConfigRequest(task_id=task_id, id="hook", tenant=workspace)
                        )
                    with pytest.raises(TaskNotFoundError):
                        await client.list_task_push_notification_configs(
                            ListTaskPushNotificationConfigsRequest(task_id=task_id, tenant=workspace)
                        )
                    with pytest.raises(TaskNotFoundError):
                        await client.delete_task_push_notification_config(
                            DeleteTaskPushNotificationConfigRequest(task_id=task_id, id="hook", tenant=workspace)
                        )
            assert len(factory.services) == 1
            assert (factory.contexts[0].tenant, factory.contexts[0].user.user_name) == ("alpha", "alice")
            assert not factory.services[0].closed.is_set()
            assert (
                await owner.get_task(GetTaskRequest(id=task.id, tenant="alpha"))
            ).status.state == TaskState.TASK_STATE_WORKING
            assert (
                await owner.get_task_push_notification_config(
                    GetTaskPushNotificationConfigRequest(task_id=task.id, id="hook", tenant="alpha")
                )
                == config
            )


async def test_sdk_mints_distinct_ids_and_cannot_rebind_an_existing_owner() -> None:
    factory = ServiceFactory()
    async with clients(create_app(factory)) as users:
        tasks = []
        for user, workspace in (("alice", "alpha"), ("alice", "beta"), ("bob", "alpha"), ("bob", "beta")):
            tasks.append((await users[user].send_message(request(workspace=workspace))).task)
            own = await users[user].list_tasks(ListTasksRequest(tenant=workspace))
            assert [item.id for item in own.tasks] == [tasks[-1].id]
        assert len({task.id for task in tasks}) == 4
        attempt = request(workspace="beta", task=tasks[0])
        attempt.metadata.update({"owner": "alice", "tenant": "alpha"})
        with pytest.raises(TaskNotFoundError):
            await users["bob"].send_message(attempt)
        attempt.message.task_id = "caller-selected-new-id"
        with pytest.raises(TaskNotFoundError):
            await users["bob"].send_message(attempt)
        assert len(factory.services) == 4


@pytest.mark.parametrize(
    "authorization,workspace,status",
    [("", "alpha", 401), ("Bearer invalid", "alpha", 401), ("Bearer demo-alice", "denied", 403)],
)
async def test_native_auth_denial_precedes_sdk_store_access(authorization: str, workspace: str, status: int) -> None:
    store = InMemoryTaskStore(owner_resolver=resolve_owner)
    store.get = AsyncMock(wraps=store.get)  # type: ignore[method-assign]
    factory = ServiceFactory()
    async with clients(create_app(factory, task_store=store)) as users:
        response = await users["alice"].httpx_client.post(
            "/a2a",
            headers={"Authorization": authorization},
            json={"jsonrpc": "2.0", "id": 1, "method": "GetTask", "params": {"id": "missing", "tenant": workspace}},
        )
        assert response.status_code == status
        if status == 401:
            assert response.headers["WWW-Authenticate"] == "Bearer"
        store.get.assert_not_called()
        assert not factory.services


async def test_advertised_auth_works_with_official_credential_interceptor() -> None:
    async with clients(create_app()) as users:
        http = users["alice"].httpx_client
        del http.headers["Authorization"]
        card = await A2ACardResolver(http, "http://localhost:8000").get_agent_card()
        credentials = InMemoryContextCredentialStore()
        await credentials.set_credentials("alice", "bearerAuth", "demo-alice")
        context = ClientCallContext(state={"sessionId": "alice"})
        client = ClientFactory(ClientConfig(httpx_client=http, streaming=False)).create(
            card, interceptors=[AuthInterceptor(credentials)]
        )
        async with client:
            events = [event async for event in client.send_message(request(immediate=False), context=context)]
            assert events[-1].task.status.state == TaskState.TASK_STATE_COMPLETED
            assert (
                await client.get_task(GetTaskRequest(id=events[-1].task.id, tenant="alpha"), context=context)
            ).id == events[-1].task.id


@pytest.mark.parametrize("cancel", [False, True])
async def test_executor_scope_outlives_http_and_closes_after_work_or_cancel(
    cancel: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = ServiceFactory()
    app = create_app(factory)
    handler = app.plugins.get(LitestarA2A).request_handler
    subscribe = handler.on_subscribe_to_task
    subscribed = asyncio.Event()

    async def observed(params: SubscribeToTaskRequest, context: ServerCallContext) -> AsyncGenerator[Event, None]:
        async with aclosing(subscribe(params, context)) as stream:
            async for event in stream:
                subscribed.set()
                yield event

    monkeypatch.setattr(handler, "on_subscribe_to_task", observed)
    async with clients(app) as users:
        with anyio.fail_after(3):
            client = users["alice"]
            task = (await client.send_message(request())).task
            service = factory.services[0]
            assert not service.closed.is_set()
            events: list[Any] = []

            async def listen() -> None:
                events.extend(
                    [event async for event in client.subscribe(SubscribeToTaskRequest(id=task.id, tenant="alpha"))]
                )

            async with anyio.create_task_group() as group:
                group.start_soon(listen)
                await subscribed.wait()
                if cancel:
                    result = await client.cancel_task(CancelTaskRequest(id=task.id, tenant="alpha"))
                    assert result.status.state == TaskState.TASK_STATE_CANCELED
                else:
                    service.release.set()
            await service.closed.wait()
            assert events[0].task.id == task.id
            expected = TaskState.TASK_STATE_CANCELED if cancel else TaskState.TASK_STATE_COMPLETED
            assert events[-1].status_update.status.state == expected
    assert service.closed.is_set()


async def test_push_crud_delegation_extension_and_extended_card() -> None:
    factory = ServiceFactory()
    push_store = InMemoryPushNotificationConfigStore(owner_resolver=resolve_owner)
    sender = RecordingPushSender(push_store)
    app = create_app(factory, push_store=push_store, push_sender=sender)
    async with clients(app) as users:
        client = users["alice"]
        response = await client.httpx_client.post(
            "/a2a",
            headers={"A2A-Extensions": EXTENSION_URI},
            json={"jsonrpc": "2.0", "id": 1, "method": "SendMessage", "params": MessageToDict(request())},
        )
        assert response.headers["A2A-Extensions"] == EXTENSION_URI
        task_id = response.json()["result"]["task"]["id"]
        config = TaskPushNotificationConfig(task_id=task_id, id="hook", url="https://example.com/hook", tenant="alpha")
        assert await client.create_task_push_notification_config(config) == config
        assert list(
            (
                await client.list_task_push_notification_configs(
                    ListTaskPushNotificationConfigsRequest(task_id=task_id, tenant="alpha")
                )
            ).configs
        ) == [config]
        await client.cancel_task(CancelTaskRequest(id=task_id, tenant="alpha"))
        assert any(
            delivered_id == task_id and url == config.url and event.status.state == TaskState.TASK_STATE_CANCELED
            for delivered_id, url, event in sender.deliveries
            if isinstance(event, TaskStatusUpdateEvent)
        )
        await client.delete_task_push_notification_config(
            DeleteTaskPushNotificationConfigRequest(task_id=task_id, id="hook", tenant="alpha")
        )
        assert not (
            await client.list_task_push_notification_configs(
                ListTaskPushNotificationConfigsRequest(task_id=task_id, tenant="alpha")
            )
        ).configs
        assert (
            await client.get_extended_agent_card(GetExtendedAgentCardRequest(tenant="alpha"))
            == app.plugins.get(LitestarA2A).agent_card
        )


async def test_stream_execution_owns_service_scope_and_activates_workspace_label() -> None:
    factory = ServiceFactory()
    async with clients(create_app(factory)) as users:
        client = users["alice"]
        client.httpx_client.headers["A2A-Extensions"] = EXTENSION_URI
        events: list[Any] = []

        async def stream() -> None:
            events.extend([event async for event in client.send_message_streaming(request())])

        with anyio.fail_after(3):
            async with anyio.create_task_group() as group:
                group.start_soon(stream)
                await factory.opened.wait()
                assert not factory.services[0].closed.is_set()
                factory.services[0].release.set()
        assert [event.WhichOneof("payload") for event in events] == ["task", "artifact_update", "status_update"]
        assert events[1].artifact_update.artifact.parts[0].text.startswith("[alpha] ")
        assert factory.services[0].closed.is_set()


class TerminalSender(RecordingPushSender):
    """Keep terminal processing alive through a public SDK sender callback."""

    def __init__(self) -> None:
        super().__init__(InMemoryPushNotificationConfigStore(owner_resolver=resolve_owner))
        self.terminal = asyncio.Event()
        self.release = asyncio.Event()

    async def send_notification(self, task_id: str, event: PushNotificationEvent) -> None:
        if isinstance(event, Task | TaskStatusUpdateEvent) and event.status.state in TERMINAL_STATES:
            self.terminal.set()
            await self.release.wait()


@pytest.mark.parametrize("state", sorted(TERMINAL_STATES))
async def test_stored_terminal_subscription_is_unsupported(state: "TaskState") -> None:
    store = InMemoryTaskStore(owner_resolver=resolve_owner)
    async with clients(create_app(task_store=store)) as users:
        task = (await users["alice"].send_message(request(immediate=False))).task

    class Owner(User):
        @property
        def user_name(self) -> str:
            return "alice"

        @property
        def is_authenticated(self) -> bool:
            return True

    context = ServerCallContext(tenant="alpha", user=Owner())
    task.status.state = state
    await store.save(task, context)
    async with clients(create_app(task_store=store)) as users:
        with pytest.raises(UnsupportedOperationError):
            async with aclosing(users["alice"].subscribe(SubscribeToTaskRequest(id=task.id, tenant="alpha"))) as stream:
                await anext(stream)


async def test_terminal_task_still_in_active_processing_rejects_subscription() -> None:
    factory = ServiceFactory()
    sender = TerminalSender()
    async with clients(create_app(factory, push_sender=sender)) as users:
        with anyio.fail_after(3):
            task = (await users["alice"].send_message(request())).task
            factory.services[0].release.set()
            await sender.terminal.wait()
            try:
                with pytest.raises(UnsupportedOperationError):
                    async with aclosing(
                        users["alice"].subscribe(SubscribeToTaskRequest(id=task.id, tenant="alpha"))
                    ) as stream:
                        await anext(stream)
            finally:
                sender.release.set()


async def test_completion_between_preflight_and_subscribe_maps_terminal_error(monkeypatch: pytest.MonkeyPatch) -> None:
    factory = ServiceFactory()
    sender = TerminalSender()
    store = InMemoryTaskStore(owner_resolver=resolve_owner)
    app = create_app(factory, task_store=store, push_sender=sender)
    async with clients(app) as users:
        with anyio.fail_after(3):
            task = (await users["alice"].send_message(request())).task
            preflight = asyncio.Event()
            proceed = asyncio.Event()
            get = store.get
            armed = True

            async def gated_get(task_id: str, context: ServerCallContext) -> Task | None:
                nonlocal armed
                snapshot = await get(task_id, context)
                if armed and context.state.get("method") == "SubscribeToTask":
                    armed = False
                    preflight.set()
                    await proceed.wait()
                return snapshot

            monkeypatch.setattr(store, "get", gated_get)

            async def subscribe() -> None:
                with pytest.raises(UnsupportedOperationError):
                    async with aclosing(
                        users["alice"].subscribe(SubscribeToTaskRequest(id=task.id, tenant="alpha"))
                    ) as stream:
                        await anext(stream)

            async with anyio.create_task_group() as group:
                group.start_soon(subscribe)
                await preflight.wait()
                factory.services[0].release.set()
                await sender.terminal.wait()
                proceed.set()
            sender.release.set()


async def test_unrelated_subscribe_validation_error_is_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    async def invalid(
        self: Any, params: SubscribeToTaskRequest, context: ServerCallContext
    ) -> AsyncGenerator[Event, None]:
        raise InvalidParamsError(message="Unrelated invalid parameter")
        yield  # type: ignore[unreachable]

    monkeypatch.setattr(DefaultRequestHandler, "on_subscribe_to_task", invalid)
    factory = ServiceFactory()
    async with clients(create_app(factory)) as users:
        task = (await users["alice"].send_message(request())).task
        with pytest.raises(InvalidParamsError, match="Unrelated invalid parameter"):
            async with aclosing(users["alice"].subscribe(SubscribeToTaskRequest(id=task.id, tenant="alpha"))) as stream:
                await anext(stream)


async def test_required_ids_keep_sdk_validation() -> None:
    async with clients(create_app()) as users:
        with pytest.raises(InvalidParamsError):
            await users["alice"].cancel_task(CancelTaskRequest(tenant="alpha"))
        with pytest.raises(InvalidParamsError):
            async with aclosing(users["alice"].subscribe(SubscribeToTaskRequest(tenant="alpha"))) as stream:
                await anext(stream)
