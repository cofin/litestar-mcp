"""End-to-end JSON-RPC roundtrip verifying rename fidelity through the pipeline.

Red-phase: until the executor delegates to :meth:`HTTPRouteHandler.to_response`,
the tool output for a ``Struct(rename="camel")`` return value will still emit
snake_case keys (the current ``schema_dump`` path).
"""

import json
from typing import Any

import pytest
from litestar import Litestar, post
from litestar.testing import TestClient
from msgspec import Struct

from litestar_mcp import LitestarMCP
from tests.integration.conftest import rpc

pytestmark = pytest.mark.integration


class _CamelOut(Struct, rename="camel"):
    first_name: str = ""
    last_login_count: int = 0


def _build_app() -> Litestar:
    @post("/greet", mcp_tool="greet", sync_to_thread=False)
    def greet() -> _CamelOut:
        return _CamelOut(first_name="Alice", last_login_count=3)

    return Litestar(route_handlers=[greet], plugins=[LitestarMCP()])


def test_mcp_tool_emits_camel_case_for_renamed_struct() -> None:
    """``tools/call`` must emit camelCase JSON-RPC body for renamed Structs."""
    app = _build_app()
    with TestClient(app=app) as client:
        response = rpc(client, "tools/call", {"name": "greet", "arguments": {}})

    result: dict[str, Any] = response["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"firstName": "Alice", "lastLoginCount": 3}
