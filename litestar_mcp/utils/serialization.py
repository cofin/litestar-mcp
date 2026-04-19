"""Cached schema serializer delegating to Litestar's native encoder pipeline.

Type-keyed pipeline: the first ``schema_dump`` call for a given
``(type, exclude_unset, id(type_encoders))`` triple builds a dispatch function
once and caches it. Subsequent calls reuse the cached
:class:`SchemaSerializer` instead of rebuilding per value.

Each dumper routes through :func:`litestar.serialization.get_serializer` +
:class:`msgspec.json.Encoder` — the same pipeline Litestar's ASGI response
path uses. That means:

- ``msgspec.Struct`` with ``rename="camel"`` emits ``{"fooBar": ...}`` (the
  wire-correct keys msgspec's native encoder produces).
- ``@dataclass``, ``pydantic.BaseModel``, ``@attrs.define``, ``datetime``,
  ``UUID``, ``Decimal``, ``Path``, and anything else Litestar's default
  serializer knows, just works.
- Custom types register once via ``type_encoders={MyType: fn}`` on the route
  decorator; the map is threaded through :func:`schema_dump` via the
  ``type_encoders=`` kwarg and picked up by ``get_serializer``. Exactly the
  same registration path HTTP dispatch honors.

msgspec's encoder unconditionally filters ``UNSET`` values at the wire level.
``exclude_unset=False`` is accepted for back-compat but is effectively a
no-op for ``Struct`` types — the wire bytes never carry ``UNSET``. Pydantic's
``model_dump(exclude_unset=...)`` semantics ARE honored before encoding.
"""

from threading import RLock
from typing import TYPE_CHECKING, Any, Final, cast

import msgspec
from litestar.serialization import get_serializer

from litestar_mcp._typing import PYDANTIC_INSTALLED, BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

__all__ = (
    "SchemaSerializer",
    "get_collection_serializer",
    "reset_serializer_cache",
    "schema_dump",
    "serialize_collection",
)

_PRIMITIVE_TYPES: Final[tuple[type[Any], ...]] = (str, bytes, int, float, bool)


def _dump_identity_dict(value: Any) -> "dict[str, Any]":
    """Pass-through for ``dict``/``None`` inputs."""
    return cast("dict[str, Any]", value)


class SchemaSerializer:
    """Dispatch wrapper cached per ``(type, exclude_unset, encoder_map_id)``."""

    __slots__ = ("_dump", "_key")

    def __init__(
        self,
        key: "tuple[type[Any] | None, bool, int | None]",
        dump: "Callable[[Any], Any]",
    ) -> None:
        """Initialize the wrapper.

        Args:
            key: ``(type, exclude_unset, encoder_map_id)`` cache key.
            dump: Pre-built dumper closure for items of that type.
        """
        self._key = key
        self._dump = dump

    @property
    def key(self) -> "tuple[type[Any] | None, bool, int | None]":
        """The cache key this pipeline was built for."""
        return self._key

    def dump_one(self, item: Any) -> Any:
        """Serialize a single item using the cached dispatcher."""
        return self._dump(item)

    def dump_many(self, items: "Iterable[Any]") -> "list[Any]":
        """Serialize each item in ``items`` using the cached dispatcher."""
        return [self._dump(item) for item in items]


_SERIALIZER_LOCK: RLock = RLock()
_SCHEMA_SERIALIZERS: "dict[tuple[type[Any] | None, bool, int | None], SchemaSerializer]" = {}


def _encoder_map_id(type_encoders: "Mapping[Any, Callable[[Any], Any]] | None") -> int | None:
    """Return a stable-for-the-map-lifetime key for the encoder map, or None."""
    if not type_encoders:
        return None
    return id(type_encoders)


def _make_serializer_key(
    sample: Any,
    exclude_unset: bool,
    type_encoders: "Mapping[Any, Callable[[Any], Any]] | None",
) -> "tuple[type[Any] | None, bool, int | None]":
    if sample is None or isinstance(sample, dict):
        return (None, exclude_unset, _encoder_map_id(type_encoders))
    return (type(sample), exclude_unset, _encoder_map_id(type_encoders))


def _build_dump_function(
    sample: Any,
    exclude_unset: bool,
    type_encoders: "Mapping[Any, Callable[[Any], Any]] | None",
) -> "Callable[[Any], Any]":
    """Pick the dumper for ``sample``'s type, frozen at cache time."""
    if sample is None or isinstance(sample, dict):
        return _dump_identity_dict

    encoders = dict(type_encoders) if type_encoders else None
    serializer = get_serializer(encoders) if encoders else get_serializer()
    encoder = msgspec.json.Encoder(enc_hook=serializer)

    if PYDANTIC_INSTALLED and isinstance(sample, BaseModel):
        # Pydantic's exclude_unset semantics are user-facing and worth
        # preserving — pre-filter via model_dump before JSON roundtrip so
        # the encoder sees a plain dict.
        def _dump_pydantic(value: Any) -> Any:
            return value.model_dump(exclude_unset=exclude_unset)

        return _dump_pydantic

    def _dump_native(value: Any) -> Any:
        return msgspec.json.decode(encoder.encode(value))

    return _dump_native


def get_collection_serializer(
    sample: Any,
    *,
    exclude_unset: bool = True,
    type_encoders: "Mapping[Any, Callable[[Any], Any]] | None" = None,
) -> "SchemaSerializer":
    """Return (and cache) a :class:`SchemaSerializer` for ``sample``'s type.

    The cache key is ``(type(sample), exclude_unset, id(type_encoders))`` —
    ``dict`` and ``None`` share a ``(None, ...)`` type component so pass-through
    inputs don't bloat the cache. Distinct encoder maps cache separately.

    Args:
        sample: Representative value used to pick the dispatch function.
        exclude_unset: Whether to strip ``UNSET``/unset values. msgspec's
            encoder always filters ``UNSET`` at the wire level; honored
            explicitly for pydantic models via ``model_dump(exclude_unset=...)``.
        type_encoders: Optional custom-type encoder map (``{MyType: fn}``).
            Threaded through :func:`litestar.serialization.get_serializer` so
            the dumper honors the same custom types an HTTP route would.

    Returns:
        A cached pipeline that emits JSON-friendly data for each item.
    """
    key = _make_serializer_key(sample, exclude_unset, type_encoders)
    with _SERIALIZER_LOCK:
        pipeline = _SCHEMA_SERIALIZERS.get(key)
        if pipeline is not None:
            return pipeline
        dump = _build_dump_function(sample, exclude_unset, type_encoders)
        pipeline = SchemaSerializer(key, dump)
        _SCHEMA_SERIALIZERS[key] = pipeline
        return pipeline


def serialize_collection(
    items: "Iterable[Any]",
    *,
    exclude_unset: bool = True,
    type_encoders: "Mapping[Any, Callable[[Any], Any]] | None" = None,
) -> "list[Any]":
    """Serialize an iterable of heterogeneous items.

    Primitives and ``None`` pass through unchanged. Other items dispatch via
    :func:`get_collection_serializer`; a local cache reuses pipelines across
    items within the iteration to avoid repeated global-lock acquisitions.

    Args:
        items: Heterogeneous iterable.
        exclude_unset: Forwarded to each per-type pipeline.
        type_encoders: Forwarded to each per-type pipeline.

    Returns:
        A list where each non-primitive item has been dumped.
    """
    serialized: list[Any] = []
    local_cache: dict[tuple[type[Any] | None, bool, int | None], SchemaSerializer] = {}
    for item in items:
        if isinstance(item, _PRIMITIVE_TYPES) or item is None or isinstance(item, dict):
            serialized.append(item)
            continue
        key = _make_serializer_key(item, exclude_unset, type_encoders)
        pipeline = local_cache.get(key)
        if pipeline is None:
            pipeline = get_collection_serializer(item, exclude_unset=exclude_unset, type_encoders=type_encoders)
            local_cache[key] = pipeline
        serialized.append(pipeline.dump_one(item))
    return serialized


def reset_serializer_cache() -> None:
    """Clear the global serializer cache.

    Useful in tests that mutate type metadata or long-running processes
    reloading modules at runtime.
    """
    with _SERIALIZER_LOCK:
        _SCHEMA_SERIALIZERS.clear()


def schema_dump(
    data: Any,
    *,
    exclude_unset: bool = True,
    type_encoders: "Mapping[Any, Callable[[Any], Any]] | None" = None,
) -> Any:
    """Dump ``data`` to a JSON-friendly form via the cached native-encoder pipeline.

    - Primitives and ``None`` pass through unchanged.
    - Dicts pass through unchanged.
    - Every schema model (msgspec, dataclass, pydantic, attrs, anything
      Litestar's default serializer knows) routes through
      :func:`litestar.serialization.get_serializer` + :class:`msgspec.json.Encoder`.
    - Custom types: register once via ``type_encoders={MyType: fn}`` on the
      route and pass the same map here (or let the executor do it for you on
      the tool-call hot path, which already uses ``handler.to_response``).

    Args:
        data: Value to dump.
        exclude_unset: Whether to strip unset sentinels. msgspec's encoder
            filters ``UNSET`` unconditionally at the wire level; the flag is
            honored explicitly for pydantic via ``model_dump(exclude_unset=...)``.
        type_encoders: Optional custom-type encoder map.

    Returns:
        A ``dict`` / ``list`` / primitive, whatever the native encoder emits.
    """
    if data is None or isinstance(data, dict):
        return data
    if isinstance(data, _PRIMITIVE_TYPES):
        return data
    return get_collection_serializer(
        data,
        exclude_unset=exclude_unset,
        type_encoders=type_encoders,
    ).dump_one(data)
