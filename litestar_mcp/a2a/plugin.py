"""Litestar-native HTTP transport for the official A2A SDK contracts."""

import hashlib
import logging
import math
import re
from contextlib import aclosing
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import anyio
from a2a.auth.user import User
from a2a.extensions.common import HTTP_EXTENSION_HEADER, get_requested_extensions
from a2a.server.agent_execution import AgentExecutor
from a2a.server.context import ServerCallContext
from a2a.server.jsonrpc_models import (
    InternalError,
    InvalidParamsError,
    InvalidRequestError,
    JSONParseError,
    JSONRPCError,
    MethodNotFoundError,
)
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.response_helpers import build_error_response
from a2a.server.tasks import InMemoryTaskStore
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
from a2a.utils.error_handlers import build_rest_error_payload
from a2a.utils.errors import (
    A2AError,
    ExtensionSupportRequiredError,
    TaskNotFoundError,
    UnsupportedOperationError,
    VersionNotSupportedError,
)
from google.protobuf.json_format import MessageToDict, Parse, ParseDict, ParseError  # type: ignore[import-untyped]
from litestar import Litestar, MediaType, Request, Response, Router, delete, get, post, route
from litestar.exceptions import HTTPException, SerializationException
from litestar.params import FromPath  # noqa: TC002
from litestar.plugins import InitPluginProtocol
from litestar.response import ServerSentEvent, ServerSentEventMessage
from litestar.status_codes import HTTP_204_NO_CONTENT, HTTP_304_NOT_MODIFIED, HTTP_404_NOT_FOUND

from litestar_mcp.a2a.config import A2AConfig
from litestar_mcp.core._streaming import StreamCleanupMiddleware, prefetch_stream, start_stream
from litestar_mcp.core.serialization import to_json

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from a2a.server.request_handlers import RequestHandler
    from anyio.streams.memory import MemoryObjectSendStream
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
        for name in ("id", "sub", "username"):
            value = self.value.get(name) if isinstance(self.value, dict) else getattr(self.value, name, None)
            if value is not None:
                return str(value)
        return ""


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


def _is_version_1_0(version: "str") -> "bool":
    match = _VERSION_PATTERN.fullmatch(version)
    return match is not None and match.group(1, 2) == ("1", "0")


def _validate_card(card: "AgentCard", path: "str") -> "None":
    """Require an absolute JSONRPC or HTTP+JSON 1.0 interface whose URL path ends with the A2A path.

    A suffix match allows the app to be served under a proxy prefix or ASGI
    ``root_path``, where the public URL is longer than the route path.
    """
    route_path = path.rstrip("/")
    matched = False
    for interface in card.supported_interfaces:
        if interface.protocol_binding == "HTTP_JSON":
            interface.protocol_binding = constants.TransportProtocol.HTTP_JSON.value
        if _is_version_1_0(interface.protocol_version) and interface.protocol_binding in {
            constants.TransportProtocol.JSONRPC.value,
            constants.TransportProtocol.HTTP_JSON.value,
        }:
            url = urlparse(interface.url)
            if url.scheme and url.netloc and url.path.rstrip("/").endswith(route_path):
                matched = True
    if not matched:
        msg = (
            "AgentCard must advertise an absolute JSONRPC or HTTP+JSON supported interface "
            "for protocol 1.0 matching the A2A path"
        )
        raise ValueError(msg)


class _LitestarQueryParams:
    """Adapter exposing Starlette-compatible ``getlist`` over Litestar ``query_params``."""

    def __init__(self, query_params: "Any") -> "None":
        self._query_params = query_params

    def keys(self) -> "Any":
        return self._query_params.keys()

    def getlist(self, key: "str") -> "list[Any]":
        return list(self._query_params.get_all(key, default=[]))


def _etag_matches(if_none_match: "str", etag: "str") -> "bool":
    """Apply RFC 9110 weak comparison of ``If-None-Match`` against ``etag``."""
    candidates = [candidate.strip() for candidate in if_none_match.split(",")]
    return "*" in candidates or etag in (candidate.removeprefix("W/") for candidate in candidates)


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

    @staticmethod
    def _resolve_tenant(
        request: "Request[Any, Any, Any]",
        explicit_tenant: "str" = "",
        *,
        tenant_header: "str | None" = "X-A2A-Tenant",
    ) -> "str":
        """Resolve A2A tenant from explicit parameter, URL path parameter, or request header."""
        if explicit_tenant:
            return explicit_tenant
        path_params = getattr(request, "path_params", None)
        if isinstance(path_params, dict):
            path_tenant = path_params.get("tenant")
            if isinstance(path_tenant, str) and path_tenant:
                return path_tenant
        if tenant_header:
            header_tenant = request.headers.get(tenant_header, "").strip()
            if header_tenant:
                return header_tenant
        return ""

    def extension_headers(self, request: "Request[Any, Any, Any]", context: "ServerCallContext") -> "dict[str, str]":
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

    async def build_context(
        self, request: "Request[Any, Any, Any]", params: "Any", method: "str", request_id: "str | int | float | None"
    ) -> "ServerCallContext":
        context = _default_context_builder(request)
        context.tenant = self._resolve_tenant(
            request,
            getattr(params, "tenant", ""),
            tenant_header=self.config.tenant_header,
        )
        context.state["method"] = method
        context.state["request_id"] = request_id
        requested = set(context.requested_extensions)
        required = {extension.uri for extension in self.agent_card.capabilities.extensions if extension.required}
        if not required <= requested:
            raise ExtensionSupportRequiredError
        if self.config.context_builder is not None:
            result = self.config.context_builder(request, context)
            context = await result if isawaitable(result) else result
        user = context.user
        if isinstance(user, _LitestarUser) and user.is_authenticated and not user.user_name:
            msg = (
                "The authenticated principal has no id, sub or username, so it would share the anonymous "
                "task owner. Set context.user in an A2AConfig.context_builder."
            )
            raise RuntimeError(msg)
        self.extension_headers(request, context)
        return context

    def _response(
        self,
        request: "Request[Any, Any, Any]",
        payload: "dict[str, Any]",
        context: "ServerCallContext | None",
    ) -> "Response[Any]":
        try:
            headers = self.extension_headers(request, context) if context is not None else {}
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
    def validate_version(request: "Request[Any, Any, Any]") -> "None":
        actual = request.headers.get(constants.VERSION_HEADER, "").strip(" \t")
        if not actual:
            raise VersionNotSupportedError(
                message=f"The {constants.VERSION_HEADER} header is required. Expected version '1.0'."
            )
        if not _is_version_1_0(actual):
            raise VersionNotSupportedError(message=f"A2A version '{actual}' is not supported. Expected version '1.0'.")

    async def _read_envelope(  # noqa: PLR0911
        self, request: "Request[Any, Any, Any]"
    ) -> "tuple[Any, str, dict[str, Any]] | dict[str, Any]":
        """Validate the JSON-RPC envelope.

        Returns:
            ``(id, method, params)``, where ``id`` is ``_MISSING_ID`` for a
            notification, or the JSON-RPC error response to send instead.
        """
        if not await request.body():
            return self._error(None, JSONParseError(message="Request body is empty"))
        try:
            body = await request.json()
        except (ValueError, SerializationException) as exc:
            return self._error(None, JSONParseError(message=str(exc)))
        if not isinstance(body, dict):
            return self._error(None, InvalidRequestError())
        candidate_id = body.get("id", _MISSING_ID)
        request_id = None
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
        if isinstance(raw_params, list):
            return self._error(request_id, InvalidParamsError(message="Parameters must be an object"))
        if not isinstance(raw_params, dict):
            return self._error(request_id, InvalidRequestError(message="Parameters must be an object"))
        return candidate_id, method, raw_params

    async def handle(self, request: "Request[Any, Any, Any]") -> "dict[str, Any] | Response[Any]":  # noqa: PLR0911
        request_id: str | int | float | None = None
        context: ServerCallContext | None = None
        try:
            envelope = await self._read_envelope(request)
            if isinstance(envelope, dict):
                return envelope
            candidate_id, method, raw_params = envelope
            if candidate_id is _MISSING_ID:
                return Response(content=None, status_code=HTTP_204_NO_CONTENT)
            request_id = candidate_id
            self.validate_version(request)
            model = self._models.get(method)
            if model is None:
                return self._error(request_id, MethodNotFoundError())
            try:
                params = ParseDict(raw_params, model())
            except Exception as exc:  # noqa: BLE001
                return self._error(request_id, InvalidParamsError(data=str(exc)))

            context = await self.build_context(request, params, method, request_id)
            if method in ("SendStreamingMessage", "SubscribeToTask"):
                return await self._stream(request, method, params, context, request_id)
            result = await self.dispatch(method, params, context)
            return self._response(request, _success_response(request_id, result), context)
        except A2AError as exc:
            return self._response(request, self._error(request_id, exc), context)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Unhandled A2A JSON-RPC error")
            return self._response(request, self._error(request_id, InternalError()), context)

    async def dispatch(  # noqa: PLR0911
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
            listed = _message_to_dict(
                await self.handler.on_list_tasks(params, context), always_print_fields_with_no_presence=True
            )
            if not params.include_artifacts:
                for task in listed.get("tasks", []):
                    task.pop("artifacts", None)
            return listed
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
        headers: dict[str, str] | None = None

        async def produce(sender: "MemoryObjectSendStream[ServerSentEventMessage]") -> None:
            nonlocal headers
            stream = (
                self.handler.on_message_send_stream(params, context)
                if method == "SendStreamingMessage"
                else self.handler.on_subscribe_to_task(params, context)
            )
            async with aclosing(stream):
                async for item in stream:
                    if headers is None:
                        headers = self.extension_headers(request, context)
                    response = proto_utils.to_stream_response(item)
                    if response.WhichOneof("payload") is None:
                        msg = "A2A stream event has no supported payload"
                        raise ValueError(msg)
                    payload = _success_response(request_id, _message_to_dict(response))
                    await sender.send(ServerSentEventMessage(data=_sse_data(payload)))
                if headers is None:
                    headers = self.extension_headers(request, context)

        owner = start_stream(request.scope, produce, capacity=1, cleanup_timeout=self.config.stream_cleanup_timeout)
        try:
            first = await prefetch_stream(request.scope, request.receive, owner)
        except anyio.EndOfStream:
            first = None

        async def events() -> "AsyncGenerator[ServerSentEventMessage, None]":
            try:
                if first is not None:
                    yield first
                async for item in owner.receiver:
                    yield item
                await owner.result()
            except A2AError as exc:
                yield ServerSentEventMessage(data=_sse_data(self._error(request_id, exc)), event="error")
            except Exception as exc:
                logger.exception("Unhandled A2A SSE stream error")
                yield ServerSentEventMessage(data=_sse_data(self._error(request_id, exc)), event="error")
            finally:
                await owner.close()

        return ServerSentEvent(events(), headers=headers)


class _HttpJsonTransport:
    """Native Litestar HTTP+JSON REST transport for A2A 1.0."""

    def __init__(self, rpc_transport: "_JsonRpcTransport") -> "None":
        self._rpc = rpc_transport
        self.config = rpc_transport.config

    @staticmethod
    def _query_params(request: "Request[Any, Any, Any]") -> "Any":
        return _LitestarQueryParams(request.query_params)

    @staticmethod
    def _rest_error_payload(error: "Any") -> "dict[str, Any]":
        return build_rest_error_payload(error)

    def _json_response(
        self,
        request: "Request[Any, Any, Any]",
        payload: "dict[str, Any]",
        context: "ServerCallContext | None",
    ) -> "Response[Any]":
        try:
            headers = self._rpc.extension_headers(request, context) if context is not None else {}
        except ValueError:
            logger.exception("Invalid A2A extension activation")
            return self._error_response(request, InternalError(), None)
        return Response(content=payload, media_type=constants.A2A_JSON_MEDIA_TYPE, headers=headers)

    def _error_response(
        self,
        request: "Request[Any, Any, Any]",
        error: "Any",
        context: "ServerCallContext | None",
    ) -> "Response[Any]":
        headers: dict[str, str] = {}
        if context is not None:
            try:
                headers = self._rpc.extension_headers(request, context)
            except ValueError:
                headers = {}
        payload = self._rest_error_payload(error)
        status_code = int(payload.get("error", {}).get("code", 500))
        return Response(content=payload, status_code=status_code, media_type=MediaType.JSON, headers=headers)

    async def _run_unary(
        self,
        request: "Request[Any, Any, Any]",
        method: "str",
        params: "Any",
    ) -> "Response[Any]":
        context: ServerCallContext | None = None
        try:
            self._rpc.validate_version(request)
            context = await self._rpc.build_context(request, params, method, None)
            result = await self._rpc.dispatch(method, params, context)
            return self._json_response(request, result or {}, context)
        except (A2AError, ParseError) as exc:
            return self._error_response(request, exc, context)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Unhandled A2A HTTP+JSON error")
            return self._error_response(request, InternalError(), context)

    async def _stream(
        self,
        request: "Request[Any, Any, Any]",
        method: "str",
        params: "Any",
        context: "ServerCallContext",
    ) -> "ServerSentEvent":
        headers: dict[str, str] | None = None

        async def produce(sender: "MemoryObjectSendStream[ServerSentEventMessage]") -> None:
            nonlocal headers
            stream = (
                self._rpc.handler.on_message_send_stream(params, context)
                if method == "SendStreamingMessage"
                else self._rpc.handler.on_subscribe_to_task(params, context)
            )
            async with aclosing(stream):
                async for item in stream:
                    if headers is None:
                        headers = self._rpc.extension_headers(request, context)
                    response = proto_utils.to_stream_response(item)
                    if response.WhichOneof("payload") is None:
                        msg = "A2A stream event has no supported payload"
                        raise ValueError(msg)
                    await sender.send(ServerSentEventMessage(data=_sse_data(_message_to_dict(response))))
                if headers is None:
                    headers = self._rpc.extension_headers(request, context)

        owner = start_stream(request.scope, produce, capacity=1, cleanup_timeout=self.config.stream_cleanup_timeout)
        try:
            first = await prefetch_stream(request.scope, request.receive, owner)
        except anyio.EndOfStream:
            first = None

        async def events() -> "AsyncGenerator[ServerSentEventMessage, None]":
            try:
                if first is not None:
                    yield first
                async for item in owner.receiver:
                    yield item
                await owner.result()
            except A2AError as exc:
                yield ServerSentEventMessage(data=_sse_data(self._rest_error_payload(exc)), event="error")
            except Exception:
                logger.exception("Unhandled A2A HTTP+JSON SSE stream error")
                yield ServerSentEventMessage(data=_sse_data(self._rest_error_payload(InternalError())), event="error")
            finally:
                await owner.close()

        return ServerSentEvent(events(), headers=headers)

    async def send_message(self, request: "Request[Any, Any, Any]") -> "Response[Any]":
        try:
            self._rpc.validate_version(request)
            params = SendMessageRequest()
            raw_body = await request.body()
            if raw_body:
                Parse(raw_body, params, ignore_unknown_fields=True)
        except (A2AError, ParseError) as exc:
            return self._error_response(request, exc, None)
        return await self._run_unary(request, "SendMessage", params)

    async def send_message_stream(self, request: "Request[Any, Any, Any]") -> "Response[Any] | ServerSentEvent":
        context: ServerCallContext | None = None
        try:
            self._rpc.validate_version(request)
            params = SendMessageRequest()
            raw_body = await request.body()
            if raw_body:
                Parse(raw_body, params, ignore_unknown_fields=True)
            context = await self._rpc.build_context(request, params, "SendStreamingMessage", None)
            return await self._stream(request, "SendStreamingMessage", params, context)
        except (A2AError, ParseError) as exc:
            return self._error_response(request, exc, context)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Unhandled A2A HTTP+JSON stream setup error")
            return self._error_response(request, InternalError(), context)

    async def _handle_task_get(
        self,
        request: "Request[Any, Any, Any]",
        task_segment: "str",
    ) -> "Response[Any] | ServerSentEvent":
        self._rpc.validate_version(request)
        if task_segment.endswith(":subscribe"):
            sub_params = SubscribeToTaskRequest(id=task_segment.removesuffix(":subscribe"))
            context = await self._rpc.build_context(request, sub_params, "SubscribeToTask", None)
            return await self._stream(request, "SubscribeToTask", sub_params, context)
        get_params = GetTaskRequest()
        proto_utils.parse_params(self._query_params(request), get_params)
        get_params.id = task_segment
        return await self._run_unary(request, "GetTask", get_params)

    async def handle_task_segment(
        self,
        request: "Request[Any, Any, Any]",
        task_segment: "str",
    ) -> "Response[Any] | ServerSentEvent":
        context: ServerCallContext | None = None
        try:
            if request.method == "GET":
                return await self._handle_task_get(request, task_segment)

            if task_segment.endswith(":cancel"):
                self._rpc.validate_version(request)
                cancel_params = CancelTaskRequest()
                raw_body = await request.body()
                if raw_body:
                    Parse(raw_body, cancel_params, ignore_unknown_fields=True)
                cancel_params.id = task_segment.removesuffix(":cancel")
                return await self._run_unary(request, "CancelTask", cancel_params)

            if task_segment.endswith(":subscribe"):
                self._rpc.validate_version(request)
                post_sub_params = SubscribeToTaskRequest()
                raw_body = await request.body()
                if raw_body:
                    Parse(raw_body, post_sub_params, ignore_unknown_fields=True)
                post_sub_params.id = task_segment.removesuffix(":subscribe")
                context = await self._rpc.build_context(request, post_sub_params, "SubscribeToTask", None)
                return await self._stream(request, "SubscribeToTask", post_sub_params, context)

            return Response(content={"detail": "Not Found"}, status_code=HTTP_404_NOT_FOUND, media_type=MediaType.JSON)
        except (A2AError, ParseError) as exc:
            return self._error_response(request, exc, context)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Unhandled A2A HTTP+JSON task route error")
            return self._error_response(request, InternalError(), context)

    async def list_tasks(self, request: "Request[Any, Any, Any]") -> "Response[Any]":
        try:
            self._rpc.validate_version(request)
            params = ListTasksRequest()
            proto_utils.parse_params(self._query_params(request), params)
        except (A2AError, ParseError) as exc:
            return self._error_response(request, exc, None)
        return await self._run_unary(request, "ListTasks", params)

    async def create_push_config(self, request: "Request[Any, Any, Any]", task_id: "str") -> "Response[Any]":
        try:
            self._rpc.validate_version(request)
            params = TaskPushNotificationConfig()
            raw_body = await request.body()
            if raw_body:
                Parse(raw_body, params, ignore_unknown_fields=True)
            params.task_id = task_id
        except (A2AError, ParseError) as exc:
            return self._error_response(request, exc, None)
        return await self._run_unary(request, "CreateTaskPushNotificationConfig", params)

    async def list_push_configs(self, request: "Request[Any, Any, Any]", task_id: "str") -> "Response[Any]":
        try:
            self._rpc.validate_version(request)
            params = ListTaskPushNotificationConfigsRequest()
            proto_utils.parse_params(self._query_params(request), params)
            params.task_id = task_id
        except (A2AError, ParseError) as exc:
            return self._error_response(request, exc, None)
        return await self._run_unary(request, "ListTaskPushNotificationConfigs", params)

    async def get_push_config(
        self,
        request: "Request[Any, Any, Any]",
        task_id: "str",
        push_id: "str",
    ) -> "Response[Any]":
        params = GetTaskPushNotificationConfigRequest(task_id=task_id, id=push_id)
        return await self._run_unary(request, "GetTaskPushNotificationConfig", params)

    async def delete_push_config(
        self,
        request: "Request[Any, Any, Any]",
        task_id: "str",
        push_id: "str",
    ) -> "Response[Any]":
        params = DeleteTaskPushNotificationConfigRequest(task_id=task_id, id=push_id)
        return await self._run_unary(request, "DeleteTaskPushNotificationConfig", params)

    async def extended_agent_card(self, request: "Request[Any, Any, Any]") -> "Response[Any]":
        params = GetExtendedAgentCardRequest()
        return await self._run_unary(request, "GetExtendedAgentCard", params)


class LitestarA2A(InitPluginProtocol):
    """Register an A2A 1.0 request handler on Litestar-native routes.

    This request-only adapter declines JSON-RPC notifications without executing
    them and returns HTTP 204. Explicit null request IDs still receive responses.
    """

    agent_card: "AgentCard"
    """Public A2A 1.0 `AgentCard` served at the discovery endpoint."""
    request_handler: "RequestHandler"
    """Resolved A2A `RequestHandler` dispatching JSON-RPC and HTTP+JSON operations."""
    config: A2AConfig
    """Active `A2AConfig` governing route paths, guards, and protocol bindings."""

    def __init__(
        self,
        agent_card: "AgentCard",
        request_handler: "RequestHandler | AgentExecutor",
        config: "A2AConfig | None" = None,
    ) -> "None":
        """Initialize the plugin.

        Args:
            agent_card: Agent card advertised at the well-known card route.
            request_handler: An A2A ``RequestHandler``, or an ``AgentExecutor`` that is
                wrapped in ``DefaultRequestHandler`` with an ``InMemoryTaskStore``.
            config: Route and protocol configuration. Defaults to ``A2AConfig()``.
        """
        self.agent_card = agent_card
        self.request_handler: RequestHandler = (
            DefaultRequestHandler(agent_executor=request_handler, task_store=InMemoryTaskStore(), agent_card=agent_card)
            if isinstance(request_handler, AgentExecutor)
            else request_handler
        )
        self.config = config or A2AConfig()
        _validate_card(agent_card, self.config.path)

    def _build_http_json_handlers(
        self,
        rest_transport: "_HttpJsonTransport",
        base_prefix: "str",
        route_opt: "dict[str, Any]",
    ) -> "list[Any]":
        def _paths(suffix: "str") -> "list[str]":
            return [f"{base_prefix}{suffix}", f"{base_prefix}/{{tenant:str}}{suffix}"]

        common_kwargs: dict[str, Any] = {
            "guards": self.config.guards,
            "opt": route_opt,
            "include_in_schema": self.config.include_in_schema,
        }

        @post(_paths("/message:send"), status_code=200, **common_kwargs)
        async def a2a_rest_message_send(
            request: "Request[Any, Any, Any]",
        ) -> "Response[Any]":
            return await rest_transport.send_message(request)

        @post(
            _paths("/message:stream"),
            status_code=200,
            middleware=[StreamCleanupMiddleware()],
            **common_kwargs,
        )
        async def a2a_rest_message_stream(
            request: "Request[Any, Any, Any]",
        ) -> "Response[Any] | ServerSentEvent":
            return await rest_transport.send_message_stream(request)

        @route(
            _paths("/tasks/{task_segment:str}"),
            http_method=["GET", "POST"],
            status_code=200,
            middleware=[StreamCleanupMiddleware()],
            **common_kwargs,
        )
        async def a2a_rest_task_segment(
            request: "Request[Any, Any, Any]",
            task_segment: "FromPath[str]",
        ) -> "Response[Any] | ServerSentEvent":
            return await rest_transport.handle_task_segment(request, task_segment)

        @get(_paths("/tasks"), **common_kwargs)
        async def a2a_rest_list_tasks(
            request: "Request[Any, Any, Any]",
        ) -> "Response[Any]":
            return await rest_transport.list_tasks(request)

        @post(_paths("/tasks/{id:str}/pushNotificationConfigs"), status_code=200, **common_kwargs)
        async def a2a_rest_create_push_config(
            request: "Request[Any, Any, Any]",
            id: "FromPath[str]",  # noqa: A002
        ) -> "Response[Any]":
            return await rest_transport.create_push_config(request, id)

        @get(_paths("/tasks/{id:str}/pushNotificationConfigs"), **common_kwargs)
        async def a2a_rest_list_push_configs(
            request: "Request[Any, Any, Any]",
            id: "FromPath[str]",  # noqa: A002
        ) -> "Response[Any]":
            return await rest_transport.list_push_configs(request, id)

        @get(_paths("/tasks/{id:str}/pushNotificationConfigs/{push_id:str}"), **common_kwargs)
        async def a2a_rest_get_push_config(
            request: "Request[Any, Any, Any]",
            id: "FromPath[str]",  # noqa: A002
            push_id: "FromPath[str]",
        ) -> "Response[Any]":
            return await rest_transport.get_push_config(request, id, push_id)

        @delete(
            _paths("/tasks/{id:str}/pushNotificationConfigs/{push_id:str}"),
            status_code=200,
            **common_kwargs,
        )
        async def a2a_rest_delete_push_config(
            request: "Request[Any, Any, Any]",
            id: "FromPath[str]",  # noqa: A002
            push_id: "FromPath[str]",
        ) -> "Response[Any]":
            return await rest_transport.delete_push_config(request, id, push_id)

        @get(_paths("/extendedAgentCard"), **common_kwargs)
        async def a2a_rest_extended_agent_card(
            request: "Request[Any, Any, Any]",
        ) -> "Response[Any]":
            return await rest_transport.extended_agent_card(request)

        return [
            a2a_rest_message_send,
            a2a_rest_message_stream,
            a2a_rest_task_segment,
            a2a_rest_list_tasks,
            a2a_rest_create_push_config,
            a2a_rest_list_push_configs,
            a2a_rest_get_push_config,
            a2a_rest_delete_push_config,
            a2a_rest_extended_agent_card,
        ]

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        """Register the A2A JSON-RPC, agent card, and optional HTTP+JSON routes on the application.

        Args:
            app_config: The Litestar application configuration being initialized.

        Returns:
            The updated application configuration.
        """
        occupied = {
            route_obj.path
            for entry in app_config.route_handlers
            for route_obj in Router(path="", route_handlers=[entry]).routes
        }
        collisions = occupied.intersection((self.config.path, self.config.agent_card_path))
        if collisions:
            msg = f"A2A route collision: {', '.join(sorted(collisions))}"
            raise ValueError(msg)

        transport = _JsonRpcTransport(self.request_handler, self.config, self.agent_card)
        card_body = to_json(_message_to_dict(self.agent_card), as_bytes=True)
        card_etag = f'"{hashlib.sha256(card_body).hexdigest()}"'
        card_headers = {"ETag": card_etag, "Cache-Control": f"public, max-age={self.config.agent_card_max_age}"}
        base_prefix = "" if self.config.path == "/" else self.config.path
        rpc_opt = {"exclude_from_csrf": True, **self.config.route_opt}
        if self.config.auth is not None:
            rpc_opt["auth"] = self.config.auth
        card_opt = {"exclude_from_auth": True, "exclude_from_csrf": True, **self.config.agent_card_route_opt}

        @post(
            [self.config.path, f"{base_prefix}/{{tenant:str}}"],
            guards=self.config.guards,
            middleware=[StreamCleanupMiddleware()],
            opt=rpc_opt,
            status_code=200,
            include_in_schema=self.config.include_in_schema,
        )
        async def a2a_endpoint(
            request: "Request[Any, Any, Any]",
        ) -> "Response[Any]":
            result = await transport.handle(request)
            if isinstance(result, Response):
                return result
            return Response(content=result, media_type=MediaType.JSON)

        @get(
            self.config.agent_card_path,
            include_in_schema=self.config.include_in_schema,
            opt=card_opt,
        )
        async def agent_card(request: "Request[Any, Any, Any]") -> "Response[bytes]":
            if _etag_matches(request.headers.get("if-none-match", ""), card_etag):
                return Response(content=b"", status_code=HTTP_304_NOT_MODIFIED, headers=card_headers)
            return Response(content=card_body, media_type=MediaType.JSON, headers=card_headers)

        app_config.route_handlers.extend((a2a_endpoint, agent_card))
        if self.config.enable_http_json:
            rest_transport = _HttpJsonTransport(transport)
            app_config.route_handlers.extend(self._build_http_json_handlers(rest_transport, base_prefix, rpc_opt))
        app_config.on_shutdown.append(self.on_shutdown)
        return app_config

    async def on_shutdown(self, app: "Litestar") -> "None":
        """Close the A2A request handler if it defines an `aclose` coroutine or method.

        Args:
            app: The shutting-down Litestar application instance.
        """
        del app
        close = getattr(self.request_handler, "aclose", None)
        if close is not None:
            result = close()
            if isawaitable(result):
                await result
