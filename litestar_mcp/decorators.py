# ruff: noqa: PYI034
"""Decorators for marking MCP tools and resources."""

from collections.abc import Callable
from typing import Any, Optional, TypeVar

F = TypeVar("F", bound=Callable[..., Any])


class MetadataRegistry:
    """Singleton registry for MCP metadata using qualnames as keys."""

    _instance: Optional["MetadataRegistry"] = None
    _data: dict[str, dict[str, Any]]

    def __new__(cls) -> "MetadataRegistry":
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._data = {}
            cls._instance = inst
        return cls._instance

    def set(self, obj: Any, value: dict[str, Any]) -> None:
        key = self._get_key(obj)
        self._data[key] = value

    def get(self, obj: Any) -> dict[str, Any] | None:
        key = self._get_key(obj)
        return self._data.get(key)  # pyright: ignore[reportReturnType]

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
    *,
    description: str | None = None,
    agent_instructions: str | None = None,
    when_to_use: str | None = None,
    returns: str | None = None,
    output_schema: dict[str, Any] | None = None,
    annotations: dict[str, Any] | None = None,
    scopes: list[str] | None = None,
    task_support: str | None = None,
) -> Callable[[F], F]:
    """Decorator to mark a route handler as an MCP tool.

    Args:
        name: The name of the MCP tool.
        description: LLM-facing description. Overrides ``fn.__doc__``.
            Ignored when ``handler.opt["mcp_description"]`` is set (opt wins).
            Empty string is treated as absent so the docstring fallback still
            applies.
        agent_instructions: Mandatory-context block rendered in the
            ``## Instructions`` section of the combined description.
        when_to_use: Optional structured hint for LLM clients — rendered as
            the ``## When to use`` section.
        returns: Optional return-shape hint — rendered as the ``## Returns``
            section.
        output_schema: Optional JSON Schema for the tool's structured output.
        annotations: Optional metadata annotations (audience, priority, etc.).
        scopes: Optional list of OAuth scopes required to call this tool.
        task_support: Optional task support mode. Must be one of ``optional``,
            ``required``, or ``forbidden``.

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
        if description is not None:
            metadata["description"] = description
        if agent_instructions is not None:
            metadata["agent_instructions"] = agent_instructions
        if when_to_use is not None:
            metadata["when_to_use"] = when_to_use
        if returns is not None:
            metadata["returns"] = returns
        if output_schema is not None:
            metadata["output_schema"] = output_schema
        if annotations is not None:
            metadata["annotations"] = annotations
        if scopes is not None:
            metadata["scopes"] = scopes
        if task_support is not None:
            if task_support not in {"optional", "required", "forbidden"}:
                msg = "task_support must be one of 'optional', 'required', or 'forbidden'"
                raise ValueError(msg)
            metadata["task_support"] = task_support
        _REGISTRY.set(fn, metadata)
        return fn

    return decorator


def mcp_resource(
    name: str,
    *,
    description: str | None = None,
    agent_instructions: str | None = None,
    when_to_use: str | None = None,
    returns: str | None = None,
) -> Callable[[F], F]:
    """Decorator to mark a route handler as an MCP resource.

    Args:
        name: The name of the MCP resource.
        description: LLM-facing description. Overrides ``fn.__doc__``. The
            opt-form key is ``mcp_resource_description`` (not
            ``mcp_description``) so handlers that expose both a tool and a
            resource on the same route can target each independently. Empty
            string is treated as absent.
        agent_instructions: Mandatory-context block rendered in the
            ``## Instructions`` section of the combined description.
        when_to_use: Optional structured hint rendered as the
            ``## When to use`` section.
        returns: Optional return-shape hint rendered as the ``## Returns``
            section.

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
        metadata: dict[str, Any] = {"type": "resource", "name": name}
        if description is not None:
            metadata["description"] = description
        if agent_instructions is not None:
            metadata["agent_instructions"] = agent_instructions
        if when_to_use is not None:
            metadata["when_to_use"] = when_to_use
        if returns is not None:
            metadata["returns"] = returns
        _REGISTRY.set(fn, metadata)
        return fn

    return decorator


def get_mcp_metadata(obj: Any) -> dict[str, Any] | None:
    """Get MCP metadata for an object if it exists.

    Args:
        obj: Object to check for MCP metadata.

    Returns:
        MCP metadata dictionary or None if not present.
    """
    return _REGISTRY.get(obj)
