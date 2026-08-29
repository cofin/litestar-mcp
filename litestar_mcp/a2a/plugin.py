"""Litestar-native HTTP transport for the official A2A SDK contracts."""

from __future__ import annotations

import json
import logging
from inspect import isawaitable
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

from a2a.auth.user import User
from a2a.compat.v0_3 import types as types_v03
from a2a.compat.v0_3.request_handler import RequestHandler03
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
from a2a.server.request_handlers.response_helpers import agent_card_to_dict, build_error_response
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
from a2a.utils import constants, json_utils, proto_utils
from a2a.utils.errors import A2AError, TaskNotFoundError, UnsupportedOperationError, VersionNotSupportedError
from google.protobuf.json_format import MessageToDict, ParseDict  # type: ignore[import-untyped]
from jsonrpc.jsonrpc2 import JSONRPC20Request, JSONRPC20Response  # type: ignore[import-untyped]
from litestar import Litestar, Request, get, post
from litestar.exceptions import SerializationException
from litestar.plugins import InitPluginProtocol
from litestar.response import ServerSentEvent, ServerSentEventMessage

from litestar_mcp.a2a.config import A2AConfig

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from a2a.server.request_handlers import RequestHandler
    from litestar.config.app import AppConfig

logger = logging.getLogger(__name__)


def _message_to_dict(message: Any, **kwargs: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", MessageToDict(message, **kwargs))


def _success_response(request_id: str | int | None, result: Any) -> dict[str, Any]:
    return cast("dict[str, Any]", JSONRPC20Response(result=result, _id=request_id).data)


class _LitestarUser(User):
    def __init__(self, value: Any) -> None:
        self.value = value

    @property
    def is_authenticated(self) -> bool:
        return self.value is not None

    @property
    def user_name(self) -> str:
        for name in ("id", "sub", "username", "display_name"):
            value = self.value.get(name) if isinstance(self.value, dict) else getattr(self.value, name, None)
            if value is not None:
                return str(value)
        return str(self.value)


def _default_context_builder(request: Request[Any, Any, Any]) -> ServerCallContext:
    scope = request.scope
    headers = dict(request.headers)
    litestar_state = scope.get("state", {})
    tenant = scope.get("tenant") or litestar_state.get("tenant", "")
    return ServerCallContext(
        state={"auth": scope.get("auth"), "headers": headers, "litestar_state": litestar_state},
        user=_LitestarUser(scope.get("user")),
        tenant=str(tenant),
        requested_extensions=get_requested_extensions(
            request.headers.getall(HTTP_EXTENSION_HEADER) if HTTP_EXTENSION_HEADER in request.headers else []
        ),
    )


def _validate_card(card: AgentCard, path: str) -> None:
    for interface in card.supported_interfaces:
        if interface.protocol_version == "1.0" and interface.protocol_binding == "JSONRPC":
            url = urlparse(interface.url)
            if url.scheme and url.netloc and url.path.rstrip("/") == path.rstrip("/"):
                return
    msg = "AgentCard must advertise an absolute JSONRPC supported interface for protocol 1.0 matching the A2A path"
    raise ValueError(msg)


class _JsonRpcTransport:
    _models: dict[str, type[Any]] = {
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
    _v03_models: dict[str, type[Any]] = {
        "message/send": types_v03.SendMessageRequest,
        "message/stream": types_v03.SendStreamingMessageRequest,
        "tasks/get": types_v03.GetTaskRequest,
        "tasks/cancel": types_v03.CancelTaskRequest,
        "tasks/pushNotificationConfig/set": types_v03.SetTaskPushNotificationConfigRequest,
        "tasks/pushNotificationConfig/get": types_v03.GetTaskPushNotificationConfigRequest,
        "tasks/pushNotificationConfig/list": types_v03.ListTaskPushNotificationConfigRequest,
        "tasks/pushNotificationConfig/delete": types_v03.DeleteTaskPushNotificationConfigRequest,
        "tasks/resubscribe": types_v03.TaskResubscriptionRequest,
        "agent/getAuthenticatedExtendedCard": types_v03.GetAuthenticatedExtendedCardRequest,
    }

    def __init__(self, handler: RequestHandler, config: A2AConfig) -> None:
        self.handler = handler
        self.config = config
        self.v03_handler = RequestHandler03(request_handler=handler) if config.enable_v0_3_compat else None

    @staticmethod
    def _error(request_id: str | int | None, error: Exception | JSONRPCError | A2AError) -> dict[str, Any]:
        if not isinstance(error, A2AError | JSONRPCError):
            error = InternalError(message=str(error))
        return build_error_response(request_id, error)

    @staticmethod
    def _validate_version(context: ServerCallContext, expected: str) -> None:
        headers = context.state.get("headers", {})
        actual = headers.get(constants.VERSION_HEADER) or headers.get(constants.VERSION_HEADER.lower())
        actual = actual or constants.PROTOCOL_VERSION_0_3
        if str(actual).split(".", 1)[0] != expected.split(".", 1)[0]:
            raise VersionNotSupportedError(
                message=f"A2A version '{actual}' is not supported. Expected version '{expected}'."
            )

    async def handle(self, request: Request[Any, Any, Any]) -> dict[str, Any] | ServerSentEvent:  # noqa: PLR0911
        request_id: str | int | None = None
        try:
            try:
                body = await request.json()
            except (json.JSONDecodeError, SerializationException, UnicodeDecodeError) as exc:
                return self._error(None, JSONParseError(message=str(exc)))
            if isinstance(body, dict):
                candidate_id = body.get("id")
                request_id = candidate_id if isinstance(candidate_id, str | int) else None
            try:
                base_request = JSONRPC20Request.from_data(body)
                if not isinstance(base_request, JSONRPC20Request):
                    return self._error(request_id, InvalidRequestError(message="Batch requests are not supported"))
                if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
                    return self._error(
                        request_id, InvalidRequestError(message="Invalid request: 'jsonrpc' must be exactly '2.0'")
                    )
            except Exception as exc:  # noqa: BLE001
                return self._error(request_id, InvalidRequestError(data=str(exc)))

            method = base_request.method
            request_id = base_request._id  # noqa: SLF001
            if not method:
                return self._error(request_id, InvalidRequestError(message="Method is required"))
            if self.config.enable_v0_3_compat and "/" in method:
                return await self._handle_v03(request, body, request_id, method)
            model = self._models.get(method)
            if model is None:
                return self._error(request_id, MethodNotFoundError())
            try:
                params = ParseDict(body.get("params", {}), model())
            except Exception as exc:  # noqa: BLE001
                return self._error(request_id, InvalidParamsError(data=str(exc)))

            context = (self.config.context_builder or _default_context_builder)(request)
            context.tenant = getattr(params, "tenant", "") or context.tenant
            context.state["method"] = method
            context.state["request_id"] = request_id
            self._validate_version(context, constants.PROTOCOL_VERSION_1_0)
            if method in ("SendStreamingMessage", "SubscribeToTask"):
                return await self._stream(method, params, context, request_id)
            result = await self._dispatch(method, params, context)
            return _success_response(request_id, result)
        except A2AError as exc:
            return self._error(request_id, exc)
        except Exception as exc:
            logger.exception("Unhandled A2A JSON-RPC error")
            return self._error(request_id, InternalError(message=str(exc)))

    async def _dispatch(  # noqa: PLR0911
        self, method: str, params: Any, context: ServerCallContext
    ) -> dict[str, Any] | None:
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
        self, method: str, params: Any, context: ServerCallContext, request_id: str | int | None
    ) -> ServerSentEvent:
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

        async def events() -> AsyncGenerator[ServerSentEventMessage, None]:
            def encode(item: Any) -> ServerSentEventMessage:
                response = proto_utils.to_stream_response(item)
                payload = _success_response(request_id, _message_to_dict(response))
                return ServerSentEventMessage(data=json_utils.dumps(payload))

            try:
                if first is not None:
                    yield encode(first)
                async for item in stream:
                    yield encode(item)
            except A2AError as exc:
                yield ServerSentEventMessage(data=json_utils.dumps(self._error(request_id, exc)), event="error")
            except Exception as exc:
                logger.exception("Unhandled A2A SSE stream error")
                yield ServerSentEventMessage(data=json_utils.dumps(self._error(request_id, exc)), event="error")
            finally:
                await stream.aclose()

        return ServerSentEvent(events())

    async def _handle_v03(
        self, request: Request[Any, Any, Any], body: dict[str, Any], request_id: str | int | None, method: str
    ) -> dict[str, Any] | ServerSentEvent:
        model = self._v03_models.get(method)
        if model is None or self.v03_handler is None:
            return self._error(request_id, MethodNotFoundError())
        try:
            request_obj = model.model_validate(body)
        except Exception as exc:  # noqa: BLE001
            return self._error(request_id, InvalidRequestError(data=str(exc)))

        context = (self.config.context_builder or _default_context_builder)(request)
        context.tenant = getattr(request_obj.params, "tenant", "")
        context.state["method"] = method
        context.state["request_id"] = request_id
        self._validate_version(context, constants.PROTOCOL_VERSION_0_3)
        if method in ("message/stream", "tasks/resubscribe"):
            return await self._stream_v03(method, request_obj, context, request_id)
        result = await self._dispatch_v03(method, request_obj, context, request_id)
        return cast("dict[str, Any]", result.model_dump(mode="json", by_alias=True, exclude_none=True))

    async def _dispatch_v03(  # noqa: PLR0911
        self, method: str, request_obj: Any, context: ServerCallContext, request_id: str | int | None
    ) -> Any:
        handler = self.v03_handler
        if handler is None:
            raise UnsupportedOperationError(message="A2A v0.3 compatibility is disabled")
        result: Any
        if method == "message/send":
            result = await handler.on_message_send(request_obj, context)
            return types_v03.SendMessageResponse(
                root=types_v03.SendMessageSuccessResponse(id=request_id, result=result)
            )
        if method == "tasks/get":
            result = await handler.on_get_task(request_obj, context)
            return types_v03.GetTaskResponse(root=types_v03.GetTaskSuccessResponse(id=request_id, result=result))
        if method == "tasks/cancel":
            result = await handler.on_cancel_task(request_obj, context)
            return types_v03.CancelTaskResponse(root=types_v03.CancelTaskSuccessResponse(id=request_id, result=result))
        if method == "tasks/pushNotificationConfig/get":
            result = await handler.on_get_task_push_notification_config(request_obj, context)
            return types_v03.GetTaskPushNotificationConfigResponse(
                root=types_v03.GetTaskPushNotificationConfigSuccessResponse(id=request_id, result=result)
            )
        if method == "tasks/pushNotificationConfig/set":
            result = await handler.on_create_task_push_notification_config(request_obj, context)
            return types_v03.SetTaskPushNotificationConfigResponse(
                root=types_v03.SetTaskPushNotificationConfigSuccessResponse(id=request_id, result=result)
            )
        if method == "tasks/pushNotificationConfig/list":
            result = await handler.on_list_task_push_notification_configs(request_obj, context)
            return types_v03.ListTaskPushNotificationConfigResponse(
                root=types_v03.ListTaskPushNotificationConfigSuccessResponse(id=request_id, result=result)
            )
        if method == "tasks/pushNotificationConfig/delete":
            await handler.on_delete_task_push_notification_config(request_obj, context)
            return types_v03.DeleteTaskPushNotificationConfigResponse(
                root=types_v03.DeleteTaskPushNotificationConfigSuccessResponse(id=request_id, result=None)
            )
        if method == "agent/getAuthenticatedExtendedCard":
            result = await handler.on_get_extended_agent_card(request_obj, context)
            return types_v03.GetAuthenticatedExtendedCardResponse(
                root=types_v03.GetAuthenticatedExtendedCardSuccessResponse(id=request_id, result=result)
            )
        raise UnsupportedOperationError(message=f"Method {method} is not supported")

    async def _stream_v03(
        self, method: str, request_obj: Any, context: ServerCallContext, request_id: str | int | None
    ) -> ServerSentEvent:
        handler = self.v03_handler
        if handler is None:
            raise UnsupportedOperationError(message="A2A v0.3 compatibility is disabled")
        stream = cast(
            "AsyncGenerator[Any, None]",
            handler.on_message_send_stream(request_obj, context)
            if method == "message/stream"
            else handler.on_subscribe_to_task(request_obj, context),
        )
        try:
            first = await anext(stream)
        except StopAsyncIteration:
            first = None
        except Exception:
            await stream.aclose()
            raise

        async def events() -> AsyncGenerator[ServerSentEventMessage, None]:
            try:
                if first is not None:
                    yield ServerSentEventMessage(data=first.model_dump_json(by_alias=True, exclude_none=True))
                async for item in stream:
                    yield ServerSentEventMessage(data=item.model_dump_json(by_alias=True, exclude_none=True))
            except Exception as exc:  # noqa: BLE001
                error = types_v03.InternalError(message=str(exc))
                response = types_v03.SendStreamingMessageResponse(
                    root=types_v03.JSONRPCErrorResponse(id=request_id, error=error)
                )
                yield ServerSentEventMessage(
                    data=response.model_dump_json(by_alias=True, exclude_none=True), event="error"
                )
            finally:
                await stream.aclose()

        return ServerSentEvent(events())


class LitestarA2A(InitPluginProtocol):
    """Register an official A2A request handler on Litestar-native routes."""

    def __init__(self, agent_card: AgentCard, request_handler: RequestHandler, config: A2AConfig | None = None) -> None:
        self.agent_card = agent_card
        self.request_handler = request_handler
        self.config = config or A2AConfig()
        _validate_card(agent_card, self.config.path)

    def on_app_init(self, app_config: AppConfig) -> AppConfig:
        occupied = {path for handler in app_config.route_handlers for path in getattr(handler, "paths", ())}
        collisions = occupied.intersection((self.config.path, self.config.agent_card_path))
        if collisions:
            msg = f"A2A route collision: {', '.join(sorted(collisions))}"
            raise ValueError(msg)

        transport = _JsonRpcTransport(self.request_handler, self.config)

        @post(self.config.path, guards=self.config.guards, opt=self.config.route_opt, status_code=200)
        async def a2a_endpoint(request: Request[Any, Any, Any]) -> Any:
            return await transport.handle(request)

        @get(self.config.agent_card_path, include_in_schema=False, opt={"exclude_from_auth": True})
        async def agent_card() -> dict[str, Any]:
            return agent_card_to_dict(self.agent_card)

        app_config.route_handlers.extend((a2a_endpoint, agent_card))
        app_config.on_shutdown.append(self.on_shutdown)
        return app_config

    async def on_shutdown(self, app: Litestar) -> None:
        del app
        close = getattr(self.request_handler, "aclose", None)
        if close is not None:
            result = close()
            if isawaitable(result):
                await result
