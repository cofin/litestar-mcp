from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

import pytest
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.server.context import ServerCallContext
from a2a.server.request_handlers import RequestHandler
from a2a.types import (
    AgentCard,
    AgentExtension,
    AgentInterface,
    Message,
    Part,
    Role,
    SendMessageRequest,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.errors import TaskNotFoundError
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
from litestar import Litestar, Request, get
from litestar.config.csrf import CSRFConfig
from litestar.connection import ASGIConnection
from litestar.exceptions import ImproperlyConfiguredException, NotAuthorizedException, PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult, DefineMiddleware
from litestar.plugins import InitPluginProtocol
from litestar.testing import AsyncTestClient

from litestar_mcp import LitestarMCP, MCPConfig
from litestar_mcp.a2a import A2AConfig, LitestarA2A

if TYPE_CHECKING:
    from litestar.config.app import AppConfig


def make_card(path: str = "/a2a") -> AgentCard:
    return AgentCard(
        name="Test agent",
        description="Test agent",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=f"https://example.com{path}",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
    )


@pytest.mark.anyio
async def test_serves_official_agent_card_and_closes_handler() -> None:
    handler = AsyncMock()
    app = Litestar(plugins=[LitestarA2A(make_card(), handler)])

    async with AsyncTestClient(app=app) as client:
        response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.json() == MessageToDict(make_card())
    handler.aclose.assert_awaited_once()


def test_rejects_card_whose_interface_does_not_match_mount() -> None:
    with pytest.raises(ValueError, match="matching the A2A path"):
        LitestarA2A(make_card("/wrong"), AsyncMock())


def test_rejects_application_owned_route_collision() -> None:
    @get("/a2a")
    async def owned() -> None: ...

    with pytest.raises(ValueError, match="A2A route collision"):
        Litestar(route_handlers=[owned], plugins=[LitestarA2A(make_card(), AsyncMock())])


class _RouteOwner(InitPluginProtocol):
    """Companion plugin that registers an application-owned ``/a2a`` handler."""

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        @get("/a2a")
        async def owned() -> None: ...

        app_config.route_handlers.append(owned)
        return app_config


@pytest.mark.parametrize("registration", ["route_handlers", "earlier_plugin"])
def test_collision_detection_is_order_independent(registration: str) -> None:
    a2a = LitestarA2A(make_card(), AsyncMock())

    @get("/a2a")
    async def owned() -> None: ...

    with pytest.raises(ValueError, match="A2A route collision"):
        if registration == "route_handlers":
            Litestar(plugins=[a2a], route_handlers=[owned])
        else:
            Litestar(plugins=[_RouteOwner(), a2a])


def test_mcp_router_mount_collision_is_reported_by_the_adapter() -> None:
    mcp = LitestarMCP(MCPConfig(base_path="/a2a"))

    with pytest.raises(ValueError, match="A2A route collision: /a2a"):
        Litestar(plugins=[mcp, LitestarA2A(make_card(), AsyncMock())])


def test_mcp_router_mounted_after_a2a_is_rejected_by_litestar() -> None:
    mcp = LitestarMCP(MCPConfig(base_path="/a2a"))

    with pytest.raises(ImproperlyConfiguredException, match="Handler already registered"):
        Litestar(plugins=[LitestarA2A(make_card(), AsyncMock()), mcp])


def test_config_requires_absolute_paths() -> None:
    with pytest.raises(ValueError, match="must start"):
        A2AConfig(path="a2a")


@pytest.mark.anyio
async def test_dispatches_jsonrpc_to_official_request_handler() -> None:
    handler = AsyncMock()
    handler.on_message_send.return_value = Message(
        message_id="reply-1",
        role=Role.ROLE_AGENT,
        parts=[Part(text="hello")],
    )
    app = Litestar(plugins=[LitestarA2A(make_card(), handler)])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "request-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "hello"}],
                    }
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["message"]["messageId"] == "reply-1"
    handler.on_message_send.assert_awaited_once()


@pytest.mark.anyio
async def test_official_jsonrpc_client_dispatches_through_litestar() -> None:
    handler = AsyncMock()
    handler.on_message_send.return_value = Message(
        message_id="official-reply",
        role=Role.ROLE_AGENT,
        parts=[Part(text="hello")],
    )
    card = make_card()
    app = Litestar(plugins=[LitestarA2A(card, handler)])

    async with AsyncTestClient(app=app) as http_client:
        http_client.headers["A2A-Version"] = "1.0"
        transport = JsonRpcTransport(http_client, card, "http://testserver.local/a2a")
        response = await transport.send_message(
            SendMessageRequest(
                message=Message(
                    message_id="official-request",
                    role=Role.ROLE_USER,
                    parts=[Part(text="hello")],
                )
            )
        )

    assert response.message.message_id == "official-reply"


@pytest.mark.anyio
@pytest.mark.parametrize("asynchronous", [False, True])
async def test_uses_litestar_context_builder(asynchronous: bool) -> None:
    observed_request: Request[Any, Any, Any] | None = None
    context = ServerCallContext(tenant="authorized")

    def build_context(request: Request[Any, Any, Any], prepared: ServerCallContext) -> ServerCallContext:
        nonlocal observed_request
        observed_request = request
        assert prepared.tenant == "requested"
        assert prepared.state["method"] == "GetTask"
        assert prepared.state["request_id"] == "x"
        context.state["headers"] = dict(request.headers)
        return context

    async def build_context_async(request: Request[Any, Any, Any], prepared: ServerCallContext) -> ServerCallContext:
        return build_context(request, prepared)

    handler = AsyncMock()
    handler.on_get_task.return_value = None
    builder = build_context_async if asynchronous else build_context
    app = Litestar(plugins=[LitestarA2A(make_card(), handler, A2AConfig(context_builder=builder))])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a",
            headers={"A2A-Version": "1.0"},
            json={"jsonrpc": "2.0", "id": "x", "method": "GetTask", "params": {"id": "missing", "tenant": "requested"}},
        )

    assert response.status_code == 200
    assert observed_request is not None
    assert handler.on_get_task.await_args.args[1] is context
    assert context.tenant == "authorized"


@pytest.mark.anyio
async def test_returns_jsonrpc_parse_error_for_malformed_json() -> None:
    app = Litestar(plugins=[LitestarA2A(make_card(), AsyncMock())])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a", content=b"{", headers={"content-type": "application/json", "A2A-Version": "1.0"}
        )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32700


class StubHandler(RequestHandler):
    def __init__(self) -> None:
        self.contexts: list[ServerCallContext] = []

    async def on_get_task(self, params: Any, context: ServerCallContext | None = None) -> Any:
        return None

    async def on_cancel_task(self, params: Any, context: ServerCallContext | None = None) -> Any:
        return None

    async def on_message_send(self, params: Any, context: ServerCallContext | None = None) -> Any:
        if context is not None:
            self.contexts.append(context)
        return Message(message_id="reply", role=Role.ROLE_AGENT, parts=[Part(text="ok")])

    async def on_message_send_stream(
        self, params: Any, context: ServerCallContext | None = None
    ) -> AsyncGenerator[Any, None]:
        yield Message(message_id="chunk", role=Role.ROLE_AGENT, parts=[Part(text="one")])
        yield Task(id="task-1", context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED))

    async def on_create_task_push_notification_config(
        self, params: Any, context: ServerCallContext | None = None
    ) -> Any:
        raise NotImplementedError

    async def on_get_task_push_notification_config(self, params: Any, context: ServerCallContext | None = None) -> Any:
        raise NotImplementedError

    async def on_list_task_push_notification_configs(
        self, params: Any, context: ServerCallContext | None = None
    ) -> Any:
        raise NotImplementedError

    async def on_delete_task_push_notification_config(
        self, params: Any, context: ServerCallContext | None = None
    ) -> Any:
        raise NotImplementedError

    async def on_subscribe_to_task(
        self, params: Any, context: ServerCallContext | None = None
    ) -> AsyncGenerator[Any, None]:
        yield Task(id="task-1", context_id="ctx-1", status=TaskStatus(state=TaskState.TASK_STATE_WORKING))

    async def on_get_extended_agent_card(self, params: Any, context: ServerCallContext | None = None) -> Any:
        return make_card()

    async def on_list_tasks(self, params: Any, context: ServerCallContext | None = None) -> Any:
        raise NotImplementedError


def send_payload(method: str = "SendMessage") -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {"message": {"messageId": "request-1", "role": "ROLE_USER", "parts": [{"text": "hello"}]}},
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "changes",
    [
        {"id": True},
        {"id": False},
        {"id": {}},
        {"id": []},
        {"jsonrpc": "1.0"},
        {"jsonrpc": 2},
        {"method": ""},
        {"method": 1},
        {"params": []},
        {"params": None},
        {"params": "bad"},
    ],
)
async def test_invalid_envelopes_never_invoke_handler(changes: dict[str, Any]) -> None:
    handler = StubHandler()
    app = Litestar(plugins=[LitestarA2A(make_card(), handler)])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json={**send_payload(), **changes})

    assert response.json()["error"]["code"] == -32600
    assert handler.contexts == []


@pytest.mark.anyio
@pytest.mark.parametrize("request_id", ["rpc-1", 0, -2, 1.25, None])
@pytest.mark.parametrize("failure", [False, True])
async def test_request_ids_are_preserved_in_success_and_error(request_id: Any, failure: bool) -> None:
    handler = AsyncMock()
    handler.on_message_send.return_value = Message(message_id="reply", role=Role.ROLE_AGENT)
    if failure:
        handler.on_message_send.side_effect = TaskNotFoundError()
    app = Litestar(plugins=[LitestarA2A(make_card(), handler)])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json={**send_payload(), "id": request_id})

    assert response.json()["id"] == request_id
    assert ("error" if failure else "result") in response.json()
    handler.on_message_send.assert_awaited_once()


@pytest.mark.anyio
async def test_notifications_return_no_content_without_execution() -> None:
    handler = StubHandler()
    payload = send_payload()
    del payload["id"]
    app = Litestar(plugins=[LitestarA2A(make_card(), handler)])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=payload)

    assert response.status_code == 204
    assert response.content == b""
    assert handler.contexts == []


@pytest.mark.anyio
async def test_omitted_params_default_to_empty_object() -> None:
    app = Litestar(plugins=[LitestarA2A(make_card(), StubHandler())])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a", headers={"A2A-Version": "1.0"}, json={"jsonrpc": "2.0", "id": 1, "method": "GetExtendedAgentCard"}
        )

    assert response.json()["result"] == MessageToDict(make_card())


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_activated_extensions_are_reported_after_handler_activation(streaming: bool) -> None:
    extensions = {"https://ext.example/a", "https://ext.example/b"}
    card = make_card()
    card.capabilities.extensions.extend(AgentExtension(uri=uri, required=True) for uri in extensions)

    class ActivatingHandler(StubHandler):
        async def on_message_send(self, params: Any, context: ServerCallContext | None = None) -> Any:
            assert context is not None
            context.state["a2a_activated_extensions"] = extensions
            return await super().on_message_send(params, context)

        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            yield await self.on_message_send(params, context)

    app = Litestar(plugins=[LitestarA2A(card, ActivatingHandler())])
    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a",
            headers={
                "A2A-Version": "1.0",
                "A2A-Extensions": "https://ext.example/b,https://ext.example/a,https://unknown",
            },
            json=send_payload("SendStreamingMessage" if streaming else "SendMessage"),
        )

    assert response.headers["A2A-Extensions"] == "https://ext.example/a, https://ext.example/b"


@pytest.mark.anyio
async def test_missing_required_extension_never_invokes_handler() -> None:
    card = make_card()
    card.capabilities.extensions.append(AgentExtension(uri="https://ext.example/required", required=True))
    handler = StubHandler()
    app = Litestar(plugins=[LitestarA2A(card, handler)])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=send_payload())

    assert response.json()["error"]["code"] == -32008
    assert handler.contexts == []


@pytest.mark.anyio
@pytest.mark.parametrize(
    "activation",
    [["https://ext.example/a"], "https://ext.example/a", {1}, {"https://unknown"}, {"https://ext.example/b"}],
)
async def test_invalid_extension_activation_returns_generic_error(activation: Any) -> None:
    def activate(request: Request[Any, Any, Any], context: ServerCallContext) -> ServerCallContext:
        context.state["a2a_activated_extensions"] = activation
        return context

    card = make_card()
    card.capabilities.extensions.extend(
        AgentExtension(uri=uri) for uri in ("https://ext.example/a", "https://ext.example/b")
    )
    handler = StubHandler()
    app = Litestar(plugins=[LitestarA2A(card, handler, A2AConfig(context_builder=activate))])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a", headers={"A2A-Version": "1.0", "A2A-Extensions": "https://ext.example/a"}, json=send_payload()
        )

    assert response.json()["error"]["code"] == -32603
    assert response.json()["error"]["message"] == "Internal error"
    assert "A2A-Extensions" not in response.headers
    assert handler.contexts == []


@pytest.mark.anyio
@pytest.mark.parametrize("uri", ["https://ext.example/a\r\nInjected: true", "https://ext.example/a,b"])
@pytest.mark.parametrize("streaming", [False, True])
async def test_header_unsafe_extension_activation_is_rejected(uri: str, streaming: bool) -> None:
    card = make_card()
    card.capabilities.extensions.append(AgentExtension(uri=uri))

    def activate(request: Request[Any, Any, Any], context: ServerCallContext) -> ServerCallContext:
        if not streaming:
            context.state["a2a_activated_extensions"] = {uri}
        return context

    class ActivatingHandler(StubHandler):
        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            assert context is not None
            context.state["a2a_activated_extensions"] = {uri}
            yield await self.on_message_send(params, context)

    app = Litestar(plugins=[LitestarA2A(card, ActivatingHandler(), A2AConfig(context_builder=activate))])
    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a",
            headers={"A2A-Version": "1.0", "A2A-Extensions": uri},
            json=send_payload("SendStreamingMessage" if streaming else "SendMessage"),
        )

    assert response.json()["error"]["code"] == -32603
    assert "A2A-Extensions" not in response.headers
    assert "Injected" not in response.headers


@pytest.mark.anyio
@pytest.mark.parametrize("streaming", [False, True])
async def test_protocol_error_preserves_valid_extension_activation(streaming: bool) -> None:
    uri = "https://ext.example/a"
    card = make_card()
    card.capabilities.extensions.append(AgentExtension(uri=uri))

    class FailingHandler(StubHandler):
        async def on_message_send(self, params: Any, context: ServerCallContext | None = None) -> Any:
            assert context is not None
            context.state["a2a_activated_extensions"] = {uri}
            raise TaskNotFoundError

        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            yield await self.on_message_send(params, context)

    app = Litestar(plugins=[LitestarA2A(card, FailingHandler())])
    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a",
            headers={"A2A-Version": "1.0", "A2A-Extensions": uri},
            json=send_payload("SendStreamingMessage" if streaming else "SendMessage"),
        )

    assert response.json()["error"]["code"] == -32001
    assert response.headers["A2A-Extensions"] == uri


@pytest.mark.anyio
async def test_non_ascii_version_is_rejected() -> None:
    handler = StubHandler()
    app = Litestar(plugins=[LitestarA2A(make_card(), handler)])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={b"A2A-Version": b"\xb9.0"}, json=send_payload())

    assert response.json()["error"]["code"] == -32009
    assert handler.contexts == []


@pytest.mark.anyio
@pytest.mark.parametrize("version", ["1.0", "1.0.0", "1.0.123", " 1.0\t"])
async def test_supported_version_semantics(version: str) -> None:
    handler = StubHandler()
    app = Litestar(plugins=[LitestarA2A(make_card(), handler)])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": version}, json=send_payload())

    assert "result" in response.json()
    assert len(handler.contexts) == 1


@pytest.mark.anyio
@pytest.mark.parametrize(
    "version",
    [None, "", " \t", "0.3", "1", "1.1", "1.999", "2.0", "1.0.0.0", "1.0-rc1", "1. 0", "01.0", "1.00"],
)
async def test_unsupported_versions_never_invoke_context_or_handler(version: str | None) -> None:
    context_builder = Mock(return_value=ServerCallContext())
    handler = StubHandler()
    app = Litestar(plugins=[LitestarA2A(make_card(), handler, A2AConfig(context_builder=context_builder))])
    headers = {"A2A-Version": version} if version is not None else {}

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers=headers, json=send_payload())

    assert response.json()["error"]["code"] == -32009
    context_builder.assert_not_called()
    assert handler.contexts == []


@pytest.mark.anyio
@pytest.mark.parametrize("error_type", [NotAuthorizedException, PermissionDeniedException])
async def test_context_http_exception_is_handled_by_litestar(error_type: type[Exception]) -> None:
    async def deny(request: Request[Any, Any, Any], context: ServerCallContext) -> ServerCallContext:
        raise error_type()

    handler = StubHandler()
    app = Litestar(plugins=[LitestarA2A(make_card(), handler, A2AConfig(context_builder=deny))])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=send_payload())

    assert response.status_code == (401 if error_type is NotAuthorizedException else 403)
    assert handler.contexts == []


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [RuntimeError("private stream detail"), TaskNotFoundError()])
@pytest.mark.parametrize("after_first", [False, True])
async def test_stream_errors_preserve_domain_codes_and_hide_unexpected_details(
    failure: Exception, after_first: bool, caplog: pytest.LogCaptureFixture
) -> None:
    class FailingHandler(StubHandler):
        async def on_message_send_stream(
            self, params: Any, context: ServerCallContext | None = None
        ) -> AsyncGenerator[Any, None]:
            if after_first:
                yield Message(message_id="reply", role=Role.ROLE_AGENT)
            raise failure

    app = Litestar(plugins=[LitestarA2A(make_card(), FailingHandler())], logging_config=None)

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=send_payload("SendStreamingMessage"))

    code = -32001 if isinstance(failure, TaskNotFoundError) else -32603
    assert f'"code":{code}' in response.text
    assert "private stream detail" not in response.text
    if isinstance(failure, RuntimeError):
        assert "private stream detail" in caplog.text


@pytest.mark.anyio
async def test_streaming_is_served_as_event_stream_to_official_client() -> None:
    card = make_card()
    app = Litestar(plugins=[LitestarA2A(card, StubHandler())])

    async with AsyncTestClient(app=app) as http_client:
        raw = await http_client.post("/a2a", headers={"A2A-Version": "1.0"}, json=send_payload("SendStreamingMessage"))
        http_client.headers["A2A-Version"] = "1.0"
        transport = JsonRpcTransport(http_client, card, "http://testserver.local/a2a")
        payloads = [
            event.WhichOneof("payload")
            async for event in transport.send_message_streaming(
                SendMessageRequest(message=Message(message_id="q", role=Role.ROLE_USER, parts=[Part(text="hi")]))
            )
        ]

    assert raw.headers["content-type"].startswith("text/event-stream")
    assert payloads == ["message", "task"]


class _AnonymousPrincipal:
    id = None
    is_authenticated = False


class _PrincipalMiddleware(AbstractAuthenticationMiddleware):
    async def authenticate_request(self, connection: ASGIConnection[Any, Any, Any, Any]) -> AuthenticationResult:
        return AuthenticationResult(user=_AnonymousPrincipal(), auth=None)


@pytest.mark.anyio
async def test_anonymous_principal_on_scope_is_not_authenticated() -> None:
    handler = StubHandler()
    app = Litestar(
        middleware=[DefineMiddleware(_PrincipalMiddleware)],
        plugins=[LitestarA2A(make_card(), handler)],
    )

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=send_payload())

    assert "result" in response.json()
    assert handler.contexts[0].user.is_authenticated is False


class _MappingPrincipalMiddleware(AbstractAuthenticationMiddleware):
    async def authenticate_request(self, connection: ASGIConnection[Any, Any, Any, Any]) -> AuthenticationResult:
        return AuthenticationResult(user={"id": None, "is_authenticated": False}, auth=None)


@pytest.mark.anyio
async def test_anonymous_mapping_principal_on_scope_is_not_authenticated() -> None:
    handler = StubHandler()
    app = Litestar(
        middleware=[DefineMiddleware(_MappingPrincipalMiddleware)],
        plugins=[LitestarA2A(make_card(), handler)],
    )

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=send_payload())

    assert "result" in response.json()
    assert handler.contexts[0].user.is_authenticated is False


@pytest.mark.anyio
async def test_version_check_reads_the_request_header_with_a_bare_context_builder() -> None:
    config = A2AConfig(context_builder=lambda _request, _context: ServerCallContext())
    app = Litestar(plugins=[LitestarA2A(make_card(), StubHandler(), config)])

    async with AsyncTestClient(app=app) as client:
        accepted = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=send_payload())
        rejected = await client.post("/a2a", json=send_payload())

    assert "result" in accepted.json()
    assert rejected.json()["error"]["code"] == -32009


@pytest.mark.anyio
async def test_version_check_rejects_malformed_compatible_major_version() -> None:
    app = Litestar(plugins=[LitestarA2A(make_card(), StubHandler())])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.bad"}, json=send_payload())

    assert response.json()["error"]["code"] == -32009


@pytest.mark.anyio
async def test_rpc_route_is_exempt_from_csrf() -> None:
    app = Litestar(csrf_config=CSRFConfig(secret="s" * 32), plugins=[LitestarA2A(make_card(), StubHandler())])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=send_payload())

    assert response.status_code == 200
    assert "result" in response.json()


@pytest.mark.anyio
async def test_guards_apply_to_rpc_route() -> None:
    def deny(connection: ASGIConnection[Any, Any, Any, Any], handler: BaseRouteHandler) -> None:
        raise PermissionDeniedException

    app = Litestar(plugins=[LitestarA2A(make_card(), StubHandler(), A2AConfig(guards=[deny]))])

    async with AsyncTestClient(app=app) as client:
        response = await client.post("/a2a", headers={"A2A-Version": "1.0"}, json=send_payload())

    assert response.status_code == 403


def test_route_opt_is_merged_onto_rpc_route() -> None:
    config = A2AConfig(route_opt={"custom": "value"})
    app = Litestar(plugins=[LitestarA2A(make_card(), StubHandler(), config)])

    handler = app.route_handler_method_map["/a2a"]["POST"]

    assert handler.opt["custom"] == "value"
    assert handler.opt["exclude_from_csrf"] is True


@pytest.mark.anyio
async def test_a2a_routes_are_hidden_from_openapi_unless_enabled() -> None:
    hidden = Litestar(plugins=[LitestarA2A(make_card(), StubHandler())])
    shown = Litestar(plugins=[LitestarA2A(make_card(), StubHandler(), A2AConfig(include_in_schema=True))])

    async with AsyncTestClient(app=hidden) as client:
        hidden_paths = (await client.get("/schema/openapi.json")).json()["paths"]
    async with AsyncTestClient(app=shown) as client:
        shown_paths = (await client.get("/schema/openapi.json")).json()["paths"]

    assert "/a2a" not in hidden_paths
    assert "/a2a" in shown_paths


@pytest.mark.anyio
async def test_agent_card_sends_cache_headers_and_honours_if_none_match() -> None:
    app = Litestar(plugins=[LitestarA2A(make_card(), StubHandler())])

    async with AsyncTestClient(app=app) as client:
        first = await client.get("/.well-known/agent-card.json")
        second = await client.get("/.well-known/agent-card.json", headers={"If-None-Match": first.headers["etag"]})

    assert first.headers["cache-control"].startswith("public, max-age=")
    assert second.status_code == 304
