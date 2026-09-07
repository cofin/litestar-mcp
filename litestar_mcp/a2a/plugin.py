"""Litestar-native HTTP transport for the official A2A SDK contracts."""

import hashlib
import logging
import math
import re
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

from a2a.auth.user import User
from a2a.extensions.common import HTTP_EXTENSION_HEADER, get_requested_extensions
from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import (
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    JSONRPCError,
    MethodNotFoundError,
)
from a2a.server.request_handlers.response_helpers import build_error_response
from a2a.types import (
    AgentCard,
    CancelTaskRequest,
    DeleteTaskPushNotificationConfigRequest,
    GetExtendedAgentCardRequest,
    GetTaskPushNotificationConfigRequest,
    GetTaskRequest,
    ListTaskPushNotificationConfigsRequest,
    ListTasksRequest,
    SendMessageRequest,
    SendMessageResponse,
    SubscribeToTaskRequest,
    Task,
    TaskPushNotificationConfig,
)
from a2a.utils import constants, proto_utils
from a2a.utils.errors import (
    A2AError,
    ExtensionSupportRequiredError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)
from google.protobuf.json_format import MessageToDict, ParseDict  # type: ignore[import-untyped]
from litestar import Litestar, MediaType, Request, Response, Router, get, post
from litestar.exceptions import HTTPException, SerializationException
from litestar.plugins import InitPluginProtocol
from litestar.response import ServerSentEvent, ServerSentEventMessage
from litestar.status_codes import HTTP_204_NO_CONTENT, HTTP_304_NOT_MODIFIED

from litestar_mcp.a2a.config import A2AConfig
from litestar_mcp.core.serialization import to_json

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from a2a.server.request_handlers import RequestHandler
    from litestar.config.app import AppConfig

logger = logging.getLogger(__name__)
_MISSING_ID = object()
_VERSION_PATTERN = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:\.(0|[1-9][0-9]*))?")


def _message_to_dict(message: "Any", **kwargs: "Any") -> "dict[str, Any]":
    return cast("dict[str, Any]", MessageToDict(message, **kwargs))


def _success_response(request_id: "str | int | float | None", result: "Any") -> "dict[str, Any]":
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _sse_data(payload: "dict[str, Any]") -> "str":
    return to_json(payload)


class _LitestarUser(User):
    def __init__(self, value: "Any") -> "None":
        self.value = value

    @property
    def is_authenticated(self) -> "bool":
        flag = (
            self.value.get("is_authenticated")
            if isinstance(self.value, dict)
            else getattr(self.value, "is_authenticated", None)
        )
        if isinstance(flag, bool):
            return flag
        return self.value is not None

    @property
    def user_name(self) -> "str":
        for name in ("id", "sub", "username", "display_name"):
            value = self.value.get(name) if isinstance(self.value, dict) else getattr(self.value, name, None)
            if value is not None:
                return str(value)
        return str(self.value)


def _header_values(request: "Request[Any, Any, Any]", name: "str") -> "list[str]":
    return list(request.headers.getall(name)) if name in request.headers else []


def _default_context_builder(request: "Request[Any, Any, Any]") -> "ServerCallContext":
    scope = request.scope
    headers = dict(request.headers)
    litestar_state = scope.get("state", {})
    return ServerCallContext(
        state={"auth": scope.get("auth"), "headers": headers, "litestar_state": litestar_state},
        user=_LitestarUser(scope.get("user")),
        requested_extensions=get_requested_extensions(_header_values(request, HTTP_EXTENSION_HEADER)),
    )


def _validate_card(card: "AgentCard", path: "str") -> "None":
    for interface in card.supported_interfaces:
        if interface.protocol_version == "1.0" and interface.protocol_binding == "JSONRPC":
            url = urlparse(interface.url)
            if url.scheme and url.netloc and url.path.rstrip("/") == path.rstrip("/"):
                return
    msg = "AgentCard must advertise an absolute JSONRPC supported interface for protocol 1.0 matching the A2A path"
    raise ValueError(msg)


class _JsonRpcTransport:
    _models: "dict[str, type[Any]]" = {
        "SendMessage": SendMessageRequest,
        "SendStreamingMessage": SendMessageRequest,
        "GetTask": GetTaskRequest,
        "ListTasks": ListTasksRequest,
        "CancelTask": CancelTaskRequest,
        "CreateTaskPushNotificationConfig": TaskPushNotificationConfig,
        "GetTaskPushNotificationConfig": GetTaskPushNotificationConfigRequest,
        "ListTaskPushNotificationConfigs": ListTaskPushNotificationConfigsRequest,
        "DeleteTaskPushNotificationConfig": DeleteTaskPushNotificationConfigRequest,
        "SubscribeToTask": SubscribeToTaskRequest,
        "GetExtendedAgentCard": GetExtendedAgentCardRequest,
    }

    def __init__(self, handler: "RequestHandler", config: "A2AConfig", agent_card: "AgentCard") -> "None":
        self.handler = handler
        self.config = config
        self.agent_card = agent_card

    def _extension_headers(self, request: "Request[Any, Any, Any]", context: "ServerCallContext") -> "dict[str, str]":
        activated = context.state.get("a2a_activated_extensions", set())
        if not isinstance(activated, set) or any(not isinstance(uri, str) for uri in activated):
            msg = "Activated extensions must be a set of URI strings"
            raise ValueError(msg)
        if any(not uri or any(char == "," or not "!" <= char <= "~" for char in uri) for uri in activated):
            msg = "Activated extension URI is not safe for a response header"
            raise ValueError(msg)
        requested = get_requested_extensions(_header_values(request, HTTP_EXTENSION_HEADER))
        advertised = {extension.uri for extension in self.agent_card.capabilities.extensions}
        if not activated <= requested & advertised:
            msg = "Activated extensions must be requested and advertised"
            raise ValueError(msg)
        return {HTTP_EXTENSION_HEADER: ", ".join(sorted(activated))} if activated else {}

    async def _build_context(
        self, request: "Request[Any, Any, Any]", params: "Any", method: "str", request_id: "str | int | float | None"
    ) -> "ServerCallContext":
        context = _default_context_builder(request)
        context.tenant = getattr(params, "tenant", "")
        context.state["method"] = method
        context.state["request_id"] = request_id
        requested = set(context.requested_extensions)
        if self.config.context_builder is not None:
            result = self.config.context_builder(request, context)
            context = await result if isawaitable(result) else result
        required = {extension.uri for extension in self.agent_card.capabilities.extensions if extension.required}
        if not required <= requested:
            raise ExtensionSupportRequiredError
        self._extension_headers(request, context)
        return context

    def _response(
        self,
        request: "Request[Any, Any, Any]",
        payload: "dict[str, Any]",
        context: "ServerCallContext | None",
    ) -> "Response[Any]":
        try:
            headers = self._extension_headers(request, context) if context is not None else {}
        except ValueError:
            logger.exception("Invalid A2A extension activation")
            payload = self._error(payload["id"], InternalError())
            headers = {}
        return Response(content=payload, media_type=MediaType.JSON, headers=headers)

    @staticmethod
    def _error(
        request_id: "str | int | float | None", error: "Exception | JSONRPCError | A2AError"
    ) -> "dict[str, Any]":
        if not isinstance(error, A2AError | JSONRPCError):
            error = InternalError()
        response = build_error_response(None, error)
        response["id"] = request_id
        return response

    @staticmethod
    def _validate_version(request: "Request[Any, Any, Any]") -> "None":
        actual = request.headers.get(constants.VERSION_HEADER, "").strip(" \t") or constants.PROTOCOL_VERSION_0_3
        match = _VERSION_PATTERN.fullmatch(actual)
        if match is None or match.group(1, 2) != ("1", "0"):
            raise VersionNotSupportedError(message=f"A2A version '{actual}' is not supported. Expected version '1.0'.")

    async def handle(self, request: "Request[Any, Any, Any]") -> "dict[str, Any] | Response[Any]":  # noqa: PLR0911
        request_id: str | int | float | None = None
        context: ServerCallContext | None = None
        try:
            try:
                body = await request.json()
            except (ValueError, SerializationException) as exc:
                return self._error(None, JSONParseError(message=str(exc)))
            if not isinstance(body, dict):
                return self._error(None, InvalidRequestError())
            candidate_id = body.get("id", _MISSING_ID)
            if candidate_id is not _MISSING_ID:
                if isinstance(candidate_id, bool) or not isinstance(candidate_id, str | int | float | type(None)):
                    return self._error(None, InvalidRequestError(message="Invalid request ID"))
                if isinstance(candidate_id, float) and not math.isfinite(candidate_id):
                    return self._error(None, InvalidRequestError(message="Invalid request ID"))
                request_id = candidate_id
            if body.get("jsonrpc") != "2.0":
                return self._error(
                    request_id, InvalidRequestError(message="Invalid request: 'jsonrpc' must be exactly '2.0'")
                )
            method = body.get("method")
            if not isinstance(method, str) or not method:
                return self._error(request_id, InvalidRequestError(message="Method is required"))
            raw_params = body.get("params", {})
            if not isinstance(raw_params, dict):
                return self._error(request_id, InvalidRequestError(message="Parameters must be an object"))
            if candidate_id is _MISSING_ID:
                return Response(content=None, status_code=HTTP_204_NO_CONTENT)
            self._validate_version(request)
            model = self._models.get(method)
            if model is None:
                return self._error(request_id, MethodNotFoundError())
            try:
                params = ParseDict(raw_params, model())
            except Exception as exc:  # noqa: BLE001
                return self._error(request_id, InvalidParamsError(data=str(exc)))

            context = await self._build_context(request, params, method, request_id)
            if method in ("SendStreamingMessage", "SubscribeToTask"):
                return await self._stream(request, method, params, context, request_id)
            result = await self._dispatch(method, params, context)
            return self._response(request, _success_response(request_id, result), context)
        except A2AError as exc:
            return self._response(request, self._error(request_id, exc), context)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Unhandled A2A JSON-RPC error")
            return self._response(request, self._error(request_id, InternalError()), context)

    async def _dispatch(  # noqa: PLR0911
        self, method: "str", params: "Any", context: "ServerCallContext"
    ) -> "dict[str, Any] | None":
        result: Any
        if method == "SendMessage":
            result = await self.handler.on_message_send(params, context)
            response = (
                SendMessageResponse(task=result) if isinstance(result, Task) else SendMessageResponse(message=result)
            )
            return _message_to_dict(response)
        if method == "GetTask":
            result = await self.handler.on_get_task(params, context)
            if result is None:
                raise TaskNotFoundError
            return _message_to_dict(result)
        if method == "ListTasks":
            return _message_to_dict(
                await self.handler.on_list_tasks(params, context), always_print_fields_with_no_presence=True
            )
        if method == "CancelTask":
            result = await self.handler.on_cancel_task(params, context)
            if result is None:
                raise TaskNotFoundError
            return _message_to_dict(result)
        if method == "CreateTaskPushNotificationConfig":
            return _message_to_dict(await self.handler.on_create_task_push_notification_config(params, context))
        if method == "GetTaskPushNotificationConfig":
            return _message_to_dict(await self.handler.on_get_task_push_notification_config(params, context))
        if method == "ListTaskPushNotificationConfigs":
            return _message_to_dict(await self.handler.on_list_task_push_notification_configs(params, context))
        if method == "DeleteTaskPushNotificationConfig":
            await self.handler.on_delete_task_push_notification_config(params, context)
            return None
        if method == "GetExtendedAgentCard":
            return _message_to_dict(await self.handler.on_get_extended_agent_card(params, context))
        raise UnsupportedOperationError(message=f"Method {method} is not supported.")

    async def _stream(
        self,
        request: "Request[Any, Any, Any]",
        method: "str",
        params: "Any",
        context: "ServerCallContext",
        request_id: "str | int | float | None",
    ) -> "ServerSentEvent":
        stream = (
            self.handler.on_message_send_stream(params, context)
            if method == "SendStreamingMessage"
            else self.handler.on_subscribe_to_task(params, context)
        )
        try:
            first = await anext(stream)
        except StopAsyncIteration:
            first = None
        except Exception:
            await stream.aclose()
            raise
        try:
            headers = self._extension_headers(request, context)
        except ValueError:
            await stream.aclose()
            raise

        async def events() -> "AsyncGenerator[ServerSentEventMessage, None]":
            def encode(item: "Any") -> "ServerSentEventMessage":
                response = proto_utils.to_stream_response(item)
                return ServerSentEventMessage(data=_sse_data(_success_response(request_id, _message_to_dict(response))))

            try:
                if first is not None:
                    yield encode(first)
                async for item in stream:
                    yield encode(item)
            except A2AError as exc:
                yield ServerSentEventMessage(data=_sse_data(self._error(request_id, exc)), event="error")
            except Exception as exc:
                logger.exception("Unhandled A2A SSE stream error")
                yield ServerSentEventMessage(data=_sse_data(self._error(request_id, exc)), event="error")
            finally:
                await stream.aclose()

        return ServerSentEvent(events(), headers=headers)


class LitestarA2A(InitPluginProtocol):
    """Register an A2A 1.0 request handler on Litestar-native routes.

    This request-only adapter declines JSON-RPC notifications without executing
    them and returns HTTP 204. Explicit null request IDs still receive responses.
    """

    def __init__(
        self, agent_card: "AgentCard", request_handler: "RequestHandler", config: "A2AConfig | None" = None
    ) -> "None":
        self.agent_card = agent_card
        self.request_handler = request_handler
        self.config = config or A2AConfig()
        _validate_card(agent_card, self.config.path)

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        occupied = {
            route.path
            for entry in app_config.route_handlers
            for route in Router(path="", route_handlers=[entry]).routes
        }
        collisions = occupied.intersection((self.config.path, self.config.agent_card_path))
        if collisions:
            msg = f"A2A route collision: {', '.join(sorted(collisions))}"
            raise ValueError(msg)

        transport = _JsonRpcTransport(self.request_handler, self.config, self.agent_card)
        card_body = to_json(_message_to_dict(self.agent_card), as_bytes=True)
        card_etag = f'"{hashlib.sha256(card_body).hexdigest()}"'
        card_headers = {"ETag": card_etag, "Cache-Control": f"public, max-age={self.config.agent_card_max_age}"}

        @post(
            self.config.path,
            guards=self.config.guards,
            opt={"exclude_from_csrf": True, **self.config.route_opt},
            status_code=200,
            include_in_schema=self.config.include_in_schema,
        )
        async def a2a_endpoint(request: "Request[Any, Any, Any]") -> "Response[Any]":
            result = await transport.handle(request)
            if isinstance(result, Response):
                return result
            return Response(content=result, media_type=MediaType.JSON)

        @get(
            self.config.agent_card_path,
            include_in_schema=self.config.include_in_schema,
            opt={"exclude_from_auth": True, "exclude_from_csrf": True},
        )
        async def agent_card(request: "Request[Any, Any, Any]") -> "Response[bytes]":
            if request.headers.get("if-none-match") == card_etag:
                return Response(content=b"", status_code=HTTP_304_NOT_MODIFIED, headers=card_headers)
            return Response(content=card_body, media_type=MediaType.JSON, headers=card_headers)

        app_config.route_handlers.extend((a2a_endpoint, agent_card))
        app_config.on_shutdown.append(self.on_shutdown)
        return app_config

    async def on_shutdown(self, app: "Litestar") -> "None":
        del app
        close = getattr(self.request_handler, "aclose", None)
        if close is not None:
            result = close()
            if isawaitable(result):
                await result
