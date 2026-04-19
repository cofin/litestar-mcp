"""Red-phase tests for :mod:`litestar_mcp._serializer` cache semantics.

Every test imports from ``litestar_mcp._serializer``, which does not exist
yet — Phase 2 creates it. All tests are expected to fail at collection time
with ``ImportError`` until the module lands.
"""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, cast

import pytest
from msgspec import Struct

from litestar_mcp._serializer import (
    SchemaSerializer,
    get_collection_serializer,
    reset_serializer_cache,
    schema_dump,
    serialize_collection,
)

pytestmark = pytest.mark.unit


class _CamelItem(Struct, rename="camel"):
    field_a: int = 0
    field_b: str = ""


class _SnakeItem(Struct):
    field_a: int = 0


@dataclass
class _DcItem:
    x: int = 0
    y: int = 0


@pytest.fixture(autouse=True)
def _clear_cache_between_tests() -> "Any":
    reset_serializer_cache()
    yield
    reset_serializer_cache()


def test_get_collection_serializer_returns_schema_serializer_instance() -> None:
    item = _CamelItem(field_a=1, field_b="x")
    pipeline = get_collection_serializer(item)
    assert isinstance(pipeline, SchemaSerializer)


def test_serializer_cache_hit_on_same_type() -> None:
    """Two ``get_collection_serializer`` calls on the same type return the same instance."""
    first = get_collection_serializer(_CamelItem())
    second = get_collection_serializer(_CamelItem(field_a=99, field_b="y"))
    assert first is second


def test_serializer_cache_key_distinguishes_exclude_unset() -> None:
    """``exclude_unset=True`` and ``False`` produce different cached serializers for the same type."""
    with_exclude = get_collection_serializer(_CamelItem(), exclude_unset=True)
    without_exclude = get_collection_serializer(_CamelItem(), exclude_unset=False)
    assert with_exclude is not without_exclude


def test_serializer_cache_distinguishes_types() -> None:
    camel_pipeline = get_collection_serializer(_CamelItem())
    snake_pipeline = get_collection_serializer(_SnakeItem())
    assert camel_pipeline is not snake_pipeline


def test_reset_serializer_cache_clears_entries() -> None:
    first = get_collection_serializer(_CamelItem())
    reset_serializer_cache()
    second = get_collection_serializer(_CamelItem())
    assert first is not second


def test_serializer_cache_thread_safety() -> None:
    """Stress test: concurrent dumps across mixed types must not error or corrupt output."""

    def _dump_one(index: int) -> dict[str, Any]:
        if index % 3 == 0:
            return cast("dict[str, Any]", schema_dump(_CamelItem(field_a=index, field_b="x")))
        if index % 3 == 1:
            return cast("dict[str, Any]", schema_dump(_DcItem(x=index, y=index + 1)))
        return cast("dict[str, Any]", schema_dump(_SnakeItem(field_a=index)))

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(_dump_one, range(1000)))

    assert len(results) == 1000
    for index, result in enumerate(results):
        if index % 3 == 0:
            assert result == {"fieldA": index, "fieldB": "x"}
        elif index % 3 == 1:
            assert result == {"x": index, "y": index + 1}
        else:
            assert result == {"field_a": index}


def test_serialize_collection_mixed_types_preserves_per_item_rendering() -> None:
    """Iterable with dict, Struct, dataclass, primitive — each handled correctly."""
    items = [
        {"raw": "dict"},
        _CamelItem(field_a=2, field_b="camel"),
        _DcItem(x=3, y=4),
        7,
        "string",
        None,
    ]
    serialized = serialize_collection(items)
    assert serialized == [
        {"raw": "dict"},
        {"fieldA": 2, "fieldB": "camel"},
        {"x": 3, "y": 4},
        7,
        "string",
        None,
    ]


def test_serialize_collection_reuses_cache_within_iteration() -> None:
    """Local cache inside ``serialize_collection`` should reuse pipelines across items."""
    items = [_CamelItem(field_a=i, field_b="x") for i in range(5)]
    serialized = serialize_collection(items)
    assert len(serialized) == 5
    # Same type across the batch must share one cache entry.
    assert get_collection_serializer(items[0]) is get_collection_serializer(items[-1])


def test_dump_many_respects_rename() -> None:
    pipeline = get_collection_serializer(_CamelItem())
    dumped = pipeline.dump_many([_CamelItem(field_a=1), _CamelItem(field_a=2)])
    assert dumped == [
        {"fieldA": 1, "fieldB": ""},
        {"fieldA": 2, "fieldB": ""},
    ]


def test_schema_serializer_key_property_reports_cache_key() -> None:
    pipeline = get_collection_serializer(_CamelItem(), exclude_unset=True)
    assert pipeline.key == (_CamelItem, True)


def test_schema_dump_for_attrs_instance_falls_through_pipeline() -> None:
    """attrs path must round-trip through ``_dump_attrs`` (not ``__dict__`` fallback)."""
    try:
        import attrs
    except ImportError:  # pragma: no cover - attrs is a dev dep
        pytest.skip("attrs not installed")

    @attrs.define
    class AttrsPoint:
        x: int = 0
        y: int = 0

    dumped = schema_dump(AttrsPoint(x=3, y=4))
    assert dumped == {"x": 3, "y": 4}


def test_schema_dump_plain_object_uses_dict_fallback() -> None:
    """Objects without dataclass/msgspec/attrs identity fall back to ``__dict__``."""

    class Plain:
        def __init__(self) -> None:
            self.alpha = 1
            self.beta = "two"

    dumped = schema_dump(Plain())
    assert dumped == {"alpha": 1, "beta": "two"}


def test_serialize_collection_routes_bare_dict_through_primitive_shortcut() -> None:
    """Dicts bypass ``get_collection_serializer`` and return as-is."""
    original = {"a": 1}
    serialized = serialize_collection([original])
    assert serialized == [original]
    # The returned dict is the identical object (no copy).
    assert serialized[0] is original


def test_get_collection_serializer_for_dict_produces_identity_pipeline() -> None:
    """Dispatching on a dict sample caches the identity dumper."""
    pipeline = get_collection_serializer({"a": 1})
    assert pipeline.key == (None, True)
    assert pipeline.dump_one({"z": 9}) == {"z": 9}
