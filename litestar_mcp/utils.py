"""Utility functions for litestar-mcp to reduce defensive programming."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler


def get_handler_function(handler: "BaseRouteHandler") -> Callable[..., Any]:
    """Extract the actual function from a handler.

    Litestar wraps functions in AnyCallable containers with .value attribute.
    We access it directly - no defensive checks needed.

    Args:
        handler: The Litestar route handler.

    Returns:
        The underlying callable function.
    """
    fn = handler.fn
    # AnyCallable has .value, regular functions don't
    # Check the type instead of using hasattr
    return getattr(fn, "value", fn)


__all__ = ("get_handler_function",)
