"""Helpers for Litestar handler argument advertisement."""

import contextlib
import inspect
import logging
import re
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import UnionType
from typing import TYPE_CHECKING, Annotated, Any, Union, get_args, get_origin, get_type_hints

from litestar.constants import RESERVED_KWARGS
from litestar.params import ParameterKwarg

from litestar_mcp.typing import DISHKA_INSTALLED, DishkaDependencyKey
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler

_logger = logging.getLogger(__name__)

_EXECUTION_CONTEXT_PARAMS = {"resolved_user", "user_claims"}
_ADVERTISED_RESERVED_KWARGS = set(RESERVED_KWARGS) - {"data"}


@dataclass(frozen=True, slots=True)
class AdvertisedHandlerParameter:
    """Handler parameter metadata relevant to MCP callers."""

    python_name: "str"
    wire_name: "str"
    annotation: "Any"
    required: "bool"
    default: "Any" = inspect.Parameter.empty
    description: "str | None" = None


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


def _parse_docstring_args(docstring: "str | None") -> "dict[str, str]":
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


def _path_parameter_names(path_parameters: "Mapping[str, Any] | Iterable[str] | None") -> "frozenset[str]":
    if path_parameters is None:
        return frozenset()
    if isinstance(path_parameters, Mapping):
        return frozenset(path_parameters)
    return frozenset(path_parameters)


def get_advertised_handler_parameters(
    handler: "BaseRouteHandler",
    *,
    path_parameters: "Mapping[str, Any] | Iterable[str] | None" = None,
) -> "list[AdvertisedHandlerParameter]":
    """Return handler parameters that MCP callers can supply."""
    try:
        parsed_parameters = handler.parsed_fn_signature.parameters
    except Exception:  # noqa: BLE001
        return []

    di_params: set[str] = set()
    with contextlib.suppress(AttributeError, TypeError):
        di_params = set(handler.resolve_dependencies().keys())

    skipped_names = _ADVERTISED_RESERVED_KWARGS | di_params | set(_path_parameter_names(path_parameters))
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
                annotation=getattr(definition, "raw", getattr(definition, "annotation", Any)),
                required=bool(getattr(definition, "is_required", False)),
                default=getattr(definition, "default", inspect.Parameter.empty),
                description=description or None,
            )
        )
        advertised_names.add(name)

    path_param_names = _path_parameter_names(path_parameters)
    for name, param in iter_dependency_input_parameters(handler, path_param_names=path_param_names):
        if name in advertised_names:
            continue
        wire_name = python_to_wire.get(name, name)
        description = doc_descriptions.get(name) or doc_descriptions.get(wire_name)
        advertised.append(
            AdvertisedHandlerParameter(
                python_name=name,
                wire_name=wire_name,
                annotation=param.annotation,
                required=param.default is inspect.Parameter.empty,
                default=param.default,
                description=description or None,
            )
        )
        advertised_names.add(name)
    return advertised


def extract_advertised_handler_arguments(
    handler: "BaseRouteHandler",
    *,
    path_parameters: "Mapping[str, Any] | Iterable[str] | None" = None,
) -> "list[dict[str, Any]]":
    """Return MCP prompt-style argument dicts for a Litestar handler."""
    args: list[dict[str, Any]] = []
    for parameter in get_advertised_handler_parameters(handler, path_parameters=path_parameters):
        arg: dict[str, Any] = {"name": parameter.wire_name}
        if parameter.description:
            arg["description"] = parameter.description
        arg["required"] = parameter.required
        args.append(arg)
    return args


def iter_dependency_input_parameters(
    handler: "BaseRouteHandler",
    *,
    path_param_names: "Iterable[str] | None" = None,
) -> "list[tuple[str, inspect.Parameter]]":
    """Walk dependency providers and yield their user-input params."""
    try:
        top_deps = dict(handler.resolve_dependencies())
    except Exception as exc:  # noqa: BLE001
        handler_name = getattr(get_handler_function(handler), "__name__", "<handler>")
        _logger.warning(
            "Failed to resolve dependencies for handler %r; provider params will be omitted from MCP schema: %s",
            handler_name,
            exc,
        )
        return []
    if not top_deps:
        return []

    dep_names: set[str] = set(top_deps)
    path_skip: set[str] = set(path_param_names) if path_param_names else set()
    framework_skip = RESERVED_KWARGS | _EXECUTION_CONTEXT_PARAMS | path_skip
    dishka_container = _handler_dishka_container(handler)

    visited: set[int] = set()
    seen_names: set[str] = set()
    collected: list[tuple[str, inspect.Parameter]] = []
    queue: deque[Any] = deque(top_deps.values())

    while queue:
        provide = queue.popleft()
        provider_fn = getattr(provide, "dependency", None)
        if provider_fn is None:
            continue
        provider_id = id(provider_fn)
        if provider_id in visited:
            continue
        visited.add(provider_id)
        try:
            provider_sig = inspect.signature(provider_fn)
        except (TypeError, ValueError) as exc:
            _logger.debug(
                "Skipping provider %r: signature introspection failed (%s).",
                getattr(provider_fn, "__name__", repr(provider_fn)),
                exc,
            )
            continue

        try:
            resolved_hints = get_type_hints(provider_fn, include_extras=True)
        except Exception:  # noqa: BLE001
            resolved_hints = {}

        for pname, param in provider_sig.parameters.items():
            if not _should_collect_dependency_parameter(
                pname,
                param,
                framework_skip=framework_skip,
                dep_names=dep_names,
                top_deps=top_deps,
                queue=queue,
                seen_names=seen_names,
            ):
                continue
            param = _resolve_provider_parameter_annotation(pname, param, resolved_hints)
            if _dishka_can_resolve(dishka_container, param.annotation):
                continue
            collected.append((pname, param))
    return collected


def parameter_aliases(handler: "BaseRouteHandler") -> "dict[str, str]":
    """Return ``{wire_name: python_name}`` for handler params whose wire name differs."""
    try:
        fn = get_handler_function(handler)
    except AttributeError:
        fn = handler

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {}

    try:
        parsed_parameters = handler.parsed_fn_signature.parameters
    except Exception:  # noqa: BLE001
        parsed_parameters = {}

    handler_name = getattr(fn, "__name__", "<handler>")
    try:
        resolved_hints = get_type_hints(fn, include_extras=True)
    except Exception:  # noqa: BLE001
        resolved_hints = {}
    aliases: dict[str, str] = {}
    for python_name, param in sig.parameters.items():
        parsed_parameter = parsed_parameters.get(python_name)
        if parsed_parameter is not None:
            param = param.replace(
                annotation=getattr(parsed_parameter, "raw", getattr(parsed_parameter, "annotation", param.annotation))
            )
        elif python_name in resolved_hints:
            param = param.replace(annotation=resolved_hints[python_name])
        _record_alias(aliases, python_name, _wire_name_for(python_name, param), handler_name)
    for python_name, param in iter_dependency_input_parameters(handler):
        _record_alias(aliases, python_name, _wire_name_for(python_name, param), handler_name)
    return aliases


def _handler_dishka_container(handler: "BaseRouteHandler") -> "Any":
    app = getattr(handler, "app", None)
    if app is None:
        for layer in getattr(handler, "ownership_layers", ()) or ():
            if getattr(layer, "state", None) is not None:
                app = layer
                break
    state = getattr(app, "state", None)
    if state is None:
        return None
    return getattr(state, "dishka_container", None)


def _dishka_component(metas: "list[ParameterKwarg]") -> "str":
    for meta in metas:
        component = getattr(meta, "component", None)
        if isinstance(component, str):
            return component
    return ""


def _dishka_dependency_key(annotation: "Any") -> "Any":
    inner, metas = _unwrap_annotated(annotation)
    if inner in {Any, inspect.Parameter.empty} or isinstance(inner, str):
        return None

    if not DISHKA_INSTALLED or DishkaDependencyKey is None:
        return None

    try:
        return DishkaDependencyKey(inner, component=_dishka_component(metas))
    except Exception:  # noqa: BLE001
        return None


def _dishka_registry_has_factory(registry: "Any", key: "Any") -> "bool":
    seen: set[int] = set()
    while registry is not None:
        registry_id = id(registry)
        if registry_id in seen:
            break
        seen.add(registry_id)

        get_factory = getattr(registry, "get_factory", None)
        if callable(get_factory):
            try:
                if get_factory(key) is not None:
                    return True
            except Exception:  # noqa: BLE001
                return False
        registry = getattr(registry, "child_registry", None)
    return False


def _dishka_can_resolve(container: "Any", annotation: "Any") -> "bool":
    if container is None:
        return False
    key = _dishka_dependency_key(annotation)
    if key is None:
        return False
    return _dishka_registry_has_factory(getattr(container, "registry", None), key)


def _unwrap_annotated(annotation: "Any") -> "tuple[Any, list[ParameterKwarg]]":
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        return args[0], [m for m in args[1:] if isinstance(m, ParameterKwarg)]
    if get_origin(annotation) in {Union, UnionType}:
        args = get_args(annotation)
        stripped_args: list[Any] = []
        metas: list[ParameterKwarg] = []
        changed = False
        for arg in args:
            stripped, arg_metas = _unwrap_annotated(arg)
            stripped_args.append(stripped)
            metas.extend(arg_metas)
            changed = changed or stripped is not arg or bool(arg_metas)
        if changed and stripped_args:
            stripped_union = stripped_args[0]
            for arg in stripped_args[1:]:
                stripped_union = stripped_union | arg
            return stripped_union, metas
    return annotation, []


def _wire_name_for(python_name: "str", param: "inspect.Parameter") -> "str":
    _, metas = _unwrap_annotated(param.annotation)
    for meta in metas:
        if meta.query:
            return meta.query
        if meta.header or meta.cookie:
            _logger.debug(
                "Provider param %r declares non-query source (header/cookie); wire name falls back to python name.",
                python_name,
            )
    return python_name


def _should_collect_dependency_parameter(
    pname: "str",
    param: "inspect.Parameter",
    *,
    framework_skip: "set[str]",
    dep_names: "set[str]",
    top_deps: "dict[str, Any]",
    queue: "deque[Any]",
    seen_names: "set[str]",
) -> "bool":
    if param.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
        return False
    if pname in framework_skip:
        return False
    if pname in dep_names:
        nested = top_deps.get(pname)
        if nested is not None:
            queue.append(nested)
        return False
    if pname in seen_names:
        return False
    seen_names.add(pname)
    return True


def _resolve_provider_parameter_annotation(
    pname: "str",
    param: "inspect.Parameter",
    resolved_hints: "dict[str, Any]",
) -> "inspect.Parameter":
    if isinstance(param.annotation, str):
        resolved = resolved_hints.get(pname)
        if resolved is not None:
            return param.replace(annotation=resolved)
    return param


def _record_alias(
    aliases: "dict[str, str]",
    python_name: "str",
    wire_name: "str",
    handler_name: "str",
) -> "None":
    if wire_name == python_name:
        return
    existing = aliases.get(wire_name)
    if existing is None:
        aliases[wire_name] = python_name
        return
    if existing == python_name:
        return
    msg = (
        f"Wire-name collision in handler {handler_name!r}: {wire_name!r} maps to both {existing!r} and {python_name!r}"
    )
    raise ValueError(msg)
