"""Tests for the executor module."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import pytest
from litestar import Litestar, get
from litestar.di import Provide

from litestar_mcp.config import MCPConfig
from litestar_mcp.executor import NotCallableInCLIContextError, execute_tool
from tests.unit.conftest import create_app_with_handler, get_handler_from_app


class TestExecutor:
    """Test suite for executor functionality."""

    @pytest.mark.asyncio
    async def test_execute_sync_handler_basic_types(self) -> None:
        """Test executing a sync handler with basic types."""

        def sync_handler(name: str, age: int = 25) -> dict[str, Any]:
            return {"name": name, "age": age}

        app, handler = create_app_with_handler(sync_handler)

        result = await execute_tool(handler, app, {"name": "Alice", "age": 30})
        assert result == {"name": "Alice", "age": 30}

    @pytest.mark.asyncio
    async def test_execute_async_handler_basic_types(self) -> None:
        """Test executing an async handler with basic types."""

        async def async_handler(message: str, urgent: bool = False) -> dict[str, Any]:
            return {"message": message, "urgent": urgent}

        app, handler = create_app_with_handler(async_handler)

        result = await execute_tool(handler, app, {"message": "Hello", "urgent": True})
        assert result == {"message": "Hello", "urgent": True}

    @pytest.mark.asyncio
    async def test_execute_handler_with_dependencies(self) -> None:
        """Test executing a handler with provided dependencies."""

        def provide_config() -> dict[str, Any]:
            return {"database_url": "sqlite:///:memory:"}

        def handler_with_deps(user_id: int, config: dict[str, Any]) -> dict[str, Any]:
            return {"user_id": user_id, "db": config["database_url"]}

        app, handler = create_app_with_handler(
            handler_with_deps, dependencies={"config": Provide(provide_config, sync_to_thread=False)}
        )

        result = await execute_tool(handler, app, {"user_id": 123})
        assert result == {"user_id": 123, "db": "sqlite:///:memory:"}

    @pytest.mark.asyncio
    async def test_cli_context_limitation_request_dependency(self) -> None:
        """Test that handlers requiring request-scoped dependencies fail in CLI context."""

        def request_dependent_handler(request: Any, user_id: int) -> dict[str, Any]:
            return {"user_id": user_id, "path": request.url.path}

        app, handler = create_app_with_handler(request_dependent_handler)

        # Mock the resolve_dependencies to return request as a dependency
        handler.resolve_dependencies = lambda: {"request": AsyncMock()}  # type: ignore[method-assign]

        with pytest.raises(NotCallableInCLIContextError) as exc_info:
            await execute_tool(handler, app, {"user_id": 123})

        assert "request-scoped dependency 'request'" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_missing_required_arguments(self) -> None:
        """Test that missing required arguments raise ValueError."""

        def handler_with_required_args(name: str, age: int) -> dict[str, Any]:
            return {"name": name, "age": age}

        app, handler = create_app_with_handler(handler_with_required_args)

        with pytest.raises(ValueError) as exc_info:
            await execute_tool(handler, app, {"name": "Alice"})  # Missing 'age'

        assert "Missing required arguments: age" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_optional_parameters_handling(self) -> None:
        """Test that optional parameters are handled correctly."""

        def handler_with_optional(name: str, greeting: str = "Hello") -> str:
            return f"{greeting}, {name}!"

        app, handler = create_app_with_handler(handler_with_optional)

        # Test with optional parameter provided
        result = await execute_tool(handler, app, {"name": "Alice", "greeting": "Hi"})
        assert result == "Hi, Alice!"

        # Test with optional parameter omitted (should use default)
        result = await execute_tool(handler, app, {"name": "Bob"})
        assert result == "Hello, Bob!"

    @pytest.mark.asyncio
    async def test_complex_return_types(self) -> None:
        """Test handlers with complex return types."""

        async def complex_handler(data: dict[str, Any]) -> dict[str, Any]:
            return {
                "processed": True,
                "input": data,
                "summary": {"count": len(data), "keys": list(data.keys())},
            }

        app, handler = create_app_with_handler(complex_handler, method="POST")

        input_data = {"name": "test", "value": 42}
        result = await execute_tool(handler, app, {"data": input_data})

        expected = {
            "processed": True,
            "input": input_data,
            "summary": {"count": 2, "keys": ["name", "value"]},
        }
        assert result == expected

    @pytest.mark.asyncio
    async def test_handler_with_no_parameters(self) -> None:
        """Test executing a handler that takes no parameters."""

        def status_handler() -> dict[str, str]:
            return {"status": "ok", "version": "1.0.0"}

        app, handler = create_app_with_handler(status_handler, route_path="/status")

        result = await execute_tool(handler, app, {})
        assert result == {"status": "ok", "version": "1.0.0"}

    @pytest.mark.asyncio
    async def test_dependency_resolution_failure(self) -> None:
        """Test that dependency resolution failures are handled."""

        def failing_dependency() -> str:
            msg = "Dependency failed"
            raise RuntimeError(msg)

        def handler_with_failing_dep(name: str, config: str) -> dict[str, Any]:
            return {"name": name, "config": config}

        app, handler = create_app_with_handler(
            handler_with_failing_dep, dependencies={"config": Provide(failing_dependency, sync_to_thread=False)}
        )

        # Should raise NotCallableInCLIContextError when dependency resolution fails
        with pytest.raises(NotCallableInCLIContextError):
            await execute_tool(handler, app, {"name": "test"})

    @pytest.mark.asyncio
    async def test_type_coercion_basic(self) -> None:
        """Test basic type handling and argument passing."""

        def typed_handler(count: int, active: bool, name: str) -> dict[str, Any]:
            return {
                "count": count,
                "active": active,
                "name": name,
                "types": [type(count).__name__, type(active).__name__, type(name).__name__],
            }

        app, handler = create_app_with_handler(typed_handler)

        result = await execute_tool(handler, app, {"count": 42, "active": True, "name": "test"})
        assert result == {"count": 42, "active": True, "name": "test", "types": ["int", "bool", "str"]}

    @pytest.mark.asyncio
    async def test_handler_docstring_preservation(self) -> None:
        """Test that handler docstrings are preserved through execution."""

        def documented_handler(value: str) -> str:
            """This is a test handler that returns the input value."""
            return f"Processed: {value}"

        app, handler = create_app_with_handler(documented_handler)

        # The docstring should be accessible
        from litestar_mcp.utils import get_handler_function

        fn = get_handler_function(handler)
        assert fn.__doc__ == "This is a test handler that returns the input value."

        result = await execute_tool(handler, app, {"value": "test"})
        assert result == "Processed: test"

    @pytest.mark.asyncio
    async def test_execute_tool_parameter_validation_error(self) -> None:
        """Test execute_tool handles parameters without type validation."""

        def strict_handler(count: int) -> dict[str, Any]:
            return {"count": count}

        app, handler = create_app_with_handler(strict_handler)

        # Pass invalid type for count parameter - executor doesn't validate types
        result = await execute_tool(handler, app, {"count": "not_an_integer"})
        assert result == {"count": "not_an_integer"}

    @pytest.mark.asyncio
    async def test_execute_tool_missing_required_parameter(self) -> None:
        """Test execute_tool handles missing required parameters."""

        def required_param_handler(name: str) -> dict[str, str]:
            return {"name": name}

        app, handler = create_app_with_handler(required_param_handler)

        # Missing required parameter should raise ValueError
        with pytest.raises(ValueError, match="Missing required arguments: name"):
            await execute_tool(handler, app, {})

    @pytest.mark.asyncio
    async def test_execute_tool_with_request_dependency_error(self) -> None:
        """Test execute_tool handles Request dependency in CLI context."""
        from litestar import Request

        def request_dependent_handler(request: Request[Any, Any, Any], value: str) -> dict[str, Any]:
            return {"path": request.url.path, "value": value}

        app, handler = create_app_with_handler(request_dependent_handler)

        # Should raise NotCallableInCLIContextError
        with pytest.raises(NotCallableInCLIContextError):
            await execute_tool(handler, app, {"value": "test"})

    @pytest.mark.asyncio
    async def test_execute_tool_with_complex_json_parameters(self) -> None:
        """Test execute_tool with complex JSON parameter parsing."""

        def complex_handler(data: dict[str, Any], items: list[str]) -> dict[str, Any]:
            return {"data": data, "items": items, "count": len(items)}

        app, handler = create_app_with_handler(complex_handler, method="POST")

        complex_args = {"data": {"nested": {"value": 42}, "flag": True}, "items": ["item1", "item2", "item3"]}

        result = await execute_tool(handler, app, complex_args)
        assert result["data"] == complex_args["data"]
        assert result["items"] == complex_args["items"]
        assert result["count"] == 3

    @pytest.mark.asyncio
    async def test_execute_tool_ignores_unused_app_dependencies(self) -> None:
        """Unused app dependencies should not be resolved for MCP tools."""
        dependency_calls: list[str] = []

        def provide_db_session() -> str:
            dependency_calls.append("called")
            msg = "request-scoped dependency should not be resolved"
            raise RuntimeError(msg)

        @get("/users", sync_to_thread=False)
        def list_users(limit: int = 10) -> dict[str, int]:
            return {"limit": limit}

        app = Litestar(
            route_handlers=[list_users],
            dependencies={"db_session": Provide(provide_db_session, sync_to_thread=False)},
        )
        handler = get_handler_from_app(app, "/users")

        result = await execute_tool(handler, app, {"limit": 5})

        assert result == {"limit": 5}
        assert dependency_calls == []

    @pytest.mark.asyncio
    async def test_execute_tool_dependency_provider_injects_scoped_dependencies(self) -> None:
        """A dependency provider hook should inject scoped resources for tool execution."""
        lifecycle_events: list[str] = []

        def provide_db_session() -> str:
            msg = "request-scoped dependency should be satisfied by dependency_provider"
            raise RuntimeError(msg)

        @get("/reports", sync_to_thread=False)
        def load_report(db_session: str) -> dict[str, str]:
            return {"db_session": db_session}

        app = Litestar(
            route_handlers=[load_report],
            dependencies={"db_session": Provide(provide_db_session, sync_to_thread=False)},
        )
        handler = get_handler_from_app(app, "/reports")

        @asynccontextmanager
        async def dependency_provider(context: Any) -> AsyncIterator[dict[str, str]]:
            lifecycle_events.append(f"enter:{context.handler.name}")
            yield {"db_session": "scoped-session"}
            lifecycle_events.append("exit")

        result = await execute_tool(
            handler,
            app,
            {},
            config=MCPConfig(dependency_provider=dependency_provider),
        )

        assert result == {"db_session": "scoped-session"}
        assert lifecycle_events == [f"enter:{handler.name}", "exit"]

    @pytest.mark.asyncio
    async def test_execute_tool_injects_authenticated_context(self) -> None:
        """Resolved auth context should be available to tool execution."""

        def who_am_i(resolved_user: dict[str, Any], user_claims: dict[str, Any]) -> dict[str, Any]:
            return {
                "user_id": resolved_user["id"],
                "subject": user_claims["sub"],
            }

        app, handler = create_app_with_handler(who_am_i)

        result = await execute_tool(
            handler,
            app,
            {},
            user_claims={"sub": "user-1"},
            resolved_user={"id": "user-1"},
        )

        assert result == {"user_id": "user-1", "subject": "user-1"}


class TestNotCallableInCLIContextError:
    """Test suite for NotCallableInCLIContextError exception."""

    def test_not_callable_in_cli_context_error_creation(self) -> None:
        """Test creation of NotCallableInCLIContextError."""
        param_name = "request"
        param_type = "Request"

        error = NotCallableInCLIContextError(param_name, param_type)

        assert param_name in str(error)
        assert param_type in str(error)
        assert "CLI context" in str(error)

    def test_not_callable_in_cli_context_error_with_different_types(self) -> None:
        """Test NotCallableInCLIContextError with different parameter types."""
        test_cases = [
            ("connection", "ASGIConnection"),
            ("response", "Response"),
            ("websocket", "WebSocket"),
        ]

        for param_name, param_type in test_cases:
            error = NotCallableInCLIContextError(param_name, param_type)
            assert param_name in str(error)
            assert param_type in str(error)
