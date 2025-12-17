"""Automatic JSON Schema generation for MCP tools."""

import contextlib
import inspect
from typing import TYPE_CHECKING, Any, Optional, Union, get_args, get_origin

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


def basic_type_to_json_schema(annotation: Any) -> "Optional[dict[str, Any]]":
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


def collection_type_to_json_schema(annotation: Any) -> "Optional[dict[str, Any]]":
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
    """Convert msgspec Struct to JSON Schema format."""
    if not MSGSPEC_INSTALLED:
        return {"type": "object", "description": "msgspec Struct (msgspec not installed)"}

    import msgspec

    properties = {}
    required = []

    fields = msgspec.structs.fields(struct_type)
    for field in fields:
        field_schema = type_to_json_schema(field.type)
        properties[field.name] = field_schema
        if field.default is msgspec.NODEFAULT:
            required.append(field.name)

    schema = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


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


def model_to_json_schema(annotation: Any) -> "Optional[dict[str, Any]]":
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


def union_type_to_json_schema(annotation: Any) -> "Optional[dict[str, Any]]":
    """Convert Union types (including Optional) to JSON Schema format with nullability."""
    origin = get_origin(annotation)

    if origin is type(None):
        return {"type": "null"}

    if origin is Union:
        args = get_args(annotation)
        non_none_args = [arg for arg in args if arg is not type(None)]
        has_none = type(None) in args

        if len(non_none_args) == 1:
            base_schema = type_to_json_schema(non_none_args[0])

            if has_none:
                if isinstance(base_schema.get("type"), str):
                    base_schema["type"] = [base_schema["type"], "null"]
                elif isinstance(base_schema.get("type"), list):
                    if "null" not in base_schema["type"]:
                        base_schema["type"].append("null")
                else:
                    return {"anyOf": [base_schema, {"type": "null"}]}

            return base_schema

        if len(non_none_args) > 1:
            schemas = [type_to_json_schema(arg) for arg in non_none_args]
            if has_none:
                schemas.append({"type": "null"})
            return {"anyOf": schemas}

    return None


def type_to_json_schema(annotation: Any) -> "dict[str, Any]":
    """Convert a Python type annotation to JSON Schema format.

    Args:
        annotation: Python type annotation to convert.

    Returns:
        JSON Schema dictionary for the type.
    """
    if annotation is None or annotation == inspect.Parameter.empty:
        return {"type": "object", "description": "No type annotation provided"}

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

    return union_type_to_json_schema(annotation) or {"type": "object", "description": f"Parameter of type {annotation}"}


def _resolve_string_annotation(annotation: str) -> Any:
    """Resolve a string annotation to a Python type with support for complex types.

    Resolves a restricted subset of Python expressions representing type annotations.
    This avoids using ``eval()`` while still supporting common typing constructs.
    """
    import ast
    import typing

    typing_extensions_module: Optional[Any] = None
    with contextlib.suppress(ImportError):
        import typing_extensions as _typing_extensions

        typing_extensions_module = _typing_extensions

    basic_types: dict[str, Any] = {
        "int": int,
        "str": str,
        "float": float,
        "bool": bool,
        "list": list,
        "dict": dict,
        "set": set,
        "tuple": tuple,
        "None": type(None),
    }

    namespace: dict[str, Any] = {}
    namespace.update(basic_types)
    namespace.update(typing.__dict__)
    namespace["typing"] = typing
    if typing_extensions_module is not None:
        namespace["typing_extensions"] = typing_extensions_module
        namespace.update(typing_extensions_module.__dict__)

    def eval_node(node: ast.AST) -> Any:
        if isinstance(node, ast.Name):
            return namespace[node.id]

        if isinstance(node, ast.Attribute):
            base = eval_node(node.value)
            if base is typing or (typing_extensions_module is not None and base is typing_extensions_module):
                return getattr(base, node.attr)
            raise KeyError(node.attr)

        if isinstance(node, ast.Constant):
            if isinstance(node.value, (str, int, float, bool)) or node.value is None:
                return node.value
            raise TypeError

        if isinstance(node, ast.Tuple):
            return tuple(eval_node(elt) for elt in node.elts)

        if isinstance(node, ast.Subscript):
            base = eval_node(node.value)
            slice_node = node.slice
            args = eval_node(slice_node)
            return base[args]

        raise TypeError

    try:
        parsed = ast.parse(annotation, mode="eval")
        return eval_node(parsed.body)
    except (KeyError, SyntaxError, TypeError, AttributeError):
        return {"type": "object", "description": f"Parameter of type {annotation}"}


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

    di_params = set()
    with contextlib.suppress(Exception):
        di_params = set(handler.resolve_dependencies().keys())

    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        if param_name in di_params:
            continue

        param_schema = type_to_json_schema(param.annotation)

        if getattr(param.annotation, "__doc__", None):
            param_schema["description"] = param.annotation.__doc__.strip()

        properties[param_name] = param_schema

        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema = {"type": "object", "properties": properties}

    if required:
        schema["required"] = required

    fn_name = getattr(fn, "__name__", "unknown_function")
    fn_doc = getattr(fn, "__doc__", None)
    if fn_doc:
        schema["description"] = f"Input parameters for {fn_name}: {fn_doc.strip()}"
    else:
        schema["description"] = f"Input parameters for {fn_name}"

    return schema
