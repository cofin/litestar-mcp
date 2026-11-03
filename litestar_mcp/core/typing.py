"""Public type surface for `litestar_mcp`.

Facade that re-exports:

- Type aliases and protocols: `DictLike`, `SupportedSchemaModel`.
- Type guards: `is_msgspec_struct`, `is_pydantic_model`,
  `is_dataclass`, `is_attrs_instance`, `is_attrs_schema`,
  `is_schema_model`, `is_dict` — canonical implementations in
  `litestar_mcp.utils.type_guards`.
- Schema dump: `schema_dump` — canonical implementation in
  `litestar_mcp.core.serialization`.
- Third-party re-exports: `AttrsInstance`, `BaseModel`,
  `DataclassProtocol`, `Struct` and the install-flag constants.

Backward-compat stability: existing `from litestar_mcp.core.typing import X`
imports continue to work unchanged.
"""

from typing import TYPE_CHECKING, Any, Protocol, TypeAlias

from litestar_mcp.core._typing import (
    ATTRS_INSTALLED,
    DISHKA_INSTALLED,
    MSGSPEC_INSTALLED,
    PYDANTIC_INSTALLED,
    AttrsInstance,
    AttrsInstanceStub,
    BaseModel,
    BaseModelStub,
    DataclassProtocol,
    DishkaDependencyKey,
    Struct,
    StructStub,
    attrs_fields,
)
from litestar_mcp.core.serialization import schema_dump
from litestar_mcp.utils.type_guards import (
    is_attrs_instance,
    is_attrs_schema,
    is_dataclass,
    is_dict,
    is_msgspec_struct,
    is_pydantic_model,
    is_schema_model,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


class DictLike(Protocol):
    """A protocol for objects that behave like a dictionary for reading."""

    def __getitem__(self, key: "str") -> "Any": ...
    def __iter__(self) -> "Iterator[str]": ...
    def __len__(self) -> "int": ...


SupportedSchemaModel: "TypeAlias" = DictLike | StructStub | BaseModelStub | DataclassProtocol | AttrsInstanceStub
"""Type alias for supported schema models.

`msgspec.Struct` | `pydantic.BaseModel` | `DataclassProtocol` | `AttrsInstance`
"""


__all__ = (
    "ATTRS_INSTALLED",
    "DISHKA_INSTALLED",
    "MSGSPEC_INSTALLED",
    "PYDANTIC_INSTALLED",
    "AttrsInstance",
    "BaseModel",
    "DataclassProtocol",
    "DictLike",
    "DishkaDependencyKey",
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
