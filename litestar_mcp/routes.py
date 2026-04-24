# ruff: noqa: PLR0915, PLR0911, C901
"""MCP JSON-RPC 2.0 Streamable HTTP transport for Litestar applications."""

import asyncio
import contextlib
import inspect
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

from litestar import Controller, MediaType, Request, Response, delete, get, post
from litestar.exceptions import SerializationException
from litestar.handlers import BaseRouteHandler
from litestar.response import ServerSentEvent, ServerSentEventMessage
from litestar.serialization import decode_json, encode_json
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_204_NO_CONTENT,
    HTTP_400_BAD_REQUEST,
    HTTP_403_FORBIDDEN,
    HTTP_404_NOT_FOUND,
    HTTP_405_METHOD_NOT_ALLOWED,
    HTTP_503_SERVICE_UNAVAILABLE,
)

from litestar_mcp.config import MCPConfig
from litestar_mcp.executor import MCPToolErrorResult, execute_tool
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
from litestar_mcp.registry import PromptRegistration, Registry
from litestar_mcp.schema_builder import generate_schema_for_handler
from litestar_mcp.sessions import MCPSessionManager, SessionTerminated
from litestar_mcp.sse import StreamLimitExceeded
from litestar_mcp.tasks import InMemoryTaskStore, TaskLookupError, TaskRecord, TaskStateError
from litestar_mcp.utils import (
    get_handler_function,
    get_mcp_metadata,
    match_uri,
    render_description,
    should_include_handler,
)

_logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-11-25"
MCP_SESSION_HEADER = "Mcp-Session-Id"

SESSION_ERROR = -32000
SESSION_NOT_INITIALIZED = -32002

_SESSION_EXEMPT_METHODS = frozenset({"initialize", "ping"})
_PRE_INIT_ALLOWED_METHODS = frozenset({"initialize", "ping", "notifications/initialized"})


@dataclass
class RequestContext:
    """Request context threaded through tool and task execution.

    Authentication lives in Litestar middleware; ``request.user`` and
    ``request.auth`` are the per-request source of truth for tool handlers.
    This struct only carries the scope identifiers used by MCP itself
    (client id, task-owner id, and the live request handle).
    """

    client_id: str
    owner_id: str
    request: "Request[Any, Any, Any] | None" = None


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


def _request_subject(request: Request[Any, Any, Any]) -> str | None:
    """Best-effort ``sub``-like identifier from ``request.auth`` claims dict.

    Middleware populates ``scope["auth"]`` with whatever shape it sets — this
    helper reads the raw scope value (avoiding the ``request.auth`` property
    which raises when no auth middleware is installed) and treats it as a
    mapping, pulling ``"sub"`` if present. Non-mapping values are ignored.
    """
    auth = request.scope.get("auth")
    if isinstance(auth, dict):
        sub = auth.get("sub")
        if isinstance(sub, str) and sub:
            return sub
    return None


def _resolve_client_id(request: Request[Any, Any, Any]) -> str:
    explicit_client_id = (
        request.headers.get("x-mcp-client-id")
        or request.headers.get("mcp-client-id")
        or request.query_params.get("clientId")
        or request.query_params.get("client_id")
    )
    if explicit_client_id:
        return explicit_client_id
    sub = _request_subject(request)
    if sub is not None:
        return f"user:{sub}"
    if request.client and request.client.host:
        return f"remote:{request.client.host}"
    return "anonymous"


def _build_request_context(request: Request[Any, Any, Any]) -> RequestContext:
    client_id = _resolve_client_id(request)
    sub = _request_subject(request)
    owner_id = f"user:{sub}" if sub is not None else f"client:{client_id}"
    return RequestContext(client_id=client_id, owner_id=owner_id, request=request)


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


def _to_pointer(name: str, msgspec_path: str) -> str:
    """Turn ``name`` + ``$.age.limit`` into ``/arguments/age/limit`` JSON Pointer.

    ``msgspec.ValidationError`` messages include a trailing ``$.<path>`` marker
    indicating which nested field failed validation. We translate that into a
    JSON Pointer rooted at ``/arguments/<name>`` so downstream UIs can render
    field-level errors.
    """
    suffix = msgspec_path.removeprefix("$").lstrip(".")
    parts = ["arguments", name]
    if suffix:
        parts.extend(p for p in suffix.split(".") if p and p != name)
    return "/" + "/".join(parts)


def _split_msgspec_error(exc: "Exception") -> tuple[str, str]:
    """Split a ``msgspec.ValidationError`` string into (reason, path).

    msgspec formats messages as ``"<reason> - at `$.path`"``. When no path is
    present we return an empty path.
    """
    text = str(exc)
    marker = " - at `"
    if marker in text and text.endswith("`"):
        reason, _, tail = text.rpartition(marker)
        path = tail[:-1]
        return reason, path
    return text, ""


def _resolve_annotated_types(handler: "BaseRouteHandler") -> dict[str, Any]:
    """Return ``{param_name: annotated_type}`` from the original handler function.

    Litestar's ``signature_model`` strips user-supplied ``msgspec.Meta``
    constraints (``ge``/``le``/``pattern``/``min_length`` …) and replaces them
    with its own ``KwargDefinition`` metadata. To enforce those constraints via
    ``msgspec.convert`` we resolve type hints directly off the original
    function, preserving the full ``Annotated[...]`` chain.
    """
    import typing as _typing

    fn = get_handler_function(handler)
    try:
        return _typing.get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001
        return {}


def _validate_tool_arguments(handler: "BaseRouteHandler", tool_args: dict[str, Any]) -> list[dict[str, str]]:
    """Validate ``tool_args`` against the handler's Litestar signature.

    Matches the executor's partitioning (Ch2): if the handler declares a
    ``data`` parameter, unrecognized tool_args are validated as fields of
    that struct type; path params are matched against the route's declared
    path variables; remaining scalars are matched against the handler's
    non-DI signature fields.

    Returns a list of ``{"path": <json-pointer>, "message": <reason>}`` dicts,
    sorted by path for deterministic output.
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
    annotated_types = _resolve_annotated_types(handler)
    errors: list[dict[str, str]] = []

    data_field = declared_by_name.get("data")
    data_type = annotated_types.get("data") if data_field is not None else None
    recognized_scalar_names = {
        name for name in declared_by_name if name not in di_params and name not in _VALIDATION_CONTEXT_PARAMS
    }

    # When the handler has a ``data`` param, tool_args keys that aren't
    # recognized scalar fields are treated as members of the data struct.
    # Validate them by building a mapping and converting it to the struct.
    if data_type is not None:
        data_payload = {k: v for k, v in tool_args.items() if k not in recognized_scalar_names}
        if data_payload:
            try:
                msgspec.convert(data_payload, data_type, strict=False)
            except msgspec.ValidationError as exc:
                reason, path = _split_msgspec_error(exc)
                errors.append({"path": _to_pointer("data", path), "message": reason})
            except TypeError:
                pass

    for field in fields:
        if field.name in di_params or field.name in _VALIDATION_CONTEXT_PARAMS:
            continue
        if field.name == "data":
            # Presence of ``data`` is implied by any matching struct field
            # in tool_args; we don't require callers to pass ``data`` as a
            # literal key.
            continue
        if field.name in tool_args:
            continue
        if field.default is msgspec.NODEFAULT and field.default_factory is msgspec.NODEFAULT:
            errors.append({"path": _to_pointer(field.name, ""), "message": "Missing required argument"})

    for name, value in tool_args.items():
        if name not in recognized_scalar_names:
            if data_type is not None:
                # Unknown-to-scalars: assumed to belong to the ``data`` struct
                # and already validated above.
                continue
            # No ``data`` parameter → unknown keys are genuinely unexpected.
            errors.append({"path": "/arguments", "message": f"Unexpected argument: {name}"})
            continue
        declared = declared_by_name[name]
        convert_type = annotated_types.get(name, declared.type)
        try:
            msgspec.convert(value, convert_type, strict=False)
        except msgspec.ValidationError as exc:
            reason, path = _split_msgspec_error(exc)
            errors.append({"path": _to_pointer(name, path), "message": reason})
        except TypeError:
            continue

    return sorted(errors, key=lambda entry: (entry["path"], entry["message"]))


def _normalize_prompt_result(result: Any) -> list[dict[str, Any]]:
    """Normalize a prompt's return value to a list of PromptMessage dicts.

    * ``str`` → single user-role text message
    * ``dict`` → treated as a single message (wrapped in a list)
    * ``list`` → used directly
    * Any other type → ``str(result)`` wrapped as a user-role text message
    """
    if isinstance(result, str):
        return [{"role": "user", "content": {"type": "text", "text": result}}]
    if isinstance(result, dict):
        if "role" in result and "content" in result:
            return [result]
        _logger.warning("Prompt returned dict missing 'role'/'content' keys: %s", list(result.keys()))
        return [{"role": "user", "content": {"type": "text", "text": str(result)}}]
    if isinstance(result, list):
        coerced: list[dict[str, Any]] = []
        for i, item in enumerate(result):
            if isinstance(item, dict):
                coerced.append(item)
            else:
                _logger.warning(
                    "Prompt returned list with non-dict element at index %d (%s), coercing to string",
                    i,
                    type(item).__name__,
                )
                coerced.append({"role": "user", "content": {"type": "text", "text": str(item)}})
        return coerced
    _logger.warning("Prompt returned unexpected type %s, coercing to string", type(result).__name__)
    return [{"role": "user", "content": {"type": "text", "text": str(result)}}]


def build_jsonrpc_router(
    config: MCPConfig,
    discovered_tools: dict[str, BaseRouteHandler],
    discovered_resources: dict[str, BaseRouteHandler],
    discovered_prompts: dict[str, PromptRegistration],
    *,
    app_ref: Any,
    request_context: RequestContext,
    task_store: InMemoryTaskStore | None = None,
    registry: Registry | None = None,
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
                {"error": "Invalid tool arguments", "errors": validation_errors},
                is_error=True,
                task_id=task_id,
            )

        try:
            result = await execute_tool(handler, app_ref, tool_args, request=request_context.request)
        except MCPToolErrorResult as err:
            return _build_tool_result(err.content, is_error=True, task_id=task_id)
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
            "prompts": {"listChanged": True},
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
                "description": render_description(
                    handler, fn, kind="tool", fallback_name=name, opt_keys=config.opt_keys
                ),
                "inputSchema": generate_schema_for_handler(handler),
            }
            if "output_schema" in metadata:
                tool_entry["outputSchema"] = metadata["output_schema"]
            if "annotations" in metadata:
                tool_entry["annotations"] = metadata["annotations"]
            if "scopes" in metadata:
                annotations = tool_entry.get("annotations") or {}
                # Explicit annotations.scopes wins when both are supplied.
                annotations.setdefault("scopes", list(metadata["scopes"]))
                tool_entry["annotations"] = annotations
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
                    "description": render_description(
                        handler, fn, kind="resource", fallback_name=name, opt_keys=config.opt_keys
                    ),
                    "mimeType": "application/json",
                }
            )
        return {"resources": resources}

    router.register("resources/list", handle_resources_list)

    async def handle_resources_templates_list(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        if registry is None:
            return {"resourceTemplates": []}
        templates = []
        for entry in registry.templates.values():
            handler_tags = set(getattr(entry.handler, "tags", None) or [])
            if not should_include_handler(entry.name, handler_tags, config):
                continue
            fn = get_handler_function(entry.handler)
            templates.append(
                {
                    "uriTemplate": entry.template,
                    "name": entry.name,
                    "description": render_description(
                        entry.handler, fn, kind="resource", fallback_name=entry.name, opt_keys=config.opt_keys
                    ),
                    "mimeType": "application/json",
                }
            )
        return {"resourceTemplates": templates}

    router.register("resources/templates/list", handle_resources_templates_list)

    async def handle_resources_read(params: dict[str, Any]) -> dict[str, Any]:
        uri = params.get("uri", "")
        if not isinstance(uri, str) or not uri:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Invalid resource URI: {uri}"))

        if uri.startswith("litestar://"):
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
                    request=request_context.request,
                )
            except MCPToolErrorResult as err:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INTERNAL_ERROR, message=f"Resource read failed: {err.content!s}")
                ) from err
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

        # Non-``litestar://`` URIs: match against registered templates.
        # First template that matches wins (documented: registration order).
        template_entries = registry.templates.values() if registry is not None else ()
        for entry in template_entries:
            extracted = match_uri(entry.template, uri)
            if extracted is None:
                continue
            try:
                result = await execute_tool(
                    entry.handler,
                    app_ref,
                    dict(extracted),
                    request=request_context.request,
                )
            except MCPToolErrorResult as err:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INTERNAL_ERROR, message=f"Resource read failed: {err.content!s}")
                ) from err
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

        raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Resource not found: {uri}"))

    router.register("resources/read", handle_resources_read)

    async def handle_completion_complete(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        # v0.5.0 default: every ref returns an empty completion. A future
        # ``@mcp_resource_completion`` decorator will dispatch through this
        # method; for now, unknown refs must not error per MCP spec.
        return {"completion": {"values": [], "total": 0, "hasMore": False}}

    router.register("completion/complete", handle_completion_complete)

    async def handle_prompts_list(params: dict[str, Any]) -> dict[str, Any]:  # noqa: ARG001
        prompts = []
        for _name, registration in discovered_prompts.items():
            prompt_entry: dict[str, Any] = {"name": registration.name}
            if registration.title is not None:
                prompt_entry["title"] = registration.title
            if registration.description is not None:
                prompt_entry["description"] = registration.description
            arguments = registration.get_arguments()
            if arguments:
                prompt_entry["arguments"] = arguments
            if registration.icons is not None:
                prompt_entry["icons"] = registration.icons
            prompts.append(prompt_entry)
        return {"prompts": prompts}

    router.register("prompts/list", handle_prompts_list)

    async def handle_prompts_get(params: dict[str, Any]) -> dict[str, Any]:
        prompt_name = params.get("name")
        if not prompt_name:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'name'"))

        registration = discovered_prompts.get(prompt_name)
        if registration is None:
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message=f"Prompt not found: {prompt_name}")
            )

        prompt_args = params.get("arguments", {})
        if not isinstance(prompt_args, dict):
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message="Prompt arguments must be an object")
            )

        if registration.handler is not None:
            try:
                result = await execute_tool(
                    registration.handler, app_ref, prompt_args, request=request_context.request
                )
            except MCPToolErrorResult as err:
                _logger.exception("Prompt handler execution failed: %s", prompt_name)
                raise JSONRPCErrorException(
                    JSONRPCError(code=INTERNAL_ERROR, message=f"Prompt execution failed: {err.content!s}")
                ) from err
            except JSONRPCErrorException:
                raise
            except TypeError as exc:
                _logger.exception("Invalid prompt arguments for handler: %s", prompt_name)
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message=f"Invalid prompt arguments: {exc!s}")
                ) from exc
            except Exception as exc:
                _logger.exception("Prompt handler execution failed: %s", prompt_name)
                raise JSONRPCErrorException(
                    JSONRPCError(code=INTERNAL_ERROR, message=f"Prompt execution failed: {exc!s}")
                ) from exc
            handler_result: dict[str, Any]
            if isinstance(result, dict) and "messages" in result:
                handler_result = result
            else:
                handler_result = {"messages": _normalize_prompt_result(result)}
            if registration.description is not None and "description" not in handler_result:
                handler_result["description"] = registration.description
            return handler_result

        if registration.fn is not None:
            # Validate arguments before calling to distinguish argument
            # mismatches (INVALID_PARAMS) from TypeErrors inside the function.
            try:
                inspect.signature(registration.fn).bind(**prompt_args)
            except TypeError as exc:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message=f"Invalid prompt arguments: {exc!s}")
                ) from exc

            try:
                result = registration.fn(**prompt_args)
                if asyncio.iscoroutine(result):
                    result = await result
            except Exception as exc:
                _logger.exception("Prompt function execution failed: %s", prompt_name)
                raise JSONRPCErrorException(
                    JSONRPCError(code=INTERNAL_ERROR, message=f"Prompt execution failed: {exc!s}")
                ) from exc
            messages = _normalize_prompt_result(result)
            get_result: dict[str, Any] = {"messages": messages}
            if registration.description is not None and "description" not in get_result:
                get_result["description"] = registration.description
            return get_result

        raise JSONRPCErrorException(  # pragma: no cover
            JSONRPCError(code=INTERNAL_ERROR, message=f"Prompt has no callable: {prompt_name}")
        )

    router.register("prompts/get", handle_prompts_get)

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
        session_manager: MCPSessionManager,
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

        _build_request_context(request)

        session_id = request.headers.get(MCP_SESSION_HEADER) or request.headers.get(MCP_SESSION_HEADER.lower())
        if not session_id:
            return _add_protocol_headers(
                Response(
                    content={"error": f"Missing required header: {MCP_SESSION_HEADER}"},
                    status_code=HTTP_400_BAD_REQUEST,
                    media_type=MediaType.JSON,
                )
            )
        try:
            await session_manager.get(session_id)
        except SessionTerminated:
            return _add_protocol_headers(
                Response(
                    content=error_response(
                        None, JSONRPCError(code=SESSION_ERROR, message="Session terminated or unknown")
                    ),
                    status_code=HTTP_404_NOT_FOUND,
                    media_type=MediaType.JSON,
                )
            )

        try:
            stream_id, stream = await registry.sse_manager.open_stream(
                session_id=session_id,
                last_event_id=request.headers.get("last-event-id"),
            )
        except StreamLimitExceeded:
            return _add_protocol_headers(
                Response(
                    content=error_response(None, JSONRPCError(code=SESSION_ERROR, message="SSE stream limit exceeded")),
                    status_code=HTTP_503_SERVICE_UNAVAILABLE,
                    media_type=MediaType.JSON,
                )
            )

        async def event_stream() -> AsyncGenerator[ServerSentEventMessage, None]:
            try:
                async for message in stream:
                    yield ServerSentEventMessage(data=message.data, event=message.event, id=message.id)
            finally:
                registry.sse_manager.disconnect(stream_id)

        response = ServerSentEvent(event_stream())
        response.headers[MCP_SESSION_HEADER] = session_id
        return _add_protocol_headers(response)

    @delete("/", name="mcp_session_delete", status_code=HTTP_200_OK)
    async def handle_delete(
        self,
        request: Request[Any, Any, Any],
        config: MCPConfig,
        registry: Registry,
        session_manager: MCPSessionManager,
    ) -> Response[Any]:
        """Terminate an MCP session and close its attached SSE streams."""
        origin_err = _validate_origin(request, config)
        if origin_err is not None:
            return origin_err

        session_id = request.headers.get(MCP_SESSION_HEADER) or request.headers.get(MCP_SESSION_HEADER.lower())
        if not session_id:
            return _add_protocol_headers(
                Response(
                    content={"error": f"Missing required header: {MCP_SESSION_HEADER}"},
                    status_code=HTTP_400_BAD_REQUEST,
                    media_type=MediaType.JSON,
                )
            )

        registry.sse_manager.close_session_streams(session_id)
        await session_manager.delete(session_id)
        return _add_protocol_headers(Response(content=None, status_code=HTTP_204_NO_CONTENT))

    @post("/", name="mcp_jsonrpc", media_type=MediaType.JSON, status_code=HTTP_200_OK)
    async def handle_jsonrpc(
        self,
        request: Request[Any, Any, Any],
        config: MCPConfig,
        discovered_tools: dict[str, Any],
        discovered_resources: dict[str, Any],
        discovered_prompts: dict[str, PromptRegistration],
        registry: Registry,
        session_manager: MCPSessionManager,
        task_store: InMemoryTaskStore | None = None,
    ) -> Response[Any]:
        """Handle a JSON-RPC 2.0 request over Streamable HTTP."""
        origin_err = _validate_origin(request, config)
        if origin_err is not None:
            return origin_err

        try:
            raw = decode_json(await request.body())
        except (SerializationException, ValueError):
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

        incoming_session_id = request.headers.get(MCP_SESSION_HEADER) or request.headers.get(MCP_SESSION_HEADER.lower())
        session = None
        response_session_id: str | None = None

        if rpc_request.method == "initialize":
            params = rpc_request.params if isinstance(rpc_request.params, dict) else {}
            session = await session_manager.create(
                protocol_version=params.get("protocolVersion", MCP_PROTOCOL_VERSION),
                client_info=params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else None,
                capabilities=params.get("capabilities") if isinstance(params.get("capabilities"), dict) else None,
            )
            response_session_id = session.id
        elif rpc_request.method in _SESSION_EXEMPT_METHODS:
            # ping may be issued without a session header
            if incoming_session_id:
                try:
                    session = await session_manager.get(incoming_session_id)
                    response_session_id = session.id
                except SessionTerminated:
                    return _add_protocol_headers(
                        Response(
                            content=error_response(
                                rpc_request.id,
                                JSONRPCError(code=SESSION_ERROR, message="Session terminated or unknown"),
                            ),
                            status_code=HTTP_404_NOT_FOUND,
                            media_type=MediaType.JSON,
                        )
                    )
        else:
            if not incoming_session_id:
                return _add_protocol_headers(
                    Response(
                        content=error_response(
                            rpc_request.id,
                            JSONRPCError(code=SESSION_ERROR, message=f"Missing required header: {MCP_SESSION_HEADER}"),
                        ),
                        status_code=HTTP_400_BAD_REQUEST,
                        media_type=MediaType.JSON,
                    )
                )
            try:
                session = await session_manager.get(incoming_session_id)
            except SessionTerminated:
                return _add_protocol_headers(
                    Response(
                        content=error_response(
                            rpc_request.id,
                            JSONRPCError(code=SESSION_ERROR, message="Session terminated or unknown"),
                        ),
                        status_code=HTTP_404_NOT_FOUND,
                        media_type=MediaType.JSON,
                    )
                )
            response_session_id = session.id
            if not session.initialized and rpc_request.method not in _PRE_INIT_ALLOWED_METHODS:
                return _add_protocol_headers(
                    Response(
                        content=error_response(
                            rpc_request.id,
                            JSONRPCError(code=SESSION_NOT_INITIALIZED, message="Session not initialized"),
                        ),
                        status_code=HTTP_200_OK,
                        media_type=MediaType.JSON,
                    )
                )

        if rpc_request.method == "notifications/initialized" and incoming_session_id:
            with contextlib.suppress(SessionTerminated):
                await session_manager.mark_initialized(incoming_session_id)

        request_context = _build_request_context(request)
        router = build_jsonrpc_router(
            config,
            discovered_tools,
            discovered_resources,
            discovered_prompts,
            app_ref=request.app,
            request_context=request_context,
            task_store=task_store,
            registry=registry,
        )
        result = await router.dispatch(rpc_request)

        response: Response[Any]
        if result is None:
            response = Response(content=None, status_code=HTTP_204_NO_CONTENT)
        else:
            response = Response(content=result, status_code=HTTP_200_OK, media_type=MediaType.JSON)

        if response_session_id is not None:
            response.headers[MCP_SESSION_HEADER] = response_session_id

        return _add_protocol_headers(response)
