from unittest.mock import AsyncMock

import pytest
from a2a.types import AgentCard, AgentInterface
from litestar import Litestar, get
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
