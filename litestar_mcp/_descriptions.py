"""Single source of truth for tool and resource description rendering.

Precedence (highest first, per field):
    1. ``handler.opt["mcp_<field>"]`` (for the description of a resource,
       the key is ``mcp_resource_description`` instead of ``mcp_description``
       so a handler that exposes both a tool and a resource on the same
       route can target each independently).
    2. Decorator metadata set by :func:`~litestar_mcp.decorators.mcp_tool`
       or :func:`~litestar_mcp.decorators.mcp_resource`.
    3. ``fn.__doc__`` (description only).
    4. Fallback ``"<kind.title()>: <name>"`` (description only).

Empty strings are treated as absent so a consumer that accidentally sets
``mcp_description=""`` does not clear the docstring fallback.

When any of the structured fields (``when_to_use``, ``returns``,
``agent_instructions``) is set, :func:`render_description` emits a
sectioned markdown string:

::

    <description>

    ## When to use
    <when_to_use>

    ## Returns
    <returns>

    ## Instructions
    <agent_instructions>

Sections appear only when their field is set; section order is fixed.
When no structured fields are set, the output is the plain description
string (unchanged from pre-Ch1 behaviour).

Callers that want plain output regardless — for example the CLI — pass
``structured=False``.
"""

from dataclasses import dataclass
from typing import Any, Literal

from litestar_mcp.decorators import get_mcp_metadata

Kind = Literal["tool", "resource"]

_STRUCTURED_FIELDS: tuple[str, str, str] = ("when_to_use", "returns", "agent_instructions")


@dataclass(frozen=True)
class DescriptionSources:
    """Resolved description fields for a handler.

    Attributes:
        description: The primary LLM-facing description (always set).
        when_to_use: Optional structured hint rendered as the
            ``## When to use`` section.
        returns: Optional return-shape hint rendered as the ``## Returns``
            section.
        agent_instructions: Optional mandatory-context block rendered as
            the ``## Instructions`` section.
    """

    description: str
    when_to_use: str | None
    returns: str | None
    agent_instructions: str | None


def _opt_key(field: str, kind: Kind) -> str:
    if field == "description" and kind == "resource":
        return "mcp_resource_description"
    return f"mcp_{field}"


def _clean(value: Any) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _read_field(handler: Any, fn: Any, field: str, kind: Kind) -> str | None:
    opt = getattr(handler, "opt", None) or {}
    opt_value = _clean(opt.get(_opt_key(field, kind)))
    if opt_value is not None:
        return opt_value

    metadata = get_mcp_metadata(handler) or get_mcp_metadata(fn) or {}
    return _clean(metadata.get(field))


def extract_description_sources(
    handler: Any,
    fn: Any,
    *,
    kind: Kind,
    fallback_name: str,
) -> DescriptionSources:
    """Resolve every description field for a handler.

    Args:
        handler: The Litestar route handler whose ``opt`` mapping may
            carry ``mcp_*`` overrides.
        fn: The underlying Python function (used for ``__doc__`` and as a
            secondary metadata-registry key).
        kind: Whether to resolve as ``"tool"`` or ``"resource"``.
        fallback_name: The final fallback used when no description source
            is set.

    Returns:
        A :class:`DescriptionSources` with ``description`` always populated.
    """
    description = _read_field(handler, fn, "description", kind)
    if description is None:
        doc = _clean(getattr(fn, "__doc__", None))
        description = doc if doc is not None else f"{kind.title()}: {fallback_name}"
    return DescriptionSources(
        description=description,
        when_to_use=_read_field(handler, fn, "when_to_use", kind),
        returns=_read_field(handler, fn, "returns", kind),
        agent_instructions=_read_field(handler, fn, "agent_instructions", kind),
    )


def render_description(
    handler: Any,
    fn: Any,
    *,
    kind: Kind,
    fallback_name: str,
    structured: bool = True,
) -> str:
    """Render the final description string for a handler.

    Args:
        handler: The Litestar route handler.
        fn: The underlying Python function.
        kind: ``"tool"`` or ``"resource"``.
        fallback_name: Fallback used when no description source is set.
        structured: When ``True`` (default), structured fields (when set)
            are appended as ``## When to use`` / ``## Returns`` /
            ``## Instructions`` sections. When ``False``, only the plain
            description source is returned — callers that render to plain
            terminals or clients that don't handle markdown pass ``False``.

    Returns:
        The rendered description string.
    """
    sources = extract_description_sources(handler, fn, kind=kind, fallback_name=fallback_name)
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
