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


def test_mcp_tool_stores_description_kwargs() -> None:
    @mcp_tool(
        "t",
        description="d",
        agent_instructions="ai",
        when_to_use="wtu",
        returns="r",
    )
    @get("/")
    def handler() -> str:
        return ""

    metadata = get_mcp_metadata(handler)
    assert metadata is not None
    assert metadata["description"] == "d"
    assert metadata["agent_instructions"] == "ai"
    assert metadata["when_to_use"] == "wtu"
    assert metadata["returns"] == "r"


def test_mcp_resource_stores_description_kwargs() -> None:
    @mcp_resource(
        "r",
        description="d",
        agent_instructions="ai",
        when_to_use="wtu",
        returns="ret",
    )
    @get("/")
    def handler() -> str:
        return ""

    metadata = get_mcp_metadata(handler)
    assert metadata is not None
    assert metadata["description"] == "d"
    assert metadata["agent_instructions"] == "ai"
    assert metadata["when_to_use"] == "wtu"
    assert metadata["returns"] == "ret"


def test_description_kwargs_default_to_none_and_omitted_from_metadata() -> None:
    @mcp_tool("t")
    @get("/")
    def handler() -> str:
        return ""

    metadata = get_mcp_metadata(handler)
    assert metadata is not None
    assert "description" not in metadata
    assert "agent_instructions" not in metadata
    assert "when_to_use" not in metadata
    assert "returns" not in metadata
