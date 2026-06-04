"""Pagination coverage for MCP list methods."""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP, MCPConfig, mcp_prompt

pytestmark = pytest.mark.unit


def _ensure_session(client: TestClient[Any]) -> str:
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
    return str(sid)


def _rpc(
    client: TestClient[Any],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    sid: str,
) -> dict[str, Any]:
    response = client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        headers={"Mcp-Session-Id": sid},
    )
    data: dict[str, Any] = response.json()
    return data


def _make_tool(index: int) -> Any:
    @get(f"/tools/{index}", mcp_tool=f"tool_{index:03}", sync_to_thread=False)
    def handler() -> dict[str, int]:
        return {"index": index}

    return handler


def _make_resource(index: int) -> Any:
    @get(f"/resources/{index}", mcp_resource=f"resource_{index:03}", sync_to_thread=False)
    def handler() -> dict[str, int]:
        return {"index": index}

    return handler


def _make_template(index: int) -> Any:
    @get(
        f"/templates/{index}/{{item_id:str}}",
        mcp_resource=f"template_{index:03}",
        mcp_resource_template=f"app://templates/{index}/{{item_id}}",
        sync_to_thread=False,
    )
    def handler(item_id: str) -> dict[str, str]:
        return {"item_id": item_id}

    return handler


def _make_prompt(index: int) -> Any:
    def prompt() -> str:
        return str(index)

    prompt.__name__ = f"prompt_{index:03}"
    prompt.__qualname__ = f"prompt_{index:03}"
    return mcp_prompt(name=f"prompt_{index:03}")(prompt)


def test_tools_list_paginates_with_opaque_cursor() -> None:
    app = Litestar(route_handlers=[_make_tool(i) for i in range(105)], plugins=[LitestarMCP(MCPConfig())])
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        first = _rpc(client, "tools/list", sid=sid)
        tools = first["result"]["tools"]
        assert [tool["name"] for tool in tools[:3]] == ["tool_000", "tool_001", "tool_002"]
        assert len(tools) == 100
        assert "nextCursor" in first["result"]

        second = _rpc(client, "tools/list", {"cursor": first["result"]["nextCursor"]}, sid=sid)
        assert [tool["name"] for tool in second["result"]["tools"]] == [
            "tool_100",
            "tool_101",
            "tool_102",
            "tool_103",
            "tool_104",
        ]
        assert "nextCursor" not in second["result"]


def test_resources_list_paginates_and_keeps_openapi_first() -> None:
    app = Litestar(route_handlers=[_make_resource(i) for i in range(105)], plugins=[LitestarMCP(MCPConfig())])
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        first = _rpc(client, "resources/list", sid=sid)
        resources = first["result"]["resources"]
        assert resources[0]["uri"] == "litestar://openapi"
        assert resources[1]["name"] == "resource_000"
        assert len(resources) == 100

        second = _rpc(client, "resources/list", {"cursor": first["result"]["nextCursor"]}, sid=sid)
        assert [resource["name"] for resource in second["result"]["resources"]] == [
            "resource_099",
            "resource_100",
            "resource_101",
            "resource_102",
            "resource_103",
            "resource_104",
        ]
        assert "nextCursor" not in second["result"]


def test_resources_templates_list_paginates() -> None:
    app = Litestar(route_handlers=[_make_template(i) for i in range(105)], plugins=[LitestarMCP(MCPConfig())])
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        first = _rpc(client, "resources/templates/list", sid=sid)
        templates = first["result"]["resourceTemplates"]
        assert templates[0]["uriTemplate"] == "app://templates/0/{item_id}"
        assert len(templates) == 100

        second = _rpc(client, "resources/templates/list", {"cursor": first["result"]["nextCursor"]}, sid=sid)
        assert [template["name"] for template in second["result"]["resourceTemplates"]] == [
            "template_100",
            "template_101",
            "template_102",
            "template_103",
            "template_104",
        ]
        assert "nextCursor" not in second["result"]


def test_prompts_list_paginates() -> None:
    app = Litestar(route_handlers=[], plugins=[LitestarMCP(MCPConfig(), prompts=[_make_prompt(i) for i in range(105)])])
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        first = _rpc(client, "prompts/list", sid=sid)
        prompts = first["result"]["prompts"]
        assert [prompt["name"] for prompt in prompts[:3]] == ["prompt_000", "prompt_001", "prompt_002"]
        assert len(prompts) == 100

        second = _rpc(client, "prompts/list", {"cursor": first["result"]["nextCursor"]}, sid=sid)
        assert [prompt["name"] for prompt in second["result"]["prompts"]] == [
            "prompt_100",
            "prompt_101",
            "prompt_102",
            "prompt_103",
            "prompt_104",
        ]
        assert "nextCursor" not in second["result"]


@pytest.mark.parametrize("method", ["tools/list", "resources/list", "resources/templates/list", "prompts/list"])
def test_list_methods_reject_invalid_cursor(method: str) -> None:
    app = Litestar(
        route_handlers=[_make_tool(0), _make_resource(0), _make_template(0)], plugins=[LitestarMCP(MCPConfig())]
    )
    with TestClient(app=app) as client:
        sid = _ensure_session(client)
        response = _rpc(client, method, {"cursor": "not-a-valid-cursor"}, sid=sid)
        assert response["error"]["code"] == -32602
        assert response["error"]["message"] == "Invalid cursor"
