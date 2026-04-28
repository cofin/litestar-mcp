"""Ch2 red-phase tests: MCP executor on Litestar's native dispatch pipeline.

These tests describe the Ch2 contract:

- Tool authors write normal Litestar handlers. Path params, query kwargs,
  ``data: StructT``, ``request: Request``, ``state: State``, ``Provide(...)``
  dependencies, and Dishka ``FromDishka[T]`` all work through the framework's
  own pipeline — no MCP-specific dependency plumbing.
- In HTTP mode the executor inherits middleware-populated state from the
  inbound ``/mcp`` request (so auth middleware and Dishka's request-container
  middleware flow through).
- In stdio mode the executor synthesizes a request and opens a child Dishka
  container when the app has one.
- Guards run in both modes against the dispatch request (Ch2 supersedes Ch1's
  stdio-skip).

Tests are expected to fail against the current executor. Phase 2 makes them
pass.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator  # noqa: TC003
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from dishka import Provider, Scope, make_async_container, provide
from dishka.integrations.litestar import FromDishka, inject, setup_dishka
from litestar import Litestar, Response, delete, get, post
from litestar.di import Provide
from litestar.exceptions import NotAuthorizedException
from litestar.status_codes import (
    HTTP_200_OK,
    HTTP_422_UNPROCESSABLE_ENTITY,
    HTTP_503_SERVICE_UNAVAILABLE,
)
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.executor import MCPToolErrorResult, execute_tool
from tests.unit.conftest import get_handler_from_app

if TYPE_CHECKING:
    from litestar import Request
    from litestar.connection import ASGIConnection
    from litestar.datastructures import State
    from litestar.handlers.base import BaseRouteHandler

pytestmark = pytest.mark.unit


# --- JSON-RPC helpers -------------------------------------------------------


def _ensure_session(client: TestClient[Any]) -> str:
    sid = getattr(client, "_mcp_session", None)
    if sid is not None:
        return sid  # type: ignore[no-any-return]
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


def _rpc(client: TestClient[Any], method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    headers: dict[str, str] = {}
    if method != "initialize":
        sid = _ensure_session(client)
        if sid:
            headers["Mcp-Session-Id"] = sid
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


def _call_tool(client: TestClient[Any], name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _rpc(client, "tools/call", {"name": name, "arguments": arguments})


# --- Shared test types ------------------------------------------------------


class NoteInput(msgspec.Struct):
    title: str
    body: str


class UpdateInput(msgspec.Struct):
    body: str


# --- Data / signature-model path -------------------------------------------


def test_data_param_parses_via_signature_model() -> None:
    """Handler declaring ``data: StructT`` receives a parsed msgspec struct."""

    @post("/notes", opt={"mcp_tool": "create_note"}, sync_to_thread=False)
    def create_note(data: NoteInput) -> dict[str, str]:
        assert isinstance(data, NoteInput)
        return {"title": data.title, "body": data.body}

    app = Litestar(route_handlers=[create_note], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(client, "create_note", {"title": "T", "body": "B"})

    assert resp["result"]["isError"] is False
    payload = resp["result"]["content"][0]
    assert '"title":"T"' in payload["text"]


def test_path_param_routes_through_scope() -> None:
    """Handler with a path-param kwarg receives it from ``tool_args``.

    `@delete("/notes/{note_id:str}")` + `arguments={"note_id": "abc"}` lands
    with ``note_id="abc"`` via normal path-param parsing.
    """

    @delete(
        "/notes/{note_id:str}",
        status_code=HTTP_200_OK,
        opt={"mcp_tool": "delete_note"},
        sync_to_thread=False,
    )
    def delete_note(note_id: str) -> dict[str, str]:
        return {"id": note_id}

    app = Litestar(route_handlers=[delete_note], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(client, "delete_note", {"note_id": "abc"})

    assert resp["result"]["isError"] is False
    assert '"id":"abc"' in resp["result"]["content"][0]["text"]


def test_query_param_routes_through_scope() -> None:
    """Plain scalar kwargs (non-path, non-data) survive native dispatch.

    Today's executor treats them as query params and raises
    ``Missing required query parameter``. After Ch2 they get routed via
    a synthesized query_string in the dispatch scope.
    """

    @get("/search", opt={"mcp_tool": "search_notes"}, sync_to_thread=False)
    def search(q: str, limit: int = 10) -> dict[str, object]:
        return {"q": q, "limit": limit}

    app = Litestar(route_handlers=[search], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(client, "search_notes", {"q": "hello", "limit": 5})

    assert resp["result"]["isError"] is False
    text = resp["result"]["content"][0]["text"]
    assert '"q":"hello"' in text
    assert '"limit":5' in text


def test_mixed_path_data_and_query_params() -> None:
    """Path param + ``data`` struct + scalar kwarg all populate from one tool_args."""

    @post(
        "/notes/{note_id:str}",
        opt={"mcp_tool": "update_note"},
        sync_to_thread=False,
    )
    def update_note(note_id: str, data: UpdateInput, notify: bool = False) -> dict[str, object]:
        return {"id": note_id, "body": data.body, "notify": notify}

    app = Litestar(route_handlers=[update_note], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(
            client,
            "update_note",
            {"note_id": "abc", "body": "updated", "notify": True},
        )

    assert resp["result"]["isError"] is False
    text = resp["result"]["content"][0]["text"]
    assert '"id":"abc"' in text
    assert '"body":"updated"' in text
    assert '"notify":true' in text


def test_dto_validation_error_surfaces_cleanly() -> None:
    """Malformed payload → MCP tool-error carrying the framework validation message."""

    @post("/notes", opt={"mcp_tool": "create_note"}, sync_to_thread=False)
    def create_note(data: NoteInput) -> dict[str, str]:
        return {"title": data.title}

    app = Litestar(route_handlers=[create_note], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(client, "create_note", {"title": "no body key"})

    assert resp["result"]["isError"] is True


# --- Live-request passthrough -----------------------------------------------


def test_request_param_receives_dispatch_request() -> None:
    """Handler taking ``request: Request`` sees a Request shaped for its route."""

    @get("/ping", opt={"mcp_tool": "ping"}, sync_to_thread=False)
    def ping(request: Request[Any, Any, Any]) -> dict[str, str]:
        return {"method": request.method, "path": request.url.path}

    app = Litestar(route_handlers=[ping], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(client, "ping", {})

    assert resp["result"]["isError"] is False
    text = resp["result"]["content"][0]["text"]
    assert '"method":"GET"' in text
    assert '"path":"/ping"' in text


def test_inbound_request_state_passes_through() -> None:
    """Middleware-populated state on the inbound /mcp request flows into the handler.

    Seed ``app.state`` with a sentinel; handler reads it via ``state: State``.
    Under Ch2, any state a middleware writes to ``request.state`` (Dishka's
    container, auth's user, etc.) also flows through — we pin the app-state
    path here because it's the simplest case to set up without a middleware.
    """

    @get("/state", opt={"mcp_tool": "read_state"}, sync_to_thread=False)
    def read_state(state: State) -> dict[str, object]:
        return {"seeded": state.seeded}

    app = Litestar(route_handlers=[read_state], plugins=[LitestarMCP()])
    app.state.seeded = "sentinel-123"

    with TestClient(app=app) as client:
        resp = _call_tool(client, "read_state", {})

    assert resp["result"]["isError"] is False
    assert '"seeded":"sentinel-123"' in resp["result"]["content"][0]["text"]


def test_request_scope_dependency_resolves_natively() -> None:
    """A ``Provide(...)`` that itself depends on ``request: Request`` works."""

    async def make_greeting(request: Request[Any, Any, Any]) -> str:
        return f"hello from {request.url.path}"

    @get(
        "/greet",
        opt={"mcp_tool": "greet"},
        dependencies={"greeting": Provide(make_greeting)},
        sync_to_thread=False,
    )
    def greet(greeting: str) -> dict[str, str]:
        return {"greeting": greeting}

    app = Litestar(route_handlers=[greet], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(client, "greet", {})

    assert resp["result"]["isError"] is False
    assert '"greeting":"hello from /greet"' in resp["result"]["content"][0]["text"]


# --- Dishka integration -----------------------------------------------------


class _Service:
    def __init__(self, token: str) -> None:
        self.token = token


class _ServiceProvider(Provider):
    scope = Scope.REQUEST

    @provide
    def service(self) -> _Service:
        return _Service(token=str(uuid.uuid4()))


def _build_dishka_app() -> Litestar:
    app = Litestar(route_handlers=[_get_service], plugins=[LitestarMCP()])
    container = make_async_container(_ServiceProvider())
    setup_dishka(container=container, app=app)
    return app


@get("/svc", opt={"mcp_tool": "get_service"})
@inject
async def _get_service(service: FromDishka[_Service]) -> dict[str, str]:
    return {"token": service.token}


class _Resource:
    pass


_CLEANUP_LOG: list[bool] = []


class _InstrumentedProvider(Provider):
    scope = Scope.REQUEST

    @provide
    async def resource(self) -> AsyncIterator[_Resource]:
        try:
            yield _Resource()
        finally:
            _CLEANUP_LOG.append(True)


@get("/res", opt={"mcp_tool": "use_res"})
@inject
async def _use_resource(res: FromDishka[_Resource]) -> dict[str, str]:
    return {"ok": "yes"}


def test_fromdishka_resolves_without_mcp_hook() -> None:
    """HTTP mode: ``@inject`` + ``FromDishka[T]`` resolves with no MCP plumbing."""
    app = _build_dishka_app()

    with TestClient(app=app) as client:
        r1 = _call_tool(client, "get_service", {})
        r2 = _call_tool(client, "get_service", {})

    assert r1["result"]["isError"] is False
    assert r2["result"]["isError"] is False
    t1 = r1["result"]["content"][0]["text"]
    t2 = r2["result"]["content"][0]["text"]
    # Different request-scoped containers → different uuid tokens.
    assert t1 != t2


# --- Stdio-mode contract ----------------------------------------------------


def test_stdio_mode_synthesizes_request() -> None:
    """Direct ``execute_tool(..., request=None)`` builds a Request and hands it to the handler."""
    seen: dict[str, Any] = {}

    @get("/probe", opt={"mcp_tool": "probe"}, sync_to_thread=False)
    def probe(request: Request[Any, Any, Any]) -> dict[str, str]:
        seen["method"] = request.method
        seen["path"] = request.url.path
        return {"ok": "yes"}

    app = Litestar(route_handlers=[probe], plugins=[LitestarMCP()])
    handler = get_handler_from_app(app, "/probe")

    result = asyncio.run(execute_tool(handler, app, {}, request=None))

    assert result == {"ok": "yes"}
    assert seen["method"] == "GET"
    assert seen["path"] == "/probe"


def test_stdio_mode_opens_dishka_child_container() -> None:
    """Stdio invocation of a Dishka-injected handler resolves ``FromDishka[T]``."""
    app = _build_dishka_app()
    handler = get_handler_from_app(app, "/svc")

    r1 = asyncio.run(execute_tool(handler, app, {}, request=None))
    r2 = asyncio.run(execute_tool(handler, app, {}, request=None))

    # Each stdio call opens a fresh child container → different uuid tokens.
    assert r1["token"] != r2["token"]


def test_stdio_mode_cleans_up_dishka_child_container() -> None:
    """Child container closes after the call — verified via an instrumented provider."""
    _CLEANUP_LOG.clear()

    app = Litestar(route_handlers=[_use_resource], plugins=[LitestarMCP()])
    container = make_async_container(_InstrumentedProvider())
    setup_dishka(container=container, app=app)
    handler = get_handler_from_app(app, "/res")

    asyncio.run(execute_tool(handler, app, {}, request=None))
    assert _CLEANUP_LOG == [True]


def test_guards_run_in_stdio_mode() -> None:
    """Ch2 supersedes Ch1's stdio-skip: guards always run against the dispatch request."""
    denied_msg = "stdio should enforce"

    def deny(_c: ASGIConnection[Any, Any, Any, Any], _h: BaseRouteHandler) -> None:
        raise NotAuthorizedException(denied_msg)

    @get("/x", guards=[deny], opt={"mcp_tool": "x"}, sync_to_thread=False)
    def tool() -> dict[str, str]:
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    handler = get_handler_from_app(app, "/x")

    with pytest.raises(NotAuthorizedException, match=denied_msg):
        asyncio.run(execute_tool(handler, app, {}, request=None))


# --- MCPToolErrorResult.status_code capture ---------------------------------


def test_execute_tool_captures_handler_4xx_status_code() -> None:
    """Regression sentinel for the executor refactor in PR #46.

    ``MCPToolErrorResult.status_code`` is the load-bearing field that the
    prompt path (``routes.py:701``) inspects to choose between
    ``INVALID_PARAMS`` and ``INTERNAL_ERROR``. Tools collapse 4xx/5xx to
    ``isError=True`` so they don't exercise the field directly — without
    this test, only the prompt path would catch a regression in
    ``_capture_asgi_response`` status capture.
    """

    @get("/bad", opt={"mcp_tool": "bad_status"}, sync_to_thread=False)
    def bad() -> Response[dict[str, str]]:
        return Response(content={"error": "nope"}, status_code=HTTP_422_UNPROCESSABLE_ENTITY)

    app = Litestar(route_handlers=[bad], plugins=[LitestarMCP()])
    handler = get_handler_from_app(app, "/bad")

    with pytest.raises(MCPToolErrorResult) as excinfo:
        asyncio.run(execute_tool(handler, app, {}, request=None))

    assert excinfo.value.status_code == HTTP_422_UNPROCESSABLE_ENTITY
    assert excinfo.value.is_client_error is True


def test_execute_tool_captures_handler_5xx_status_code() -> None:
    @get("/down", opt={"mcp_tool": "down_status"}, sync_to_thread=False)
    def down() -> Response[dict[str, str]]:
        return Response(content={"error": "down"}, status_code=HTTP_503_SERVICE_UNAVAILABLE)

    app = Litestar(route_handlers=[down], plugins=[LitestarMCP()])
    handler = get_handler_from_app(app, "/down")

    with pytest.raises(MCPToolErrorResult) as excinfo:
        asyncio.run(execute_tool(handler, app, {}, request=None))

    assert excinfo.value.status_code == HTTP_503_SERVICE_UNAVAILABLE
    assert excinfo.value.is_client_error is False
