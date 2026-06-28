"""Snippet: marking and registering MCP prompts. Referenced from docs/usage/marking_routes.rst."""

from litestar import Litestar, get

from litestar_mcp import LitestarMCP, mcp_prompt


def build() -> "Litestar":
    # start-example
    @mcp_prompt(name="summarize", description="Summarise a document in a chosen style.")
    async def summarize(text: "str", style: "str" = "concise") -> "str":
        """Build a summarisation prompt.

        Args:
            text: The document to summarise.
            style: Summary style, e.g. ``concise`` or ``detailed``.
        """
        return f"Summarise the following in a {style} style:\n\n{text}"

    @get(
        "/prompts/code-review",
        mcp_prompt="code_review",
        mcp_prompt_description="Ask the model to review a code diff.",
    )
    async def code_review(code: "str") -> "dict[str, object]":
        """Handler-based prompt: routed under HTTP *and* exposed via ``prompts/get``."""
        return {"messages": [{"role": "user", "content": {"type": "text", "text": f"Review: {code}"}}]}

    app = Litestar(
        route_handlers=[code_review],
        plugins=[LitestarMCP(prompts=[summarize])],
    )
    # end-example
    return app
