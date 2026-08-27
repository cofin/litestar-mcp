"""Multi-protocol MCP and A2A coexistence snippet."""

from litestar import Litestar, get

from litestar_mcp import A2AConfig, A2APlugin, LitestarMCP, MCPConfig


@get("/tools/multiply", mcp_tool="multiply", mcp_description="Multiply numbers")
def multiply(a: int, b: int) -> int:
    """Multiply two integers."""
    return a * b


@get("/skills/greet", opt={"a2a_skill": "greet", "a2a_description": "Greet person"})
def greet(name: str) -> str:
    """Greet a person."""
    return f"Hello, {name}!"


mcp = LitestarMCP(config=MCPConfig(base_path="/mcp"))
a2a = A2APlugin(config=A2AConfig(base_path="/a2a", auto_export_mcp_tools=True))

app = Litestar(route_handlers=[multiply, greet], plugins=[mcp, a2a])
