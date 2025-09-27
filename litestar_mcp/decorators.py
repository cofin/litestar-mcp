"""Decorators for marking MCP tools and resources."""

from typing import Any, Callable, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


def mcp_tool(name: str) -> Callable[[F], F]:
    """Decorator to mark a route handler as an MCP tool.

    Args:
        name: The name of the MCP tool.

    Returns:
        Decorator function that adds MCP metadata to the handler.

    Example:
        ```python
        @mcp_tool(name="user_manager")
        @get("/users")
        async def get_users() -> list[dict]:
            return [{"id": 1, "name": "Alice"}]
        ```
    """

    def decorator(fn: F) -> F:
        # Attach MCP metadata directly to the function/handler
        # This preserves all function properties including async nature
        metadata = {"type": "tool", "name": name}
        fn._mcp_metadata = metadata  # type: ignore[attr-defined] # noqa: SLF001

        # If this is already a Litestar handler, also add metadata to it
        # This handles the case where @mcp_tool is applied after @get/@post
        if hasattr(fn, "__class__") and "litestar" in str(fn.__class__):
            fn._mcp_metadata = metadata  # type: ignore[attr-defined] # noqa: SLF001

        return fn

    return decorator


def mcp_resource(name: str) -> Callable[[F], F]:
    """Decorator to mark a route handler as an MCP resource.

    Args:
        name: The name of the MCP resource.

    Returns:
        Decorator function that adds MCP metadata to the handler.

    Example:
        ```python
        @mcp_resource(name="app_config")
        @get("/config")
        async def get_config() -> dict:
            return {"debug": True}
        ```
    """

    def decorator(fn: F) -> F:
        # Attach MCP metadata directly to the function/handler
        # This preserves all function properties including async nature
        metadata = {"type": "resource", "name": name}
        fn._mcp_metadata = metadata  # type: ignore[attr-defined] # noqa: SLF001

        # If this is already a Litestar handler, also add metadata to it
        # This handles the case where @mcp_resource is applied after @get/@post
        if hasattr(fn, "__class__") and "litestar" in str(fn.__class__):
            fn._mcp_metadata = metadata  # type: ignore[attr-defined] # noqa: SLF001

        return fn

    return decorator
