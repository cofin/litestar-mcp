"""Class-based controller for grouping MCP prompts with shared instructions and guards."""

import inspect
from typing import TYPE_CHECKING, Any, cast

from litestar import Controller, Router
from litestar.handlers import HTTPRouteHandler, post

from litestar_mcp.mcp.executor import require_internal_dispatch
from litestar_mcp.utils import set_mcp_metadata

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ("PromptController", "prompt")


def prompt(
    name: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
    arguments: list[dict[str, Any]] | None = None,
    icons: list[dict[str, Any]] | None = None,
) -> "Callable[[Callable[..., Any]], HTTPRouteHandler]":
    """Mark a controller method as an MCP prompt served through the controller's guards and dependencies."""

    def decorator(fn: "Callable[..., Any]") -> HTTPRouteHandler:
        prompt_name = name or fn.__name__
        set_mcp_metadata(
            fn,
            {
                "type": "prompt",
                "name": prompt_name,
                "title": title,
                "description": description or inspect.cleandoc(fn.__doc__ or "") or None,
                "arguments": arguments,
                "icons": icons,
            },
        )
        sync_option = None if inspect.iscoroutinefunction(fn) else False
        return post(
            f"/prompts/{prompt_name}",
            guards=[require_internal_dispatch],
            include_in_schema=False,
            sync_to_thread=sync_option,
        )(fn)

    return decorator


class PromptController(Controller):
    """Litestar controller whose @prompt methods are served as MCP prompts.

    Attributes:
        prefix: Optional namespace prepended to prompt names as <prefix>/<name>.
        instructions: Text prepended to the first user message of every prompt result.
    """

    prefix: str | None = None
    instructions: str | None = None
    include_in_schema = False

    def __init__(self, owner: Router | None = None) -> None:
        """Allow direct construction for agent use; Litestar passes its router when mounting."""
        super().__init__(cast("Router", owner))

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Give each subclass a unique internal mount path unless it declares path."""
        super().__init_subclass__(**kwargs)
        if "path" not in cls.__dict__:
            cls.path = f"/_litestar_mcp/{cls.__module__}.{cls.__qualname__}".lower()

    def qualify_prompt_name(self, name: str) -> str:
        """Return the registered prompt name for name under this controller's prefix."""
        return f"{self.prefix}/{name}" if self.prefix else name
