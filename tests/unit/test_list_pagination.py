"""Cursor pagination for ``tools/list``, ``resources/list``, ``prompts/list``."""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, mcp_prompt
from litestar_mcp.config import MCPConfig


def _rpc(
    client: TestClient[Any],
    method: str,
    params: "dict[str, Any] | None" = None,
    headers: "dict[str, str] | None" = None,
) -> Any:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    return client.post("/mcp", json=body, headers=headers or {})


def _init_session(client: TestClient[Any]) -> str:
    init = _rpc(
        client,
        "initialize",
        {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "x"}},
    )
    sid = init.headers["mcp-session-id"]
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    return sid


def _make_tools_app(count: int, page_size: int) -> Litestar:
    handlers = []
    for i in range(count):

        @get(f"/t{i}", name=f"t{i}", opt={"mcp_tool": f"tool_{i:03d}"}, sync_to_thread=False)
        def _h() -> dict[str, int]:
            return {"i": 0}

        handlers.append(_h)
    return Litestar(route_handlers=handlers, plugins=[LitestarMCP(config=MCPConfig(list_page_size=page_size))])


def test_tools_list_paginates_with_next_cursor() -> None:
    app = _make_tools_app(count=5, page_size=2)
    with TestClient(app=app) as client:
        sid = _init_session(client)
        headers = {"Mcp-Session-Id": sid}

        # Page 1
        r1 = _rpc(client, "tools/list", {}, headers=headers).json()["result"]
        assert len(r1["tools"]) == 2
        assert "nextCursor" in r1

        # Page 2
        r2 = _rpc(client, "tools/list", {"cursor": r1["nextCursor"]}, headers=headers).json()["result"]
        assert len(r2["tools"]) == 2
        assert "nextCursor" in r2

        # Page 3 — final page, no nextCursor
        r3 = _rpc(client, "tools/list", {"cursor": r2["nextCursor"]}, headers=headers).json()["result"]
        assert len(r3["tools"]) == 1
        assert "nextCursor" not in r3

        all_names = [t["name"] for t in r1["tools"] + r2["tools"] + r3["tools"]]
        assert all_names == [f"tool_{i:03d}" for i in range(5)]


def test_tools_list_single_page_omits_next_cursor() -> None:
    app = _make_tools_app(count=2, page_size=100)
    with TestClient(app=app) as client:
        sid = _init_session(client)
        result = _rpc(client, "tools/list", {}, headers={"Mcp-Session-Id": sid}).json()["result"]
        assert len(result["tools"]) == 2
        assert "nextCursor" not in result


def test_tools_list_rejects_invalid_cursor() -> None:
    app = _make_tools_app(count=3, page_size=2)
    with TestClient(app=app) as client:
        sid = _init_session(client)
        resp = _rpc(client, "tools/list", {"cursor": "!!!not-base64!!!"}, headers={"Mcp-Session-Id": sid}).json()
        assert resp["error"]["code"] == -32602


def test_tools_list_rejects_non_string_cursor() -> None:
    app = _make_tools_app(count=3, page_size=2)
    with TestClient(app=app) as client:
        sid = _init_session(client)
        resp = _rpc(client, "tools/list", {"cursor": 42}, headers={"Mcp-Session-Id": sid}).json()
        assert resp["error"]["code"] == -32602


def test_tools_list_rejects_negative_offset_cursor() -> None:
    import base64

    app = _make_tools_app(count=3, page_size=2)
    with TestClient(app=app) as client:
        sid = _init_session(client)
        negative = base64.urlsafe_b64encode(b"-1").decode("ascii")
        resp = _rpc(client, "tools/list", {"cursor": negative}, headers={"Mcp-Session-Id": sid}).json()
        assert resp["error"]["code"] == -32602


def test_tools_list_cursor_past_end_returns_empty_page() -> None:
    import base64

    app = _make_tools_app(count=3, page_size=2)
    with TestClient(app=app) as client:
        sid = _init_session(client)
        far = base64.urlsafe_b64encode(b"99").decode("ascii")
        result = _rpc(client, "tools/list", {"cursor": far}, headers={"Mcp-Session-Id": sid}).json()["result"]
        assert result["tools"] == []
        assert "nextCursor" not in result


def test_resources_list_paginates() -> None:
    handlers = []
    # The built-in litestar://openapi resource counts as one entry.
    for i in range(4):

        @get(f"/r{i}", name=f"r{i}", opt={"mcp_resource": f"res_{i:03d}"}, sync_to_thread=False)
        def _h() -> dict[str, int]:
            return {"i": 0}

        handlers.append(_h)
    app = Litestar(route_handlers=handlers, plugins=[LitestarMCP(config=MCPConfig(list_page_size=2))])
    with TestClient(app=app) as client:
        sid = _init_session(client)
        headers = {"Mcp-Session-Id": sid}
        r1 = _rpc(client, "resources/list", {}, headers=headers).json()["result"]
        assert len(r1["resources"]) == 2
        assert "nextCursor" in r1
        r2 = _rpc(client, "resources/list", {"cursor": r1["nextCursor"]}, headers=headers).json()["result"]
        assert len(r2["resources"]) == 2
        assert "nextCursor" in r2
        r3 = _rpc(client, "resources/list", {"cursor": r2["nextCursor"]}, headers=headers).json()["result"]
        assert len(r3["resources"]) == 1
        assert "nextCursor" not in r3


def test_prompts_list_paginates() -> None:
    @mcp_prompt(name="p_a")
    def p_a() -> str:
        return "a"

    @mcp_prompt(name="p_b")
    def p_b() -> str:
        return "b"

    @mcp_prompt(name="p_c")
    def p_c() -> str:
        return "c"

    app = Litestar(plugins=[LitestarMCP(prompts=[p_a, p_b, p_c], config=MCPConfig(list_page_size=2))])
    with TestClient(app=app) as client:
        sid = _init_session(client)
        headers = {"Mcp-Session-Id": sid}
        r1 = _rpc(client, "prompts/list", {}, headers=headers).json()["result"]
        assert len(r1["prompts"]) == 2
        assert "nextCursor" in r1
        r2 = _rpc(client, "prompts/list", {"cursor": r1["nextCursor"]}, headers=headers).json()["result"]
        assert len(r2["prompts"]) == 1
        assert "nextCursor" not in r2


def test_tools_list_empty_registry_returns_empty_page() -> None:
    app = Litestar(plugins=[LitestarMCP(config=MCPConfig(list_page_size=10))])
    with TestClient(app=app) as client:
        sid = _init_session(client)
        result = _rpc(client, "tools/list", {}, headers={"Mcp-Session-Id": sid}).json()["result"]
        assert result["tools"] == []
        assert "nextCursor" not in result


def test_prompts_list_empty_registry_returns_empty_page() -> None:
    app = Litestar(plugins=[LitestarMCP(config=MCPConfig(list_page_size=10))])
    with TestClient(app=app) as client:
        sid = _init_session(client)
        result = _rpc(client, "prompts/list", {}, headers={"Mcp-Session-Id": sid}).json()["result"]
        assert result["prompts"] == []
        assert "nextCursor" not in result


@pytest.mark.parametrize("bad_size", [0, -1, -100])
def test_mcp_config_rejects_non_positive_list_page_size(bad_size: int) -> None:
    with pytest.raises(ValueError, match="list_page_size must be a positive integer"):
        MCPConfig(list_page_size=bad_size)
