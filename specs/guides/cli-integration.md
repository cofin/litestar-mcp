# CLI Integration Patterns for litestar-mcp

Patterns for integrating CLI commands with Litestar plugins.

## Overview

litestar-mcp provides CLI commands through the `CLIPlugin` protocol, enabling local tool execution without a running server.

## LitestarGroup Pattern

**Pattern**: Use `LitestarGroup` for all CLI command groups.

**Example**:
```python
from litestar.cli._utils import LitestarGroup
import click

@click.group(cls=LitestarGroup, name="mcp")
def mcp_group(ctx: "click.Context") -> None:
    """MCP commands."""
    plugin = get_mcp_plugin(ctx.obj.app)
    ctx.obj = {"app": ctx.obj, "plugin": plugin}
```

**When to use**: For all CLI command groups in Litestar plugins.

## Plugin Retrieval Pattern

**Pattern**: Retrieve plugin from app using `app.plugins.get()`.

**Example**:
```python
from contextlib import suppress
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar_mcp.plugin import LitestarMCP

def get_mcp_plugin(app: "Litestar") -> "LitestarMCP":
    """Retrieve the MCP plugin.

    Args:
        app: The Litestar application.

    Returns:
        The MCP plugin instance.

    Raises:
        RuntimeError: If plugin not found.
    """
    from litestar_mcp.plugin import LitestarMCP

    with suppress(KeyError):
        return app.plugins.get(LitestarMCP)
    raise RuntimeError("MCP plugin not found. Ensure it's registered.")
```

**When to use**: At the start of CLI command groups to access plugin state.

## Dynamic Command Generation Pattern

**Pattern**: Use `MultiCommand` to generate commands dynamically.

**Example**:
```python
class ToolExecutor(click.MultiCommand):
    """Dynamic command executor for MCP tools."""

    def list_commands(self, ctx: click.Context) -> "list[str]":
        """List all available tool commands."""
        app = ctx.obj.app
        plugin = get_mcp_plugin(app)
        return sorted(plugin.discovered_tools.keys())

    def get_command(self, ctx: click.Context, name: str) -> "Optional[click.Command]":
        """Generate command for specific tool."""
        app = ctx.obj.app
        plugin = get_mcp_plugin(app)

        if name not in plugin.discovered_tools:
            return None

        handler = plugin.discovered_tools[name]
        # Generate Click command from handler signature
        # ...

mcp_group.add_command(ToolExecutor(name="run"))
```

**When to use**: For dynamic command generation based on discovered routes.

## CLI Context Limitations Pattern

**Pattern**: Handle request-scoped dependency errors gracefully.

**Example**:
```python
from litestar_mcp.executor import NotCallableInCLIContextError

try:
    result = await execute_tool(handler, app, tool_args)
except NotCallableInCLIContextError as e:
    console.print(f"[red]Error:[/red] {e}")
    ctx.exit(1)
```

**When to use**: When executing tools from CLI that may require request-scoped dependencies.

## Related Patterns

- See AGENTS.md → CLI Integration section
- See [Plugin Architecture](plugin-architecture.md) for plugin initialization
- See [Testing Patterns](testing-patterns.md) for CLI testing

---

**This guide is automatically updated** by the Docs & Vision agent.
