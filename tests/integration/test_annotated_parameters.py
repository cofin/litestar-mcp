"""Integration coverage for Annotated[T, Parameter(...)] query params (#52)."""

from datetime import datetime  # noqa: TC003
from typing import Annotated, Any

import pytest
from litestar import Litestar, get
from litestar.params import Parameter
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from tests.integration.conftest import parse_tool_payload, rpc


@get("/annotated", mcp_tool="annotated_list")
async def annotated_list(
    is_paid: "Annotated[bool | None, Parameter(query='isPaid', description='Whether the order is paid')]" = None,
    prepared_after: "Annotated[datetime | None, Parameter(query='preparedAfter', description='Filter: prepared_at >= this')]" = None,
) -> "dict[str, Any]":
    return {
        "is_paid": is_paid,
        "prepared_after": prepared_after.isoformat() if prepared_after else None,
    }


@pytest.fixture
def annotated_app() -> "Litestar":
    return Litestar(route_handlers=[annotated_list], plugins=[LitestarMCP()])


def test_annotated_query_params_yield_typed_schema(annotated_app: "Litestar") -> "None":
    with TestClient(app=annotated_app) as client:
        tools = rpc(client, "tools/list")["result"]["tools"]
        tool = next(t for t in tools if t["name"] == "annotated_list")
        props = tool["inputSchema"]["properties"]

        assert set(props) == {"isPaid", "preparedAfter"}

        is_paid = props["isPaid"]
        assert "anyOf" in is_paid
        boolean_member = next(m for m in is_paid["anyOf"] if m.get("type") == "boolean")
        null_member = next(m for m in is_paid["anyOf"] if m.get("type") == "null")
        assert boolean_member == {"type": "boolean"}
        assert null_member == {"type": "null"}
        assert is_paid["description"] == "Whether the order is paid"
        assert "Runtime representation of an annotated type" not in is_paid.get("description", "")


def test_annotated_tool_call_dispatches_with_wire_keys(annotated_app: "Litestar") -> "None":
    with TestClient(app=annotated_app) as client:
        rpc_result = rpc(
            client,
            "tools/call",
            {
                "name": "annotated_list",
                "arguments": {"isPaid": True, "preparedAfter": "2026-01-01T00:00:00"},
            },
        )
        payload = parse_tool_payload(rpc_result)
        assert payload == {"is_paid": True, "prepared_after": "2026-01-01T00:00:00"}
