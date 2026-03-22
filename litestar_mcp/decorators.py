# ruff: noqa: PYI034
"""Decorators for marking MCP tools and resources."""

from typing import Any, Callable, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class MetadataRegistry:
    """Singleton registry for MCP metadata using qualnames as keys."""

    _instance: Optional["MetadataRegistry"] = None

    def __new__(cls) -> "MetadataRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._data = {}  # noqa: SLF001
        return cls._instance

    def set(self, obj: Any, value: dict[str, Any]) -> None:
        key = self._get_key(obj)
        self._data[key] = value

    def get(self, obj: Any) -> Optional[dict[str, Any]]:
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


def mcp_tool(
    name: str,
    output_schema: Optional[dict[str, Any]] = None,
    annotations: Optional[dict[str, Any]] = None,
    scopes: Optional[list[str]] = None,
) -> Callable[[F], F]:
    """Decorator to mark a route handler as an MCP tool.

    Args:
        name: The name of the MCP tool.
        output_schema: Optional JSON Schema for the tool's structured output.
        annotations: Optional metadata annotations (audience, priority, etc.).
        scopes: Optional list of OAuth scopes required to call this tool.

    Returns:
        Decorator function that adds MCP metadata to the handler.

    Example:
        ```python
        @mcp_tool(name="user_manager", annotations={"audience": ["user"]})
        @get("/users")
        async def get_users() -> list[dict]:
            return [{"id": 1, "name": "Alice"}]
        ```
    """

    def decorator(fn: F) -> F:
        metadata: dict[str, Any] = {"type": "tool", "name": name}
        if output_schema is not None:
            metadata["output_schema"] = output_schema
        if annotations is not None:
            metadata["annotations"] = annotations
        if scopes is not None:
            metadata["scopes"] = scopes
        _REGISTRY.set(fn, metadata)
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


def get_mcp_metadata(obj: Any) -> Optional[dict[str, Any]]:
    """Get MCP metadata for an object if it exists.

    Args:
        obj: Object to check for MCP metadata.

    Returns:
        MCP metadata dictionary or None if not present.
    """
    return _REGISTRY.get(obj)
