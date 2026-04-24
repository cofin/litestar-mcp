# ruff: noqa: PYI034
"""Utilities shared across the litestar-mcp package.

This module is the single home for handler-introspection helpers
(``get_handler_function``), discovery filtering (``should_include_handler``),
MCP metadata decorators (``@mcp_tool``, ``@mcp_resource``, ``@mcp_prompt``,
``get_mcp_metadata``, ``MetadataRegistry``), LLM-facing description
rendering (``render_description``, ``extract_description_sources``,
``DescriptionSources``), and the RFC 6570 Level 1 URI template helpers
(``parse_template``, ``match_uri``, ``expand_template``). Before v0.5.0
these lived in separate modules (``filters.py``, ``decorators.py``,
``_descriptions.py``, ``_uri_template.py``); Ch5 of the v0.5.0 roadmap
flattens them into this single module.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Optional, TypeVar

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler

    from litestar_mcp.config import MCPConfig, MCPOptKeys


F = TypeVar("F", bound=Callable[..., Any])


# ---------------------------------------------------------------------------
# Handler helpers
# ---------------------------------------------------------------------------


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
    resolved = getattr(fn, "value", fn)
    return getattr(resolved, "__dishka_orig_func__", resolved)


# ---------------------------------------------------------------------------
# Discovery filters
# ---------------------------------------------------------------------------


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
    if config.exclude_tags and tags & set(config.exclude_tags):
        return False
    if config.include_tags and not (tags & set(config.include_tags)):
        return False
    if config.exclude_operations and name in config.exclude_operations:
        return False
    return not (config.include_operations and name not in config.include_operations)


# ---------------------------------------------------------------------------
# MCP metadata registry + decorators
# ---------------------------------------------------------------------------


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
        target = obj
        if hasattr(obj, "fn"):
            target = obj.fn
            if hasattr(target, "value"):
                target = target.value

        if hasattr(target, "__func__"):
            target = target.__func__

        if hasattr(target, "__wrapped__"):
            target = target.__wrapped__

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
        scopes: Optional list of OAuth scopes advertised as discovery
            metadata (surfaced under ``tools[].annotations.scopes`` in
            ``tools/list``). Scopes are **not** enforced inline — attach a
            Litestar ``Guard`` to the route / router / controller for
            authorization.
        task_support: Optional task support mode. Must be one of ``optional``,
            ``required``, or ``forbidden``.

    Returns:
        Decorator function that adds MCP metadata to the handler.

    Example:
        Pass MCP metadata straight through to the route decorator —
        Litestar funnels unknown kwargs into ``handler.opt``, so no
        double-decoration is required:

        ```python
        @get("/users", mcp_tool="user_manager", mcp_description="List every user.")
        async def get_users() -> list[dict]:
            return [{"id": 1, "name": "Alice"}]
        ```

        The decorator form is retained for parity; it carries the same
        metadata to the registry and is useful when you want to stamp
        ``output_schema``, ``annotations``, ``scopes``, or ``task_support``
        without mixing more keys into ``handler.opt``.
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
    uri_template: str | None = None,
    description: str | None = None,
    agent_instructions: str | None = None,
    when_to_use: str | None = None,
    returns: str | None = None,
) -> Callable[[F], F]:
    """Decorator to mark a route handler as an MCP resource.

    Args:
        name: The name of the MCP resource.
        uri_template: Optional RFC 6570 Level 1 URI template
            (e.g. ``"app://workspaces/{workspace_id}/files/{file_id}"``).
            Concrete URIs matching the template dispatch to this handler
            with extracted variables passed as kwargs.
        description: LLM-facing description. Overrides ``fn.__doc__``.
        agent_instructions: Mandatory-context block rendered in the
            ``## Instructions`` section.
        when_to_use: Optional structured hint rendered as the
            ``## When to use`` section.
        returns: Optional return-shape hint rendered as the ``## Returns``
            section.

    Returns:
        Decorator function that adds MCP metadata to the handler.

    Example:
        Pass MCP metadata straight through to the route decorator —
        Litestar funnels unknown kwargs into ``handler.opt``:

        ```python
        @get("/config", mcp_resource="app_config")
        async def get_config() -> dict:
            return {"debug": True}

        @get(
            "/workspaces/{workspace_id:str}/files/{file_id:str}",
            mcp_resource="workspace_file",
            mcp_resource_template="app://workspaces/{workspace_id}/files/{file_id}",
        )
        async def read_workspace_file(workspace_id: str, file_id: str) -> dict:
            ...
        ```

        The decorator form is retained for parity; it carries the same
        metadata to the registry.
    """
    if uri_template is not None:
        parse_template(uri_template)

    def decorator(fn: F) -> F:
        metadata: dict[str, Any] = {"type": "resource", "name": name}
        if uri_template is not None:
            metadata["resource_template"] = uri_template
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


def mcp_prompt(
    name: str,
    *,
    title: str | None = None,
    description: str | None = None,
    arguments: list[dict[str, Any]] | None = None,
    icons: list[dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    """Decorator to mark a callable as an MCP prompt template.

    Prompt functions take keyword arguments matching the declared prompt
    arguments and return prompt messages. The return value is normalised
    to a list of ``PromptMessage`` dicts:

    * ``str`` → single ``{"role": "user", "content": {"type": "text", "text": ...}}``
    * ``dict`` → treated as a single message and wrapped in a list
    * ``list[dict]`` → used directly
    * Any other type → ``str(result)`` wrapped as a single user text message

    Both sync and async callables are supported.

    Args:
        name: Unique identifier for the prompt (used in ``prompts/get``).
        title: Optional human-readable display name for UI clients.
        description: LLM-facing description. When omitted, ``fn.__doc__``
            is used as the fallback during registration.
        arguments: Optional explicit argument definitions — each entry is a
            dict with ``name`` (required), ``description`` (optional), and
            ``required`` (optional, defaults to introspection from the
            function signature). When omitted the argument list is derived
            automatically from the decorated function's signature and
            Google-style docstring.
        icons: Optional list of icon objects for UI display. Each entry is a
            dict with ``src`` (URL), ``mimeType``, and optionally ``sizes``
            per the MCP spec.

    Returns:
        Decorator function that adds MCP metadata to the callable.

    Example:
        Standalone prompt function registered via
        ``LitestarMCP(prompts=[summarize_text])``:

        ```python
        @mcp_prompt(name="summarize", description="Summarise a document.")
        async def summarize_text(text: str, style: str = "concise") -> str:
            return f"Please summarise the following in a {style} style:\\n\\n{text}"
        ```
    """

    def decorator(fn: F) -> F:
        metadata: dict[str, Any] = {"type": "prompt", "name": name}
        if title is not None:
            metadata["title"] = title
        if description is not None:
            metadata["description"] = description
        if arguments is not None:
            metadata["arguments"] = arguments
        if icons is not None:
            metadata["icons"] = icons
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


# ---------------------------------------------------------------------------
# Description rendering
# ---------------------------------------------------------------------------

Kind = Literal["tool", "resource", "prompt"]

_STRUCTURED_FIELDS: tuple[str, str, str] = ("when_to_use", "returns", "agent_instructions")


@dataclass(frozen=True)
class DescriptionSources:
    """Resolved description fields for a handler.

    Attributes:
        description: The primary LLM-facing description (always set).
        when_to_use: Optional ``## When to use`` section.
        returns: Optional ``## Returns`` section.
        agent_instructions: Optional ``## Instructions`` section.
    """

    description: str
    when_to_use: str | None
    returns: str | None
    agent_instructions: str | None


def _clean(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _default_opt_keys() -> "MCPOptKeys":
    """Return a default :class:`MCPOptKeys` without creating an import cycle."""
    from litestar_mcp.config import MCPOptKeys

    return MCPOptKeys()


def _read_field(
    handler: Any,
    fn: Any,
    field_name: str,
    kind: Kind,
    opt_keys: "MCPOptKeys",
) -> str | None:
    opt = getattr(handler, "opt", None) or {}
    opt_value = _clean(opt.get(opt_keys.for_field(field_name, kind)))
    if opt_value is not None:
        return opt_value
    metadata = get_mcp_metadata(handler) or get_mcp_metadata(fn) or {}
    return _clean(metadata.get(field_name))


def extract_description_sources(
    handler: Any,
    fn: Any,
    *,
    kind: Kind,
    fallback_name: str,
    opt_keys: "MCPOptKeys | None" = None,
) -> DescriptionSources:
    """Resolve every description field for a handler."""
    keys = opt_keys if opt_keys is not None else _default_opt_keys()
    description = _read_field(handler, fn, "description", kind, keys)
    if description is None:
        doc = _clean(getattr(fn, "__doc__", None))
        description = doc if doc is not None else f"{kind.title()}: {fallback_name}"
    return DescriptionSources(
        description=description,
        when_to_use=_read_field(handler, fn, "when_to_use", kind, keys),
        returns=_read_field(handler, fn, "returns", kind, keys),
        agent_instructions=_read_field(handler, fn, "agent_instructions", kind, keys),
    )


def render_description(
    handler: Any,
    fn: Any,
    *,
    kind: Kind,
    fallback_name: str,
    structured: bool = True,
    opt_keys: "MCPOptKeys | None" = None,
) -> str:
    """Render the final description string for a handler."""
    sources = extract_description_sources(handler, fn, kind=kind, fallback_name=fallback_name, opt_keys=opt_keys)
    if not structured:
        return sources.description

    sections: list[str] = [sources.description]
    if sources.when_to_use:
        sections.append(f"## When to use\n{sources.when_to_use}")
    if sources.returns:
        sections.append(f"## Returns\n{sources.returns}")
    if sources.agent_instructions:
        sections.append(f"## Instructions\n{sources.agent_instructions}")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# RFC 6570 Level 1 URI template helper
# ---------------------------------------------------------------------------

_VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class _Variable:
    name: str


@dataclass(frozen=True, slots=True)
class _Literal:
    text: str


Segment = _Variable | _Literal


def parse_template(template: str) -> list[Segment]:
    """Parse ``template`` into alternating literal + variable segments."""
    if template.count("{") != template.count("}"):
        msg = f"Unbalanced braces in template: {template!r}"
        raise ValueError(msg)

    segments: list[Segment] = []
    pos = 0
    for match in _VAR_RE.finditer(template):
        if match.start() > pos:
            segments.append(_Literal(template[pos : match.start()]))
        segments.append(_Variable(match.group(1)))
        pos = match.end()
    if pos < len(template):
        segments.append(_Literal(template[pos:]))

    for seg in segments:
        if isinstance(seg, _Literal) and ("{" in seg.text or "}" in seg.text):
            msg = f"Invalid variable in template: {template!r}"
            raise ValueError(msg)

    if not segments:
        msg = f"Empty template: {template!r}"
        raise ValueError(msg)
    return segments


def match_uri(template: str, uri: str) -> "dict[str, str] | None":
    """Match ``uri`` against ``template`` and extract variable values."""
    segments = parse_template(template)
    values: dict[str, str] = {}
    remaining = uri
    for i, seg in enumerate(segments):
        if isinstance(seg, _Literal):
            if not remaining.startswith(seg.text):
                return None
            remaining = remaining[len(seg.text) :]
            continue

        next_literal: str | None = next(
            (s.text for s in segments[i + 1 :] if isinstance(s, _Literal)),
            None,
        )
        if next_literal is None:
            value, rest = remaining, ""
        else:
            idx = remaining.find(next_literal)
            if idx < 0:
                return None
            value, rest = remaining[:idx], remaining[idx:]
        if not value or "/" in value:
            return None
        values[seg.name] = value
        remaining = rest

    if remaining:
        return None
    return values


def expand_template(template: str, values: "dict[str, str]") -> str:
    """Substitute ``{var}`` placeholders with values from ``values``."""
    for name in values:
        if not _IDENT_RE.match(name):
            msg = f"Invalid variable name: {name!r}"
            raise ValueError(msg)
    segments = parse_template(template)
    parts: list[str] = []
    for seg in segments:
        if isinstance(seg, _Literal):
            parts.append(seg.text)
        else:
            parts.append(values[seg.name])
    return "".join(parts)


__all__ = (
    "DescriptionSources",
    "MetadataRegistry",
    "expand_template",
    "extract_description_sources",
    "get_handler_function",
    "get_mcp_metadata",
    "match_uri",
    "mcp_prompt",
    "mcp_resource",
    "mcp_tool",
    "parse_template",
    "render_description",
    "should_include_handler",
)
