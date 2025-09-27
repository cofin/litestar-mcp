# ruff: noqa: RUF100, PLR0913, A002, DOC201, PLR6301, PLR0917, ARG004, ARG002, ARG001
"""Wrapper around library classes for compatibility when libraries are installed."""

import enum
from enum import Enum
from typing import Any, ClassVar, Optional, Protocol, Union, runtime_checkable

from typing_extensions import Literal, TypeVar, dataclass_transform


@runtime_checkable
class DataclassProtocol(Protocol):
    """Protocol for instance checking dataclasses."""

    __dataclass_fields__: "ClassVar[dict[str, Any]]"


T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)

# Always define stub types for type checking


class BaseModelStub:
    """Placeholder implementation for Pydantic BaseModel."""

    model_fields: ClassVar[dict[str, Any]] = {}
    __slots__ = ("__dict__", "__pydantic_extra__", "__pydantic_fields_set__", "__pydantic_private__")

    def __init__(self, **data: Any) -> None:
        self.__dict__.update(data)

    def model_dump(  # noqa: PLR0913
        self,
        /,
        *,
        include: "Optional[Any]" = None,  # noqa: ARG002
        exclude: "Optional[Any]" = None,  # noqa: ARG002
        context: "Optional[Any]" = None,  # noqa: ARG002
        by_alias: bool = False,  # noqa: ARG002
        exclude_unset: bool = False,  # noqa: ARG002
        exclude_defaults: bool = False,  # noqa: ARG002
        exclude_none: bool = False,  # noqa: ARG002
        round_trip: bool = False,  # noqa: ARG002
        warnings: "Union[bool, Literal['none', 'warn', 'error']]" = True,  # noqa: ARG002
        serialize_as_any: bool = False,  # noqa: ARG002
    ) -> "dict[str, Any]":
        """Placeholder implementation."""
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    def model_json_schema(  # noqa: PLR0913
        self,
        by_alias: bool = True,  # noqa: ARG002
        ref_template: str = "#/$defs/{model}",  # noqa: ARG002
        schema_generator: "Optional[Any]" = None,  # noqa: ARG002
        mode: str = "validation",  # noqa: ARG002
    ) -> "dict[str, Any]":
        """Placeholder implementation for JSON schema generation."""
        return {"type": "object", "properties": {}, "description": "Pydantic model not available"}


# Try to import real implementations at runtime
try:
    from pydantic import BaseModel as _RealBaseModel

    BaseModel = _RealBaseModel
    PYDANTIC_INSTALLED = True  # pyright: ignore[reportConstantRedefinition]
except ImportError:
    BaseModel = BaseModelStub  # type: ignore[assignment,misc]
    PYDANTIC_INSTALLED = False  # pyright: ignore[reportConstantRedefinition]

# Always define stub types for msgspec


@dataclass_transform()
class StructStub:
    """Placeholder implementation for msgspec Struct."""

    __struct_fields__: ClassVar[tuple[str, ...]] = ()
    __slots__ = ()

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def convert_stub(  # noqa: PLR0913
    obj: Any,  # noqa: ARG001
    type: Any,  # noqa: A002,ARG001
    *,
    strict: bool = True,  # noqa: ARG001
    from_attributes: bool = False,  # noqa: ARG001
    dec_hook: "Optional[Any]" = None,  # noqa: ARG001
    builtin_types: "Optional[Any]" = None,  # noqa: ARG001
    str_keys: bool = False,  # noqa: ARG001
) -> Any:
    """Placeholder implementation."""
    return {}


class UnsetTypeStub(enum.Enum):
    UNSET = "UNSET"


UNSET_STUB = UnsetTypeStub.UNSET

# Try to import real implementations at runtime
try:
    from msgspec import UNSET as _REAL_UNSET
    from msgspec import Struct as _RealStruct
    from msgspec import UnsetType as _RealUnsetType
    from msgspec import convert as _real_convert

    Struct = _RealStruct
    UnsetType = _RealUnsetType
    UNSET = _REAL_UNSET
    convert = _real_convert
    MSGSPEC_INSTALLED = True  # pyright: ignore[reportConstantRedefinition]
except ImportError:
    Struct = StructStub  # type: ignore[assignment,misc]
    UnsetType = UnsetTypeStub  # type: ignore[assignment,misc]
    UNSET = UNSET_STUB  # type: ignore[assignment] # pyright: ignore[reportConstantRedefinition]
    convert = convert_stub
    MSGSPEC_INSTALLED = False  # pyright: ignore[reportConstantRedefinition]


# Always define stub type for attrs
@dataclass_transform()
class AttrsInstanceStub:
    """Placeholder Implementation for attrs classes"""

    __attrs_attrs__: ClassVar[tuple[Any, ...]] = ()
    __slots__ = ()

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


def attrs_asdict_stub(*args: Any, **kwargs: Any) -> "dict[str, Any]":  # noqa: ARG001
    """Placeholder implementation"""
    return {}


def attrs_define_stub(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001
    """Placeholder implementation"""
    return lambda cls: cls  # pyright: ignore[reportUnknownVariableType,reportUnknownLambdaType]


def attrs_field_stub(*args: Any, **kwargs: Any) -> Any:  # noqa: ARG001
    """Placeholder implementation"""
    return None


def attrs_fields_stub(*args: Any, **kwargs: Any) -> "tuple[Any, ...]":  # noqa: ARG001
    """Placeholder implementation"""
    return ()


def attrs_has_stub(*args: Any, **kwargs: Any) -> bool:  # noqa: ARG001
    """Placeholder implementation"""
    return False


# Try to import real implementations at runtime
try:
    from attrs import AttrsInstance as _RealAttrsInstance  # pyright: ignore
    from attrs import asdict as _real_attrs_asdict
    from attrs import define as _real_attrs_define
    from attrs import field as _real_attrs_field
    from attrs import fields as _real_attrs_fields
    from attrs import has as _real_attrs_has

    AttrsInstance = _RealAttrsInstance
    attrs_asdict = _real_attrs_asdict
    attrs_define = _real_attrs_define
    attrs_field = _real_attrs_field
    attrs_fields = _real_attrs_fields
    attrs_has = _real_attrs_has
    ATTRS_INSTALLED = True  # pyright: ignore[reportConstantRedefinition]
except ImportError:
    AttrsInstance = AttrsInstanceStub  # type: ignore[misc]
    attrs_asdict = attrs_asdict_stub
    attrs_define = attrs_define_stub
    attrs_field = attrs_field_stub
    attrs_fields = attrs_fields_stub
    attrs_has = attrs_has_stub  # type: ignore[assignment]
    ATTRS_INSTALLED = False  # pyright: ignore[reportConstantRedefinition]


class EmptyEnum(Enum):
    """A sentinel enum used as placeholder."""

    EMPTY = 0


EmptyType = Union[Literal[EmptyEnum.EMPTY], UnsetType]
Empty = EmptyEnum.EMPTY


__all__ = (
    "ATTRS_INSTALLED",
    "MSGSPEC_INSTALLED",
    "PYDANTIC_INSTALLED",
    "UNSET",
    "UNSET_STUB",
    "AttrsInstance",
    "AttrsInstanceStub",
    "BaseModel",
    "BaseModelStub",
    "DataclassProtocol",
    "Empty",
    "EmptyEnum",
    "EmptyType",
    "Struct",
    "StructStub",
    "T",
    "T_co",
    "UnsetType",
    "UnsetTypeStub",
    "attrs_asdict",
    "attrs_asdict_stub",
    "attrs_define",
    "attrs_define_stub",
    "attrs_field",
    "attrs_field_stub",
    "attrs_fields",
    "attrs_fields_stub",
    "attrs_has",
    "attrs_has_stub",
    "convert",
    "convert_stub",
)
