"""Utility functions for litestar-mcp to reduce defensive programming."""

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler


def get_handler_function(handler: "BaseRouteHandler") -> Callable[..., Any]:
    """Extract the actual function from a handler.

    Litestar wraps functions in AnyCallable containers with .value attribute.
    Dishka-injected handlers also wrap the original function and expose it via
    ``__dishka_orig_func__``. MCP execution needs the original callable
    signature so dependency injection hooks can see the actual handler
    parameters instead of Dishka's synthetic ``request`` wrapper.

    Args:
        handler: The Litestar route handler.

    Returns:
        The underlying callable function.
    """
    fn = handler.fn
    # AnyCallable has .value, regular functions don't
    # Check the type instead of using hasattr
    resolved = getattr(fn, "value", fn)
    return getattr(resolved, "__dishka_orig_func__", resolved)


__all__ = ("get_handler_function",)
