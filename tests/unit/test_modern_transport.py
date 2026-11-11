"""MCP 2026-07-28 and 2025-11-25 Streamable HTTP contract tests."""

import asyncio
import contextlib
from typing import TYPE_CHECKING, Annotated, Any, cast

import httpx2
import pytest
from litestar import Litestar, get
from litestar.enums import ScopeType
from litestar.exceptions import PermissionDeniedException
from litestar.middleware import ASGIMiddleware
from litestar.params import Parameter
from litestar.stores.memory import MemoryStore
from litestar.stores.registry import StoreRegistry
from litestar.testing import AsyncTestClient, TestClient

from litestar_mcp import SUPPORTED_PROTOCOL_VERSIONS, LitestarMCP, MCPConfig, MCPToolResult
from litestar_mcp.core.jsonrpc import INVALID_PARAMS, JSONRPCErrorException
from litestar_mcp.core.serialization import from_json
from litestar_mcp.mcp.client import MCPStreamableHTTPClient
from litestar_mcp.mcp.routes import (
    MCP_METHOD_HEADER,
    MCP_PROTOCOL_VERSION,
    MCP_PROTOCOL_VERSION_HEADER,
    UNSUPPORTED_PROTOCOL_VERSION,
)
from litestar_mcp.mcp.service import (
    SESSION_STORE_NAME,
    MCPHandlerService,
    MCPRequestContext,
    MCPSession,
    MCPSessionStore,
    get_mcp_request_context,
    get_session_store,
    resolve_session_store,
)
from litestar_mcp.mcp.stdio import ASGIStreamingTransport
from tests.unit.conftest import PROTOCOL_VERSION, mcp_envelope

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from litestar import Request
    from litestar.connection import ASGIConnection
    from litestar.handlers import BaseRouteHandler
    from litestar.types import ASGIApp, Receive, Scope, Send


class _HeaderAuthMiddleware(ASGIMiddleware):
    scopes = (ScopeType.HTTP,)

    async def handle(self, scope: "Scope", receive: "Receive", send: "Send", next_app: "ASGIApp") -> None:
        raw_headers = dict(scope.get("headers", ()))
        raw_user = raw_headers.get(b"x-test-user")
        if raw_user is not None:
            cast("dict[str, Any]", scope)["auth"] = {"sub": raw_user.decode("utf-8")}
        await next_app(scope, receive, send)


async def _next_sse_payload(lines: "AsyncIterator[str]") -> dict[str, Any]:
    while True:
        line = await asyncio.wait_for(lines.__anext__(), timeout=2.0)
        if line.startswith("data:"):
            payload = from_json(line.removeprefix("data:").strip())
            assert isinstance(payload, dict)
            return payload


async def _drain_remaining_sse_payloads(lines: "AsyncIterator[str]") -> list[dict[str, Any]]:
    remaining: list[dict[str, Any]] = []
    while True:
        try:
            line = await asyncio.wait_for(lines.__anext__(), timeout=2.0)
        except StopAsyncIteration:
            return remaining
        if line.startswith("data:"):
            payload = from_json(line.removeprefix("data:").strip())
            assert isinstance(payload, dict)
            remaining.append(payload)


async def _wait_for_active_stream(plugin: LitestarMCP) -> None:
    for _ in range(400):
        if plugin.registry.subscription_manager.active_stream_count > 0:
            return
        await asyncio.sleep(0.005)
    raise asyncio.TimeoutError


def _app(config: MCPConfig | None = None) -> Litestar:
    @get("/z", mcp_tool="z_tool", sync_to_thread=False)
    def z_tool() -> dict[str, str]:
        return {"name": "z"}

    @get("/a", mcp_tool="a_tool", sync_to_thread=False)
    def a_tool() -> dict[str, str]:
        return {"name": "a"}

    return Litestar(route_handlers=[z_tool, a_tool], plugins=[LitestarMCP(config)])


def _request(
    client: TestClient[Any],
    method: str,
    *,
    id_: int = 1,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    meta_version: str = PROTOCOL_VERSION,
) -> Any:
    """POST one request; ``headers`` and ``meta_version`` deliberately override the shared envelope."""
    body, request_headers = mcp_envelope(method, params, msg_id=id_)
    body["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] = meta_version
    request_headers.update(headers or {})
    return client.post("/mcp", json=body, headers=request_headers)


def test_server_discover_is_stateless_and_self_describing() -> None:
    with TestClient(app=_app()) as client:
        response = _request(client, "server/discover")

    assert response.status_code == 200
    assert "mcp-session-id" not in response.headers
    assert response.headers["mcp-protocol-version"] == "2026-07-28"
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"]
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"
    assert result["_meta"]["io.modelcontextprotocol/serverInfo"]["name"]


def test_tools_list_has_cache_hints_result_metadata_and_sorted_tools() -> None:
    with TestClient(app=_app()) as client:
        response = _request(client, "tools/list")

    assert response.status_code == 200
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["ttlMs"] == 0
    assert result["cacheScope"] == "private"
    assert [tool["name"] for tool in result["tools"]] == ["a_tool", "z_tool"]


def test_header_body_protocol_version_mismatch_is_header_mismatch() -> None:
    with TestClient(app=_app()) as client:
        response = _request(client, "tools/list", headers={"MCP-Protocol-Version": "2025-11-25"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020


def test_unsupported_protocol_version_lists_supported_versions() -> None:
    with TestClient(app=_app()) as client:
        response = _request(
            client,
            "tools/list",
            headers={"MCP-Protocol-Version": "2099-01-01"},
            meta_version="2099-01-01",
        )
        body = response.json()

    assert response.status_code == 400
    assert body["error"]["code"] == -32022
    assert body["error"]["data"]["supportedVersions"] == ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"]


def test_method_header_mismatch_is_header_mismatch() -> None:
    with TestClient(app=_app()) as client:
        response = _request(client, "tools/list", headers={"Mcp-Method": "resources/list"})

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32020


@pytest.mark.parametrize(
    "method",
    [
        "initialize",
        "ping",
        "unknown/method",
        "resources/subscribe",
        "resources/unsubscribe",
    ],
)
def test_unknown_method_uses_http_404_and_jsonrpc_method_not_found(method: str) -> None:
    with TestClient(app=_app()) as client:
        response = _request(client, method)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == -32601


def test_mcp_endpoint_rejects_get_and_accepts_stateless_delete() -> None:
    with TestClient(app=_app()) as client:
        get_response = client.get("/mcp")
        delete_response = client.delete("/mcp")
        delete_forbidden = client.delete("/mcp", headers={"Origin": "https://attacker.example"})

    assert get_response.status_code == 405
    assert delete_response.status_code == 204
    assert delete_forbidden.status_code == 403


def test_same_origin_is_allowed_and_cross_origin_is_forbidden() -> None:
    with TestClient(app=_app()) as client:
        same_origin = _request(client, "server/discover", headers={"Origin": "http://testserver.local"})
        cross_origin = _request(client, "server/discover", headers={"Origin": "https://attacker.example"})

    assert same_origin.status_code == 200
    assert cross_origin.status_code == 403


def test_exact_configured_origin_is_allowed() -> None:
    config = MCPConfig(allowed_origins=["https://client.example"])
    with TestClient(app=_app(config)) as client:
        response = _request(client, "server/discover", headers={"Origin": "https://client.example"})

    assert response.status_code == 200


@pytest.mark.parametrize(
    ("req_version", "expected_version", "expect_session_header"),
    [
        ("2025-11-25", "2025-11-25", True),
        ("2025-06-18", "2025-06-18", True),
        ("2025-03-26", "2025-03-26", True),
        ("2024-11-05", "2025-11-25", True),
        ("2026-07-28", "2026-07-28", False),
    ],
)
def test_initialize_negotiates_versions_and_issues_session_header(
    req_version: str,
    expected_version: str,
    expect_session_header: bool,
) -> None:
    config = MCPConfig(instructions="Follow workspace rules.")
    with TestClient(app=_app(config)) as client:
        init_resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": req_version,
                    "capabilities": {},
                    "clientInfo": {"name": "test-client", "version": "1.0.0"},
                },
            },
        )

    assert init_resp.status_code == 200
    assert init_resp.headers["mcp-protocol-version"] == expected_version
    if expect_session_header:
        sid = init_resp.headers.get("mcp-session-id")
        assert isinstance(sid, str) and len(sid) > 0
    else:
        assert "mcp-session-id" not in init_resp.headers
    init_result = init_resp.json()["result"]
    assert init_result["protocolVersion"] == expected_version
    assert "tools" in init_result["capabilities"]
    assert "resources" in init_result["capabilities"]
    assert "name" in init_result["serverInfo"]
    assert init_result["instructions"] == "Follow workspace rules."


def test_dual_version_initialize_notifications_and_ping_interoperability() -> None:
    assert SUPPORTED_PROTOCOL_VERSIONS == ("2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26")

    config = MCPConfig(instructions="Follow workspace rules.")
    with TestClient(app=_app(config)) as client:
        notif_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"MCP-Protocol-Version": "2025-06-18"},
        )
        assert notif_resp.status_code == 202
        assert notif_resp.headers["mcp-protocol-version"] == "2025-06-18"
        assert notif_resp.content == b""

        cancelled_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 99}},
        )
        assert cancelled_resp.status_code == 202
        assert cancelled_resp.content == b""

        ping_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
            headers={"MCP-Protocol-Version": "2025-06-18"},
        )
        assert ping_resp.status_code == 200
        assert ping_resp.headers["mcp-protocol-version"] == "2025-06-18"
        assert "result" in ping_resp.json()

        tools_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            headers={"MCP-Protocol-Version": "2025-06-18"},
        )
        assert tools_resp.status_code == 200
        assert tools_resp.headers["mcp-protocol-version"] == "2025-06-18"
        assert [t["name"] for t in tools_resp.json()["result"]["tools"]] == ["a_tool", "z_tool"]

        call_resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "a_tool", "arguments": {}},
            },
            headers={"MCP-Protocol-Version": "2025-03-26"},
        )
        assert call_resp.status_code == 200
        assert call_resp.headers["mcp-protocol-version"] == "2025-03-26"
        assert call_resp.json()["result"]["content"][0]["type"] == "text"


def test_legacy_session_header_is_ignored() -> None:
    with TestClient(app=_app()) as client:
        response = _request(client, "tools/list", headers={"Mcp-Session-Id": "legacy"})

    assert response.status_code == 200
    assert "mcp-session-id" not in response.headers


def test_explicit_null_structured_content_is_not_omitted() -> None:
    result = MCPToolResult(content="ok", structured_content=None).to_result()

    assert "structuredContent" in result
    assert result["structuredContent"] is None


def test_parameter_header_annotation_is_discovered_and_validated() -> None:
    @get("/region", mcp_tool="region", sync_to_thread=False)
    def region(
        region_name: Annotated[str, Parameter(schema_extra={"x-mcp-header": "Region"})],
    ) -> str:
        return region_name

    app = Litestar(route_handlers=[region], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        listed = _request(client, "tools/list").json()
        prop = listed["result"]["tools"][0]["inputSchema"]["properties"]["region_name"]
        assert prop["x-mcp-header"] == "Region"

        missing = _request(
            client,
            "tools/call",
            params={"name": "region", "arguments": {"region_name": "eu-west1"}},
        )
        matching = _request(
            client,
            "tools/call",
            params={"name": "region", "arguments": {"region_name": "eu-west1"}},
            headers={"Mcp-Param-Region": "eu-west1"},
        )

    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == -32020
    assert matching.status_code == 200


def test_subscriptions_listen_streams_acknowledgement_first() -> None:
    app = _app()
    plugin = app.plugins.get(LitestarMCP)

    class FiniteSubscriptions:
        async def open(self, subscription_id: Any, notifications: dict[str, Any]) -> Any:
            async def stream() -> Any:
                yield {
                    "jsonrpc": "2.0",
                    "method": "notifications/subscriptions/acknowledged",
                    "params": {
                        "_meta": {"io.modelcontextprotocol/subscriptionId": subscription_id},
                        "notifications": notifications,
                    },
                }

            return "finite", stream()

        async def disconnect(self, stream_id: str) -> None:
            return None

    plugin.registry.set_subscription_manager(FiniteSubscriptions())  # type: ignore[arg-type]
    with TestClient(app=app) as client:
        body, headers = mcp_envelope(
            "subscriptions/listen", {"notifications": {"toolsListChanged": True}}, msg_id="sub-1"
        )
        with client.stream("POST", "/mcp", json=body, headers=headers) as response:
            lines = response.iter_lines()
            data_line = next(line for line in lines if line.startswith("data: "))

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert '"method":"notifications/subscriptions/acknowledged"' in data_line
    assert '"io.modelcontextprotocol/subscriptionId":"sub-1"' in data_line


def test_2025_11_25_session_lifecycle_headers_and_malformed_id() -> None:
    with TestClient(app=_app()) as client:
        init_resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "lifecycle-client", "version": "1.0.0"},
                },
            },
        )
        assert init_resp.status_code == 200
        assert init_resp.headers["mcp-protocol-version"] == "2025-11-25"
        sid = init_resp.headers["mcp-session-id"]
        assert sid

        notif_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Mcp-Session-Id": sid},
        )
        assert notif_resp.status_code == 202
        assert notif_resp.headers["mcp-protocol-version"] == "2025-11-25"

        tools_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={"MCP-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
        )
        assert tools_resp.status_code == 200
        assert tools_resp.headers["mcp-protocol-version"] == "2025-11-25"
        assert "mcp-session-id" not in tools_resp.headers

        malformed_space = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            headers={"MCP-Protocol-Version": "2025-11-25", "Mcp-Session-Id": "bad id"},
        )
        assert malformed_space.status_code == 404

        malformed_overlong = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}},
            headers={"MCP-Protocol-Version": "2025-11-25", "Mcp-Session-Id": "x" * 513},
        )
        assert malformed_overlong.status_code == 404

        del_resp = client.delete("/mcp", headers={"Mcp-Session-Id": sid})
        assert del_resp.status_code == 204
        assert del_resp.headers["mcp-protocol-version"] == "2025-11-25"

        after_del = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 5, "method": "tools/list", "params": {}},
            headers={"MCP-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
        )
        assert after_del.status_code == 404

        second_del = client.delete("/mcp", headers={"Mcp-Session-Id": sid})
        assert second_del.status_code == 404


def test_delete_ignores_session_on_2026_and_rejects_invalid_protocol_version() -> None:
    with TestClient(app=_app()) as client:
        init_resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            },
        )
        sid = init_resp.headers["mcp-session-id"]

        modern_del = client.delete(
            "/mcp",
            headers={"MCP-Protocol-Version": "2026-07-28", "Mcp-Session-Id": sid},
        )
        assert modern_del.status_code == 204
        assert modern_del.headers["mcp-protocol-version"] == "2026-07-28"

        still_alive = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={"Mcp-Session-Id": sid},
        )
        assert still_alive.status_code == 200
        assert still_alive.headers["mcp-protocol-version"] == "2025-11-25"

        bad_version_del = client.delete("/mcp", headers={"MCP-Protocol-Version": "2099-01-01"})
        assert bad_version_del.status_code == 400
        assert bad_version_del.json()["error"]["code"] == -32022


@pytest.mark.parametrize(
    ("version_header", "session_mode", "expected_status", "expected_header_version"),
    [
        ("2099-01-01", "none", 400, "2026-07-28"),
        ("2025-11-25", "missing", 404, "2025-11-25"),
        ("2026-07-28", "legacy", 202, "2026-07-28"),
        (None, "active", 202, "2025-11-25"),
    ],
)
def test_client_notification_header_version_and_session_validation(
    version_header: str | None,
    session_mode: str,
    expected_status: int,
    expected_header_version: str,
) -> None:
    with TestClient(app=_app()) as client:
        headers: dict[str, str] = {}
        if version_header is not None:
            headers["MCP-Protocol-Version"] = version_header
        if session_mode == "missing":
            headers["Mcp-Session-Id"] = "missing"
        elif session_mode == "legacy":
            headers["Mcp-Session-Id"] = "legacy"
        elif session_mode == "active":
            init_resp = client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
                },
            )
            headers["Mcp-Session-Id"] = init_resp.headers["mcp-session-id"]

        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers=headers,
        )

    assert resp.status_code == expected_status
    assert resp.headers["mcp-protocol-version"] == expected_header_version
    if expected_status == 400:
        assert resp.json()["error"]["code"] == -32022


def test_progress_response_threads_2025_11_25_session_context() -> None:
    @get("/ctx", mcp_tool="ctx_tool")
    async def ctx_tool() -> dict[str, Any]:
        ctx = get_mcp_request_context()
        await ctx.report_progress(1, 2, "half")
        return {
            "session_id": ctx.session_id,
            "version": ctx.protocol_version,
            "client_id": ctx.client_id,
            "caps": ctx.client_capabilities,
        }

    app = Litestar(route_handlers=[ctx_tool], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        init_resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"roots": {"listChanged": True}},
                    "clientInfo": {"name": "progress-client", "version": "1.0"},
                },
            },
        )
        sid = init_resp.headers["mcp-session-id"]

        with client.stream(
            "POST",
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "ctx_tool",
                    "arguments": {},
                    "_meta": {"progressToken": "p1"},
                },
            },
            headers={"Mcp-Session-Id": sid},
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["mcp-protocol-version"] == "2025-11-25"
            payloads = [
                from_json(line.removeprefix("data:").strip()) for line in resp.iter_lines() if line.startswith("data:")
            ]

    assert payloads[0]["method"] == "notifications/progress"
    assert payloads[0]["params"]["progressToken"] == "p1"
    tool_output = from_json(payloads[-1]["result"]["content"][0]["text"])
    assert tool_output == {
        "session_id": sid,
        "version": "2025-11-25",
        "client_id": "progress-client",
        "caps": {"roots": {"listChanged": True}},
    }


@pytest.mark.asyncio
async def test_session_owner_isolation_across_post_notifications_get_and_delete() -> None:
    app = Litestar(
        route_handlers=[],
        middleware=[_HeaderAuthMiddleware()],
        plugins=[LitestarMCP()],
    )
    async with AsyncTestClient(app=app) as client:
        init_resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            },
            headers={"X-Test-User": "alice"},
        )
        sid = init_resp.headers["mcp-session-id"]

        bob_post = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            headers={"X-Test-User": "bob", "Mcp-Session-Id": sid},
        )
        bob_notif = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"X-Test-User": "bob", "Mcp-Session-Id": sid},
        )
        bob_get = await client.get(
            "/mcp",
            headers={"X-Test-User": "bob", "Accept": "text/event-stream", "Mcp-Session-Id": sid},
        )
        bob_delete = await client.delete(
            "/mcp",
            headers={"X-Test-User": "bob", "Mcp-Session-Id": sid},
        )
        alice_post = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            headers={"X-Test-User": "alice", "Mcp-Session-Id": sid},
        )

    assert bob_post.status_code == 404
    assert bob_notif.status_code == 404
    assert bob_get.status_code == 404
    assert bob_delete.status_code == 404
    assert alice_post.status_code == 200


@pytest.mark.asyncio
async def test_mcp_session_store_concurrent_mutations_and_fallback_resolution() -> None:
    store = MCPSessionStore(MemoryStore(), ttl_seconds=60.0)
    created = await store.create(protocol_version="2025-11-25", owner_id="user:alice")
    sid = created.session_id

    await asyncio.gather(
        store.add_subscription(sid, "user:alice", "a://1"),
        store.add_subscription(sid, "user:alice", "b://2"),
        store.add_subscription(sid, "user:alice", "a://1"),
        store.set_log_level(sid, "user:alice", "warning"),
    )

    loaded = await store.get(sid, "user:alice")
    assert loaded is not None
    assert sorted(loaded.subscriptions) == ["a://1", "b://2"]
    assert loaded.log_level == "warning"
    assert await store.get(sid, "user:bob") is None
    assert await store.get("dead\nid", "user:alice") is None

    updated = await store.remove_subscription(sid, "user:alice", "a://1")
    assert updated is not None
    assert updated.subscriptions == ["b://2"]

    fallback_app = Litestar(
        route_handlers=[],
        stores=StoreRegistry(stores={"unrelated": MemoryStore()}, default_factory=cast("Any", None)),
    )
    first_store = resolve_session_store(fallback_app)
    second_store = resolve_session_store(fallback_app)
    assert isinstance(first_store, MemoryStore)
    assert first_store is second_store
    assert fallback_app.state._mcp_session_store is first_store


@pytest.mark.asyncio
async def test_custom_session_store_persists_encoded_session_with_ttl() -> None:
    custom_store = MemoryStore()
    app = Litestar(
        route_handlers=[],
        stores={SESSION_STORE_NAME: custom_store},
        plugins=[LitestarMCP(MCPConfig(session_ttl_seconds=42.2))],
    )
    async with AsyncTestClient(app=app) as client:
        init_resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            },
        )
        sid = init_resp.headers["mcp-session-id"]

        raw = await custom_store.get(f"mcp-session:{sid}")
        ttl = await custom_store.expires_in(f"mcp-session:{sid}")

    assert raw is not None
    decoded = from_json(raw)
    assert decoded["session_id"] == sid
    assert decoded["protocol_version"] == "2025-11-25"
    assert isinstance(MCPSession(**decoded), MCPSession)
    assert ttl is not None
    assert 0 < ttl <= 43


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("accept_header", "extra_headers", "enable_get_sse", "expected_status"),
    [
        ("*/*", {}, True, 405),
        ("application/json", {}, True, 406),
        ("text/event-stream;q=0", {}, True, 406),
        ("text/event-stream", {}, False, 405),
        ("text/event-stream", {"MCP-Protocol-Version": "2026-07-28"}, True, 405),
        ("text/event-stream", {"Origin": "https://attacker.example"}, True, 403),
        ("text/event-stream", {"Mcp-Session-Id": "unknown-id"}, True, 404),
        ("text/event-stream", {"MCP-Protocol-Version": "2099-01-01"}, True, 400),
    ],
)
async def test_get_mcp_negotiation_origin_and_session_errors(
    accept_header: str,
    extra_headers: dict[str, str],
    enable_get_sse: bool,
    expected_status: int,
) -> None:
    app = _app(MCPConfig(enable_get_sse=enable_get_sse))
    headers = {"Accept": accept_header, **extra_headers}
    async with AsyncTestClient(app=app) as client:
        response = await client.get("/mcp", headers=headers)

    assert response.status_code == expected_status
    if expected_status == 405:
        allow = response.headers.get("allow", "")
        assert "POST" in allow and "DELETE" in allow
    if expected_status == 400:
        assert response.json()["error"]["code"] == -32022


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected_version"),
    [
        ("explicit_header", "2025-06-18"),
        ("session_fallback", "2025-06-18"),
        ("headerless_sessionless", "2025-11-25"),
    ],
)
async def test_get_mcp_resolved_protocol_version_header(mode: str, expected_version: str) -> None:
    app = _app()
    async with (
        app.lifespan(),
        httpx2.AsyncClient(transport=ASGIStreamingTransport(app), base_url="http://testserver.local") as client,
    ):
        headers: dict[str, str] = {"Accept": "text/event-stream, application/json"}
        if mode == "explicit_header":
            headers["MCP-Protocol-Version"] = "2025-06-18"
        elif mode == "session_fallback":
            init_resp = await client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2025-06-18", "capabilities": {}},
                },
            )
            headers["Mcp-Session-Id"] = init_resp.headers["mcp-session-id"]

        async with client.stream("GET", "/mcp", headers=headers) as response:
            assert response.status_code == 200
            assert response.headers["mcp-protocol-version"] == expected_version
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["x-accel-buffering"] == "no"


@pytest.mark.asyncio
async def test_get_mcp_returns_503_when_stream_limit_exceeded() -> None:
    app = _app(MCPConfig(subscription_max_streams=1))
    async with (
        app.lifespan(),
        httpx2.AsyncClient(transport=ASGIStreamingTransport(app), base_url="http://testserver.local") as client,
        client.stream("GET", "/mcp", headers={"Accept": "text/event-stream"}) as first,
    ):
        assert first.status_code == 200
        second = await client.get("/mcp", headers={"Accept": "text/event-stream"})
        assert second.status_code == 503


@pytest.mark.asyncio
async def test_get_mcp_enforces_configured_guards() -> None:
    def deny_guard(_connection: "ASGIConnection[Any, Any, Any, Any]", _handler: "BaseRouteHandler") -> None:
        msg = "forbidden"
        raise PermissionDeniedException(msg)

    app = _app(MCPConfig(guards=[deny_guard]))
    async with AsyncTestClient(app=app) as client:
        response = await client.get("/mcp", headers={"Accept": "text/event-stream"})

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("resource_subs", "authorizer_mode"),
    [
        ("doc://secret", "none"),
        ([123], "none"),
        ([""], "none"),
        (["denied://x"], "sync"),
        (["denied://y"], "async"),
    ],
)
def test_post_subscriptions_listen_rejects_invalid_or_unauthorized_resources(
    resource_subs: Any,
    authorizer_mode: str,
) -> None:
    authorizer: Callable[[Request[Any, Any, Any], str], Awaitable[bool] | bool] | None = None
    if authorizer_mode == "sync":

        def sync_auth(_req: "Request[Any, Any, Any]", uri: str) -> bool:
            return uri.startswith("allowed://")

        authorizer = sync_auth
    elif authorizer_mode == "async":

        async def async_auth(_req: "Request[Any, Any, Any]", uri: str) -> bool:
            return uri.startswith("allowed://")

        authorizer = async_auth

    app = _app(MCPConfig(subscription_authorizer=authorizer))
    with TestClient(app=app) as client:
        response = _request(
            client,
            "subscriptions/listen",
            params={"notifications": {"resourceSubscriptions": resource_subs}},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == -32602


@pytest.mark.asyncio
async def test_post_subscriptions_listen_accepts_authorized_resources() -> None:
    async def async_auth(_req: "Request[Any, Any, Any]", uri: str) -> bool:
        return uri.startswith("allowed://")

    app = _app(MCPConfig(subscription_authorizer=async_auth))
    async with (
        app.lifespan(),
        httpx2.AsyncClient(transport=ASGIStreamingTransport(app), base_url="http://testserver.local") as client,
    ):
        body, headers = mcp_envelope(
            "subscriptions/listen",
            {"notifications": {"resourceSubscriptions": ["allowed://doc"]}},
            msg_id="sub-auth",
        )
        async with client.stream("POST", "/mcp", json=body, headers=headers) as response:
            assert response.status_code == 200
            data_line = ""
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data_line = line
                    break

    payload = from_json(data_line.removeprefix("data: "))
    assert payload["method"] == "notifications/subscriptions/acknowledged"
    assert payload["params"]["notifications"] == {"resourceSubscriptions": ["allowed://doc"]}


@pytest.mark.asyncio
async def test_get_mcp_session_sse_delivers_events_strips_2026_meta_and_closes_on_delete() -> None:
    app = _app()
    plugin = app.plugins.get(LitestarMCP)
    async with (
        app.lifespan(),
        httpx2.AsyncClient(transport=ASGIStreamingTransport(app), base_url="http://testserver.local") as client,
    ):
        init_resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            },
        )
        sid = init_resp.headers["mcp-session-id"]
        await get_session_store(app, plugin.config).add_subscription(sid, None, "doc://1")

        async with client.stream(
            "GET",
            "/mcp",
            headers={"Accept": "text/event-stream", "Mcp-Session-Id": sid},
        ) as sse_resp:
            assert sse_resp.status_code == 200
            assert sse_resp.headers["mcp-protocol-version"] == "2025-11-25"
            assert "mcp-session-id" not in sse_resp.headers

            lines = sse_resp.aiter_lines()
            await plugin.registry.notify_tools_list_changed()
            await plugin.registry.subscription_manager.publish(
                "notifications/resources/updated",
                {
                    "uri": "doc://1",
                    "_meta": {"io.modelcontextprotocol/subscriptionId": "2026-leak", "trace": "ok"},
                },
            )

            first_event = await _next_sse_payload(lines)
            second_event = await _next_sse_payload(lines)

            assert first_event["method"] == "notifications/tools/list_changed"
            assert "_meta" not in first_event["params"]
            assert second_event["method"] == "notifications/resources/updated"
            assert second_event["params"]["uri"] == "doc://1"
            assert second_event["params"]["_meta"] == {"trace": "ok"}

            del_resp = await client.delete("/mcp", headers={"Mcp-Session-Id": sid})
            assert del_resp.status_code == 204
            assert del_resp.headers["mcp-protocol-version"] == "2025-11-25"

            remaining = await _drain_remaining_sse_payloads(lines)
            assert remaining == []


@pytest.mark.parametrize(("logging_enabled", "expect_logging"), [(True, True), (False, False)])
def test_capabilities_logging_toggle(logging_enabled: bool, expect_logging: bool) -> None:
    app = _app(MCPConfig(logging=logging_enabled))
    with TestClient(app=app) as client:
        discover_resp = _request(client, "server/discover")
        init_resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            },
        )

    assert discover_resp.status_code == 200
    assert init_resp.status_code == 200
    discover_caps = discover_resp.json()["result"]["capabilities"]
    init_caps = init_resp.json()["result"]["capabilities"]
    if expect_logging:
        assert discover_caps["logging"] == {}
        assert init_caps["logging"] == {}
    else:
        assert "logging" not in discover_caps
        assert "logging" not in init_caps


@pytest.mark.parametrize(
    "params",
    [
        {"level": "debug"},
        {"level": "emergency"},
        {},
    ],
)
def test_logging_set_level_rejected_in_modern_2026_mode(params: dict[str, Any]) -> None:
    app = _app(MCPConfig(default_log_level="info"))
    with TestClient(app=app) as client:
        resp = _request(client, "logging/setLevel", params=dict(params))

    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == -32601


@pytest.mark.parametrize(
    ("params", "expected_status", "expected_code"),
    [
        ({"level": "debug"}, 200, None),
        ({"level": "emergency"}, 200, None),
        ({"level": "warn"}, 400, -32602),
        ({"level": None}, 400, -32602),
        ({"level": ""}, 400, -32602),
        ({"level": 3}, 400, -32602),
        ({"level": "WARN"}, 400, -32602),
        ({"level": "trace"}, 400, -32602),
        ({}, 400, -32602),
    ],
)
def test_logging_set_level_rpc_validation_legacy_session(
    params: dict[str, Any],
    expected_status: int,
    expected_code: int | None,
) -> None:
    app = _app(MCPConfig(default_log_level="info"))
    plugin = app.plugins.get(LitestarMCP)
    with TestClient(app=app) as client:
        init_resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            },
        )
        sid = init_resp.headers["mcp-session-id"]
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "logging/setLevel", "params": dict(params)},
            headers={"MCP-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
        )

    assert resp.status_code == expected_status
    assert plugin.config.default_log_level == "info"
    body = resp.json()
    if expected_code is None:
        assert body["result"]["resultType"] == "complete"
    else:
        assert body["error"]["code"] == expected_code


@pytest.mark.asyncio
async def test_logging_set_level_disabled_and_expired_session_errors() -> None:
    disabled_app = _app(MCPConfig(logging=False))
    async with AsyncTestClient(app=disabled_app) as client:
        body_2026, headers_2026 = mcp_envelope("logging/setLevel", {"level": "error"})
        modern_resp = await client.post("/mcp", json=body_2026, headers=headers_2026)
        init_resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25", "capabilities": {}},
            },
        )
        sid = init_resp.headers["mcp-session-id"]
        legacy_resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "logging/setLevel", "params": {"level": "error"}},
            headers={"MCP-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
        )
        expired_http_resp = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "logging/setLevel", "params": {"level": "error"}},
            headers={"MCP-Protocol-Version": "2025-11-25", "Mcp-Session-Id": "missing-session"},
        )

    assert modern_resp.status_code == 404
    assert modern_resp.json()["error"]["code"] == -32601
    assert legacy_resp.status_code == 404
    assert legacy_resp.json()["error"]["code"] == -32601
    assert expired_http_resp.status_code == 404

    active_app = _app()
    plugin = active_app.plugins.get(LitestarMCP)
    service = MCPHandlerService(
        config=plugin.config,
        discovered_tools={},
        discovered_resources={},
        discovered_prompts={},
        app_ref=active_app,
        registry=plugin.registry,
    )
    with pytest.raises(JSONRPCErrorException, match="Session not found or expired") as exc_info:
        await service.logging_set_level(
            {"level": "error"},
            MCPRequestContext(client_id="unit", owner_id=None, session_id="expired-session"),
        )
    assert exc_info.value.error.code == INVALID_PARAMS


@pytest.mark.parametrize(
    "bad_meta_level",
    ["bogus", "", 123, True, None, ["debug"], {"level": "info"}],
)
def test_invalid_meta_log_level_returns_400_before_tool_execution(bad_meta_level: Any) -> None:
    calls: list[str] = []

    @get("/tracked", mcp_tool="tracked", sync_to_thread=False)
    def tracked() -> dict[str, str]:
        calls.append("ran")
        return {"ok": "yes"}

    app = Litestar(route_handlers=[tracked], plugins=[LitestarMCP()])
    with TestClient(app=app) as client:
        resp = _request(
            client,
            "tools/call",
            params={
                "name": "tracked",
                "arguments": {},
                "_meta": {"io.modelcontextprotocol/logLevel": bad_meta_level},
            },
        )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32602
    assert calls == []


@pytest.mark.parametrize(
    "bad_filter",
    [
        {"logLevel": "bogus"},
        {"logLevel": ["debug"]},
        {"logging": None},
        {"logging": "debug"},
        {"logging": {"level": "fatal"}},
    ],
)
def test_post_subscriptions_listen_rejects_invalid_logging_filters(bad_filter: dict[str, Any]) -> None:
    with TestClient(app=_app()) as client:
        resp = _request(client, "subscriptions/listen", params={"notifications": bad_filter})

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == -32602


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("meta_level", "expected_post_levels", "expected_listen_levels"),
    [
        ("warning", ["error"], ["error"]),
        (None, ["info", "error"], ["debug", "info", "error"]),
    ],
)
async def test_meta_log_level_2026_and_post_sse_gating(
    meta_level: str | None,
    expected_post_levels: list[str],
    expected_listen_levels: list[str],
) -> None:
    @get("/emit-logs", mcp_tool="emit_logs")
    async def emit_logs() -> dict[str, bool]:
        ctx = get_mcp_request_context()
        await ctx.log("debug", "dbg-msg")
        await ctx.log("info", "info-msg")
        await ctx.log("error", {"code": 500}, logger="worker")
        return {"done": True}

    app = Litestar(route_handlers=[emit_logs], plugins=[LitestarMCP(MCPConfig(default_log_level="info"))])
    plugin = app.plugins.get(LitestarMCP)
    dbg_body, dbg_headers = mcp_envelope(
        "subscriptions/listen",
        {"notifications": {"logLevel": "debug"}},
        msg_id="sub-dbg",
    )
    tools_body, tools_headers = mcp_envelope(
        "subscriptions/listen",
        {"notifications": {"toolsListChanged": True}},
        msg_id="sub-tools",
    )
    call_meta: dict[str, Any] = {"progressToken": "p1"}
    if meta_level is not None:
        call_meta["io.modelcontextprotocol/logLevel"] = meta_level
    call_body, call_headers = mcp_envelope(
        "tools/call",
        {"name": "emit_logs", "arguments": {}, "_meta": call_meta},
        msg_id="call-1",
    )

    async with (
        app.lifespan(),
        httpx2.AsyncClient(transport=ASGIStreamingTransport(app), base_url="http://testserver.local") as client,
        client.stream("POST", "/mcp", json=dbg_body, headers=dbg_headers) as dbg_resp,
        client.stream("POST", "/mcp", json=tools_body, headers=tools_headers) as tools_resp,
    ):
        assert dbg_resp.status_code == 200
        assert tools_resp.status_code == 200
        dbg_lines = dbg_resp.aiter_lines()
        tools_lines = tools_resp.aiter_lines()
        dbg_ack = await _next_sse_payload(dbg_lines)
        tools_ack = await _next_sse_payload(tools_lines)
        assert dbg_ack["params"]["notifications"] == {"logLevel": "debug"}
        assert tools_ack["params"]["notifications"] == {"toolsListChanged": True}

        async with client.stream("POST", "/mcp", json=call_body, headers=call_headers) as call_resp:
            assert call_resp.status_code == 200
            post_frames = await _drain_remaining_sse_payloads(call_resp.aiter_lines())

        listen_logs = [await _next_sse_payload(dbg_lines) for _ in expected_listen_levels]
        await plugin.registry.notify_tools_list_changed()
        tools_event = await _next_sse_payload(tools_lines)

    post_logs = [frame for frame in post_frames if frame.get("method") == "notifications/message"]
    assert [frame["params"]["level"] for frame in post_logs] == expected_post_levels
    assert post_logs[-1]["params"] == {"level": "error", "data": {"code": 500}, "logger": "worker"}
    assert post_frames[-1]["id"] == "call-1"
    assert post_frames[-1]["result"]["isError"] is False
    assert [frame["params"]["level"] for frame in listen_logs] == expected_listen_levels
    assert listen_logs[-1]["params"]["logger"] == "worker"
    assert listen_logs[-1]["params"]["_meta"] == {"io.modelcontextprotocol/subscriptionId": "sub-dbg"}
    assert tools_event["method"] == "notifications/tools/list_changed"


@pytest.mark.asyncio
async def test_session_set_level_store_sync_and_get_sse() -> None:
    @get("/log-progress", mcp_tool="log_progress")
    async def log_progress() -> dict[str, str]:
        ctx = get_mcp_request_context()
        await ctx.report_progress(1, 2, "step")
        await ctx.log("debug", "drop-debug")
        await ctx.log("info", "drop-info")
        await ctx.log("critical", {"alert": True}, logger="audit")
        return {"status": "logged"}

    app = Litestar(route_handlers=[log_progress], plugins=[LitestarMCP(MCPConfig(default_log_level="info"))])
    plugin = app.plugins.get(LitestarMCP)
    received: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async with (
        app.lifespan(),
        MCPStreamableHTTPClient(
            "http://testserver.local/mcp",
            transport=ASGIStreamingTransport(app),
            protocol_version="2025-11-25",
        ) as mcp_client,
        httpx2.AsyncClient(transport=ASGIStreamingTransport(app), base_url="http://testserver.local") as http_client,
    ):
        await mcp_client.initialize()
        sid = mcp_client.session_id
        assert sid is not None
        set_resp = await mcp_client.set_logging_level("warning")
        assert set_resp["resultType"] == "complete"

        stored = await get_session_store(app, plugin.config).get(sid, None)
        assert stored is not None and stored.log_level == "warning"

        plugin.registry.subscription_manager.clear_session_log_level(sid)
        assert plugin.registry.subscription_manager.get_session_log_level(sid) == "info"

        async def _consume() -> None:
            async for frame in mcp_client.open_event_stream():
                await received.put(frame)

        stream_task = asyncio.create_task(_consume())
        try:
            await asyncio.wait_for(_wait_for_active_stream(plugin), timeout=2.0)
            assert plugin.registry.subscription_manager.get_session_log_level(sid) == "warning"

            plugin.registry.subscription_manager.clear_session_log_level(sid)
            async with http_client.stream(
                "POST",
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": "sess-call",
                    "method": "tools/call",
                    "params": {"name": "log_progress", "arguments": {}, "_meta": {"progressToken": "p-sess"}},
                },
                headers={"MCP-Protocol-Version": "2025-11-25", "Mcp-Session-Id": sid},
            ) as post_resp:
                assert post_resp.status_code == 200
                post_frames = await _drain_remaining_sse_payloads(post_resp.aiter_lines())

            assert plugin.registry.subscription_manager.get_session_log_level(sid) == "warning"
            log_event = await asyncio.wait_for(received.get(), timeout=2.0)
            await plugin.registry.notify_tools_list_changed()
            after_log = await asyncio.wait_for(received.get(), timeout=2.0)
        finally:
            stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stream_task

    assert [frame.get("method") for frame in post_frames[:-1]] == ["notifications/progress"]
    assert post_frames[-1]["id"] == "sess-call"
    assert log_event == {
        "jsonrpc": "2.0",
        "method": "notifications/message",
        "params": {"level": "critical", "data": {"alert": True}, "logger": "audit"},
    }
    assert after_log["method"] == "notifications/tools/list_changed"


def test_bare_request_with_unsupported_protocol_version_returns_32022() -> None:
    """POST /mcp with an unsupported MCP-Protocol-Version header rejects with -32022 even without 2026 _meta."""
    app = _app()
    with TestClient(app=app) as client:
        response = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"MCP-Protocol-Version": "2024-11-05"},
        )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == UNSUPPORTED_PROTOCOL_VERSION


@pytest.mark.anyio
async def test_raising_subscription_authorizer_fails_closed() -> None:
    """Raising subscription_authorizer fails closed with INVALID_PARAMS on subscriptions/listen and resources/subscribe."""

    def _exploding_authorizer(_request: Any, _uri: str) -> bool:
        msg = "authorizer exploded"
        raise RuntimeError(msg)

    @get("/items", mcp_resource="items_res", sync_to_thread=False)
    def items_res() -> dict[str, str]:
        return {"ok": "yes"}

    config = MCPConfig(subscription_authorizer=_exploding_authorizer)
    app = Litestar(route_handlers=[items_res], plugins=[LitestarMCP(config)])

    async with AsyncTestClient(app=app) as client:
        modern_payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "subscriptions/listen",
            "params": {
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": MCP_PROTOCOL_VERSION,
                    "io.modelcontextprotocol/clientCapabilities": {},
                    "io.modelcontextprotocol/clientInfo": {"name": "client", "version": "1.0"},
                },
                "notifications": {"resourceSubscriptions": ["litestar://items_res"]},
            },
        }
        listen_resp = await client.post(
            "/mcp",
            json=modern_payload,
            headers={
                "Accept": "application/json, text/event-stream",
                MCP_PROTOCOL_VERSION_HEADER: MCP_PROTOCOL_VERSION,
                MCP_METHOD_HEADER: "subscriptions/listen",
            },
        )
        assert listen_resp.status_code == 400
        assert listen_resp.json()["error"]["code"] == -32602

        init_resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": "c", "version": "1"},
                },
            },
            headers={"MCP-Protocol-Version": "2025-11-25"},
        )
        assert init_resp.status_code == 200
        session_id = init_resp.headers["Mcp-Session-Id"]

        sub_resp = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "resources/subscribe",
                "params": {"uri": "litestar://items_res"},
            },
            headers={"MCP-Protocol-Version": "2025-11-25", "Mcp-Session-Id": session_id},
        )
        assert sub_resp.status_code == 400
        assert sub_resp.json()["error"]["code"] == -32602
