"""Executable reference example: Coffee Shop Barista Agent with Litestar MCP."""

from typing import Any

from litestar import Litestar
from litestar.di import Provide

from litestar_mcp import (
    LitestarMCP,
    PromptController,
    PromptMessage,
    SkillController,
    prompt,
    tool,
)
from litestar_mcp.agent import (
    Agent,
    AgentChatController,
    AgentGroup,
    AgentRuntime,
    GoogleGenAIClient,
)
from litestar_mcp.mcp.config import MCPConfig


class BaristaSkillController(SkillController):
    """Barista skill providing beverage menu lookup and order preparation tools."""

    name = "barista"
    description = "Specialized skills for querying menu items and preparing coffee orders"
    prefix = "barista"
    instructions = (
        "Barista Operational Grounding:\n"
        "- Suggest seasonal single-origin roasts when asked for recommendations.\n"
        "- Confirm milk alternatives (oat, almond, soy) before finalizing espresso drinks.\n"
        "- Never accept orders for items outside the published menu.\n"
    )

    def __init__(self, owner: Any = None) -> None:
        """Initialize the barista skill with catalog menu and empty order list."""
        super().__init__(owner)
        self.menu: dict[str, dict[str, Any]] = {
            "espresso": {"price": 3.50, "roast": "Ethiopian Yirgacheffe", "decaf_available": True},
            "flat_white": {"price": 4.75, "roast": "Guatemalan Antigua", "decaf_available": True},
            "cold_brew": {"price": 5.00, "roast": "Colombian Huila", "decaf_available": False},
        }
        self.orders: list[dict[str, Any]] = []

    @tool(description="Retrieve available coffee drinks and pricing.")
    def get_menu(self) -> dict[str, dict[str, Any]]:
        """Return the current coffee menu."""
        return self.menu

    @tool(description="Place a customized beverage order.")
    def place_order(self, beverage: str, milk: str = "whole", shots: int = 2) -> dict[str, Any]:
        """Record an order and return confirmation details."""
        bev = beverage.lower().replace(" ", "_")
        if bev not in self.menu:
            return {"status": "error", "message": f"'{beverage}' is not on the menu."}

        order_record = {
            "order_id": len(self.orders) + 1,
            "beverage": bev,
            "milk": milk,
            "shots": shots,
            "price": self.menu[bev]["price"],
            "status": "in_queue",
        }
        self.orders.append(order_record)
        return {"status": "success", "order": order_record}


class CoffeePromptController(PromptController):
    """Prompt templates for greeting customers and authoring seasonal menus."""

    prefix = "coffee"
    instructions = (
        "Persona: Warm, knowledgeable neighborhood specialty coffee barista.\n"
        "Tone: Welcoming, concise, and focused on quality craft brewing.\n"
    )

    @prompt(title="Welcome Greeting", description="Greet a customer and introduce daily specials.")
    def welcome_customer(self, customer_name: str = "Friend") -> str:
        """Generate a warm barista greeting."""
        return f"Welcome to the coffee bar, {customer_name}! What can we craft for you today?"

    @prompt(title="Tasting Notes", description="Generate tasting notes for a given roast.")
    def roast_notes(self, roast_name: str) -> list[PromptMessage]:
        """Produce structured tasting notes for single-origin coffees."""
        return [
            PromptMessage.user(
                f"Produce professional SCA-style tasting notes and brewing parameters for `{roast_name}`."
            )
        ]


def create_model_client() -> GoogleGenAIClient:
    """Instantiate GoogleGenAIClient with optional thinking budget."""
    return GoogleGenAIClient(
        model="gemini-3.8-flash",
        thinking_budget=2048,
        temperature=0.2,
    )


def setup_coffee_agent_group() -> AgentGroup:
    """Build and configure the coffee shop agent group with specialists."""
    model_client = GoogleGenAIClient(model="gemini-3.8-flash")

    barista_agent = Agent(
        name="barista",
        description="Prepares coffee drinks, looks up menu pricing, and manages customer orders.",
        instructions="You are the lead barista. Handle menu questions and beverage customizations.",
        skills=[BaristaSkillController()],
        model=model_client,
    )

    triage_coordinator = Agent(
        name="concierge",
        description="Greets customers and routes coffee questions to the barista specialist.",
        instructions="Greet customers warmly. Delegate ordering and coffee questions to the barista.",
        model=model_client,
    )

    return AgentGroup(
        coordinator=triage_coordinator,
        specialists=[barista_agent],
    )


coffee_shop_group = setup_coffee_agent_group()

agent_runtime = AgentRuntime(target=coffee_shop_group)

mcp_plugin = LitestarMCP(
    config=MCPConfig(
        prompt_controllers=[CoffeePromptController],
        skill_controllers=[BaristaSkillController],
    )
)

app = Litestar(
    route_handlers=[
        AgentChatController,
        CoffeePromptController,
        BaristaSkillController,
    ],
    plugins=[mcp_plugin],
    dependencies={"runtime": Provide(lambda: agent_runtime, sync_to_thread=False)},
)
