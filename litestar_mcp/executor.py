"""Core execution logic for invoking MCP tools."""

import inspect
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from litestar import Litestar
from litestar.exceptions import ImproperlyConfiguredException
from litestar.handlers.base import BaseRouteHandler
from litestar.utils.helpers import get_name
from litestar.utils.sync import ensure_async_callable

from litestar_mcp.typing import schema_dump
from litestar_mcp.utils import get_handler_function

if TYPE_CHECKING:
    from collections.abc import Callable

    from litestar_mcp.config import MCPConfig

_UNSUPPORTED_CLI_DEPENDENCIES = {"request", "socket", "headers", "cookies", "query", "body"}
_EXECUTION_CONTEXT_PARAMS = {"resolved_user", "user_claims"}


@dataclass
class ToolExecutionContext:
    """Context exposed to dependency providers during tool execution."""

    app: Litestar
    handler: BaseRouteHandler
    tool_args: dict[str, Any]
    user_claims: "dict[str, Any] | None" = None
    resolved_user: Any = None


def _check_unsupported_dependency(dep_name: str, fn: Any) -> None:
    """Check if a dependency is unsupported in CLI context and raise error if so."""
    if dep_name in _UNSUPPORTED_CLI_DEPENDENCIES:
        raise NotCallableInCLIContextError(get_name(fn), dep_name)


async def _call_dependency_provider(dep_provider: Any) -> Any:
    if hasattr(dep_provider, "dependency"):
        dependency_fn = dep_provider.dependency
        if inspect.iscoroutinefunction(dependency_fn):
            return await dependency_fn()
        return dependency_fn()

    if inspect.iscoroutinefunction(dep_provider):
        return await dep_provider()

    if callable(dep_provider):
        return dep_provider()

    return dep_provider


@asynccontextmanager
async def _async_generator_dependency_context(generator: Any) -> Any:
    """Adapt an async generator dependency into an async context manager."""
    try:
        value = await anext(generator)
    except StopAsyncIteration as exc:
        msg = "Async generator dependency did not yield a value"
        raise RuntimeError(msg) from exc

    try:
        yield value
    finally:
        try:
            await anext(generator)
        except StopAsyncIteration:
            return

        msg = "Async generator dependency yielded more than one value"
        raise RuntimeError(msg)


@contextmanager
def _generator_dependency_context(generator: Any) -> Any:
    """Adapt a generator dependency into a context manager."""
    try:
        value = next(generator)
    except StopIteration as exc:
        msg = "Generator dependency did not yield a value"
        raise RuntimeError(msg) from exc

    try:
        yield value
    finally:
        try:
            next(generator)
        except StopIteration:
            return

        msg = "Generator dependency yielded more than one value"
        raise RuntimeError(msg)


def _get_dependency_signature(dep_provider: Any) -> inspect.Signature:
    """Return the callable signature for a dependency provider."""
    provider_fn = dep_provider.dependency if hasattr(dep_provider, "dependency") else dep_provider
    try:
        return inspect.signature(provider_fn)
    except (TypeError, ValueError):
        return inspect.Signature()


async def _invoke_dependency_provider(
    dep_provider: Any,
    provider_kwargs: "dict[str, Any]",
    exit_stack: AsyncExitStack,
) -> Any:
    """Invoke a dependency provider, entering generator dependencies when needed."""
    if hasattr(dep_provider, "dependency"):
        dependency_fn = dep_provider.dependency
        if getattr(dep_provider, "has_async_generator_dependency", False):
            generator = dependency_fn(**provider_kwargs)
            return await exit_stack.enter_async_context(_async_generator_dependency_context(generator))
        if getattr(dep_provider, "has_sync_generator_dependency", False):
            generator = dependency_fn(**provider_kwargs)
            return exit_stack.enter_context(_generator_dependency_context(generator))
        return await dep_provider(**provider_kwargs)

    if inspect.isasyncgenfunction(dep_provider):
        return await exit_stack.enter_async_context(_async_generator_dependency_context(dep_provider(**provider_kwargs)))

    if inspect.isgeneratorfunction(dep_provider):
        return exit_stack.enter_context(_generator_dependency_context(dep_provider(**provider_kwargs)))

    if inspect.iscoroutinefunction(dep_provider):
        return await dep_provider(**provider_kwargs)

    if callable(dep_provider):
        return dep_provider(**provider_kwargs)

    return dep_provider


async def _resolve_dependency_value(
    *,
    dep_name: str,
    dep_provider: Any,
    fn: Any,
    resolved_deps: "dict[str, Any]",
    resolved_values: "dict[str, Any]",
    tool_args: "dict[str, Any]",
    exit_stack: AsyncExitStack,
    in_progress: "set[str]",
) -> Any:
    """Recursively resolve a dependency value and its transitive inputs."""
    if dep_name in resolved_values:
        return resolved_values[dep_name]

    if dep_name in in_progress:
        msg = f"Circular dependency detected while resolving '{dep_name}'"
        raise RuntimeError(msg)

    in_progress.add(dep_name)
    try:
        provider_kwargs: dict[str, Any] = {}
        provider_signature = _get_dependency_signature(dep_provider)
        for parameter_name, parameter in provider_signature.parameters.items():
            if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
                continue

            if parameter_name in resolved_values:
                provider_kwargs[parameter_name] = resolved_values[parameter_name]
                continue

            if parameter_name in tool_args:
                provider_kwargs[parameter_name] = tool_args[parameter_name]
                continue

            if parameter_name in resolved_deps:
                _check_unsupported_dependency(parameter_name, fn)
                provider_kwargs[parameter_name] = await _resolve_dependency_value(
                    dep_name=parameter_name,
                    dep_provider=resolved_deps[parameter_name],
                    fn=fn,
                    resolved_deps=resolved_deps,
                    resolved_values=resolved_values,
                    tool_args=tool_args,
                    exit_stack=exit_stack,
                    in_progress=in_progress,
                )
                continue

            if parameter.default is inspect.Parameter.empty:
                msg = f"Missing dependency input: {parameter_name}"
                raise ValueError(msg)

        value = await _invoke_dependency_provider(dep_provider, provider_kwargs, exit_stack)
        resolved_values[dep_name] = value
        return value
    finally:
        in_progress.remove(dep_name)


async def _resolve_dependencies(
    handler: BaseRouteHandler,
    fn: Any,
    signature: inspect.Signature,
    pre_resolved_dependencies: "dict[str, Any]",
    tool_args: "dict[str, Any]",
    exit_stack: AsyncExitStack,
) -> "dict[str, Any]":
    """Resolve only the dependencies that the handler actually consumes."""
    consumed_dependency_names = set(signature.parameters).difference(pre_resolved_dependencies)
    dependencies: dict[str, Any] = {}
    resolved_values = dict(pre_resolved_dependencies)

    try:
        resolved_deps = handler.resolve_dependencies()
    except NotCallableInCLIContextError:
        raise
    except (AttributeError, TypeError):
        return {}

    for dep_name, dep_provider in resolved_deps.items():
        if dep_name not in consumed_dependency_names:
            continue

        _check_unsupported_dependency(dep_name, fn)

        try:
            dependencies[dep_name] = await _resolve_dependency_value(
                dep_name=dep_name,
                dep_provider=dep_provider,
                fn=fn,
                resolved_deps=resolved_deps,
                resolved_values=resolved_values,
                tool_args=tool_args,
                exit_stack=exit_stack,
                in_progress=set(),
            )
        except Exception as exc:
            raise NotCallableInCLIContextError(get_name(fn), dep_name) from exc

    return dependencies


async def _resolve_dependency_provider(
    config: "MCPConfig | None",
    context: ToolExecutionContext,
    exit_stack: AsyncExitStack,
) -> "dict[str, Any]":
    """Resolve context-managed dependencies from the configured provider hook."""
    if config is None or config.dependency_provider is None:
        return {}

    provided = config.dependency_provider(context)
    if hasattr(provided, "__aenter__"):
        resolved = await exit_stack.enter_async_context(provided)
    elif hasattr(provided, "__enter__"):
        resolved = exit_stack.enter_context(provided)
    elif hasattr(provided, "__await__"):
        resolved = await provided
    else:
        resolved = provided

    if resolved is None:
        return {}
    if not isinstance(resolved, dict):
        msg = "dependency_provider must return a mapping of injected keyword arguments"
        raise TypeError(msg)
    return resolved


def _inject_execution_context(
    signature: inspect.Signature,
    *,
    user_claims: "dict[str, Any] | None",
    resolved_user: Any,
) -> "dict[str, Any]":
    """Inject reserved execution context parameters when the handler requests them."""
    injected: dict[str, Any] = {}

    if "user_claims" in signature.parameters and user_claims is not None:
        injected["user_claims"] = user_claims
    if "resolved_user" in signature.parameters and resolved_user is not None:
        injected["resolved_user"] = resolved_user

    return injected


def _create_cli_scope(app: Litestar) -> "dict[str, Any]":
    """Create a minimal synthetic ASGI scope for CLI-driven dependency resolution."""
    return {"type": "http", "app": app, "state": {}}


def _iter_scope_resources(scope: "dict[str, Any]", *, root: bool = False) -> "list[Any]":
    """Collect resources stored inside the synthetic scope for later cleanup."""
    resources: list[Any] = []
    reserved_root_keys = {"type", "app", "state"}

    for key, value in scope.items():
        if root and key in reserved_root_keys:
            continue
        if isinstance(value, dict):
            resources.extend(_iter_scope_resources(value))
        else:
            resources.append(value)
    return resources


def _register_scope_cleanup(scope: "dict[str, Any]", exit_stack: AsyncExitStack) -> None:
    """Register cleanup callbacks for closable resources stored in the synthetic scope."""
    seen: set[int] = set()
    for resource in _iter_scope_resources(scope, root=True):
        resource_id = id(resource)
        if resource_id in seen:
            continue
        seen.add(resource_id)

        aclose = getattr(resource, "aclose", None)
        if callable(aclose):
            exit_stack.push_async_callback(aclose)
            continue

        close = getattr(resource, "close", None)
        if callable(close):
            if inspect.iscoroutinefunction(close):
                exit_stack.push_async_callback(close)
            else:
                exit_stack.callback(close)


class NotCallableInCLIContextError(ImproperlyConfiguredException):
    """Raised when a tool is not callable from the CLI due to its dependencies."""

    def __init__(self, handler_name: str, parameter_name: str) -> None:
        """Initialize the exception.

        Args:
            handler_name: Name of the handler that cannot be called.
            parameter_name: Name of the parameter causing the issue.
        """
        super().__init__(
            f"Tool '{handler_name}' cannot be called from the CLI because it depends on the request-scoped "
            f"dependency '{parameter_name}', which is not available in a CLI context."
        )


async def execute_tool(
    handler: BaseRouteHandler,
    app: Litestar,
    tool_args: "dict[str, Any]",
    *,
    config: "MCPConfig | None" = None,
    user_claims: "dict[str, Any] | None" = None,
    resolved_user: Any = None,
) -> Any:
    """Execute a given route handler with arguments, handling dependency injection.

    Args:
        handler: The route handler to execute.
        app: The Litestar app instance.
        tool_args: A dictionary of arguments to pass to the tool.
        config: Optional MCP configuration for advanced dependency injection hooks.
        user_claims: Optional validated user claims.
        resolved_user: Optional user object derived from the claims.

    Returns:
        The result of the handler execution.

    Raises:
        ValueError: If required arguments are missing.
    """
    try:
        fn: Callable[..., Any] = get_handler_function(handler)
    except AttributeError:
        fn = cast("Callable[..., Any]", handler)

    signature = inspect.signature(fn)
    cli_scope = _create_cli_scope(app)
    dependency_context = {"state": app.state, "scope": cli_scope}
    call_args = _inject_execution_context(signature, user_claims=user_claims, resolved_user=resolved_user)

    for parameter_name in signature.parameters:
        _check_unsupported_dependency(parameter_name, fn)

    async with AsyncExitStack() as exit_stack:
        scope_cleanup_stack = AsyncExitStack()
        await exit_stack.enter_async_context(scope_cleanup_stack)
        execution_context = ToolExecutionContext(
            app=app,
            handler=handler,
            tool_args=tool_args,
            user_claims=user_claims,
            resolved_user=resolved_user,
        )
        provided_dependencies = await _resolve_dependency_provider(config, execution_context, exit_stack)
        call_args.update(provided_dependencies)

        resolved_inputs = {**dependency_context, **call_args}
        dependencies = await _resolve_dependencies(handler, fn, signature, resolved_inputs, tool_args, exit_stack)
        call_args.update(dependencies)
        _register_scope_cleanup(cli_scope, scope_cleanup_stack)

        for parameter_name in signature.parameters:
            if parameter_name in call_args or parameter_name in _EXECUTION_CONTEXT_PARAMS:
                continue
            if parameter_name in tool_args:
                call_args[parameter_name] = tool_args[parameter_name]

        required_params = {
            parameter_name
            for parameter_name, parameter in signature.parameters.items()
            if parameter.default is inspect.Parameter.empty and parameter_name not in call_args
        }
        if required_params:
            missing_args = ", ".join(sorted(required_params))
            msg = f"Missing required arguments: {missing_args}"
            raise ValueError(msg)

        if getattr(handler, "sync_to_thread", False):
            async_fn = ensure_async_callable(fn)
            result = await async_fn(**call_args)
        elif inspect.iscoroutinefunction(fn):
            result = await fn(**call_args)
        else:
            result = fn(**call_args)

    if not isinstance(result, (str, int, float, bool, list, dict, type(None))):
        return schema_dump(result)

    return result
