"""Coverage for ``_validate_tool_arguments`` — the msgspec/signature_model validator.

These tests exercise the tool-argument validator through the full JSON-RPC
surface so we catch regressions in the error envelope shape (JSON Pointer
paths under ``errors[]``) and in which parameters are treated as user-supplied
arguments vs. DI-injected context.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import msgspec
import pytest
from litestar import Litestar, get
from litestar.di import Provide
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig

pytestmark = pytest.mark.unit


def _initialize(client: TestClient[Any]) -> str:
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "t"},
            },
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    return str(sid)


def _call(client: TestClient[Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    sid = _initialize(client)
    resp = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers={"Mcp-Session-Id": sid},
    )
    return resp.json()  # type: ignore[no-any-return]


def _error_payload(result: dict[str, Any]) -> dict[str, Any]:
    """Extract the parsed JSON error body from an isError tool result."""
    assert "result" in result
    assert result["result"]["isError"] is True
    text = result["result"]["content"][0]["text"]
    return json.loads(text)  # type: ignore[no-any-return]


class Point(msgspec.Struct):
    """Nested payload used for JSON Pointer path assertions."""

    x: int
    y: int


class TestInputValidation:
    def test_valid_args_pass_through(self) -> None:
        @get("/greet", opt={"mcp_tool": "greet"}, sync_to_thread=False)
        def greet(name: str) -> dict[str, str]:
            return {"hello": name}

        app = Litestar(route_handlers=[greet], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _call(client, "greet", {"name": "Alice"})
            assert "result" in result
            assert result["result"]["isError"] is False
            assert json.loads(result["result"]["content"][0]["text"]) == {"hello": "Alice"}

    def test_missing_required_yields_invalid_params(self) -> None:
        @get("/greet", opt={"mcp_tool": "greet"}, sync_to_thread=False)
        def greet(name: str) -> dict[str, str]:
            return {"hello": name}

        app = Litestar(route_handlers=[greet], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _call(client, "greet", {})
            payload = _error_payload(result)
            assert payload["error"] == "Invalid tool arguments"
            paths = {e["path"] for e in payload["errors"]}
            assert "/arguments/name" in paths

    def test_wrong_type_yields_invalid_params_with_path(self) -> None:
        @get("/add", opt={"mcp_tool": "add"}, sync_to_thread=False)
        def add(count: int) -> dict[str, int]:
            return {"count": count}

        app = Litestar(route_handlers=[add], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _call(client, "add", {"count": "not-an-int"})
            payload = _error_payload(result)
            assert any(e["path"] == "/arguments/count" for e in payload["errors"])

    def test_unexpected_argument_yields_invalid_params(self) -> None:
        @get("/greet", opt={"mcp_tool": "greet"}, sync_to_thread=False)
        def greet(name: str) -> dict[str, str]:
            return {"hello": name}

        app = Litestar(route_handlers=[greet], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _call(client, "greet", {"name": "Alice", "unknown": 1})
            payload = _error_payload(result)
            unexpected = [e for e in payload["errors"] if e["path"] == "/arguments"]
            assert unexpected, payload
            assert "unknown" in unexpected[0]["message"]

    def test_handler_with_no_typed_args_skips_validation(self) -> None:
        @get("/ping", opt={"mcp_tool": "ping_tool"}, sync_to_thread=False)
        def ping_tool() -> dict[str, bool]:
            return {"ok": True}

        app = Litestar(route_handlers=[ping_tool], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            # An extra argument on a zero-arg handler is the only path to a
            # validation error for this case; no typed args means nothing to
            # type-check against.
            result = _call(client, "ping_tool", {})
            assert "result" in result
            assert result["result"]["isError"] is False

    def test_msgspec_meta_constraint_enforced(self) -> None:
        @get("/bounded", opt={"mcp_tool": "bounded"}, sync_to_thread=False)
        def bounded(age: Annotated[int, msgspec.Meta(ge=0, le=120)]) -> dict[str, int]:
            return {"age": age}

        app = Litestar(route_handlers=[bounded], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _call(client, "bounded", {"age": 999})
            payload = _error_payload(result)
            assert any(e["path"] == "/arguments/age" for e in payload["errors"])

    def test_nested_struct_path_is_json_pointer(self) -> None:
        @get("/place", opt={"mcp_tool": "place"}, sync_to_thread=False)
        def place(point: Point) -> dict[str, Any]:
            return {"x": point.x, "y": point.y}

        app = Litestar(route_handlers=[place], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _call(client, "place", {"point": {"x": "bad", "y": 2}})
            payload = _error_payload(result)
            paths = {e["path"] for e in payload["errors"]}
            assert "/arguments/point/x" in paths, payload

    def test_di_param_not_treated_as_user_arg(self) -> None:
        async def provide_secret() -> str:
            return "s3cr3t"

        @get(
            "/di",
            opt={"mcp_tool": "di_tool"},
            dependencies={"secret": Provide(provide_secret)},
            sync_to_thread=False,
        )
        def di_tool(name: str, secret: str) -> dict[str, str]:
            return {"name": name, "secret": secret}

        app = Litestar(route_handlers=[di_tool], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            # Caller only supplies ``name``; ``secret`` is DI-injected and
            # must not be reported as "missing required".
            result = _call(client, "di_tool", {"name": "Alice"})
            assert "result" in result
            assert result["result"]["isError"] is False

    def test_optional_param_with_default_is_not_required(self) -> None:
        @get("/opt", opt={"mcp_tool": "opt_tool"}, sync_to_thread=False)
        def opt_tool(name: str = "world") -> dict[str, str]:
            return {"hello": name}

        app = Litestar(route_handlers=[opt_tool], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _call(client, "opt_tool", {})
            assert "result" in result
            assert result["result"]["isError"] is False
            assert json.loads(result["result"]["content"][0]["text"]) == {"hello": "world"}
