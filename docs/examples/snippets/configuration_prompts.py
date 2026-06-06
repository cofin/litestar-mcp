"""Snippet: registering a standalone prompt. Referenced from docs/usage/configuration.rst."""

from typing import Any

from litestar import Litestar

from litestar_mcp import LitestarMCP, mcp_prompt


def build() -> Litestar:
    # start-example
    @mcp_prompt(
        "greeting",
        description="Greet a user by name.",
    )
    def greeting(name: str = "world") -> dict[str, Any]:
        """A standalone prompt callable - not bound to any route handler.

        Args:
            name: Who to greet.
        """
        return {"role": "user", "content": {"type": "text", "text": f"Say hello to {name}."}}

    app = Litestar(route_handlers=[], plugins=[LitestarMCP(prompts=[greeting])])
    # end-example
    return app
