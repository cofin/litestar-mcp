"""Snippet: Litestar dependency injection on standalone MCP tools."""

# --8<-- [start:example]
from litestar.di import NamedDependency, Provide

from litestar_mcp import MCP

mcp = MCP("dependency-server")


def provide_account_id() -> str:
    return "acct-123"


@mcp.tool(
    dependencies={"account_id": Provide(provide_account_id, sync_to_thread=False)},
    sync_to_thread=False,
)
def current_account(account_id: NamedDependency[str]) -> dict[str, str]:
    return {"account_id": account_id}


# --8<-- [end:example]
