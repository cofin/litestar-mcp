"""Class-based controller for grouping MCP prompts with shared instructions and guards."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from functools import wraps
from typing import TYPE_CHECKING, Any, cast

from litestar import Controller

from litestar_mcp.mcp.registry import PromptRegistration
from litestar_mcp.utils import get_mcp_metadata, set_mcp_metadata

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(slots=True)
class PromptMessage:
    """Canonical prompt message representation for MCP prompts."""

    role: str
    content: str
    name: str | None = None

    @classmethod
    def user(cls, content: str) -> PromptMessage:
        """Create a user prompt message."""
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> PromptMessage:
        """Create an assistant prompt message."""
        return cls(role="assistant", content=content)

    @classmethod
    def system(cls, content: str) -> PromptMessage:
        """Create a system prompt message."""
        return cls(role="system", content=content)

    def to_dict(self) -> dict[str, Any]:
        """Convert prompt message to dictionary representation."""
        data: dict[str, Any] = {
            "role": self.role,
            "content": {
                "type": "text",
                "text": self.content,
            },
        }
        if self.name is not None:
            data["name"] = self.name
        return data

    def to_mcp_dict(self) -> dict[str, Any]:
        """Convert prompt message to canonical MCP wire dictionary."""
        return self.to_dict()


def prompt(
    name: str | None = None,
    *,
    title: str | None = None,
    description: str | None = None,
    arguments: list[dict[str, Any]] | None = None,
    icons: list[dict[str, Any]] | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to mark a function or method as an MCP prompt."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        prompt_name = name or fn.__name__
        prompt_desc = description or inspect.cleandoc(fn.__doc__ or "")
        resolved_title = title or prompt_name.replace("_", " ").title()
        set_mcp_metadata(
            fn,
            {
                "type": "prompt",
                "name": prompt_name,
                "title": resolved_title,
                "description": prompt_desc,
                "arguments": arguments,
                "icons": icons,
            },
        )
        setattr(fn, "__mcp_prompt__", True)
        setattr(
            fn,
            "__mcp_prompt_metadata__",
            {
                "type": "prompt",
                "name": prompt_name,
                "title": title,
                "description": prompt_desc,
                "arguments": arguments,
                "icons": icons,
            },
        )
        return fn

    return decorator


def _normalize_dict_message(item: dict[str, Any]) -> tuple[PromptMessage, bool]:
    """Normalize a dictionary into a PromptMessage and determine if it is a system message."""
    role = item.get("role", "user")
    content = item.get("content", "")
    content_text = content.get("text", "") if isinstance(content, dict) else str(content)
    is_system = role == "system"
    return PromptMessage(role=role, content=content_text, name=item.get("name")), is_system


class PromptController(Controller):
    """Controller for declaring class-based MCP prompt groups.

    Mirrors Litestar's Controller model with class-level configurable
    attributes including instructions, namespace prefix, guards, and tags.
    """

    instructions: str | None = None
    prefix: str | None = None

    def __init__(self, owner: Any = None) -> None:
        super().__init__(owner if owner is not None else cast("Any", None))
        if not hasattr(self, "path"):
            self.path = "/"

    def format_prompt_response(self, result: Any) -> list[dict[str, Any]]:
        """Normalize raw prompt return values into canonical MCP message payloads."""
        messages: list[PromptMessage] = []
        if isinstance(result, str):
            if self.instructions:
                messages.append(PromptMessage.system(self.instructions))
            messages.append(PromptMessage.user(result))
        elif isinstance(result, PromptMessage):
            if self.instructions and result.role != "system":
                messages.append(PromptMessage.system(self.instructions))
            messages.append(result)
        elif isinstance(result, list):
            has_system = False
            for item in result:
                if isinstance(item, PromptMessage):
                    if item.role == "system":
                        has_system = True
                    messages.append(item)
                elif isinstance(item, dict):
                    msg, is_sys = _normalize_dict_message(item)
                    if is_sys:
                        has_system = True
                    messages.append(msg)
                elif isinstance(item, str):
                    messages.append(PromptMessage.user(item))
            if self.instructions and not has_system:
                messages.insert(0, PromptMessage.system(self.instructions))
        elif isinstance(result, dict):
            msg, is_sys = _normalize_dict_message(result)
            if self.instructions and not is_sys:
                messages.append(PromptMessage.system(self.instructions))
            messages.append(msg)
        else:
            if self.instructions:
                messages.append(PromptMessage.system(self.instructions))
            messages.append(PromptMessage.user(str(result)))

        return [msg.to_dict() for msg in messages]

    def _resolve_prompt_prefix(self) -> str:
        """Resolve the namespace prefix for prompts declared on this controller."""
        return self.prefix or ""

    def get_prompt_registrations(self) -> list[PromptRegistration]:
        """Discover and build PromptRegistration records for all @prompt or @mcp_prompt methods."""
        registrations: list[PromptRegistration] = []
        prefix = self._resolve_prompt_prefix()
        for attr_name in dir(self):
            if attr_name.startswith("_"):
                continue
            attr = getattr(self, attr_name, None)
            if not callable(attr):
                continue
            meta = getattr(attr, "__mcp_prompt_metadata__", None) or get_mcp_metadata(attr)
            if not meta or meta.get("type") != "prompt":
                continue

            raw_name = meta["name"]
            if prefix and not raw_name.startswith(f"{prefix}/") and not raw_name.startswith("/"):
                resolved_name = f"{prefix}/{raw_name}"
            else:
                resolved_name = raw_name

            bound_method = attr

            def make_invoker(target_method: Callable[..., Any]) -> Callable[..., Any]:
                if inspect.iscoroutinefunction(target_method):

                    @wraps(target_method)
                    async def async_invoker(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
                        res = await target_method(*args, **kwargs)
                        return self.format_prompt_response(res)

                    return async_invoker

                @wraps(target_method)
                def sync_invoker(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
                    res = target_method(*args, **kwargs)
                    return self.format_prompt_response(res)

                return sync_invoker

            invoker = make_invoker(bound_method)
            registrations.append(
                PromptRegistration(
                    name=resolved_name,
                    fn=invoker,
                    title=meta.get("title"),
                    description=meta.get("description"),
                    arguments=meta.get("arguments"),
                    icons=meta.get("icons"),
                )
            )
        return registrations

    def get_prompts(self) -> list[PromptRegistration]:
        """Return PromptRegistration records for all prompts on this controller."""
        return self.get_prompt_registrations()


__all__ = (
    "PromptController",
    "PromptMessage",
    "prompt",
)
