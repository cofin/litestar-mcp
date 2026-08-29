"""Wire JSON encoding shared by the MCP and A2A transports."""

import datetime
import enum
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import PurePath

import msgspec
import pytest

from litestar_mcp.core.serialization import DEFAULT_TYPE_ENCODERS, from_json, to_json
from litestar_mcp.utils._json import StandardLibSerializer


class Colour(enum.Enum):
    RED = "red"


@dataclass
class Point:
    x: int
    y: int


class Struct(msgspec.Struct):
    name: str


def test_to_json_normalises_common_python_types() -> None:
    payload = {
        "when": datetime.datetime(2026, 8, 29, 12, 0, tzinfo=datetime.timezone.utc),
        "day": datetime.date(2026, 8, 29),
        "id": uuid.UUID("12345678-1234-5678-1234-567812345678"),
        "amount": Decimal("1.5"),
        "path": PurePath("a/b"),
        "tags": {"x"},
        "colour": Colour.RED,
        "raw": b"bytes",
    }

    expected = {
        "when": "2026-08-29T12:00:00Z",
        "day": "2026-08-29",
        "id": "12345678-1234-5678-1234-567812345678",
        "amount": "1.5",
        "path": "a/b",
        "tags": ["x"],
        "colour": "red",
        "raw": "Ynl0ZXM=",
    }

    assert from_json(to_json(payload)) == expected
    assert from_json(StandardLibSerializer().encode(payload)) == expected


def test_to_json_encodes_dataclasses_and_structs_structurally() -> None:
    payload = {"point": Point(1, 2), "struct": Struct(name="n")}
    expected = {"point": {"x": 1, "y": 2}, "struct": {"name": "n"}}

    assert from_json(to_json(payload)) == expected
    assert from_json(StandardLibSerializer().encode(payload)) == expected


def test_to_json_rejects_unsupported_values() -> None:
    with pytest.raises(TypeError, match="unsupported JSON value"):
        to_json({"value": object()})


def test_to_json_returns_bytes_on_request_and_from_json_accepts_both() -> None:
    encoded = to_json({"a": 1}, as_bytes=True)

    assert isinstance(encoded, bytes)
    assert from_json(encoded) == {"a": 1}
    assert from_json('{"a": 1}') == {"a": 1}


def test_default_type_encoders_is_a_mapping_of_types() -> None:
    assert DEFAULT_TYPE_ENCODERS[uuid.UUID] is str
    assert datetime.datetime in DEFAULT_TYPE_ENCODERS
