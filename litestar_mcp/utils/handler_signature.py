"""Helpers for Litestar handler argument advertisement."""

import contextlib
import inspect
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from litestar.constants import RESERVED_KWARGS
from litestar.handlers import BaseRouteHandler

from litestar_mcp.schema_builder import iter_dependency_input_parameters, parameter_aliases
from litestar_mcp.utils import get_handler_function


@dataclass(frozen=True, slots=True)
class AdvertisedHandlerParameter:
    """Handler parameter metadata relevant to MCP callers."""

    python_name: str
    wire_name: str
    annotation: Any
    required: bool
    description: str | None = None


_GOOGLE_SECTION_HEADERS = frozenset(
    {
        "Args:",
        "Arguments:",
        "Params:",
        "Parameters:",
        "Returns:",
        "Return:",
        "Raises:",
        "Yields:",
        "Yield:",
        "Notes:",
        "Note:",
        "Examples:",
        "Example:",
        "Attributes:",
        "References:",
        "See Also:",
        "Warnings:",
        "Warning:",
        "Todo:",
        "Todos:",
    }
)


def _parse_docstring_args(docstring: str | None) -> dict[str, str]:
    """Extract parameter descriptions from a Google-style docstring."""
    if not docstring:
        return {}
    lines = docstring.splitlines()
    in_args = False
    result: dict[str, str] = {}
    current_name: str | None = None
    current_desc: list[str] = []
    param_indent: int | None = None
    param_re = re.compile(r"^(\s+)(\w+)(?:\s*\([^)]*\))?\s*:\s*(.*)$")

    for line in lines:
        stripped = line.strip()
        if stripped in ("Args:", "Arguments:", "Params:", "Parameters:"):
            in_args = True
            continue
        if not in_args:
            continue
        if stripped and not line[0].isspace():
            break
        if stripped in _GOOGLE_SECTION_HEADERS:
            break

        line_indent = len(line) - len(line.lstrip())
        match = param_re.match(line)
        is_continuation = (
            current_name is not None
            and stripped
            and (match is None or (param_indent is not None and line_indent > param_indent))
        )

        if match and not is_continuation:
            if current_name is not None:
                result[current_name] = " ".join(current_desc).strip()
            current_name = match.group(2)
            current_desc = [match.group(3)] if match.group(3) else []
            if param_indent is None:
                param_indent = line_indent
        elif is_continuation:
            current_desc.append(stripped)
        elif current_name is not None and not stripped:
            pass

    if current_name is not None:
        result[current_name] = " ".join(current_desc).strip()
    return result


def _path_parameter_names(path_parameters: Mapping[str, Any] | Iterable[str] | None) -> frozenset[str]:
    if path_parameters is None:
        return frozenset()
    if isinstance(path_parameters, Mapping):
        return frozenset(path_parameters)
    return frozenset(path_parameters)


def get_advertised_handler_parameters(
    handler: BaseRouteHandler,
    *,
    path_parameters: Mapping[str, Any] | Iterable[str] | None = None,
) -> list[AdvertisedHandlerParameter]:
    """Return handler parameters that MCP callers can supply."""
    try:
        parsed_parameters = handler.parsed_fn_signature.parameters
    except Exception:  # noqa: BLE001
        return []

    di_params: set[str] = set()
    with contextlib.suppress(AttributeError, TypeError):
        di_params = set(handler.resolve_dependencies().keys())

    skipped_names = set(RESERVED_KWARGS) | di_params | set(_path_parameter_names(path_parameters))
    aliases = parameter_aliases(handler)
    python_to_wire = {python_name: wire_name for wire_name, python_name in aliases.items()}
    fn = get_handler_function(handler)
    doc_descriptions = _parse_docstring_args(getattr(fn, "__doc__", None))

    advertised: list[AdvertisedHandlerParameter] = []
    advertised_names: set[str] = set()
    for name, definition in parsed_parameters.items():
        if name == "self" or name in skipped_names:
            continue
        wire_name = python_to_wire.get(name, name)
        description = doc_descriptions.get(name) or doc_descriptions.get(wire_name)
        advertised.append(
            AdvertisedHandlerParameter(
                python_name=name,
                wire_name=wire_name,
                annotation=getattr(definition, "annotation", Any),
                required=bool(getattr(definition, "is_required", False)),
                description=description or None,
            )
        )
        advertised_names.add(name)

    path_param_names = _path_parameter_names(path_parameters)
    for name, param in iter_dependency_input_parameters(handler):
        if name in advertised_names or name in path_param_names:
            continue
        wire_name = python_to_wire.get(name, name)
        description = doc_descriptions.get(name) or doc_descriptions.get(wire_name)
        advertised.append(
            AdvertisedHandlerParameter(
                python_name=name,
                wire_name=wire_name,
                annotation=param.annotation,
                required=param.default is inspect.Parameter.empty,
                description=description or None,
            )
        )
        advertised_names.add(name)
    return advertised


def extract_advertised_handler_arguments(
    handler: BaseRouteHandler,
    *,
    path_parameters: Mapping[str, Any] | Iterable[str] | None = None,
) -> list[dict[str, Any]]:
    """Return MCP prompt-style argument dicts for a Litestar handler."""
    args: list[dict[str, Any]] = []
    for parameter in get_advertised_handler_parameters(handler, path_parameters=path_parameters):
        arg: dict[str, Any] = {"name": parameter.wire_name}
        if parameter.description:
            arg["description"] = parameter.description
        arg["required"] = parameter.required
        args.append(arg)
    return args
