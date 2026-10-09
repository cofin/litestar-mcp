from __future__ import annotations

import httpx2
import pytest
from litestar import Litestar
from litestar.di import Provide
from litestar.testing import AsyncTestClient

from litestar_mcp.agent.controller import AgentChatController
from litestar_mcp.agent.models import MockModelClient, ModelDelta
from litestar_mcp.agent.runtime import AgentRuntime
from litestar_mcp.agent.spec import Agent
from litestar_mcp.agent.streaming import AgentStreamFrame, iter_agent_stream


def test_agent_stream_frame_constructors_and_sse() -> None:
    f_delta = AgentStreamFrame.delta("Hello", turn_id="t1", agent_name="bot", seq=1)
    assert f_delta.event == "delta"
    assert f_delta.delta == "Hello"
    assert f_delta.seq == 1
    sse_msg = f_delta.to_sse_message()
    assert sse_msg.event == "delta"
    assert sse_msg.id == "1"

    f_thought = AgentStreamFrame.thought("Thinking", turn_id="t1", seq=2)
    assert f_thought.event == "thought"
    assert f_thought.thought == "Thinking"

    f_tc = AgentStreamFrame.tool_call("search", {"q": "test"}, call_id="c1", turn_id="t1", seq=3)
    assert f_tc.event == "tool_call"
    assert f_tc.tool_name == "search"

    f_tr = AgentStreamFrame.tool_result("search", {"items": []}, call_id="c1", turn_id="t1", seq=4)
    assert f_tr.event == "tool_result"

    f_comp = AgentStreamFrame.complete("Done", token_usage={"prompt_tokens": 5}, turn_id="t1", seq=5)
    assert f_comp.event == "complete"
    assert f_comp.data["payload"]["output"] == "Done"

    f_err = AgentStreamFrame.error("Boom", turn_id="t1", seq=6)
    assert f_err.event == "error"
    assert f_err.data["payload"]["error"] == "Boom"


@pytest.mark.asyncio
async def test_iter_agent_stream_with_httpx2() -> None:
    f1 = AgentStreamFrame.session("s1", turn_id="t1", seq=1)
    f2 = AgentStreamFrame.delta("Chunk 1", turn_id="t1", seq=2)
    f3 = AgentStreamFrame.complete("Chunk 1", turn_id="t1", seq=3)

    sse_body = (
        f"id: 1\nevent: session\ndata: {f1.to_json()}\n\n"
        f": ping\n\n"
        f"id: 2\nevent: delta\ndata: {f2.to_json()}\n\n"
        f"id: 3\nevent: complete\ndata: {f3.to_json()}\n\n"
    ).encode()

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse_body,
            request=request,
        )

    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as client:
        resp = await client.get("http://testserver/agent/stream")
        frames = [frame async for frame in iter_agent_stream(resp)]

    assert len(frames) == 3
    assert frames[0].event == "session"
    assert frames[0].seq == 1
    assert frames[1].event == "delta"
    assert frames[1].delta == "Chunk 1"
    assert frames[1].seq == 2
    assert frames[2].event == "complete"
    assert frames[2].seq == 3


@pytest.mark.asyncio
async def test_agent_chat_controller_endpoints() -> None:
    model = MockModelClient(
        responses=[
            [
                ModelDelta(event_type="thought", thought="Formulating answer"),
                ModelDelta(event_type="delta", text="Streaming reply"),
            ],
            [
                ModelDelta(event_type="delta", text="Second turn reply"),
            ],
        ]
    )
    agent = Agent(name="chat_bot", model=model)
    runtime = AgentRuntime(target=agent)

    app = Litestar(
        route_handlers=[AgentChatController],
        dependencies={"runtime": Provide(lambda: runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        chat_res = await client.post(
            "/agent/chat",
            json={"session_id": "sess-chat", "turn_id": "turn-1", "message": "Hi"},
        )
        assert chat_res.status_code in {200, 201}
        assert "text/event-stream" in chat_res.headers.get("content-type", "")
        text_body = chat_res.text
        assert "event: session" in text_body
        assert "event: thought" in text_body
        assert "event: delta" in text_body
        assert "Streaming reply" in text_body
        assert "event: complete" in text_body

        turn_res = await client.post(
            "/agent/turns",
            json={"session_id": "sess-chat", "turn_id": "turn-2", "message": "Follow up"},
        )
        assert turn_res.status_code in {200, 201}
        turn_data = turn_res.json()
        assert turn_data["output"] == "Second turn reply"

        hist_res = await client.get("/agent/sessions/sess-chat")
        assert hist_res.status_code == 200
        hist_data = hist_res.json()
        assert hist_data["session_id"] == "sess-chat"
        assert len(hist_data["messages"]) == 4
