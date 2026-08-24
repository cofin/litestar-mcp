# ruff: noqa: PLR0915, C901
"""MCP service layer handler."""

import asyncio
import base64
import contextvars
import inspect
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar, get_type_hints

import msgspec
from litestar.serialization import encode_json

from litestar_mcp._cursor import decode_cursor, encode_cursor
from litestar_mcp.content import (
    MCPBlobResource,
    MCPInputRequiredResult,
    MCPResourceLink,
    MCPToolResult,
    enforce_blob_size,
    is_content_block,
    normalize_content_blocks,
)
from litestar_mcp.error_mapping import (
    mcp_error_for_prompt_execution,
    mcp_error_for_resource_not_found,
    mcp_error_for_resource_read,
)
from litestar_mcp.executor import (
    MCPHandlerResponse,
    MCPToolErrorResult,
    execute_handler,
    execute_handler_response,
    execute_tool,
)
from litestar_mcp.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    METHOD_NOT_FOUND,
    JSONRPCError,
    JSONRPCErrorException,
)
from litestar_mcp.registry import (
    PromptRegistration,
    Registry,
    _normalize_prompt_result,
    render_prompt_entry,
    resolve_prompt_description,
    should_include_prompt,
)
from litestar_mcp.schema_builder import generate_schema_for_handler
from litestar_mcp.tasks import MCPTaskStore, TaskLookupError, TaskRecord
from litestar_mcp.utils import (
    get_handler_function,
    get_mcp_metadata,
    match_uri,
    render_description,
    should_include_handler,
)
from litestar_mcp.utils.handler_signature import (
    _unwrap_annotated,
    get_advertised_handler_parameters,
    resolve_tool_argument_aliases,
)

if TYPE_CHECKING:
    from litestar import Litestar, Request
    from litestar.handlers import BaseRouteHandler

    from litestar_mcp.config import MCPConfig

_logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2026-07-28"
TASKS_EXTENSION = "io.modelcontextprotocol/tasks"
MISSING_REQUIRED_CLIENT_CAPABILITY = -32021


@dataclass
class MCPRequestContext:
    """Request context threaded through tool and task execution.

    Authentication lives in Litestar middleware; ``request.user`` and
    ``request.auth`` are the per-request source of truth for tool handlers.
    This struct only carries the scope identifiers used by MCP itself
    (client id, task-owner id, and the live request handle).
    """

    client_id: "str"
    owner_id: "str | None"
    request: "Request[Any, Any, Any] | None" = None
    scope_overrides: "dict[str, Any] | None" = None
    protocol_version: "str" = MCP_PROTOCOL_VERSION
    client_capabilities: "dict[str, Any] | None" = None
    client_info: "dict[str, Any] | None" = None
    input_responses: "dict[str, Any] | None" = None
    request_state: "str | None" = None


RequestContext = MCPRequestContext

_request_context: contextvars.ContextVar[MCPRequestContext | None] = contextvars.ContextVar(
    "litestar_mcp_request_context", default=None
)


def get_mcp_request_context() -> "MCPRequestContext":
    """Return the context for the currently executing MCP handler."""
    context = _request_context.get()
    if context is None:
        msg = "No MCP request is currently executing"
        raise RuntimeError(msg)
    return context


_T = TypeVar("_T")


def _paginate_list(items: "list[_T]", params: "dict[str, Any]", page_size: "int") -> "tuple[list[_T], str | None]":
    """Slice ``items`` by the opaque cursor in ``params`` and return ``(page, next_cursor)``."""
    cursor = params.get("cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise JSONRPCErrorException(
            JSONRPCError(code=INVALID_PARAMS, message="The 'cursor' parameter must be a string")
        )
    try:
        offset = decode_cursor(cursor) if cursor else 0
    except ValueError as exc:
        raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
    page = items[offset : offset + page_size]
    next_cursor = encode_cursor(offset + page_size) if offset + page_size < len(items) else None
    return page, next_cursor


def _serialize_tool_content(value: "Any") -> "str":
    if isinstance(value, str):
        return value
    return encode_json(value).decode("utf-8")


def _scope_overrides_for_context(context: "RequestContext") -> "dict[str, Any] | None":
    return context.scope_overrides if context.request is None else None


def _is_resource_text_media_type(mime_type: "str") -> "bool":
    """Return whether a resource body should be sent as MCP text."""
    return (
        mime_type.startswith("text/")
        or mime_type == "application/json"
        or mime_type.endswith(("+json", "+xml"))
        or mime_type
        in {
            "application/javascript",
            "application/xml",
            "application/x-yaml",
            "application/yaml",
        }
    )


def _resource_mime_type(handler: "BaseRouteHandler", config: "MCPConfig") -> "str":
    """Resolve resource MIME metadata from opt keys or decorator metadata."""
    opt = getattr(handler, "opt", None) or {}
    opt_mime_type = opt.get(config.opt_keys.resource_mime_type)
    if isinstance(opt_mime_type, str) and opt_mime_type:
        return opt_mime_type
    fn = get_handler_function(handler)
    metadata = get_mcp_metadata(handler) or get_mcp_metadata(fn) or {}
    metadata_mime_type = metadata.get("mime_type")
    if isinstance(metadata_mime_type, str) and metadata_mime_type:
        return metadata_mime_type
    return "application/json"


def _resource_uri(name: "str", handler: "BaseRouteHandler", config: "MCPConfig") -> "str":
    """Resolve a resource's concrete URI override."""
    opt = getattr(handler, "opt", None) or {}
    configured_uri = opt.get(config.opt_keys.resource_uri)
    if isinstance(configured_uri, str) and configured_uri:
        return configured_uri
    return f"litestar://{name}"


def _without_undefined_values(value: "Any") -> "Any":
    """Remove Litestar OpenAPI undefined sentinels before JSON encoding."""
    if isinstance(value, dict):
        return {key: _without_undefined_values(item) for key, item in value.items() if type(item) is not object}
    if isinstance(value, list):
        return [_without_undefined_values(item) for item in value if type(item) is not object]
    return value


def _resource_content_from_response(
    uri: "str",
    response: "MCPHandlerResponse",
    *,
    fallback_mime_type: "str",
    max_blob_bytes: "int | None",
) -> "dict[str, Any]":
    """Build one MCP ResourceContents object from a captured handler response."""
    mime_type = response.media_type or fallback_mime_type
    content: dict[str, Any] = {"uri": uri, "mimeType": mime_type}
    body = response.body
    if _is_resource_text_media_type(mime_type):
        try:
            content["text"] = body.decode("utf-8")
        except UnicodeDecodeError:
            pass
        else:
            return content

    enforce_blob_size(len(body), max_blob_bytes=max_blob_bytes)
    content["blob"] = base64.b64encode(body).decode("ascii")
    return content


def _looks_like_tool_content(value: "Any") -> "bool":
    """Return whether a plain handler result is an explicit MCP content block."""
    if isinstance(value, (MCPBlobResource, MCPResourceLink)):
        return True
    if is_content_block(value):
        return True
    if isinstance(value, list) and value:
        return all(isinstance(item, (MCPBlobResource, MCPResourceLink)) or is_content_block(item) for item in value)
    return False


def _build_tool_result(
    value: "Any",
    *,
    is_error: "bool",
    max_blob_bytes: "int | None" = None,
) -> "dict[str, Any]":
    try:
        if isinstance(value, MCPInputRequiredResult):
            return value.to_result()
        if isinstance(value, MCPToolResult):
            result = value.to_result(max_blob_bytes=max_blob_bytes)
            result["isError"] = bool(is_error or result.get("isError", False))
            return result
        if _looks_like_tool_content(value):
            return {
                "content": normalize_content_blocks(value, max_blob_bytes=max_blob_bytes),
                "isError": is_error,
            }
        result = {
            "content": [{"type": "text", "text": _serialize_tool_content(value)}],
            "isError": is_error,
        }
    except (TypeError, ValueError) as exc:
        result = {
            "content": [{"type": "text", "text": _serialize_tool_content({"error": str(exc)})}],
            "isError": True,
        }
    return result


def _to_pointer(name: "str", msgspec_path: "str") -> "str":
    """Turn ``name`` + ``$.age.limit`` into ``/arguments/age/limit`` JSON Pointer."""
    suffix = msgspec_path.removeprefix("$").lstrip(".")
    parts = ["arguments", name]
    if suffix:
        parts.extend(p for p in suffix.split(".") if p and p != name)
    return "/" + "/".join(parts)


def _split_msgspec_error(exc: "Exception") -> "tuple[str, str]":
    """Split a ``msgspec.ValidationError`` string into (reason, path)."""
    text = str(exc)
    marker = " - at `"
    if marker in text and text.endswith("`"):
        reason, _, tail = text.rpartition(marker)
        path = tail[:-1]
        return reason, path
    return text, ""


def _resolve_annotated_types(handler: "BaseRouteHandler") -> "dict[str, Any]":
    """Return ``{param_name: annotated_type}`` from the original handler function."""
    fn = get_handler_function(handler)
    try:
        return get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001
        return {}


def _validate_tool_arguments(handler: "BaseRouteHandler", tool_args: "dict[str, Any]") -> "list[dict[str, str]]":
    """Validate ``tool_args`` against the handler's Litestar signature.

    Path parameters are excluded since Litestar extracts them directly from
    path variables; remaining scalars are matched against the handler's
    non-DI signature fields.

    Returns a list of ``{"path": <json-pointer>, "message": <reason>}`` dicts,
    sorted by path for deterministic output.
    """

    advertised_params = get_advertised_handler_parameters(handler)
    python_to_wire = {p.python_name: p.wire_name for p in advertised_params}
    resolved_args, consumed, _ = resolve_tool_argument_aliases(tool_args, advertised_params)
    normalized_args = {key: value for key, value in tool_args.items() if key not in consumed}
    normalized_args.update(
        {
            parameter.python_name: resolved_args[parameter.wire_name]
            for parameter in advertised_params
            if parameter.wire_name in resolved_args
        }
    )
    tool_args = normalized_args

    try:
        declared_by_name = dict(handler.parsed_fn_signature.parameters)
    except Exception:  # noqa: BLE001
        return []

    advertised_params = get_advertised_handler_parameters(handler)
    advertised_by_name = {param.python_name: param for param in advertised_params}
    annotated_types = _resolve_annotated_types(handler)
    errors: list[dict[str, str]] = []

    data_field = declared_by_name.get("data")
    data_type = annotated_types.get("data") if data_field is not None else None
    recognized_scalar_names = set(advertised_by_name)

    # When the handler has a ``data`` param, tool_args keys that aren't
    # recognized scalar fields are treated as members of the data struct.
    # Validate them by building a mapping and converting it to the struct.
    if data_type is not None:
        has_explicit_data = "data" in tool_args
        data_payload = (
            tool_args["data"]
            if has_explicit_data
            else {k: v for k, v in tool_args.items() if k not in recognized_scalar_names}
        )
        if has_explicit_data or data_payload:
            try:
                msgspec.convert(data_payload, data_type, strict=False)
            except msgspec.ValidationError as exc:
                reason, path = _split_msgspec_error(exc)
                errors.append({"path": _to_pointer("data", path), "message": reason})
            except TypeError:
                pass

    for name, parameter in advertised_by_name.items():
        if name in tool_args:
            continue
        if name == "data" and data_type is not None:
            data_member_names = set(tool_args) - recognized_scalar_names
            if data_member_names:
                continue
        if parameter.required:
            errors.append({"path": _to_pointer(parameter.wire_name, ""), "message": "Missing required argument"})

    for name, value in tool_args.items():
        if name == "data" and data_type is not None:
            continue
        if name not in recognized_scalar_names:
            if data_type is not None:
                continue
            display_name = python_to_wire.get(name, name)
            errors.append({"path": "/arguments", "message": f"Unexpected argument: {display_name}"})
            continue
        declared = declared_by_name.get(name)
        if declared is not None:
            default_annotation = getattr(declared, "annotation", Any)
        else:
            raw = getattr(advertised_by_name[name], "annotation", Any)
            inner, _ = _unwrap_annotated(raw)
            default_annotation = inner
        convert_type = annotated_types.get(name, default_annotation)
        try:
            msgspec.convert(value, convert_type, strict=False)
        except msgspec.ValidationError as exc:
            reason, path = _split_msgspec_error(exc)
            wire_name = advertised_by_name[name].wire_name
            errors.append({"path": _to_pointer(wire_name, path), "message": reason})
        except TypeError:
            continue

    return sorted(errors, key=lambda entry: (entry["path"], entry["message"]))


class MCPHandlerService:
    """Service class encapsulating the MCP JSON-RPC business logic."""

    def __init__(
        self,
        config: "MCPConfig",
        discovered_tools: "dict[str, BaseRouteHandler]",
        discovered_resources: "dict[str, BaseRouteHandler]",
        discovered_prompts: "dict[str, PromptRegistration]",
        app_ref: "Litestar",
        registry: "Registry | None",
        task_store: "MCPTaskStore | None" = None,
    ) -> "None":
        self.config = config
        self.discovered_tools = discovered_tools
        self.discovered_resources = discovered_resources
        self.discovered_prompts = discovered_prompts
        self.app_ref = app_ref
        self.registry = registry
        self.task_store = task_store
        self.task_config = config.task_config

    async def _execute_tool_call(
        self,
        tool_name: "str",
        handler: "BaseRouteHandler",
        tool_args: "dict[str, Any]",
        context: "RequestContext",
    ) -> "dict[str, Any]":
        validation_errors = _validate_tool_arguments(handler, tool_args)
        if validation_errors:
            return _build_tool_result(
                {"error": "Invalid tool arguments", "errors": validation_errors},
                is_error=True,
                max_blob_bytes=self.config.max_blob_bytes,
            )

        try:
            token = _request_context.set(context)
            try:
                result = await execute_tool(
                    handler,
                    self.app_ref,
                    tool_args,
                    request=context.request,
                    scope_overrides=_scope_overrides_for_context(context),
                    config=self.config,
                    tool_name=tool_name,
                )
            finally:
                _request_context.reset(token)
        except MCPToolErrorResult as err:
            return _build_tool_result(
                err.content,
                is_error=True,
                max_blob_bytes=self.config.max_blob_bytes,
            )
        except JSONRPCErrorException:
            raise
        except Exception as exc:  # noqa: BLE001
            return _build_tool_result(
                {"error": str(exc)},
                is_error=True,
                max_blob_bytes=self.config.max_blob_bytes,
            )

        return _build_tool_result(result, is_error=False, max_blob_bytes=self.config.max_blob_bytes)

    async def _run_task(
        self,
        record: "TaskRecord",
        tool_name: "str",
        handler: "BaseRouteHandler",
        tool_args: "dict[str, Any]",
        context: "RequestContext",
    ) -> "None":
        if self.task_store is None:
            return
        try:
            while True:
                result = await self._execute_tool_call(tool_name, handler, tool_args, context)
                if result.get("resultType") != "input_required":
                    result.setdefault("resultType", "complete")
                    await self.task_store.complete(record.task_id, result)
                    return
                await self.task_store.require_input(
                    record.task_id,
                    result.get("inputRequests"),
                    result.get("requestState"),
                )
                context.input_responses = await self.task_store.wait_for_input(record.task_id)
                context.request_state = result.get("requestState")
        except JSONRPCErrorException as exc:
            await self.task_store.fail(record.task_id, exc.error)
        except asyncio.CancelledError:
            await self.task_store.mark_cancelled(record.task_id)
        except Exception as exc:  # noqa: BLE001
            await self.task_store.fail(
                record.task_id,
                JSONRPCError(code=INTERNAL_ERROR, message=str(exc)),
                status_message=str(exc),
            )

    async def server_discover(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        """Describe the modern protocol versions, capabilities, and extensions."""
        capabilities: dict[str, Any] = {
            "tools": {"listChanged": True},
            "resources": {"listChanged": True},
        }
        if self.discovered_prompts:
            capabilities["prompts"] = {"listChanged": True}
        if self.task_config is not None:
            capabilities["extensions"] = {TASKS_EXTENSION: {}}
        result: dict[str, Any] = {
            "supportedVersions": [MCP_PROTOCOL_VERSION],
            "capabilities": capabilities,
        }
        if self.config.instructions is not None:
            result["instructions"] = self.config.instructions
        return result

    async def tools_list(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        tools = []
        for name, handler in sorted(self.discovered_tools.items()):
            handler_tags = set(getattr(handler, "tags", None) or [])
            if not should_include_handler(name, handler_tags, self.config):
                continue

            fn = get_handler_function(handler)
            metadata = get_mcp_metadata(handler) or get_mcp_metadata(fn) or {}
            tool_entry: dict[str, Any] = {
                "name": name,
                "description": render_description(
                    handler, fn, kind="tool", fallback_name=name, opt_keys=self.config.opt_keys
                ),
                "inputSchema": metadata.get("input_schema", generate_schema_for_handler(handler)),
            }
            if "output_schema" in metadata:
                tool_entry["outputSchema"] = metadata["output_schema"]
            if "annotations" in metadata:
                tool_entry["annotations"] = metadata["annotations"]
            if "scopes" in metadata:
                annotations = tool_entry.get("annotations") or {}
                annotations.setdefault("scopes", list(metadata["scopes"]))
                tool_entry["annotations"] = annotations
            tools.append(tool_entry)
        try:
            page, next_cursor = _paginate_list(tools, params, self.config.list_page_size)
        except ValueError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        result: dict[str, Any] = {"tools": page}
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def tools_call(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        tool_name = params.get("name")
        if not tool_name:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'name'"))

        handler = self.discovered_tools.get(tool_name)
        if handler is None:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Tool not found: {tool_name}"))
        handler_tags = set(getattr(handler, "tags", None) or [])
        if not should_include_handler(tool_name, handler_tags, self.config):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Tool not found: {tool_name}"))

        fn = get_handler_function(handler)
        metadata = get_mcp_metadata(handler) or get_mcp_metadata(fn) or {}
        handler_opt = getattr(handler, "opt", None) or {}
        required_capabilities = metadata.get("required_client_capabilities") or handler_opt.get(
            self.config.opt_keys.required_client_capabilities
        )
        if required_capabilities is not None:
            if not isinstance(required_capabilities, dict):
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INTERNAL_ERROR,
                        message=f"Tool '{tool_name}' has invalid required client capability metadata",
                    )
                )
            client_capabilities = context.client_capabilities or {}
            missing_capabilities = {
                name: declaration
                for name, declaration in required_capabilities.items()
                if name not in client_capabilities
            }
            if missing_capabilities:
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                        message=f"Tool '{tool_name}' requires additional client capabilities",
                        data={"requiredCapabilities": missing_capabilities},
                    )
                )
        tool_args = params.get("arguments", {})
        if not isinstance(tool_args, dict):
            return _build_tool_result(
                {"error": "Tool arguments must be an object"},
                is_error=True,
                max_blob_bytes=self.config.max_blob_bytes,
            )

        task_support = metadata.get("task_support")
        extensions = (context.client_capabilities or {}).get("extensions", {})
        task_capable = isinstance(extensions, dict) and TASKS_EXTENSION in extensions
        task_enabled = self.task_config is not None and self.task_store is not None
        if task_support == "required" and not (task_enabled and task_capable):
            raise JSONRPCErrorException(
                JSONRPCError(
                    code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                    message=f"Tool '{tool_name}' requires the {TASKS_EXTENSION} client capability",
                )
            )
        if task_support not in {"optional", "required"} or not task_enabled or not task_capable:
            return await self._execute_tool_call(tool_name, handler, tool_args, context)
        task_input_before_start = metadata.get("task_input_before_start") or handler_opt.get(
            self.config.opt_keys.task_input_before_start
        )
        if task_input_before_start and context.input_responses is None:
            return await self._execute_tool_call(tool_name, handler, tool_args, context)

        task_store = self.task_store
        if task_store is None:  # pragma: no cover - narrowed by task_enabled
            return await self._execute_tool_call(tool_name, handler, tool_args, context)
        record = await task_store.create(context.owner_id)
        background_task = asyncio.create_task(self._run_task(record, tool_name, handler, tool_args, context))
        await task_store.attach_runner(record.task_id, background_task)
        return {"resultType": "task", **record.to_dict()}

    async def resources_list(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        resources = [
            {
                "uri": "litestar://openapi",
                "name": "openapi",
                "description": "OpenAPI schema for this Litestar application",
                "mimeType": "application/json",
            }
        ]
        for name, handler in self.discovered_resources.items():
            handler_tags = set(getattr(handler, "tags", None) or [])
            if not should_include_handler(name, handler_tags, self.config):
                continue

            fn = get_handler_function(handler)
            resources.append(
                {
                    "uri": _resource_uri(name, handler, self.config),
                    "name": name,
                    "description": render_description(
                        handler,
                        fn,
                        kind="resource",
                        fallback_name=name,
                        opt_keys=self.config.opt_keys,
                    ),
                    "mimeType": _resource_mime_type(handler, self.config),
                }
            )
        try:
            page, next_cursor = _paginate_list(resources, params, self.config.list_page_size)
        except ValueError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        result: dict[str, Any] = {"resources": page}
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def resources_templates_list(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        if self.registry is None:
            return {"resourceTemplates": []}
        templates = []
        for entry in self.registry.templates.values():
            handler_tags = set(getattr(entry.handler, "tags", None) or [])
            if not should_include_handler(entry.name, handler_tags, self.config):
                continue
            fn = get_handler_function(entry.handler)
            templates.append(
                {
                    "uriTemplate": entry.template,
                    "name": entry.name,
                    "description": render_description(
                        entry.handler,
                        fn,
                        kind="resource",
                        fallback_name=entry.name,
                        opt_keys=self.config.opt_keys,
                    ),
                    "mimeType": _resource_mime_type(entry.handler, self.config),
                }
            )
        try:
            page, next_cursor = _paginate_list(templates, params, self.config.list_page_size)
        except ValueError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        result: dict[str, Any] = {"resourceTemplates": page}
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def resources_read(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        uri = params.get("uri", "")
        if not isinstance(uri, str) or not uri:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Invalid resource URI: {uri}"))

        if uri == "litestar://openapi" and self.app_ref is not None:
            return {
                "contents": [
                    {
                        "uri": "litestar://openapi",
                        "mimeType": "application/json",
                        "text": encode_json(_without_undefined_values(self.app_ref.openapi_schema.to_schema())).decode(
                            "utf-8"
                        ),
                    }
                ]
            }

        resource_match = next(
            (
                (name, handler)
                for name, handler in self.discovered_resources.items()
                if _resource_uri(name, handler, self.config) == uri
            ),
            None,
        )
        if resource_match is not None:
            resource_name, handler = resource_match
            handler_tags = set(getattr(handler, "tags", None) or [])
            if not should_include_handler(resource_name, handler_tags, self.config):
                raise JSONRPCErrorException(mcp_error_for_resource_not_found(uri))

            try:
                token = _request_context.set(context)
                try:
                    response = await execute_handler_response(
                        handler,
                        self.app_ref,
                        {},
                        request=context.request,
                        scope_overrides=_scope_overrides_for_context(context),
                    )
                finally:
                    _request_context.reset(token)
            except MCPToolErrorResult as err:
                raise JSONRPCErrorException(mcp_error_for_resource_read(err)) from err
            except Exception as exc:
                raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc

            if isinstance(response.content, MCPInputRequiredResult):
                return response.content.to_result()
            try:
                content = _resource_content_from_response(
                    uri,
                    response,
                    fallback_mime_type=_resource_mime_type(handler, self.config),
                    max_blob_bytes=self.config.max_blob_bytes,
                )
            except ValueError as exc:
                raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc

            return {"contents": [content]}

        template_entries = self.registry.templates.values() if self.registry is not None else ()
        for entry in template_entries:
            extracted = match_uri(entry.template, uri)
            if extracted is None:
                continue
            handler_tags = set(getattr(entry.handler, "tags", None) or [])
            if not should_include_handler(entry.name, handler_tags, self.config):
                continue
            try:
                token = _request_context.set(context)
                try:
                    response = await execute_handler_response(
                        entry.handler,
                        self.app_ref,
                        dict(extracted),
                        request=context.request,
                        scope_overrides=_scope_overrides_for_context(context),
                    )
                finally:
                    _request_context.reset(token)
            except MCPToolErrorResult as err:
                raise JSONRPCErrorException(mcp_error_for_resource_read(err)) from err
            except Exception as exc:
                raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc

            if isinstance(response.content, MCPInputRequiredResult):
                return response.content.to_result()
            try:
                content = _resource_content_from_response(
                    uri,
                    response,
                    fallback_mime_type=_resource_mime_type(entry.handler, self.config),
                    max_blob_bytes=self.config.max_blob_bytes,
                )
            except ValueError as exc:
                raise JSONRPCErrorException(mcp_error_for_resource_read(exc)) from exc

            return {"contents": [content]}

        raise JSONRPCErrorException(mcp_error_for_resource_not_found(uri))

    async def completion_complete(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        return {"completion": {"values": [], "total": 0, "hasMore": False}}

    async def prompts_list(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        prompts = [
            render_prompt_entry(registration, self.config)
            for registration in self.discovered_prompts.values()
            if should_include_prompt(registration, self.config)
        ]
        try:
            page, next_cursor = _paginate_list(prompts, params, self.config.list_page_size)
        except ValueError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        result: dict[str, Any] = {"prompts": page}
        if next_cursor is not None:
            result["nextCursor"] = next_cursor
        return result

    async def prompts_get(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        prompt_name = params.get("name")
        if not prompt_name:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'name'"))

        registration = self.discovered_prompts.get(prompt_name)
        if registration is None or not should_include_prompt(registration, self.config):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=f"Prompt not found: {prompt_name}"))

        prompt_args = params.get("arguments", {})
        if not isinstance(prompt_args, dict):
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Prompt arguments must be an object"))

        for arg_key, arg_val in prompt_args.items():
            if not isinstance(arg_val, str):
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INVALID_PARAMS,
                        message=f"Argument '{arg_key}' must be a string, got {type(arg_val).__name__}",
                    )
                )

        resolved_description = resolve_prompt_description(registration, self.config)

        if registration.handler is not None:
            declared_args = registration.get_arguments()
            declared_names = {arg["name"] for arg in declared_args}
            missing = [arg["name"] for arg in declared_args if arg.get("required") and arg["name"] not in prompt_args]
            if missing:
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INVALID_PARAMS,
                        message=f"Missing required prompt argument(s): {', '.join(sorted(missing))}",
                    )
                )
            unknown = [name for name in prompt_args if name not in declared_names]
            if unknown:
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INVALID_PARAMS,
                        message=(
                            f"Unknown prompt argument(s): {', '.join(sorted(unknown))}. "
                            "Prompt handlers should declare scalar arguments only; "
                            "any 'data' body parameter on the handler is ignored by prompts/get."
                        ),
                    )
                )

            try:
                token = _request_context.set(context)
                try:
                    result = await execute_handler(
                        registration.handler,
                        self.app_ref,
                        prompt_args,
                        request=context.request,
                        scope_overrides=_scope_overrides_for_context(context),
                    )
                finally:
                    _request_context.reset(token)
            except MCPToolErrorResult as err:
                _logger.warning(
                    "Prompt handler returned error result: %s (status=%d)",
                    prompt_name,
                    err.status_code,
                )
                raise JSONRPCErrorException(mcp_error_for_prompt_execution(err)) from err
            except JSONRPCErrorException:
                raise
            except Exception as exc:
                _logger.exception("Prompt handler execution failed: %s", prompt_name)
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INTERNAL_ERROR,
                        message="Prompt execution failed",
                        data={"error": type(exc).__name__, "detail": str(exc)},
                    )
                ) from exc
            if isinstance(result, MCPInputRequiredResult):
                return result.to_result()
            handler_result: dict[str, Any]
            if isinstance(result, dict) and "messages" in result:
                handler_result = result
            else:
                handler_result = {"messages": _normalize_prompt_result(result)}
            if resolved_description is not None and "description" not in handler_result:
                handler_result["description"] = resolved_description
            return handler_result

        if registration.fn is not None:
            try:
                inspect.signature(registration.fn).bind(**prompt_args)
            except TypeError as exc:
                raise JSONRPCErrorException(
                    JSONRPCError(code=INVALID_PARAMS, message=f"Invalid prompt arguments: {exc!s}")
                ) from exc

            try:
                token = _request_context.set(context)
                try:
                    result = registration.fn(**prompt_args)
                    if inspect.isawaitable(result):
                        result = await result
                finally:
                    _request_context.reset(token)
            except Exception as exc:
                _logger.exception("Prompt function execution failed: %s", prompt_name)
                raise JSONRPCErrorException(
                    JSONRPCError(
                        code=INTERNAL_ERROR,
                        message="Prompt execution failed",
                        data={"error": type(exc).__name__, "detail": str(exc)},
                    )
                ) from exc
            if isinstance(result, MCPInputRequiredResult):
                return result.to_result()
            messages = _normalize_prompt_result(result)
            get_result: dict[str, Any] = {"messages": messages}
            if resolved_description is not None and "description" not in get_result:
                get_result["description"] = resolved_description
            return get_result

        raise JSONRPCErrorException(JSONRPCError(code=INTERNAL_ERROR, message=f"Prompt has no callable: {prompt_name}"))

    async def tasks_get(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        _require_tasks_capability(context)
        if self.task_store is None:
            raise JSONRPCErrorException(JSONRPCError(code=METHOD_NOT_FOUND, message="Task store not configured"))
        task_id = params.get("taskId")
        if not task_id:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'taskId'"))
        try:
            record = await self.task_store.get(task_id, context.owner_id)
        except TaskLookupError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        return record.to_dict()

    async def tasks_update(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        _require_tasks_capability(context)
        if self.task_store is None:
            raise JSONRPCErrorException(JSONRPCError(code=METHOD_NOT_FOUND, message="Task store not configured"))
        task_id = params.get("taskId")
        if not task_id:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'taskId'"))
        input_responses = params.get("inputResponses")
        if not isinstance(input_responses, dict):
            raise JSONRPCErrorException(
                JSONRPCError(code=INVALID_PARAMS, message="The 'inputResponses' parameter must be an object")
            )
        try:
            await self.task_store.update(task_id, context.owner_id, input_responses)
        except TaskLookupError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        return {}

    async def tasks_cancel(self, params: "dict[str, Any]", context: "RequestContext") -> "dict[str, Any]":
        _require_tasks_capability(context)
        if self.task_store is None:
            raise JSONRPCErrorException(JSONRPCError(code=METHOD_NOT_FOUND, message="Task store not configured"))
        task_id = params.get("taskId")
        if not task_id:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message="Missing required param: 'taskId'"))
        try:
            await self.task_store.cancel(task_id, context.owner_id)
        except TaskLookupError as exc:
            raise JSONRPCErrorException(JSONRPCError(code=INVALID_PARAMS, message=str(exc))) from exc
        return {}


def _require_tasks_capability(context: "RequestContext") -> None:
    extensions = (context.client_capabilities or {}).get("extensions")
    if not isinstance(extensions, dict) or TASKS_EXTENSION not in extensions:
        raise JSONRPCErrorException(
            JSONRPCError(
                code=MISSING_REQUIRED_CLIENT_CAPABILITY,
                message=f"The {TASKS_EXTENSION} client capability is required",
            )
        )
