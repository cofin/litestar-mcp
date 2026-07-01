import asyncio
import contextlib
import inspect
import logging
import os
import sys
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from litestar import Litestar, Response
from litestar.exceptions import PermissionDeniedException, SerializationException
from litestar.handlers import get, post
from litestar.openapi.spec import Operation
from litestar.serialization import decode_json, encode_json
from litestar.types import Empty, TypeDecodersSequence

from litestar_mcp.config import MCPConfig
from litestar_mcp.jsonrpc import (
    INTERNAL_ERROR,
    PARSE_ERROR,
    JSONRPCError,
    JSONRPCErrorException,
    error_response,
    parse_request,
)
from litestar_mcp.plugin import LitestarMCP
from litestar_mcp.routes import _build_cached_router
from litestar_mcp.services.handler import RequestContext

if TYPE_CHECKING:
    from collections.abc import Sequence

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


@dataclass(frozen=True, slots=True)
class MCPStdioContext:
    """Runtime identity context for standalone MCP stdio transports.

    Stdio has no HTTP header layer. Resolve credentials out-of-band in the
    host process, then pass the resulting identity values here so Litestar
    handlers and guards can read the usual ``scope["user"]`` /
    ``scope["auth"]`` / session / state fields.
    """

    client_id: "str" = "stdio"
    owner_id: "str | None" = None
    user: "Any" = None
    auth: "Any" = None
    session: "Mapping[str, Any] | None" = None
    state: "Mapping[str, Any] | None" = None


def _resolve_stdio_owner_id(context: "MCPStdioContext") -> "str":
    if context.owner_id is not None:
        return str(context.owner_id)
    if isinstance(context.auth, Mapping):
        auth_sub = context.auth.get("sub")
        if auth_sub is not None:
            return str(auth_sub)
    for attr in ("id", "sub"):
        value = getattr(context.user, attr, None)
        if value is not None:
            return str(value)
    return "stdio"


def _build_stdio_scope_overrides(context: "MCPStdioContext") -> "dict[str, Any]":
    return {
        "user": context.user,
        "auth": context.auth,
        "session": dict(context.session or {}),
        "state": dict(context.state or {}),
    }


def _require_internal_dispatch(connection: "Any", _route_handler: "Any") -> "None":
    """Reject direct HTTP access to standalone wrapper internal routes."""
    if not connection.scope.get(_INTERNAL_DISPATCH_SCOPE_KEY):
        msg = "Standalone MCP internal routes are not directly accessible"
        raise PermissionDeniedException(msg)


def _build_standalone_route_kwargs(
    handler_kwargs: "dict[str, Any]",
    *,
    forced_opt: "dict[str, Any]",
    default_opt: "dict[str, Any] | None" = None,
) -> "dict[str, Any]":
    """Merge user route kwargs with standalone MCP internal route metadata."""
    route_kwargs = dict(handler_kwargs)
    route_kwargs.pop("path", None)

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
            res = await fn(*args, **kwargs)
            if isinstance(res, Response):
                return res
            return Response(
                content=encode_json(res),
                media_type="application/json",
            )

        return async_wrapped

    @wraps(fn)
    def sync_wrapped(*args: "Any", **kwargs: "Any") -> "Any":
        res = fn(*args, **kwargs)
        if isinstance(res, Response):
            return res
        return Response(
            content=encode_json(res),
            media_type="application/json",
        )

    return sync_wrapped


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
        dto: "type[AbstractDTO[Any]] | None | EmptyType" = Empty,
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
        return_dto: "type[AbstractDTO[Any]] | None | EmptyType" = Empty,
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
            path = f"/mcp/internal/tools/{tool_name}"
            opt_keys = self.config.opt_keys
            handler = post(
                path=path,
                **_build_standalone_route_kwargs(
                    route_kwargs,
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
        after_request: "AfterRequestHookHandler | None" = None,
        after_response: "AfterResponseHookHandler | None" = None,
        background: "BackgroundTask | BackgroundTasks | None" = None,
        before_request: "BeforeRequestHookHandler | None" = None,
        cache: "bool | int | type[CACHE_FOREVER]" = False,
        cache_control: "CacheControlHeader | None" = None,
        cache_key_builder: "CacheKeyBuilder | None" = None,
        dependencies: "Dependencies | None" = None,
        dto: "type[AbstractDTO[Any]] | None | EmptyType" = Empty,
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
        return_dto: "type[AbstractDTO[Any]] | None | EmptyType" = Empty,
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

            path = f"/mcp/internal/resources/{clean_path}"
            opt_keys = self.config.opt_keys
            handler = get(
                path=path,
                **_build_standalone_route_kwargs(
                    route_kwargs,
                    forced_opt={
                        opt_keys.resource: resource_name,
                        opt_keys.resource_template: uri,
                    },
                    default_opt={opt_keys.resource_description: description or fn.__doc__ or ""},
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
        dto: "type[AbstractDTO[Any]] | None | EmptyType" = Empty,
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
        return_dto: "type[AbstractDTO[Any]] | None | EmptyType" = Empty,
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
            path = f"/mcp/internal/prompts/{prompt_name}"
            opt_keys = self.config.opt_keys
            handler = get(
                path=path,
                **_build_standalone_route_kwargs(
                    route_kwargs,
                    forced_opt={opt_keys.prompt: prompt_name},
                    default_opt={opt_keys.prompt_description: description or fn.__doc__ or ""},
                ),
            )(_json_response_wrapper(fn))
            self.plugin.register_dynamic_handler(handler)
            return fn

        return decorator

    def run(
        self,
        transport: "Literal['sse', 'stdio']" = "sse",
        **kwargs: "Any",
    ) -> "None":
        """Run the MCP server using the specified transport.

        Args:
            transport: The transport to use ("sse" or "stdio").
            **kwargs: Arguments passed to the runner.
        """
        if transport == "sse":
            self._run_sse(**kwargs)
        elif transport == "stdio":
            self._run_stdio(**kwargs)
        else:
            msg = f"Unsupported transport: {transport}"  # type: ignore[unreachable]
            raise ValueError(msg)

    def _run_sse(self, **kwargs: "Any") -> "None":
        """Run the server using Server-Sent Events (SSE) transport."""
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
        **_kwargs: "Any",
    ) -> "None":
        """Run the server using Stdio transport."""
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(self._async_run_stdio(stdio_context=stdio_context))

    async def _async_run_stdio(self, *, stdio_context: "MCPStdioContext | None" = None) -> "None":
        """Run the server asynchronously using manual ASGI lifespan driver.

        This sets up queues, coordinates lifespan events, starts the app task,
        triggers startup, runs the stdio loop, and triggers shutdown on exit.
        """
        logger = logging.getLogger(__name__)

        receive_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        startup_complete = asyncio.Event()
        shutdown_complete = asyncio.Event()
        startup_error: str | None = None

        async def receive() -> "dict[str, Any]":
            return await receive_queue.get()

        async def send(message: "dict[str, Any]") -> "None":
            nonlocal startup_error
            mtype = message.get("type")
            if mtype == "lifespan.startup.complete":
                startup_complete.set()
            elif mtype == "lifespan.startup.failed":
                startup_error = message.get("message", "Unknown startup error")
                startup_complete.set()
            elif mtype == "lifespan.shutdown.complete":
                shutdown_complete.set()

        scope = {
            "type": "lifespan",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
        }

        async def run_app() -> "None":
            try:
                await self.app(scope, receive, send)  # type: ignore[arg-type]
            except Exception as e:
                nonlocal startup_error
                if not startup_complete.is_set():
                    startup_error = str(e)
                    startup_complete.set()
                logger.exception("Error in background ASGI application task")
                raise

        app_task = asyncio.create_task(run_app())

        await receive_queue.put({"type": "lifespan.startup"})
        await startup_complete.wait()

        if startup_error:
            app_task.cancel()
            msg = f"Application startup failed: {startup_error}"
            raise RuntimeError(msg)

        try:
            await self._stdio_loop(stdio_context=stdio_context)
        finally:
            await receive_queue.put({"type": "lifespan.shutdown"})
            try:
                await asyncio.wait_for(shutdown_complete.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Lifespan shutdown timed out after 5 seconds")

            app_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await app_task

    async def _stdio_loop(self, *, stdio_context: "MCPStdioContext | None" = None) -> "None":
        """Run the stdin/stdout read/write loop."""
        logger = logging.getLogger(__name__)
        loop = asyncio.get_running_loop()

        # Bind pipes
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin.buffer)

        w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout.buffer)
        writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)

        # Build router using plugin discovered definitions
        plugin = self.plugin
        router = _build_cached_router(
            app=self.app,
            config=plugin.config,
            discovered_tools=plugin.discovered_tools,
            discovered_resources=plugin.discovered_resources,
            discovered_prompts=plugin.discovered_prompts,
            registry=plugin.registry,
            task_store=plugin.task_store,
        )

        resolved_stdio_context = stdio_context or MCPStdioContext()
        request_context = RequestContext(
            client_id=resolved_stdio_context.client_id,
            owner_id=_resolve_stdio_owner_id(resolved_stdio_context),
            request=None,
            scope_overrides=_build_stdio_scope_overrides(resolved_stdio_context),
        )

        while True:
            line_bytes = await reader.readline()
            if not line_bytes:
                break

            line = line_bytes.decode("utf-8").strip()
            if not line:
                continue

            try:
                raw = decode_json(line_bytes)
            except SerializationException as exc:
                resp = error_response(
                    None,
                    JSONRPCError(code=PARSE_ERROR, message=f"Parse error: {exc}"),
                )
                writer.write(encode_json(resp) + b"\n")
                await writer.drain()
                continue

            try:
                rpc_request = parse_request(raw)
            except JSONRPCErrorException as exc:
                resp = error_response(
                    raw.get("id") if isinstance(raw, dict) else None,
                    exc.error,
                )
                writer.write(encode_json(resp) + b"\n")
                await writer.drain()
                continue

            try:
                result = await router.dispatch(rpc_request, request_context)
            except Exception as exc:
                logger.exception("Unexpected error in stdio loop processing line")
                resp = error_response(
                    rpc_request.id,
                    JSONRPCError(code=INTERNAL_ERROR, message=f"Internal error: {exc}"),
                )
                writer.write(encode_json(resp) + b"\n")
                await writer.drain()
                continue

            if result is not None:
                try:
                    writer.write(encode_json(result) + b"\n")
                    await writer.drain()
                except Exception:
                    logger.exception("Failed to write stdio response")
