"""Unit tests for AgentChatController HTTP and SSE endpoints, ownership, and typing."""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

import anyio
import pytest
from litestar import Litestar
from litestar.di import Provide
from litestar.exceptions import ImproperlyConfiguredException, PermissionDeniedException
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult
from litestar.openapi.config import OpenAPIConfig
from litestar.testing import AsyncTestClient

if TYPE_CHECKING:
    from litestar.connection import ASGIConnection

from litestar_mcp.agent.controller import AgentChatController
from litestar_mcp.agent.guards import TurnBudget
from litestar_mcp.agent.models import MockModelClient, ModelDelta
from litestar_mcp.agent.runtime import AgentRuntime
from litestar_mcp.agent.spec import Agent
from litestar_mcp.core.tools import tool


class HeaderAuthMiddleware(AbstractAuthenticationMiddleware):
    """Authentication middleware setting connection.user from X-User header."""

    async def authenticate_request(self, connection: "ASGIConnection[Any, Any, Any, Any]") -> "AuthenticationResult":
        """Authenticate request if X-User header is present, else unauthenticated."""
        user_id = connection.headers.get("X-User")
        if user_id:
            return AuthenticationResult(user={"id": user_id}, auth=user_id)
        return AuthenticationResult(user=None, auth=None)


class CustomMoney:
    """Custom type to test layered type encoders."""

    def __init__(self, amount: str) -> None:
        """Initialize custom money."""
        self.amount = amount


@tool(description="Get balance.")
def get_balance() -> CustomMoney:
    """Return custom money balance."""
    return CustomMoney("1.00")


def test_chat_dependencies_are_not_query_parameters() -> None:
    """AgentChatController requires runtime dependency and excludes it from query parameters in OpenAPI."""
    with pytest.raises(ImproperlyConfiguredException):
        Litestar(route_handlers=[AgentChatController])

    dummy_runtime = AgentRuntime(target=Agent(name="bot"))
    app = Litestar(
        route_handlers=[AgentChatController],
        dependencies={"runtime": Provide(lambda: dummy_runtime, sync_to_thread=False)},
        openapi_config=OpenAPIConfig(title="Test", version="1.0.0"),
    )

    paths = app.openapi_schema.paths
    assert paths is not None
    for path_item in paths.values():
        if path_item is not None:
            for op in (path_item.get, path_item.post, path_item.put, path_item.delete):
                if op is not None and op.parameters:
                    param_names = [
                        getattr(p, "name", None) for p in op.parameters if getattr(p, "name", None) is not None
                    ]
                    assert "runtime" not in param_names


@pytest.mark.anyio
async def test_chat_streams_session_first_and_completes() -> None:
    """POST /agent/chat streams session frame first, text deltas, and complete frame with matching seq ids."""
    model = MockModelClient(
        responses=[
            [
                ModelDelta(event_type="delta", text="chunk1"),
                ModelDelta(event_type="delta", text="chunk2"),
            ],
        ]
    )
    agent = Agent(name="bot", model=model)
    runtime = AgentRuntime(target=agent)

    app = Litestar(
        route_handlers=[AgentChatController],
        dependencies={"runtime": Provide(lambda: runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.post("/agent/chat", json={"message": "hello"})
        assert res.status_code == 200
        text = res.text
        lines = [line for line in text.splitlines() if line]

        events: list[dict[str, str]] = []
        current: dict[str, str] = {}
        for line in lines:
            if line.startswith("id: "):
                current["id"] = line[4:]
            elif line.startswith("event: "):
                current["event"] = line[7:]
            elif line.startswith("data: "):
                current["data"] = line[6:]
                events.append(dict(current))
                current.clear()

        event_names = [e["event"] for e in events]
        assert event_names == ["session", "delta", "delta", "complete"]
        assert events[0]["id"] == "1"
        assert events[1]["id"] == "2"
        assert events[2]["id"] == "3"
        assert events[3]["id"] == "4"


@pytest.mark.anyio
async def test_chat_rejects_unknown_fields() -> None:
    """POST /agent/chat rejects unknown fields in body with 400 Bad Request."""
    dummy_runtime = AgentRuntime(target=Agent(name="bot"))
    app = Litestar(
        route_handlers=[AgentChatController],
        dependencies={"runtime": Provide(lambda: dummy_runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.post("/agent/chat", json={"message": "hi", "turn_id": "t1"})
        assert res.status_code == 400


@pytest.mark.anyio
async def test_turns_returns_typed_result() -> None:
    """POST /agent/turns returns typed TurnResult response."""
    model = MockModelClient(responses=[[ModelDelta(event_type="delta", text="reply content")]])
    agent = Agent(name="turn_bot", model=model)
    runtime = AgentRuntime(target=agent)

    app = Litestar(
        route_handlers=[AgentChatController],
        dependencies={"runtime": Provide(lambda: runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.post("/agent/turns", json={"message": "hi"})
        assert res.status_code == 200
        data = res.json()
        assert data["output"] == "reply content"
        assert data["agent_name"] == "turn_bot"
        assert isinstance(data["messages"], list)
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == "user"
        assert data["messages"][1]["role"] == "assistant"


@pytest.mark.anyio
async def test_turn_timeout_maps_to_504() -> None:
    """Turn timing out maps to HTTP 504 Gateway Timeout."""

    class HangingModel:
        """Model that sleeps past budget."""

        async def stream_turn(self, **kwargs: Any) -> AsyncIterator[ModelDelta]:
            """Sleep indefinitely."""
            await anyio.sleep(10.0)
            yield ModelDelta(event_type="delta", text="never")

    agent = Agent(name="slow_bot", model=HangingModel())
    runtime = AgentRuntime(target=agent, budget=TurnBudget(timeout_seconds=0.05))

    app = Litestar(
        route_handlers=[AgentChatController],
        dependencies={"runtime": Provide(lambda: runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.post("/agent/turns", json={"message": "hang"})
        assert res.status_code == 504


@pytest.mark.anyio
async def test_session_history_is_owner_scoped() -> None:
    """User A creates a session, User B gets 404, User A gets history, and anonymous gets 404."""
    model = MockModelClient(responses=[[ModelDelta(event_type="delta", text="secret answer")]])
    agent = Agent(name="auth_bot", model=model)
    runtime = AgentRuntime(target=agent)

    app = Litestar(
        route_handlers=[AgentChatController],
        middleware=[HeaderAuthMiddleware],
        dependencies={"runtime": Provide(lambda: runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        turn_res = await client.post(
            "/agent/turns",
            json={"message": "my secret", "session_id": "shared_sid"},
            headers={"X-User": "alice"},
        )
        assert turn_res.status_code == 200

        res_bob = await client.get("/agent/sessions/shared_sid", headers={"X-User": "bob"})
        assert res_bob.status_code == 404

        res_anon = await client.get("/agent/sessions/shared_sid")
        assert res_anon.status_code == 404

        res_alice = await client.get("/agent/sessions/shared_sid", headers={"X-User": "alice"})
        assert res_alice.status_code == 200
        data = res_alice.json()
        assert data["session_id"] == "shared_sid"
        assert len(data["messages"]) == 2


@pytest.mark.anyio
async def test_anonymous_cannot_choose_session_id() -> None:
    """Anonymous POST /agent/turns with a chosen session_id gets a generated session_id."""
    model = MockModelClient(responses=[[ModelDelta(event_type="delta", text="ok")]])
    agent = Agent(name="bot", model=model)
    runtime = AgentRuntime(target=agent)

    app = Litestar(
        route_handlers=[AgentChatController],
        dependencies={"runtime": Provide(lambda: runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.post("/agent/turns", json={"message": "hi", "session_id": "chosen_by_client"})
        assert res.status_code == 200
        data = res.json()
        assert data["session_id"] != "chosen_by_client"


@pytest.mark.anyio
async def test_idle_stream_emits_ping() -> None:
    """Idle stream emits ping frame when ping_interval elapses without model deltas."""

    class DelayedModel:
        """Model that delays first delta."""

        async def stream_turn(self, **kwargs: Any) -> AsyncIterator[ModelDelta]:
            """Delay before delta."""
            await anyio.sleep(0.12)
            yield ModelDelta(event_type="delta", text="delayed text")

    class FastPingController(AgentChatController):
        """Controller with short ping interval for test."""

        ping_interval = 0.04

    agent = Agent(name="delay_bot", model=DelayedModel())
    runtime = AgentRuntime(target=agent)

    app = Litestar(
        route_handlers=[FastPingController],
        dependencies={"runtime": Provide(lambda: runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.post("/agent/chat", json={"message": "start"})
        assert res.status_code == 200
        text = res.text
        assert "event: ping" in text
        assert "delayed text" in text


@pytest.mark.anyio
async def test_layered_type_encoders_applied_to_frames() -> None:
    """App-level type encoders are applied when serializing tool results in stream frames."""
    model = MockModelClient(
        responses=[
            [
                ModelDelta(event_type="tool_call", tool_name="get_balance", call_id="c1", arguments={}),
            ],
            [
                ModelDelta(event_type="delta", text="Balance checked"),
            ],
        ]
    )
    agent = Agent(name="bank_bot", tools=[get_balance], model=model)
    runtime = AgentRuntime(target=agent)

    app = Litestar(
        route_handlers=[AgentChatController],
        type_encoders={CustomMoney: lambda m: f"${m.amount}"},
        dependencies={"runtime": Provide(lambda: runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.post("/agent/chat", json={"message": "balance"})
        assert res.status_code == 200
        text = res.text
        assert "$1.00" in text


@pytest.mark.anyio
async def test_chat_controller_guards_apply() -> None:
    """Controller guards block requests to all routes."""

    class DeniedByGuardError(PermissionDeniedException):
        """Guard rejection error."""

    def deny_guard(connection: Any, _: Any) -> None:
        """Deny all connections."""
        raise DeniedByGuardError

    class GuardedChatController(AgentChatController):
        """Chat controller with denying guard."""

        guards = [deny_guard]

    dummy_runtime = AgentRuntime(target=Agent(name="bot"))
    app = Litestar(
        route_handlers=[GuardedChatController],
        dependencies={"runtime": Provide(lambda: dummy_runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res_chat = await client.post("/agent/chat", json={"message": "hi"})
        assert res_chat.status_code == 403

        res_turns = await client.post("/agent/turns", json={"message": "hi"})
        assert res_turns.status_code == 403

        res_sess = await client.get("/agent/sessions/s1")
        assert res_sess.status_code == 403


@pytest.mark.anyio
async def test_no_get_turn_stream_route() -> None:
    """GET /agent/turns/{id}/stream route no longer exists."""
    dummy_runtime = AgentRuntime(target=Agent(name="bot"))
    app = Litestar(
        route_handlers=[AgentChatController],
        dependencies={"runtime": Provide(lambda: dummy_runtime, sync_to_thread=False)},
    )

    async with AsyncTestClient(app=app) as client:
        res = await client.get("/agent/turns/turn-123/stream")
        assert res.status_code in {404, 405}
