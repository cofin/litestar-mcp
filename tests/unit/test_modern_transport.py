"""MCP 2026-07-28 stateless Streamable HTTP contract tests."""

from typing import Annotated, Any

import pytest
from litestar import Litestar, get
from litestar.params import Parameter
from litestar.testing import TestClient

from litestar_mcp import SUPPORTED_PROTOCOL_VERSIONS, LitestarMCP, MCPConfig, MCPToolResult
from tests.unit.conftest import PROTOCOL_VERSION, mcp_envelope


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
    result = response.json()["result"]
    assert result["resultType"] == "complete"
    assert result["supportedVersions"] == ["2026-07-28", "2025-06-18", "2025-03-26"]
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
    assert body["error"]["data"]["supportedVersions"] == ["2026-07-28", "2025-06-18", "2025-03-26"]


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
        "logging/setLevel",
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


def test_dual_version_initialize_notifications_and_ping_interoperability() -> None:
    assert SUPPORTED_PROTOCOL_VERSIONS == ("2026-07-28", "2025-06-18", "2025-03-26")

    config = MCPConfig(instructions="Follow workspace rules.")
    with TestClient(app=_app(config)) as client:
        for req_version, expected_version in (
            ("2026-07-28", "2026-07-28"),
            ("2025-06-18", "2025-06-18"),
            ("2025-03-26", "2025-03-26"),
            ("2024-11-05", "2025-06-18"),
        ):
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
            init_result = init_resp.json()["result"]
            assert init_result["protocolVersion"] == expected_version
            assert "tools" in init_result["capabilities"]
            assert "resources" in init_result["capabilities"]
            assert "name" in init_result["serverInfo"]
            assert init_result["instructions"] == "Follow workspace rules."

        notif_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"MCP-Protocol-Version": "2025-06-18"},
        )
        assert notif_resp.status_code == 202
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
        assert "result" in ping_resp.json()

        tools_resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
            headers={"MCP-Protocol-Version": "2025-06-18"},
        )
        assert tools_resp.status_code == 200
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
