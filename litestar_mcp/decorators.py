"""Decorators for marking MCP tools and resources."""

from typing import Any, Callable, Dict, Optional, TypeVar
from weakref import WeakKeyDictionary

F = TypeVar("F", bound=Callable[..., Any])

# Global registry for metadata to avoid mutating handler objects directly
# using WeakKeyDictionary to avoid memory leaks
_METADATA_REGISTRY: WeakKeyDictionary[Any, Dict[str, Any]] = WeakKeyDictionary()


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
        _METADATA_REGISTRY[fn] = {"type": "tool", "name": name}
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
        _METADATA_REGISTRY[fn] = {"type": "resource", "name": name}
        return fn

    return decorator


def get_mcp_metadata(obj: Any) -> Optional[Dict[str, Any]]:
    """Get MCP metadata for an object if it exists.

    Args:
        obj: Object to check for MCP metadata.

    Returns:
        MCP metadata dictionary or None if not present.
    """
    return _METADATA_REGISTRY.get(obj)