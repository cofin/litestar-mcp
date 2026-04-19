"""Red-phase tests for :func:`litestar_mcp.typing.schema_dump` rename fidelity.

Pins the msgspec ``rename`` contract and regression-pins the no-rename path.
Part of the executor-parity flow (closes GH #42).
"""

from dataclasses import dataclass
from typing import Any

import pytest
from litestar import Litestar, post
from litestar.testing import TestClient
from msgspec import UNSET, Struct, UnsetType

from litestar_mcp import LitestarMCP
from litestar_mcp.typing import schema_dump

pytestmark = pytest.mark.unit


class CamelStruct(Struct, rename="camel"):
    """Canonical camelCase struct used across the rename parametrizations."""

    foo_bar: int = 0
    baz_qux: str = ""


class KebabStruct(Struct, rename="kebab"):
    foo_bar: int = 0


class PascalStruct(Struct, rename="pascal"):
    foo_bar: int = 0


def _shout(name: str) -> str:
    return name.upper()


class CallableRenameStruct(Struct, rename=_shout):
    foo_bar: int = 0


class NoRenameStruct(Struct):
    foo_bar: int = 0


class CamelWithUnset(Struct, rename="camel"):
    foo_bar: int | UnsetType = UNSET
    always_set: str = "x"


class Outer(Struct, rename="camel"):
    inner_field: "InnerCamel" = None  # type: ignore[assignment]


class InnerCamel(Struct, rename="camel"):
    nested_name: str = ""


@dataclass
class DcPoint:
    x: int = 0
    y: int = 0


@pytest.mark.parametrize("exclude_unset", [True, False])
def test_schema_dump_honors_camel_rename(exclude_unset: bool) -> None:
    """CamelStruct fields must emit ``fooBar`` / ``bazQux`` keys."""
    dumped = schema_dump(CamelStruct(foo_bar=7, baz_qux="hi"), exclude_unset=exclude_unset)
    assert dumped == {"fooBar": 7, "bazQux": "hi"}


@pytest.mark.parametrize("exclude_unset", [True, False])
def test_schema_dump_honors_kebab_rename(exclude_unset: bool) -> None:
    dumped = schema_dump(KebabStruct(foo_bar=1), exclude_unset=exclude_unset)
    assert dumped == {"foo-bar": 1}


@pytest.mark.parametrize("exclude_unset", [True, False])
def test_schema_dump_honors_pascal_rename(exclude_unset: bool) -> None:
    dumped = schema_dump(PascalStruct(foo_bar=1), exclude_unset=exclude_unset)
    assert dumped == {"FooBar": 1}


@pytest.mark.parametrize("exclude_unset", [True, False])
def test_schema_dump_honors_callable_rename(exclude_unset: bool) -> None:
    dumped = schema_dump(CallableRenameStruct(foo_bar=1), exclude_unset=exclude_unset)
    assert dumped == {"FOO_BAR": 1}


@pytest.mark.parametrize("exclude_unset", [True, False])
def test_schema_dump_no_rename_keeps_snake_case(exclude_unset: bool) -> None:
    """Regression pin: Structs without ``rename=`` still emit snake_case."""
    dumped = schema_dump(NoRenameStruct(foo_bar=42), exclude_unset=exclude_unset)
    assert dumped == {"foo_bar": 42}


def test_schema_dump_exclude_unset_strips_unset_with_rename() -> None:
    """UNSET values stripped; remaining keys still camelCased."""
    dumped = schema_dump(CamelWithUnset(), exclude_unset=True)
    assert dumped == {"alwaysSet": "x"}


def test_schema_dump_include_unset_keeps_unset_with_rename() -> None:
    """``exclude_unset=False`` preserves UNSET values under the renamed key."""
    dumped = schema_dump(CamelWithUnset(), exclude_unset=False)
    assert dumped == {"fooBar": UNSET, "alwaysSet": "x"}


def test_schema_dump_nested_renamed_struct_stays_as_struct_instance() -> None:
    """Nested Structs are NOT recursed into by ``schema_dump``.

    The outer dict has camelCased keys, but the inner value remains a Struct
    instance. This pins current behavior; deep recursion is not in scope.
    """
    dumped = schema_dump(Outer(inner_field=InnerCamel(nested_name="hi")), exclude_unset=True)
    assert isinstance(dumped, dict)
    assert "innerField" in dumped
    assert isinstance(dumped["innerField"], InnerCamel)


def test_schema_dump_dataclass_unchanged() -> None:
    """Dataclass path must keep original field names."""
    assert schema_dump(DcPoint(x=1, y=2)) == {"x": 1, "y": 2}


def test_schema_dump_primitive_passthrough() -> None:
    for value in (42, "hello", 3.14, True, None):
        assert schema_dump(value) == value  # type: ignore[comparison-overlap]


def test_schema_dump_dict_passthrough() -> None:
    source = {"a": 1, "b": {"c": 2}}
    assert schema_dump(source) == source


@pytest.fixture
def camel_roundtrip_app() -> Litestar:
    """App with a tool returning ``CamelStruct`` — end-to-end path A check."""

    @post("/camel", mcp_tool="camel_tool", sync_to_thread=False)
    def camel_tool() -> CamelStruct:
        return CamelStruct(foo_bar=9, baz_qux="wire")

    return Litestar(route_handlers=[camel_tool], plugins=[LitestarMCP()])


def _rpc(client: TestClient[Any], method: str, params: "dict[str, Any] | None" = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = _ensure_session(client)
        if sid:
            headers["Mcp-Session-Id"] = sid
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


def _ensure_session(client: TestClient[Any]) -> str:
    existing = getattr(client, "_mcp_session", None)
    if existing is not None:
        return str(existing)
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    client._mcp_session = sid  # type: ignore[attr-defined]
    return str(sid)


def test_mcp_tool_emits_camel_case_for_renamed_struct(camel_roundtrip_app: Litestar) -> None:
    """End-to-end: tool returning ``Struct(rename="camel")`` emits camelCase JSON."""
    import json

    with TestClient(app=camel_roundtrip_app) as client:
        resp = _rpc(client, "tools/call", {"name": "camel_tool", "arguments": {}})

    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload == {"fooBar": 9, "bazQux": "wire"}
