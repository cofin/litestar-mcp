# pyright: ignore[reportAttributeAccessIssue]
from typing import TYPE_CHECKING, Any, Protocol, TypeAlias, TypeGuard, cast

from typing_extensions import TypeVar

from litestar_mcp._typing import (
    ATTRS_INSTALLED,
    MSGSPEC_INSTALLED,
    PYDANTIC_INSTALLED,
    AttrsInstance,
    AttrsInstanceStub,
    BaseModel,
    BaseModelStub,
    DataclassProtocol,
    Struct,
    StructStub,
    attrs_fields,
    attrs_has,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class DictLike(Protocol):
    """A protocol for objects that behave like a dictionary for reading."""

    def __getitem__(self, key: str) -> Any: ...
    def __iter__(self) -> "Iterator[str]": ...
    def __len__(self) -> int: ...


T = TypeVar("T")

SupportedSchemaModel: TypeAlias = "DictLike | StructStub | BaseModelStub | DataclassProtocol | AttrsInstanceStub"
"""Type alias for supported schema models.

:class:`msgspec.Struct` | :class:`pydantic.BaseModel` | :class:`DataclassProtocol` | :class:`AttrsInstance`
"""


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
    """Check if an object is a dataclass.

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
        else:
            return True
    # Check instance
    try:
        _ = type(obj).__dataclass_fields__  # pyright: ignore
    except AttributeError:
        return False
    else:
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


def is_schema_model(obj: Any) -> "TypeGuard[SupportedSchemaModel]":
    """Check if a value is a supported schema model (msgspec Struct, Pydantic model, attrs instance, or dataclass).

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


def schema_dump(data: Any, exclude_unset: bool = True) -> "dict[str, Any] | None":
    """Dump a data object to a dictionary via the cached serializer pipeline.

    Thin delegator preserving back-compat for existing
    ``from litestar_mcp.typing import schema_dump`` imports. The
    heavy lifting — type dispatch, caching, msgspec ``rename`` fidelity —
    lives in :mod:`litestar_mcp.utils.serialization`.

    Args:
        data: :type:`dict[str, Any]` | :class:`DataclassProtocol` | :class:`msgspec.Struct` | :class:`pydantic.BaseModel` | :class:`AttrsInstance`
        exclude_unset: :type:`bool` Whether to exclude unset values.

    Returns:
        :type:`dict[str, Any] | None`
    """
    from litestar_mcp.utils.serialization import schema_dump as _cached_dump

    return cast("dict[str, Any] | None", _cached_dump(data, exclude_unset=exclude_unset))


def _dataclass_to_dict(
    obj: "DataclassProtocol",
    exclude_none: bool = False,
    exclude_empty: bool = False,
    convert_nested: bool = True,
) -> "dict[str, Any]":
    """Convert a dataclass instance to a dictionary.

    Args:
        obj: A dataclass instance.
        exclude_none: Whether to exclude None values.
        exclude_empty: Whether to exclude Empty values.
        convert_nested: Whether to recursively convert nested dataclasses.

    Returns:
        A dictionary of key/value pairs.
    """
    from dataclasses import fields

    from litestar_mcp._typing import Empty

    ret: dict[str, Any] = {}
    for field in fields(obj):
        value = getattr(obj, field.name)

        if exclude_none and value is None:
            continue
        if exclude_empty and value is Empty:
            continue

        if is_dataclass(value) and convert_nested:
            ret[field.name] = _dataclass_to_dict(value, exclude_none, exclude_empty)
        else:
            ret[field.name] = value
    return ret


__all__ = (
    "ATTRS_INSTALLED",
    "MSGSPEC_INSTALLED",
    "PYDANTIC_INSTALLED",
    "AttrsInstance",
    "BaseModel",
    "DataclassProtocol",
    "DictLike",
    "Struct",
    "SupportedSchemaModel",
    "attrs_fields",
    "is_attrs_instance",
    "is_attrs_schema",
    "is_dataclass",
    "is_dict",
    "is_msgspec_struct",
    "is_pydantic_model",
    "is_schema_model",
    "schema_dump",
)
