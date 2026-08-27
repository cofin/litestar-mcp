"""Type guards, protocols, and stub models for multi-library schema reflection."""

import dataclasses
import enum
from collections.abc import Iterator
from enum import Enum
from typing import Any, ClassVar, Literal, Protocol, TypeAlias, TypeGuard, runtime_checkable

from typing_extensions import TypeVar, dataclass_transform

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


@runtime_checkable
class DataclassProtocol(Protocol):
    """Protocol for instance checking dataclasses."""

    __dataclass_fields__: ClassVar[dict[str, Any]]


class DictLike(Protocol):
    """A protocol for objects that behave like a dictionary for reading."""

    def __getitem__(self, key: str) -> Any: ...
    def __iter__(self) -> Iterator[str]: ...
    def __len__(self) -> int: ...


class BaseModelStub:
    """Placeholder implementation for Pydantic BaseModel."""

    model_fields: ClassVar[dict[str, Any]] = {}
    __slots__ = ("__dict__", "__pydantic_extra__", "__pydantic_fields_set__", "__pydantic_private__")

    def __init__(self, **data: Any) -> None:
        self.__dict__.update(data)

    def model_dump(
        self,
        /,
        *,
        include: Any | None = None,
        exclude: Any | None = None,
        context: Any | None = None,
        by_alias: bool = False,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        round_trip: bool = False,
        warnings: bool | Literal["none", "warn", "error"] = True,
        serialize_as_any: bool = False,
    ) -> dict[str, Any]:
        """Placeholder dictionary dump."""
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    def model_json_schema(
        self,
        by_alias: bool = True,
        ref_template: str = "#/$defs/{model}",
        schema_generator: Any | None = None,
        mode: str = "validation",
    ) -> dict[str, Any]:
        """Placeholder JSON schema generation."""
        return {"type": "object", "properties": {}, "description": "Pydantic model not available"}


BaseModel: Any
try:
    from pydantic import BaseModel as _RealBaseModel

    BaseModel = _RealBaseModel
    PYDANTIC_INSTALLED = True
except ImportError:
    BaseModel = BaseModelStub
    PYDANTIC_INSTALLED = False


@dataclass_transform()
class StructStub:
    """Placeholder implementation for msgspec Struct."""

    __struct_fields__: ClassVar[tuple[str, ...]] = ()
    __slots__ = ()

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def convert_stub(
    _obj: Any,
    _type: Any,
    *,
    strict: bool = True,
    from_attributes: bool = False,
    dec_hook: Any | None = None,
    builtin_types: Any | None = None,
    str_keys: bool = False,
) -> Any:
    """Placeholder converter."""
    _ = (strict, from_attributes, dec_hook, builtin_types, str_keys)
    return {}


class UnsetTypeStub(enum.Enum):
    UNSET = "UNSET"


UNSET_STUB = UnsetTypeStub.UNSET

Struct: Any
UnsetType: Any
UNSET: Any
convert: Any
try:
    from msgspec import UNSET as _REAL_UNSET
    from msgspec import Struct as _RealStruct
    from msgspec import UnsetType as _RealUnsetType
    from msgspec import convert as _real_convert

    Struct = _RealStruct
    UnsetType = _RealUnsetType
    UNSET = _REAL_UNSET
    convert = _real_convert
    MSGSPEC_INSTALLED = True
except ImportError:
    Struct = StructStub
    UnsetType = UnsetTypeStub
    UNSET = UNSET_STUB
    convert = convert_stub
    MSGSPEC_INSTALLED = False


@dataclass_transform()
class AttrsInstanceStub:
    """Placeholder Implementation for attrs classes."""

    __attrs_attrs__: ClassVar[tuple[Any, ...]] = ()
    __slots__ = ()

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


def attrs_asdict_stub(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    """Placeholder dictionary conversion for attrs."""
    return {}


def attrs_define_stub(*_args: Any, **_kwargs: Any) -> Any:
    """Placeholder class decorator for attrs."""
    return lambda cls: cls


def attrs_field_stub(*_args: Any, **_kwargs: Any) -> Any:
    """Placeholder field definition for attrs."""
    return None


def attrs_fields_stub(*_args: Any, **_kwargs: Any) -> tuple[Any, ...]:
    """Placeholder fields accessor for attrs."""
    return ()


def attrs_has_stub(*_args: Any, **_kwargs: Any) -> bool:
    """Placeholder type check for attrs."""
    return False


AttrsInstance: Any
attrs_has: Any
try:
    from attrs import AttrsInstance as _RealAttrsInstance
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
    ATTRS_INSTALLED = True
except ImportError:
    AttrsInstance = AttrsInstanceStub
    attrs_asdict = attrs_asdict_stub
    attrs_define = attrs_define_stub
    attrs_field = attrs_field_stub
    attrs_fields = attrs_fields_stub
    attrs_has = attrs_has_stub
    ATTRS_INSTALLED = False


class DishkaDependencyKeyStub:
    """Placeholder implementation for Dishka DependencyKey."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        pass


DishkaDependencyKey: Any
try:
    from dishka.entities.key import DependencyKey as DishkaDependencyKey

    DISHKA_INSTALLED = True
except ImportError:
    DishkaDependencyKey = DishkaDependencyKeyStub
    DISHKA_INSTALLED = False


class EmptyEnum(Enum):
    """A sentinel enum used as placeholder."""

    EMPTY = 0


EmptyType = Literal[EmptyEnum.EMPTY] | UnsetType
Empty = EmptyEnum.EMPTY

SupportedSchemaModel: TypeAlias = DictLike | StructStub | BaseModelStub | DataclassProtocol | AttrsInstanceStub


def is_pydantic_model(obj: Any) -> TypeGuard[BaseModelStub]:
    """Check if a value is a pydantic model class or instance."""
    if not PYDANTIC_INSTALLED:
        return False
    if isinstance(obj, type):
        try:
            return issubclass(obj, BaseModel)
        except TypeError:
            return False
    return isinstance(obj, BaseModel)


def is_msgspec_struct(obj: Any) -> TypeGuard[StructStub]:
    """Check if a value is a msgspec struct class or instance."""
    if not MSGSPEC_INSTALLED:
        return False
    if isinstance(obj, type):
        try:
            return issubclass(obj, Struct)
        except TypeError:
            return False
    return isinstance(obj, Struct)


def is_dataclass(obj: Any) -> TypeGuard[DataclassProtocol]:
    """Check if an object is a dataclass (class or instance)."""
    return dataclasses.is_dataclass(obj)


def is_attrs_instance(obj: Any) -> TypeGuard[AttrsInstanceStub]:
    """Check if a value is an attrs class instance."""
    return ATTRS_INSTALLED and attrs_has(obj.__class__)


def is_attrs_schema(cls: Any) -> TypeGuard[type[AttrsInstanceStub]]:
    """Check if a class type is an attrs schema."""
    return ATTRS_INSTALLED and attrs_has(cls)


def is_schema_model(obj: Any) -> TypeGuard[Any]:
    """Check if a value is a supported schema model."""
    return (
        is_msgspec_struct(obj)
        or is_pydantic_model(obj)
        or is_attrs_instance(obj)
        or is_attrs_schema(obj)
        or is_dataclass(obj)
    )


def is_dict(obj: Any) -> TypeGuard[dict[str, Any]]:
    """Check if a value is a dictionary."""
    return isinstance(obj, dict)


__all__ = (
    "ATTRS_INSTALLED",
    "DISHKA_INSTALLED",
    "MSGSPEC_INSTALLED",
    "PYDANTIC_INSTALLED",
    "UNSET",
    "UNSET_STUB",
    "AttrsInstance",
    "AttrsInstanceStub",
    "BaseModel",
    "BaseModelStub",
    "DataclassProtocol",
    "DictLike",
    "DishkaDependencyKey",
    "DishkaDependencyKeyStub",
    "Empty",
    "EmptyEnum",
    "EmptyType",
    "Struct",
    "StructStub",
    "SupportedSchemaModel",
    "T",
    "T_co",
    "UnsetType",
    "UnsetTypeStub",
    "attrs_asdict",
    "attrs_define",
    "attrs_field",
    "attrs_fields",
    "attrs_has",
    "convert",
    "is_attrs_instance",
    "is_attrs_schema",
    "is_dataclass",
    "is_dict",
    "is_msgspec_struct",
    "is_pydantic_model",
    "is_schema_model",
)
