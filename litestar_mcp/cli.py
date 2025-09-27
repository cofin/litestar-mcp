"""CLI for interacting with MCP tools."""

import asyncio
import contextlib
import inspect
import json
from typing import Any, Optional

import click
from litestar import Litestar
from rich.console import Console
from rich.json import JSON

from litestar_mcp.executor import NotCallableInCLIContextError, execute_tool
from litestar_mcp.plugin import LitestarMCP
from litestar_mcp.utils import get_handler_function


def _find_mcp_plugin(app: Litestar) -> Optional[LitestarMCP]:
    """Find the LitestarMCP plugin instance on the app."""

    try:
        mcp_plugin = app.plugins.get(LitestarMCP)
    except KeyError:
        return None
    else:
        return mcp_plugin


class ToolExecutor(click.MultiCommand):  # type: ignore[valid-type,misc,unused-ignore]
    """A dynamic click MultiCommand to run discovered MCP tools."""

    def __init__(self, **attrs: Any) -> None:
        """Initialize the tool executor."""
        super().__init__(**attrs)
        self._console = Console()

    def list_commands(self, ctx: click.Context) -> list[str]:
        """List the names of all discovered tools and resources."""
        app: Litestar = ctx.obj
        plugin = _find_mcp_plugin(app)
        if not plugin:
            return []
        # Include both tools and resources
        all_commands = set(plugin.discovered_tools.keys()) | set(plugin.discovered_resources.keys())
        return sorted(all_commands)

    def get_command(self, ctx: click.Context, cmd_name: str) -> Optional[click.Command]:
        """Create a click.Command for a specific tool or resource by its name."""
        app: Litestar = ctx.obj
        plugin = _find_mcp_plugin(app)
        if not plugin:
            return None

        # Check both tools and resources
        handler = plugin.discovered_tools.get(cmd_name) or plugin.discovered_resources.get(cmd_name)
        if not handler:
            return None
        fn = get_handler_function(handler)
        sig = inspect.signature(fn)

        # Dependencies that are handled by the executor, not the CLI
        di_params: set[str] = set()
        with contextlib.suppress(Exception):
            di_params = set(handler.resolve_dependencies().keys())

        # Create CLI options from function signature
        params = []
        for param in sig.parameters.values():
            if param.name in di_params:
                continue

            # For complex types, accept a JSON string
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

            # For boolean parameters with defaults, create flags instead of options
            if annotation is bool and param.default is not inspect.Parameter.empty:
                option_kwargs["is_flag"] = True
                option_kwargs["default"] = param.default
                option_kwargs.pop("required", None)  # Flags can't be required

            params.append(click.Option([f"--{param.name}"], **option_kwargs))  # pyright: ignore

        @click.pass_context
        def callback(ctx: click.Context, /, **kwargs: Any) -> None:
            """The actual command callback that executes the tool."""
            app: Litestar = ctx.obj

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

        # Get docstring from the underlying function
        fn_doc = fn.__doc__ or "No description provided."

        from typing import cast

        return click.Command(
            cmd_name,
            params=cast("list[click.Parameter]", params),
            callback=callback,
            help=fn_doc,
            short_help=f"Execute the '{cmd_name}' tool.",
        )


@click.group()
@click.pass_context
def mcp_cli(ctx: click.Context) -> None:
    """MCP CLI for Litestar applications."""


@mcp_cli.command(name="list-tools")
@click.pass_context
def list_tools(ctx: click.Context) -> None:
    """List all available MCP tools."""
    app: Litestar = ctx.obj
    plugin = _find_mcp_plugin(app)
    console = Console()

    if not plugin or not plugin.discovered_tools:
        console.print("[yellow]No MCP tools discovered.[/yellow]")
        return

    console.print(f"[bold green]Discovered {len(plugin.discovered_tools)} tools:[/bold green]")
    for name in sorted(plugin.discovered_tools.keys()):
        handler = plugin.discovered_tools[name]
        # Get the underlying function and its docstring
        fn = get_handler_function(handler)
        description = fn.__doc__ or "No description"
        # Clean up the description - take first line only
        first_line = description.split("\n")[0].strip()
        console.print(f"- [bold]{name}[/bold]: {first_line}")


def _parse_cli_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Parse CLI kwargs, converting JSON strings to objects."""
    parsed_kwargs: dict[str, Any] = {}
    for key, value in kwargs.items():
        if value is None:
            parsed_kwargs[key] = value
            continue

        try:
            # Attempt to parse if it looks like JSON
            if isinstance(value, str) and value.startswith(("{", "[")):
                parsed_kwargs[key] = json.loads(value)
            else:
                parsed_kwargs[key] = value
        except (json.JSONDecodeError, TypeError):
            parsed_kwargs[key] = value
    return parsed_kwargs


def _display_result(console: Console, result: Any) -> None:
    """Display the result of tool execution."""
    if isinstance(result, str):
        console.print(result)
    else:
        console.print(JSON.from_data(result))


# Add the dynamic 'run' command group
mcp_cli.add_command(ToolExecutor(name="run", help="Run a discovered MCP tool by name."))
