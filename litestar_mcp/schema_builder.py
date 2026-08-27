"""Automatic JSON Schema generation for MCP tools."""

import logging
import re
from typing import Any

from litestar_mcp.core.schema import (
    _merge_parameter_meta,
    _resolve_string_annotation,
    attrs_to_json_schema,
    basic_type_to_json_schema,
    collection_type_to_json_schema,
    dataclass_to_json_schema,
    generate_schema_for_handler,
    model_to_json_schema,
    msgspec_to_json_schema,
    pydantic_to_json_schema,
    type_to_json_schema,
    union_type_to_json_schema,
)

__all__ = (
    "_merge_parameter_meta",
    "_resolve_string_annotation",
    "attrs_to_json_schema",
    "basic_type_to_json_schema",
    "collection_type_to_json_schema",
    "dataclass_to_json_schema",
    "generate_schema_for_handler",
    "iter_mcp_header_fields",
    "model_to_json_schema",
    "msgspec_to_json_schema",
    "pydantic_to_json_schema",
    "type_to_json_schema",
    "union_type_to_json_schema",
    "validate_mcp_header_schema",
)

_logger = logging.getLogger(__name__)
_HEADER_TOKEN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def iter_mcp_header_fields(
    schema: dict[str, Any],
    path: tuple[str, ...] = (),
) -> list[tuple[tuple[str, ...], str, dict[str, Any]]]:
    """Return statically reachable ``x-mcp-header`` schema properties."""
    fields: list[tuple[tuple[str, ...], str, dict[str, Any]]] = []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return fields
    for property_name, property_schema in properties.items():
        if not isinstance(property_name, str) or not isinstance(property_schema, dict):
            continue
        property_path = (*path, property_name)
        header_name = property_schema.get("x-mcp-header")
        if isinstance(header_name, str):
            fields.append((property_path, header_name, property_schema))
        fields.extend(iter_mcp_header_fields(property_schema, property_path))
    return fields


def validate_mcp_header_schema(schema: dict[str, Any]) -> None:
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
