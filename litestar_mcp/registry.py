"""Central registry for MCP tools, resources, and prompts."""

import inspect
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from litestar.handlers import BaseRouteHandler

from litestar_mcp.sse import SSEManager
from litestar_mcp.utils import get_mcp_metadata, parse_template

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ResourceTemplate:
    """A declared RFC 6570 Level 1 URI template bound to a resource handler."""

    name: str
    template: str
    handler: BaseRouteHandler


def _parse_docstring_args(docstring: str | None) -> dict[str, str]:
    """Extract parameter descriptions from a Google-style docstring.

    Parses the ``Args:`` (or ``Arguments:``, ``Params:``, ``Parameters:``)
    section and returns ``{param_name: description}``.  Supports multi-line
    descriptions (continuation lines indented further than the parameter line).
    """
    if not docstring:
        return {}
    lines = docstring.splitlines()
    in_args = False
    result: dict[str, str] = {}
    current_name: str | None = None
    current_desc: list[str] = []
    args_indent: int | None = None

    for line in lines:
        stripped = line.strip()
        if stripped in ("Args:", "Arguments:", "Params:", "Parameters:"):
            in_args = True
            args_indent = len(line) - len(line.lstrip())
            continue
        if not in_args:
            continue
        # Detect end of Args section: next section header at same or lesser indent
        if stripped and not line[0].isspace():
            break
        if stripped.endswith(":") and args_indent is not None:
            line_indent = len(line) - len(line.lstrip())
            if line_indent <= args_indent:
                break
        # Match "param_name: description" or "param_name (type): description"
        m = re.match(r"^\s+(\w+)(?:\s*\([^)]*\))?\s*:\s*(.*)$", line)
        if m:
            if current_name is not None:
                result[current_name] = " ".join(current_desc).strip()
            current_name = m.group(1)
            current_desc = [m.group(2)] if m.group(2) else []
        elif current_name is not None and stripped:
            current_desc.append(stripped)
        elif current_name is not None and not stripped:
            # Blank line ends current param
            result[current_name] = " ".join(current_desc).strip()
            current_name = None
            current_desc = []

    if current_name is not None:
        result[current_name] = " ".join(current_desc).strip()
    return result


@dataclass(frozen=True, slots=True)
class PromptRegistration:
    """A registered MCP prompt — either a standalone callable or a route handler.

    Standalone prompts are plain (async) functions decorated with
    ``@mcp_prompt`` and passed to ``LitestarMCP(prompts=[...])``.

    Handler-based prompts are Litestar route handlers discovered via the
    ``mcp_prompt`` opt key, executed through the normal Litestar pipeline.

    Attributes:
        name: Unique prompt identifier used in ``prompts/get``.
        fn: The callable to invoke (standalone prompt functions).
        handler: The Litestar route handler (handler-based prompts).
        title: Optional human-readable display name.
        description: Optional LLM-facing description.
        arguments: Explicit argument definitions. When ``None``, derived
            from the function signature at list time (standalone prompts
            only). Handler-based prompts return an empty argument list
            unless arguments are set explicitly.
        icons: Optional list of icon objects for UI display.
    """

    name: str
    fn: Callable[..., Any] | None = None
    handler: BaseRouteHandler | None = None
    title: str | None = None
    description: str | None = None
    arguments: list[dict[str, Any]] | None = field(default=None, hash=False)
    icons: list[dict[str, Any]] | None = field(default=None, hash=False)

    def __post_init__(self) -> None:
        if self.fn is not None and self.handler is not None:
            msg = "PromptRegistration cannot have both fn and handler set"
            raise ValueError(msg)
        if self.fn is None and self.handler is None:
            msg = "PromptRegistration must have either fn or handler set"
            raise ValueError(msg)

    def get_arguments(self) -> list[dict[str, Any]]:
        """Return prompt arguments, introspecting from signature if needed.

        When ``arguments`` was set explicitly, returns that list unchanged.
        Otherwise inspects the function signature and enriches each entry
        with a ``description`` parsed from a Google-style docstring (if
        present).
        """
        if self.arguments is not None:
            return self.arguments
        target = self.fn
        if target is None:
            return []
        sig = inspect.signature(target)
        doc_descriptions = _parse_docstring_args(getattr(target, "__doc__", None))
        _skip = {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}
        args: list[dict[str, Any]] = []
        for param_name, param in sig.parameters.items():
            if param.kind in _skip or param_name == "self":
                continue
            arg: dict[str, Any] = {"name": param_name}
            desc = doc_descriptions.get(param_name)
            if desc:
                arg["description"] = desc
            arg["required"] = param.default is inspect.Parameter.empty
            args.append(arg)
        return args


class Registry:
    """Central registry for MCP tools, resources, and prompts.

    This class decouples metadata storage and discovery from the route handlers themselves,
    avoiding issues with __slots__ or object mutation.
    """

    def __init__(self) -> None:
        """Initialize the registry."""
        self._tools: dict[str, BaseRouteHandler] = {}
        self._resources: dict[str, BaseRouteHandler] = {}
        self._templates: dict[str, ResourceTemplate] = {}
        self._prompts: dict[str, PromptRegistration] = {}
        self._sse_manager: SSEManager | None = None

    def set_sse_manager(self, manager: SSEManager) -> None:
        """Set the SSE manager for notifications."""
        self._sse_manager = manager

    @property
    def sse_manager(self) -> SSEManager:
        """Return the configured SSE manager."""
        if self._sse_manager is None:
            msg = "SSE manager has not been configured"
            raise RuntimeError(msg)
        return self._sse_manager

    @property
    def tools(self) -> dict[str, BaseRouteHandler]:
        """Get registered tools."""
        return self._tools

    @property
    def resources(self) -> dict[str, BaseRouteHandler]:
        """Get registered resources."""
        return self._resources

    def register_tool(self, name: str, handler: BaseRouteHandler) -> None:
        """Register a tool.

        Args:
            name: The tool name.
            handler: The route handler.
        """
        self._tools[name] = handler

    def register_resource(self, name: str, handler: BaseRouteHandler) -> None:
        """Register a resource.

        Args:
            name: The resource name.
            handler: The route handler.
        """
        self._resources[name] = handler

    @property
    def templates(self) -> dict[str, ResourceTemplate]:
        """Get registered resource templates, keyed by resource name."""
        return self._templates

    def register_resource_template(self, name: str, handler: BaseRouteHandler, template: str) -> None:
        """Register an RFC 6570 Level 1 URI template for a resource.

        Args:
            name: The resource name (same key as ``register_resource``).
            handler: The route handler bound to the template.
            template: The URI template string. Validated at registration;
                invalid templates raise :class:`ValueError`.
        """
        parse_template(template)
        self._templates[name] = ResourceTemplate(name=name, template=template, handler=handler)

    @property
    def prompts(self) -> dict[str, PromptRegistration]:
        """Get registered prompts."""
        return self._prompts

    def register_prompt(
        self,
        name: str,
        fn: Callable[..., Any],
        *,
        title: str | None = None,
        description: str | None = None,
        arguments: list[dict[str, Any]] | None = None,
        icons: list[dict[str, Any]] | None = None,
    ) -> None:
        """Register a standalone prompt function.

        Args:
            name: Unique prompt identifier.
            fn: The callable to invoke on ``prompts/get``.
            title: Optional human-readable display name.
            description: Optional description. Falls back to ``fn.__doc__``.
            arguments: Explicit argument definitions. When ``None``, derived
                from the function signature.
            icons: Optional list of icon objects for UI display.
        """
        if name in self._prompts:
            _logger.warning("Overwriting existing prompt registration: %s", name)
        desc = description
        if desc is None:
            doc = getattr(fn, "__doc__", None)
            if isinstance(doc, str) and doc.strip():
                desc = doc.strip()
        self._prompts[name] = PromptRegistration(
            name=name,
            fn=fn,
            title=title,
            description=desc,
            arguments=arguments,
            icons=icons,
        )

    def register_prompt_handler(
        self,
        name: str,
        handler: BaseRouteHandler,
        *,
        title: str | None = None,
        description: str | None = None,
        arguments: list[dict[str, Any]] | None = None,
        icons: list[dict[str, Any]] | None = None,
    ) -> None:
        """Register a route-handler-based prompt.

        The handler is executed via the normal Litestar pipeline on
        ``prompts/get``. If it returns a dict containing a ``messages``
        key, that dict is returned directly. Otherwise the return value
        is normalized into a messages list (str becomes a single user
        text message, dict is wrapped as a single message, list is used
        directly).

        Args:
            name: Unique prompt identifier.
            handler: The Litestar route handler.
            title: Optional human-readable display name.
            description: Optional description.
            arguments: Explicit argument definitions. When ``None``,
                handler-based prompts report an empty argument list.
            icons: Optional list of icon objects for UI display.
        """
        if name in self._prompts:
            _logger.warning("Overwriting existing prompt registration: %s", name)
        metadata = get_mcp_metadata(handler) or {}
        desc = description if description is not None else metadata.get("description")
        self._prompts[name] = PromptRegistration(
            name=name,
            handler=handler,
            title=title if title is not None else metadata.get("title"),
            description=desc,
            arguments=arguments if arguments is not None else metadata.get("arguments"),
            icons=icons if icons is not None else metadata.get("icons"),
        )

    async def publish_notification(
        self,
        method: str,
        params: dict[str, Any],
        session_id: str | None = None,
    ) -> None:
        """Publish a JSON-RPC 2.0 notification to connected clients.

        Args:
            method: The notification method (e.g., 'notifications/resources/updated').
            params: The notification parameters.
            session_id: Optional session to target; when omitted the
                notification fans out to every attached session.
        """
        if self._sse_manager:
            # Wrap in JSON-RPC 2.0 notification envelope (no id)
            await self._sse_manager.publish(
                {
                    "jsonrpc": "2.0",
                    "method": method,
                    "params": params,
                },
                session_id=session_id,
            )

    async def notify_resource_updated(self, uri: str) -> None:
        """Notify clients that a resource has been updated.

        Args:
            uri: The URI of the updated resource.
        """
        await self.publish_notification("notifications/resources/updated", {"uri": uri})

    async def notify_tools_list_changed(self) -> None:
        """Notify clients that the tool list has changed."""
        await self.publish_notification("notifications/tools/list_changed", {})

    async def notify_prompts_list_changed(self) -> None:
        """Notify clients that the prompt list has changed."""
        await self.publish_notification("notifications/prompts/list_changed", {})
