# ruff: noqa: PLR0915, C901
"""MCP JSON-RPC 2.0 Streamable HTTP transport for Litestar applications."""

import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from litestar import Controller, MediaType, Request, Response, get, post
from litestar.handlers import BaseRouteHandler
from litestar.response import ServerSentEvent, ServerSentEventMessage
from litestar.serialization import encode_json
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_204_NO_CONTENT,
    HTTP_401_UNAUTHORIZED,
    HTTP_403_FORBIDDEN,
    HTTP_405_METHOD_NOT_ALLOWED,
)

from litestar_mcp.auth import resolve_user, validate_bearer_token
from litestar_mcp.config import MCPConfig
from litestar_mcp.decorators import get_mcp_metadata
from litestar_mcp.executor import execute_tool
from litestar_mcp.filters import should_include_handler
from litestar_mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JSONRPCError,
    JSONRPCErrorException,
    JSONRPCRouter,
    error_response,
    parse_request,
)
from litestar_mcp.registry import Registry
from litestar_mcp.schema_builder import generate_schema_for_handler
from litestar_mcp.tasks import InMemoryTaskStore, TaskLookupError, TaskRecord, TaskStateError
from litestar_mcp.utils import get_handler_function

MCP_PROTOCOL_VERSION = "2025-11-25"

_AUTH_EXEMPT_METHODS = frozenset({"initialize", "ping", "notifications/initialized"})


@dataclass
class RequestContext:
    """Authenticated request context used across tool and task execution."""

    client_id: str
    owner_id: str
    user_claims: dict[str, Any] | None = None
    resolved_user: Any = None


def _validate_origin(request: Request[Any, Any, Any], config: MCPConfig) -> Response[Any] | None:
    """Validate the Origin header if allowed_origins is configured."""
    if not config.allowed_origins:
        return None

    origin = request.headers.get("origin")
    if origin and origin not in config.allowed_origins:
        return Response(
            content={"error": "Origin not allowed"},
            status_code=HTTP_403_FORBIDDEN,
            media_type=MediaType.JSON,
        )
    return None


def _add_protocol_headers(response: Response[Any]) -> Response[Any]:
    """Add standard MCP protocol headers to a response."""
    response.headers["mcp-protocol-version"] = MCP_PROTOCOL_VERSION
    return response


def _auth_config_is_enabled(config: MCPConfig) -> bool:
    return bool(config.auth and (config.auth.token_validator or config.auth.providers or config.auth.issuer))


def _resolve_client_id(request: Request[Any, Any, Any], user_claims: dict[str, Any] | None) -> str:
    explicit_client_id = (
        request.headers.get("x-mcp-client-id")
        or request.headers.get("mcp-client-id")
        or request.query_params.get("clientId")
        or request.query_params.get("client_id")
    )
    if explicit_client_id:
        return explicit_client_id
    if user_claims and user_claims.get("sub"):
        return f"user:{user_claims['sub']}"
    if request.client and request.client.host:
        return f"remote:{request.client.host}"
    return "anonymous"


def _build_request_context(
    request: Request[Any, Any, Any],
    *,
    user_claims: dict[str, Any] | None,
    resolved_user: Any,
) -> RequestContext:
    client_id = _resolve_client_id(request, user_claims)
    owner_id = f"user:{user_claims['sub']}" if user_claims and user_claims.get("sub") else f"client:{client_id}"
    return RequestContext(client_id=client_id, owner_id=owner_id, user_claims=user_claims, resolved_user=resolved_user)


def _serialize_tool_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    return encode_json(value).decode("utf-8")


def _build_tool_result(value: Any, *, is_error: bool, task_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "content": [{"type": "text", "text": _serialize_tool_content(value)}],
        "isError": is_error,
    }
    if task_id is not None:
        result["_meta"] = {"io.modelcontextprotocol/related-task": {"taskId": task_id}}
    return result


_VALIDATION_CONTEXT_PARAMS = {
    "resolved_user",
    "user_claims",
    "request",
    "socket",
    "state",
    "scope",
    "headers",
    "cookies",
    "query",
    "body",
    "data",
}


def _validate_tool_arguments(handler: "BaseRouteHandler", tool_args: dict[str, Any]) -> list[str]:
    """Validate ``tool_args`` against the handler's Litestar signature model.

    Litestar attaches a ``signature_model`` (a msgspec Struct) to every
    route handler whose fields mirror the handler's typed parameters after
    forward-ref resolution and ``Annotated`` / ``Parameter`` metadata
    normalisation. We introspect those fields (rather than raw
    ``inspect.signature`` annotations) so constraints declared via
    ``msgspec.Meta`` and Litestar ``Parameter()`` markers flow through
    ``msgspec.convert`` automatically.

    Per-field iteration (instead of one-shot ``msgspec.convert(args, model)``)
    lets us ignore DI-injected fields and reject extras, both of which the
    bare signature model cannot express.
    """
    import msgspec

    signature_model = getattr(handler, "signature_model", None)
    if signature_model is None:
        return []

    try:
        fields = msgspec.structs.fields(signature_model)
    except TypeError:
        return []

    di_params: set[str] = set()
    with contextlib.suppress(AttributeError, TypeError):
        di_params = set(handler.resolve_dependencies().keys())

    declared_by_name = {field.name: field for field in fields}
    errors: list[str] = []

    for field in fields:
        if field.name in di_params or field.name in _VALIDATION_CONTEXT_PARAMS:
            continue
        if field.name in tool_args:
            continue
        if field.default is msgspec.NODEFAULT and field.default_factory is msgspec.NODEFAULT:
            errors.append(f"{field.name}: required")

    for name, value in tool_args.items():
        declared = declared_by_name.get(name)
        if declared is None:
            errors.append(f"{name}: unexpected argument")
            continue
        if name in di_params or name in _VALIDATION_CONTEXT_PARAMS:
            continue
        try:
            msgspec.convert(value, declared.type, strict=False)
        except msgspec.ValidationError as exc:
            errors.append(f"{name}: {exc}")
        except TypeError:
            continue

    return sorted(errors)


async def _authenticate_request(
    request: Request[Any, Any, Any],
    config: MCPConfig,
    *,
    method_name: str,
) -> tuple[dict[str, Any] | None, Any, Response[Any] | None]:
    auth_config = config.auth
    if auth_config is None or not _auth_config_is_enabled(config) or method_name in _AUTH_EXEMPT_METHODS:
        return None, None, None

    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        return (
            None,
            None,
            Response(
                content={"error": "Missing or invalid Authorization header"},
                status_code=HTTP_401_UNAUTHORIZED,
                media_type=MediaType.JSON,
                headers={"WWW-Authenticate": "Bearer"},
            ),
        )

    token = auth_header[7:]
    user_claims = await validate_bearer_token(token, auth_config)
    if user_claims is None:
        return (
            None,
            None,
            Response(
                content={"error": "Invalid token"},
                status_code=HTTP_401_UNAUTHORIZED,
                media_type=MediaType.JSON,
                headers={"WWW-Authenticate": "Bearer"},
            ),
        )

    resolved_user = await resolve_user(user_claims, auth_config, request.app)
    return user_claims, resolved_user, None


def build_jsonrpc_router(
    config: MCPConfig,
    discovered_tools: dict[str, BaseRouteHandler],
    discovered_resources: dict[str, BaseRouteHandler],
    *,
    app_ref: Any,
    request_context: RequestContext,
    task_store: InMemoryTaskStore | None = None,
) -> JSONRPCRouter:
    """Build and return a JSONRPCRouter wired to MCP method handlers."""
    router = JSONRPCRouter()
    task_config = config.task_config

    async def execute_tool_call(
        handler: BaseRouteHandler,
        tool_args: dict[str, Any],
        *,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        validation_errors = _validate_tool_arguments(handler, tool_args)
        if validation_errors:
            return _build_tool_result(
                {"error": "Invalid tool arguments", "details": validation_errors},
                is_error=True,
                task_id=task_id,
            )

        try:
            result = await execute_tool(
                handler,
                app_ref,
                tool_args,
                config=config,
                user_claims=request_context.user_claims,
                resolved_user=request_context.resolved_user,
            )
        except Exception as exc:  # noqa: BLE001
            return _build_tool_result({"error": str(exc)}, is_error=True, task_id=task_id)

        return _build_tool_result(result, is_error=False, task_id=task_id)

    async def run_task(
        record: TaskRecord,
        handler: "BaseRouteHandler",
        tool_args: dict[str, Any],
    ) -> None:
        try:
            result = await execute_tool_call(handler, tool_args, task_id=record.task_id)
            await task_store.complete(record.task_id, result)  # type: ignore[union-attr]
        except JSONRPCErrorException as exc:
            await task_store.fail(record.task_id, exc.error)  # type: ignore[union-attr]
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            await task_store.fail(  # type: ignore[union-attr]
                record.task_id,
                JSONRPCError(code=INTERNAL_ERROR, message=str(exc)),
                status_message=str(exc),
            )

    async def handle_initialize(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        server_name = config.name or "Litestar MCP Server"
        server_version = "1.0.0"

        if app_ref is not None:
            openapi_config = app_ref.openapi_config
            if openapi_config:
                server_name = config.name or openapi_config.title
                server_version = openapi_config.version

        capabilities: dict[str, Any] = {
            "tools": {"listChanged": True},
            "resources": {"subscribe": True, "listChanged": True},
        }
        if task_config is not None:
            task_capabilities: dict[str, Any] = {"requests": {"tools": {"call": {}}}}
            if task_config.list_enabled:
                task_capabilities["list"] = {}
            if task_config.cancel_enabled:
                task_capabilities["cancel"] = {}
            capabilities["tasks"] = task_capabilities

        return {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": capabilities,
            "serverInfo": {"name": server_name, "version": server_version},
        }

    router.register("initialize", handle_initialize)

    async def handle_initialized(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        return {}

    router.register("notifications/initialized", handle_initialized)

    async def handle_ping(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        return {}

    router.register("ping", handle_ping)

    async def handle_tools_list(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        tools = []
        for name, handler in discovered_tools.items():
            handler_tags = set(getattr(handler, "tags", None) or [])
            if not should_include_handler(name, handler_tags, config):
                continue

            fn = get_handler_function(handler)
            metadata = get_mcp_metadata(handler) or get_mcp_metadata(fn) or {}
            tool_entry: dict[str, Any] = {
                "name": name,
                "description": (fn.__doc__ or f"Tool: {name}").strip(),
                "inputSchema": generate_schema_for_handler(handler),
            }
            if "output_schema" in metadata:
                tool_entry["outputSchema"] = metadata["output_schema"]
            if "annotations" in metadata:
                tool_entry["annotations"] = metadata["annotations"]
            if task_config is not None and metadata.get("task_support") is not None:
                tool_entry["execution"] = {"taskSupport": metadata["task_support"]}
            tools.append(tool_entry)
        return {"tools": tools}

    router.register("tools/list", handle_tools_list)

    async def handle_tools_call(params: dict[str, Any]) -> dict[str, Any]:
        tool_name = params.get("name")
        if not tool_name:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'name'"))

        handler = discovered_tools.get(tool_name)
        if handler is None:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Tool not found: {tool_name}"))

        fn = get_handler_function(handler)
        metadata = get_mcp_metadata(handler) or get_mcp_metadata(fn) or {}
        tool_args = params.get("arguments", {})
        if not isinstance(tool_args, dict):
            return _build_tool_result({"error": "Tool arguments must be an object"}, is_error=True)

        if metadata.get("scopes") is not None:
            if request_context.user_claims is None:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message="This tool requires an authenticated request context.")
                )
            required_scopes = set(metadata["scopes"])
            user_scopes = set(request_context.user_claims.get("scopes", []))
            if not required_scopes.issubset(user_scopes):
                missing_scopes = sorted(required_scopes - user_scopes)
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message=f"Insufficient scope. Required: {missing_scopes}")
                )

        task_request = params.get("task")
        task_support = metadata.get("task_support")

        if task_request is None:
            if task_support == "required" and task_config is not None:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_REQUEST, message="Task augmentation required for tools/call requests")
                )
            return await execute_tool_call(handler, tool_args)

        if task_config is None:
            raise JSONRPCErrorException(
                JSONRPCError(code=METHOD_NOT_FOUND, message=f"Task augmentation is not supported for tool: {tool_name}")
            )
        if task_support not in {"optional", "required"}:
            raise JSONRPCErrorException(
                JSONRPCError(code=METHOD_NOT_FOUND, message=f"Task augmentation is not supported for tool: {tool_name}")
            )
        if not isinstance(task_request, dict):
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message="The 'task' parameter must be an object")
            )

        record = await task_store.create(request_context.owner_id, task_request.get("ttl"))  # type: ignore[union-attr]
        background_task = asyncio.create_task(run_task(record, handler, tool_args))
        await task_store.attach_background_task(record.task_id, background_task)  # type: ignore[union-attr]
        return {"task": record.to_dict()}

    router.register("tools/call", handle_tools_call)

    async def handle_resources_list(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        resources = [
            {
                "uri": "litestar://openapi",
                "name": "openapi",
                "description": "OpenAPI schema for this Litestar application",
                "mimeType": "application/json",
            }
        ]
        for name, handler in discovered_resources.items():
            handler_tags = set(getattr(handler, "tags", None) or [])
            if not should_include_handler(name, handler_tags, config):
                continue

            fn = get_handler_function(handler)
            resources.append(
                {
                    "uri": f"litestar://{name}",
                    "name": name,
                    "description": (fn.__doc__ or f"Resource: {name}").strip(),
                    "mimeType": "application/json",
                }
            )
        return {"resources": resources}

    router.register("resources/list", handle_resources_list)

    async def handle_resources_read(params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri", "")
        if not uri.startswith("litestar://"):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Invalid resource URI: {uri}"))

        resource_name = uri[len("litestar://") :]
        if resource_name == "openapi" and app_ref is not None:
            return {
                "contents": [
                    {
                        "uri": "litestar://openapi",
                        "mimeType": "application/json",
                        "text": encode_json(app_ref.openapi_schema).decode("utf-8"),
                    }
                ]
            }

        handler = discovered_resources.get(resource_name)
        if handler is None:
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message=f"Resource not found: {resource_name}")
            )

        try:
            result = await execute_tool(
                handler,
                app_ref,
                {},
                config=config,
                user_claims=request_context.user_claims,
                resolved_user=request_context.resolved_user,
            )
        except Exception as exc:
            raise JSONRPCErrorException(
                JSONRPCError(code=INTERNAL_ERROR, message=f"Resource read failed: {exc!s}")
            ) from exc

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": encode_json(result).decode("utf-8"),
                }
            ]
        }

    router.register("resources/read", handle_resources_read)

    if task_store is not None:

        async def handle_tasks_get(params: dict[str, Any]) -> dict[str, Any]:
            task_id = params.get("taskId")
            if not task_id:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'taskId'")
                )
            try:
                record = await task_store.get(task_id, request_context.owner_id)
            except TaskLookupError as exc:
                raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
            return record.to_dict()

        async def handle_tasks_result(params: dict[str, Any]) -> dict[str, Any]:
            task_id = params.get("taskId")
            if not task_id:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'taskId'")
                )
            try:
                record = await task_store.wait_for_terminal(task_id, request_context.owner_id)
            except TaskLookupError as exc:
                raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc

            if record.result is not None:
                meta = record.result.setdefault("_meta", {})
                meta["io.modelcontextprotocol/related-task"] = {"taskId": task_id}
                return record.result
            if record.error is not None:
                raise JSONRPCErrorException(record.error)
            raise JSONRPCErrorException(
                JSONRPCError(code=INTERNAL_ERROR, message="Task did not produce a final result")
            )

        async def handle_tasks_list(params: dict[str, Any]) -> dict[str, Any]:
            limit = params.get("limit", 50)
            if not isinstance(limit, int) or limit <= 0:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message="The 'limit' parameter must be a positive integer")
                )
            try:
                tasks, next_cursor = await task_store.list(
                    request_context.owner_id,
                    cursor=params.get("cursor"),
                    limit=limit,
                )
            except ValueError as exc:
                raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
            result: dict[str, Any] = {"tasks": [task.to_dict() for task in tasks]}
            if next_cursor is not None:
                result["nextCursor"] = next_cursor
            return result

        async def handle_tasks_cancel(params: dict[str, Any]) -> dict[str, Any]:
            task_id = params.get("taskId")
            if not task_id:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'taskId'")
                )
            try:
                record = await task_store.cancel(task_id, request_context.owner_id)
            except TaskLookupError as exc:
                raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
            except TaskStateError as exc:
                raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
            return record.to_dict()

        router.register("tasks/get", handle_tasks_get)
        router.register("tasks/result", handle_tasks_result)
        router.register("tasks/list", handle_tasks_list)
        router.register("tasks/cancel", handle_tasks_cancel)

    return router


class MCPController(Controller):
    """MCP JSON-RPC 2.0 Streamable HTTP controller."""

    @get("/", name="mcp_sse", media_type=MediaType.TEXT)
    async def handle_sse(
        self,
        request: Request[Any, Any, Any],
        config: MCPConfig,
        registry: Registry,
    ) -> Response[Any]:
        """Handle GET-based Streamable HTTP SSE streams on the MCP endpoint."""
        origin_err = _validate_origin(request, config)
        if origin_err is not None:
            return origin_err

        accept_header = request.headers.get("accept", "")
        if "text/event-stream" not in accept_header:
            return _add_protocol_headers(
                Response(
                    content={"error": "GET /mcp requires Accept: text/event-stream"},
                    status_code=HTTP_405_METHOD_NOT_ALLOWED,
                    media_type=MediaType.JSON,
                )
            )

        user_claims, resolved_user, auth_response = await _authenticate_request(request, config, method_name="__sse__")
        if auth_response is not None:
            return auth_response

        request_context = _build_request_context(request, user_claims=user_claims, resolved_user=resolved_user)
        stream_id, stream = await registry.sse_manager.open_stream(
            request_context.client_id,
            last_event_id=request.headers.get("last-event-id"),
        )

        async def event_stream() -> AsyncGenerator[ServerSentEventMessage, None]:
            try:
                async for message in stream:
                    yield ServerSentEventMessage(data=message.data, event=message.event, id=message.id)
            finally:
                registry.sse_manager.disconnect(stream_id)

        return _add_protocol_headers(ServerSentEvent(event_stream()))

    @post("/", name="mcp_jsonrpc", media_type=MediaType.JSON, status_code=HTTP_200_OK)
    async def handle_jsonrpc(
        self,
        request: Request[Any, Any, Any],
        config: MCPConfig,
        discovered_tools: dict[str, Any],
        discovered_resources: dict[str, Any],
        registry: Registry,
        task_store: InMemoryTaskStore | None = None,
    ) -> Response[Any]:
        """Handle a JSON-RPC 2.0 request over Streamable HTTP."""
        origin_err = _validate_origin(request, config)
        if origin_err is not None:
            return origin_err

        try:
            raw = json.loads(await request.body())
        except (json.JSONDecodeError, ValueError):
            return _add_protocol_headers(
                Response(
                    content=error_response(None, JSONRPCError(code=PARSE_ERROR, message="Parse error")),
                    status_code=HTTP_200_OK,
                    media_type=MediaType.JSON,
                )
            )

        try:
            rpc_request = parse_request(raw)
        except JSONRPCErrorException as exc:
            return _add_protocol_headers(
                Response(
                    content=error_response(raw.get("id") if isinstance(raw, dict) else None, exc.error),
                    status_code=HTTP_200_OK,
                    media_type=MediaType.JSON,
                )
            )

        user_claims, resolved_user, auth_response = await _authenticate_request(
            request,
            config,
            method_name=rpc_request.method,
        )
        if auth_response is not None:
            return auth_response

        request_context = _build_request_context(request, user_claims=user_claims, resolved_user=resolved_user)
        router = build_jsonrpc_router(
            config,
            discovered_tools,
            discovered_resources,
            app_ref=request.app,
            request_context=request_context,
            task_store=task_store,
        )
        result = await router.dispatch(rpc_request)

        if result is None:
            return _add_protocol_headers(Response(content=None, status_code=HTTP_204_NO_CONTENT))

        return _add_protocol_headers(Response(content=result, status_code=HTTP_200_OK, media_type=MediaType.JSON))
