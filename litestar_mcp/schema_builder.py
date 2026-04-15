"""Automatic JSON Schema generation for MCP tools."""

import contextlib
import inspect
from types import UnionType
from typing import TYPE_CHECKING, Any, Union, get_args, get_origin

if TYPE_CHECKING:
    from litestar.handlers import BaseRouteHandler

from litestar_mcp.typing import (
    MSGSPEC_INSTALLED,
    is_attrs_instance,
    is_dataclass,
    is_msgspec_struct,
    is_pydantic_model,
)
from litestar_mcp.utils import get_handler_function

_EXECUTION_CONTEXT_PARAMS = {"resolved_user", "user_claims"}


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


def type_to_json_schema(annotation: Any) -> "dict[str, Any]":
    """Convert a Python type annotation to JSON Schema format.

    Args:
        annotation: Python type annotation to convert.

    Returns:
        JSON Schema dictionary for the type.
    """
    # Handle None/empty annotation
    if annotation is None or annotation == inspect.Parameter.empty:
        return {"type": "object", "description": "No type annotation provided"}

    # Handle stringified annotations (common in forward references)
    if isinstance(annotation, str):
        annotation = _resolve_string_annotation(annotation)
        if isinstance(annotation, dict):  # If resolution failed
            return annotation

    # Try type conversions in order of complexity
    if result := basic_type_to_json_schema(annotation):
        return result
    if result := collection_type_to_json_schema(annotation):
        return result
    if result := model_to_json_schema(annotation):
        return result

    # Try union types and fallback
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
    # Get the actual function to inspect
    try:
        fn = get_handler_function(handler)
    except AttributeError:
        # Fallback for test cases where handler might be a raw function
        fn = handler

    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        # Handler has no callable function - return empty schema
        return {"type": "object", "properties": {}}

    # Get dependencies that will be handled by DI, not passed as arguments
    di_params = set()
    with contextlib.suppress(Exception):
        di_params = set(handler.resolve_dependencies().keys())

    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        # Skip dependency injection parameters
        if param_name in di_params or param_name in _EXECUTION_CONTEXT_PARAMS:
            continue

        # Generate schema for this parameter
        param_schema = type_to_json_schema(param.annotation)

        # Add description if available from docstring or annotation
        if getattr(param.annotation, "__doc__", None):
            param_schema["description"] = param.annotation.__doc__.strip()

        properties[param_name] = param_schema

        # Check if parameter is required (no default value)
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    # Build the complete schema
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }

    if required:
        schema["required"] = required

    # Add overall description from the function docstring
    fn_name = getattr(fn, "__name__", "unknown_function")
    fn_doc = getattr(fn, "__doc__", None)
    if fn_doc:
        schema["description"] = "Input parameters for " + str(fn_name) + ": " + str(fn_doc.strip())
    else:
        schema["description"] = "Input parameters for " + str(fn_name)

    return schema
