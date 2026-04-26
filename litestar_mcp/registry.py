"""Central registry for MCP tools, resources, and prompts."""

import contextlib
import inspect
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from litestar.handlers import BaseRouteHandler

from litestar_mcp.sse import SSEManager
from litestar_mcp.utils import (
    get_handler_function,
    get_mcp_metadata,
    parse_template,
    render_description,
    should_include_handler,
)

if TYPE_CHECKING:
    from litestar_mcp.config import MCPConfig

_logger = logging.getLogger(__name__)

_VALIDATION_CONTEXT_PARAMS = frozenset({
    "request",
    "socket",
    "state",
    "scope",
    "headers",
    "cookies",
    "query",
    "body",
    "data",
})
"""Litestar magic-injection parameter names — excluded from MCP arg advertising.

Shared with :mod:`litestar_mcp.routes` tool validation. Handler-based prompts
also exclude these names because they're populated by the framework rather
than passed by the MCP caller.
"""

# MCP PromptMessage content discriminator → required structural keys.
# Per the 2025-11-25 schema, every content block carries a ``type`` and the
# variant-specific payload keys listed here. Used by ``_normalize_prompt_result``
# to validate dict-shaped messages without silently coercing them to text.
_PROMPT_CONTENT_REQUIRED_KEYS: dict[str, frozenset[str]] = {
    "text": frozenset({"text"}),
    "image": frozenset({"data", "mimeType"}),
    "audio": frozenset({"data", "mimeType"}),
    "resource_link": frozenset({"uri", "name"}),
    "resource": frozenset({"resource"}),
}


@dataclass(frozen=True, slots=True)
class ResourceTemplate:
    """A declared RFC 6570 Level 1 URI template bound to a resource handler."""

    name: str
    template: str
    handler: BaseRouteHandler


_GOOGLE_SECTION_HEADERS = frozenset({
    "Args:", "Arguments:", "Params:", "Parameters:",
    "Returns:", "Return:", "Raises:", "Yields:", "Yield:",
    "Notes:", "Note:", "Examples:", "Example:",
    "Attributes:", "References:", "See Also:",
    "Warnings:", "Warning:", "Todo:", "Todos:",
})


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

    for line in lines:
        stripped = line.strip()
        if stripped in ("Args:", "Arguments:", "Params:", "Parameters:"):
            in_args = True
            continue
        if not in_args:
            continue
        # Detect end of Args section: unindented non-empty line or known section header
        if stripped and not line[0].isspace():
            break
        if stripped in _GOOGLE_SECTION_HEADERS:
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
            # Blank line between params — skip without terminating
            # the current param so continuation lines are still captured.
            pass

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
            from the function signature (standalone prompts) or the
            handler's ``signature_model`` (handler-based prompts), with
            DI- and framework-injected parameters filtered out.
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

        For standalone prompts, inspects ``fn.__signature__``.

        For handler-based prompts, walks the handler's ``signature_model``
        fields (the same model the tool-validation path uses), filtering out
        DI dependencies and Litestar's magic-injected parameters
        (``request``, ``headers``, …) so the advertised shape matches what
        an MCP caller is expected to supply.

        Both paths enrich each entry with a Google-style docstring
        description when present.
        """
        if self.arguments is not None:
            return self.arguments
        if self.handler is not None:
            return _introspect_handler_arguments(self.handler)
        if self.fn is None:
            return []
        sig = inspect.signature(self.fn)
        doc_descriptions = _parse_docstring_args(getattr(self.fn, "__doc__", None))
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


def _introspect_handler_arguments(handler: BaseRouteHandler) -> list[dict[str, Any]]:
    """Derive prompt arguments from a route handler's ``signature_model``.

    Mirrors the partitioning used by tool-call validation in
    :mod:`litestar_mcp.routes`: DI dependencies and framework-injected names
    (``request``, ``headers``, ``state``, …) are excluded so the advertised
    arguments match what callers actually supply via ``prompts/get``.
    Descriptions are pulled from a Google-style docstring on the underlying
    function when available.
    """
    import msgspec

    try:
        signature_model = handler.signature_model
    except Exception:  # noqa: BLE001
        return []
    if signature_model is None:
        return []
    try:
        fields = msgspec.structs.fields(signature_model)
    except TypeError:
        return []

    di_params: set[str] = set()
    with contextlib.suppress(AttributeError, TypeError):
        di_params = set(handler.resolve_dependencies().keys())

    fn = get_handler_function(handler)
    doc_descriptions = _parse_docstring_args(getattr(fn, "__doc__", None))

    args: list[dict[str, Any]] = []
    for f in fields:
        if f.name in di_params or f.name in _VALIDATION_CONTEXT_PARAMS:
            continue
        arg: dict[str, Any] = {"name": f.name}
        desc = doc_descriptions.get(f.name)
        if desc:
            arg["description"] = desc
        arg["required"] = f.default is msgspec.NODEFAULT and f.default_factory is msgspec.NODEFAULT
        args.append(arg)
    return args


def _normalize_prompt_result(result: Any) -> list[dict[str, Any]]:
    """Normalize a prompt's return value to a list of PromptMessage dicts.

    * ``str`` → single user-role text message.
    * ``dict`` with a valid ``role`` + ``content`` (per :data:`_PROMPT_CONTENT_REQUIRED_KEYS`)
      is returned as-is, wrapped in a list.
    * ``list`` items follow the same dict rules.
    * Any other type — or any dict that doesn't look like a valid
      ``PromptMessage`` content block — is coerced to a user-role text
      message via ``str(item)`` with a ``warning`` log.

    The variant check covers the spec's ``text`` / ``image`` / ``audio`` /
    ``resource_link`` / ``resource`` content types: a content block missing
    only its outer ``role`` (e.g. an unwrapped image) is still recognised
    and not mangled into a stringified dict.
    """
    if isinstance(result, str):
        return [{"role": "user", "content": {"type": "text", "text": result}}]
    if isinstance(result, dict):
        return [_coerce_prompt_message(result, index=None)]
    if isinstance(result, list):
        return [_coerce_prompt_message(item, index=i) for i, item in enumerate(result)]
    _logger.warning("Prompt returned unexpected type %s, coercing to string", type(result).__name__)
    return [{"role": "user", "content": {"type": "text", "text": str(result)}}]


def _coerce_prompt_message(item: Any, *, index: int | None) -> dict[str, Any]:
    """Coerce a single result element into a valid ``PromptMessage`` dict.

    Recognises:
      * Already-shaped messages (``role`` + ``content`` where ``content`` is
        a valid content block or list of content blocks).
      * Unwrapped content blocks (``type`` + variant-required keys) — wrapped
        in a ``user``-role envelope.
      * Anything else — stringified with a warning.
    """
    if not isinstance(item, dict):
        _logger.warning(
            "Prompt result element %sis not a dict (%s), coercing to string",
            f"at index {index} " if index is not None else "",
            type(item).__name__,
        )
        return {"role": "user", "content": {"type": "text", "text": str(item)}}

    if "role" in item and "content" in item:
        content = item["content"]
        if _looks_like_content(content) or _looks_like_content_list(content):
            return item

    if _looks_like_content(item):
        return {"role": "user", "content": item}

    _logger.warning(
        "Prompt result element %sdid not match PromptMessage shape (keys=%s), coercing to string",
        f"at index {index} " if index is not None else "",
        list(item.keys()),
    )
    return {"role": "user", "content": {"type": "text", "text": str(item)}}


def _looks_like_content(value: Any) -> bool:
    """True when ``value`` is a dict matching a known content-block variant."""
    if not isinstance(value, dict):
        return False
    variant = value.get("type")
    required = _PROMPT_CONTENT_REQUIRED_KEYS.get(variant) if isinstance(variant, str) else None
    return required is not None and required.issubset(value.keys())


def _looks_like_content_list(value: Any) -> bool:
    """True when ``value`` is a non-empty list of valid content-block dicts."""
    return isinstance(value, list) and bool(value) and all(_looks_like_content(item) for item in value)


def resolve_prompt_description(registration: "PromptRegistration", config: "MCPConfig") -> str | None:
    """Resolve the description string for a registered prompt.

    Handler-based prompts run through ``render_description`` so opt-key
    overrides, structured sections, and docstring fallbacks all apply
    consistently with tools and resources. Standalone prompts use the
    description captured at registration time (decorator value or
    ``fn.__doc__`` fallback) — there's no opt-key plumbing on a bare fn.
    """
    if registration.handler is not None:
        fn = get_handler_function(registration.handler)
        return render_description(
            registration.handler,
            fn,
            kind="prompt",
            fallback_name=registration.name,
            opt_keys=config.opt_keys,
        )
    return registration.description


def render_prompt_entry(registration: "PromptRegistration", config: "MCPConfig") -> dict[str, Any]:
    """Build a Prompt entry dict for ``prompts/list`` and the server manifest.

    Single source of truth for the wire shape so route + manifest
    rendering can't drift. Optional fields (``title``, ``description``,
    ``arguments``, ``icons``) are omitted when absent rather than emitted
    as ``null``.
    """
    entry: dict[str, Any] = {"name": registration.name}
    if registration.title is not None:
        entry["title"] = registration.title
    description = resolve_prompt_description(registration, config)
    if description is not None:
        entry["description"] = description
    arguments = registration.get_arguments()
    if arguments:
        entry["arguments"] = arguments
    if registration.icons is not None:
        entry["icons"] = registration.icons
    return entry


def should_include_prompt(registration: "PromptRegistration", config: "MCPConfig") -> bool:
    """Apply ``include/exclude_operations`` and tag filters to a prompt.

    Handler-based prompts get the full filter set (tags + name).
    Standalone (fn-based) prompts skip the tag filters — they have no
    handler tags to test — but ``include_operations`` /
    ``exclude_operations`` name filters still apply because they select
    by prompt name, which fn-based prompts have just like everything else.
    """
    if registration.handler is not None:
        handler_tags = set(getattr(registration.handler, "tags", None) or [])
        return should_include_handler(registration.name, handler_tags, config)
    if config.exclude_operations and registration.name in config.exclude_operations:
        return False
    return not (config.include_operations and registration.name not in config.include_operations)


class Registry:
    """Central registry for MCP tools, resources, and prompts.

    This class decouples metadata storage and discovery from the route handlers themselves,
    avoiding issues with __slots__ or object mutation.

    Note:
        Tools and resources are stored as bare ``BaseRouteHandler`` values
        because every entry has a single underlying handler. Prompts use
        :class:`PromptRegistration` instead — a prompt may originate from
        either a standalone ``@mcp_prompt`` callable *or* a route handler,
        so the dataclass carries the ``fn | handler`` union plus the
        per-prompt metadata (title, description, arguments, icons) that
        can't live on a bare callable.
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
        if name in self._tools:
            _logger.warning("Overwriting existing tool registration: %s", name)
        self._tools[name] = handler

    def register_resource(self, name: str, handler: BaseRouteHandler) -> None:
        """Register a resource.

        Args:
            name: The resource name.
            handler: The route handler.
        """
        if name in self._resources:
            _logger.warning("Overwriting existing resource registration: %s", name)
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
        if name in self._templates:
            _logger.warning("Overwriting existing resource template registration: %s", name)
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
