"""Tests for litestar_mcp.typing module - simplified version."""

from dataclasses import dataclass
from typing import Any, Optional

import pytest

from litestar_mcp._typing import BaseModelStub, DataclassProtocol
from litestar_mcp.typing import (
    is_attrs_instance,
    is_dataclass,
    is_dict,
    is_msgspec_struct,
    is_pydantic_model,
    is_schema_model,
    schema_dump,
)


@dataclass
class SampleDataclass:
    """Sample dataclass for testing."""

    name: str
    age: int
    optional_field: Optional[str] = None


class TestBasicTypeGuards:
    """Test basic type guard functions."""

    def test_is_dataclass_with_instance(self) -> None:
        """Test is_dataclass with dataclass instance."""
        instance = SampleDataclass(name="test", age=25)
        assert is_dataclass(instance) is True

    def test_is_dataclass_with_class(self) -> None:
        """Test is_dataclass with dataclass class."""
        assert is_dataclass(SampleDataclass) is True

    def test_is_dataclass_with_non_dataclass(self) -> None:
        """Test is_dataclass with non-dataclass objects."""
        assert is_dataclass("not a dataclass") is False
        assert is_dataclass(42) is False
        assert is_dataclass({}) is False

    def test_is_dict_with_dict(self) -> None:
        """Test is_dict with dictionary."""
        assert is_dict({}) is True
        assert is_dict({"key": "value"}) is True

    def test_is_dict_with_non_dict(self) -> None:
        """Test is_dict with non-dictionary."""
        assert is_dict("not a dict") is False
        assert is_dict([]) is False
        assert is_dict(42) is False

    def test_is_schema_model_with_dataclass(self) -> None:
        """Test is_schema_model with dataclass."""
        instance = SampleDataclass(name="test", age=25)
        assert is_schema_model(instance) is True

    def test_is_schema_model_with_non_schema(self) -> None:
        """Test is_schema_model with non-schema objects."""
        assert is_schema_model("not a schema") is False
        assert is_schema_model(42) is False

    def test_pydantic_not_available(self) -> None:
        """Test pydantic type guard when not available."""
        assert is_pydantic_model("not a model") is False
        assert is_pydantic_model({}) is False

    def test_msgspec_not_available(self) -> None:
        """Test msgspec type guard when not available."""
        assert is_msgspec_struct("not a struct") is False
        assert is_msgspec_struct({}) is False

    def test_attrs_not_available(self) -> None:
        """Test attrs type guard when not available."""
        assert is_attrs_instance("not attrs") is False
        assert is_attrs_instance({}) is False


class TestSchemaDump:
    """Test schema_dump function."""

    def test_schema_dump_with_dict(self) -> None:
        """Test schema_dump with dict input."""
        data = {"name": "test", "age": 25}
        result = schema_dump(data)
        assert result is data

    def test_schema_dump_with_dataclass(self) -> None:
        """Test schema_dump with dataclass input."""
        instance = SampleDataclass(name="test", age=25)
        result = schema_dump(instance)

        expected = {"name": "test", "age": 25, "optional_field": None}
        assert result == expected

    def test_schema_dump_with_none(self) -> None:
        """Test schema_dump with None input."""
        result = schema_dump(None)
        assert result is None


class TestBaseModelStub:
    """Test BaseModelStub functionality."""

    def test_basemodel_stub_init(self) -> None:
        """Test BaseModelStub initialization."""
        model = BaseModelStub(name="test", age=25)
        assert model.name == "test"  # type: ignore[attr-defined]
        assert model.age == 25  # type: ignore[attr-defined]

    def test_basemodel_stub_model_dump(self) -> None:
        """Test BaseModelStub model_dump."""
        model = BaseModelStub(name="test", age=25)
        result = model.model_dump()
        assert result == {"name": "test", "age": 25}

    def test_basemodel_stub_model_fields(self) -> None:
        """Test BaseModelStub has model_fields."""
        assert hasattr(BaseModelStub, "model_fields")
        assert BaseModelStub.model_fields == {}


class TestDataclassProtocol:
    """Test DataclassProtocol."""

    def test_dataclass_protocol(self) -> None:
        """Test DataclassProtocol with dataclass."""
        instance = SampleDataclass(name="test", age=25)
        assert isinstance(instance, DataclassProtocol)
        assert hasattr(instance, "__dataclass_fields__")


class TestEdgeCases:
    """Test edge cases."""

    def test_type_guards_with_none(self) -> None:
        """Test type guards with None."""
        assert is_dict(None) is False
        assert is_dataclass(None) is False
        assert is_schema_model(None) is False
        assert is_pydantic_model(None) is False
        assert is_msgspec_struct(None) is False
        assert is_attrs_instance(None) is False

    def test_type_guards_with_empty_containers(self) -> None:
        """Test type guards with empty containers."""
        assert is_dict({}) is True
        assert is_dataclass([]) is False
        assert is_schema_model([]) is False


@pytest.mark.parametrize(
    "guard_func,test_obj,expected",
    [
        (is_dict, {}, True),
        (is_dict, [], False),
        (is_dataclass, SampleDataclass("test", 25), True),
        (is_dataclass, {}, False),
        (is_schema_model, SampleDataclass("test", 25), True),
        (is_schema_model, {}, False),
    ],
    ids=["dict_true", "dict_false", "dataclass_true", "dataclass_false", "schema_true", "schema_false"],
)
def test_type_guard_performance(guard_func: Any, test_obj: Any, expected: bool) -> None:
    """Test type guard performance."""
    for _ in range(10):  # Reduced iterations for faster tests
        result = guard_func(test_obj)
        assert result == expected
