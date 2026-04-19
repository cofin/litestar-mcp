"""Cached schema serializer with msgspec rename fidelity.

Type-keyed pipeline: the first ``schema_dump`` call for a given ``(type,
exclude_unset)`` builds a dispatch function once and caches it. Subsequent
calls reuse the cached :class:`SchemaSerializer` instead of re-dispatching
the if/elif chain on every value.

The msgspec dump path uses :func:`msgspec.structs.fields` so Structs declared
with ``rename=`` (``"camel"``, ``"kebab"``, ``"pascal"``, a callable, ...)
emit their wire-correct keys — the same contract msgspec's native encoder
honors. Previous ``__struct_fields__``-based dumps silently dropped rename.
"""

from functools import partial
from threading import RLock
from typing import TYPE_CHECKING, Any, Final, cast

from msgspec import structs as msgspec_structs

from litestar_mcp._typing import UNSET, attrs_asdict
from litestar_mcp.typing import (
    _dataclass_to_dict,
    is_attrs_instance,
    is_dataclass,
    is_dict,
    is_msgspec_struct,
    is_pydantic_model,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

__all__ = (
    "SchemaSerializer",
    "get_collection_serializer",
    "reset_serializer_cache",
    "schema_dump",
    "serialize_collection",
)

_PRIMITIVE_TYPES: Final[tuple[type[Any], ...]] = (str, bytes, int, float, bool)


def _dump_identity_dict(value: Any) -> "dict[str, Any]":
    return cast("dict[str, Any]", value)


def _dump_msgspec_fields(value: Any) -> "dict[str, Any]":
    """Dump every field, honoring ``rename=`` via :attr:`FieldInfo.encode_name`."""
    return {field.encode_name: value.__getattribute__(field.name) for field in msgspec_structs.fields(type(value))}


def _dump_msgspec_excluding_unset(value: Any) -> "dict[str, Any]":
    """Rename-aware dump with ``UNSET`` values stripped."""
    result: dict[str, Any] = {}
    for field in msgspec_structs.fields(type(value)):
        field_value = value.__getattribute__(field.name)
        if field_value is UNSET:
            continue
        result[field.encode_name] = field_value
    return result


def _dump_dataclass(value: Any, *, exclude_unset: bool) -> "dict[str, Any]":
    return _dataclass_to_dict(value, exclude_empty=exclude_unset)


def _dump_pydantic(value: Any, *, exclude_unset: bool) -> "dict[str, Any]":
    return cast("dict[str, Any]", value.model_dump(exclude_unset=exclude_unset))


def _dump_attrs(value: Any) -> "dict[str, Any]":
    return attrs_asdict(value, recurse=True)


def _dump_dict_attr(value: Any) -> "dict[str, Any]":
    return dict(value.__dict__)


def _dump_mapping(value: Any) -> "dict[str, Any]":
    return dict(value)


class SchemaSerializer:
    """Dispatch wrapper cached per ``(type, exclude_unset)``."""

    __slots__ = ("_dump", "_key")

    def __init__(
        self,
        key: "tuple[type[Any] | None, bool]",
        dump: "Callable[[Any], dict[str, Any]]",
    ) -> None:
        """Initialize the wrapper.

        Args:
            key: ``(type, exclude_unset)`` — ``type`` is ``None`` for dict/``None`` inputs.
            dump: Pre-built dumper function for items of that type.
        """
        self._key = key
        self._dump = dump

    @property
    def key(self) -> "tuple[type[Any] | None, bool]":
        """The cache key this pipeline was built for."""
        return self._key

    def dump_one(self, item: Any) -> "dict[str, Any]":
        """Serialize a single item using the cached dispatcher."""
        return self._dump(item)

    def dump_many(self, items: "Iterable[Any]") -> "list[dict[str, Any]]":
        """Serialize each item in ``items`` using the cached dispatcher."""
        return [self._dump(item) for item in items]


_SERIALIZER_LOCK: RLock = RLock()
_SCHEMA_SERIALIZERS: "dict[tuple[type[Any] | None, bool], SchemaSerializer]" = {}


def _make_serializer_key(sample: Any, exclude_unset: bool) -> "tuple[type[Any] | None, bool]":
    if sample is None or isinstance(sample, dict):
        return (None, exclude_unset)
    return (type(sample), exclude_unset)


def _build_dump_function(sample: Any, exclude_unset: bool) -> "Callable[[Any], dict[str, Any]]":  # noqa: PLR0911
    """Choose the dump function for ``sample``'s type, frozen at cache time."""
    if sample is None or isinstance(sample, dict):
        return _dump_identity_dict
    if is_dataclass(sample):
        return cast("Callable[[Any], dict[str, Any]]", partial(_dump_dataclass, exclude_unset=exclude_unset))
    if is_pydantic_model(sample):
        return cast("Callable[[Any], dict[str, Any]]", partial(_dump_pydantic, exclude_unset=exclude_unset))
    if is_msgspec_struct(sample):
        return _dump_msgspec_excluding_unset if exclude_unset else _dump_msgspec_fields
    if is_attrs_instance(sample):
        return _dump_attrs
    if hasattr(sample, "__dict__"):
        return _dump_dict_attr
    return _dump_mapping


def get_collection_serializer(sample: Any, *, exclude_unset: bool = True) -> "SchemaSerializer":
    """Return (and cache) a :class:`SchemaSerializer` for ``sample``'s type.

    The cache key is ``(type(sample), exclude_unset)`` — dicts and ``None``
    share a ``(None, exclude_unset)`` key so repeated pass-through inputs
    don't bloat the cache.

    Args:
        sample: Representative value used to pick the dispatch function.
        exclude_unset: Whether to strip ``UNSET`` values (msgspec) or pass
            ``exclude_unset`` through to pydantic's ``model_dump``.

    Returns:
        A cached pipeline that emits ``dict[str, Any]`` for each item.
    """
    key = _make_serializer_key(sample, exclude_unset)
    with _SERIALIZER_LOCK:
        pipeline = _SCHEMA_SERIALIZERS.get(key)
        if pipeline is not None:
            return pipeline
        dump = _build_dump_function(sample, exclude_unset)
        pipeline = SchemaSerializer(key, dump)
        _SCHEMA_SERIALIZERS[key] = pipeline
        return pipeline


def serialize_collection(items: "Iterable[Any]", *, exclude_unset: bool = True) -> "list[Any]":
    """Serialize an iterable of heterogeneous items.

    Primitives and ``None`` pass through unchanged. Remaining items are
    dispatched via :func:`get_collection_serializer`; a local cache reuses
    pipelines across items within the iteration to avoid repeated global
    lock acquisitions.

    Args:
        items: Heterogeneous iterable.
        exclude_unset: Forwarded to each per-type pipeline.

    Returns:
        A list where each non-primitive item has been dumped to ``dict``.
    """
    serialized: list[Any] = []
    local_cache: dict[tuple[type[Any] | None, bool], SchemaSerializer] = {}
    for item in items:
        if isinstance(item, _PRIMITIVE_TYPES) or item is None or isinstance(item, dict):
            serialized.append(item)
            continue
        key = _make_serializer_key(item, exclude_unset)
        pipeline = local_cache.get(key)
        if pipeline is None:
            pipeline = get_collection_serializer(item, exclude_unset=exclude_unset)
            local_cache[key] = pipeline
        serialized.append(pipeline.dump_one(item))
    return serialized


def reset_serializer_cache() -> None:
    """Clear the global serializer cache.

    Useful in tests that mutate type metadata, and in long-running processes
    reloading modules at runtime.
    """
    with _SERIALIZER_LOCK:
        _SCHEMA_SERIALIZERS.clear()


def schema_dump(data: Any, *, exclude_unset: bool = True) -> Any:
    """Dump ``data`` to a JSON-friendly form via the cached pipeline.

    - Primitives and ``None`` pass through unchanged.
    - Dicts pass through unchanged.
    - Dataclass / pydantic / msgspec / attrs instances route through a
      cached per-type dumper. msgspec rename is honored.
    - Objects with ``__dict__`` fall back to ``dict(value.__dict__)``.

    Args:
        data: Value to dump.
        exclude_unset: Whether to strip ``UNSET`` / empty sentinel values.

    Returns:
        A ``dict`` for schema instances, the original ``data`` for primitives
        and dicts.
    """
    if is_dict(data):
        return data
    if isinstance(data, _PRIMITIVE_TYPES) or data is None:
        return data
    return get_collection_serializer(data, exclude_unset=exclude_unset).dump_one(data)
