"""Tests for MCP decorators."""

from litestar.handlers import get

from litestar_mcp.decorators import get_mcp_metadata, mcp_resource, mcp_tool


def test_mcp_tool_decorator_storage() -> None:
    @mcp_tool(name="test_tool")
    @get("/")
    def my_handler() -> str:
        return "hello"

    metadata = get_mcp_metadata(my_handler)
    assert metadata is not None
    assert metadata["type"] == "tool"
    assert metadata["name"] == "test_tool"
    # Ensure no attribute mutation on the function itself if possible,
    # or at least that it's correctly retrieved.
    # Note: Litestar handlers might be tricky, but we want a central way to get this.


def test_mcp_resource_decorator_storage() -> None:
    @mcp_resource(name="test_resource")
    @get("/")
    def my_handler() -> str:
        return "hello"

    metadata = get_mcp_metadata(my_handler)
    assert metadata is not None
    assert metadata["type"] == "resource"
    assert metadata["name"] == "test_resource"
