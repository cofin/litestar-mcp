"""CLI commands for MCP plugin integration."""

import asyncio
import contextlib
import inspect
import os
import shlex
import subprocess
import sys
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Literal, cast

import httpx
from anyio.abc import ByteSendStream
from anyio.to_thread import run_sync as run_sync_in_worker_thread
from click.exceptions import Exit as ClickExit
from litestar.cli._utils import LitestarGroup
from litestar.exceptions import SerializationException
from litestar.serialization import decode_json
from rich.console import Console
from rich.json import JSON

from litestar_mcp import bridge as bridge_transport
from litestar_mcp.auth.backend import BEARER_TOKEN_PREFIX, DEFAULT_AUTH_HEADER_NAME
from litestar_mcp.bridge import TokenProvider
from litestar_mcp.exceptions import BridgeConnectionError, MissingDependencyError
from litestar_mcp.executor import NotCallableInCLIContextError, execute_tool
from litestar_mcp.utils import get_handler_function, render_description
from litestar_mcp.utils.handler_signature import iter_dependency_input_parameters

try:
    import rich_click as click
except ImportError:  # pragma: no cover
    import click  # type: ignore[no-redef]

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.cli._utils import LitestarEnv

    from litestar_mcp.plugin import LitestarMCP


def get_mcp_plugin(app: "Litestar") -> "LitestarMCP":
    """Retrieve the MCP plugin from the Litestar application's plugins.

    This function imports ``LitestarMCP`` locally to break circular dependency
    with ``litestar_mcp.plugin`` during CLI/command setup.

    Args:
        app: The Litestar application

    Returns:
        The MCP plugin

    Raises:
        RuntimeError: If the MCP plugin is not found
    """
    from contextlib import suppress

    from litestar_mcp.plugin import LitestarMCP

    with suppress(KeyError):
        return app.plugins.get(LitestarMCP)
    msg = "Failed to initialize MCP commands. The required LitestarMCP plugin is missing."
    raise RuntimeError(msg)  # pragma: no cover


class ToolExecutor(click.MultiCommand):  # type: ignore[valid-type,misc,unused-ignore]  # pragma: no cover
    """A dynamic click MultiCommand to run discovered MCP tools."""

    def __init__(self, **attrs: "Any") -> "None":  # pragma: no cover
        """Initialize the tool executor."""
        super().__init__(**attrs)
        self._console = Console()

    def list_commands(self, ctx: "click.Context") -> "list[str]":  # pragma: no cover
        """List the names of all discovered tools and resources."""
        try:
            plugin = _get_ctx_plugin(ctx)
            # Include both tools and resources
            all_commands = set(plugin.discovered_tools.keys()) | set(plugin.discovered_resources.keys())
            return sorted(all_commands)
        except RuntimeError:
            return []

    def get_command(self, ctx: "click.Context", cmd_name: "str") -> "click.Command | None":  # pragma: no cover
        """Create a click.Command for a specific tool or resource by its name."""
        try:
            plugin = _get_ctx_plugin(ctx)
        except RuntimeError:
            return None

        # Check both tools and resources
        handler = plugin.discovered_tools.get(cmd_name) or plugin.discovered_resources.get(cmd_name)
        if not handler:
            return None
        kind: Literal["tool", "resource"] = "tool" if cmd_name in plugin.discovered_tools else "resource"
        fn = get_handler_function(handler)
        sig = inspect.signature(fn)

        # Dependencies that are handled by the executor, not the CLI
        di_params: set[str] = set()
        with contextlib.suppress(Exception):
            di_params = set(handler.resolve_dependencies().keys())

        # Create CLI options from function signature + provider params.
        params: list[click.Option] = []
        seen: set[str] = set()
        for param in sig.parameters.values():
            if param.name in di_params:
                continue
            _append_cli_option(params, seen, param.name, param)
        for name, param in iter_dependency_input_parameters(handler):
            _append_cli_option(params, seen, name, param)

        @click.pass_context
        def callback(ctx: "click.Context", /, **kwargs: "Any") -> "None":
            """The actual command callback that executes the tool."""
            app = _get_ctx_app(ctx)

            # Parse JSON strings
            parsed_kwargs: dict[str, Any] = _parse_cli_kwargs(kwargs)

            try:
                result = asyncio.run(execute_tool(handler, app, parsed_kwargs))
                _display_result(self._console, result)
            except (NotCallableInCLIContextError, ValueError) as e:
                self._console.print(f"[bold red]Error executing tool '{cmd_name}':[/bold red]")
                self._console.print(str(e))
                ctx.exit(1)
            except Exception as e:  # noqa: BLE001
                self._console.print(f"[bold red]Unexpected error executing tool '{cmd_name}':[/bold red]")
                self._console.print(str(e))
                ctx.exit(1)

        # Use the description helper in plain (unstructured) mode so CLI
        # output stays terminal-friendly — no ``##`` markdown headers.
        fn_doc = render_description(
            handler,
            fn,
            kind=kind,
            fallback_name=cmd_name,
            structured=False,
            opt_keys=plugin.config.opt_keys,
        )

        return click.Command(
            cmd_name,
            params=cast("list[click.Parameter]", params),
            callback=callback,
            help=fn_doc,
            short_help=f"Execute the '{cmd_name}' tool.",
        )


@click.group(cls=LitestarGroup, name="mcp")
def mcp_group(ctx: "click.Context | None" = None, app: "Litestar | None" = None) -> "None":
    """Manage MCP tools and resources."""
    if ctx is None:
        ctx = cast("click.Context", click.get_current_context())
    app = app or _get_ctx_app(ctx)
    ctx.meta["mcp_plugin"] = get_mcp_plugin(app)


@mcp_group.command(name="list-tools")  # type: ignore[untyped-decorator]
def list_tools(app: "Litestar") -> "None":
    """List all available MCP tools.

    Uses plain description so terminal output never shows ``##`` headers.
    """
    plugin = get_mcp_plugin(app)  # pragma: no cover
    console = Console()  # pragma: no cover

    if not plugin.discovered_tools:  # pragma: no cover
        console.print("[yellow]No MCP tools discovered.[/yellow]")  # pragma: no cover
        return  # pragma: no cover

    console.print(f"[bold green]Discovered {len(plugin.discovered_tools)} tools:[/bold green]")  # pragma: no cover
    for name in sorted(plugin.discovered_tools.keys()):  # pragma: no cover
        handler = plugin.discovered_tools[name]  # pragma: no cover
        fn = get_handler_function(handler)  # pragma: no cover
        # pragma: no cover
        description = render_description(
            handler,
            fn,
            kind="tool",
            fallback_name=name,
            structured=False,
            opt_keys=plugin.config.opt_keys,
        )  # pragma: no cover
        first_line = description.split("\n")[0].strip()  # pragma: no cover
        console.print(f"- [bold]{name}[/bold]: {first_line}")  # pragma: no cover


@mcp_group.command(name="list-resources")  # type: ignore[untyped-decorator]
def list_resources(app: "Litestar") -> "None":
    """List all available MCP resources.

    Uses plain description so terminal output never shows ``##`` headers.
    """
    plugin = get_mcp_plugin(app)  # pragma: no cover
    console = Console()  # pragma: no cover

    if not plugin.discovered_resources:  # pragma: no cover
        console.print("[yellow]No MCP resources discovered.[/yellow]")  # pragma: no cover
        return  # pragma: no cover

    console.print(
        f"[bold green]Discovered {len(plugin.discovered_resources)} resources:[/bold green]"
    )  # pragma: no cover
    for name in sorted(plugin.discovered_resources.keys()):  # pragma: no cover
        handler = plugin.discovered_resources[name]  # pragma: no cover
        fn = get_handler_function(handler)  # pragma: no cover
        # pragma: no cover
        description = render_description(  # pragma: no cover
            handler,
            fn,
            kind="resource",
            fallback_name=name,
            structured=False,
            opt_keys=plugin.config.opt_keys,
        )
        first_line = description.split("\n")[0].strip()  # pragma: no cover
        console.print(f"- [bold]{name}[/bold]: {first_line}")  # pragma: no cover


mcp_group.add_command(ToolExecutor(name="run", help="Run a discovered MCP tool by name."))  # pragma: no cover


@mcp_group.command(name="bridge")  # type: ignore[untyped-decorator]
@click.option(
    "--endpoint",
    help="Full MCP Streamable HTTP endpoint URL. Overrides --base-url and the app's MCPConfig.base_path.",
)
@click.option(
    "--base-url",
    help="Base URL of the already-running Litestar app. Defaults to LITESTAR_HOST/LITESTAR_PORT or http://127.0.0.1:8000.",
)
@click.option("--header", "header_values", multiple=True, help="Static HTTP header as 'Name: value'.")
@click.option("--bearer-env", help="Environment variable containing the bearer token.")
@click.option("--bearer-cmd", help="Command whose stdout returns the bearer token.")
@click.option(
    "--header-name", default=DEFAULT_AUTH_HEADER_NAME, show_default=True, help="Header used for bearer tokens."
)
@click.option(
    "--token-prefix", default=BEARER_TOKEN_PREFIX, show_default=True, help="Prefix prepended to bearer token values."
)
@click.option(
    "--timeout", default=30.0, show_default=True, type=float, help="HTTP connect/write/pool timeout in seconds."
)
@click.option(
    "--sse-read-timeout",
    default=300.0,
    show_default=True,
    type=float,
    help="SSE stream read timeout in seconds. Use 0 for no quiet-period timeout.",
)
@click.option(
    "--max-message-size",
    default=bridge_transport.DEFAULT_MAX_STDIN_MESSAGE_SIZE,
    show_default=True,
    type=int,
    help="Maximum bytes for one stdin JSON-RPC message. Use -1 to disable the limit.",
)
@click.option("--discover", is_flag=True, help="Resolve the endpoint from /.well-known/mcp-server.json.")
def _bridge_command(
    ctx: "click.Context",
    endpoint: "str | None",
    base_url: "str | None",
    header_values: "tuple[str, ...]",
    bearer_env: "str | None",
    bearer_cmd: "str | None",
    header_name: "str",
    token_prefix: "str",
    timeout: "float",
    sse_read_timeout: "float",
    max_message_size: "int",
    discover: "bool",
) -> "None":
    """Proxy local stdio JSON-RPC to this Litestar app's MCP endpoint."""
    plugin = _get_ctx_plugin(ctx)
    resolved_endpoint = _resolve_bridge_endpoint(
        plugin=plugin,
        env=_get_ctx_env(ctx),
        endpoint=endpoint,
        base_url=base_url,
    )
    _run_bridge_from_options(
        endpoint=resolved_endpoint,
        header_values=header_values,
        bearer_env=bearer_env,
        bearer_cmd=bearer_cmd,
        header_name=header_name,
        token_prefix=token_prefix,
        timeout=timeout,
        sse_read_timeout=sse_read_timeout,
        max_message_size=max_message_size,
        discover=discover,
    )


class _BufferedByteSendStream(ByteSendStream):
    def __init__(self, buffer: "Any") -> "None":
        self._buffer = buffer

    async def send(self, item: "bytes") -> "None":
        def write() -> None:
            self._buffer.write(item)
            self._buffer.flush()

        await run_sync_in_worker_thread(write, abandon_on_cancel=True)

    async def aclose(self) -> "None":
        return None


def _get_ctx_app(ctx: "click.Context") -> "Litestar":
    obj = ctx.obj
    if isinstance(obj, dict):
        return cast("Litestar", obj["app"])
    if callable(obj):
        obj = obj()
        ctx.obj = obj
    env = cast("LitestarEnv", obj)
    return env.app


def _get_ctx_plugin(ctx: "click.Context") -> "LitestarMCP":
    plugin = ctx.meta.get("mcp_plugin")
    if plugin is not None:
        return cast("LitestarMCP", plugin)
    obj = ctx.obj
    if isinstance(obj, dict) and "plugin" in obj:
        return cast("LitestarMCP", obj["plugin"])
    return get_mcp_plugin(_get_ctx_app(ctx))


def _get_ctx_env(ctx: "click.Context") -> "LitestarEnv | None":
    obj = ctx.obj
    if isinstance(obj, dict):
        return cast("LitestarEnv | None", obj.get("env"))
    if callable(obj):
        obj = obj()
        ctx.obj = obj
    return cast("LitestarEnv", obj)


def _append_cli_option(
    params: "list[click.Option]",
    seen: "set[str]",
    name: "str",
    param: "inspect.Parameter",
) -> "None":
    """Append a ``click.Option`` derived from a handler/provider parameter."""
    if name in seen:
        return
    seen.add(name)
    annotation = param.annotation
    is_json = (
        annotation in {dict, list, set}
        or hasattr(annotation, "__origin__")
        or (hasattr(annotation, "__module__") and annotation.__module__ != "builtins")
    )
    help_text = f"Type: {getattr(annotation, '__name__', str(annotation))}"
    if is_json:
        help_text += ". Pass as JSON string if complex type."
    option_kwargs: dict[str, Any] = {
        "help": help_text,
        "required": param.default is inspect.Parameter.empty,
    }
    if annotation is bool and param.default is not inspect.Parameter.empty:
        option_kwargs["is_flag"] = True
        option_kwargs["default"] = param.default
        option_kwargs.pop("required", None)
    params.append(click.Option([f"--{name}"], **option_kwargs))  # pyright: ignore


def _parse_cli_kwargs(kwargs: "dict[str, Any]") -> "dict[str, Any]":  # pragma: no cover
    parsed_kwargs: dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            parsed_kwargs[key] = value
            continue

        try:
            if isinstance(value, str) and value.startswith(("{", "[")):
                parsed_kwargs[key] = decode_json(value)
            else:
                parsed_kwargs[key] = value
        except (SerializationException, TypeError):
            parsed_kwargs[key] = value
    return parsed_kwargs


def _display_result(console: "Console", result: "Any") -> "None":  # pragma: no cover
    if isinstance(result, str):
        console.print(result)
    else:
        console.print(JSON.from_data(result))


def _default_bridge_base_url(env: "LitestarEnv | None") -> "str":
    host = getattr(env, "host", None) or "127.0.0.1"
    port = getattr(env, "port", None) or 8000
    if "://" in host:
        return host.rstrip("/")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    return f"http://{host}:{port}"


def _join_base_url_and_path(base_url: "str", path: "str") -> "str":
    base = base_url.rstrip("/")
    route_path = path.strip("/")
    return f"{base}/{route_path}" if route_path else base


def _resolve_bridge_endpoint(
    *,
    plugin: "LitestarMCP",
    env: "LitestarEnv | None",
    endpoint: "str | None",
    base_url: "str | None",
) -> "str":
    if endpoint:
        return endpoint
    return _join_base_url_and_path(base_url or _default_bridge_base_url(env), plugin.config.base_path)


def _parse_header(value: str) -> tuple[str, str]:
    if ":" not in value:
        msg = "Headers must use 'Name: value' format."
        raise click.BadParameter(msg)
    name, header_value = value.split(":", 1)
    name = name.strip()
    if not name:
        msg = "Header name cannot be empty."
        raise click.BadParameter(msg)
    return name, header_value.strip()


def _headers_from_options(values: tuple[str, ...]) -> dict[str, str]:
    return dict(_parse_header(value) for value in values)


def _token_provider_from_env(variable_name: str) -> Callable[[], str]:
    def provide_token() -> str:
        value = os.getenv(variable_name)
        if value is None:
            msg = f"Environment variable {variable_name!r} is not set."
            raise click.ClickException(msg)
        return value

    return provide_token


def _token_provider_from_cmd(command: str) -> Callable[[], str]:
    args = shlex.split(command)

    def provide_token() -> str:
        completed = subprocess.run(args, check=True, capture_output=True, text=True)  # noqa: S603
        return completed.stdout.strip()

    return provide_token


def _headers_with_token(
    headers: Mapping[str, str],
    token_provider: TokenProvider | None,
    *,
    header_name: str,
    token_prefix: str,
) -> dict[str, str]:
    request_headers = dict(headers)
    if token_provider is None:
        return request_headers
    token = token_provider()
    if inspect.isawaitable(token):
        msg = "--discover requires a synchronous bearer token provider."
        raise click.ClickException(msg)
    request_headers[header_name] = f"{token_prefix}{token}"
    return request_headers


def _discover_endpoint(
    origin: str,
    *,
    headers: Mapping[str, str],
    token_provider: TokenProvider | None,
    header_name: str,
    token_prefix: str,
    timeout: float,
) -> str:
    url = httpx.URL(origin)
    root = f"{url.scheme}://{url.netloc.decode()}"
    manifest_url = f"{root}/.well-known/mcp-server.json"
    try:
        response = httpx.get(
            manifest_url,
            headers=_headers_with_token(
                headers,
                token_provider,
                header_name=header_name,
                token_prefix=token_prefix,
            ),
            timeout=timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
    except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
        raise click.ClickException(str(BridgeConnectionError(manifest_url))) from exc
    except httpx.HTTPError as exc:
        msg = f"Failed to fetch MCP server manifest at {manifest_url}: {exc}"
        raise click.ClickException(msg) from exc

    try:
        manifest = decode_json(response.content)
    except Exception as exc:
        msg = f"Failed to decode MCP server manifest at {manifest_url}: {exc}"
        raise click.ClickException(msg) from exc

    endpoint = manifest.get("endpoints", {}).get("mcp") if isinstance(manifest, dict) else None
    if not isinstance(endpoint, str) or not endpoint:
        msg = f"{manifest_url} did not include endpoints.mcp"
        raise click.ClickException(msg)
    return endpoint


def _run_bridge_from_options(
    *,
    endpoint: str,
    header_values: tuple[str, ...],
    bearer_env: str | None,
    bearer_cmd: str | None,
    header_name: str,
    token_prefix: str,
    timeout: float,
    sse_read_timeout: float,
    max_message_size: int,
    discover: bool,
) -> None:
    if bearer_env and bearer_cmd:
        msg = "--bearer-env and --bearer-cmd are mutually exclusive."
        raise click.UsageError(msg)

    headers = _headers_from_options(header_values)
    token_provider: TokenProvider | None = None
    if bearer_env:
        token_provider = _token_provider_from_env(bearer_env)
    elif bearer_cmd:
        token_provider = _token_provider_from_cmd(bearer_cmd)

    resolved_endpoint = (
        _discover_endpoint(
            endpoint,
            headers=headers,
            token_provider=token_provider,
            header_name=header_name,
            token_prefix=token_prefix,
            timeout=timeout,
        )
        if discover
        else endpoint
    )
    stdout = _BufferedByteSendStream(sys.stdout.buffer)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            exit_code = bridge_transport.run_bridge(
                endpoint=resolved_endpoint,
                headers=headers,
                token_provider=token_provider,
                header_name=header_name,
                token_prefix=token_prefix,
                timeout=timeout,
                sse_read_timeout=sse_read_timeout,
                max_message_size=max_message_size,
                stdout=stdout,
                stderr=sys.stderr,
            )
    except MissingDependencyError as exc:
        raise click.ClickException(str(exc)) from exc
    raise ClickExit(exit_code)
