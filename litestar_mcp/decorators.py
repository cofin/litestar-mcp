"""Decorators for marking MCP tools and resources."""

from typing import Any, Callable, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def mcp_tool(name: str, description: Optional[str] = None) -> Callable[[F], F]:
    """Decorator to mark a route handler as an MCP tool.

    Args:
        name: The name of the MCP tool.
        description: Optional description override for the tool.

    Returns:
        Decorator function that adds MCP metadata to the handler.

    Example:
        ```python
        @mcp_tool(name="user_manager", description="Manage users")
        @get("/users")
        async def get_users() -> list[dict]:
            return [{"id": 1, "name": "Alice"}]
        ```
    """

    def decorator(fn: F) -> F:
        fn._mcp_pending = {  # type: ignore[attr-defined]
            "type": "tool",
            "name": name,
            "description": description,
        }
        return fn

    return decorator


def mcp_resource(name: str, description: Optional[str] = None) -> Callable[[F], F]:
    """Decorator to mark a route handler as an MCP resource.

    Args:
        name: The name of the MCP resource.
        description: Optional description override for the resource.

    Returns:
        Decorator function that adds MCP metadata to the handler.

    Example:
        ```python
        @mcp_resource(name="app_config", description="Application configuration")
        @get("/config")
        async def get_config() -> dict:
            return {"debug": True}
        ```
    """

    def decorator(fn: F) -> F:
        fn._mcp_pending = {  # type: ignore[attr-defined]
            "type": "resource",
            "name": name,
            "description": description,
        }
        return fn

    return decorator
