"""Class-based controller for grouping MCP prompts with shared instructions and guards."""

import copy
import inspect
from typing import TYPE_CHECKING, Any, cast

from litestar import Controller, Router
from litestar.handlers import HTTPRouteHandler, post

from litestar_mcp.core._typing import DISHKA_INSTALLED, dishka_inject
from litestar_mcp.core.tools import has_dishka_parameters
from litestar_mcp.mcp.executor import require_internal_dispatch
from litestar_mcp.utils import set_mcp_metadata
from litestar_mcp.utils.handler_signature import normalize_synthesized_handler_signature

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ("PromptController", "prompt")


def _wrap_dishka_if_needed(fn: "Callable[..., Any]") -> "Callable[..., Any]":
    """Wrap callable with dishka.integrations.litestar.inject if it uses FromDishka."""
    if not DISHKA_INSTALLED or getattr(fn, "__dishka_injected__", False):
        return fn
    if not has_dishka_parameters(fn):
        return fn
    return cast("Callable[..., Any]", dishka_inject(fn))


def prompt(
    name: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
    arguments: list[dict[str, Any]] | None = None,
    icons: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
) -> "Callable[[Callable[..., Any]], HTTPRouteHandler]":
    """Mark a controller method as an MCP prompt served through the controller's guards and dependencies."""

    def decorator(fn: "Callable[..., Any]") -> HTTPRouteHandler:
        prompt_name = name or fn.__name__
        cleaned_title = title.strip() if isinstance(title, str) and title.strip() else None
        cleaned_icons = [copy.deepcopy(dict(item)) for item in icons] if icons else None
        cleaned_meta = copy.deepcopy(dict(meta)) if meta else None
        metadata: dict[str, Any] = {
            "type": "prompt",
            "name": prompt_name,
            "title": cleaned_title,
            "description": description or inspect.cleandoc(fn.__doc__ or "") or None,
            "arguments": arguments,
            "icons": cleaned_icons,
        }
        if cleaned_meta is not None:
            metadata["meta"] = cleaned_meta
        set_mcp_metadata(fn, metadata, kind="prompt")
        target_fn = _wrap_dishka_if_needed(fn)
        if target_fn is not fn:
            set_mcp_metadata(target_fn, metadata, kind="prompt")
        normalize_synthesized_handler_signature(target_fn, fn, path=f"/prompts/{prompt_name}")
        sync_option = None if inspect.iscoroutinefunction(target_fn) else False
        return post(
            f"/prompts/{prompt_name}",
            guards=[require_internal_dispatch],
            include_in_schema=False,
            sync_to_thread=sync_option,
        )(target_fn)

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
