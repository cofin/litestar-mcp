"""Authenticated, workspace-scoped A2A application using the official SDK.

Run with an ASGI server, for example::

    uv run --extra a2a --with granian granian --interface asgi docs.examples.a2a_application.main:app

The demonstration credentials are ``Bearer demo-alice`` and ``Bearer demo-bob``;
both users belong to ``alpha`` and ``beta``. Replace this credential lookup and
membership service with your application's authentication and authorization.

Only SDK-generated task IDs may create tasks. Ownership is immutable. Custom
stores must enforce globally unique IDs and immutable ownership across all
tenants: SDK active-task routing and push dispatch use the task ID globally.
The SDK's in-memory stores alone do not enforce those cross-owner invariants.

Push support records SDK delegation only: no live webhook delivery, retries,
durable persistence or multiworker coordination is demonstrated.
"""

# /// script
# requires-python = ">=3.10"
# dependencies = ["litestar-mcp[a2a]"]
# ///

import json
from collections.abc import AsyncGenerator, Callable
from contextlib import AbstractAsyncContextManager, aclosing, asynccontextmanager
from dataclasses import dataclass
from typing import Any

from a2a.server.agent_execution import AgentExecutor, RequestContext
from a2a.server.context import ServerCallContext
from a2a.server.events import Event, EventQueue
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import (
    InMemoryPushNotificationConfigStore,
    InMemoryTaskStore,
    PushNotificationConfigStore,
    PushNotificationEvent,
    PushNotificationSender,
    TaskStore,
)
from a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    Artifact,
    CancelTaskRequest,
    HTTPAuthSecurityScheme,
    Part,
    SecurityRequirement,
    SecurityScheme,
    StringList,
    SubscribeToTaskRequest,
    Task,
    TaskArtifactUpdateEvent,
    TaskState,
    TaskStatus,
    TaskStatusUpdateEvent,
)
from a2a.utils.errors import InvalidParamsError, TaskNotFoundError, UnsupportedOperationError
from a2a.utils.proto_utils import validate_proto_required_fields
from litestar import Litestar, Request
from litestar.connection import ASGIConnection
from litestar.exceptions import NotAuthorizedException, PermissionDeniedException
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult, DefineMiddleware

from litestar_mcp.a2a import A2AConfig, LitestarA2A

EXTENSION_URI = "https://example.com/extensions/workspace-label"
TERMINAL_STATES = {
    TaskState.TASK_STATE_COMPLETED,
    TaskState.TASK_STATE_CANCELED,
    TaskState.TASK_STATE_FAILED,
    TaskState.TASK_STATE_REJECTED,
}


@dataclass(frozen=True)
class Principal:
    id: str
    workspaces: frozenset[str] = frozenset({"alpha", "beta"})

    async def authorize_workspace(self, workspace: str) -> str:
        """Replace with an awaited membership lookup in the existing application."""
        if workspace not in self.workspaces:
            raise PermissionDeniedException(detail="Workspace membership required")
        return workspace


class DemoAuthentication(AbstractAuthenticationMiddleware):
    async def authenticate_request(self, connection: "ASGIConnection[Any, Any, Any, Any]") -> AuthenticationResult:
        credentials = {"Bearer demo-alice": Principal("alice"), "Bearer demo-bob": Principal("bob")}
        principal = credentials.get(connection.headers.get("authorization", ""))
        if principal is None:
            raise NotAuthorizedException(
                detail="Valid demonstration credentials required", headers={"WWW-Authenticate": "Bearer"}
            )
        return AuthenticationResult(user=principal, auth=None)


async def authorize_context(request: "Request[Principal, Any, Any]", context: ServerCallContext) -> ServerCallContext:
    context.tenant = await request.user.authorize_workspace(context.tenant)
    if EXTENSION_URI in context.requested_extensions:
        context.state["a2a_activated_extensions"] = {EXTENSION_URI}
    return context


def resolve_owner(context: ServerCallContext) -> str:
    """Share the same collision-safe owner key between both SDK stores."""
    if not context.user.is_authenticated or not context.user.user_name or not context.tenant:
        raise NotAuthorizedException(
            detail="An authenticated workspace owner is required", headers={"WWW-Authenticate": "Bearer"}
        )
    return json.dumps([context.tenant, context.user.user_name], separators=(",", ":"))


class WorkspaceTaskHandler(DefaultRequestHandler):
    """Example-local authorization correction for A2A SDK 1.1.2.

    Its active registry bypasses scoped stores for cancel/subscribe and reports
    terminal subscription as InvalidParamsError. Remove these overrides only
    after an upstream fix passes the ownership and terminal-race regressions.
    Globally unique task IDs and immutable ownership remain prerequisites.
    """

    async def on_cancel_task(self, params: CancelTaskRequest, context: ServerCallContext) -> Task | None:
        validate_proto_required_fields(params)
        if await self.task_store.get(params.id, context) is None:
            raise TaskNotFoundError
        return await super().on_cancel_task(params, context)

    async def on_subscribe_to_task(
        self, params: SubscribeToTaskRequest, context: ServerCallContext
    ) -> AsyncGenerator[Event, None]:
        validate_proto_required_fields(params)
        task = await self.task_store.get(params.id, context)
        if task is None:
            raise TaskNotFoundError
        if task.status.state in TERMINAL_STATES:
            raise UnsupportedOperationError
        try:
            async with aclosing(super().on_subscribe_to_task(params, context)) as stream:
                async for event in stream:
                    yield event
        except InvalidParamsError:
            task = await self.task_store.get(params.id, context)
            if task is not None and task.status.state in TERMINAL_STATES:
                raise UnsupportedOperationError from None
            raise


class ApplicationService:
    async def answer(self, text: str) -> str:
        return f"Workspace answer: {text}"


@asynccontextmanager
async def service_scope(context: ServerCallContext) -> AsyncGenerator[ApplicationService, None]:
    """Supply this verified context to an existing application/Dishka scope.

    Resolve the application service within that scope and yield it here. Its
    async context manager then owns teardown independently of the HTTP request.
    """
    yield ApplicationService()


class ApplicationExecutor(AgentExecutor):
    def __init__(
        self, service_factory: Callable[[ServerCallContext], AbstractAsyncContextManager[ApplicationService]]
    ) -> None:
        self.service_factory = service_factory

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        """Own service resources while executor work outlives HTTP dependencies."""
        async with self.service_factory(context.call_context) as service:
            if context.current_task is None:
                await event_queue.enqueue_event(
                    Task(
                        id=context.task_id,
                        context_id=context.context_id,
                        status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                        history=[context.message] if context.message is not None else [],
                    )
                )
            answer = await service.answer(context.get_user_input())
            if EXTENSION_URI in context.call_context.state.get("a2a_activated_extensions", set()):
                answer = f"[{context.tenant}] {answer}"
            await event_queue.enqueue_event(
                TaskArtifactUpdateEvent(
                    task_id=context.task_id,
                    context_id=context.context_id,
                    artifact=Artifact(artifact_id="answer", parts=[Part(text=answer)]),
                    last_chunk=True,
                )
            )
            await event_queue.enqueue_event(self.status(context, TaskState.TASK_STATE_COMPLETED))

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        await event_queue.enqueue_event(self.status(context, TaskState.TASK_STATE_CANCELED))

    @staticmethod
    def status(context: RequestContext, state: "TaskState") -> TaskStatusUpdateEvent:
        return TaskStatusUpdateEvent(
            task_id=context.task_id, context_id=context.context_id, status=TaskStatus(state=state)
        )


class RecordingPushSender(PushNotificationSender):
    """Record configured destinations and SDK events without sending HTTP requests."""

    def __init__(self, store: PushNotificationConfigStore) -> None:
        self.store = store
        self.deliveries: list[tuple[str, str, PushNotificationEvent]] = []

    async def send_notification(self, task_id: str, event: PushNotificationEvent) -> None:
        for config in await self.store.get_info_for_dispatch(task_id):
            self.deliveries.append((task_id, config.url, event))


def create_app(
    service_factory: Callable[[ServerCallContext], AbstractAsyncContextManager[ApplicationService]] = service_scope,
    *,
    task_store: TaskStore | None = None,
    push_store: PushNotificationConfigStore | None = None,
    push_sender: PushNotificationSender | None = None,
) -> Litestar:
    card = AgentCard(
        name="Workspace agent",
        description="Authenticated application services through the official A2A SDK",
        version="1.0.0",
        security_schemes={
            "bearerAuth": SecurityScheme(http_auth_security_scheme=HTTPAuthSecurityScheme(scheme="Bearer"))
        },
        security_requirements=[SecurityRequirement(schemes={"bearerAuth": StringList()})],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=[
            AgentSkill(id="answer", name="Answer", description="Answer within an authorized workspace", tags=["demo"])
        ],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=True,
            extended_agent_card=True,
            extensions=[
                AgentExtension(uri=EXTENSION_URI, description="Prefix the answer with the authorized workspace")
            ],
        ),
        supported_interfaces=[
            AgentInterface(url="http://localhost:8000/a2a", protocol_binding="JSONRPC", protocol_version="1.0")
        ],
    )
    if push_store is None:
        push_store = InMemoryPushNotificationConfigStore(owner_resolver=resolve_owner)
    if task_store is None:
        task_store = InMemoryTaskStore(owner_resolver=resolve_owner)
    if push_sender is None:
        push_sender = RecordingPushSender(push_store)
    handler = WorkspaceTaskHandler(
        agent_executor=ApplicationExecutor(service_factory),
        task_store=task_store,
        agent_card=card,
        push_config_store=push_store,
        push_sender=push_sender,
        extended_agent_card=card,
    )
    return Litestar(
        middleware=[DefineMiddleware(DemoAuthentication)],
        plugins=[LitestarA2A(card, handler, A2AConfig(context_builder=authorize_context))],
    )


app = create_app()
