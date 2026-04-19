"""Minimal RFC 6570 Level 1 URI template helper.

Supports simple string substitution: ``{var}``. Variable names must be
valid Python identifiers. Variables do NOT cross ``/`` — ``{id}`` in
``app://w/{id}`` will not match ``app://w/1/2``.

Level 2+ operators (``{+var}``, ``{?var}``, ``{&var}``, etc.) are not
supported; they are tracked as a follow-up.
"""

import re
from dataclasses import dataclass

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
    """Parse ``template`` into alternating literal + variable segments.

    Raises:
        ValueError: On unbalanced braces, empty ``{}``, or an invalid
            variable name.
    """
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

    # Any stray `{` or `}` in literal segments means the template contained
    # malformed syntax that the variable regex did not capture (e.g. `{}`,
    # `{0foo}`, unbalanced).
    for seg in segments:
        if isinstance(seg, _Literal) and ("{" in seg.text or "}" in seg.text):
            msg = f"Invalid variable in template: {template!r}"
            raise ValueError(msg)

    if not segments:
        msg = f"Empty template: {template!r}"
        raise ValueError(msg)
    return segments


def match_uri(template: str, uri: str) -> "dict[str, str] | None":
    """Match ``uri`` against ``template`` and extract variable values.

    Returns ``None`` when the URI does not match. ``{var}`` matches a
    non-empty run of characters that does NOT cross ``/``.
    """
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
    """Substitute ``{var}`` placeholders with values from ``values``.

    Raises:
        KeyError: If ``values`` is missing a required variable.
        ValueError: If a provided variable name is not a valid identifier.
    """
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
