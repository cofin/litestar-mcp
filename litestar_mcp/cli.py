"""CLI commands for MCP plugin integration."""

import asyncio
import contextlib
import inspect
import json
from typing import TYPE_CHECKING, Any, Optional

import rich_click as click
from litestar.cli._utils import LitestarGroup
from rich.console import Console
from rich.json import JSON

from litestar_mcp.executor import execute_tool
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from litestar import Litestar

    from litestar_mcp.plugin import LitestarMCP


def get_mcp_plugin(app: "Litestar") -> "LitestarMCP":
    """Retrieve the MCP plugin from the Litestar application's plugins.

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

    def __init__(self, **attrs: Any) -> None:  # pragma: no cover
        """Initialize the tool executor."""
        super().__init__(**attrs)  # pyright: ignore
        self._console = Console()

    def list_commands(self, ctx: click.Context) -> list[str]:  # pragma: no cover
        """List the names of all discovered tools and resources."""
        app: Litestar = ctx.obj.app
        try:
            plugin = get_mcp_plugin(app)
            all_commands = set(plugin.discovered_tools.keys()) | set(plugin.discovered_resources.keys())
            return sorted(all_commands)
        except RuntimeError:
            return []

    def get_command(self, ctx: click.Context, cmd_name: str) -> Optional[click.Command]:  # pragma: no cover
        """Create a click.Command for a specific tool or resource by its name."""
        app: Litestar = ctx.obj.app
        try:
            plugin = get_mcp_plugin(app)
        except RuntimeError:
            return None

        handler = plugin.discovered_tools.get(cmd_name) or plugin.discovered_resources.get(cmd_name)
        if not handler:
            return None
        fn = get_handler_function(handler)
        sig = inspect.signature(fn)

        di_params: set[str] = set()
        with contextlib.suppress(Exception):
            di_params = set(handler.resolve_dependencies().keys())

        params = []
        for param in sig.parameters.values():
            if param.name in di_params:
                continue

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

            params.append(click.Option([f"--{param.name}"], **option_kwargs))  # pyright: ignore

        @click.pass_context
        def callback(ctx: click.Context, /, **kwargs: Any) -> None:
            """The actual command callback that executes the tool."""
            app: Litestar = ctx.obj.app
            parsed_kwargs: dict[str, Any] = _parse_cli_kwargs(kwargs)

            try:
                result = asyncio.run(execute_tool(handler, app, parsed_kwargs))
                _display_result(self._console, result)
            except Exception as e:  # noqa: BLE001
                self._console.print(f"[bold red]Unexpected error executing tool '{cmd_name}':[/bold red]")
                self._console.print(str(e))
                ctx.exit(1)

        fn_doc = fn.__doc__ or "No description provided."

        from typing import cast

        return click.Command(
            cmd_name,
            params=cast("list[click.Parameter]", params),
            callback=callback,
            help=fn_doc,
            short_help=f"Execute the '{cmd_name}' tool.",
        )


@click.group(cls=LitestarGroup, name="mcp")
def mcp_group(ctx: "click.Context") -> None:
    """Manage MCP tools and resources."""
    plugin = get_mcp_plugin(ctx.obj.app)
    ctx.obj = {"app": ctx.obj, "plugin": plugin}


@mcp_group.command(name="list-tools")  # type: ignore[misc]
def list_tools(ctx: click.Context) -> None:  # pragma: no cover
    """List all available MCP tools."""
    plugin = ctx.obj["plugin"]
    console = Console()

    if not plugin.discovered_tools:
        console.print("[yellow]No MCP tools discovered.[/yellow]")
        return

    console.print(f"[bold green]Discovered {len(plugin.discovered_tools)} tools:[/bold green]")
    for name in sorted(plugin.discovered_tools.keys()):
        handler = plugin.discovered_tools[name]
        fn = get_handler_function(handler)
        description = fn.__doc__ or "No description"
        first_line = description.split("\n")[0].strip()
        console.print(f"- [bold]{name}[/bold]: {first_line}")


@mcp_group.command(name="list-resources")  # type: ignore[misc]
def list_resources(ctx: click.Context) -> None:  # pragma: no cover
    """List all available MCP resources."""
    plugin = ctx.obj["plugin"]
    console = Console()

    if not plugin.discovered_resources:
        console.print("[yellow]No MCP resources discovered.[/yellow]")
        return

    console.print(f"[bold green]Discovered {len(plugin.discovered_resources)} resources:[/bold green]")
    for name in sorted(plugin.discovered_resources.keys()):
        handler = plugin.discovered_resources[name]
        fn = get_handler_function(handler)
        description = fn.__doc__ or "No description"
        first_line = description.split("\n")[0].strip()
        console.print(f"- [bold]{name}[/bold]: {first_line}")


def _parse_cli_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:  # pragma: no cover
    """Parse CLI kwargs, converting JSON strings to objects."""
    parsed_kwargs: dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            parsed_kwargs[key] = value
            continue

        try:
            if isinstance(value, str) and value.startswith(("{", "[")):
                parsed_kwargs[key] = json.loads(value)
            else:
                parsed_kwargs[key] = value
        except (json.JSONDecodeError, TypeError):
            parsed_kwargs[key] = value
    return parsed_kwargs


def _display_result(console: Console, result: Any) -> None:  # pragma: no cover
    """Display the result of tool execution."""
    if isinstance(result, str):
        console.print(result)
    else:
        console.print(JSON.from_data(result))


mcp_group.add_command(ToolExecutor(name="run", help="Run a discovered MCP tool by name."))  # pragma: no cover
