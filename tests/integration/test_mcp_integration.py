"""End-to-end JSON-RPC roundtrip verifying rename fidelity and conformance features."""

import asyncio
import json
import os
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

import httpx2
import pytest
from litestar import Litestar, post
from litestar.testing import TestClient
from msgspec import Struct

from litestar_mcp import LitestarMCP, MCPConfig, MCPSkillsConfig
from litestar_mcp.mcp.client import MCPStreamableHTTPClient
from litestar_mcp.mcp.routes import MCP_SESSION_ID_HEADER
from litestar_mcp.mcp.stdio import ASGIStreamingTransport
from tests.conformance import server as conformance_server
from tests.integration.conftest import rpc

pytestmark = pytest.mark.integration


class _CamelOut(Struct, rename="camel"):
    first_name: "str" = ""
    last_login_count: "int" = 0


def _build_app() -> "Litestar":
    @post("/greet", mcp_tool="greet", sync_to_thread=False)
    def greet() -> "_CamelOut":
        return _CamelOut(first_name="Alice", last_login_count=3)

    return Litestar(route_handlers=[greet], plugins=[LitestarMCP()])


def test_mcp_tool_emits_camel_case_for_renamed_struct() -> "None":
    """``tools/call`` must emit camelCase JSON-RPC body for renamed Structs."""
    app = _build_app()
    with TestClient(app=app) as client:
        response = rpc(client, "tools/call", {"name": "greet", "arguments": {}})

    result: dict[str, Any] = response["result"]
    assert result["isError"] is False
    payload = json.loads(result["content"][0]["text"])
    assert payload == {"firstName": "Alice", "lastLoginCount": 3}


@pytest.fixture
def anyio_backend() -> "str":
    """Pin async integration tests to the asyncio backend."""
    return "asyncio"


async def _collect_sse_events(
    client: MCPStreamableHTTPClient,
    events: list[dict[str, Any]],
) -> None:
    """Drain Streamable HTTP ``GET`` SSE notifications into ``events``."""
    async for event in client.open_event_stream():
        events.extend((event,))


async def _wait_until(predicate: Callable[[], bool], *, max_wait: float = 5.0) -> None:
    """Poll ``predicate`` on the event loop until truthy or max wait elapses."""
    steps = max(1, int(max_wait / 0.01))
    for _ in range(steps):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise asyncio.TimeoutError


async def _verify_interop_session_features(
    client: MCPStreamableHTTPClient,
    events: list[dict[str, Any]],
) -> None:
    """Exercise resource subscriptions, log filtering, background tasks, UI widgets, and structured output."""
    await _wait_until(lambda: conformance_server.plugin.registry.subscription_manager.active_stream_count > 0)

    await client.subscribe_resource("ui://conformance/widget.html")
    await conformance_server.plugin._registry.notify_resource_updated("ui://conformance/widget.html")
    await _wait_until(
        lambda: any(
            ev.get("method") == "notifications/resources/updated"
            and ev.get("params", {}).get("uri") == "ui://conformance/widget.html"
            for ev in events
        )
    )
    resource_events = [ev for ev in events if ev.get("method") == "notifications/resources/updated"]
    assert "io.modelcontextprotocol/subscriptionId" not in resource_events[-1]["params"].get("_meta", {})

    await client.set_logging_level("warning")
    log_output = await client.call_tool("test_logging_tool")
    assert log_output == "Emitted info and error log notifications."
    await _wait_until(
        lambda: any(
            ev.get("method") == "notifications/message"
            and ev.get("params", {}).get("level") == "error"
            and ev.get("params", {}).get("data") == "Conformance error log"
            for ev in events
        )
    )
    assert not any(
        ev.get("method") == "notifications/message" and ev.get("params", {}).get("data") == "Conformance info log"
        for ev in events
    )

    await client.set_logging_level("info")
    task_call = await client.request(
        "tools/call",
        {
            "name": "interop_task_job",
            "arguments": {"label": "alpha"},
            "_meta": {"progressToken": "interop-tok-1"},
        },
    )
    assert task_call.get("resultType") == "task"
    task_id = task_call["taskId"]

    task_status: dict[str, Any] = {}
    for _ in range(250):
        task_status = await client.request("tasks/get", {"taskId": task_id})
        if task_status.get("status") == "completed":
            break
        await asyncio.sleep(0.02)

    assert json.loads(task_status["result"]["content"][0]["text"]) == {
        "status": "ok",
        "label": "alpha",
    }
    await _wait_until(
        lambda: (
            {
                ev.get("params", {}).get("progress")
                for ev in events
                if ev.get("method") == "notifications/progress"
                and ev.get("params", {}).get("progressToken") == "interop-tok-1"
            }
            >= {1, 2}
            and any(
                ev.get("method") == "notifications/message"
                and ev.get("params", {}).get("data") == "Interop task finished: alpha"
                for ev in events
            )
        )
    )

    widget_read = await client.request("resources/read", {"uri": "ui://conformance/widget.html"})
    widget_item = widget_read["contents"][0]
    assert widget_item["text"] == '<main data-widget="conformance">Widget ready</main>'
    assert widget_item["mimeType"] == "text/html;profile=mcp-app"
    assert widget_item["_meta"] == {"ui": {"prefersBorder": True}}

    quote = await client.call_tool("test_structured_quote", {"cents": 1250})
    assert quote == {"amount": "$12.50", "currency": "USD"}


@pytest.mark.anyio
async def test_interop_2025_11_25_sse_logging_task_and_session_delete() -> None:
    """Verify full 2025-11-25 session SSE, logging filter, task progress, UI widget, quote, and 404 delete."""
    port = os.getenv("MCP_CONFORMANCE_PORT", "18765")
    endpoint = f"http://localhost:{port}/mcp"

    async with conformance_server.app.lifespan():
        client = MCPStreamableHTTPClient(
            endpoint,
            transport=ASGIStreamingTransport(conformance_server.app),
            protocol_version="2025-11-25",
            capabilities={"extensions": {"io.modelcontextprotocol/tasks": {}}},
        )
        async with client:
            init_result = await client.initialize()
            assert isinstance(client.session_id, str)
            assert client.session_id
            capabilities = init_result["capabilities"]
            assert capabilities["logging"] == {}
            assert "io.modelcontextprotocol/ui" in capabilities["extensions"]

            events: list[dict[str, Any]] = []
            stream_task = asyncio.create_task(_collect_sse_events(client, events))
            try:
                await _verify_interop_session_features(client, events)
            finally:
                stream_task.cancel()
                with suppress(asyncio.CancelledError):
                    await stream_task

            terminated_session_id = client.session_id
            assert terminated_session_id is not None
            await client.terminate_session()
            assert cast("Any", client).session_id is None

            async with httpx2.AsyncClient(transport=ASGIStreamingTransport(conformance_server.app)) as raw_client:
                stale_headers = {
                    MCP_SESSION_ID_HEADER: terminated_session_id,
                    "MCP-Protocol-Version": "2025-11-25",
                    "Accept": "application/json, text/event-stream",
                }
                post_response = await raw_client.post(
                    endpoint,
                    json={"jsonrpc": "2.0", "id": "post-delete", "method": "tools/list", "params": {}},
                    headers=stale_headers,
                )
                get_response = await raw_client.get(endpoint, headers=stale_headers)
                assert post_response.status_code == 404
                assert get_response.status_code == 404


@pytest.mark.anyio
async def test_interop_2026_07_28_stateless_parity() -> None:
    """Verify 2026-07-28 stateless discovery, outputSchema, custom quote encoding, and UI widget parity."""
    port = os.getenv("MCP_CONFORMANCE_PORT", "18765")
    endpoint = f"http://localhost:{port}/mcp"

    async with conformance_server.app.lifespan():
        client = MCPStreamableHTTPClient(
            endpoint,
            transport=ASGIStreamingTransport(conformance_server.app),
            protocol_version="2026-07-28",
        )
        async with client:
            discover = await client.initialize()
            assert discover["supportedVersions"] == ["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"]
            assert client.session_id is None

            tools = await client.list_tools()
            tools_by_name = {tool["name"]: tool for tool in tools}
            assert tools_by_name["test_structured_quote"]["outputSchema"]["type"] == "object"

            quote = await client.call_tool("test_structured_quote", {"cents": 1995})
            assert quote == {"amount": "$19.95", "currency": "USD"}

            widget_read = await client.request("resources/read", {"uri": "ui://conformance/widget.html"})
            widget_item = widget_read["contents"][0]
            assert widget_item["text"] == '<main data-widget="conformance">Widget ready</main>'
            assert widget_item["mimeType"] == "text/html;profile=mcp-app"
            assert widget_item["_meta"] == {"ui": {"prefersBorder": True}}


@pytest.mark.anyio
@pytest.mark.parametrize("protocol_version", ["2025-11-25", "2026-07-28"])
async def test_interop_skills_prompts_client(tmp_path: Path, protocol_version: str) -> None:
    """Verify skills, skill resources, prompt projection, and 2025-11-25 list_changed SSE notifications."""
    skill_dir = tmp_path / "demo-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: demo-skill\n"
        "description: Demo skill for dual-mode interop\n"
        "tags:\n"
        "  - demo\n"
        "---\n"
        "# Demo Skill\n\n"
        "Use the demo skill steps cleanly.\n",
        encoding="utf-8",
    )

    plugin = LitestarMCP(
        MCPConfig(
            skills=MCPSkillsConfig(
                paths=[tmp_path],
                expose_as_prompts=True,
                list_changed=True,
            )
        )
    )
    app = Litestar(plugins=[plugin])

    async with app.lifespan():
        client = MCPStreamableHTTPClient(
            "http://testserver.local/mcp",
            transport=ASGIStreamingTransport(app),
            protocol_version=protocol_version,
        )
        async with client:
            await client.initialize()

            if protocol_version == "2025-11-25":
                events: list[dict[str, Any]] = []
                stream_task = asyncio.create_task(_collect_sse_events(client, events))
                try:
                    await _wait_until(lambda: plugin.registry.subscription_manager.active_stream_count > 0)
                    assert plugin.skill_catalog is not None
                    await plugin.skill_catalog.notify_list_changed()
                    await _wait_until(
                        lambda: (
                            {"notifications/skills/list_changed", "notifications/resources/list_changed"}
                            <= {ev.get("method") for ev in events}
                        )
                    )
                finally:
                    stream_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await stream_task

            skills = await client.list_skills(tags=["demo"])
            assert [item["frontmatter"]["name"] for item in skills] == ["demo-skill"]

            skill_manifest = await client.get_skill("skill://demo-skill/SKILL.md")
            assert skill_manifest["frontmatter"]["name"] == "demo-skill"
            assert skill_manifest["uri"] == "skill://demo-skill/SKILL.md"

            directory_entries = await client.read_skill_directory("skill://demo-skill")
            assert any(entry.get("uri") == "skill://demo-skill/SKILL.md" for entry in directory_entries)

            prompt_result = await client.request("prompts/get", {"name": "demo-skill"})
            assert prompt_result["messages"]
            assert any("Demo Skill" in str(message) for message in prompt_result["messages"])
