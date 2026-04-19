"""Endpoint-level tests for rendered tool/resource descriptions.

These live under ``tests/unit/`` (rather than ``tests/integration/``) because
they use Litestar's in-process ``TestClient`` and don't need a database —
matching the project convention that unit tests exercise wire behaviour
without external services.
"""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig, mcp_resource, mcp_tool


def _make_app(*handlers: Any) -> Litestar:
    return Litestar(route_handlers=list(handlers), plugins=[LitestarMCP(MCPConfig())])


def _init_and_get_session(client: TestClient[Any]) -> str:
    init = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "it"}},
        },
    )
    assert init.status_code == 200, init.text
    sid = init.headers["mcp-session-id"]
    client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    return sid


def _rpc(client: TestClient[Any], method: str, sid: str, params: "dict[str, Any] | None" = None) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        body["params"] = params
    resp = client.post("/mcp", json=body, headers={"Mcp-Session-Id": sid})
    return resp.json()  # type: ignore[no-any-return]


def test_tools_list_returns_decorator_description() -> None:
    @mcp_tool("t", description="LLM prose", when_to_use="When asked")
    @get("/x", sync_to_thread=False)
    def handler() -> dict[str, Any]:
        return {}

    with TestClient(app=_make_app(handler)) as client:
        sid = _init_and_get_session(client)
        result = _rpc(client, "tools/list", sid)
        tools = result["result"]["tools"]
        descr = next(t["description"] for t in tools if t["name"] == "t")
        assert descr.startswith("LLM prose")
        assert "## When to use\nWhen asked" in descr


def test_tools_list_opt_form_overrides_docstring() -> None:
    @get("/x", opt={"mcp_tool": "t", "mcp_description": "opt-prose"}, sync_to_thread=False)
    def handler() -> dict[str, Any]:
        """docstring-prose."""
        return {}

    with TestClient(app=_make_app(handler)) as client:
        sid = _init_and_get_session(client)
        result = _rpc(client, "tools/list", sid)
        tools = result["result"]["tools"]
        descr = next(t["description"] for t in tools if t["name"] == "t")
        assert descr == "opt-prose"


def test_tools_list_docstring_fallback_unchanged() -> None:
    """v0.4.0 regression guard: plain docstring → unchanged plain string."""

    @get("/x", opt={"mcp_tool": "t"}, sync_to_thread=False)
    def handler() -> dict[str, Any]:
        """plain-docstring."""
        return {}

    with TestClient(app=_make_app(handler)) as client:
        sid = _init_and_get_session(client)
        result = _rpc(client, "tools/list", sid)
        tools = result["result"]["tools"]
        descr = next(t["description"] for t in tools if t["name"] == "t")
        assert descr == "plain-docstring."
        assert "##" not in descr


def test_resources_list_returns_rendered_description() -> None:
    @mcp_resource("r", description="res-prose", when_to_use="Sometimes")
    @get("/y", sync_to_thread=False)
    def handler() -> dict[str, Any]:
        return {}

    with TestClient(app=_make_app(handler)) as client:
        sid = _init_and_get_session(client)
        result = _rpc(client, "resources/list", sid)
        resources = result["result"]["resources"]
        descr = next(r["description"] for r in resources if r["name"] == "r")
        assert descr.startswith("res-prose")
        assert "## When to use\nSometimes" in descr


def test_resources_list_opt_form_resource_description_key() -> None:
    @get(
        "/y",
        opt={"mcp_resource": "r", "mcp_resource_description": "opt-res-prose"},
        sync_to_thread=False,
    )
    def handler() -> dict[str, Any]:
        """docstring-prose."""
        return {}

    with TestClient(app=_make_app(handler)) as client:
        sid = _init_and_get_session(client)
        result = _rpc(client, "resources/list", sid)
        resources = result["result"]["resources"]
        descr = next(r["description"] for r in resources if r["name"] == "r")
        assert descr == "opt-res-prose"


def test_agent_card_and_mcp_server_manifest_match_tools_list() -> None:
    @mcp_tool("t", description="primary", when_to_use="wtu", returns="r")
    @get("/x", sync_to_thread=False)
    def handler() -> dict[str, Any]:
        return {}

    with TestClient(app=_make_app(handler)) as client:
        sid = _init_and_get_session(client)
        tl = _rpc(client, "tools/list", sid)
        tl_descr = next(t["description"] for t in tl["result"]["tools"] if t["name"] == "t")

        agent_card = client.get("/.well-known/agent-card.json").json()
        ac_descr = next(s["description"] for s in agent_card["skills"] if s["id"] == "t")

        mcp_manifest = client.get("/.well-known/mcp-server.json").json()
        mm_descr = next(t["description"] for t in mcp_manifest["tools"] if t["name"] == "t")

        assert tl_descr == ac_descr == mm_descr
        assert "## When to use\nwtu" in tl_descr
        assert "## Returns\nr" in tl_descr


@pytest.mark.parametrize(
    "doc",
    [
        "  leading-spaces-and-trailing-spaces.  ",
        "\nleading newline.",
        "trailing newline.\n",
    ],
)
def test_tools_list_docstring_stripped_like_legacy(doc: str) -> None:
    """Plain-mode output must equal legacy ``(fn.__doc__ or ...).strip()``."""

    @get("/x", opt={"mcp_tool": "t"}, sync_to_thread=False)
    def handler() -> dict[str, Any]:
        return {}

    handler.fn.__doc__ = doc

    with TestClient(app=_make_app(handler)) as client:
        sid = _init_and_get_session(client)
        result = _rpc(client, "tools/list", sid)
        tools = result["result"]["tools"]
        descr = next(t["description"] for t in tools if t["name"] == "t")
        assert descr == doc.strip()
