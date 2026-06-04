"""Snippet: register standalone prompts. Referenced from docs/usage/configuration.rst."""

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig, mcp_prompt


@mcp_prompt(name="greet", description="Greet a user by name.")
async def greet(name: str) -> str:
    """Build a greeting prompt.

    Args:
        name: The user's name to greet.
    """
    return f"Please greet {name} warmly."


def build() -> Litestar:
    # start-example
    app = Litestar(
        plugins=[
            LitestarMCP(
                MCPConfig(name="prompt-demo"),
                prompts=[greet],
            ),
        ],
    )
    # end-example
    return app
