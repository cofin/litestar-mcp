"""Red-phase tests for schema_dump honoring route/app-level type_encoders.

Chapter B goal: :func:`litestar_mcp.utils.serialization.schema_dump` must
delegate to :func:`litestar.serialization.get_serializer` so a custom type
the user registers via ``type_encoders={MyType: fn}`` on a route serializes
the same way through MCP ``tools/call`` as it does through HTTP. Before the
rewrite, the bespoke per-library dumpers ignored ``type_encoders`` — only
the hot path via ``handler.to_response`` honored them.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
from msgspec import Struct

from litestar_mcp.utils.serialization import (
    get_collection_serializer,
    reset_serializer_cache,
    schema_dump,
)

pytestmark = pytest.mark.unit


class _WorkspaceId:
    """Toy custom type with no default encoder."""

    def __init__(self, value: str) -> None:
        self.value = value


class _CamelOut(Struct, rename="camel"):
    workspace_id: _WorkspaceId
    created_by: str = ""


@pytest.fixture(autouse=True)
def _clear_cache() -> "Any":
    reset_serializer_cache()
    yield
    reset_serializer_cache()


# ---------------------------------------------------------------------------
# type_encoders kwarg — round-trips through litestar.serialization.get_serializer
# ---------------------------------------------------------------------------


def test_schema_dump_honors_route_type_encoders() -> None:
    """Custom type registered via ``type_encoders`` on the route serializes correctly."""
    encoders = {_WorkspaceId: lambda w: w.value}
    value = _CamelOut(workspace_id=_WorkspaceId("6bc9e12e"), created_by="alice")

    dumped = schema_dump(value, type_encoders=encoders)

    assert dumped == {"workspaceId": "6bc9e12e", "createdBy": "alice"}


def test_schema_dump_without_type_encoders_raises_for_unknown_custom_type() -> None:
    """No encoder declared for the custom type → msgspec raises during encode.

    Matches HTTP behavior: if the user doesn't register the encoder, they
    get a clear failure telling them which type is unsupported.
    """
    value = _CamelOut(workspace_id=_WorkspaceId("xyz"))
    with pytest.raises(TypeError, match="_WorkspaceId"):
        schema_dump(value)


@dataclass
class _DcEvent:
    name: str = "launch"
    when: datetime = datetime(2026, 4, 19, 12, 0, 0, tzinfo=timezone.utc)


def test_schema_dump_handles_datetime_natively_no_encoder_needed() -> None:
    """Litestar's default serializer already handles datetime — user adds no encoder."""
    dumped = schema_dump(_DcEvent())

    assert isinstance(dumped, dict)
    assert dumped["when"].startswith("2026-04-19")
    assert dumped["name"] == "launch"


def test_schema_dump_handles_decimal_natively_no_encoder_needed() -> None:
    """Decimal passes through Litestar's default serializer as a string."""

    @dataclass
    class _Money:
        amount: Decimal = Decimal("19.99")

    dumped = schema_dump(_Money())

    # Litestar's default serializer encodes Decimal as a string (finance-safe).
    assert dumped == {"amount": "19.99"}


def test_schema_dump_handles_uuid_natively_no_encoder_needed() -> None:
    @dataclass
    class _Entity:
        id: UUID

    dumped = schema_dump(_Entity(id=UUID("6bc9e12e-0000-0000-0000-000000000000")))

    assert dumped == {"id": "6bc9e12e-0000-0000-0000-000000000000"}


# ---------------------------------------------------------------------------
# Cache keying
# ---------------------------------------------------------------------------


def test_cache_distinguishes_distinct_type_encoder_maps() -> None:
    """Three schema_dump calls with three distinct encoder maps cache separately."""
    encoders_a = {_WorkspaceId: lambda w: w.value}
    encoders_b = {_WorkspaceId: lambda w: {"wid": w.value}}
    encoders_c = None

    pipeline_a = get_collection_serializer(_CamelOut(workspace_id=_WorkspaceId("x")), type_encoders=encoders_a)
    pipeline_b = get_collection_serializer(_CamelOut(workspace_id=_WorkspaceId("x")), type_encoders=encoders_b)
    pipeline_c = get_collection_serializer(_CamelOut(workspace_id=_WorkspaceId("x")), type_encoders=encoders_c)

    assert pipeline_a is not pipeline_b
    assert pipeline_b is not pipeline_c
    assert pipeline_a is not pipeline_c


def test_cache_reuses_entry_for_equivalent_encoder_map() -> None:
    """Same ``{type: fn}`` map across two calls → one cache entry."""
    shared_encoders = {_WorkspaceId: lambda w: w.value}

    first = get_collection_serializer(_CamelOut(workspace_id=_WorkspaceId("a")), type_encoders=shared_encoders)
    second = get_collection_serializer(_CamelOut(workspace_id=_WorkspaceId("b")), type_encoders=shared_encoders)

    assert first is second


# ---------------------------------------------------------------------------
# Rename fidelity pinned end-to-end through the new native-encoder path
# ---------------------------------------------------------------------------


def test_camel_rename_still_honored_via_native_encoder() -> None:
    """Regression pin: after swapping dumpers to get_serializer, Struct(rename='camel') still emits camelCase."""
    encoders = {_WorkspaceId: lambda w: w.value}
    dumped = schema_dump(_CamelOut(workspace_id=_WorkspaceId("z"), created_by="bob"), type_encoders=encoders)

    assert "workspaceId" in dumped
    assert "createdBy" in dumped
    assert "workspace_id" not in dumped
