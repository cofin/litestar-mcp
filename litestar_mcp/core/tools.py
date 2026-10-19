"""Core tool abstraction with type-based context injection, universal DI, and msgspec argument validation."""

import functools
import inspect
from collections.abc import Callable, Mapping, Sequence
from contextlib import AsyncExitStack, asynccontextmanager, contextmanager
from contextvars import ContextVar
from types import UnionType
from typing import Annotated, Any, Union, cast, get_args, get_origin, get_type_hints

import anyio
import anyio.to_thread
import msgspec
from litestar import Request
from litestar.connection import ASGIConnection
from litestar.di import Provide
from litestar.exceptions import ImproperlyConfiguredException
from litestar.serialization import decode_json, encode_json, get_serializer

from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.exceptions import LitestarMCPError
from litestar_mcp.core.schema_builder import hoist_definitions, type_to_json_schema
from litestar_mcp.utils.handler_signature import (
    DIParameterSpec,
    extract_di_parameter,
    has_dishka_parameters,
)

__all__ = (
    "DIParameterSpec",
    "Tool",
    "ToolArgumentError",
    "ToolCall",
    "ToolResult",
    "current_tool_context",
    "execute_tool_calls",
    "extract_di_parameter",
    "has_dishka_parameters",
    "tool",
)

_current_tool_context: ContextVar[ToolContext | None] = ContextVar("litestar_mcp_current_tool_context", default=None)


class ToolCall(msgspec.Struct, kw_only=True):
    """A model-requested tool invocation."""

    name: "str"
    call_id: "str"
    arguments: "dict[str, Any]" = msgspec.field(default_factory=dict)
    provider_metadata: "dict[str, dict[str, Any]]" = msgspec.field(default_factory=dict)


class ToolResult(msgspec.Struct, kw_only=True):
    """The JSON-compatible outcome of one tool invocation."""

    call_id: "str"
    name: "str"
    content: "Any"
    is_error: "bool" = False

    @property
    def result(self) -> "Any":
        """Alias for content."""
        return self.content

    @property
    def error(self) -> "str | None":
        """Error string if invocation failed, or None."""
        if not self.is_error:
            return None
        if isinstance(self.content, dict) and "error" in self.content:
            return str(self.content["error"])
        return str(self.content)


class ToolArgumentError(LitestarMCPError, ValueError):
    """Raised when model-supplied arguments fail validation against the tool's argument model."""


class Tool:
    """Executable tool with a JSON Schema, msgspec argument model, and universal DI resolution."""

    __slots__ = (
        "_args_model",
        "_di_injected",
        "_injected",
        "bound_instance",
        "dependencies",
        "description",
        "fn",
        "name",
        "parameters",
    )

    name: str
    description: str
    parameters: dict[str, Any]
    bound_instance: Any | None
    fn: Callable[..., Any]
    dependencies: dict[str, Any]
    _di_injected: dict[str, DIParameterSpec]

    def __init__(
        self,
        fn: "Callable[..., Any]",
        *,
        name: "str | None" = None,
        description: "str | None" = None,
        dependencies: "Mapping[str, Any] | None" = None,
        bound_instance: "Any | None" = None,
        _args_model: "type[Any] | None" = None,
        _injected: "dict[str, Callable[[ToolContext], Any]] | None" = None,
        _di_injected: "dict[str, DIParameterSpec] | None" = None,
        parameters: "dict[str, Any] | None" = None,
        _enclosing_dependencies: "Mapping[str, Any] | None" = None,
    ) -> "None":
        self.fn = fn
        self.bound_instance = bound_instance
        self.name = str(name or getattr(fn, "__name__", "tool"))
        resolved_desc = description if description is not None else getattr(fn, "__doc__", None)
        self.description = (resolved_desc or "").strip()
        self.dependencies = dict(dependencies) if dependencies is not None else {}

        if parameters is not None and _injected is not None:
            self._args_model = _args_model
            self._injected = _injected
            self._di_injected = dict(_di_injected or {})
            self.parameters = parameters
            return

        effective_deps: dict[str, Any] = {}
        if _enclosing_dependencies:
            effective_deps.update(_enclosing_dependencies)
        effective_deps.update(self.dependencies)

        param_schema, args_model, injected, di_injected = self._build_tool_schema(
            self.name,
            fn,
            explicit_dependencies=effective_deps,
            defer_unknown_types="self" in inspect.signature(fn).parameters,
        )
        self.parameters = param_schema
        self._args_model = args_model
        self._injected = injected
        self._di_injected = di_injected

    def __get__(self, instance: "Any", owner: "type[Any] | None" = None) -> "Tool":
        """Support descriptor binding when a tool is accessed on a controller or class instance."""
        if instance is None:
            return self
        return Tool(
            self.fn,
            name=self.name,
            description=self.description,
            dependencies=self.dependencies,
            bound_instance=instance,
            _args_model=self._args_model,
            _injected=self._injected,
            _di_injected=self._di_injected,
            parameters=self.parameters,
        )

    def __call__(self, *args: "Any", **kwargs: "Any") -> "Any":
        """Invoke the underlying callable directly."""
        if self.bound_instance is not None:
            return self.fn(self.bound_instance, *args, **kwargs)
        return self.fn(*args, **kwargs)

    @property
    def stripped_parameters(self) -> "set[str]":
        """Names of parameters injected from context or DI rather than passed by the caller."""
        return set(self._injected.keys()) | set(self._di_injected.keys())

    @property
    def di_parameters(self) -> "dict[str, DIParameterSpec]":
        """Return mapping of DI-injected parameter names to their specifications."""
        return dict(self._di_injected)

    @property
    def input_schema(self) -> "dict[str, Any]":
        """JSON Schema representing tool inputs."""
        return self.parameters

    def register_dependencies(self, controller_dependencies: "Mapping[str, Any]") -> "None":
        """Merge controller-level dependencies and rebuild the tool schema."""
        merged = dict(controller_dependencies)
        merged.update(self.dependencies)
        param_schema, args_model, injected, di_injected = self._build_tool_schema(
            self.name,
            self.fn,
            explicit_dependencies=merged,
            defer_unknown_types=False,
        )
        self.parameters = param_schema
        self._args_model = args_model
        self._injected = injected
        self._di_injected = di_injected

    @classmethod
    def from_schema(
        cls,
        name: "str",
        description: "str | None" = None,
        parameters: "dict[str, Any] | None" = None,
        call: "Callable[..., Any] | None" = None,
    ) -> "Tool":
        """Construct a tool instance from an explicit JSON schema and callable."""
        param_schema = dict(parameters) if parameters is not None else {"type": "object", "properties": {}}
        if call is None:

            async def _noop(**_: Any) -> None:
                return None

            target_call = _noop
        else:
            target_call = call
        return cls(
            target_call,
            name=name,
            description=description,
            parameters=param_schema,
            _args_model=None,
            _injected={},
            _di_injected={},
        )

    def to_json_schema(self) -> "dict[str, Any]":
        """Return the JSON schema representation exposed to LLMs."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    async def execute(
        self,
        arguments: "Mapping[str, Any] | None" = None,
        *,
        context: "ToolContext | None" = None,
    ) -> "Any":
        """Validate arguments, inject context and DI parameters, and execute the tool."""
        raw_args = dict(arguments or {})
        pre_resolved_di: dict[str, Any] = {}
        for di_name in self._di_injected:
            if di_name in raw_args:
                pre_resolved_di[di_name] = raw_args.pop(di_name)

        if self._args_model is not None:
            try:
                converted = msgspec.convert(raw_args, self._args_model, strict=False)
            except (msgspec.ValidationError, msgspec.DecodeError) as exc:
                msg = f"Failed to validate arguments: {exc}"
                raise ToolArgumentError(msg) from exc
            kwargs = dict(msgspec.structs.asdict(converted))
        else:
            kwargs = raw_args

        kwargs.update(pre_resolved_di)
        effective_ctx = context or ToolContext()
        for param_name, injector in self._injected.items():
            kwargs[param_name] = injector(effective_ctx)

        callable_fn = functools.partial(self.fn, self.bound_instance) if self.bound_instance is not None else self.fn

        async with AsyncExitStack() as exit_stack:
            unresolved_specs = [spec for name, spec in self._di_injected.items() if name not in kwargs]
            if unresolved_specs:
                resolved_di = await _resolve_di_parameters(
                    unresolved_specs,
                    effective_ctx,
                    exit_stack,
                    tool_name=self.name,
                )
                kwargs.update(resolved_di)

            token = _current_tool_context.set(effective_ctx)
            try:
                if inspect.iscoroutinefunction(self.fn):
                    result = await callable_fn(**kwargs)
                else:
                    result = await anyio.to_thread.run_sync(functools.partial(callable_fn, **kwargs))
                if inspect.iscoroutine(result):
                    result = await result
            finally:
                _current_tool_context.reset(token)

        serializer = get_serializer(effective_ctx.type_encoders) if effective_ctx.type_encoders else None
        return decode_json(encode_json(result, serializer=serializer))

    @classmethod
    def _build_tool_schema(
        cls,
        name: str,
        fn: "Callable[..., Any]",
        *,
        explicit_dependencies: "Mapping[str, Any] | None" = None,
        defer_unknown_types: bool = False,
    ) -> "tuple[dict[str, Any], type[Any] | None, dict[str, Callable[[ToolContext], Any]], dict[str, DIParameterSpec]]":
        """Build JSON Schema, typed arguments model, and injected dependencies for a tool callable."""
        try:
            type_hints = get_type_hints(fn, include_extras=True)
        except NameError as exc:
            msg = f"Tool {getattr(fn, '__qualname__', name)!r} has unresolvable annotations: {exc}"
            raise ImproperlyConfiguredException(msg) from exc
        except (TypeError, ValueError):
            type_hints = getattr(fn, "__annotations__", {})

        sig = inspect.signature(fn)
        injected: dict[str, Callable[[ToolContext], Any]] = {}
        di_injected: dict[str, DIParameterSpec] = {}
        properties: dict[str, Any] = {}
        required: list[str] = []
        fields: list[tuple[str, Any] | tuple[str, Any, Any]] = []

        for param in sig.parameters.values():
            if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
                continue
            if param.name == "self":
                continue

            hint = type_hints.get(param.name, param.annotation)
            if hint is inspect.Parameter.empty:
                hint = Any

            if is_injected_parameter(hint):
                injected[param.name] = _resolve_injector(hint)
                continue

            di_spec = extract_di_parameter(
                param.name,
                hint,
                default=param.default,
                explicit_dependencies=explicit_dependencies,
            )
            if di_spec is not None:
                di_injected[param.name] = di_spec
                continue

            prop_schema = type_to_json_schema(hint)
            properties[param.name] = prop_schema
            if param.default is inspect.Parameter.empty:
                required.append(param.name)
                fields.append((param.name, hint))
            else:
                fields.append((param.name, hint, param.default))

        param_schema: dict[str, Any] = {
            "type": "object",
            "properties": properties,
            "additionalProperties": False,
        }
        if required:
            param_schema["required"] = required

        defs: dict[str, Any] = {}
        hoist_definitions(properties, defs)
        if defs:
            param_schema["$defs"] = defs

        try:
            args_model = msgspec.defstruct(
                f"{name}_arguments",
                fields,
                kw_only=True,
                forbid_unknown_fields=True,
            )
            msgspec.inspect.type_info(args_model)
        except TypeError as exc:
            if defer_unknown_types:
                return param_schema, None, injected, di_injected
            msg = f"Tool {name!r} has invalid parameter types: {exc}"
            raise ImproperlyConfiguredException(msg) from exc

        return param_schema, args_model, injected, di_injected


def tool(
    name_or_fn: "Callable[..., Any] | str | None" = None,
    *,
    name: "str | None" = None,
    description: "str | None" = None,
    dependencies: "Mapping[str, Any] | None" = None,
) -> "Any":
    """Decorate a callable as an executable tool."""
    enclosing_deps = _capture_enclosing_class_dependencies()
    if callable(name_or_fn):
        return Tool(
            name_or_fn,
            name=name,
            description=description,
            dependencies=dependencies,
            _enclosing_dependencies=enclosing_deps,
        )

    resolved_name = name or (name_or_fn if isinstance(name_or_fn, str) else None)

    def decorator(fn: "Callable[..., Any]") -> "Tool":
        captured = enclosing_deps or _capture_enclosing_class_dependencies()
        return Tool(
            fn,
            name=resolved_name,
            description=description,
            dependencies=dependencies,
            _enclosing_dependencies=captured,
        )

    return decorator


def current_tool_context() -> "ToolContext | None":
    """Return the active ToolContext for the current execution task."""
    return _current_tool_context.get()


async def execute_tool_calls(
    calls: "Sequence[ToolCall]",
    tools: "Mapping[str, Tool]",
    *,
    context: "ToolContext | None" = None,
) -> "list[ToolResult]":
    """Execute a sequence of tool calls concurrently, returning results in call order."""
    results: list[ToolResult | None] = [None] * len(calls)

    async def _run_single(idx: int, call: ToolCall) -> None:
        tool_obj = tools.get(call.name)
        if tool_obj is None:
            results[idx] = ToolResult(
                call_id=call.call_id,
                name=call.name,
                content={"error": f"Unknown tool: {call.name}"},
                is_error=True,
            )
            return
        try:
            output = await tool_obj.execute(call.arguments, context=context)
            results[idx] = ToolResult(
                call_id=call.call_id,
                name=call.name,
                content=output,
                is_error=False,
            )
        except Exception as exc:
            results[idx] = ToolResult(
                call_id=call.call_id,
                name=call.name,
                content={"error": f"{type(exc).__name__}: {exc}"},
                is_error=True,
            )

    async with anyio.create_task_group() as group:
        for i, call in enumerate(calls):
            group.start_soon(_run_single, i, call)

    return [r for r in results if r is not None]


def is_injected_parameter(annotation: "Any") -> "bool":
    """Return whether an annotation indicates an injected context parameter."""
    unwrapped = _unwrap_type(annotation)
    if unwrapped is ToolContext:
        return True
    if isinstance(unwrapped, type):
        if issubclass(unwrapped, ToolContext):
            return True
        if issubclass(unwrapped, ASGIConnection):
            return True
    return False


def _capture_enclosing_class_dependencies() -> "dict[str, Any]":
    frame = inspect.currentframe()
    try:
        caller = frame.f_back if frame is not None else None
        while caller is not None:
            deps = caller.f_locals.get("dependencies")
            if isinstance(deps, Mapping):
                return dict(deps)
            caller = caller.f_back
    finally:
        del frame
    return {}


def _unwrap_annotated_metadata(annotation: "Any") -> "tuple[Any, list[Any]]":
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        if not args:
            return annotation, []
        inner, nested_metas = _unwrap_annotated_metadata(args[0])
        return inner, [*nested_metas, *args[1:]]
    if origin in (Union, UnionType):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return _unwrap_annotated_metadata(non_none[0])
    return annotation, []


def _unwrap_type(annotation: "Any") -> "Any":
    inner, _ = _unwrap_annotated_metadata(annotation)
    return inner


def _resolve_injector(hint: "Any") -> "Callable[[ToolContext], Any]":
    unwrapped = _unwrap_type(hint)
    if unwrapped is ToolContext or (isinstance(unwrapped, type) and issubclass(unwrapped, ToolContext)):
        return lambda ctx: ctx
    if isinstance(unwrapped, type) and issubclass(unwrapped, ASGIConnection):
        return lambda ctx: ctx.request
    return lambda ctx: ctx


async def _resolve_di_parameters(
    specs: "Sequence[DIParameterSpec]",
    ctx: "ToolContext",
    exit_stack: "AsyncExitStack",
    *,
    tool_name: "str",
) -> "dict[str, Any]":
    resolved: dict[str, Any] = {}
    dishka_req_container: Any | None = None

    for spec in specs:
        if ctx.dependency_resolver is not None:
            custom_val = ctx.dependency_resolver(spec.name, spec.type_hint, ctx)
            if inspect.isawaitable(custom_val):
                custom_val = await custom_val
            if custom_val is not inspect.Parameter.empty:
                resolved[spec.name] = custom_val
                continue

        if spec.provider is not None and (
            isinstance(spec.provider, Provide) or callable(getattr(spec.provider, "dependency", None))
        ):
            resolved[spec.name] = await _invoke_callable_provider(spec.provider, exit_stack)
            continue

        if spec.source_kind == "dishka" or (
            spec.source_kind in {"container", "litestar"} and _find_dishka_container(ctx) is not None
        ):
            if dishka_req_container is None:
                dishka_req_container = await _acquire_dishka_request_container(ctx, exit_stack)
            if dishka_req_container is not None:
                try:
                    resolved[spec.name] = await dishka_req_container.get(
                        spec.type_hint,
                        component=spec.component,
                    )
                    continue
                except Exception:
                    if spec.source_kind == "dishka" and spec.default is inspect.Parameter.empty:
                        raise

        generic_container = _find_generic_container(ctx)
        if generic_container is not None:
            found, val = await _resolve_from_generic_container(generic_container, spec.type_hint, exit_stack)
            if found:
                resolved[spec.name] = val
                continue

        if spec.default is not inspect.Parameter.empty:
            resolved[spec.name] = spec.default
            continue

        msg = f"Cannot resolve dependency parameter {spec.name!r} ({spec.type_hint!r}) for tool {tool_name!r}"
        raise ImproperlyConfiguredException(msg)

    return resolved


async def _invoke_callable_provider(
    provider: "Any",
    exit_stack: "AsyncExitStack",
) -> "Any":
    dep_fn = getattr(provider, "dependency", provider)
    if inspect.isasyncgenfunction(dep_fn):
        cm = asynccontextmanager(dep_fn)()
        return await exit_stack.enter_async_context(cm)
    if inspect.isgeneratorfunction(dep_fn):
        sync_cm = contextmanager(dep_fn)()
        return exit_stack.enter_context(sync_cm)
    if isinstance(provider, Provide):
        return await provider()
    res = dep_fn()
    if inspect.isawaitable(res):
        return await res
    return res


def _is_dishka_container(container: "Any") -> "bool":
    if container is None:
        return False
    mod = getattr(type(container), "__module__", "")
    return mod.startswith("dishka") or hasattr(container, "registry")


def _find_dishka_container(ctx: "ToolContext") -> "Any | None":
    if _is_dishka_container(ctx.dishka_container):
        return ctx.dishka_container
    if _is_dishka_container(ctx.container):
        return ctx.container
    req = ctx.request
    if req is not None:
        req_state = getattr(req, "state", None)
        if req_state is not None:
            c = getattr(req_state, "dishka_container", None)
            if _is_dishka_container(c):
                return c
        app = getattr(req, "app", None)
        app_state = getattr(app, "state", None)
        if app_state is not None:
            c = getattr(app_state, "dishka_container", None)
            if _is_dishka_container(c):
                return c
    return None


async def _acquire_dishka_request_container(
    ctx: "ToolContext",
    exit_stack: "AsyncExitStack",
) -> "Any | None":
    req = ctx.request
    if req is not None:
        req_state = getattr(req, "state", None)
        req_container = getattr(req_state, "dishka_container", None) if req_state is not None else None
        if _is_dishka_container(req_container):
            scope_name = getattr(getattr(getattr(req_container, "registry", None), "scope", None), "name", "")
            if scope_name != "APP":
                return req_container

    container = _find_dishka_container(ctx)
    if container is None:
        return None

    registry = getattr(container, "registry", None)
    scope_name = getattr(getattr(registry, "scope", None), "name", "")
    if scope_name == "APP" and callable(container):
        dishka_ctx: dict[Any, Any] = {ASGIConnection: req, Request: req}
        return await exit_stack.enter_async_context(cast("Any", container(context=dishka_ctx)))
    return container


def _find_generic_container(ctx: "ToolContext") -> "Any | None":
    if ctx.container is not None and not _is_dishka_container(ctx.container):
        return ctx.container
    req = ctx.request
    if req is not None:
        for state_owner in (req, getattr(req, "app", None)):
            state = getattr(state_owner, "state", None)
            if state is None:
                continue
            for attr in ("wireup_container", "svcs_registry", "di_container", "container"):
                val = getattr(state, attr, None)
                if val is not None and not hasattr(val, "_mock_return_value"):
                    return val
    return ctx.container


async def _try_call_container_method(
    method: "Callable[..., Any]",
    type_hint: "Any",
) -> "tuple[bool, Any]":
    try:
        val = method(type_hint)
        if inspect.isawaitable(val):
            val = await val
    except Exception:
        return False, None
    return True, val


async def _resolve_from_generic_container(
    container: "Any",
    type_hint: "Any",
    exit_stack: "AsyncExitStack",
) -> "tuple[bool, Any]":
    for method_name in ("aget", "get", "resolve"):
        method = getattr(container, method_name, None)
        if callable(method):
            found, val = await _try_call_container_method(method, type_hint)
            if found:
                return True, val

    if callable(container):
        try:
            scoped: Any = container()
            if hasattr(scoped, "__aenter__"):
                entered = await exit_stack.enter_async_context(scoped)
                return await _resolve_from_generic_container(entered, type_hint, exit_stack)
            if hasattr(scoped, "__enter__"):
                entered = exit_stack.enter_context(scoped)
                return await _resolve_from_generic_container(entered, type_hint, exit_stack)
        except Exception:
            return False, None

    return False, None
