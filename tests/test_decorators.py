"""Tests for the decorators module."""

from typing import Any

from litestar import Litestar, get, post

from litestar_mcp import LitestarMCP, mcp_resource, mcp_tool


class TestDecorators:
    """Test suite for MCP decorators functionality."""

    def test_mcp_tool_decorator_discovery(self) -> None:
        """Test that @mcp_tool decorator is discovered by the plugin."""

        @mcp_tool(name="user_manager")
        @get("/users")
        async def get_users() -> list[dict[str, Any]]:
            """Get all users from the system."""
            return [{"id": 1, "name": "Alice"}]

        plugin = LitestarMCP()
        Litestar(plugins=[plugin], route_handlers=[get_users])

        assert "user_manager" in plugin.discovered_tools
        assert len(plugin.discovered_tools) == 1
        assert plugin.discovered_tools["user_manager"] == get_users

    def test_mcp_resource_decorator_discovery(self) -> None:
        """Test that @mcp_resource decorator is discovered by the plugin."""

        @mcp_resource(name="app_config")
        @get("/config")
        async def get_config() -> dict[str, Any]:
            """Get application configuration."""
            return {"debug": True, "version": "1.0.0"}

        plugin = LitestarMCP()
        Litestar(plugins=[plugin], route_handlers=[get_config])

        assert "app_config" in plugin.discovered_resources
        assert len(plugin.discovered_resources) == 1
        assert plugin.discovered_resources["app_config"] == get_config

    def test_mixed_decorator_and_opt_discovery(self) -> None:
        """Test that both decorator and opt-based marking work together."""

        @mcp_tool(name="decorator_tool")
        @get("/decorator")
        async def decorator_tool() -> dict[str, str]:
            return {"type": "decorator"}

        @get("/opt", opt={"mcp_tool": "opt_tool"})
        async def opt_tool() -> dict[str, str]:
            return {"type": "opt"}

        @mcp_resource(name="decorator_resource")
        @get("/decorator-resource")
        async def decorator_resource() -> dict[str, str]:
            return {"type": "decorator_resource"}

        @get("/opt-resource", opt={"mcp_resource": "opt_resource"})
        async def opt_resource() -> dict[str, str]:
            return {"type": "opt_resource"}

        plugin = LitestarMCP()
        Litestar(
            plugins=[plugin],
            route_handlers=[decorator_tool, opt_tool, decorator_resource, opt_resource],
        )

        # Both tools should be discovered
        assert len(plugin.discovered_tools) == 2
        assert "decorator_tool" in plugin.discovered_tools
        assert "opt_tool" in plugin.discovered_tools

        # Both resources should be discovered
        assert len(plugin.discovered_resources) == 2
        assert "decorator_resource" in plugin.discovered_resources
        assert "opt_resource" in plugin.discovered_resources

    def test_decorator_precedence_over_opt(self) -> None:
        """Test that decorator takes precedence over opt dict when both are present."""

        @mcp_tool(name="decorator_name")
        @get("/conflict", opt={"mcp_tool": "opt_name"})
        async def conflicting_tool() -> dict[str, str]:
            return {"message": "conflict resolved"}

        plugin = LitestarMCP()
        Litestar(plugins=[plugin], route_handlers=[conflicting_tool])

        # Should use decorator name, not opt name
        assert "decorator_name" in plugin.discovered_tools
        assert "opt_name" not in plugin.discovered_tools
        assert len(plugin.discovered_tools) == 1

    def test_decorator_metadata_preservation(self) -> None:
        """Test that decorator metadata is properly attached to functions."""

        @mcp_tool(name="test_tool")
        @get("/test", sync_to_thread=False)
        def test_function() -> str:
            return "test"

        # Check that metadata is attached
        from litestar_mcp.utils import get_mcp_metadata

        metadata = get_mcp_metadata(test_function)
        assert metadata is not None
        assert metadata["type"] == "tool"
        assert metadata["name"] == "test_tool"

    def test_resource_decorator_metadata_preservation(self) -> None:
        """Test that resource decorator metadata is properly attached."""

        @mcp_resource(name="test_resource")
        @get("/resource", sync_to_thread=False)
        def resource_function() -> dict[str, str]:
            return {"data": "test"}

        # Check that metadata is attached
        from litestar_mcp.utils import get_mcp_metadata

        metadata = get_mcp_metadata(resource_function)
        assert metadata is not None
        assert metadata["type"] == "resource"
        assert metadata["name"] == "test_resource"

    def test_decorator_with_async_handlers(self) -> None:
        """Test decorators work correctly with async handlers."""

        @mcp_tool(name="async_tool")
        @post("/async")
        async def async_tool(data: dict[str, Any]) -> dict[str, Any]:
            """Process data asynchronously."""
            return {"processed": True, "input": data}

        plugin = LitestarMCP()
        Litestar(plugins=[plugin], route_handlers=[async_tool])

        assert "async_tool" in plugin.discovered_tools
        assert plugin.discovered_tools["async_tool"] == async_tool

    def test_decorator_function_wrapper_preservation(self) -> None:
        """Test that decorated functions preserve metadata correctly."""

        @mcp_tool(name="working_tool")
        @get("/working", sync_to_thread=False)
        def working_function(message: str) -> dict[str, str]:
            """A function that should work normally."""
            return {"message": f"Processed: {message}"}

        # The decorated object should be a Litestar handler with MCP metadata
        from litestar_mcp.utils import get_handler_function, get_mcp_metadata

        metadata = get_mcp_metadata(working_function)
        assert metadata == {"type": "tool", "name": "working_tool"}

        # The underlying function should be accessible
        fn = get_handler_function(working_function)
        assert fn.__doc__ == "A function that should work normally."

    def test_multiple_tools_same_handler_different_names(self) -> None:
        """Test that multiple handlers can use decorators with different names."""

        @mcp_tool(name="tool_one")
        @get("/one", sync_to_thread=False)
        def tool_one() -> dict[str, str]:
            return {"id": "one"}

        @mcp_tool(name="tool_two")
        @get("/two", sync_to_thread=False)
        def tool_two() -> dict[str, str]:
            return {"id": "two"}

        @mcp_resource(name="resource_one")
        @get("/res-one", sync_to_thread=False)
        def resource_one() -> dict[str, str]:
            return {"resource": "one"}

        @mcp_resource(name="resource_two")
        @get("/res-two", sync_to_thread=False)
        def resource_two() -> dict[str, str]:
            return {"resource": "two"}

        plugin = LitestarMCP()
        Litestar(
            plugins=[plugin],
            route_handlers=[tool_one, tool_two, resource_one, resource_two],
        )

        assert len(plugin.discovered_tools) == 2
        assert "tool_one" in plugin.discovered_tools
        assert "tool_two" in plugin.discovered_tools

        assert len(plugin.discovered_resources) == 2
        assert "resource_one" in plugin.discovered_resources
        assert "resource_two" in plugin.discovered_resources

    def test_decorator_backward_compatibility(self) -> None:
        """Test that existing opt-based approach still works when decorators are available."""

        @get("/old-style", opt={"mcp_tool": "old_tool"})
        async def old_style_tool() -> dict[str, str]:
            return {"style": "old"}

        @get("/old-resource", opt={"mcp_resource": "old_resource"})
        async def old_style_resource() -> dict[str, str]:
            return {"style": "old"}

        plugin = LitestarMCP()
        Litestar(plugins=[plugin], route_handlers=[old_style_tool, old_style_resource])

        # Should still discover opt-based tools/resources
        assert "old_tool" in plugin.discovered_tools
        assert "old_resource" in plugin.discovered_resources

    def test_empty_decorator_name_handling(self) -> None:
        """Test handling of edge cases in decorator names."""

        @mcp_tool(name="")
        @get("/empty", sync_to_thread=False)
        def empty_name_tool() -> dict[str, str]:
            return {"name": "empty"}

        plugin = LitestarMCP()
        Litestar(plugins=[plugin], route_handlers=[empty_name_tool])

        # Should handle empty name gracefully
        assert "" in plugin.discovered_tools

    def test_decorator_type_hints_preserved(self) -> None:
        """Test that type hints are preserved through decoration."""

        @mcp_tool(name="typed_tool")
        @get("/typed", sync_to_thread=False)
        def typed_function(count: int, message: str) -> dict[str, Any]:
            return {"count": count, "message": message}

        # Type hints should be preserved in the underlying function
        import inspect

        # Get the underlying function from the Litestar handler
        from litestar_mcp.utils import get_handler_function

        fn = get_handler_function(typed_function)
        sig = inspect.signature(fn)
        assert sig.parameters["count"].annotation is int
        assert sig.parameters["message"].annotation is str
        # Skip the return annotation check as it involves generic types

    def test_nested_route_handlers_with_decorators(self) -> None:
        """Test decorators work with nested route handlers."""

        @mcp_tool(name="nested_tool")
        @get("/nested", sync_to_thread=False)
        def nested_tool() -> dict[str, str]:
            return {"location": "nested"}

        # Mock nested structure
        class MockContainer:
            route_handlers = [nested_tool]

        plugin = LitestarMCP()
        plugin._discover_mcp_routes([MockContainer()])  # pyright: ignore

        assert "nested_tool" in plugin.discovered_tools
