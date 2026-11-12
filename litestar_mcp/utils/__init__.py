"""Utilities shared across the litestar-mcp package.

This module is the single home for handler-introspection helpers
(``get_handler_function``), discovery filtering (``should_include_handler``),
MCP metadata decorators (``@mcp_tool``, ``@mcp_resource``, ``@mcp_prompt``,
``get_mcp_metadata``, ``MetadataRegistry``), LLM-facing description
rendering (``render_description``, ``extract_description_sources``,
``DescriptionSources``), and the RFC 6570 Level 1 URI template helpers
(``classify_resource_uri``, ``parse_template``, ``match_uri``,
``expand_template``). Before v0.5.0
these lived in separate modules (``filters.py``, ``decorators.py``,
``_descriptions.py``, ``_uri_template.py``); they are now flattened into
this single module.
"""

import copy
import inspect
import re
import weakref
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, TypeVar, cast

from litestar.stores.registry import StoreRegistry

from litestar_mcp.mcp.config import MCPOptKeys

if TYPE_CHECKING:
    from litestar.config.app import AppConfig
    from litestar.handlers import BaseRouteHandler
    from litestar.stores.base import Store
    from typing_extensions import Self

    from litestar_mcp.mcp.config import MCPConfig

__all__ = (
    "DescriptionSources",
    "MetadataRegistry",
    "classify_resource_uri",
    "clean_icons",
    "clean_meta",
    "clean_title",
    "expand_template",
    "extract_description_sources",
    "get_all_mcp_metadata",
    "get_handler_function",
    "get_mcp_metadata",
    "match_uri",
    "mcp_prompt",
    "mcp_resource",
    "mcp_tool",
    "normalize_task_support",
    "parse_template",
    "render_description",
    "resolve_app_store",
    "set_mcp_metadata",
    "should_include_handler",
    "strip_docstring_sections",
    "validate_tool_descriptions",
)


F = TypeVar("F", bound=Callable[..., Any])
Kind = Literal["tool", "resource", "prompt"]
_DEFS_REF_PREFIX = "#/$defs/"
_STRUCTURED_FIELDS: "tuple[str, str, str]" = ("when_to_use", "returns", "agent_instructions")
_VAR_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_GOOGLE_HEADER_LOWER = frozenset(
    {
        "args:",
        "arguments:",
        "params:",
        "parameters:",
        "keyword args:",
        "keyword arguments:",
        "other parameters:",
        "returns:",
        "return:",
        "raises:",
        "yields:",
        "yield:",
        "notes:",
        "note:",
        "examples:",
        "example:",
        "attributes:",
        "references:",
        "see also:",
        "warnings:",
        "warning:",
        "todo:",
        "todos:",
    }
)
_NUMPY_SECTION_TITLES = frozenset(
    {
        "parameters",
        "args",
        "arguments",
        "other parameters",
        "returns",
        "yields",
        "raises",
        "attributes",
        "see also",
        "notes",
        "references",
        "examples",
        "warnings",
    }
)
_SPHINX_FIELD_RE = re.compile(
    r"^:(?:param|parameter|arg|argument|key|keyword|type|vartype|returns|return|rtype|raises|raise|except|exception)\b",
    re.IGNORECASE,
)


def strip_docstring_sections(docstring: "str | None") -> "str":
    """Return docstring summary and body prose with Google, NumPy, and Sphinx sections stripped."""
    if not docstring:
        return ""
    cleaned = inspect.cleandoc(docstring)
    lines = cleaned.splitlines()
    prose_lines: list[str] = []
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.lower() in _GOOGLE_HEADER_LOWER:
            break
        if _SPHINX_FIELD_RE.match(stripped):
            break
        if (
            stripped.lower() in _NUMPY_SECTION_TITLES
            and idx + 1 < len(lines)
            and re.match(r"^-{3,}\s*$", lines[idx + 1].strip())
        ):
            break
        prose_lines.append(line)
    return "\n".join(prose_lines).strip()


class MetadataRegistry:
    """Singleton registry for MCP metadata keyed weakly by callable identity and role."""

    _instance: "MetadataRegistry | None" = None
    _data: "weakref.WeakKeyDictionary[Any, dict[str, dict[str, Any]]]"
    _fallback: "dict[int, tuple[Any, dict[str, dict[str, Any]]]]"

    def __new__(cls) -> "Self":
        if cls._instance is None:
            inst = super().__new__(cls)
            inst._data = weakref.WeakKeyDictionary()
            inst._fallback = {}
            cls._instance = inst
        return cast("Self", cls._instance)

    def set(self, obj: "Any", value: "dict[str, Any]", *, kind: "Kind | str | None" = None) -> "None":
        """Store MCP metadata dictionary for `obj` under `kind`."""
        role = str(kind or value.get("type") or "tool")
        bucket = self._bucket(self._unwrap_target(obj), create=True)
        if bucket is not None:
            bucket[role] = value

    def get(self, obj: "Any", *, kind: "Kind | str | None" = None) -> "dict[str, Any] | None":
        """Retrieve the stored MCP metadata dictionary for `obj`, optionally filtered by `kind`."""
        bucket = self._bucket(self._unwrap_target(obj), create=False)
        if not bucket:
            return None
        if kind is not None:
            return bucket.get(str(kind))
        for role in ("tool", "resource", "prompt"):
            entry = bucket.get(role)
            if entry is not None:
                return entry
        return next(iter(bucket.values()), None)

    def get_all(self, obj: "Any") -> "list[dict[str, Any]]":
        """Return all stored role metadata dictionaries associated with `obj`."""
        bucket = self._bucket(self._unwrap_target(obj), create=False)
        if not bucket:
            return []
        return list(bucket.values())

    def _bucket(self, target: "Any", *, create: "bool") -> "dict[str, dict[str, Any]] | None":
        try:
            existing = self._data.get(target)
            if existing is None and create:
                existing = {}
                self._data[target] = existing
        except TypeError:
            entry = self._fallback.get(id(target))
            if entry is not None and entry[0] is target:
                return entry[1]
            if not create:
                return None
            created: dict[str, dict[str, Any]] = {}
            self._fallback[id(target)] = (target, created)
            return created
        else:
            return existing

    @staticmethod
    def _unwrap_target(obj: "Any") -> "Any":
        target = obj
        if hasattr(obj, "fn") and not isinstance(obj, type):
            target = obj.fn
            if not isinstance(target, type) and hasattr(target, "value") and callable(target.value):
                target = target.value

        seen: set[int] = set()
        sentinel = object()
        while id(target) not in seen:
            seen.add(id(target))
            for attr in ("__dishka_orig_func__", "__func__", "__wrapped__"):
                unwrapped = getattr(target, attr, sentinel)
                if unwrapped is not sentinel:
                    target = unwrapped
                    break
            else:
                break
        return target


_REGISTRY = MetadataRegistry()


@dataclass(frozen=True)
class DescriptionSources:
    """Resolved description fields for a handler."""

    description: "str"
    """The primary LLM-facing description (always set)."""
    when_to_use: "str | None"
    """Optional `## When to use` section."""
    returns: "str | None"
    """Optional `## Returns` section."""
    agent_instructions: "str | None"
    """Optional `## Instructions` section."""


def get_handler_function(handler: "BaseRouteHandler") -> "Callable[..., Any]":
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


def should_include_handler(name: "str", tags: "set[str]", config: "MCPConfig") -> "bool":
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


def normalize_task_support(value: "str | bool | None") -> "str | None":
    """Normalize a task support mode or soft alias into a canonical MCP mode.

    Args:
        value: Canonical mode (``"optional"``, ``"required"``, ``"forbidden"``)
            or soft alias (``True``, ``False``, ``"sync"``, ``"disabled"``,
            ``"inline"``).

    Returns:
        Canonical mode string (``"optional"``, ``"required"``, ``"forbidden"``)
        or ``None``.

    Raises:
        ValueError: When ``value`` is not a recognized task mode.
    """
    if value is None:
        return None
    if value is True or value == "optional":
        return "optional"
    if value == "required":
        return "required"
    if value is False or value in {"forbidden", "sync", "disabled", "inline"}:
        return "forbidden"
    msg = (
        "task_support must be one of 'optional', 'required', 'forbidden', 'sync', 'disabled', 'inline', True, or False"
    )
    raise ValueError(msg)


def mcp_tool(
    name: "str | None" = None,
    *,
    title: "str | None" = None,
    description: "str | None" = None,
    agent_instructions: "str | None" = None,
    when_to_use: "str | None" = None,
    returns: "str | None" = None,
    input_schema: "dict[str, Any] | None" = None,
    output_schema: "dict[str, Any] | bool | None" = None,
    annotations: "dict[str, Any] | None" = None,
    icons: "Sequence[dict[str, Any]] | None" = None,
    meta: "Mapping[str, Any] | None" = None,
    read_only: "bool | None" = None,
    scopes: "list[str] | None" = None,
    task: "str | bool | None" = None,
    task_support: "str | bool | None" = None,
    task_input_before_start: "bool" = False,
    queue: "str | bool | None" = None,
) -> "Callable[[F], F]":
    """Decorator to mark a route handler as an MCP tool.

    Args:
        name: The name of the MCP tool. Defaults to the function name.
        title: Optional human-readable display title for UI clients.
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
        input_schema: Optional explicit JSON Schema for the tool input.
        output_schema: Optional explicit object JSON Schema mapping for the
            tool's structured output, `True` to synthesize from the handler's
            return annotation, or `False` to opt out of synthesis.
        annotations: Optional metadata annotations (audience, priority, etc.).
        icons: Optional sequence of MCP icon descriptor dicts for UI display.
        meta: Optional MCP `_meta` descriptor mapping (including SEP-1865
            `ui` linkage and visibility fields).
        read_only: Optional shorthand setting ``annotations["readOnlyHint"]``.
        scopes: Optional list of OAuth scopes advertised as discovery
            metadata (surfaced under ``tools[].annotations.scopes`` in
            ``tools/list``). Scopes are **not** enforced inline — attach a
            Litestar ``Guard`` to the route / router / controller for
            authorization.
        task: Preferred task mode parameter. Accepts ``"optional"`` (or ``True``),
            ``"required"``, or ``"sync"`` / ``"disabled"`` / ``"inline"`` /
            ``"forbidden"`` / ``False``.
        task_support: Optional task support mode alias for ``task``.
        task_input_before_start: Execute the first call synchronously so an
            MRTR input-required result can be completed before creating the
            task on the retry.
        queue: Optional target ``litestar-queues`` queue name for this tool,
            or ``False`` / ``"in_process"`` to force in-process background task
            execution.

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
        an explicit ``input_schema`` / ``output_schema``, ``annotations``,
        ``scopes``, or task/MRTR policy without mixing more keys into
        ``handler.opt``.
    """
    validated_output_schema = _validate_output_schema_value(output_schema)

    def decorator(fn: "F") -> "F":
        metadata: dict[str, Any] = {"type": "tool", "name": name or _callable_name(fn)}
        _apply_descriptor_metadata(metadata, title=title, icons=icons, meta=meta)
        _apply_non_none_fields(
            metadata,
            {
                "description": description,
                "agent_instructions": agent_instructions,
                "when_to_use": when_to_use,
                "returns": returns,
                "input_schema": copy.deepcopy(dict(input_schema)) if input_schema is not None else None,
            },
        )
        if validated_output_schema is not None:
            metadata["output_schema"] = (
                copy.deepcopy(validated_output_schema)
                if isinstance(validated_output_schema, dict)
                else validated_output_schema
            )
        if annotations is not None or read_only is not None:
            merged_annotations: dict[str, Any] = copy.deepcopy(dict(annotations)) if annotations else {}
            if read_only is not None:
                merged_annotations["readOnlyHint"] = bool(read_only)
            metadata["annotations"] = merged_annotations
            if read_only is not None:
                metadata["read_only"] = bool(read_only)
        if scopes is not None:
            metadata["scopes"] = scopes
        raw_task = task if task is not None else task_support
        if raw_task is not None:
            metadata["task_support"] = normalize_task_support(raw_task)
        if task_input_before_start:
            metadata["task_input_before_start"] = True
        if queue is not None:
            metadata["queue"] = _validate_tool_queue(queue)
        _REGISTRY.set(fn, metadata, kind="tool")
        return fn

    return decorator


def mcp_resource(
    name: "str | None" = None,
    *,
    uri: "str | None" = None,
    uri_template: "str | None" = None,
    mime_type: "str | None" = None,
    title: "str | None" = None,
    description: "str | None" = None,
    icons: "Sequence[dict[str, Any]] | None" = None,
    meta: "Mapping[str, Any] | None" = None,
    agent_instructions: "str | None" = None,
    when_to_use: "str | None" = None,
    returns: "str | None" = None,
) -> "Callable[[F], F]":
    """Decorator to mark a route handler as an MCP resource.

    Args:
        name: The name of the MCP resource. Defaults to the function name.
        uri: Optional concrete resource URI (for example
            `"ui://app/dashboard.html"`) or RFC 6570 Level 1 URI template
            (for example `"app://items/{item_id}"`). Classified via
            `classify_resource_uri` into static `resource_uri` metadata or
            templated `resource_template` metadata. Cannot be combined with
            `uri_template`.
        uri_template: Optional RFC 6570 Level 1 URI template
            (for example `"app://workspaces/{workspace_id}/files/{file_id}"`).
            Concrete URIs matching the template dispatch to this handler
            with extracted variables passed as kwargs. Brace-free values are
            classified as static resource URIs. Cannot be combined with `uri`.
        mime_type: Optional resource MIME type advertised in list responses
            and used for resource reads.
        title: Optional human-readable display title for UI clients.
        description: LLM-facing description. Overrides `fn.__doc__`.
        icons: Optional sequence of MCP icon descriptor dicts for UI display.
        meta: Optional MCP `_meta` descriptor mapping emitted in list and read
            responses.
        agent_instructions: Mandatory-context block rendered in the
            `## Instructions` section.
        when_to_use: Optional structured hint rendered as the
            `## When to use` section.
        returns: Optional return-shape hint rendered as the `## Returns`
            section.

    Returns:
        Decorator function that adds MCP metadata to the handler.

    Example:
        Pass MCP metadata straight through to the route decorator —
        Litestar funnels unknown kwargs into `handler.opt`:

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
    static_uri, template_uri = classify_resource_uri(uri=uri, uri_template=uri_template)

    def decorator(fn: "F") -> "F":
        metadata: dict[str, Any] = {"type": "resource", "name": name or _callable_name(fn)}
        _apply_non_none_fields(
            metadata,
            {
                "resource_uri": static_uri,
                "resource_template": template_uri,
                "mime_type": mime_type,
                "description": description,
                "agent_instructions": agent_instructions,
                "when_to_use": when_to_use,
                "returns": returns,
            },
        )
        _apply_descriptor_metadata(metadata, title=title, icons=icons, meta=meta)
        _REGISTRY.set(fn, metadata, kind="resource")
        return fn

    return decorator


def mcp_prompt(
    name: "str | None" = None,
    *,
    title: "str | None" = None,
    description: "str | None" = None,
    arguments: "list[dict[str, Any]] | None" = None,
    icons: "Sequence[dict[str, Any]] | None" = None,
    meta: "Mapping[str, Any] | None" = None,
) -> "Callable[[F], F]":
    r"""Decorator to mark a callable as an MCP prompt template.

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
            dict with ``name`` (required), and optional ``title``,
            ``description``, and ``required`` keys. ``title`` is the
            human-readable display name from the MCP ``BaseMetadata`` mixin
            (a hint for UI clients; not used for matching). When omitted,
            the argument list is derived automatically from the decorated
            function's signature and Google-style docstring — note that
            signature introspection cannot infer ``title``, so set it
            explicitly when needed.
        icons: Optional sequence of icon objects for UI display. Each entry is
            a dict with ``src`` (URL), ``mimeType``, and optionally ``sizes``
            per the MCP spec.
        meta: Optional MCP `_meta` descriptor mapping emitted in `prompts/list`.

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

    def decorator(fn: "F") -> "F":
        metadata: dict[str, Any] = {"type": "prompt", "name": name or _callable_name(fn)}
        _apply_descriptor_metadata(metadata, title=title, icons=icons, meta=meta)
        _apply_non_none_fields(metadata, {"description": description, "arguments": arguments})
        _REGISTRY.set(fn, metadata, kind="prompt")
        return fn

    return decorator


def get_mcp_metadata(obj: "Any", *, kind: "Kind | str | None" = None) -> "dict[str, Any] | None":
    """Get MCP metadata for an object if it exists.

    Args:
        obj: Object to check for MCP metadata.
        kind: Optional MCP primitive role (`"tool"`, `"resource"`, or `"prompt"`)
            used to isolate stacked role metadata.

    Returns:
        MCP metadata dictionary or `None` if not present for the requested role.
    """
    registered = _REGISTRY.get(obj, kind=kind)
    if registered is not None:
        return registered
    if kind in (None, "tool"):
        return _external_tool_metadata(obj)
    return None


def get_all_mcp_metadata(obj: "Any") -> "list[dict[str, Any]]":
    """Return all MCP role metadata dictionaries registered on an object.

    Args:
        obj: Callable or route handler to inspect.

    Returns:
        Ordered list of role metadata dictionaries associated with `obj`.
    """
    entries = list(_REGISTRY.get_all(obj))
    if _REGISTRY.get(obj, kind="tool") is None:
        ext_meta = _external_tool_metadata(obj)
        if ext_meta is not None:
            entries.append(ext_meta)
    return entries


def set_mcp_metadata(
    obj: "Any",
    metadata: "dict[str, Any]",
    *,
    kind: "Kind | str | None" = None,
) -> "None":
    """Set MCP metadata for an object in the registry.

    Args:
        obj: Object to register MCP metadata for.
        metadata: MCP metadata dictionary.
        kind: Optional explicit MCP primitive role override.
    """
    _REGISTRY.set(obj, metadata, kind=kind)


def extract_description_sources(
    handler: "Any",
    fn: "Any",
    *,
    kind: "Kind",
    fallback_name: "str",
    opt_keys: "MCPOptKeys | None" = None,
) -> "DescriptionSources":
    """Resolve every description field for a handler."""
    keys = opt_keys if opt_keys is not None else MCPOptKeys()
    description = _read_field(handler, fn, "description", kind, keys)
    if description is None:
        handler_desc = _clean(getattr(handler, "description", None)) or _clean(getattr(handler, "summary", None))
        if handler_desc is not None:
            description = handler_desc
        else:
            raw_doc = getattr(fn, "__doc__", None)
            doc = _clean(strip_docstring_sections(raw_doc)) if isinstance(raw_doc, str) else None
            description = doc if doc is not None else f"{kind.title()}: {fallback_name}"
    return DescriptionSources(
        description=description,
        when_to_use=_read_field(handler, fn, "when_to_use", kind, keys),
        returns=_read_field(handler, fn, "returns", kind, keys),
        agent_instructions=_read_field(handler, fn, "agent_instructions", kind, keys),
    )


def validate_tool_descriptions(
    tools: "Sequence[Mapping[str, Any]]",
    *,
    max_tool_description_length: "int | None" = 1000,
    max_param_description_length: "int | None" = 750,
    max_output_field_description_length: "int | None" = 300,
) -> "list[str]":
    """Validate tool, input parameter, and output schema field description lengths."""
    violations: list[str] = []
    for tool_entry in tools:
        tool_name = str(tool_entry.get("name", "<unnamed>"))
        tool_desc = tool_entry.get("description")
        if (
            max_tool_description_length is not None
            and isinstance(tool_desc, str)
            and len(tool_desc) > max_tool_description_length
        ):
            violations.append(
                f"Tool '{tool_name}' description length {len(tool_desc)} exceeds maximum {max_tool_description_length}"
            )
        input_schema = tool_entry.get("inputSchema")
        if max_param_description_length is not None and isinstance(input_schema, Mapping):
            input_defs = input_schema.get("$defs")
            resolved_input_defs = input_defs if isinstance(input_defs, Mapping) else {}
            seen_input: set[tuple[str, int]] = set()
            for label, param_desc in _iter_schema_field_descriptions(input_schema, resolved_input_defs):
                desc_len = len(param_desc)
                if desc_len > max_param_description_length and (label, desc_len) not in seen_input:
                    seen_input.add((label, desc_len))
                    violations.append(
                        f"Tool '{tool_name}' parameter '{label}' description length {desc_len} "
                        f"exceeds maximum {max_param_description_length}"
                    )
        output_schema = tool_entry.get("outputSchema")
        if max_output_field_description_length is not None and isinstance(output_schema, Mapping):
            output_defs = output_schema.get("$defs")
            resolved_output_defs = output_defs if isinstance(output_defs, Mapping) else {}
            seen_output: set[tuple[str, int]] = set()
            for label, field_desc in _iter_schema_field_descriptions(output_schema, resolved_output_defs):
                desc_len = len(field_desc)
                if desc_len > max_output_field_description_length and (label, desc_len) not in seen_output:
                    seen_output.add((label, desc_len))
                    violations.append(
                        f"Tool '{tool_name}' output field '{label}' description length {desc_len} "
                        f"exceeds maximum {max_output_field_description_length}"
                    )
    return violations


def render_description(
    handler: "Any",
    fn: "Any",
    *,
    kind: "Kind",
    fallback_name: "str",
    structured: "bool" = True,
    opt_keys: "MCPOptKeys | None" = None,
) -> "str":
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


def classify_resource_uri(
    *,
    uri: "str | None" = None,
    uri_template: "str | None" = None,
) -> "tuple[str | None, str | None]":
    """Validate and classify a resource URI or template into `(static_uri, template_uri)`.

    Args:
        uri: Optional concrete resource URI or RFC 6570 Level 1 URI template.
        uri_template: Optional RFC 6570 Level 1 URI template or brace-free URI.

    Returns:
        A `(static_uri, template_uri)` pair where at most one element is a
        non-empty string and both are `None` when neither input is provided.

    Raises:
        ValueError: If both `uri` and `uri_template` are provided, or if the
            supplied string is empty, whitespace-only, or contains malformed
            template braces.
    """
    if uri is not None and uri_template is not None:
        msg = "Cannot specify both 'uri' and 'uri_template'"
        raise ValueError(msg)
    raw = uri if uri is not None else uri_template
    if raw is None:
        return None, None
    if not isinstance(raw, str) or not raw.strip():
        msg = f"Invalid resource URI: {raw!r}"
        raise ValueError(msg)
    segments = parse_template(raw)
    if any(isinstance(seg, _Variable) for seg in segments):
        return None, raw
    return raw, None


def parse_template(template: "str") -> "list[Segment]":
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


def match_uri(template: "str", uri: "str") -> "dict[str, str] | None":
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


def expand_template(template: "str", values: "dict[str, str]") -> "str":
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


def resolve_app_store(app_config: "AppConfig", name: "str") -> "Store":
    """Return the named store from the application's store registry.

    Integrations call this from ``on_app_init`` when the user did not pass an
    explicit store, so configuring ``Litestar(stores=...)`` once (for example a
    ``StoreRegistry`` whose ``default_factory`` returns namespaced Redis stores)
    covers them too. A plain ``stores`` mapping is promoted to the
    ``StoreRegistry`` that Litestar would build from it, so the integration and
    ``app.stores`` share the same instance.

    Args:
        app_config: Application configuration being initialized.
        name: Store registry name.

    Returns:
        The registered store, or the registry's default store for ``name``.
    """
    stores = app_config.stores
    registry = stores if isinstance(stores, StoreRegistry) else StoreRegistry(stores)
    app_config.stores = registry
    return registry.get(name)


def _validate_output_schema_value(output_schema: "Any") -> "dict[str, Any] | bool | None":
    """Validate an explicit `output_schema` policy passed to `@mcp_tool`."""
    if output_schema is None:
        return None
    if isinstance(output_schema, bool):
        return output_schema
    if not isinstance(output_schema, Mapping):
        msg = "output_schema must be a mapping, bool, or None"
        raise TypeError(msg)

    normalized: dict[str, Any] = copy.deepcopy(dict(output_schema))
    raw_defs = normalized.get("$defs")
    defs: Mapping[str, Any] = raw_defs if isinstance(raw_defs, Mapping) else {}
    current: Mapping[str, Any] = normalized
    visited_refs: frozenset[str] = frozenset()
    while "$ref" in current and "type" not in current:
        resolved = _resolve_local_schema_ref(current, defs, visited_refs)
        if resolved is None:
            break
        ref, _, target = resolved
        visited_refs = visited_refs | {ref}
        current = target

    if current.get("type") != "object":
        msg = "output_schema mapping must resolve to root type 'object'"
        raise ValueError(msg)
    return normalized


def _resolve_local_schema_ref(
    schema: "Mapping[str, Any]",
    defs: "Mapping[str, Any]",
    visited_refs: "frozenset[str]",
) -> "tuple[str, str, Mapping[str, Any]] | None":
    """Resolve a local `#/$defs/<Name>` pointer against `defs` when not yet visited."""
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith(_DEFS_REF_PREFIX) or ref in visited_refs:
        return None
    def_name = ref[len(_DEFS_REF_PREFIX) :]
    target = defs.get(def_name)
    if not isinstance(target, Mapping):
        return None
    return ref, def_name, target


def _iter_schema_field_descriptions(
    schema: "Mapping[str, Any]",
    defs: "Mapping[str, Any]",
    *,
    path: "tuple[str, ...]" = (),
    visited_refs: "frozenset[str]" = frozenset(),
    seen_defs: "set[str] | None" = None,
) -> "list[tuple[str, str]]":
    """Return `(dotted_field_path, description)` pairs across nested schema nodes and `$defs`."""
    is_root = path == () and not visited_refs and seen_defs is None
    active_seen_defs = seen_defs if seen_defs is not None else set()
    results: list[tuple[str, str]] = []

    resolved_ref = _resolve_local_schema_ref(schema, defs, visited_refs)
    if resolved_ref is not None:
        ref, def_name, target = resolved_ref
        active_seen_defs.add(def_name)
        results.extend(
            _iter_schema_field_descriptions(
                target,
                defs,
                path=path,
                visited_refs=visited_refs | {ref},
                seen_defs=active_seen_defs,
            )
        )

    props = schema.get("properties")
    if isinstance(props, Mapping):
        for prop_name, prop_schema in props.items():
            if not isinstance(prop_schema, Mapping):
                continue
            prop_path = (*path, str(prop_name))
            prop_desc = prop_schema.get("description")
            if isinstance(prop_desc, str):
                results.append((".".join(prop_path), prop_desc))
            results.extend(
                _iter_schema_field_descriptions(
                    prop_schema,
                    defs,
                    path=prop_path,
                    visited_refs=visited_refs,
                    seen_defs=active_seen_defs,
                )
            )

    items_schema = schema.get("items")
    if isinstance(items_schema, Mapping):
        items_desc = items_schema.get("description")
        if isinstance(items_desc, str) and "$ref" not in items_schema:
            items_path = (*path, "[]") if path else ("[]",)
            results.append((".".join(items_path), items_desc))
        results.extend(
            _iter_schema_field_descriptions(
                items_schema,
                defs,
                path=path,
                visited_refs=visited_refs,
                seen_defs=active_seen_defs,
            )
        )

    additional_props = schema.get("additionalProperties")
    if isinstance(additional_props, Mapping):
        results.extend(
            _iter_schema_field_descriptions(
                additional_props,
                defs,
                path=path,
                visited_refs=visited_refs,
                seen_defs=active_seen_defs,
            )
        )

    for key in ("prefixItems", "anyOf", "oneOf", "allOf"):
        branches = schema.get(key)
        if isinstance(branches, Sequence) and not isinstance(branches, (str, bytes)):
            for branch in branches:
                if isinstance(branch, Mapping):
                    results.extend(
                        _iter_schema_field_descriptions(
                            branch,
                            defs,
                            path=path,
                            visited_refs=visited_refs,
                            seen_defs=active_seen_defs,
                        )
                    )

    if is_root:
        for def_name, def_schema in defs.items():
            def_key = str(def_name)
            if def_key not in active_seen_defs and isinstance(def_schema, Mapping):
                active_seen_defs.add(def_key)
                results.extend(
                    _iter_schema_field_descriptions(
                        def_schema,
                        defs,
                        path=(def_key,),
                        visited_refs=frozenset({f"{_DEFS_REF_PREFIX}{def_key}"}),
                        seen_defs=active_seen_defs,
                    )
                )

    return results


def _callable_name(fn: "Any") -> "str":
    """Return the name of a plain callable or of the function wrapped by a route handler."""
    plain_name = getattr(fn, "__name__", None)
    if isinstance(plain_name, str):
        return plain_name
    return str(get_handler_function(fn).__name__)


def _clean(value: "Any") -> "str | None":
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _validate_tool_queue(queue: "object") -> "str | bool":
    if queue is True or not isinstance(queue, (str, bool)) or (isinstance(queue, str) and not queue.strip()):
        msg = "queue must be a non-empty queue name string, 'in_process', or False"
        raise ValueError(msg)
    return queue


def clean_title(value: "Any") -> "str | None":
    """Return stripped string title when non-empty, otherwise ``None``."""
    return _clean(value)


def clean_icons(value: "Any") -> "list[dict[str, Any]] | None":
    """Return deep-copied mapping entries from an icon sequence, or ``None``."""
    if not value or isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        return None
    copied = [copy.deepcopy(dict(item)) for item in value if isinstance(item, Mapping)]
    return copied or None


def clean_meta(value: "Any") -> "dict[str, Any] | None":
    """Return a deep-copied descriptor ``_meta`` mapping when non-empty, or ``None``."""
    if not value or not isinstance(value, Mapping):
        return None
    copied = copy.deepcopy(dict(value))
    return copied or None


def _apply_descriptor_metadata(
    metadata: "dict[str, Any]",
    *,
    title: "str | None" = None,
    icons: "Sequence[dict[str, Any]] | None" = None,
    meta: "Mapping[str, Any] | None" = None,
) -> "None":
    cleaned_title = clean_title(title)
    if cleaned_title is not None:
        metadata["title"] = cleaned_title
    cleaned_icons = clean_icons(icons)
    if cleaned_icons is not None:
        metadata["icons"] = cleaned_icons
    cleaned_meta = clean_meta(meta)
    if cleaned_meta is not None:
        metadata["meta"] = cleaned_meta


def _apply_non_none_fields(metadata: "dict[str, Any]", fields: "Mapping[str, Any]") -> "None":
    for key, val in fields.items():
        if val is not None:
            metadata[key] = val


def _external_tool_metadata(obj: "Any") -> "dict[str, Any] | None":
    target = get_handler_function(obj) if hasattr(obj, "fn") else obj
    ext_meta = getattr(target, "_mcp_tool_meta", None)
    if isinstance(ext_meta, Mapping):
        normalized: dict[str, Any] = {
            "type": "tool",
            **ext_meta,
            "name": str(ext_meta.get("name") or _callable_name(target)),
        }
        raw_task = normalized.pop("task", None)
        if raw_task is None:
            raw_task = normalized.get("task_support")
        if raw_task is not None:
            normalized["task_support"] = normalize_task_support(raw_task)
        return normalized
    return None


def _read_field(
    handler: "Any",
    fn: "Any",
    field_name: "str",
    kind: "Kind",
    opt_keys: "MCPOptKeys",
) -> "str | None":
    opt = getattr(handler, "opt", None) or {}
    opt_value = _clean(opt.get(opt_keys.for_field(field_name, kind)))
    if opt_value is not None:
        return opt_value
    metadata = get_mcp_metadata(handler, kind=kind) or get_mcp_metadata(fn, kind=kind) or {}
    return _clean(metadata.get(field_name))


@dataclass(frozen=True, slots=True)
class _Variable:
    name: "str"


@dataclass(frozen=True, slots=True)
class _Literal:
    text: "str"


Segment = _Variable | _Literal
