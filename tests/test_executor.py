"""Tests for the executor module."""

from typing import Any

import pytest
from litestar import Litestar, get
from litestar.di import Provide

from litestar_mcp.executor import NotCallableInCLIContextError, execute_tool
from tests.conftest import create_app_with_handler, get_handler_from_app


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
        """Handlers declaring request-scoped framework kwargs fail in MCP context.

        Litestar's KwargsModel marks `request` (and friends) as reserved
        kwargs, which the executor then detects via expected_reserved_kwargs
        — no monkey-patching needed.
        """
        from litestar import Request

        def request_dependent_handler(request: Request[Any, Any, Any], user_id: int) -> dict[str, Any]:
            return {"user_id": user_id, "path": request.url.path}

        app, handler = create_app_with_handler(request_dependent_handler)

        with pytest.raises(NotCallableInCLIContextError) as exc_info:
            await execute_tool(handler, app, {"user_id": 123})

        assert "request" in str(exc_info.value)
        assert "request context" in str(exc_info.value)

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
    async def test_unused_plugin_dependency_is_not_resolved(self) -> None:
        """Regression for #19: app-level deps not consumed by handler must not be called.

        Reproduces the SQLAlchemyPlugin scenario: an app-level `db_engine` dep is
        registered, but the handler doesn't declare it. The provider must NOT be
        invoked, so the tool can execute successfully.
        """
        provider_was_called = False

        def broken_db_engine() -> str:
            nonlocal provider_was_called
            provider_was_called = True
            msg = "db_engine should never be called — handler doesn't consume it"
            raise RuntimeError(msg)

        @get("/health", sync_to_thread=False)
        def health_check() -> dict[str, str]:
            return {"status": "ok"}

        app = Litestar(
            route_handlers=[health_check],
            dependencies={"db_engine": Provide(broken_db_engine, sync_to_thread=False)},
        )
        handler = get_handler_from_app(app, "/health", "GET")

        result = await execute_tool(handler, app, {})

        assert result == {"status": "ok"}
        assert provider_was_called is False, "db_engine provider was called despite handler not declaring it"

    @pytest.mark.asyncio
    async def test_unused_plugin_dependency_with_handler_args(self) -> None:
        """Same registry as #19 scenario, but handler also takes a tool arg.

        Ensures the fix doesn't accidentally drop or rename real parameters.
        """

        def broken_db_engine() -> str:
            msg = "should not be called"
            raise RuntimeError(msg)

        @get("/greet", sync_to_thread=False)
        def greet(name: str) -> dict[str, str]:
            return {"hello": name}

        app = Litestar(
            route_handlers=[greet],
            dependencies={"db_engine": Provide(broken_db_engine, sync_to_thread=False)},
        )
        handler = get_handler_from_app(app, "/greet", "GET")

        result = await execute_tool(handler, app, {"name": "Alice"})
        assert result == {"hello": "Alice"}

    @pytest.mark.asyncio
    async def test_transitive_dependency_resolution(self) -> None:
        """A handler consuming `user` transitively pulls in `role`.

        Verifies the executor walks provider signatures, not just the handler's.
        """

        def provide_role() -> str:
            return "admin"

        def provide_user(role: str) -> dict[str, str]:
            return {"name": "Alice", "role": role}

        @get("/me", sync_to_thread=False)
        def me(user: dict[str, str]) -> dict[str, str]:
            return user

        app = Litestar(
            route_handlers=[me],
            dependencies={
                "role": Provide(provide_role, sync_to_thread=False),
                "user": Provide(provide_user, sync_to_thread=False),
            },
        )
        handler = get_handler_from_app(app, "/me", "GET")

        result = await execute_tool(handler, app, {})
        assert result == {"name": "Alice", "role": "admin"}

    @pytest.mark.asyncio
    async def test_diamond_dependency_resolution(self) -> None:
        """Diamond graph: handler → a; a → b, c; b → d; c → d.

        Stresses two properties of ``_call_provider_without_connection``:

        1. ``d`` must be resolved exactly once (not once per consumer), so the
           call-count assertion below catches any accidental re-invocation.
        2. ``provide_a`` declares *two* kwargs (``b`` and ``c``) — the
           ``elif field.has_default: continue`` branch must not false-
           positive here and wrongly raise ``NotCallableInCLIContextError``.

        Together these prove ``KwargsModel.dependency_batches`` gives us a
        correct topological order for non-linear graphs, not just chains.
        """
        call_counts: dict[str, int] = {"a": 0, "b": 0, "c": 0, "d": 0}

        def provide_d() -> str:
            call_counts["d"] += 1
            return "d-value"

        def provide_b(d: str) -> str:
            call_counts["b"] += 1
            return f"b({d})"

        def provide_c(d: str) -> str:
            call_counts["c"] += 1
            return f"c({d})"

        def provide_a(b: str, c: str) -> dict[str, str]:
            call_counts["a"] += 1
            return {"b": b, "c": c}

        @get("/diamond", sync_to_thread=False)
        def diamond(a: dict[str, str]) -> dict[str, str]:
            return a

        app = Litestar(
            route_handlers=[diamond],
            dependencies={
                "a": Provide(provide_a, sync_to_thread=False),
                "b": Provide(provide_b, sync_to_thread=False),
                "c": Provide(provide_c, sync_to_thread=False),
                "d": Provide(provide_d, sync_to_thread=False),
            },
        )
        handler = get_handler_from_app(app, "/diamond", "GET")

        result = await execute_tool(handler, app, {})

        assert result == {"b": "b(d-value)", "c": "c(d-value)"}
        assert call_counts == {"a": 1, "b": 1, "c": 1, "d": 1}, (
            f"each provider must run exactly once; got {call_counts}"
        )

    @pytest.mark.asyncio
    async def test_consumed_request_scoped_dep_raises_clear_error(self) -> None:
        """When a handler actually declares a request-scoped dep, the error
        must name the dep and mention request context.
        """

        def broken_db_session() -> str:
            msg = "needs a real request scope"
            raise RuntimeError(msg)

        @get("/items", sync_to_thread=False)
        def list_items(db_session: str) -> dict[str, str]:
            return {"db": db_session}

        app = Litestar(
            route_handlers=[list_items],
            dependencies={"db_session": Provide(broken_db_session, sync_to_thread=False)},
        )
        handler = get_handler_from_app(app, "/items", "GET")

        with pytest.raises(NotCallableInCLIContextError) as exc_info:
            await execute_tool(handler, app, {})

        message = str(exc_info.value)
        assert "db_session" in message
        assert "request context" in message

    @pytest.mark.asyncio
    async def test_generator_dependency_is_rejected(self) -> None:
        """Generator-based providers are rejected upfront without running.

        A generator provider expresses a setup/teardown lifecycle
        (``yield resource`` → cleanup). Outside a real request scope we
        have no way to drive the teardown half deterministically, so we
        must refuse to invoke these providers at all. Verifies that:

        1. The provider is NEVER called (no setup side-effects leak).
        2. The error names the offending dependency.
        3. The error mentions "request context" so users understand why.
        """
        setup_was_called = False

        def provide_session() -> Any:
            nonlocal setup_was_called
            setup_was_called = True
            yield "session-value"

        @get("/items", sync_to_thread=False)
        def list_items(session: str) -> dict[str, str]:
            return {"db": session}

        app = Litestar(
            route_handlers=[list_items],
            dependencies={"session": Provide(provide_session)},
        )
        handler = get_handler_from_app(app, "/items", "GET")

        with pytest.raises(NotCallableInCLIContextError) as exc_info:
            await execute_tool(handler, app, {})

        message = str(exc_info.value)
        assert "session" in message
        assert "request context" in message
        assert setup_was_called is False, "generator provider setup ran — it must be rejected before invocation"

    @pytest.mark.asyncio
    async def test_cli_path_still_rejects_request_injection(self) -> None:
        """CLI path must still reject handlers declaring request: Request.

        The HTTP path (Task 4) allows it, but no connection is available in
        the CLI, so the reserved-kwargs gate should fire.
        """
        from litestar import Request

        def needs_request(request: Request[Any, Any, Any]) -> dict[str, Any]:
            return {"path": request.url.path}

        app, handler = create_app_with_handler(needs_request)

        with pytest.raises(NotCallableInCLIContextError) as exc_info:
            await execute_tool(handler, app, {})  # no connection → CLI path

        assert "request" in str(exc_info.value)


class TestNotCallableInCLIContextError:
    """Test suite for NotCallableInCLIContextError exception."""

    def test_not_callable_in_cli_context_error_creation(self) -> None:
        """Test creation of NotCallableInCLIContextError."""
        handler_name = "list_items"
        param_name = "db_session"

        error = NotCallableInCLIContextError(handler_name, param_name)

        assert handler_name in str(error)
        assert param_name in str(error)
        assert "request context" in str(error)

    def test_not_callable_in_cli_context_error_with_different_types(self) -> None:
        """Test NotCallableInCLIContextError with different parameter types."""
        test_cases = [
            ("list_items", "db_session"),
            ("get_user", "current_user"),
            ("health", "connection"),
        ]

        for handler_name, param_name in test_cases:
            error = NotCallableInCLIContextError(handler_name, param_name)
            assert handler_name in str(error)
            assert param_name in str(error)
            assert "request context" in str(error)
