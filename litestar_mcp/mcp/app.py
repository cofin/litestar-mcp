import inspect
import os
import sys
import urllib.parse
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from litestar import Litestar, Response
from litestar.exceptions import PermissionDeniedException
from litestar.handlers import get, post
from litestar.openapi.spec import Operation
from litestar.serialization import encode_json
from litestar.types import Empty, TypeDecodersSequence

from litestar_mcp.mcp.config import MCPConfig
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.stdio import MCPStdioContext, run_stdio

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

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


def _standalone_internal_path(base_path: "str", *parts: "str") -> "str":
    return "/".join((base_path.rstrip("/"), "internal", *parts))


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


def _json_response_wrapper(fn: "Callable[..., Any]") -> "Callable[..., Any]":
    """Wrap a handler function to always return a JSON Response.

    This ensures that primitive return types like strings are serialized
    as JSON (e.g., '"hello"') instead of plain text ('hello'), allowing
    the MCP execution layer to parse them correctly.
    """
    from functools import wraps

    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapped(*args: "Any", **kwargs: "Any") -> "Any":
            return _to_json_response(await fn(*args, **kwargs))

        return async_wrapped

    @wraps(fn)
    def sync_wrapped(*args: "Any", **kwargs: "Any") -> "Any":
        return _to_json_response(fn(*args, **kwargs))

    return sync_wrapped


def _to_json_response(res: "Any") -> "Response[Any]":
    """Return ``res`` as a JSON response that still honours the handler's ``type_encoders``.

    Strings, bytes and ``None`` are pre-encoded because Litestar would otherwise send
    them verbatim; every other value is left for Litestar to serialize with the
    resolved ``type_encoders`` of the route.
    """
    if isinstance(res, Response):
        return res
    if res is None or isinstance(res, (str, bytes, bytearray)):
        return Response(content=encode_json(res), media_type="application/json")
    return Response(content=res, media_type="application/json")


class MCP:
    """A class that simplifies Model Context Protocol application setup.

    This provides decorators and programmatic server execution for standalone
    applications.
    """

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
        **kwargs: "Any",
    ) -> "Callable[[Callable[..., Any]], Callable[..., Any]]":
        """Decorator to register a function as an MCP tool.

        This dynamically wraps the function inside a Litestar route handler
        and registers it to the plugin. Additional keyword arguments are
        passed through to Litestar's ``post()`` route decorator.
        """
        route_kwargs = _collect_route_handler_kwargs(locals())

        def decorator(fn: "Callable[..., Any]") -> "Callable[..., Any]":
            tool_name = name or fn.__name__
            path = _standalone_internal_path(self.config.base_path, "tools", tool_name)
            opt_keys = self.config.opt_keys
            handler = post(
                path=path,
                **_build_standalone_route_kwargs(
                    route_kwargs,
                    fn=fn,
                    forced_opt={opt_keys.tool: tool_name},
                    default_opt={opt_keys.description: description or fn.__doc__ or ""},
                ),
            )(_json_response_wrapper(fn))
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
        **kwargs: "Any",
    ) -> "Callable[[Callable[..., Any]], Callable[..., Any]]":
        """Decorator to register a function as an MCP resource.

        This dynamically wraps the function inside a Litestar route handler
        and registers it to the plugin. Additional keyword arguments are
        passed through to Litestar's ``get()`` route decorator.
        """
        route_kwargs = _collect_route_handler_kwargs(locals())

        def decorator(fn: "Callable[..., Any]") -> "Callable[..., Any]":
            resource_name = name or fn.__name__
            parsed = urllib.parse.urlparse(uri)
            clean_path = parsed.netloc + parsed.path if parsed.scheme else uri.lstrip("/")

            path = _standalone_internal_path(self.config.base_path, "resources", clean_path)
            opt_keys = self.config.opt_keys
            handler = get(
                path=path,
                **_build_standalone_route_kwargs(
                    route_kwargs,
                    fn=fn,
                    forced_opt={
                        opt_keys.resource: resource_name,
                        opt_keys.resource_template: uri,
                    },
                    default_opt={
                        opt_keys.resource_description: description or fn.__doc__ or "",
                        opt_keys.resource_mime_type: mime_type,
                    },
                ),
            )(_json_response_wrapper(fn))
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
        **kwargs: "Any",
    ) -> "Callable[[Callable[..., Any]], Callable[..., Any]]":
        """Decorator to register a function as an MCP prompt.

        This dynamically wraps the function inside a Litestar route handler
        and registers it to the plugin. Additional keyword arguments are
        passed through to Litestar's ``get()`` route decorator.
        """
        route_kwargs = _collect_route_handler_kwargs(locals())

        def decorator(fn: "Callable[..., Any]") -> "Callable[..., Any]":
            prompt_name = name or fn.__name__
            path = _standalone_internal_path(self.config.base_path, "prompts", prompt_name)
            opt_keys = self.config.opt_keys
            handler = get(
                path=path,
                **_build_standalone_route_kwargs(
                    route_kwargs,
                    fn=fn,
                    forced_opt={opt_keys.prompt: prompt_name},
                    default_opt={opt_keys.prompt_description: description or fn.__doc__ or ""},
                ),
            )(_json_response_wrapper(fn))
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

        Keyword arguments are forwarded to :func:`litestar_mcp.mcp.stdio.run_stdio`.
        """
        run_stdio(self.app, stdio_context=stdio_context or MCPStdioContext(), **kwargs)
