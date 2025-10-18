# Error Handling Patterns for litestar-mcp

Patterns for error handling in litestar-mcp following Litestar conventions.

## Overview

litestar-mcp uses Litestar's exception hierarchy for consistent error handling across HTTP endpoints and CLI commands.

## Custom Exception Pattern

**Pattern**: Inherit from Litestar exceptions for framework integration.

**Example**:
```python
from litestar.exceptions import ImproperlyConfiguredException

class NotCallableInCLIContextError(ImproperlyConfiguredException):
    """Raised when tool cannot be called from CLI context."""

    def __init__(self, handler_name: str, parameter_name: str) -> None:
        """Initialize error.

        Args:
            handler_name: Name of handler that cannot be called.
            parameter_name: Name of parameter causing issue.
        """
        super().__init__(
            f"Tool '{handler_name}' cannot be called from CLI because it depends on "
            f"request-scoped dependency '{parameter_name}', not available in CLI context."
        )
```

**When to use**: For custom errors specific to litestar-mcp functionality.

**Common base exceptions**:
- `ImproperlyConfiguredException` - Configuration or setup issues
- `NotFoundException` - Resource not found (404)
- `ValidationException` - Input validation failures
- `InternalServerException` - Unexpected errors (500)

## HTTP Error Handling Pattern

**Pattern**: Let Litestar handle exceptions automatically, or catch and return MCP error format.

**Example**:
```python
from litestar import post
from litestar.exceptions import NotFoundException

@post("/tools/{tool_name:str}")
async def call_tool(
    self,
    tool_name: str,
    data: "dict[str, Any]",
    discovered_tools: "dict[str, BaseRouteHandler]",
) -> "dict[str, Any]":
    """Execute MCP tool."""
    if tool_name not in discovered_tools:
        raise NotFoundException(detail=f"Tool '{tool_name}' not found")

    try:
        result = await execute_tool(handler, app, tool_args)
        return {"content": [{"type": "text", "text": encode_json(result).decode()}]}
    except Exception as e:
        return {
            "error": {
                "code": -1,
                "message": f"Tool execution failed: {e!s}",
            }
        }
```

**When to use**: In HTTP endpoints to provide proper HTTP status codes and MCP error responses.

## CLI Error Handling Pattern

**Pattern**: Catch exceptions in CLI and exit with appropriate code.

**Example**:
```python
from rich.console import Console
import click

console = Console()

@click.command()
@click.pass_context
def run_tool(ctx: click.Context, **kwargs: Any) -> None:
    """Run MCP tool."""
    try:
        result = asyncio.run(execute_tool(handler, app, kwargs))
        console.print(JSON.from_data(result))
    except NotCallableInCLIContextError as e:
        console.print(f"[bold red]Error:[/bold red] {e}")
        ctx.exit(1)
    except ValueError as e:
        console.print(f"[bold red]Invalid arguments:[/bold red] {e}")
        ctx.exit(1)
    except Exception as e:
        console.print(f"[bold red]Unexpected error:[/bold red] {e}")
        ctx.exit(1)
```

**When to use**: In CLI commands to provide user-friendly error messages.

## Validation Error Pattern

**Pattern**: Validate inputs and raise clear errors early.

**Example**:
```python
def validate_tool_arguments(handler: BaseRouteHandler, tool_args: "dict[str, Any]") -> None:
    """Validate tool arguments match handler signature.

    Args:
        handler: The route handler.
        tool_args: Arguments to validate.

    Raises:
        ValueError: If required arguments are missing.
    """
    sig = inspect.signature(handler.fn.value)
    required_params = {
        name for name, param in sig.parameters.items()
        if param.default is inspect.Parameter.empty
    }

    missing = required_params - set(tool_args.keys())
    if missing:
        missing_args = ", ".join(sorted(missing))
        raise ValueError(f"Missing required arguments: {missing_args}")
```

**When to use**: Before executing operations to provide clear error messages.

## Related Patterns

- See AGENTS.md → Error Handling section
- See [Testing Patterns](testing-patterns.md) for error testing
- See [Plugin Architecture](plugin-architecture.md) for plugin errors

---

**This guide is automatically updated** by the Docs & Vision agent.
