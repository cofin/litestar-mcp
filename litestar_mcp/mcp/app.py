"""High-level FastMCP-style `MCP` application wrapper around Litestar and `LitestarMCP`."""

import contextlib
import copy
import hashlib
import inspect
import os
import re
import sys
import urllib.parse
from collections.abc import Mapping
from functools import wraps
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast, get_args, get_origin, get_type_hints

from litestar import Litestar, Response
from litestar.exceptions import PermissionDeniedException
from litestar.handlers import get, post
from litestar.openapi.spec import Operation
from litestar.serialization import get_serializer
from litestar.types import Empty, TypeDecodersSequence

from litestar_mcp.core.schema_builder import unwrap_root_object_schema
from litestar_mcp.core.serialization import encode_json
from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.content import MCPBlobResource, MCPInputRequiredResult, MCPResourceLink, MCPToolResult
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.registry import _clean_icons, _clean_meta, _clean_title
from litestar_mcp.mcp.stdio import MCPStdioContext, run_stdio
from litestar_mcp.utils import classify_resource_uri
from litestar_mcp.utils.handler_signature import normalize_synthesized_handler_signature

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from litestar.background_tasks import BackgroundTask, BackgroundTasks
    from litestar.config.response_cache import CACHE_FOREVER
    from litestar.connection import Request
    from litestar.datastructures import CacheControlHeader, ETag
    from litestar.dto import AbstractDTO
    from litestar.enums import MediaType
    from litestar.exceptions import HTTPException
    from litestar.openapi.datastructures import ResponseSpec
    from litestar.openapi.spec import SecurityRequirement
    from litestar.types import (
        AfterRequestHookHandler,
        AfterResponseHookHandler,
        BeforeRequestHookHandler,
        CacheKeyBuilder,
        ControllerRouterHandler,
        Dependencies,
        EmptyType,
        ExceptionHandlersMap,
        Guard,
        Middleware,
        ResponseCookies,
        ResponseHeaders,
        TypeEncodersMap,
    )
    from litestar.types.callable_types import OperationIDCreator

_INTERNAL_DISPATCH_SCOPE_KEY = "litestar_mcp.internal_dispatch"
_FULL_TEMPLATE_SEGMENT_RE = re.compile(r"\{[A-Za-z_][A-Za-z0-9_]*\}")
_SAFE_SEGMENT_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]+")
_ROUTE_HANDLER_KWARG_NAMES = (
    "after_request",
    "after_response",
    "background",
    "before_request",
    "cache",
    "cache_control",
    "cache_key_builder",
    "dependencies",
    "dto",
    "etag",
    "exception_handlers",
    "guards",
    "media_type",
    "middleware",
    "opt",
    "request_class",
    "response_class",
    "response_cookies",
    "response_headers",
    "return_dto",
    "signature_namespace",
    "status_code",
    "sync_to_thread",
    "content_encoding",
    "content_media_type",
    "deprecated",
    "description",
    "include_in_schema",
    "operation_class",
    "operation_id",
    "raises",
    "response_description",
    "responses",
    "security",
    "summary",
    "tags",
    "type_decoders",
    "type_encoders",
)


def _require_internal_dispatch(connection: "Any", _route_handler: "Any") -> "None":
    """Reject direct HTTP access to standalone wrapper internal routes."""
    if not connection.scope.get(_INTERNAL_DISPATCH_SCOPE_KEY):
        msg = "Standalone MCP internal routes are not directly accessible"
        raise PermissionDeniedException(msg)


def _build_standalone_route_kwargs(
    handler_kwargs: "dict[str, Any]",
    *,
    fn: "Callable[..., Any]",
    forced_opt: "dict[str, Any]",
    default_opt: "dict[str, Any] | None" = None,
) -> "dict[str, Any]":
    """Merge user route kwargs with standalone MCP internal route metadata."""
    route_kwargs = dict(handler_kwargs)
    route_kwargs.pop("path", None)
    if route_kwargs.get("sync_to_thread") is None and not inspect.iscoroutinefunction(fn):
        route_kwargs["sync_to_thread"] = False

    user_guards = route_kwargs.pop("guards", None)
    guards = [_require_internal_dispatch]
    if user_guards is not None:
        guards.extend(list(user_guards))

    opt = dict(route_kwargs.pop("opt", {}) or {})
    user_opt_keys = set(forced_opt)
    if default_opt is not None:
        user_opt_keys.update(default_opt)
    for key in user_opt_keys:
        if key in route_kwargs:
            opt[key] = route_kwargs.pop(key)

    if default_opt is not None:
        for key, value in default_opt.items():
            opt.setdefault(key, value)
    opt.update(forced_opt)

    route_kwargs["guards"] = guards
    route_kwargs["opt"] = opt
    return route_kwargs


def _populate_forced_descriptors(
    forced_opt: "dict[str, Any]",
    *,
    title_key: "str",
    icons_key: "str",
    meta_key: "str",
    title: "str | None",
    icons: "list[dict[str, Any]] | None",
    meta: "dict[str, Any] | None",
) -> "None":
    """Store normalized non-empty descriptor fields in `forced_opt` without clobbering omitted keys."""
    if title is not None:
        forced_opt[title_key] = title
    if icons is not None:
        forced_opt[icons_key] = copy.deepcopy(icons)
    if meta is not None:
        forced_opt[meta_key] = copy.deepcopy(meta)


def _standalone_internal_path(base_path: "str", *parts: "str") -> "str":
    return "/".join((base_path.rstrip("/"), "internal", *parts))


def _resolve_resource_return_annotation(fn: "Callable[..., Any]") -> "Any":
    """Resolve the return annotation of a standalone resource callable, unwrapping `Annotated`."""
    try:
        hints = get_type_hints(fn, include_extras=True)
        ret = hints.get("return")
    except (AttributeError, NameError, TypeError, ValueError):
        ret = (getattr(fn, "__annotations__", None) or {}).get("return")
    if ret is None:
        ret = (getattr(fn, "__annotations__", None) or {}).get("return")
    while get_origin(ret) is Annotated:
        args = get_args(ret)
        if not args:
            break
        ret = args[0]
    return ret


def _default_resource_mime_type_for_callable(fn: "Callable[..., Any]") -> "str | None":
    """Return `"application/octet-stream"` when `fn` declares a `bytes` or `bytearray` return annotation."""
    ret = _resolve_resource_return_annotation(fn)
    if ret in (bytes, bytearray):
        return "application/octet-stream"
    if isinstance(ret, str) and ret.strip() in {"bytes", "bytearray"}:
        return "application/octet-stream"
    return None


def _normalize_resource_path_segment(seg: "str", uri: "str") -> "str":
    """Keep whole `{var}` path segments typed for Litestar and sanitize partial-brace segments."""
    if _FULL_TEMPLATE_SEGMENT_RE.fullmatch(seg):
        return f"{{{seg[1:-1]}:str}}"
    if "{" in seg or "}" in seg:
        cleaned = _SAFE_SEGMENT_CHARS_RE.sub("-", seg.replace("{", "").replace("}", "")).strip("-") or "segment"
        digest = hashlib.sha256(uri.encode("utf-8")).hexdigest()[:8]
        return f"{cleaned}-{digest}"
    return seg


def _standalone_resource_internal_path(base_path: "str", uri: "str", resource_name: "str") -> "str":
    """Build a normalized internal Litestar route path for a standalone MCP resource."""
    parsed = urllib.parse.urlparse(uri)
    raw_parts = [parsed.scheme] if parsed.scheme else []
    raw_parts.extend((parsed.netloc, parsed.path))
    segments = [_normalize_resource_path_segment(seg, uri) for part in raw_parts for seg in part.split("/") if seg]
    if not segments:
        segments = [resource_name]
    return _standalone_internal_path(base_path, "resources", *segments)


def _collect_route_handler_kwargs(values: "dict[str, Any]") -> "dict[str, Any]":
    """Collect Litestar route-handler kwargs from a standalone decorator frame."""
    route_kwargs = {key: values[key] for key in _ROUTE_HANDLER_KWARG_NAMES}
    route_name = values["route_name"]
    if route_name is not None:
        route_kwargs["name"] = route_name
    route_kwargs.update(values["kwargs"])
    return route_kwargs


def _convert_kwargs_to_flags(kwargs: "dict[str, Any]") -> "list[str]":
    """Convert Python keyword arguments to Litestar CLI run flags."""
    flags = []
    mapping = {
        "host": "--host",
        "port": "--port",
        "reload": "--reload",
        "reload_dirs": "--reload-dir",
        "reload_includes": "--reload-include",
        "reload_excludes": "--reload-exclude",
        "workers": "--web-concurrency",
        "fd": "--fd",
        "uds": "--uds",
        "debug": "--debug",
        "pdb": "--pdb",
        "ssl_certfile": "--ssl-certfile",
        "ssl_keyfile": "--ssl-keyfile",
        "create_self_signed_cert": "--create-self-signed-cert",
    }

    for key, value in kwargs.items():
        if key not in mapping:
            continue

        flag = mapping[key]

        if isinstance(value, bool):
            if value:
                flags.append(flag)
        elif isinstance(value, (list, tuple)):
            for item in value:
                flags.extend([flag, str(item)])
        elif value is not None:
            flags.extend([flag, str(value)])

    return flags


def _resolve_litestar_app_env(app: "Litestar") -> "str | None":
    """Attempt to resolve the import path for the Litestar application.

    This inspects the call stack to find where the application is defined
    and matches it against the module globals to find its variable name.
    """
    if os.getenv("LITESTAR_APP"):
        return os.getenv("LITESTAR_APP")

    frame = inspect.currentframe()
    caller_frame = None
    while frame:
        module = inspect.getmodule(frame)
        if module and "litestar_mcp" not in module.__name__:
            caller_frame = frame
            break
        frame = frame.f_back

    if not caller_frame:
        return None

    caller_globals = caller_frame.f_globals
    caller_module = inspect.getmodule(caller_frame)
    if not caller_module:
        return None

    app_var_name = None
    for name, val in caller_globals.items():
        if val is app:
            app_var_name = name
            break

    if not app_var_name:
        return None

    module_name = caller_module.__name__
    if module_name == "__main__" and hasattr(caller_module, "__file__") and caller_module.__file__:
        file_path = Path(caller_module.__file__).resolve()
        for path_str in sys.path:
            if not path_str:
                continue
            path = Path(path_str).resolve()
            if file_path.is_relative_to(path):
                rel_path = file_path.relative_to(path)
                module_name = ".".join(rel_path.with_suffix("").parts)
                break
        else:
            module_name = file_path.stem

    return f"{module_name}:{app_var_name}"


def _validate_standalone_output_schema(output_schema: "Any") -> "dict[str, Any] | bool | None":
    """Validate and normalize an `output_schema` policy passed to `MCP.tool`."""
    if output_schema is None:
        return None
    if isinstance(output_schema, bool):
        return output_schema
    if not isinstance(output_schema, Mapping):
        msg = "output_schema must be a mapping, bool, or None"
        raise TypeError(msg)
    resolved_schema = unwrap_root_object_schema(dict(output_schema))
    if resolved_schema.get("type") != "object":
        msg = "output_schema mapping must resolve to root type 'object'"
        raise ValueError(msg)
    return resolved_schema


def _merge_type_encoders(app_encoders: "Any", route_encoders: "Any") -> "TypeEncodersMap | None":
    """Merge standalone application and route `type_encoders` with route precedence."""
    merged: dict[Any, Callable[[Any], Any]] = {}
    if isinstance(app_encoders, Mapping):
        merged.update(app_encoders)
    if isinstance(route_encoders, Mapping):
        merged.update(route_encoders)
    return merged or None


def _json_response_wrapper(
    fn: "Callable[..., Any]",
    *,
    path: "str" = "",
    dependencies: "Mapping[str, Any] | None" = None,
    type_encoders: "TypeEncodersMap | None" = None,
    type_encoders_resolver: "Callable[[], TypeEncodersMap | None] | None" = None,
) -> "Callable[..., Any]":
    """Wrap a handler function to return a JSON `Response` or MCP content helper."""

    def _active_encoders() -> "TypeEncodersMap | None":
        resolved = type_encoders_resolver() if type_encoders_resolver is not None else None
        return resolved or type_encoders

    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapped(*args: "Any", **kwargs: "Any") -> "Any":
            return _to_json_response(await fn(*args, **kwargs), type_encoders=_active_encoders())

        return normalize_synthesized_handler_signature(async_wrapped, fn, path=path, dependencies=dependencies)

    @wraps(fn)
    def sync_wrapped(*args: "Any", **kwargs: "Any") -> "Any":
        return _to_json_response(fn(*args, **kwargs), type_encoders=_active_encoders())

    return normalize_synthesized_handler_signature(sync_wrapped, fn, path=path, dependencies=dependencies)


def _to_json_response(res: "Any", *, type_encoders: "TypeEncodersMap | None" = None) -> "Any":
    """Return `res` as a JSON response or untouched MCP helper while honoring `type_encoders`.

    MCP content helpers pass through untouched so the internal executor pipeline
    can inspect them directly. Native primitives (`str`, `bytes`, `bytearray`,
    `None`) consult `type_encoders` by MRO before pre-encoding because `msgspec`
    serializes native primitives without invoking `enc_hook`.
    """
    if isinstance(res, (Response, MCPToolResult, MCPBlobResource, MCPResourceLink, MCPInputRequiredResult)):
        return res
    if res is None or isinstance(res, (str, bytes, bytearray)):
        if type_encoders:
            for base in type(res).__mro__[:-1]:
                encoder = type_encoders.get(base)
                if encoder is not None and encoder is not base:
                    res = encoder(res)
                    break
        if res is None or isinstance(res, (str, bytes, bytearray)):
            serializer = get_serializer(type_encoders) if type_encoders else None
            return Response(content=encode_json(res, serializer=serializer), media_type="application/json")
    return Response(content=res, media_type="application/json", type_encoders=type_encoders)


def _is_json_compatible_media_type(media_type: "str | None") -> "bool":
    """Return whether `media_type` represents JSON content safe for Litestar JSON serialization."""
    if not media_type:
        return False
    base = media_type.split(";", 1)[0].strip().lower()
    return base == "application/json" or base.endswith("+json")


def _to_resource_response(res: "Any", *, media_type: "str | None" = None) -> "Response[Any]":
    """Convert a standalone resource return value into a Litestar Response without JSON-quoting raw text or bytes."""
    if isinstance(res, Response):
        return res
    if isinstance(res, (bytes, bytearray)):
        return Response(content=bytes(res), media_type=media_type or "application/octet-stream")
    if isinstance(res, str):
        return Response(content=res, media_type=media_type or "text/plain")
    if res is None:
        return Response(content=b"", media_type=media_type or "text/plain")
    target_media_type = media_type if _is_json_compatible_media_type(media_type) else "application/json"
    return Response(content=res, media_type=target_media_type)


def _resource_response_wrapper(
    fn: "Callable[..., Any]",
    *,
    path: "str" = "",
    dependencies: "Mapping[str, Any] | None" = None,
    media_type: "str | None" = None,
) -> "Callable[..., Any]":
    """Wrap a standalone resource handler preserving raw `str`/`bytes` media types and Litestar signature hints."""
    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapped(*args: "Any", **kwargs: "Any") -> "Any":
            return _to_resource_response(await fn(*args, **kwargs), media_type=media_type)

        return normalize_synthesized_handler_signature(async_wrapped, fn, path=path, dependencies=dependencies)

    @wraps(fn)
    def sync_wrapped(*args: "Any", **kwargs: "Any") -> "Any":
        return _to_resource_response(fn(*args, **kwargs), media_type=media_type)

    return normalize_synthesized_handler_signature(sync_wrapped, fn, path=path, dependencies=dependencies)


class MCP:
    """A class that simplifies Model Context Protocol application setup.

    This provides decorators and programmatic server execution for standalone
    applications.
    """

    config: MCPConfig
    """Active `MCPConfig` instance governing the standalone MCP application."""
    plugin: LitestarMCP
    """Underlying `LitestarMCP` plugin registered on the Litestar application."""

    def __init__(
        self,
        name: "str",
        *,
        instructions: "str | None" = None,
        config: "MCPConfig | None" = None,
        plugins: "list[Any] | None" = None,
        route_handlers: "list[ControllerRouterHandler] | None" = None,
        **kwargs: "Any",
    ) -> "None":
        resolved_plugins = plugins or []
        found_plugin: LitestarMCP | None = None
        for p in resolved_plugins:
            if isinstance(p, LitestarMCP):
                found_plugin = p
                break

        if found_plugin is None:
            self.config = config or MCPConfig()
            self.config.name = name
            if instructions is not None:
                self.config.instructions = instructions
            found_plugin = LitestarMCP(config=self.config)
            resolved_plugins.append(found_plugin)
        else:
            self.config = found_plugin.config
            self.config.name = name
            if instructions is not None:
                self.config.instructions = instructions

        self.plugin: LitestarMCP = found_plugin

        self._route_handlers = route_handlers or []
        self._plugins = resolved_plugins
        self._kwargs = kwargs
        self._app: Litestar | None = None

    @property
    def app(self) -> "Litestar":
        """Get the Litestar application instance.

        This lazily instantiates the Litestar app upon first access,
        ensuring all dynamically registered handlers are captured.
        """
        if self._app is None:
            self._app = Litestar(
                route_handlers=self._route_handlers,
                plugins=self._plugins,
                **self._kwargs,
            )
        return self._app

    def tool(
        self,
        name: "str | None" = None,
        *,
        after_request: "AfterRequestHookHandler | None" = None,
        after_response: "AfterResponseHookHandler | None" = None,
        background: "BackgroundTask | BackgroundTasks | None" = None,
        before_request: "BeforeRequestHookHandler | None" = None,
        cache: "bool | int | type[CACHE_FOREVER]" = False,
        cache_control: "CacheControlHeader | None" = None,
        cache_key_builder: "CacheKeyBuilder | None" = None,
        dependencies: "Dependencies | None" = None,
        dto: "type[AbstractDTO[Any]] | EmptyType | None" = Empty,
        etag: "ETag | None" = None,
        exception_handlers: "ExceptionHandlersMap | None" = None,
        guards: "Sequence[Guard] | None" = None,
        media_type: "MediaType | str | None" = None,
        middleware: "Sequence[Middleware] | None" = None,
        route_name: "str | None" = None,
        opt: "Mapping[str, Any] | None" = None,
        request_class: "type[Request[Any, Any, Any]] | None" = None,
        response_class: "type[Response[Any]] | None" = None,
        response_cookies: "ResponseCookies | None" = None,
        response_headers: "ResponseHeaders | None" = None,
        return_dto: "type[AbstractDTO[Any]] | EmptyType | None" = Empty,
        signature_namespace: "Mapping[str, Any] | None" = None,
        status_code: "int | None" = None,
        sync_to_thread: "bool | None" = None,
        content_encoding: "str | None" = None,
        content_media_type: "str | None" = None,
        deprecated: "bool" = False,
        description: "str | None" = None,
        include_in_schema: "bool | EmptyType" = Empty,
        operation_class: "type[Operation]" = Operation,
        operation_id: "str | OperationIDCreator | None" = None,
        raises: "Sequence[type[HTTPException]] | None" = None,
        response_description: "str | None" = None,
        responses: "Mapping[int, ResponseSpec] | None" = None,
        security: "Sequence[SecurityRequirement] | None" = None,
        summary: "str | None" = None,
        tags: "Sequence[str] | None" = None,
        type_decoders: "TypeDecodersSequence | None" = None,
        type_encoders: "TypeEncodersMap | None" = None,
        output_schema: "dict[str, Any] | bool | None" = None,
        title: "str | None" = None,
        icons: "Sequence[dict[str, Any]] | None" = None,
        meta: "Mapping[str, Any] | None" = None,
        **kwargs: "Any",
    ) -> "Callable[[Callable[..., Any]], Callable[..., Any]]":
        """Decorator to register a function as an MCP tool.

        This dynamically wraps the function inside a Litestar route handler
        and registers it to the plugin. Additional keyword arguments are
        passed through to Litestar's `post()` route decorator.
        """
        route_kwargs = _collect_route_handler_kwargs(locals())
        validated_output = _validate_standalone_output_schema(output_schema)
        cleaned_title = _clean_title(title)
        cleaned_icons = _clean_icons(icons)
        cleaned_meta = _clean_meta(meta)

        def decorator(fn: "Callable[..., Any]") -> "Callable[..., Any]":
            tool_name = name or fn.__name__
            path = _standalone_internal_path(self.config.base_path, "tools", tool_name)
            opt_keys = self.config.opt_keys
            forced_opt: dict[str, Any] = {opt_keys.tool: tool_name, "_mcp_synthesized_tool": True}
            if validated_output is not None:
                forced_opt[opt_keys.output_schema] = (
                    validated_output if isinstance(validated_output, bool) else copy.deepcopy(validated_output)
                )
            _populate_forced_descriptors(
                forced_opt,
                title_key=opt_keys.tool_title,
                icons_key=opt_keys.tool_icons,
                meta_key=opt_keys.tool_meta,
                title=cleaned_title,
                icons=cleaned_icons,
                meta=cleaned_meta,
            )

            fallback_encoders = _merge_type_encoders(
                self._kwargs.get("type_encoders"),
                route_kwargs.get("type_encoders"),
            )
            handler_ref: list[Any] = []

            def _resolve_encoders() -> "TypeEncodersMap | None":
                if handler_ref and getattr(handler_ref[0], "ownership_layers", None):
                    with contextlib.suppress(Exception):
                        resolved = handler_ref[0].resolve_type_encoders()
                        if resolved:
                            return cast("TypeEncodersMap", resolved)
                return fallback_encoders

            handler = post(
                path=path,
                **_build_standalone_route_kwargs(
                    route_kwargs,
                    fn=fn,
                    forced_opt=forced_opt,
                    default_opt={opt_keys.description: description or fn.__doc__ or ""},
                ),
            )(
                _json_response_wrapper(
                    fn,
                    path=path,
                    dependencies=route_kwargs.get("dependencies"),
                    type_encoders=fallback_encoders,
                    type_encoders_resolver=_resolve_encoders,
                )
            )
            handler_ref.append(handler)

            self.plugin.register_dynamic_handler(handler)
            return fn

        return decorator

    def resource(
        self,
        uri: "str",
        *,
        name: "str | None" = None,
        mime_type: "str | None" = None,
        after_request: "AfterRequestHookHandler | None" = None,
        after_response: "AfterResponseHookHandler | None" = None,
        background: "BackgroundTask | BackgroundTasks | None" = None,
        before_request: "BeforeRequestHookHandler | None" = None,
        cache: "bool | int | type[CACHE_FOREVER]" = False,
        cache_control: "CacheControlHeader | None" = None,
        cache_key_builder: "CacheKeyBuilder | None" = None,
        dependencies: "Dependencies | None" = None,
        dto: "type[AbstractDTO[Any]] | EmptyType | None" = Empty,
        etag: "ETag | None" = None,
        exception_handlers: "ExceptionHandlersMap | None" = None,
        guards: "Sequence[Guard] | None" = None,
        media_type: "MediaType | str | None" = None,
        middleware: "Sequence[Middleware] | None" = None,
        route_name: "str | None" = None,
        opt: "Mapping[str, Any] | None" = None,
        request_class: "type[Request[Any, Any, Any]] | None" = None,
        response_class: "type[Response[Any]] | None" = None,
        response_cookies: "ResponseCookies | None" = None,
        response_headers: "ResponseHeaders | None" = None,
        return_dto: "type[AbstractDTO[Any]] | EmptyType | None" = Empty,
        signature_namespace: "Mapping[str, Any] | None" = None,
        status_code: "int | None" = None,
        sync_to_thread: "bool | None" = None,
        content_encoding: "str | None" = None,
        content_media_type: "str | None" = None,
        deprecated: "bool" = False,
        description: "str | None" = None,
        include_in_schema: "bool | EmptyType" = Empty,
        operation_class: "type[Operation]" = Operation,
        operation_id: "str | OperationIDCreator | None" = None,
        raises: "Sequence[type[HTTPException]] | None" = None,
        response_description: "str | None" = None,
        responses: "Mapping[int, ResponseSpec] | None" = None,
        security: "Sequence[SecurityRequirement] | None" = None,
        summary: "str | None" = None,
        tags: "Sequence[str] | None" = None,
        type_decoders: "TypeDecodersSequence | None" = None,
        type_encoders: "TypeEncodersMap | None" = None,
        title: "str | None" = None,
        icons: "Sequence[dict[str, Any]] | None" = None,
        meta: "Mapping[str, Any] | None" = None,
        **kwargs: "Any",
    ) -> "Callable[[Callable[..., Any]], Callable[..., Any]]":
        """Decorator to register a function as an MCP resource.

        This dynamically wraps the function inside a Litestar route handler
        and registers it to the plugin. Additional keyword arguments are
        passed through to Litestar's `get()` route decorator.
        """
        route_kwargs = _collect_route_handler_kwargs(locals())
        static_uri, template_uri = classify_resource_uri(uri=uri)
        cleaned_title = _clean_title(title)
        cleaned_icons = _clean_icons(icons)
        cleaned_meta = _clean_meta(meta)

        def decorator(fn: "Callable[..., Any]") -> "Callable[..., Any]":
            resource_name = name or fn.__name__
            path = _standalone_resource_internal_path(self.config.base_path, uri, resource_name)
            opt_keys = self.config.opt_keys
            forced_opt: dict[str, Any] = {opt_keys.resource: resource_name}
            if static_uri is not None:
                forced_opt[opt_keys.resource_uri] = static_uri
            elif template_uri is not None:
                forced_opt[opt_keys.resource_template] = template_uri
            _populate_forced_descriptors(
                forced_opt,
                title_key=opt_keys.resource_title,
                icons_key=opt_keys.resource_icons,
                meta_key=opt_keys.resource_meta,
                title=cleaned_title,
                icons=cleaned_icons,
                meta=cleaned_meta,
            )

            route_media_type = route_kwargs.get("media_type")
            raw_route_mime = getattr(route_media_type, "value", route_media_type)
            if isinstance(mime_type, str) and mime_type:
                configured_mime: str | None = mime_type
            elif isinstance(raw_route_mime, str) and raw_route_mime:
                configured_mime = raw_route_mime
            else:
                configured_mime = _default_resource_mime_type_for_callable(fn)

            handler = get(
                path=path,
                **_build_standalone_route_kwargs(
                    route_kwargs,
                    fn=fn,
                    forced_opt=forced_opt,
                    default_opt={
                        opt_keys.resource_description: description or fn.__doc__ or "",
                        opt_keys.resource_mime_type: configured_mime,
                    },
                ),
            )(
                _resource_response_wrapper(
                    fn,
                    path=path,
                    dependencies=route_kwargs.get("dependencies"),
                    media_type=configured_mime,
                )
            )
            self.plugin.register_dynamic_handler(handler)
            return fn

        return decorator

    def prompt(
        self,
        name: "str | None" = None,
        *,
        after_request: "AfterRequestHookHandler | None" = None,
        after_response: "AfterResponseHookHandler | None" = None,
        background: "BackgroundTask | BackgroundTasks | None" = None,
        before_request: "BeforeRequestHookHandler | None" = None,
        cache: "bool | int | type[CACHE_FOREVER]" = False,
        cache_control: "CacheControlHeader | None" = None,
        cache_key_builder: "CacheKeyBuilder | None" = None,
        dependencies: "Dependencies | None" = None,
        dto: "type[AbstractDTO[Any]] | EmptyType | None" = Empty,
        etag: "ETag | None" = None,
        exception_handlers: "ExceptionHandlersMap | None" = None,
        guards: "Sequence[Guard] | None" = None,
        media_type: "MediaType | str | None" = None,
        middleware: "Sequence[Middleware] | None" = None,
        route_name: "str | None" = None,
        opt: "Mapping[str, Any] | None" = None,
        request_class: "type[Request[Any, Any, Any]] | None" = None,
        response_class: "type[Response[Any]] | None" = None,
        response_cookies: "ResponseCookies | None" = None,
        response_headers: "ResponseHeaders | None" = None,
        return_dto: "type[AbstractDTO[Any]] | EmptyType | None" = Empty,
        signature_namespace: "Mapping[str, Any] | None" = None,
        status_code: "int | None" = None,
        sync_to_thread: "bool | None" = None,
        content_encoding: "str | None" = None,
        content_media_type: "str | None" = None,
        deprecated: "bool" = False,
        description: "str | None" = None,
        include_in_schema: "bool | EmptyType" = Empty,
        operation_class: "type[Operation]" = Operation,
        operation_id: "str | OperationIDCreator | None" = None,
        raises: "Sequence[type[HTTPException]] | None" = None,
        response_description: "str | None" = None,
        responses: "Mapping[int, ResponseSpec] | None" = None,
        security: "Sequence[SecurityRequirement] | None" = None,
        summary: "str | None" = None,
        tags: "Sequence[str] | None" = None,
        type_decoders: "TypeDecodersSequence | None" = None,
        type_encoders: "TypeEncodersMap | None" = None,
        title: "str | None" = None,
        arguments: "Sequence[dict[str, Any]] | None" = None,
        icons: "Sequence[dict[str, Any]] | None" = None,
        meta: "Mapping[str, Any] | None" = None,
        **kwargs: "Any",
    ) -> "Callable[[Callable[..., Any]], Callable[..., Any]]":
        """Decorator to register a function as an MCP prompt.

        This dynamically wraps the function inside a Litestar route handler
        and registers it to the plugin. Additional keyword arguments are
        passed through to Litestar's `get()` route decorator.
        """
        route_kwargs = _collect_route_handler_kwargs(locals())
        cleaned_title = _clean_title(title)
        cleaned_arguments = (
            [copy.deepcopy(dict(item)) for item in arguments if isinstance(item, Mapping)]
            if arguments is not None
            else None
        )
        cleaned_icons = _clean_icons(icons)
        cleaned_meta = _clean_meta(meta)

        def decorator(fn: "Callable[..., Any]") -> "Callable[..., Any]":
            prompt_name = name or fn.__name__
            path = _standalone_internal_path(self.config.base_path, "prompts", prompt_name)
            opt_keys = self.config.opt_keys
            forced_opt: dict[str, Any] = {opt_keys.prompt: prompt_name}
            if cleaned_arguments is not None:
                forced_opt[opt_keys.prompt_arguments] = copy.deepcopy(cleaned_arguments)
            _populate_forced_descriptors(
                forced_opt,
                title_key=opt_keys.prompt_title,
                icons_key=opt_keys.prompt_icons,
                meta_key=opt_keys.prompt_meta,
                title=cleaned_title,
                icons=cleaned_icons,
                meta=cleaned_meta,
            )
            handler = get(
                path=path,
                **_build_standalone_route_kwargs(
                    route_kwargs,
                    fn=fn,
                    forced_opt=forced_opt,
                    default_opt={opt_keys.prompt_description: description or fn.__doc__ or ""},
                ),
            )(_json_response_wrapper(fn, path=path, dependencies=route_kwargs.get("dependencies")))
            self.plugin.register_dynamic_handler(handler)
            return fn

        return decorator

    def run(
        self,
        transport: "Literal['streamable-http', 'stdio']" = "streamable-http",
        **kwargs: "Any",
    ) -> "None":
        """Run the MCP server using the specified transport.

        Args:
            transport: The transport to use ("streamable-http" or "stdio").
            **kwargs: Arguments passed to the runner.
        """
        if transport == "streamable-http":
            self._run_streamable_http(**kwargs)
        elif transport == "stdio":
            self._run_stdio(**kwargs)
        else:
            msg = f"Unsupported transport: {transport}"  # type: ignore[unreachable]
            raise ValueError(msg)

    def _run_streamable_http(self, **kwargs: "Any") -> "None":
        """Run the Streamable HTTP server through Litestar's CLI."""
        args = ["run", *_convert_kwargs_to_flags(kwargs)]
        self._execute_cli(args)

    def _execute_cli(self, args: "list[str]") -> "None":
        """Execute the Litestar CLI programmatically."""
        from litestar.cli._utils import LitestarEnv
        from litestar.cli.main import litestar_group

        app_path = _resolve_litestar_app_env(self.app)
        if not app_path:
            msg = (
                "Could not resolve the Litestar application import path. "
                "Please expose the Litestar instance globally (e.g., 'app = mcp.app') "
                "or set the LITESTAR_APP environment variable."
            )
            raise RuntimeError(msg)

        os.environ["LITESTAR_APP"] = app_path

        env = LitestarEnv.from_env(app_path)
        litestar_group.main(args=args, obj=env)

    def _run_stdio(
        self,
        *,
        stdio_context: "MCPStdioContext | None" = None,
        **kwargs: "Any",
    ) -> "None":
        """Run the server over stdio through the in-process transport.

        Keyword arguments are forwarded to `litestar_mcp.mcp.stdio.run_stdio`.
        """
        run_stdio(self.app, stdio_context=stdio_context or MCPStdioContext(), **kwargs)
