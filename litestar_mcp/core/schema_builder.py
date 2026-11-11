"""Automatic JSON Schema generation for MCP tools."""

import contextlib
import copy
import enum
import inspect
import logging
import re
import typing
from collections.abc import Mapping
from dataclasses import MISSING, fields
from functools import lru_cache
from types import UnionType
from typing import TYPE_CHECKING, Annotated, Any, Literal, Union, cast, get_args, get_origin

import msgspec
from litestar.params import ParameterKwarg
from litestar.response import Response
from litestar.types import Empty
from typing_extensions import NotRequired, ReadOnly, Required, get_type_hints, is_typeddict

from litestar_mcp.core._typing import attrs_nothing
from litestar_mcp.core.typing import (
    attrs_fields,
    is_attrs_schema,
    is_dataclass,
    is_msgspec_struct,
    is_pydantic_model,
)
from litestar_mcp.utils import get_handler_function, strip_docstring_sections
from litestar_mcp.utils.handler_signature import (
    _unwrap_annotated,
    get_advertised_handler_parameters,
)

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler

_logger = logging.getLogger(__name__)

_REQUIRED_ORIGINS: "tuple[Any, ...]" = tuple(
    marker for marker in (Required, getattr(typing, "Required", None)) if marker is not None
)
_NOT_REQUIRED_ORIGINS: "tuple[Any, ...]" = tuple(
    marker for marker in (NotRequired, getattr(typing, "NotRequired", None)) if marker is not None
)
_READONLY_ORIGINS: "tuple[Any, ...]" = tuple(
    marker for marker in (ReadOnly, getattr(typing, "ReadOnly", None)) if marker is not None
)
_MCP_CONTENT_MODULES: "frozenset[str]" = frozenset({"litestar_mcp.mcp.content"})
_MCP_CONTENT_TYPE_NAMES: "frozenset[str]" = frozenset(
    {
        "MCPBlobResource",
        "MCPInputRequiredResult",
        "MCPResourceLink",
        "MCPToolResult",
    }
)
_MCP_INPUT_REQUIRED_NAMES: "frozenset[str]" = frozenset({"MCPInputRequiredResult"})
_EMPTY_RETURN_MARKERS: "tuple[Any, ...]" = (
    None,
    type(None),
    inspect.Parameter.empty,
    inspect.Signature.empty,
    Empty,
    Any,
    object,
)


def basic_type_to_json_schema(annotation: "Any") -> "dict[str, Any] | None":
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


def collection_type_to_json_schema(annotation: "Any") -> "dict[str, Any] | None":
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


def pydantic_to_json_schema(model: "Any") -> "dict[str, Any]":
    """Convert Pydantic model to JSON Schema format."""
    schema: dict[str, Any] = model.model_json_schema()
    return schema


def msgspec_to_json_schema(struct_type: "Any") -> "dict[str, Any]":
    """Generate JSON Schema 2020-12 for a msgspec.Struct via msgspec's built-in.

    Delegates to ``msgspec.json.schema`` which provides full JSON Schema
    2020-12 coverage including ``$defs`` for nested Structs, Enum support,
    tagged-union discriminators, and ``msgspec.Meta`` constraint translation.
    """
    return msgspec.json.schema(struct_type)


def dataclass_to_json_schema(dataclass_type: "Any") -> "dict[str, Any]":
    """Convert dataclass to JSON Schema format."""
    properties = {}
    required = []

    for field in fields(dataclass_type):
        field_schema = type_to_json_schema(field.type)
        properties[field.name] = field_schema
        if field.default is MISSING and field.default_factory is MISSING:
            required.append(field.name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def attrs_to_json_schema(attrs_type: "Any") -> "dict[str, Any]":
    """Convert attrs class to JSON Schema format."""
    properties = {}
    required = []

    for field in attrs_fields(attrs_type):
        field_schema = type_to_json_schema(field.type)
        properties[field.name] = field_schema
        if field.default is attrs_nothing:
            required.append(field.name)

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def typed_dict_to_json_schema(typed_dict_type: "Any") -> "dict[str, Any]":
    """Convert a `typing.TypedDict` or `typing_extensions.TypedDict` to JSON Schema format."""
    if not is_typeddict(typed_dict_type):
        return {}

    try:
        hints = get_type_hints(typed_dict_type, include_extras=True)
    except (NameError, TypeError, ValueError):
        raw_annotations = getattr(typed_dict_type, "__annotations__", None)
        hints = dict(raw_annotations) if isinstance(raw_annotations, Mapping) else {}

    raw_required = getattr(typed_dict_type, "__required_keys__", None)
    default_total = bool(getattr(typed_dict_type, "__total__", True))
    properties: dict[str, Any] = {}
    required: list[str] = []

    for field_name, field_annotation in hints.items():
        base_required = field_name in raw_required if isinstance(raw_required, (set, frozenset)) else default_total
        inner_type, is_required, metas = _unwrap_typed_dict_field(field_annotation, default_required=base_required)
        field_schema = copy.deepcopy(type_to_json_schema(inner_type))
        _apply_annotation_metas(field_schema, metas)
        properties[field_name] = field_schema
        if is_required:
            required.append(field_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def model_to_json_schema(annotation: "Any") -> "dict[str, Any] | None":
    """Convert a model class (Pydantic, msgspec, attrs, dataclass) to JSON Schema format.

    This is the main entry point for structured type conversion.
    """
    if is_pydantic_model(annotation):
        return pydantic_to_json_schema(annotation)

    if is_msgspec_struct(annotation):
        return msgspec_to_json_schema(annotation)

    if is_attrs_schema(annotation):
        return attrs_to_json_schema(annotation)

    if is_dataclass(annotation):
        return dataclass_to_json_schema(annotation)

    return None


def union_type_to_json_schema(annotation: "Any") -> "dict[str, Any] | None":
    """Convert Union types (including Optional and `NoneType`) to JSON Schema format."""
    origin = get_origin(annotation)

    if annotation is type(None) or origin is type(None):
        return {"type": "null"}

    if origin in {Union, UnionType}:
        args = get_args(annotation)
        if len(args) == 1:
            return type_to_json_schema(args[0])
        any_of = []
        for arg in args:
            if arg is type(None):
                any_of.append({"type": "null"})
            else:
                any_of.append(type_to_json_schema(arg))
        return {"anyOf": any_of}

    return None


def type_to_json_schema(annotation: "Any") -> "dict[str, Any]":
    """Convert a Python type annotation to JSON Schema format.

    Args:
        annotation: Python type annotation to convert.

    Returns:
        JSON Schema dictionary for the type.
    """
    if annotation is None or annotation == inspect.Parameter.empty:
        return {"type": "object", "description": "No type annotation provided"}

    inner, metas = _unwrap_outer_annotated(annotation)
    if inner is not annotation:
        schema = type_to_json_schema(inner)
        for meta in metas:
            if isinstance(meta, msgspec.Meta):
                _merge_msgspec_meta(schema, meta)
            elif isinstance(meta, ParameterKwarg):
                _merge_parameter_meta(schema, meta)
        return schema

    if isinstance(annotation, str):
        annotation = _resolve_string_annotation(annotation)
        if isinstance(annotation, dict):
            return annotation

    for converter in (
        basic_type_to_json_schema,
        collection_type_to_json_schema,
        literal_type_to_json_schema,
        typed_dict_to_json_schema,
        model_to_json_schema,
    ):
        if result := converter(annotation):
            return result

    return union_type_to_json_schema(annotation) or {
        "type": "object",
        "description": "Parameter of type " + str(annotation),
    }


def literal_type_to_json_schema(annotation: "Any") -> "dict[str, Any] | None":
    """Describe ``Literal[...]`` and ``Enum`` annotations as JSON Schema ``enum`` constraints."""
    if get_origin(annotation) is Literal:
        values = list(get_args(annotation))
    elif isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        values = [member.value for member in annotation]
    else:
        return None
    schema: dict[str, Any] = {"enum": values}
    kinds = {type(value) for value in values}
    if kinds == {str}:
        schema["type"] = "string"
    elif kinds == {bool}:
        schema["type"] = "boolean"
    elif kinds <= {int}:
        schema["type"] = "integer"
    return schema


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

    advertised_params = get_advertised_handler_parameters(handler)

    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in advertised_params:
        prop_schema = type_to_json_schema(param.annotation)
        _, metas = _unwrap_annotated(param.annotation)
        if param.default is not inspect.Parameter.empty and any(getattr(m, "const", False) for m in metas):
            prop_schema["const"] = param.default
        if param.description and "description" not in prop_schema:
            prop_schema["description"] = param.description
        properties[param.wire_name] = prop_schema

        if param.required:
            required.append(param.wire_name)

    schema: dict[str, Any] = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }

    if required:
        schema["required"] = required

    fn_name = getattr(fn, "__name__", "unknown_function")
    handler_desc = getattr(handler, "description", None) or getattr(handler, "summary", None)
    fn_doc = getattr(fn, "__doc__", None)
    cleaned_doc = (
        handler_desc.strip()
        if isinstance(handler_desc, str) and handler_desc.strip()
        else (strip_docstring_sections(fn_doc) if isinstance(fn_doc, str) else "")
    )
    if cleaned_doc:
        schema["description"] = "Input parameters for " + str(fn_name) + ": " + cleaned_doc
    else:
        schema["description"] = "Input parameters for " + str(fn_name)

    definitions: dict[str, Any] = {}
    hoist_definitions(properties, definitions)
    if definitions:
        schema["$defs"] = definitions

    return schema


@lru_cache(maxsize=1024)
def cached_schema_for_handler(handler: "BaseRouteHandler") -> "dict[str, Any]":
    """Return `generate_schema_for_handler` output, computed once per handler.

    The returned schema is shared between callers and must not be mutated.
    """
    return generate_schema_for_handler(handler)


def unwrap_root_object_schema(schema: "dict[str, Any]") -> "dict[str, Any]":
    """Unwrap root `#/$defs/<Name>` envelopes into a root schema while retaining reachable `$defs`.

    Input schemas and nested `$defs` are deep-copied so cached model definitions
    are never mutated in place. Top-level metadata keys (`title`, `description`,
    `examples`, `$schema`) already present on `schema` take precedence over keys
    defined inside the referenced `$defs` target.
    """
    result: dict[str, Any] = copy.deepcopy(dict(schema))
    raw_defs = result.pop("$defs", None)
    defs: dict[str, Any] = copy.deepcopy(dict(raw_defs)) if isinstance(raw_defs, Mapping) else {}
    for def_value in list(defs.values()):
        hoist_definitions(def_value, defs)
    hoist_definitions(result, defs)

    visited_refs: set[str] = set()
    while "$ref" in result and "type" not in result:
        ref = result.get("$ref")
        if not isinstance(ref, str) or not ref.startswith(_DEFS_REF_PREFIX) or ref in visited_refs:
            break
        def_name = ref[len(_DEFS_REF_PREFIX) :]
        target = defs.get(def_name)
        if not isinstance(target, Mapping):
            break
        visited_refs.add(ref)
        result.pop("$ref", None)
        merged: dict[str, Any] = copy.deepcopy(dict(target))
        merged.update(result)
        result = merged
        hoist_definitions(result, defs)

    reachable = _collect_reachable_defs(result, defs)
    if reachable:
        result["$defs"] = {name: copy.deepcopy(defs[name]) for name in defs if name in reachable}
    return result


def type_to_output_schema(annotation: "Any") -> "dict[str, Any] | None":
    """Synthesize an MCP-compliant root `"type": "object"` JSON Schema for a return annotation.

    Returns `None` for missing, scalar, array, nullable, `Response`, or MCP
    content-helper annotations that do not map to a single root JSON object.
    """
    inner, metas = _unwrap_outer_annotated(annotation)
    if _is_non_schema_return(inner):
        return None

    origin = get_origin(inner)
    if origin in {Union, UnionType}:
        remaining = [arg for arg in get_args(inner) if not _is_input_required_member(arg)]
        if len(remaining) != 1:
            return None
        sub = type_to_output_schema(remaining[0])
        if sub is None:
            return None
        merged = copy.deepcopy(sub)
        _apply_annotation_metas(merged, metas)
        return merged if merged.get("type") == "object" else None

    if is_typeddict(inner):
        raw_schema: dict[str, Any] | None = typed_dict_to_json_schema(inner)
    elif inner is dict or origin is dict:
        raw_schema = {"type": "object"}
    else:
        raw_schema = model_to_json_schema(inner)

    if not raw_schema:
        return None
    schema = unwrap_root_object_schema(raw_schema)
    if is_dataclass(inner) and "description" not in schema:
        raw_doc = inspect.getdoc(inner)
        inner_name = getattr(inner, "__name__", None)
        if raw_doc and (inner_name is None or not raw_doc.startswith(f"{inner_name}(")):
            schema["description"] = raw_doc
    _apply_annotation_metas(schema, metas)
    return schema if schema.get("type") == "object" else None


def generate_output_schema_for_handler(handler: "BaseRouteHandler") -> "dict[str, Any] | None":
    """Generate an MCP `outputSchema` for a route handler's return annotation, or `None`."""
    return type_to_output_schema(_extract_handler_return_annotation(handler))


@lru_cache(maxsize=1024)
def cached_output_schema_for_handler(handler: "BaseRouteHandler") -> "dict[str, Any] | None":
    """Return `generate_output_schema_for_handler` output, computed once per handler.

    Callers must `copy.deepcopy` the returned dictionary before mutating
    `properties` or `$defs`.
    """
    return generate_output_schema_for_handler(handler)


_DEFS_REF_PREFIX = "#/$defs/"


def hoist_definitions(node: "Any", definitions: "dict[str, Any]") -> "None":
    """Move every nested ``$defs`` block into ``definitions``, the root schema's ``$defs``.

    Model schemas from pydantic and msgspec are standalone documents whose
    ``#/$defs/...`` references resolve against their own root. Nested under a
    tool's ``properties`` they would point nowhere, so their definitions move to
    the tool schema's root. A name already taken by a different definition is
    renamed, and the references inside that model's subtree are rewritten.
    """
    if isinstance(node, list):
        for item in node:
            hoist_definitions(item, definitions)
        return
    if not isinstance(node, dict):
        return
    local = node.pop("$defs", None)
    if isinstance(local, dict):
        renames: dict[str, str] = {}
        for name, definition in cast("dict[str, Any]", local).items():
            target = name
            suffix = 2
            while target in definitions and definitions[target] != definition:
                target = f"{name}{suffix}"
                suffix += 1
            if target != name:
                renames[name] = target
        if renames:
            _rename_refs(node, renames)
            _rename_refs(local, renames)
        for name, definition in cast("dict[str, Any]", local).items():
            hoist_definitions(definition, definitions)
            definitions.setdefault(renames.get(name, name), definition)
    for value in node.values():
        hoist_definitions(value, definitions)


_HEADER_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def iter_mcp_header_fields(
    schema: "dict[str, Any]",
    path: "tuple[str, ...]" = (),
    defs: "dict[str, Any] | None" = None,
    visited_refs: "frozenset[str]" = frozenset(),
) -> "list[tuple[tuple[str, ...], str, dict[str, Any]]]":
    """Return statically reachable ``x-mcp-header`` schema properties."""
    fields: list[tuple[tuple[str, ...], str, dict[str, Any]]] = []
    schema_defs = schema.get("$defs")
    root_defs: dict[str, Any] = defs if defs is not None else (schema_defs if isinstance(schema_defs, dict) else {})
    ref = schema.get("$ref")
    if isinstance(ref, str) and ref.startswith(_DEFS_REF_PREFIX) and ref not in visited_refs:
        target = root_defs.get(ref[len(_DEFS_REF_PREFIX) :])
        if isinstance(target, dict):
            fields.extend(
                iter_mcp_header_fields(
                    target,
                    path=path,
                    defs=root_defs,
                    visited_refs=visited_refs | {ref},
                )
            )
    properties = schema.get("properties")
    if isinstance(properties, dict):
        for property_name, property_schema in properties.items():
            if not isinstance(property_name, str) or not isinstance(property_schema, dict):
                continue
            property_path = (*path, property_name)
            header_name = property_schema.get("x-mcp-header")
            if isinstance(header_name, str):
                fields.append((property_path, header_name, property_schema))
            fields.extend(
                iter_mcp_header_fields(
                    property_schema,
                    path=property_path,
                    defs=root_defs,
                    visited_refs=visited_refs,
                )
            )
    for combinator in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(combinator)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict):
                    fields.extend(
                        iter_mcp_header_fields(
                            branch,
                            path=path,
                            defs=root_defs,
                            visited_refs=visited_refs,
                        )
                    )
    return fields


def validate_mcp_header_schema(schema: "dict[str, Any]") -> "None":
    """Validate all custom MCP header annotations in one tool schema."""
    seen: set[str] = set()
    for path, header_name, property_schema in iter_mcp_header_fields(schema):
        if not header_name or _HEADER_TOKEN.fullmatch(header_name) is None:
            msg = f"Invalid x-mcp-header value {header_name!r} at {'.'.join(path)}"
            raise ValueError(msg)
        normalized = header_name.lower()
        if normalized in seen:
            msg = f"Duplicate x-mcp-header value {header_name!r}"
            raise ValueError(msg)
        seen.add(normalized)
        schema_types = {property_schema.get("type")}
        any_of = property_schema.get("anyOf")
        if isinstance(any_of, list):
            schema_types = {member.get("type") for member in any_of if isinstance(member, dict)}
            schema_types.discard("null")
        if schema_types not in ({"string"}, {"integer"}, {"boolean"}):
            msg = f"x-mcp-header at {'.'.join(path)} must annotate a string, integer, or boolean"
            raise ValueError(msg)


def _rename_refs(node: "Any", renames: "dict[str, str]") -> "None":
    if isinstance(node, list):
        for item in node:
            _rename_refs(item, renames)
        return
    if not isinstance(node, dict):
        return
    ref = node.get("$ref")
    if isinstance(ref, str) and ref.startswith(_DEFS_REF_PREFIX):
        name = ref[len(_DEFS_REF_PREFIX) :]
        if name in renames:
            node["$ref"] = _DEFS_REF_PREFIX + renames[name]
    for value in node.values():
        _rename_refs(value, renames)


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


def _merge_parameter_meta(schema: "dict[str, Any]", meta: "Any") -> "None":
    """Copy non-None fields from `meta` into `schema` using JSON Schema names."""
    for attr, key in _META_FIELD_MAP:
        value = getattr(meta, attr, None)
        if value is None:
            continue
        if key == "examples" and not isinstance(value, list):
            schema[key] = [value]
        else:
            schema[key] = value
    schema_extra = getattr(meta, "schema_extra", None)
    if isinstance(schema_extra, dict):
        schema.update(schema_extra)


def _merge_msgspec_meta(schema: "dict[str, Any]", meta: "msgspec.Meta") -> "None":
    """Copy non-None fields from `msgspec.Meta` into `schema` using JSON Schema names."""
    for attr, key in _META_FIELD_MAP:
        value = getattr(meta, attr, None)
        if value is None:
            continue
        if key == "examples" and not isinstance(value, list):
            schema[key] = [value]
        else:
            schema[key] = value
    extra_json_schema = meta.extra_json_schema
    if isinstance(extra_json_schema, dict):
        schema.update(extra_json_schema)


def _apply_annotation_metas(schema: "dict[str, Any]", metas: "list[Any]") -> "None":
    """Apply `ParameterKwarg` and `msgspec.Meta` metadata objects to `schema`."""
    for meta in metas:
        if isinstance(meta, ParameterKwarg):
            _merge_parameter_meta(schema, meta)
        elif isinstance(meta, msgspec.Meta):
            _merge_msgspec_meta(schema, meta)


def _unwrap_outer_annotated(annotation: "Any") -> "tuple[Any, list[Any]]":
    """Unwrap nested outer `Annotated[T, ...]` wrappers without altering unions."""
    if get_origin(annotation) is Annotated:
        args = get_args(annotation)
        if not args:
            return annotation, []
        inner, nested_metas = _unwrap_outer_annotated(args[0])
        return inner, [*nested_metas, *args[1:]]
    return annotation, []


def _unwrap_typed_dict_field(
    annotation: "Any",
    *,
    default_required: "bool",
) -> "tuple[Any, bool, list[Any]]":
    """Unwrap `Annotated`, `Required`, `NotRequired`, and `ReadOnly` markers on a `TypedDict` field."""
    current = annotation
    required_override: bool | None = None
    outer_to_inner_groups: list[tuple[Any, ...]] = []
    while True:
        origin = get_origin(current)
        args = get_args(current)
        if origin is Annotated and args:
            outer_to_inner_groups.append(args[1:])
            current = args[0]
            continue
        if origin in _REQUIRED_ORIGINS and args:
            if required_override is None:
                required_override = True
            current = args[0]
            continue
        if origin in _NOT_REQUIRED_ORIGINS and args:
            if required_override is None:
                required_override = False
            current = args[0]
            continue
        if origin in _READONLY_ORIGINS and args:
            current = args[0]
            continue
        break
    metas: list[Any] = [meta for group in reversed(outer_to_inner_groups) for meta in group]
    is_required = default_required if required_override is None else required_override
    return current, is_required, metas


def _collect_reachable_defs(root: "Any", defs: "Mapping[str, Any]") -> "set[str]":
    """Return names in `defs` transitively referenced by `#/$defs/<Name>` pointers in `root`."""
    reachable: set[str] = set()

    def _walk(node: "Any") -> "None":
        if isinstance(node, list):
            for item in node:
                _walk(item)
            return
        if not isinstance(node, Mapping):
            return
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(_DEFS_REF_PREFIX):
            def_name = ref[len(_DEFS_REF_PREFIX) :]
            if def_name in defs and def_name not in reachable:
                reachable.add(def_name)
                _walk(defs[def_name])
        for key, value in node.items():
            if key == "$defs":
                continue
            _walk(value)

    _walk(root)
    return reachable


def _is_mcp_content_type(tp: "Any", *, names: "frozenset[str]" = _MCP_CONTENT_TYPE_NAMES) -> "bool":
    """Return whether `tp` is a class from `litestar_mcp.mcp.content` matching `names`."""
    return getattr(tp, "__module__", None) in _MCP_CONTENT_MODULES and getattr(tp, "__name__", None) in names


def _is_non_schema_return(tp: "Any") -> "bool":
    """Return whether `tp` represents an empty, unstructured, HTTP `Response`, or MCP helper return."""
    if any(tp is marker for marker in _EMPTY_RETURN_MARKERS):
        return True
    if isinstance(tp, str):
        return True
    if tp is Response or get_origin(tp) is Response:
        return True
    return _is_mcp_content_type(tp)


def _is_input_required_member(tp: "Any") -> "bool":
    """Return whether union member `tp` is `MCPInputRequiredResult`."""
    inner, _ = _unwrap_outer_annotated(tp)
    return _is_mcp_content_type(inner, names=_MCP_INPUT_REQUIRED_NAMES)


def _extract_handler_return_annotation(handler: "BaseRouteHandler") -> "Any":
    """Extract a route handler's return annotation while preserving `Annotated` metadata."""
    ret_annotation: Any = inspect.Signature.empty
    parsed_sig: Any = None
    with contextlib.suppress(Exception):
        parsed_sig = handler.parsed_fn_signature
    if parsed_sig is not None:
        return_type = getattr(parsed_sig, "return_type", None)
        if return_type is not None:
            raw = getattr(return_type, "raw", inspect.Signature.empty)
            ann = getattr(return_type, "annotation", inspect.Signature.empty)
            ret_annotation = ann if isinstance(raw, str) and not isinstance(ann, str) else raw
    if ret_annotation is not inspect.Signature.empty and not isinstance(ret_annotation, str):
        return ret_annotation

    try:
        fn: Any = inspect.unwrap(get_handler_function(handler))
    except (AttributeError, ValueError):
        fn = handler

    localns: dict[str, Any] = {}
    resolve_ns = getattr(handler, "resolve_signature_namespace", None)
    if callable(resolve_ns):
        with contextlib.suppress(Exception):
            resolved_ns = resolve_ns()
            if isinstance(resolved_ns, Mapping):
                localns.update(resolved_ns)
    raw_ns = getattr(handler, "signature_namespace", None)
    if isinstance(raw_ns, Mapping):
        for key, value in raw_ns.items():
            localns.setdefault(key, value)

    try:
        hints = get_type_hints(fn, localns=localns or None, include_extras=True)
    except (NameError, TypeError, ValueError):
        raw_annotations = getattr(fn, "__annotations__", None)
        raw_ret = (
            raw_annotations.get("return", inspect.Signature.empty)
            if isinstance(raw_annotations, dict)
            else inspect.Signature.empty
        )
        if isinstance(raw_ret, str) and raw_ret in localns:
            return localns[raw_ret]
        return raw_ret
    return hints.get("return", inspect.Signature.empty)


def _resolve_string_annotation(annotation: "str") -> "Any":
    """Resolve a string annotation to a Python type or fallback object schema."""
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

    return {"type": "object", "description": "Parameter of type " + str(annotation)}
