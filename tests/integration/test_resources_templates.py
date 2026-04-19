"""MCP 2025-11-25 resource-templates + completion/complete flows.

Ch4 of ``v0.5.0-consumer-readiness`` adds RFC 6570 Level 1 URI templates to
``@mcp_resource`` + ``mcp_resource_template`` opt-key, plus the
``resources/templates/list`` and ``completion/complete`` JSON-RPC methods.
"""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import AsyncTestClient

from litestar_mcp import LitestarMCP, MCPConfig

pytestmark = pytest.mark.integration


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _app(*handlers: Any) -> Litestar:
    return Litestar(route_handlers=list(handlers), plugins=[LitestarMCP(MCPConfig())])


async def _init(client: AsyncTestClient[Any]) -> str:
    init = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 0,
            "method": "initialize",
            "params": {"protocolVersion": "2025-11-25", "capabilities": {}, "clientInfo": {"name": "t"}},
        },
    )
    sid = init.headers.get("mcp-session-id", "")
    await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Mcp-Session-Id": sid},
    )
    return str(sid)


async def _rpc(
    client: AsyncTestClient[Any], method: str, params: dict[str, Any] | None = None, *, sid: str
) -> dict[str, Any]:
    body: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return (await client.post("/mcp", json=body, headers={"Mcp-Session-Id": sid})).json()  # type: ignore[no-any-return]


@pytest.mark.anyio
async def test_resources_templates_list_surfaces_registered_template() -> None:
    @get(
        "/wf/{wid:str}/{fid:str}",
        mcp_resource="wf",
        mcp_resource_template="app://w/{wid}/f/{fid}",
        sync_to_thread=False,
    )
    def handler(wid: str, fid: str) -> dict[str, str]:
        return {"wid": wid, "fid": fid}

    async with AsyncTestClient(app=_app(handler)) as client:
        sid = await _init(client)
        resp = await _rpc(client, "resources/templates/list", sid=sid)
        templates = resp["result"]["resourceTemplates"]
        assert any(t["uriTemplate"] == "app://w/{wid}/f/{fid}" and t["name"] == "wf" for t in templates)


@pytest.mark.anyio
async def test_resources_read_dispatches_template_uri_with_extracted_vars() -> None:
    @get(
        "/wf/{wid:str}/{fid:str}",
        mcp_resource="wf",
        mcp_resource_template="app://w/{wid}/f/{fid}",
        sync_to_thread=False,
    )
    def handler(wid: str, fid: str) -> dict[str, str]:
        return {"wid": wid, "fid": fid}

    async with AsyncTestClient(app=_app(handler)) as client:
        sid = await _init(client)
        resp = await _rpc(client, "resources/read", {"uri": "app://w/42/f/99"}, sid=sid)
        content = resp["result"]["contents"][0]["text"]
        assert '"wid":"42"' in content or '"wid": "42"' in content
        assert '"fid":"99"' in content or '"fid": "99"' in content


@pytest.mark.anyio
async def test_resources_read_unknown_uri_returns_error() -> None:
    @get(
        "/wf/{wid:str}",
        mcp_resource="wf",
        mcp_resource_template="app://w/{wid}",
        sync_to_thread=False,
    )
    def handler(wid: str) -> dict[str, str]:
        return {"wid": wid}

    async with AsyncTestClient(app=_app(handler)) as client:
        sid = await _init(client)
        resp = await _rpc(client, "resources/read", {"uri": "unknown://x"}, sid=sid)
        assert "error" in resp


@pytest.mark.anyio
async def test_completion_complete_returns_empty_default_for_registered_template() -> None:
    @get(
        "/wf/{wid:str}",
        mcp_resource="wf",
        mcp_resource_template="app://w/{wid}",
        sync_to_thread=False,
    )
    def handler(wid: str) -> dict[str, str]:
        return {"wid": wid}

    async with AsyncTestClient(app=_app(handler)) as client:
        sid = await _init(client)
        resp = await _rpc(
            client,
            "completion/complete",
            {"ref": {"type": "ref/resource", "uri": "app://w/{wid}"}, "argument": {"name": "wid", "value": "ab"}},
            sid=sid,
        )
        assert resp["result"]["completion"] == {"values": [], "total": 0, "hasMore": False}


@pytest.mark.anyio
async def test_completion_complete_returns_empty_for_unknown_ref() -> None:
    async with AsyncTestClient(app=_app()) as client:
        sid = await _init(client)
        resp = await _rpc(
            client,
            "completion/complete",
            {"ref": {"type": "ref/resource", "uri": "app://unknown"}, "argument": {"name": "x", "value": ""}},
            sid=sid,
        )
        assert resp["result"]["completion"] == {"values": [], "total": 0, "hasMore": False}


@pytest.mark.anyio
async def test_concrete_resource_still_resolves_via_litestar_scheme() -> None:
    """Template registration does not break the existing ``litestar://<name>`` path."""

    @get("/config", mcp_resource="app_config", sync_to_thread=False)
    def handler() -> dict[str, bool]:
        return {"debug": True}

    async with AsyncTestClient(app=_app(handler)) as client:
        sid = await _init(client)
        resp = await _rpc(client, "resources/read", {"uri": "litestar://app_config"}, sid=sid)
        assert resp["result"]["contents"][0]["text"] in ('{"debug":true}', '{"debug": true}')


@pytest.mark.anyio
async def test_ambiguous_templates_resolve_first_registered() -> None:
    """When two templates could match the same URI, registration order wins."""

    @get("/a/{x:str}", mcp_resource="first", mcp_resource_template="app://x/{x}", sync_to_thread=False)
    def first(x: str) -> dict[str, str]:
        return {"which": "first", "x": x}

    @get("/b/{x:str}", mcp_resource="second", mcp_resource_template="app://x/{x}", sync_to_thread=False)
    def second(x: str) -> dict[str, str]:
        return {"which": "second", "x": x}

    async with AsyncTestClient(app=_app(first, second)) as client:
        sid = await _init(client)
        resp = await _rpc(client, "resources/read", {"uri": "app://x/42"}, sid=sid)
        assert '"which":"first"' in resp["result"]["contents"][0]["text"].replace(" ", "")
