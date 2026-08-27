"""Unit tests for A2A dynamic registry and Agent Card manifest builder."""

from dataclasses import dataclass
from typing import Any

from litestar import Litestar, get

from litestar_mcp import LitestarMCP
from litestar_mcp.a2a.manifest import build_agent_card
from litestar_mcp.a2a.registry import A2ARegistry, SkillRegistration
from litestar_mcp.a2a.types import AgentCard, AgentSkill


@dataclass
class OrderInput:
    order_id: str
    include_history: bool = False


def sample_skill_fn(order: OrderInput, priority: int = 1) -> dict[str, Any]:
    """Look up order details.

    Args:
        order: The order identifier and history options.
        priority: Processing priority (1-5).

    Returns:
        The fetched order dictionary.
    """
    return {"order_id": order.order_id, "priority": priority}


def test_skill_registration_introspection() -> None:
    """Verify SkillRegistration automatically derives JSON Schema and docstrings."""
    reg = SkillRegistration(
        fn=sample_skill_fn,
        id="lookup_order",
        name="Order Lookup",
        tags=["orders", "ecommerce"],
        examples=["lookup_order(order_id='123')"],
    )

    skill = reg.to_agent_skill()
    assert isinstance(skill, AgentSkill)
    assert skill.id == "lookup_order"
    assert skill.name == "Order Lookup"
    assert skill.description == "Look up order details."
    assert "order" in skill.input_schema.get("properties", {})
    assert "priority" in skill.input_schema.get("properties", {})
    assert skill.tags == ["orders", "ecommerce"]
    assert skill.examples == ["lookup_order(order_id='123')"]


def test_build_agent_card() -> None:
    """Test build_agent_card constructs compliant AgentCard metadata."""
    registry = A2ARegistry()
    registry.register(
        SkillRegistration(
            fn=sample_skill_fn,
            id="lookup_order",
            name="Order Lookup",
        )
    )

    app = Litestar(route_handlers=[])

    card = build_agent_card(
        app=app,
        registry=registry,
        name="StoreAgent",
        description="E-commerce support agent",
        version="2.0.0",
        base_url="https://api.example.com/a2a",
    )

    assert isinstance(card, AgentCard)
    assert card.name == "StoreAgent"
    assert card.description == "E-commerce support agent"
    assert card.version == "2.0.0"
    assert card.protocol_version == "1.0"
    assert card.url == "https://api.example.com/a2a"
    assert len(card.skills) == 1
    assert card.skills[0].id == "lookup_order"


def test_build_agent_card_auto_export_mcp_tools() -> None:
    """Test opt-in auto-export of MCP tools as A2A skills."""

    @get("/search", opt={"mcp_tool": "search_warehouse"})
    def tool_fn(search_term: str) -> str:
        """Search items in warehouse.

        Args:
            search_term: The search query string.

        Returns:
            The search result.
        """
        return search_term

    app = Litestar(route_handlers=[tool_fn], plugins=[LitestarMCP()])

    registry = A2ARegistry()
    card = build_agent_card(
        app=app,
        registry=registry,
        name="HybridAgent",
        auto_export_mcp_tools=True,
    )

    assert len(card.skills) == 1
    assert card.skills[0].name == "search_warehouse"
    assert card.skills[0].description == "Search items in warehouse."
    assert "search_term" in card.skills[0].input_schema.get("properties", {})
