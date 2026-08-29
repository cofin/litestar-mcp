from typing import Any
from unittest.mock import AsyncMock

import pytest
from a2a.client.transports.jsonrpc import JsonRpcTransport
from a2a.server.context import ServerCallContext
from a2a.types import AgentCard, AgentInterface, Message, Part, Role, SendMessageRequest
from litestar import Litestar, Request, get
from litestar.testing import AsyncTestClient

from litestar_mcp.a2a import A2AConfig, LitestarA2A


def make_card(path: str = "/a2a") -> AgentCard:
    return AgentCard(
        name="Test agent",
        description="Test agent",
        version="1.0.0",
        supported_interfaces=[
            AgentInterface(
                url=f"https://example.com{path}",
                protocol_binding="JSONRPC",
                protocol_version="1.0",
            )
        ],
    )


@pytest.mark.anyio
async def test_serves_official_agent_card_and_closes_handler() -> None:
    handler = AsyncMock()
    app = Litestar(plugins=[LitestarA2A(make_card(), handler)])

    async with AsyncTestClient(app=app) as client:
        response = await client.get("/.well-known/agent-card.json")

    assert response.status_code == 200
    assert response.json()["supportedInterfaces"][0]["protocolVersion"] == "1.0"
    handler.aclose.assert_awaited_once()


def test_rejects_card_whose_interface_does_not_match_mount() -> None:
    with pytest.raises(ValueError, match="matching the A2A path"):
        LitestarA2A(make_card("/wrong"), AsyncMock())


def test_rejects_application_owned_route_collision() -> None:
    @get("/a2a")
    async def owned() -> None: ...

    with pytest.raises(ValueError, match="A2A route collision"):
        Litestar(route_handlers=[owned], plugins=[LitestarA2A(make_card(), AsyncMock())])


def test_config_requires_absolute_paths() -> None:
    with pytest.raises(ValueError, match="must start"):
        A2AConfig(path="a2a")


@pytest.mark.anyio
async def test_dispatches_jsonrpc_to_official_request_handler() -> None:
    handler = AsyncMock()
    handler.on_message_send.return_value = Message(
        message_id="reply-1",
        role=Role.ROLE_AGENT,
        parts=[Part(text="hello")],
    )
    app = Litestar(plugins=[LitestarA2A(make_card(), handler)])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a",
            headers={"A2A-Version": "1.0"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": "request-1",
                        "role": "ROLE_USER",
                        "parts": [{"text": "hello"}],
                    }
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["message"]["messageId"] == "reply-1"
    handler.on_message_send.assert_awaited_once()


@pytest.mark.anyio
async def test_official_jsonrpc_client_dispatches_through_litestar() -> None:
    handler = AsyncMock()
    handler.on_message_send.return_value = Message(
        message_id="official-reply",
        role=Role.ROLE_AGENT,
        parts=[Part(text="hello")],
    )
    card = make_card()
    app = Litestar(plugins=[LitestarA2A(card, handler)])

    async with AsyncTestClient(app=app) as http_client:
        http_client.headers["A2A-Version"] = "1.0"
        transport = JsonRpcTransport(http_client, card, "http://testserver.local/a2a")
        response = await transport.send_message(
            SendMessageRequest(
                message=Message(
                    message_id="official-request",
                    role=Role.ROLE_USER,
                    parts=[Part(text="hello")],
                )
            )
        )

    assert response.message.message_id == "official-reply"


@pytest.mark.anyio
async def test_uses_litestar_context_builder() -> None:
    observed_request: Request[Any, Any, Any] | None = None
    context = ServerCallContext()

    def build_context(request: Request[Any, Any, Any]) -> ServerCallContext:
        nonlocal observed_request
        observed_request = request
        context.state["headers"] = dict(request.headers)
        return context

    handler = AsyncMock()
    handler.on_get_task.return_value = None
    app = Litestar(plugins=[LitestarA2A(make_card(), handler, A2AConfig(context_builder=build_context))])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a",
            headers={"A2A-Version": "1.0"},
            json={"jsonrpc": "2.0", "id": "x", "method": "GetTask", "params": {"id": "missing"}},
        )

    assert response.status_code == 200
    assert observed_request is not None
    handler.on_get_task.assert_awaited_once()


@pytest.mark.anyio
async def test_returns_jsonrpc_parse_error_for_malformed_json() -> None:
    app = Litestar(plugins=[LitestarA2A(make_card(), AsyncMock())])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a", content=b"{", headers={"content-type": "application/json", "A2A-Version": "1.0"}
        )

    assert response.status_code == 200
    assert response.json()["error"]["code"] == -32700


@pytest.mark.anyio
async def test_v03_compatibility_is_disabled_by_default() -> None:
    app = Litestar(plugins=[LitestarA2A(make_card(), AsyncMock())])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a",
            json={"jsonrpc": "2.0", "id": 1, "method": "tasks/get", "params": {"id": "task-1"}},
        )

    assert response.json()["error"]["code"] == -32601


@pytest.mark.anyio
async def test_v03_compatibility_uses_official_conversions_when_enabled() -> None:
    handler = AsyncMock()
    handler.on_message_send.return_value = Message(
        message_id="reply-v03",
        role=Role.ROLE_AGENT,
        parts=[Part(text="hello")],
    )
    app = Litestar(plugins=[LitestarA2A(make_card(), handler, A2AConfig(enable_v0_3_compat=True))])

    async with AsyncTestClient(app=app) as client:
        response = await client.post(
            "/a2a",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "message/send",
                "params": {
                    "message": {
                        "messageId": "request-v03",
                        "role": "user",
                        "parts": [{"kind": "text", "text": "hello"}],
                    }
                },
            },
        )

    assert response.status_code == 200
    assert response.json()["result"]["messageId"] == "reply-v03"
