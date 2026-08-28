# ruff: noqa: C901, PLR0911
"""MCP 2026-07-28 JSON-RPC transport for Litestar applications."""

import asyncio
import base64
import binascii
from typing import TYPE_CHECKING, Any

from litestar import Controller, Litestar, MediaType, Request, Response, post
from litestar.di import NamedDependency  # noqa: TC002
from litestar.exceptions import SerializationException
from litestar.response import ServerSentEvent, ServerSentEventMessage
from litestar.serialization import decode_json, encode_json
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
)

from litestar_mcp.config import MCPConfig  # noqa: TC001
from litestar_mcp.jsonrpc import (
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JSONRPCError,
    JSONRPCErrorException,
    JSONRPCRouter,
    error_response,
    parse_request,
)
from litestar_mcp.registry import PromptRegistration, Registry  # noqa: TC001
from litestar_mcp.schema_builder import generate_schema_for_handler, iter_mcp_header_fields
from litestar_mcp.services.handler import MCPHandlerService, MCPRequestContext
from litestar_mcp.tasks import MCPTaskStore  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from litestar_mcp.jsonrpc import JSONRPCRequest

MCP_PROTOCOL_VERSION = "2026-07-28"
MCP_PROTOCOL_VERSION_HEADER = "MCP-Protocol-Version"
MCP_METHOD_HEADER = "Mcp-Method"
MCP_NAME_HEADER = "Mcp-Name"

HEADER_MISMATCH = -32020
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021
UNSUPPORTED_PROTOCOL_VERSION = -32022

_NAME_FIELDS = {
    "tools/call": "name",
    "resources/read": "uri",
    "prompts/get": "name",
    "tasks/get": "taskId",
    "tasks/update": "taskId",
    "tasks/cancel": "taskId",
}
_CACHEABLE_METHODS = {
    "server/discover",
    "tools/list",
    "resources/list",
    "resources/templates/list",
    "resources/read",
    "prompts/list",
}
_BASE64_PREFIX = "=?base64?"
_BASE64_SUFFIX = "?="


def _error(
    request_id: "Any",
    *,
    code: "int",
    message: "str",
    status_code: "int",
    data: "Any | None" = None,
) -> "Response[Any]":
    response = Response(
        content=error_response(request_id, JSONRPCError(code=code, message=message, data=data)),
        status_code=status_code,
        media_type=MediaType.JSON,
    )
    response.headers[MCP_PROTOCOL_VERSION_HEADER] = MCP_PROTOCOL_VERSION
    return response


def _request_origin(request: "Request[Any, Any, Any]") -> "str":
    return f"{request.url.scheme}://{request.url.netloc}"


def _validate_origin(request: "Request[Any, Any, Any]", config: "MCPConfig") -> "Response[Any] | None":
    """Reject a present Origin unless it is same-origin or explicitly allowed."""
    origin = request.headers.get("origin")
    if origin is None:
        return None
    allowed = set(config.allowed_origins or ())
    allowed.add(_request_origin(request))
    if origin in allowed:
        return None
    return _error(
        None,
        code=INVALID_PARAMS,
        message="Origin not allowed",
        status_code=HTTP_403_FORBIDDEN,
    )


def decode_mcp_header_value(value: "str") -> "str":
    """Decode the MCP Base64 sentinel encoding, returning plain values unchanged."""
    if not (value.startswith(_BASE64_PREFIX) and value.endswith(_BASE64_SUFFIX)):
        return value
    payload = value[len(_BASE64_PREFIX) : -len(_BASE64_SUFFIX)]
    try:
        return base64.b64decode(payload, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        msg = "Invalid MCP Base64 header value"
        raise ValueError(msg) from exc


def _request_metadata_error(
    request: "Request[Any, Any, Any]",
    rpc_request: "JSONRPCRequest",
    discovered_tools: "dict[str, Any]",
) -> "Response[Any] | None":
    params = rpc_request.params
    meta = params.get("_meta") if isinstance(params, dict) else None
    if not isinstance(meta, dict):
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message="Missing or invalid params._meta",
            status_code=HTTP_400_BAD_REQUEST,
        )

    body_version = meta.get("io.modelcontextprotocol/protocolVersion")
    client_capabilities = meta.get("io.modelcontextprotocol/clientCapabilities")
    if not isinstance(body_version, str) or not isinstance(client_capabilities, dict):
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message="Missing required MCP request metadata",
            status_code=HTTP_400_BAD_REQUEST,
        )

    raw_header_version = request.headers.get(MCP_PROTOCOL_VERSION_HEADER)
    raw_header_method = request.headers.get(MCP_METHOD_HEADER)
    header_version = raw_header_version.strip() if raw_header_version is not None else None
    header_method = raw_header_method.strip() if raw_header_method is not None else None
    if header_version != body_version:
        return _error(
            rpc_request.id,
            code=HEADER_MISMATCH,
            message="MCP-Protocol-Version header does not match request metadata",
            status_code=HTTP_400_BAD_REQUEST,
        )
    if header_method != rpc_request.method:
        return _error(
            rpc_request.id,
            code=HEADER_MISMATCH,
            message="Mcp-Method header does not match request method",
            status_code=HTTP_400_BAD_REQUEST,
        )
    if body_version != MCP_PROTOCOL_VERSION:
        return _error(
            rpc_request.id,
            code=UNSUPPORTED_PROTOCOL_VERSION,
            message=f"Unsupported protocol version: {body_version}",
            status_code=HTTP_400_BAD_REQUEST,
            data={
                "supported": [MCP_PROTOCOL_VERSION],
                "supportedVersions": [MCP_PROTOCOL_VERSION],
                "requested": body_version,
            },
        )

    name_field = _NAME_FIELDS.get(rpc_request.method)
    if name_field is not None:
        body_name = params.get(name_field)
        header_name = request.headers.get(MCP_NAME_HEADER)
        try:
            decoded_name = decode_mcp_header_value(header_name.strip()) if header_name is not None else None
        except ValueError as exc:
            return _error(
                rpc_request.id,
                code=HEADER_MISMATCH,
                message=str(exc),
                status_code=HTTP_400_BAD_REQUEST,
            )
        if not isinstance(body_name, str) or decoded_name != body_name:
            return _error(
                rpc_request.id,
                code=HEADER_MISMATCH,
                message="Mcp-Name header does not match request parameters",
                status_code=HTTP_400_BAD_REQUEST,
            )
    if rpc_request.method != "tools/call":
        return None
    tool_name = params.get("name")
    handler = discovered_tools.get(tool_name) if isinstance(tool_name, str) else None
    if handler is None:
        return None
    arguments = params.get("arguments")
    if not isinstance(arguments, dict):
        return None
    for path, custom_name, _property_schema in iter_mcp_header_fields(generate_schema_for_handler(handler)):
        value: Any = arguments
        present = True
        for part in path:
            if not isinstance(value, dict) or part not in value:
                present = False
                break
            value = value[part]
        header_value = request.headers.get(f"Mcp-Param-{custom_name}")
        if not present or value is None:
            if header_value is None:
                continue
        elif header_value is not None:
            try:
                decoded_value = decode_mcp_header_value(header_value.strip())
            except ValueError:
                decoded_value = None
            expected = ("true" if value else "false") if isinstance(value, bool) else str(value)
            if decoded_value == expected:
                continue
        return _error(
            rpc_request.id,
            code=HEADER_MISMATCH,
            message=f"Mcp-Param-{custom_name} header does not match request arguments",
            status_code=HTTP_400_BAD_REQUEST,
        )
    return None


def _request_subject(request: "Request[Any, Any, Any]") -> "str | None":
    auth = request.scope.get("auth")
    if isinstance(auth, dict):
        sub = auth.get("sub")
        if isinstance(sub, str) and sub:
            return sub
    user = request.scope.get("user")
    if isinstance(user, dict):
        for key in ("id", "sub"):
            value = user.get(key)
            if value is not None:
                return str(value)
    else:
        for attribute in ("id", "sub"):
            value = getattr(user, attribute, None)
            if value is not None:
                return str(value)
    return None


def _build_request_context(request: "Request[Any, Any, Any]", rpc_request: "JSONRPCRequest") -> "MCPRequestContext":
    meta = rpc_request.params["_meta"]
    client_info = meta.get("io.modelcontextprotocol/clientInfo")
    client_id = client_info.get("name") if isinstance(client_info, dict) else None
    sub = _request_subject(request)
    return MCPRequestContext(
        client_id=client_id or "anonymous",
        owner_id=f"user:{sub}" if sub is not None else None,
        request=request,
        protocol_version=MCP_PROTOCOL_VERSION,
        client_capabilities=meta["io.modelcontextprotocol/clientCapabilities"],
        client_info=client_info if isinstance(client_info, dict) else None,
        input_responses=rpc_request.params.get("inputResponses"),
        request_state=rpc_request.params.get("requestState"),
    )


def _build_cached_router(
    app: "Litestar",
    config: "MCPConfig",
    discovered_tools: "dict[str, Any]",
    discovered_resources: "dict[str, Any]",
    discovered_prompts: "dict[str, PromptRegistration]",
    registry: "Registry",
    task_store: "MCPTaskStore | None",
) -> "JSONRPCRouter":
    router = JSONRPCRouter()

    def service() -> "MCPHandlerService":
        return MCPHandlerService(
            config=config,
            discovered_tools=discovered_tools,
            discovered_resources=discovered_resources,
            discovered_prompts=discovered_prompts,
            app_ref=app,
            registry=registry,
            task_store=task_store,
        )

    router.register("server/discover", lambda params, ctx: service().server_discover(params, ctx))
    router.register("tools/list", lambda params, ctx: service().tools_list(params, ctx))
    router.register("tools/call", lambda params, ctx: service().tools_call(params, ctx))
    router.register("resources/list", lambda params, ctx: service().resources_list(params, ctx))
    router.register("resources/templates/list", lambda params, ctx: service().resources_templates_list(params, ctx))
    router.register("resources/read", lambda params, ctx: service().resources_read(params, ctx))
    router.register("completion/complete", lambda params, ctx: service().completion_complete(params, ctx))
    router.register("prompts/list", lambda params, ctx: service().prompts_list(params, ctx))
    router.register("prompts/get", lambda params, ctx: service().prompts_get(params, ctx))
    if task_store is not None and config.task_config is not None:
        router.register("tasks/get", lambda params, ctx: service().tasks_get(params, ctx))
        router.register("tasks/update", lambda params, ctx: service().tasks_update(params, ctx))
        router.register("tasks/cancel", lambda params, ctx: service().tasks_cancel(params, ctx))
    return router


def _server_info(app: "Litestar", config: "MCPConfig") -> "dict[str, str]":
    openapi = app.openapi_config
    return {
        "name": config.name or (openapi.title if openapi is not None else "Litestar MCP Server"),
        "version": openapi.version if openapi is not None else "1.0.0",
    }


def _finalize_result(
    payload: "dict[str, Any]",
    *,
    method: "str",
    app: "Litestar",
    config: "MCPConfig",
) -> "None":
    result = payload.get("result")
    if not isinstance(result, dict):
        return
    result.setdefault("resultType", "complete")
    meta = result.setdefault("_meta", {})
    meta.setdefault("io.modelcontextprotocol/serverInfo", _server_info(app, config))
    if method in _CACHEABLE_METHODS:
        result.setdefault("ttlMs", config.cache_ttl_ms)
        result.setdefault("cacheScope", config.cache_scope)


async def _subscription_response(
    rpc_request: "JSONRPCRequest",
    registry: "Registry",
    config: "MCPConfig",
) -> "Response[Any]":
    notifications = rpc_request.params.get("notifications")
    if not isinstance(notifications, dict):
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message="subscriptions/listen notifications must be an object",
            status_code=HTTP_400_BAD_REQUEST,
        )
    try:
        stream_id, stream = await registry.subscription_manager.open(rpc_request.id, notifications)
    except Exception as exc:
        from litestar_mcp.sse import StreamLimitExceeded

        if not isinstance(exc, StreamLimitExceeded):
            raise
        return _error(
            rpc_request.id,
            code=INVALID_PARAMS,
            message=str(exc),
            status_code=HTTP_400_BAD_REQUEST,
        )

    async def event_stream() -> "AsyncGenerator[ServerSentEventMessage, None]":
        next_message = asyncio.create_task(stream.__anext__())
        try:
            while True:
                done, _ = await asyncio.wait(
                    {next_message},
                    timeout=config.subscription_keepalive_seconds,
                )
                if not done:
                    yield ServerSentEventMessage(comment="keepalive")
                    continue
                try:
                    message = next_message.result()
                except StopAsyncIteration:
                    return
                yield ServerSentEventMessage(data=encode_json(message).decode("utf-8"))
                next_message = asyncio.create_task(stream.__anext__())
        finally:
            next_message.cancel()
            await registry.subscription_manager.disconnect(stream_id)

    response = ServerSentEvent(event_stream())
    response.headers[MCP_PROTOCOL_VERSION_HEADER] = MCP_PROTOCOL_VERSION
    response.headers["X-Accel-Buffering"] = "no"
    return response


class MCPController(Controller):
    """POST-only MCP JSON-RPC controller."""

    @post("/", name="mcp_jsonrpc", status_code=HTTP_200_OK)
    async def handle_jsonrpc(
        self,
        request: "Request[Any, Any, Any]",
        config: "NamedDependency[MCPConfig]",
        discovered_tools: "NamedDependency[dict[str, Any]]",
        discovered_resources: "NamedDependency[dict[str, Any]]",
        discovered_prompts: "NamedDependency[dict[str, PromptRegistration]]",
        registry: "NamedDependency[Registry]",
        task_store: "NamedDependency[MCPTaskStore | None]" = None,
    ) -> "Response[Any]":
        """Validate and dispatch one independent MCP request."""
        origin_error = _validate_origin(request, config)
        if origin_error is not None:
            return origin_error
        try:
            raw = decode_json(await request.body())
        except (SerializationException, ValueError):
            return _error(None, code=PARSE_ERROR, message="Parse error", status_code=HTTP_400_BAD_REQUEST)
        try:
            rpc_request = parse_request(raw)
        except JSONRPCErrorException as exc:
            return _error(
                raw.get("id") if isinstance(raw, dict) else None,
                code=exc.error.code,
                message=exc.error.message,
                data=exc.error.data,
                status_code=HTTP_400_BAD_REQUEST,
            )
        metadata_error = _request_metadata_error(request, rpc_request, discovered_tools)
        if metadata_error is not None:
            return metadata_error
        if rpc_request.method == "subscriptions/listen":
            return await _subscription_response(rpc_request, registry, config)

        app = request.app
        if not hasattr(app.state, "mcp_router"):
            app.state.mcp_router = _build_cached_router(
                app,
                config,
                discovered_tools,
                discovered_resources,
                discovered_prompts,
                registry,
                task_store,
            )
        router: JSONRPCRouter = app.state.mcp_router
        if rpc_request.method not in router.methods:
            return _error(
                rpc_request.id,
                code=METHOD_NOT_FOUND,
                message=f"Method not found: {rpc_request.method}",
                status_code=HTTP_404_NOT_FOUND,
            )
        result = await router.dispatch(rpc_request, _build_request_context(request, rpc_request))
        if result is None:
            return _error(
                rpc_request.id,
                code=INVALID_PARAMS,
                message="Client notifications are not supported over Streamable HTTP",
                status_code=HTTP_400_BAD_REQUEST,
            )
        _finalize_result(result, method=rpc_request.method, app=app, config=config)
        error = result.get("error")
        error_code = error.get("code") if isinstance(error, dict) else None
        status_code = (
            HTTP_400_BAD_REQUEST
            if error_code
            in {
                INVALID_PARAMS,
                HEADER_MISMATCH,
                MISSING_REQUIRED_CLIENT_CAPABILITY,
                UNSUPPORTED_PROTOCOL_VERSION,
            }
            else HTTP_200_OK
        )
        response = Response(content=result, status_code=status_code, media_type=MediaType.JSON)
        response.headers[MCP_PROTOCOL_VERSION_HEADER] = MCP_PROTOCOL_VERSION
        return response


__all__ = (
    "HEADER_MISMATCH",
    "MCP_METHOD_HEADER",
    "MCP_NAME_HEADER",
    "MCP_PROTOCOL_VERSION",
    "MCP_PROTOCOL_VERSION_HEADER",
    "MISSING_REQUIRED_CLIENT_CAPABILITY",
    "UNSUPPORTED_PROTOCOL_VERSION",
    "MCPController",
    "decode_mcp_header_value",
)
