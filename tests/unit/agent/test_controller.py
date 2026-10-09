from litestar import Litestar
from litestar.di import Provide
from litestar.testing import TestClient

from litestar_mcp.agent.controller import AgentChatController
from litestar_mcp.agent.models import MockModelClient, ModelDelta
from litestar_mcp.agent.runtime import AgentRuntime
from litestar_mcp.agent.spec import Agent


def test_agent_chat_controller() -> None:
    mock_responses = [
        [
            ModelDelta(event_type="delta", text="Hello from agent chat!"),
        ]
    ]
    model = MockModelClient(responses=mock_responses)
    agent = Agent(name="assistant", model=model)
    runtime = AgentRuntime(target=agent)

    app = Litestar(
        route_handlers=[AgentChatController],
        dependencies={"runtime": Provide(lambda: runtime, sync_to_thread=False)},
    )

    with TestClient(app=app) as client:
        res = client.post(
            "/agent/turns",
            json={"session_id": "s1", "message": "Hi there"},
        )
        assert res.status_code in {200, 201}
        data = res.json()
        assert data["session_id"] == "s1"
        assert "Hello from agent chat!" in data["output"]

        stream_res = client.get("/agent/turns/turn-123/stream?message=hello")
        assert stream_res.status_code == 200
        assert "text/event-stream" in stream_res.headers.get("content-type", "")
