"""Snippet: register standalone prompts."""

# --8<-- [start:example]
from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig, mcp_prompt


@mcp_prompt(description="Greet a user by name.")
async def greet(name: str) -> str:
    """Build a greeting prompt.

    Args:
        name: The user's name to greet.
    """
    return f"Please greet {name} warmly."


app = Litestar(
    route_handlers=[],
    plugins=[
        LitestarMCP(
            MCPConfig(name="prompt-demo"),
            prompts=[greet],
        ),
    ],
)
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
