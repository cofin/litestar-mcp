"""Progress notifications flow on the response stream of the request that supplied the token."""

import json
from typing import Any

import pytest
from litestar import Litestar, get
from litestar.testing import AsyncTestClient

from litestar_mcp import LitestarMCP, get_mcp_request_context
from litestar_mcp.sse import SubscriptionManager

pytestmark = pytest.mark.anyio


@get("/slow", mcp_tool="slow")
async def slow() -> dict[str, Any]:
    context = get_mcp_request_context()
    await context.report_progress(1, total=2, message="half")
    return {"ok": True}


def _events(body: str) -> list[dict[str, Any]]:
    return [json.loads(line[5:]) for line in body.splitlines() if line.startswith("data:")]


async def test_progress_token_switches_the_response_to_a_stream() -> None:
    app = Litestar(route_handlers=[slow], plugins=[LitestarMCP()])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {"name": "slow", "arguments": {}, "_meta": {"progressToken": "tok-1"}},
            },
        )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = _events(response.text)
    assert events[0]["method"] == "notifications/progress"
    assert events[0]["params"] == {"progressToken": "tok-1", "progress": 1, "total": 2, "message": "half"}
    assert events[-1]["id"] == 7
    assert events[-1]["result"]["content"][0]["text"] == '{"ok":true}'


async def test_without_progress_token_the_response_stays_json() -> None:
    @get("/plain", mcp_tool="plain", sync_to_thread=False)
    def plain() -> dict[str, Any]:
        return {"ok": True}

    app = Litestar(route_handlers=[plain], plugins=[LitestarMCP()])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "plain", "arguments": {}}},
        )

    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["result"]["content"][0]["text"] == '{"ok":true}'


async def test_progress_is_never_fanned_out_to_listen_streams() -> None:
    manager = SubscriptionManager()
    _stream_id, stream = await manager.open("listen", {"toolsListChanged": True})

    await manager.publish("notifications/progress", {"progressToken": "tok-2", "progress": 1})
    await manager.publish("notifications/tools/list_changed", {})
    acknowledgement = await stream.__anext__()
    delivered = await stream.__anext__()
    await stream.aclose()

    assert acknowledgement["method"] == "notifications/subscriptions/acknowledged"
    assert delivered["method"] == "notifications/tools/list_changed"
