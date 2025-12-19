"""Filtering utilities for MCP tool and resource discovery.

Implements precedence-based filtering to control which handlers are exposed
via MCP protocol, with parity to fastapi-mcp filtering semantics.
"""

from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler

from litestar_mcp.config import MCPConfig
from litestar_mcp.utils import get_handler_function

__all__ = ("should_include_handler",)


def _normalize_path_components(path_template: str) -> "list[str]":
    components: list[str] = []
    for part in path_template.strip("/").split("/"):
        if not part:
            continue
        if part.startswith("{") and part.endswith("}"):
            name = part[1:-1].split(":", 1)[0]
            components.append(name)
        else:
            components.append(part)
    return components


def _resolve_operation_id(handler: "BaseRouteHandler") -> Optional[str]:
    operation_id = getattr(handler, "operation_id", None)
    if isinstance(operation_id, str):
        return operation_id

    if callable(operation_id):
        try:
            http_method = next(iter(handler.http_methods))
        except Exception:
            http_method = "GET"
        path_template = sorted(handler.paths)[0] if getattr(handler, "paths", None) else getattr(handler, "path", "/")
        path_components = _normalize_path_components(path_template)
        try:
            return operation_id(handler, http_method, path_components)
        except Exception:
            return getattr(get_handler_function(handler), "__name__", None)

    return getattr(get_handler_function(handler), "__name__", None)


def should_include_handler(handler: "BaseRouteHandler", config: MCPConfig) -> bool:
    """Determine if a handler should be exposed via MCP based on filter config.

    Filter precedence (evaluated in order):
    1. include_tags (OR logic) - Endpoint must have at least one matching tag
    2. exclude_tags (OR logic, overrides include_tags) - Endpoint with any excluded tag is removed
    3. include_operations (OR logic) - Endpoint operation_id must be in list
    4. exclude_operations (OR logic, overrides include_operations) - Explicit exclusion

    Empty include list means explicit restriction (no endpoints match).
    Empty exclude list has no effect (no restrictions applied).

    Args:
        handler: Litestar route handler to evaluate
        config: MCP configuration with filter settings

    Returns:
        True if handler passes all filters, False otherwise

    Example:
        >>> config = MCPConfig(
        ...     include_tags=["public"],
        ...     exclude_tags=["admin"],
        ...     exclude_operations=["delete_user"]
        ... )
        >>> should_include_handler(handler, config)
        True
    """
    operation_id = _resolve_operation_id(handler)
    handler_tags = set(handler.tags) if handler.tags else set()  # type: ignore[attr-defined]

    if config.include_tags is not None:
        if not config.include_tags:
            return False
        if not handler_tags.intersection(config.include_tags):
            return False

    if config.exclude_tags is not None and handler_tags.intersection(config.exclude_tags):
        return False

    if config.include_operations is not None:
        if not config.include_operations:
            return False
        if operation_id not in config.include_operations:
            return False

    return not (config.exclude_operations is not None and operation_id in config.exclude_operations)
