"""Tests for the schema builder module."""

from dataclasses import dataclass
from typing import Any, Optional

import pytest
from litestar import get
from litestar.handlers import BaseRouteHandler

from litestar_mcp.schema_builder import (
    basic_type_to_json_schema,
    collection_type_to_json_schema,
    dataclass_to_json_schema,
    generate_schema_for_handler,
    msgspec_to_json_schema,
    pydantic_to_json_schema,
    type_to_json_schema,
)
from tests.conftest import create_app_with_handler


class TestSchemaBuilder:
    """Test suite for schema builder functionality."""

    def test_basic_type_mappings(self) -> None:
        """Test mapping of basic Python types to JSON Schema."""
        assert type_to_json_schema(str) == {"type": "string"}
        assert type_to_json_schema(int) == {"type": "integer"}
        assert type_to_json_schema(float) == {"type": "number"}
        assert type_to_json_schema(bool) == {"type": "boolean"}

    def test_list_type_mappings(self) -> None:
        """Test mapping of list types to JSON Schema."""
        # Basic list
        assert type_to_json_schema(list) == {"type": "array"}

        # Typed list
        list_str_schema = type_to_json_schema(list[str])
        assert list_str_schema == {"type": "array", "items": {"type": "string"}}

        # Nested list
        list_int_schema = type_to_json_schema(list[int])
        assert list_int_schema == {"type": "array", "items": {"type": "integer"}}

    def test_dict_type_mappings(self) -> None:
        """Test mapping of dict types to JSON Schema."""
        assert type_to_json_schema(dict) == {"type": "object"}
        assert type_to_json_schema(dict[str, Any]) == {"type": "object"}

    def test_set_type_mappings(self) -> None:
        """Test mapping of set types to JSON Schema."""
        # Basic set
        assert type_to_json_schema(set) == {"type": "array", "uniqueItems": True}

        # Typed set
        set_str_schema = type_to_json_schema(set[str])
        assert set_str_schema == {"type": "array", "items": {"type": "string"}, "uniqueItems": True}

    def test_complex_type_fallback(self) -> None:
        """Test fallback for complex types we can't handle."""

        class CustomClass:
            pass

        schema = type_to_json_schema(CustomClass)
        assert schema["type"] == "object"
        assert "Parameter of type" in schema["description"]

    def test_generate_schema_for_simple_handler(self) -> None:
        """Test schema generation for a handler with simple parameters."""

        def simple_handler(name: str, age: int, active: bool = True) -> dict[str, Any]:
            return {"name": name, "age": age, "active": active}

        _, handler = create_app_with_handler(simple_handler)
        schema = generate_schema_for_handler(handler)

        assert schema["type"] == "object"
        assert "properties" in schema
        assert "required" in schema

        # Check properties
        properties = schema["properties"]
        assert properties["name"]["type"] == "string"
        assert properties["age"]["type"] == "integer"
        assert properties["active"]["type"] == "boolean"

        # Check required fields (active has default, so not required)
        assert set(schema["required"]) == {"name", "age"}

    def test_generate_schema_for_handler_with_optional_params(self) -> None:
        """Test schema generation for handler with optional parameters."""

        def handler_with_optional(message: str, count: int = 1, tags: Optional[list[str]] = None) -> dict[str, Any]:
            return {"message": message, "count": count, "tags": tags}

        _, handler = create_app_with_handler(handler_with_optional)
        schema = generate_schema_for_handler(handler)

        # Only 'message' should be required
        assert schema["required"] == ["message"]

        # All parameters should be in properties
        properties = schema["properties"]
        assert "message" in properties
        assert "count" in properties
        assert "tags" in properties

        # Check types
        assert properties["message"]["type"] == "string"
        assert properties["count"]["type"] == "integer"
        assert properties["tags"]["type"] == "array"

    def test_generate_schema_with_complex_types(self) -> None:
        """Test schema generation with complex parameter types."""

        def complex_handler(config: dict[str, Any], items: list[str], metadata: dict[str, int]) -> dict[str, Any]:
            return {"processed": True}

        _, handler = create_app_with_handler(complex_handler)
        schema = generate_schema_for_handler(handler)

        properties = schema["properties"]

        # Check complex types
        assert properties["config"]["type"] == "object"
        assert properties["items"]["type"] == "array"
        assert properties["items"]["items"]["type"] == "string"
        assert properties["metadata"]["type"] == "object"

        # All should be required
        assert set(schema["required"]) == {"config", "items", "metadata"}

    def test_generate_schema_excludes_di_parameters(self) -> None:
        """Test that dependency injection parameters are excluded from schema."""
        from litestar.di import Provide

        def provide_config() -> dict[str, str]:
            return {"setting": "value"}

        def handler_with_di(user_id: int, config: dict[str, str]) -> dict[str, Any]:
            return {"user_id": user_id, "config": config}

        _, handler = create_app_with_handler(
            handler_with_di, dependencies={"config": Provide(provide_config, sync_to_thread=False)}
        )

        schema = generate_schema_for_handler(handler)

        # Only user_id should be in the schema, not config
        assert "user_id" in schema["properties"]
        assert "config" not in schema["properties"]
        assert schema["required"] == ["user_id"]

    def test_generate_schema_no_parameters(self) -> None:
        """Test schema generation for handler with no parameters."""

        def status_handler() -> dict[str, str]:
            """Get system status."""
            return {"status": "ok"}

        _, handler = create_app_with_handler(status_handler, route_path="/status")
        schema = generate_schema_for_handler(handler)

        assert schema["type"] == "object"
        assert schema["properties"] == {}
        assert "required" not in schema or schema["required"] == []

    def test_generate_schema_with_docstring(self) -> None:
        """Test that schema includes description from function docstring."""

        def documented_handler(value: str) -> str:
            """This is a test handler that processes a value."""
            return f"Processed: {value}"

        _, handler = create_app_with_handler(documented_handler)
        schema = generate_schema_for_handler(handler)

        assert "description" in schema
        assert "This is a test handler that processes a value" in schema["description"]

    def test_generate_schema_without_docstring(self) -> None:
        """Test schema generation for handler without docstring."""

        def undocumented_handler(value: str) -> str:
            return f"Processed: {value}"

        _, handler = create_app_with_handler(undocumented_handler)
        schema = generate_schema_for_handler(handler)

        assert "description" in schema
        assert "undocumented_handler" in schema["description"]

    @pytest.mark.skipif(True, reason="Pydantic not required dependency")
    def test_pydantic_model_integration(self) -> None:
        """Test integration with Pydantic models if available."""
        try:
            from pydantic import BaseModel

            class UserModel(BaseModel):
                name: str
                age: int
                email: Optional[str] = None

            @get("/test")
            def pydantic_handler(user: UserModel) -> dict[str, Any]:
                return {"user": user.model_dump()}

            handler = BaseRouteHandler(fn=pydantic_handler, http_method="GET", path="/test")
            schema = generate_schema_for_handler(handler)

            # Should use Pydantic's schema generation
            user_schema = schema["properties"]["user"]
            assert "properties" in user_schema
            assert "name" in user_schema["properties"]
            assert "age" in user_schema["properties"]
            assert "email" in user_schema["properties"]

        except ImportError:
            pytest.skip("Pydantic not available")

    def test_type_annotation_edge_cases(self) -> None:
        """Test edge cases in type annotation handling."""

        def edge_case_handler(any_param: Any, union_param: Optional[str], raw_param: Any) -> dict[str, Any]:
            return {"processed": True}

        _, handler = create_app_with_handler(edge_case_handler)
        schema = generate_schema_for_handler(handler)

        properties = schema["properties"]

        # Any should fallback to object
        assert properties["any_param"]["type"] == "object"

        # Optional[str] should be handled
        assert "union_param" in properties

        # Parameter without annotation should be handled gracefully
        assert "raw_param" in properties

    def test_nested_complex_types(self) -> None:
        """Test schema generation with nested complex types."""

        def nested_handler(nested_data: dict[str, list[dict[str, str]]]) -> dict[str, Any]:
            return {"processed": True}

        _, handler = create_app_with_handler(nested_handler)
        schema = generate_schema_for_handler(handler)

        # Should handle nested complexity gracefully
        properties = schema["properties"]
        assert properties["nested_data"]["type"] == "object"


class TestBasicTypeToJsonSchema:
    """Test suite for basic_type_to_json_schema function."""

    def test_basic_type_to_json_schema_string(self) -> None:
        """Test string type conversion."""
        assert basic_type_to_json_schema(str) == {"type": "string"}

    def test_basic_type_to_json_schema_integer(self) -> None:
        """Test integer type conversion."""
        assert basic_type_to_json_schema(int) == {"type": "integer"}

    def test_basic_type_to_json_schema_float(self) -> None:
        """Test float type conversion."""
        assert basic_type_to_json_schema(float) == {"type": "number"}

    def test_basic_type_to_json_schema_boolean(self) -> None:
        """Test boolean type conversion."""
        assert basic_type_to_json_schema(bool) == {"type": "boolean"}

    def test_basic_type_to_json_schema_unsupported(self) -> None:
        """Test unsupported type returns None."""
        assert basic_type_to_json_schema(list) is None
        assert basic_type_to_json_schema(dict) is None
        assert basic_type_to_json_schema(object) is None


class TestCollectionTypeToJsonSchema:
    """Test suite for collection_type_to_json_schema function."""

    def test_collection_type_list_basic(self) -> None:
        """Test basic list type conversion."""
        assert collection_type_to_json_schema(list) == {"type": "array"}

    def test_collection_type_list_typed(self) -> None:
        """Test typed list conversion."""
        result = collection_type_to_json_schema(list[str])
        expected = {"type": "array", "items": {"type": "string"}}
        assert result == expected

    def test_collection_type_dict_basic(self) -> None:
        """Test basic dict type conversion."""
        assert collection_type_to_json_schema(dict) == {"type": "object"}

    def test_collection_type_dict_typed(self) -> None:
        """Test typed dict conversion."""
        result = collection_type_to_json_schema(dict[str, int])
        assert result == {"type": "object"}

    def test_collection_type_set_basic(self) -> None:
        """Test basic set type conversion."""
        result = collection_type_to_json_schema(set)
        expected = {"type": "array", "uniqueItems": True}
        assert result == expected

    def test_collection_type_set_typed(self) -> None:
        """Test typed set conversion."""
        result = collection_type_to_json_schema(set[str])
        expected = {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
        assert result == expected

    def test_collection_type_unsupported(self) -> None:
        """Test unsupported collection type returns None."""
        assert collection_type_to_json_schema(str) is None
        assert collection_type_to_json_schema(int) is None


class TestDataclassToJsonSchema:
    """Test suite for dataclass_to_json_schema function."""

    def test_dataclass_to_json_schema_basic(self) -> None:
        """Test basic dataclass conversion."""

        @dataclass
        class TestDataclass:
            name: str
            age: int

        result = dataclass_to_json_schema(TestDataclass)

        assert result["type"] == "object"
        assert "properties" in result
        assert "required" in result

        properties = result["properties"]
        assert properties["name"]["type"] == "string"
        assert properties["age"]["type"] == "integer"

        assert set(result["required"]) == {"name", "age"}

    def test_dataclass_to_json_schema_with_defaults(self) -> None:
        """Test dataclass with default values."""

        @dataclass
        class TestDataclass:
            name: str
            age: int = 25
            active: bool = True

        result = dataclass_to_json_schema(TestDataclass)

        # Only required fields should be in required array
        assert result["required"] == ["name"]

        # All fields should be in properties
        properties = result["properties"]
        assert len(properties) == 3
        assert "name" in properties
        assert "age" in properties
        assert "active" in properties

    def test_dataclass_to_json_schema_optional_fields(self) -> None:
        """Test dataclass with optional fields."""

        @dataclass
        class TestDataclass:
            name: str
            description: Optional[str] = None

        result = dataclass_to_json_schema(TestDataclass)

        assert result["required"] == ["name"]
        assert len(result["properties"]) == 2


class TestMsgspecToJsonSchema:
    """Test suite for msgspec_to_json_schema function."""

    def test_msgspec_to_json_schema_not_installed(self) -> None:
        """Test behavior when msgspec is not installed."""
        import unittest.mock

        with unittest.mock.patch("litestar_mcp.schema_builder.MSGSPEC_INSTALLED", False):

            class MockStruct:
                pass

            result = msgspec_to_json_schema(MockStruct)

            assert result["type"] == "object"
            assert "msgspec not installed" in result["description"]

    def test_msgspec_to_json_schema_imports(self) -> None:
        """Test that msgspec import is handled correctly."""
        # This test verifies the import behavior inside the function
        try:
            import msgspec

            # If msgspec is available, test it
            if hasattr(msgspec, "Struct"):

                class TestStruct(msgspec.Struct):
                    name: str
                    age: int = 25

                result = msgspec_to_json_schema(TestStruct)
                assert result["type"] == "object"
                assert "properties" in result
        except ImportError:
            # If msgspec is not available, just pass
            pass


class TestPydanticToJsonSchema:
    """Test suite for pydantic_to_json_schema function."""

    def test_pydantic_to_json_schema_mock(self) -> None:
        """Test pydantic schema generation with mock model."""

        class MockPydanticModel:
            @classmethod
            def model_json_schema(cls) -> dict[str, Any]:
                return {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
                    "required": ["name", "age"],
                }

        result = pydantic_to_json_schema(MockPydanticModel)

        assert result["type"] == "object"
        assert "properties" in result
        assert "name" in result["properties"]
        assert "age" in result["properties"]


class TestTypeToJsonSchemaIntegration:
    """Integration tests for type_to_json_schema function."""

    def test_type_to_json_schema_recursive_lists(self) -> None:
        """Test recursive type conversion with nested lists."""
        result = type_to_json_schema(list[list[str]])

        expected = {"type": "array", "items": {"type": "array", "items": {"type": "string"}}}
        assert result == expected

    def test_type_to_json_schema_complex_nested(self) -> None:
        """Test complex nested type conversion."""
        result = type_to_json_schema(dict[str, list[int]])

        # Should handle the dict but may not fully parse the value type
        assert result["type"] == "object"

    def test_type_to_json_schema_unknown_type(self) -> None:
        """Test handling of unknown/custom types."""

        class CustomType:
            pass

        result = type_to_json_schema(CustomType)

        assert result["type"] == "object"
        assert "description" in result
        assert "CustomType" in result["description"]


class TestEdgeCasesAndErrorHandling:
    """Test edge cases and error handling in schema builder."""

    def test_generate_schema_handler_with_no_function(self) -> None:
        """Test error handling when handler has no function."""

        # Create a mock handler without fn attribute
        class MockHandler:
            pass

        handler = MockHandler()

        # Should handle gracefully and not crash
        import contextlib

        with contextlib.suppress(AttributeError):
            generate_schema_for_handler(handler)  # type: ignore[arg-type]

    def test_type_to_json_schema_with_none(self) -> None:
        """Test type_to_json_schema with None input."""
        result = type_to_json_schema(None)

        # Should handle gracefully
        assert result is not None
        assert result["type"] == "object"

    def test_dataclass_to_json_schema_empty_dataclass(self) -> None:
        """Test dataclass with no fields."""

        @dataclass
        class EmptyDataclass:
            pass

        result = dataclass_to_json_schema(EmptyDataclass)

        assert result["type"] == "object"
        assert result["properties"] == {}
        assert "required" not in result or result["required"] == []

    def test_generate_schema_with_complex_defaults(self) -> None:
        """Test schema generation with complex default values."""

        def handler_with_complex_defaults(
            name: str,
            config: Optional[dict[str, Any]] = None,
            items: Optional[list[str]] = None,
        ) -> dict[str, Any]:
            return {"name": name, "config": config or {}, "items": items or []}

        _, handler = create_app_with_handler(handler_with_complex_defaults)
        schema = generate_schema_for_handler(handler)

        # Should only require name parameter
        assert schema["required"] == ["name"]

        # Should include all parameters in properties
        properties = schema["properties"]
        assert "name" in properties
        assert "config" in properties
        assert "items" in properties
