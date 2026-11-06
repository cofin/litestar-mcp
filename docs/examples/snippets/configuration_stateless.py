"""Snippet: stateless MCP transport defaults."""

# --8<-- [start:example]
from litestar_mcp import MCPConfig

config = MCPConfig(
    cache_ttl_ms=0,
    cache_scope="private",
    subscription_max_streams=10_000,
    subscription_keepalive_seconds=15,
)
# --8<-- [end:example]
