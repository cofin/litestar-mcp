"""Automatic JSON Schema generation for MCP tools."""

import contextlib
import inspect
import logging
import typing as _typing
from collections import deque
from types import UnionType
from typing import TYPE_CHECKING, Annotated, Any, Union, get_args, get_origin

from litestar.constants import RESERVED_KWARGS
from litestar.params import ParameterKwarg

from litestar_mcp.typing import (
    MSGSPEC_INSTALLED,
    is_attrs_instance,
    is_dataclass,
    is_msgspec_struct,
    is_pydantic_model,
)
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from collections.abc import Iterable

    from litestar.handlers import BaseRouteHandler

_logger = logging.getLogger(__name__)

_EXECUTION_CONTEXT_PARAMS = {"resolved_user", "user_claims"}


def _unwrap_annotated(annotation: Any) -> "tuple[Any, list[ParameterKwarg]]":
    """Return ``(inner_type, [ParameterKwarg, ...])``.

    For non-Annotated annotations, returns ``(annotation, [])``.
    Foreign metadata (strings, ``msgspec.Meta``, etc.) is ignored.

    .. note::
       If the handler's module uses ``from __future__ import annotations``,
       parameter annotations are stringified at definition time. ``get_origin``
       on a string returns ``None``, so this helper falls through to the
       no-Annotated branch and the schema builder will not emit
       ``Parameter`` metadata or wire-name aliases for that handler. Use
       runtime ``Annotated[...]`` annotations (i.e. omit the future import,
       or rely on Litestar's own type-hint resolution).
    """
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        return args[0], [m for m in args[1:] if isinstance(m, ParameterKwarg)]
    return annotation, []


def _wire_name_for(python_name: str, param: inspect.Parameter) -> str:
    """Return the externally visible wire name for ``param``.

    Only ``Parameter(query=...)`` aliases produce a distinct wire name. Header
    and cookie sources are not yet routed by the MCP dispatcher, so a provider
    that declares ``Parameter(header=...)`` will surface under its python name
    in the inputSchema; this is logged at DEBUG so it's discoverable.
    """
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


def iter_dependency_input_parameters(
    handler: "BaseRouteHandler",
    *,
    path_param_names: "Iterable[str] | None" = None,
) -> "list[tuple[str, inspect.Parameter]]":
    """Walk dependency providers and yield their user-input params.

    Litestar's HTTP routing inherits query/path/form params declared on a
    :class:`~litestar.di.Provide` factory up to the route, so the MCP tool
    schema must mirror them. This walks every ``Provide`` reachable from
    ``handler.resolve_dependencies()`` -- which Litestar resolves across all
    layered scopes (app/router/controller/handler) -- plus any sub-provider
    whose parameter name also resolves to a known DI key. Names skipped:

    * names that are themselves DI keys (those are providers, not inputs);
    * :data:`litestar.constants.RESERVED_KWARGS` (framework injections like
      ``state`` / ``request`` / ``scope``);
    * :data:`_EXECUTION_CONTEXT_PARAMS` (auth context keys);
    * ``path_param_names`` when supplied (path-bound values aren't user input
      from the MCP caller's perspective at the schema layer).

    Returns a list of ``(python_name, inspect.Parameter)`` in stable order.
    Duplicate python names across providers are deduplicated by first-seen.

    Note:
        Sub-providers registered at scopes the handler does not reference are
        not walked: ``resolve_dependencies()`` already returns the full layered
        DI graph visible to this handler. If your codebase relies on deeper
        graphs, advertise the relevant params directly on the handler.
    """
    try:
        top_deps = dict(handler.resolve_dependencies())
    except Exception as exc:  # noqa: BLE001
        # Broken DI on a handler means the route itself can't serve requests,
        # but schema-build runs at app startup -- propagating here would block
        # the whole app from booting because of one bad route. Log loudly so
        # operators can correlate "empty inputSchema" with the underlying DI
        # failure rather than silently shrinking the schema.
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

    # Cycle key is the provider *function* identity, not the Provide wrapper:
    # two Provides registered under different names with sync/cache flag
    # differences but the same underlying callable have identical signatures,
    # so walking either yields the same params.
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

        # Resolve stringified annotations (PEP 563 / ``from __future__ import
        # annotations``) so downstream consumers -- the schema builder and
        # ``routes._validate_tool_arguments`` -- see real types rather than
        # ``'int'`` strings. ``inspect.signature`` does not eval forward refs.
        try:
            resolved_hints = _typing.get_type_hints(provider_fn, include_extras=True)
        except Exception:  # noqa: BLE001
            resolved_hints = {}

        for pname, param in provider_sig.parameters.items():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if pname in framework_skip:
                continue
            if pname in dep_names:
                # Transitive provider — walk it but do not emit as user input.
                nested = top_deps.get(pname)
                if nested is not None:
                    queue.append(nested)
                continue
            if pname in seen_names:
                continue
            seen_names.add(pname)
            # Only swap in the resolved hint when the source annotation was a
            # string (PEP 563). For live ``Annotated[...]`` objects we already
            # have the real type, and ``get_type_hints`` on Python 3.10/3.11
            # would silently wrap params with ``None`` defaults in
            # ``Optional[...]``, hiding the inner ``Annotated`` from
            # ``_unwrap_annotated`` and dropping wire-name aliases.
            if isinstance(param.annotation, str):
                resolved = resolved_hints.get(pname)
                if resolved is not None:
                    param = param.replace(annotation=resolved)
            collected.append((pname, param))
    return collected


_META_FIELD_MAP: "tuple[tuple[str, str], ...]" = (
    ("description", "description"),
    ("title", "title"),
    ("examples", "examples"),
    ("ge", "minimum"),
    ("le", "maximum"),
    ("gt", "exclusiveMinimum"),
    ("lt", "exclusiveMaximum"),
    ("min_length", "minLength"),
    ("max_length", "maxLength"),
    ("pattern", "pattern"),
    ("multiple_of", "multipleOf"),
)


def _merge_parameter_meta(schema: "dict[str, Any]", meta: ParameterKwarg) -> None:
    """Copy non-None fields from ``meta`` into ``schema`` using JSON Schema names."""
    for attr, key in _META_FIELD_MAP:
        value = getattr(meta, attr, None)
        if value is None:
            continue
        if key == "examples" and not isinstance(value, list):
            schema[key] = [value]
        else:
            schema[key] = value


def basic_type_to_json_schema(annotation: Any) -> "dict[str, Any] | None":
    """Convert basic Python types to JSON Schema format."""
    if annotation is str:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bool:
        return {"type": "boolean"}
    return None


def collection_type_to_json_schema(annotation: Any) -> "dict[str, Any] | None":
    """Convert collection types (list, dict, set) to JSON Schema format."""
    origin = get_origin(annotation)

    if annotation is list or origin is list:
        args = get_args(annotation)
        if args:
            return {"type": "array", "items": type_to_json_schema(args[0])}
        return {"type": "array"}

    if annotation is dict or origin is dict:
        return {"type": "object"}

    if annotation is set or origin is set:
        args = get_args(annotation)
        if args:
            return {"type": "array", "items": type_to_json_schema(args[0]), "uniqueItems": True}
        return {"type": "array", "uniqueItems": True}

    return None


def pydantic_to_json_schema(model: Any) -> "dict[str, Any]":
    """Convert Pydantic model to JSON Schema format."""
    schema: dict[str, Any] = model.model_json_schema()
    return schema


def msgspec_to_json_schema(struct_type: Any) -> "dict[str, Any]":
    """Generate JSON Schema 2020-12 for a msgspec.Struct via msgspec's built-in.

    Delegates to ``msgspec.json.schema`` which provides full JSON Schema
    2020-12 coverage including ``$defs`` for nested Structs, Enum support,
    tagged-union discriminators, and ``msgspec.Meta`` constraint translation.
    """
    if not MSGSPEC_INSTALLED:
        return {"type": "object", "description": "msgspec Struct (msgspec not installed)"}

    import msgspec

    return msgspec.json.schema(struct_type)


def dataclass_to_json_schema(dataclass_type: Any) -> "dict[str, Any]":
    """Convert dataclass to JSON Schema format."""
    from dataclasses import MISSING, fields

    properties = {}
    required = []

    for field in fields(dataclass_type):
        field_schema = type_to_json_schema(field.type)
        properties[field.name] = field_schema
        if field.default is MISSING and field.default_factory is MISSING:
            required.append(field.name)

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def attrs_to_json_schema(attrs_type: Any) -> "dict[str, Any]":
    """Convert attrs class to JSON Schema format."""
    from litestar_mcp.typing import attrs_fields

    properties = {}
    required = []

    for field in attrs_fields(attrs_type):
        field_schema = type_to_json_schema(field.type)
        properties[field.name] = field_schema
        if field.default is inspect.Parameter.empty:
            required.append(field.name)

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def model_to_json_schema(annotation: Any) -> "dict[str, Any] | None":
    """Convert a model class (Pydantic, msgspec, attrs, dataclass) to JSON Schema format.

    This is the main entry point for structured type conversion.
    """
    if is_pydantic_model(annotation):
        return pydantic_to_json_schema(annotation)

    if is_msgspec_struct(annotation):
        return msgspec_to_json_schema(annotation)

    if is_attrs_instance(annotation):
        return attrs_to_json_schema(annotation)

    if is_dataclass(annotation):
        return dataclass_to_json_schema(annotation)

    return None


def union_type_to_json_schema(annotation: Any) -> "dict[str, Any] | None":
    """Convert Union types (including Optional) to JSON Schema format."""
    origin = get_origin(annotation)

    if origin is type(None):  # NoneType
        return {"type": "null"}

    # Handle Union types, including Optional[T] which is Union[T, None]
    if origin in (Union, UnionType):
        args = get_args(annotation)
        if len(args) == 1:
            return type_to_json_schema(args[0])
        # Build anyOf for all member types (including NoneType → {"type": "null"})
        any_of = []
        for arg in args:
            if arg is type(None):
                any_of.append({"type": "null"})
            else:
                any_of.append(type_to_json_schema(arg))
        return {"anyOf": any_of}

    return None


def type_to_json_schema(annotation: Any) -> "dict[str, Any]":  # noqa: PLR0911
    """Convert a Python type annotation to JSON Schema format.

    Args:
        annotation: Python type annotation to convert.

    Returns:
        JSON Schema dictionary for the type.
    """
    if annotation is None or annotation == inspect.Parameter.empty:
        return {"type": "object", "description": "No type annotation provided"}

    inner, metas = _unwrap_annotated(annotation)
    if inner is not annotation:
        schema = type_to_json_schema(inner)
        for meta in metas:
            _merge_parameter_meta(schema, meta)
        return schema

    if isinstance(annotation, str):
        annotation = _resolve_string_annotation(annotation)
        if isinstance(annotation, dict):
            return annotation

    if result := basic_type_to_json_schema(annotation):
        return result
    if result := collection_type_to_json_schema(annotation):
        return result
    if result := model_to_json_schema(annotation):
        return result

    return union_type_to_json_schema(annotation) or {
        "type": "object",
        "description": "Parameter of type " + str(annotation),
    }


def _resolve_string_annotation(annotation: str) -> Any:
    """Resolve a string annotation to a Python type."""
    # Common basic types
    basic_types = {
        "int": int,
        "str": str,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
        "set": set,
    }

    if annotation in basic_types:
        return basic_types[annotation]

    # For complex string annotations, return object schema
    return {"type": "object", "description": "Parameter of type " + str(annotation)}


def generate_schema_for_handler(handler: "BaseRouteHandler") -> "dict[str, Any]":
    """Generate a JSON Schema for an MCP tool handler.

    Args:
        handler: The route handler to generate schema for.

    Returns:
        JSON Schema dictionary describing the tool's input parameters.
    """
    try:
        fn = get_handler_function(handler)
    except AttributeError:
        fn = handler

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}}

    di_params: set[str] = set()
    with contextlib.suppress(Exception):
        di_params = set(handler.resolve_dependencies().keys())

    properties: dict[str, Any] = {}
    required: list[str] = []
    wire_to_python: dict[str, str] = {}
    handler_name = getattr(fn, "__name__", "<handler>")

    def _emit(python_name: str, param: inspect.Parameter) -> None:
        wire_name = _wire_name_for(python_name, param)
        _, metas = _unwrap_annotated(param.annotation)

        if wire_name in wire_to_python:
            if wire_to_python[wire_name] == python_name:
                return
            existing = wire_to_python[wire_name]
            msg = (
                f"Wire-name collision in handler {handler_name!r}: "
                f"{wire_name!r} maps to both {existing!r} and {python_name!r}"
            )
            raise ValueError(msg)
        wire_to_python[wire_name] = python_name

        prop_schema = type_to_json_schema(param.annotation)
        # ``ParameterKwarg.const=True`` means "must equal the default value",
        # so we emit the default itself as the JSON Schema ``const`` value
        # rather than a literal boolean — which would constrain non-bool
        # parameters to ``true``/``false`` and mislead schema-driven clients.
        if param.default is not inspect.Parameter.empty and any(getattr(m, "const", False) for m in metas):
            prop_schema["const"] = param.default
        properties[wire_name] = prop_schema

        if param.default is inspect.Parameter.empty:
            required.append(wire_name)

    for python_name, param in sig.parameters.items():
        if python_name in di_params or python_name in _EXECUTION_CONTEXT_PARAMS:
            continue
        _emit(python_name, param)

    for python_name, param in iter_dependency_input_parameters(handler):
        _emit(python_name, param)

    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }

    if required:
        schema["required"] = required

    fn_name = getattr(fn, "__name__", "unknown_function")
    fn_doc = getattr(fn, "__doc__", None)
    if fn_doc:
        schema["description"] = "Input parameters for " + str(fn_name) + ": " + str(fn_doc.strip())
    else:
        schema["description"] = "Input parameters for " + str(fn_name)

    return schema


def _record_alias(
    aliases: "dict[str, str]",
    python_name: str,
    wire_name: str,
    handler_name: str,
) -> None:
    """Insert ``{wire_name: python_name}`` into ``aliases``; raise on real collision.

    Same-python-name re-emissions (e.g. a path param echoed by a provider) are
    no-ops, matching the dedupe semantics in :func:`generate_schema_for_handler`.
    Different python names mapping to one wire name is a wire-contract bug --
    raise rather than silently dropping the later entry.
    """
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


def parameter_aliases(handler: "BaseRouteHandler") -> "dict[str, str]":
    """Return ``{wire_name: python_name}`` for handler params whose wire name differs.

    Wire name is taken from ``Parameter(query=...)``. Header and cookie sources
    are intentionally ignored — the executor only synthesizes query strings,
    so exposing those wire names would produce schemas the dispatcher cannot
    honor. Re-add them when header/cookie dispatch lands (out of scope for #52).

    Raises ``ValueError`` on real wire-name collisions (the same alias mapping
    to two different python names), mirroring :func:`generate_schema_for_handler`.
    """
    try:
        fn = get_handler_function(handler)
    except AttributeError:
        fn = handler  # raw function in tests

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return {}

    handler_name = getattr(fn, "__name__", "<handler>")
    aliases: dict[str, str] = {}
    for python_name, param in sig.parameters.items():
        _record_alias(aliases, python_name, _wire_name_for(python_name, param), handler_name)
    for python_name, param in iter_dependency_input_parameters(handler):
        _record_alias(aliases, python_name, _wire_name_for(python_name, param), handler_name)
    return aliases
