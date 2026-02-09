"""Decorators for marking MCP tools and resources."""

from typing import Any, Callable, Dict, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class MetadataRegistry:
    """Singleton registry for MCP metadata using qualnames as keys."""
    _instance: Optional["MetadataRegistry"] = None
    
    def __new__(cls) -> "MetadataRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}
        return cls._instance

    def set(self, obj: Any, value: Dict[str, Any]) -> None:
        key = self._get_key(obj)
        self._data[key] = value

    def get(self, obj: Any) -> Optional[Dict[str, Any]]:
        key = self._get_key(obj)
        return self._data.get(key)

    def _get_key(self, obj: Any) -> str:
        # Resolve to the underlying function
        target = obj
        if hasattr(obj, "fn"):
            target = obj.fn
            if hasattr(target, "value"):
                target = target.value
        
        if hasattr(target, "__func__"):
            target = target.__func__
            
        if hasattr(target, "__wrapped__"):
            target = target.__wrapped__

        # Use qualname and module as key
        module = getattr(target, "__module__", "unknown")
        qualname = getattr(target, "__qualname__", "unknown")
        return f"{module}.{qualname}"

_REGISTRY = MetadataRegistry()


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
        _REGISTRY.set(fn, {"type": "tool", "name": name})
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
        _REGISTRY.set(fn, {"type": "resource", "name": name})
        return fn

    return decorator


def get_mcp_metadata(obj: Any) -> Optional[Dict[str, Any]]:
    """Get MCP metadata for an object if it exists.

    Args:
        obj: Object to check for MCP metadata.

    Returns:
        MCP metadata dictionary or None if not present.
    """
    return _REGISTRY.get(obj)