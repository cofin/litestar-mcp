"""Tests for MCPToolRegistry."""

import threading
from typing import Any
from weakref import ref as weakref_ref

import pytest
from litestar import Litestar, get
from litestar.handlers import BaseRouteHandler

from litestar_mcp.registry import HandlerSignature, MCPMetadata, MCPToolRegistry
from tests.conftest import get_handler_from_app


def collect_route_handlers(route_handlers: "list[Any]") -> "list[BaseRouteHandler]":
    """Recursively collect all BaseRouteHandler instances from route handlers list."""
    handlers: list[BaseRouteHandler] = []
    for item in route_handlers:
        if isinstance(item, BaseRouteHandler):
            handlers.append(item)
        if hasattr(item, "route_handlers"):
            handlers.extend(collect_route_handlers(item.route_handlers))
    return handlers


class TestHandlerSignature:
    """Tests for HandlerSignature composite key."""

    def test_from_handler_extracts_signature(self) -> None:
        """Test signature extraction from handler."""

        @get("/users", sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return [{"id": 1, "name": "Alice"}]

        app = Litestar(route_handlers=[list_users])
        handler = get_handler_from_app(app, "/users", "GET")

        sig = HandlerSignature.from_handler(handler)

        assert sig.route_path in ["/users", "/"]
        assert "GET" in sig.http_methods
        assert "list_users" in sig.endpoint_qualname
        assert sig.app_namespace is None

    def test_signature_equality(self) -> None:
        """Test signatures with same values are equal."""

        @get("/test", sync_to_thread=False)
        def test_handler() -> dict[str, str]:
            return {"status": "ok"}

        app = Litestar(route_handlers=[test_handler])
        handler = get_handler_from_app(app, "/test", "GET")

        sig1 = HandlerSignature.from_handler(handler)
        sig2 = HandlerSignature.from_handler(handler)

        assert sig1 == sig2
        assert hash(sig1) == hash(sig2)

    def test_different_handlers_different_signatures(self) -> None:
        """Test different handlers produce different signatures."""

        @get("/users", sync_to_thread=False)
        def list_users() -> list[dict[str, str]]:
            return []

        @get("/posts", sync_to_thread=False)
        def list_posts() -> list[dict[str, str]]:
            return []

        app = Litestar(route_handlers=[list_users, list_posts])
        users_handler = get_handler_from_app(app, "/users", "GET")
        posts_handler = get_handler_from_app(app, "/posts", "GET")

        users_sig = HandlerSignature.from_handler(users_handler)
        posts_sig = HandlerSignature.from_handler(posts_handler)

        assert users_sig != posts_sig


class TestMCPMetadata:
    """Tests for MCPMetadata dataclass."""

    def test_metadata_creation(self) -> None:
        """Test creating metadata instance."""

        @get("/test", sync_to_thread=False)
        def test_handler() -> dict[str, str]:
            return {"status": "ok"}

        app = Litestar(route_handlers=[test_handler])
        handler = get_handler_from_app(app, "/test", "GET")

        metadata = MCPMetadata(
            type="tool",
            name="test_tool",
            description="Test description",
            handler_ref=weakref_ref(handler),
        )

        assert metadata.type == "tool"
        assert metadata.name == "test_tool"
        assert metadata.description == "Test description"
        assert metadata.handler_ref() is handler  # type: ignore[misc]

    def test_metadata_without_description(self) -> None:
        """Test metadata creation without description."""

        @get("/test", sync_to_thread=False)
        def test_handler() -> dict[str, str]:
            return {"status": "ok"}

        app = Litestar(route_handlers=[test_handler])
        handler = get_handler_from_app(app, "/test", "GET")

        metadata = MCPMetadata(
            type="resource",
            name="test_resource",
            handler_ref=weakref_ref(handler),
        )

        assert metadata.type == "resource"
        assert metadata.name == "test_resource"
        assert metadata.description is None


class TestMCPToolRegistry:
    """Tests for MCPToolRegistry."""

    def test_register_tool(self) -> None:
        """Test registering a tool in registry."""

        @get("/users", sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return [{"id": 1, "name": "Alice"}]

        app = Litestar(route_handlers=[list_users])
        handler = get_handler_from_app(app, "/users", "GET")

        registry = MCPToolRegistry()
        registry.register(handler, "tool", "list_users", "List all users")

        metadata = registry.get_metadata(handler)
        assert metadata is not None
        assert metadata.type == "tool"
        assert metadata.name == "list_users"
        assert metadata.description == "List all users"

    def test_register_resource(self) -> None:
        """Test registering a resource in registry."""

        @get("/config", sync_to_thread=False)
        def get_config() -> dict[str, Any]:
            return {"debug": True}

        app = Litestar(route_handlers=[get_config])
        handler = get_handler_from_app(app, "/config", "GET")

        registry = MCPToolRegistry()
        registry.register(handler, "resource", "app_config")

        metadata = registry.get_metadata(handler)
        assert metadata is not None
        assert metadata.type == "resource"
        assert metadata.name == "app_config"
        assert metadata.description is None

    def test_register_duplicate_name_conflict(self) -> None:
        """Test registering same handler signature with different name raises error."""

        @get("/users", sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return []

        app = Litestar(route_handlers=[list_users])
        handler = get_handler_from_app(app, "/users", "GET")

        registry = MCPToolRegistry()
        registry.register(handler, "tool", "users_list")

        with pytest.raises(ValueError, match="already registered"):
            registry.register(handler, "tool", "different_name")

    def test_register_duplicate_idempotent(self) -> None:
        """Test re-registering same handler with same name is idempotent."""

        @get("/users", sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return []

        app = Litestar(route_handlers=[list_users])
        handler = get_handler_from_app(app, "/users", "GET")

        registry = MCPToolRegistry()
        registry.register(handler, "tool", "users_list")
        registry.register(handler, "tool", "users_list")

        metadata = registry.get_metadata(handler)
        assert metadata is not None
        assert metadata.name == "users_list"

    def test_unregister(self) -> None:
        """Test unregistering a handler."""

        @get("/users", sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return []

        app = Litestar(route_handlers=[list_users])
        handler = get_handler_from_app(app, "/users", "GET")

        registry = MCPToolRegistry()
        registry.register(handler, "tool", "users_list")

        assert registry.get_metadata(handler) is not None

        removed = registry.unregister(handler)
        assert removed is True
        assert registry.get_metadata(handler) is None

    def test_unregister_not_registered(self) -> None:
        """Test unregistering a handler that was never registered."""

        @get("/users", sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return []

        app = Litestar(route_handlers=[list_users])
        handler = get_handler_from_app(app, "/users", "GET")

        registry = MCPToolRegistry()
        removed = registry.unregister(handler)
        assert removed is False

    def test_rebuild_diff(self) -> None:
        """Test rebuild returns added and removed sets."""

        @get("/users", opt={"mcp_tool": "users_list"}, sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return []

        @get("/posts", opt={"mcp_tool": "posts_list"}, sync_to_thread=False)
        def list_posts() -> list[dict[str, Any]]:
            return []

        @get("/comments", opt={"mcp_tool": "comments_list"}, sync_to_thread=False)
        def list_comments() -> list[dict[str, Any]]:
            return []

        handlers1 = [list_users, list_posts]
        registry = MCPToolRegistry()
        registry.rebuild(handlers1)

        handlers2 = [list_posts, list_comments]
        added, removed = registry.rebuild(handlers2)

        assert "comments_list" in added
        assert "users_list" in removed
        assert "posts_list" not in added
        assert "posts_list" not in removed

    def test_list_tools(self) -> None:
        """Test listing all registered tools."""

        @get("/users", opt={"mcp_tool": "users_list"}, sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return []

        @get("/config", opt={"mcp_resource": "app_config"}, sync_to_thread=False)
        def get_config() -> dict[str, Any]:
            return {}

        handlers = [list_users, get_config]
        registry = MCPToolRegistry()
        registry.rebuild(handlers)

        tools = registry.list_tools()
        assert "users_list" in tools
        assert "app_config" not in tools

    def test_list_resources(self) -> None:
        """Test listing all registered resources."""

        @get("/users", opt={"mcp_tool": "users_list"}, sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return []

        @get("/config", opt={"mcp_resource": "app_config"}, sync_to_thread=False)
        def get_config() -> dict[str, Any]:
            return {}

        handlers = [list_users, get_config]
        registry = MCPToolRegistry()
        registry.rebuild(handlers)

        resources = registry.list_resources()
        assert "app_config" in resources
        assert "users_list" not in resources

    def test_get_by_name(self) -> None:
        """Test finding handler by MCP name."""

        @get("/users", opt={"mcp_tool": "users_list"}, sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return []

        handlers = [list_users]
        registry = MCPToolRegistry()
        registry.rebuild(handlers)

        handler = registry.get_by_name("users_list")
        assert handler is not None

        fn = handler.fn.value if hasattr(handler.fn, "value") else handler.fn
        assert fn.__name__ == "list_users"

    def test_get_by_name_not_found(self) -> None:
        """Test get_by_name returns None for unknown name."""
        registry = MCPToolRegistry()
        handler = registry.get_by_name("nonexistent")
        assert handler is None

    def test_get_metadata(self) -> None:
        """Test getting metadata for a handler."""

        @get("/users", sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return []

        app = Litestar(route_handlers=[list_users])
        handler = get_handler_from_app(app, "/users", "GET")

        registry = MCPToolRegistry()
        registry.register(handler, "tool", "users_list", "List users")

        metadata = registry.get_metadata(handler)
        assert metadata is not None
        assert metadata.name == "users_list"
        assert metadata.description == "List users"

    def test_weakref_cleanup(self) -> None:
        """Test that weakrefs are used to avoid memory leaks."""
        registry = MCPToolRegistry()

        @get("/temp", sync_to_thread=False)
        def temp_handler() -> dict[str, str]:
            return {"status": "ok"}

        registry.register(temp_handler, "tool", "temp_tool")

        metadata = registry.get_metadata(temp_handler)
        assert metadata is not None
        assert metadata.handler_ref is not None
        assert metadata.handler_ref() is temp_handler

    def test_thread_safe_registration(self) -> None:
        """Test concurrent registration is thread-safe."""

        registry = MCPToolRegistry(thread_safe=True)
        errors: list[Exception] = []
        handlers: list[BaseRouteHandler] = []
        lock = threading.Lock()

        def register_tool(index: int) -> None:
            try:

                def tool_handler() -> dict[str, int]:
                    return {"value": index}

                tool_handler.__name__ = f"tool_handler_{index}"
                tool_handler.__qualname__ = f"tool_handler_{index}"

                decorated = get(f"/tool{index}", sync_to_thread=False)(tool_handler)

                with lock:
                    handlers.append(decorated)
                registry.register(decorated, "tool", f"tool_{index}")
            except (ValueError, TypeError, AttributeError) as e:
                with lock:
                    errors.append(e)

        threads = [threading.Thread(target=register_tool, args=(i,)) for i in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(errors) == 0, f"Errors occurred: {errors}"

        tools = registry.list_tools()
        assert len(tools) == 10

    def test_thread_safe_disabled(self) -> None:
        """Test registry with thread_safe=False."""

        @get("/users", sync_to_thread=False)
        def list_users() -> list[dict[str, Any]]:
            return []

        app = Litestar(route_handlers=[list_users])
        handler = get_handler_from_app(app, "/users", "GET")

        registry = MCPToolRegistry(thread_safe=False)
        registry.register(handler, "tool", "users_list")

        assert registry._lock is None

        metadata = registry.get_metadata(handler)
        assert metadata is not None
        assert metadata.name == "users_list"

    def test_rebuild_with_mcp_pending(self) -> None:
        """Test rebuild works with _mcp_pending decorator pattern."""

        def list_users() -> list[dict[str, Any]]:
            return []

        list_users._mcp_pending = {"type": "tool", "name": "users_tool", "description": "List users"}  # type: ignore[attr-defined]

        decorated = get("/users", sync_to_thread=False)(list_users)

        handlers = [decorated]
        registry = MCPToolRegistry()
        registry.rebuild(handlers)

        tools = registry.list_tools()
        assert "users_tool" in tools

    def test_rebuild_preserves_description_from_pending(self) -> None:
        """Test rebuild preserves description from _mcp_pending."""

        def list_users() -> list[dict[str, Any]]:
            return []

        list_users._mcp_pending = {"type": "tool", "name": "users_tool", "description": "Custom description"}

        decorated = get("/users", sync_to_thread=False)(list_users)

        handlers = [decorated]
        registry = MCPToolRegistry()
        registry.rebuild(handlers)

        handler = registry.get_by_name("users_tool")
        assert handler is not None
        metadata = registry.get_metadata(handler)
        assert metadata is not None
        assert metadata.description == "Custom description"
