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
from litestar import Litestar, get, post
from litestar.di import Provide
from litestar.params import FromQuery  # noqa: TC002 - Litestar resolves handler markers at runtime.
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


class Payload(msgspec.Struct):
    """Payload used for data-wrapper validation assertions."""

    title: str


class _DishkaDriver:
    pass


class _DishkaTaskService:
    pass


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

    def test_empty_data_wrapper_validates_required_struct_fields(self) -> None:
        @post("/payload", opt={"mcp_tool": "create_payload"}, sync_to_thread=False)
        def create_payload(data: Payload) -> dict[str, str]:
            return {"title": data.title}

        app = Litestar(route_handlers=[create_payload], plugins=[LitestarMCP(MCPConfig())])
        with TestClient(app=app) as client:
            result = _call(client, "create_payload", {"data": {}})
            payload = _error_payload(result)
            paths = {e["path"] for e in payload["errors"]}
            assert "/arguments/data" in paths, payload

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

    def test_provider_only_param_does_not_crash_validator(self) -> None:
        """Provider-declared params don't appear in ``parsed_fn_signature``.

        Before the fix, ``declared_by_name[name]`` raised ``KeyError`` for any
        provider param that reached ``_validate_tool_arguments``. The fallback
        resolves the provider's annotations via ``typing.get_type_hints`` at
        walk time, so PEP 563 stringified annotations (this module uses
        ``from __future__ import annotations``) resolve to real types --
        ``msgspec.convert`` would otherwise see ``'int'`` strings and bail.
        """
        from litestar.params import Dependency

        from litestar_mcp.services.handler import _validate_tool_arguments

        async def provide_pagination(limit: int = 20, offset: int = 0) -> dict[str, int]:
            return {"limit": limit, "offset": offset}

        @get(
            "/pp",
            opt={"mcp_tool": "pp_tool"},
            dependencies={"pagination": Provide(provide_pagination)},
            sync_to_thread=False,
        )
        def pp_tool(
            pagination: Annotated[dict[str, int], Dependency(skip_validation=True)],
        ) -> dict[str, int]:
            return pagination

        app = Litestar(route_handlers=[pp_tool], plugins=[LitestarMCP(MCPConfig())])

        handler = next(
            rh
            for route in app.routes
            for rh in getattr(route, "route_handlers", [])
            if getattr(rh, "fn", None) is pp_tool.fn
        )

        # Happy path: previously raised KeyError on ``declared_by_name[name]``.
        assert _validate_tool_arguments(handler, {"limit": 7, "offset": 3}) == []

        # Type coercion uses the provider's annotation as the fallback.
        errs = _validate_tool_arguments(handler, {"limit": "not-an-int"})
        paths = {e["path"] for e in errs}
        assert "/arguments/limit" in paths, errs

        # End-to-end smoke: the JSON-RPC dispatcher accepts the provider params
        # without 4xx-ing at the HTTP layer.
        with TestClient(app=app) as client:
            ok = _call(client, "pp_tool", {"limit": 7, "offset": 3})
            assert "result" in ok
            assert ok["result"]["isError"] is False
            assert json.loads(ok["result"]["content"][0]["text"]) == {"limit": 7, "offset": 3}

    def test_dishka_resolved_provider_param_is_not_required(self) -> None:
        from dishka import Provider, Scope, make_async_container, provide
        from dishka.integrations.litestar import LitestarProvider, setup_dishka

        class DishkaProvider(Provider):
            scope = Scope.REQUEST

            @provide
            def driver(self) -> _DishkaDriver:
                return _DishkaDriver()

        async def provide_task_service(driver: _DishkaDriver) -> _DishkaTaskService:
            return _DishkaTaskService()

        @get("/hello", opt={"mcp_tool": "hello"}, sync_to_thread=False)
        def hello(name: FromQuery[str]) -> dict[str, str]:
            return {"hello": name}

        app = Litestar(
            route_handlers=[hello],
            dependencies={"task_service": Provide(provide_task_service)},
            plugins=[LitestarMCP(MCPConfig())],
        )
        container = make_async_container(LitestarProvider(), DishkaProvider())
        setup_dishka(container=container, app=app)

        with TestClient(app=app) as client:
            result = _call(client, "hello", {"name": "Ada"})

        assert "result" in result
        assert result["result"]["isError"] is False
        assert json.loads(result["result"]["content"][0]["text"]) == {"hello": "Ada"}
