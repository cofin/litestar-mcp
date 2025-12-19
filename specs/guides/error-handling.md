# Error Handling Patterns for litestar-mcp

Patterns for error handling in litestar-mcp following Litestar conventions.

## Overview

litestar-mcp relies on Litestar's exception hierarchy so errors behave consistently in HTTP handlers and CLI commands.

## Framework Exception Pattern

**Pattern**: Use Litestar exceptions directly for MCP errors.

**Example**:
```python
from litestar.exceptions import NotFoundException, ValidationException

def require_tool(name: str, tools: "dict[str, BaseRouteHandler]") -> "BaseRouteHandler":
    if name not in tools:
        raise NotFoundException(detail=f"Tool '{name}' not found")
    return tools[name]

def validate_arguments(data: dict) -> None:
    if "arguments" not in data:
        raise ValidationException("Missing 'arguments' in MCP payload")
```

**When to use**: Prefer Litestar’s built-in exceptions unless a plugin-specific error is needed.

**Common exceptions**:
- `NotFoundException` - Resource not found (404)
- `ValidationException` - Input validation failures (422)
- `HTTPException` - Explicit HTTP status and message
- `InternalServerException` - Unexpected errors (500)

## HTTP Error Handling Pattern

**Pattern**: Let Litestar handle exceptions automatically, or catch and return MCP error format.

**Example**:
```python
from litestar import post
from litestar.exceptions import NotFoundException
from litestar.serialization import encode_json

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
from litestar.exceptions import ValidationException
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
    except ValidationException as e:
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
from litestar.exceptions import ValidationException

def validate_tool_arguments(handler: BaseRouteHandler, tool_args: "dict[str, Any]") -> None:
    """Validate tool arguments match handler signature.

    Args:
        handler: The route handler.
        tool_args: Arguments to validate.

    Raises:
        ValidationException: If required arguments are missing.
    """
    sig = inspect.signature(handler.fn)
    required_params = {
        name for name, param in sig.parameters.items()
        if param.default is inspect.Parameter.empty
    }

    missing = required_params - set(tool_args.keys())
    if missing:
        missing_args = ", ".join(sorted(missing))
        raise ValidationException(f"Missing required arguments: {missing_args}")
```

**When to use**: Before executing operations to provide clear error messages.

## Related Patterns

- See AGENTS.md → Error Handling section
- See [Testing Patterns](testing-patterns.md) for error testing
- See [Plugin Architecture](plugin-architecture.md) for plugin errors

---

**This guide is automatically updated** by the Docs & Vision agent.
