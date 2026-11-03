"""Snippet: Bidirectional protocol bridges connecting Litestar MCP routes, AgentRuntime, and A2A 1.0."""

# start-example
from litestar import Litestar, get

from litestar_mcp import LitestarMCP, SkillController, tool
from litestar_mcp.agent import (
    Agent,
    AgentChatController,
    AgentRuntime,
    MockModelClient,
    ModelDelta,
    agent_to_a2a,
    agent_to_mcp,
    mcp_to_tools,
)


@get("/inventory/{sku:str}", mcp_tool="lookup_inventory")
async def lookup_inventory(sku: str) -> dict[str, str | int]:
    """Look up warehouse stock levels for a product SKU."""
    return {"sku": sku, "available": 42}


class InventorySkillController(SkillController):
    """Domain skill providing reorder threshold checks for inventory agents."""

    name = "inventory"
    description = "Verify warehouse stock and reorder thresholds"
    tags = ("inventory", "warehouse")
    min_stock = 10

    @tool
    def check_reorder_threshold(self, sku: str, available: int) -> dict[str, str | bool]:
        """Determine whether a SKU has fallen below its reorder threshold."""
        return {"sku": sku, "reorder_required": available < self.min_stock}


catalog_mcp = LitestarMCP()
catalog_app = Litestar(route_handlers=[lookup_inventory], plugins=[catalog_mcp])
bridged_tools = mcp_to_tools(catalog_mcp)

model = MockModelClient(responses=[[ModelDelta(event_type="delta", text="SKU espresso-beans has 42 units in stock.")]])
inventory_agent = Agent(
    name="inventory_specialist",
    description="Answers warehouse inventory questions using bridged MCP tools.",
    instructions="Check inventory availability before confirming stock counts.",
    tools=bridged_tools,
    skills=[InventorySkillController],
    model=model,
)
runtime = AgentRuntime(inventory_agent, default_model=model)
mcp_bridge_plugin = agent_to_mcp(inventory_agent, path="/mcp")
a2a_bridge_plugin = agent_to_a2a(
    inventory_agent,
    path="/a2a",
    base_url="https://agents.example.com",
    runtime=runtime,
)
app = Litestar(
    route_handlers=[AgentChatController],
    plugins=[runtime, mcp_bridge_plugin, a2a_bridge_plugin],
)
# end-example


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
