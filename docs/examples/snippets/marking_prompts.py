"""Snippet: marking a route handler as an MCP prompt. Referenced from docs/usage/marking_routes.rst."""

from typing import Any

from litestar import Litestar, get

from litestar_mcp import LitestarMCP


def build() -> Litestar:
    # start-example
    @get(
        "/prompts/review",
        mcp_prompt="code_review",
        mcp_prompt_description="Ask the model to review a snippet of code.",
    )
    async def code_review(language: str = "python") -> dict[str, Any]:
        """Return a single MCP ``PromptMessage``.

        Args:
            language: Programming language of the snippet under review.
        """
        text = f"Please review the following {language} code for bugs and style."
        return {"role": "user", "content": {"type": "text", "text": text}}

    app = Litestar(route_handlers=[code_review], plugins=[LitestarMCP()])
    # end-example
    return app
