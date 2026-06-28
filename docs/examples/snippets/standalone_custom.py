from litestar_mcp import MCP


class DummyPlugin:
    pass


plugin = DummyPlugin()

mcp = MCP(
    name="my-mcp-server",
    plugins=[plugin],  # Forwarded to Litestar
)
