"""Type-identification helpers for schema-library dispatch.

:mod:`litestar_mcp.schema_builder` uses these to branch on the return
annotation kind (msgspec / pydantic / attrs / dataclass) when building MCP
input/output schemas. They are the "which schema library is this" guards —
distinct from :mod:`litestar_mcp.utils.serialization`, which handles
*runtime* encoding.

All guards accept both instances and classes; ``is_schema_model`` unions the
four per-library branches for the common "is this any model class" case.
"""

from typing import Any, TypeGuard

from litestar_mcp._typing import (
    ATTRS_INSTALLED,
    MSGSPEC_INSTALLED,
    PYDANTIC_INSTALLED,
    AttrsInstanceStub,
    BaseModel,
    BaseModelStub,
    DataclassProtocol,
    Struct,
    StructStub,
    attrs_has,
)

__all__ = (
    "is_attrs_instance",
    "is_attrs_schema",
    "is_dataclass",
    "is_dict",
    "is_msgspec_struct",
    "is_pydantic_model",
    "is_schema_model",
)


def is_pydantic_model(obj: Any) -> "TypeGuard[BaseModelStub]":
    """Check if a value is a pydantic model class or instance.

    Args:
        obj: Value to check.

    Returns:
        bool
    """
    if not PYDANTIC_INSTALLED:
        return False
    if isinstance(obj, type):
        try:
            return issubclass(obj, BaseModel)
        except TypeError:
            return False
    return isinstance(obj, BaseModel)


def is_msgspec_struct(obj: Any) -> "TypeGuard[StructStub]":
    """Check if a value is a msgspec struct class or instance.

    Args:
        obj: Value to check.

    Returns:
        bool
    """
    if not MSGSPEC_INSTALLED:
        return False
    if isinstance(obj, type):
        try:
            return issubclass(obj, Struct)
        except TypeError:
            return False
    return isinstance(obj, Struct)


def is_dataclass(obj: Any) -> "TypeGuard[DataclassProtocol]":
    """Check if an object is a dataclass (class or instance).

    Args:
        obj: Value to check.

    Returns:
        bool
    """
    if isinstance(obj, type):
        try:
            _ = obj.__dataclass_fields__  # type: ignore[attr-defined]
        except AttributeError:
            return False
        return True
    try:
        _ = type(obj).__dataclass_fields__  # pyright: ignore
    except AttributeError:
        return False
    return True


def is_attrs_instance(obj: Any) -> "TypeGuard[AttrsInstanceStub]":
    """Check if a value is an attrs class instance.

    Args:
        obj: Value to check.

    Returns:
        bool
    """
    return ATTRS_INSTALLED and attrs_has(obj.__class__)


def is_attrs_schema(cls: Any) -> "TypeGuard[type[AttrsInstanceStub]]":
    """Check if a class type is an attrs schema.

    Args:
        cls: Class to check.

    Returns:
        bool
    """
    return ATTRS_INSTALLED and attrs_has(cls)


def is_schema_model(obj: Any) -> "TypeGuard[Any]":
    """Check if a value is a supported schema model (msgspec/pydantic/attrs/dataclass).

    Args:
        obj: Value to check.

    Returns:
        bool
    """
    return (
        is_msgspec_struct(obj)
        or is_pydantic_model(obj)
        or is_attrs_instance(obj)
        or is_attrs_schema(obj)
        or is_dataclass(obj)
    )


def is_dict(obj: Any) -> "TypeGuard[dict[str, Any]]":
    """Check if a value is a dictionary.

    Args:
        obj: Value to check.

    Returns:
        bool
    """
    return isinstance(obj, dict)
