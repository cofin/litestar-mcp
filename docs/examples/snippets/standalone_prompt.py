from typing import Any

from litestar_mcp import MCP

mcp = MCP("my-mcp-server")


@mcp.prompt(name="explain_code", description="Ask the model to explain a code snippet.")
def explain(code: str) -> list[dict[str, Any]]:
    return [
        {
            "role": "user",
            "content": f"Explain this python code:\n\n{code}",
        }
    ]
