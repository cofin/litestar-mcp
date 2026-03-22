"""Filtering logic for MCP tool and resource discovery."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litestar_mcp.config import MCPConfig


def should_include_handler(name: str, tags: set[str], config: "MCPConfig") -> bool:
    """Determine whether a handler should be included based on config filters.

    Precedence: exclude > include; tags > operations.

    Args:
        name: The handler/tool name.
        tags: Set of tags associated with the handler.
        config: MCP configuration with filter fields.

    Returns:
        True if the handler should be included, False otherwise.
    """
    # ── Tag-level filtering (highest precedence) ──
    if config.exclude_tags:
        if tags & set(config.exclude_tags):
            return False

    if config.include_tags:
        if not (tags & set(config.include_tags)):
            return False

    # ── Operation-level filtering ──
    if config.exclude_operations:
        if name in config.exclude_operations:
            return False

    if config.include_operations:
        if name not in config.include_operations:
            return False

    return True
