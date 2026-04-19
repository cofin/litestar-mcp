"""Public type surface for :mod:`litestar_mcp`.

Facade that re-exports:

- Type aliases and protocols: :class:`DictLike`, :data:`SupportedSchemaModel`.
- Type guards: :func:`is_msgspec_struct`, :func:`is_pydantic_model`,
  :func:`is_dataclass`, :func:`is_attrs_instance`, :func:`is_attrs_schema`,
  :func:`is_schema_model`, :func:`is_dict` — canonical implementations in
  :mod:`litestar_mcp.utils.type_guards`.
- Schema dump: :func:`schema_dump` — canonical implementation in
  :mod:`litestar_mcp.utils.serialization`.
- Third-party re-exports: :class:`AttrsInstance`, :class:`BaseModel`,
  :class:`DataclassProtocol`, :class:`Struct` and the install-flag constants.

Backward-compat stability: existing ``from litestar_mcp.typing import X``
imports continue to work unchanged.
"""

from typing import TYPE_CHECKING, Any, Protocol, TypeAlias

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
)
from litestar_mcp.utils.serialization import schema_dump
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

    def __getitem__(self, key: str) -> Any: ...
    def __iter__(self) -> "Iterator[str]": ...
    def __len__(self) -> int: ...


SupportedSchemaModel: TypeAlias = "DictLike | StructStub | BaseModelStub | DataclassProtocol | AttrsInstanceStub"
"""Type alias for supported schema models.

:class:`msgspec.Struct` | :class:`pydantic.BaseModel` | :class:`DataclassProtocol` | :class:`AttrsInstance`
"""


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
