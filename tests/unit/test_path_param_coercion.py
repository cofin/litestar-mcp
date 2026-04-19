"""Red-phase tests for path-parameter type coercion in the MCP executor (GH #43).

Route patterns like ``{workspace_id:uuid}`` declare a typed path parameter that
Litestar's own HTTP pipeline coerces via
:func:`litestar._asgi.routing_trie.traversal.parse_path_params`. The MCP
executor bypasses that step, so guards and handlers reading
``connection.path_params[name]`` over MCP see the raw ``str`` the JSON-RPC
client sent — while the same guard over HTTP sees a typed value. Comparisons
like ``UUID == str`` silently fail, producing wrong ``PermissionDenied`` /
``404`` responses.

These tests pin the fix: the dispatch scope must carry the coerced value for
every built-in parser (``uuid``, ``int``, ``float``, ``datetime`` …), leave
untyped ``str`` params alone, and surface structured ``INVALID_PARAMS`` errors
for unparseable values.
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

import pytest
from litestar import Litestar, get
from litestar.exceptions import PermissionDeniedException
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection
    from litestar.handlers.base import BaseRouteHandler

pytestmark = pytest.mark.unit


def _ensure_session(client: TestClient[Any]) -> str:
    sid = getattr(client, "_mcp_session", None)
    if sid is not None:
        return str(sid)
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid_val = init.headers.get("mcp-session-id", "")
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid_val},
    )
    client._mcp_session = sid_val  # type: ignore[attr-defined]
    return str(sid_val)


def _call_tool(client: TestClient[Any], name: str, args: "dict[str, Any] | None" = None) -> dict[str, Any]:
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args or {}}}
    sid = _ensure_session(client)
    headers = {"Mcp-Session-Id": sid} if sid else {}
    return client.post("/mcp", json=body, headers=headers).json()  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Typed path-param coercion
# ---------------------------------------------------------------------------


def test_uuid_path_param_coerced_from_tool_args() -> None:
    """``{wid:uuid}`` + MCP ``tools/call arguments={"wid": "6bc9..."}`` ⇒ handler sees a real UUID."""
    captured: dict[str, Any] = {}

    @get("/items/{wid:uuid}", mcp_tool="get_item", sync_to_thread=False)
    def tool(wid: UUID) -> dict[str, Any]:
        captured["wid"] = wid
        captured["wid_type"] = type(wid).__name__
        return {"ok": True}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "get_item", {"wid": "6bc9e12e-0000-0000-0000-000000000000"})

    assert resp["result"]["isError"] is False
    assert isinstance(captured["wid"], UUID)
    assert captured["wid"] == UUID("6bc9e12e-0000-0000-0000-000000000000")


def test_int_path_param_coerced() -> None:
    captured: dict[str, Any] = {}

    @get("/items/{item_id:int}", mcp_tool="get_item", sync_to_thread=False)
    def tool(item_id: int) -> dict[str, Any]:
        captured["item_id"] = item_id
        return {"ok": True}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "get_item", {"item_id": "42"})

    assert resp["result"]["isError"] is False
    assert captured["item_id"] == 42
    assert isinstance(captured["item_id"], int)


def test_datetime_path_param_coerced() -> None:
    captured: dict[str, Any] = {}

    @get("/events/{when:datetime}", mcp_tool="get_event", sync_to_thread=False)
    def tool(when: datetime) -> dict[str, Any]:
        captured["when"] = when
        return {"ok": True}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "get_event", {"when": "2026-04-19T12:00:00"})

    assert resp["result"]["isError"] is False
    assert isinstance(captured["when"], datetime)
    assert captured["when"].year == 2026


def test_str_path_param_unchanged() -> None:
    """Regression pin: ``{name:str}`` (no coercion needed) still works unchanged."""
    captured: dict[str, Any] = {}

    @get("/users/{name:str}", mcp_tool="get_user", sync_to_thread=False)
    def tool(name: str) -> dict[str, Any]:
        captured["name"] = name
        return {"ok": True}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "get_user", {"name": "alice"})

    assert resp["result"]["isError"] is False
    assert captured["name"] == "alice"


def test_mixed_typed_and_untyped_path_params() -> None:
    """Mixed UUID + str in the same path coerce independently."""
    captured: dict[str, Any] = {}

    @get("/orgs/{org_id:uuid}/projects/{project:str}", mcp_tool="get_project", sync_to_thread=False)
    def tool(org_id: UUID, project: str) -> dict[str, Any]:
        captured["org_id"] = org_id
        captured["project"] = project
        return {"ok": True}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(
            client,
            "get_project",
            {"org_id": "6bc9e12e-0000-0000-0000-000000000000", "project": "atlas"},
        )

    assert resp["result"]["isError"] is False
    assert isinstance(captured["org_id"], UUID)
    assert captured["project"] == "atlas"


# ---------------------------------------------------------------------------
# Bad-input surface
# ---------------------------------------------------------------------------


def test_bad_uuid_path_param_surfaces_invalid_params_error() -> None:
    """A bogus UUID must NOT invoke the handler; JSON-RPC returns a structured error."""
    called = False

    @get("/items/{wid:uuid}", mcp_tool="get_item", sync_to_thread=False)
    def tool(wid: UUID) -> dict[str, Any]:
        nonlocal called
        called = True
        return {"wid": str(wid)}

    app = Litestar(route_handlers=[tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _call_tool(client, "get_item", {"wid": "not-a-uuid"})

    assert called is False
    # Either a structured tools/call error (isError=True with a clear payload)
    # OR a JSON-RPC-level INVALID_PARAMS error. Both are acceptable; we pin
    # that the path-param name surfaces somewhere in the error payload so the
    # caller knows which argument broke.
    if "error" in resp:
        assert resp["error"]["code"] in (-32602, -32603)  # INVALID_PARAMS or INTERNAL_ERROR
        assert "wid" in str(resp["error"])
    else:
        assert resp["result"]["isError"] is True
        body = resp["result"]["content"][0]["text"]
        assert "wid" in body


# ---------------------------------------------------------------------------
# End-to-end guard reproducer (matches GH #43 body)
# ---------------------------------------------------------------------------


def test_guard_sees_coerced_uuid_path_param() -> None:
    """Reproducer from GH #43.

    Guards run BEFORE ``signature_model.parse_values_from_connection_kwargs``,
    so any coercion the signature model would apply for the handler body is
    not yet visible when the guard fires. Litestar's HTTP pipeline pre-coerces
    ``scope["path_params"]`` via
    :func:`litestar._asgi.routing_trie.traversal.parse_path_params` on the way
    into the ASGI handler, so HTTP guards see typed values. The MCP executor
    must do the same.

    Before the fix: ``connection.path_params["workspace_id"]`` is the raw
    ``str`` ``"6bc9..."``; ``isinstance(…, UUID)`` is ``False`` and the guard
    rejects the request. After the fix: the guard sees a ``UUID`` instance.
    """

    observed: dict[str, Any] = {}

    def requires_uuid(
        connection: "ASGIConnection[Any, Any, Any, Any]",
        _handler: "BaseRouteHandler",
    ) -> None:
        observed["type"] = type(connection.path_params["workspace_id"]).__name__
        if not isinstance(connection.path_params["workspace_id"], UUID):
            message = f"guard saw raw {observed['type']}, expected UUID"
            raise PermissionDeniedException(message)

    @get(
        "/api/workspaces/{workspace_id:uuid}/files",
        mcp_tool="list_workspace_files",
        guards=[requires_uuid],
        sync_to_thread=False,
    )
    def list_files(workspace_id: UUID) -> dict[str, Any]:
        return {"workspace_id": str(workspace_id), "files": []}

    app = Litestar(route_handlers=[list_files], plugins=[LitestarMCP()])

    with TestClient(app=app) as client:
        resp = _call_tool(
            client,
            "list_workspace_files",
            {"workspace_id": "6bc9e12e-0000-0000-0000-000000000000"},
        )

    assert observed["type"] == "UUID", f"guard saw {observed['type']!r} — #43 fix missing"
    assert resp["result"]["isError"] is False
