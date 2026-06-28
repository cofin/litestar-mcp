from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import sys
import urllib.parse
from collections.abc import Callable  # noqa: TC003
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from litestar import Litestar, Response
from litestar.exceptions import SerializationException
from litestar.handlers import get, post
from litestar.serialization import decode_json, encode_json

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
    from litestar.types import ControllerRouterHandler


def _convert_kwargs_to_flags(kwargs: dict[str, Any]) -> list[str]:
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


def _resolve_litestar_app_env(app: Litestar) -> str | None:
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


def _json_response_wrapper(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Wrap a handler function to always return a JSON Response.

    This ensures that primitive return types like strings are serialized
    as JSON (e.g., '"hello"') instead of plain text ('hello'), allowing
    the MCP execution layer to parse them correctly.
    """
    from functools import wraps

    if inspect.iscoroutinefunction(fn):

        @wraps(fn)
        async def async_wrapped(*args: Any, **kwargs: Any) -> Any:
            res = await fn(*args, **kwargs)
            if isinstance(res, Response):
                return res
            return Response(
                content=encode_json(res),
                media_type="application/json",
            )

        return async_wrapped

    @wraps(fn)
    def sync_wrapped(*args: Any, **kwargs: Any) -> Any:
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
        name: str,
        *,
        instructions: str | None = None,
        config: MCPConfig | None = None,
        plugins: list[Any] | None = None,
        route_handlers: list[ControllerRouterHandler] | None = None,
        **kwargs: Any,
    ) -> None:
        self.config = config or MCPConfig()
        self.config.name = name
        if instructions is not None:
            self.config.instructions = instructions

        resolved_plugins = plugins or []
        found_plugin: LitestarMCP | None = None
        for p in resolved_plugins:
            if isinstance(p, LitestarMCP):
                found_plugin = p
                break

        if found_plugin is None:
            found_plugin = LitestarMCP(config=self.config)
            resolved_plugins.append(found_plugin)

        self.plugin: LitestarMCP = found_plugin

        self._route_handlers = route_handlers or []
        self._plugins = resolved_plugins
        self._kwargs = kwargs
        self._app: Litestar | None = None

    @property
    def app(self) -> Litestar:
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
        name: str | None = None,
        *,
        description: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to register a function as an MCP tool.

        This dynamically wraps the function inside a Litestar route handler
        and registers it to the plugin.
        """

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or fn.__name__
            path = f"/mcp/internal/tools/{tool_name}"
            handler = post(
                path=path,
                mcp_tool=tool_name,
                mcp_description=description or fn.__doc__ or "",
            )(_json_response_wrapper(fn))
            self.plugin.register_dynamic_handler(handler)
            return fn

        return decorator

    def resource(
        self,
        uri: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to register a function as an MCP resource.

        This dynamically wraps the function inside a Litestar route handler
        and registers it to the plugin.
        """

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            resource_name = name or fn.__name__
            parsed = urllib.parse.urlparse(uri)
            clean_path = parsed.netloc + parsed.path if parsed.scheme else uri.lstrip("/")

            path = f"/mcp/internal/resources/{clean_path}"
            handler = get(
                path=path,
                mcp_resource=resource_name,
                mcp_resource_template=uri,
                mcp_resource_description=description or fn.__doc__ or "",
            )(_json_response_wrapper(fn))
            self.plugin.register_dynamic_handler(handler)
            return fn

        return decorator

    def prompt(
        self,
        name: str | None = None,
        *,
        description: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to register a function as an MCP prompt.

        This dynamically wraps the function inside a Litestar route handler
        and registers it to the plugin.
        """

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            prompt_name = name or fn.__name__
            path = f"/mcp/internal/prompts/{prompt_name}"
            handler = get(
                path=path,
                mcp_prompt=prompt_name,
                mcp_prompt_description=description or fn.__doc__ or "",
            )(_json_response_wrapper(fn))
            self.plugin.register_dynamic_handler(handler)
            return fn

        return decorator

    def run(
        self,
        transport: Literal["sse", "stdio"] = "sse",
        **kwargs: Any,
    ) -> None:
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

    def _run_sse(self, **kwargs: Any) -> None:
        """Run the server using Server-Sent Events (SSE) transport."""
        args = ["run", *_convert_kwargs_to_flags(kwargs)]
        self._execute_cli(args)

    def _execute_cli(self, args: list[str]) -> None:
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

    def _run_stdio(self, **kwargs: Any) -> None:
        """Run the server using Stdio transport."""
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(self._async_run_stdio())

    async def _async_run_stdio(self) -> None:
        """Run the server asynchronously using manual ASGI lifespan driver.

        This sets up queues, coordinates lifespan events, starts the app task,
        triggers startup, runs the stdio loop, and triggers shutdown on exit.
        """
        logger = logging.getLogger(__name__)

        receive_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        startup_complete = asyncio.Event()
        shutdown_complete = asyncio.Event()
        startup_error: str | None = None

        async def receive() -> dict[str, Any]:
            return await receive_queue.get()

        async def send(message: dict[str, Any]) -> None:
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

        async def run_app() -> None:
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
            await self._stdio_loop()
        finally:
            await receive_queue.put({"type": "lifespan.shutdown"})
            try:
                await asyncio.wait_for(shutdown_complete.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning("Lifespan shutdown timed out after 5 seconds")

            app_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await app_task

    async def _stdio_loop(self) -> None:
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

        request_context = RequestContext(client_id="stdio", owner_id="stdio", request=None)

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
