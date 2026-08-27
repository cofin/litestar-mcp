"""Unit tests for JSON Schema Draft 2020-12 generation from Python types."""

from dataclasses import dataclass

from litestar_mcp.core import (
    basic_type_to_json_schema,
    collection_type_to_json_schema,
    dataclass_to_json_schema,
    generate_schema_for_handler,
    union_type_to_json_schema,
)


@dataclass
class SampleConfig:
    """Sample dataclass for schema tests."""

    host: str
    port: int = 8000
    verbose: bool = False


def test_basic_types() -> None:
    """Verify basic primitive type schema generation."""
    assert basic_type_to_json_schema(str) == {"type": "string"}
    assert basic_type_to_json_schema(int) == {"type": "integer"}
    assert basic_type_to_json_schema(float) == {"type": "number"}
    assert basic_type_to_json_schema(bool) == {"type": "boolean"}


def test_collections() -> None:
    """Verify collection types generate valid array and object schemas."""
    list_schema = collection_type_to_json_schema(list[str])
    assert list_schema == {"type": "array", "items": {"type": "string"}}

    dict_schema = collection_type_to_json_schema(dict)
    assert dict_schema == {"type": "object"}


def test_dataclass_schema() -> None:
    """Verify dataclass converts to object schema with properties and required list."""
    schema = dataclass_to_json_schema(SampleConfig)
    assert schema["type"] == "object"
    assert "host" in schema["properties"]
    assert "port" in schema["properties"]
    assert "verbose" in schema["properties"]
    assert schema["required"] == ["host"]


def test_union_schema() -> None:
    """Verify optional and union types generate anyOf schemas."""
    schema = union_type_to_json_schema(str | None)
    assert schema is not None
    assert "anyOf" in schema


def test_generate_schema_for_handler() -> None:
    """Verify handler function reflection produces a complete JSON Schema."""

    def my_handler(query: str, limit: int = 10) -> str:
        """Search items.

        Args:
            query: The search query text.
            limit: Maximum items to return.
        """
        return query

    schema = generate_schema_for_handler(my_handler)
    assert schema["type"] == "object"
    assert "query" in schema["properties"]
    assert "limit" in schema["properties"]
    assert schema["required"] == ["query"]
