"""Core tool abstraction with type-based context injection and msgspec argument validation."""

import functools
import inspect
from collections.abc import Callable, Mapping, Sequence
from contextvars import ContextVar
from types import UnionType
from typing import Annotated, Any, Union, get_args, get_origin, get_type_hints

import anyio
import anyio.to_thread
import msgspec
from litestar.connection import ASGIConnection
from litestar.exceptions import ImproperlyConfiguredException
from litestar.serialization import decode_json, encode_json, get_serializer

from litestar_mcp.core.context import ToolContext
from litestar_mcp.core.exceptions import LitestarMCPError
from litestar_mcp.core.schema_builder import hoist_definitions, type_to_json_schema

__all__ = (
    "Tool",
    "ToolArgumentError",
    "ToolCall",
    "ToolResult",
    "current_tool_context",
    "execute_tool_calls",
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
    """Executable tool with a JSON Schema, msgspec argument model, and type-based context injection."""

    __slots__ = (
        "_args_model",
        "_injected",
        "bound_instance",
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

    def __init__(
        self,
        fn: "Callable[..., Any]",
        *,
        name: "str | None" = None,
        description: "str | None" = None,
        bound_instance: "Any | None" = None,
        _args_model: "type[Any] | None" = None,
        _injected: "dict[str, Callable[[ToolContext], Any]] | None" = None,
        parameters: "dict[str, Any] | None" = None,
    ) -> "None":
        self.fn = fn
        self.bound_instance = bound_instance
        self.name = str(name or getattr(fn, "__name__", "tool"))
        resolved_desc = description if description is not None else getattr(fn, "__doc__", None)
        self.description = (resolved_desc or "").strip()

        if parameters is not None and _injected is not None:
            self._args_model = _args_model
            self._injected = _injected
            self.parameters = parameters
            return

        param_schema, args_model, injected = self._build_tool_schema(self.name, fn)
        self.parameters = param_schema
        self._args_model = args_model
        self._injected = injected

    def __get__(self, instance: "Any", owner: "type[Any] | None" = None) -> "Tool":
        """Support descriptor binding when a tool is accessed on a controller or class instance."""
        if instance is None:
            return self
        return Tool(
            self.fn,
            name=self.name,
            description=self.description,
            bound_instance=instance,
            _args_model=self._args_model,
            _injected=self._injected,
            parameters=self.parameters,
        )

    def __call__(self, *args: "Any", **kwargs: "Any") -> "Any":
        """Invoke the underlying callable directly."""
        if self.bound_instance is not None:
            return self.fn(self.bound_instance, *args, **kwargs)
        return self.fn(*args, **kwargs)

    @property
    def stripped_parameters(self) -> "set[str]":
        """Names of parameters injected from context rather than passed by the caller."""
        return set(self._injected.keys())

    @property
    def input_schema(self) -> "dict[str, Any]":
        """JSON Schema representing tool inputs."""
        return self.parameters

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
        """Validate arguments, inject context parameters, and execute the tool."""
        if self._args_model is not None:
            try:
                converted = msgspec.convert(dict(arguments or {}), self._args_model, strict=False)
            except (msgspec.ValidationError, msgspec.DecodeError) as exc:
                msg = f"Failed to validate arguments: {exc}"
                raise ToolArgumentError(msg) from exc
            kwargs = dict(msgspec.structs.asdict(converted))
        else:
            kwargs = dict(arguments or {})

        effective_ctx = context or ToolContext()
        for param_name, injector in self._injected.items():
            kwargs[param_name] = injector(effective_ctx)

        callable_fn = functools.partial(self.fn, self.bound_instance) if self.bound_instance is not None else self.fn

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
    ) -> "tuple[dict[str, Any], type[Any], dict[str, Callable[[ToolContext], Any]]]":
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
            msg = f"Tool {name!r} has invalid parameter types: {exc}"
            raise ImproperlyConfiguredException(msg) from exc

        return param_schema, args_model, injected


def tool(
    name_or_fn: "Callable[..., Any] | str | None" = None,
    *,
    name: "str | None" = None,
    description: "str | None" = None,
) -> "Any":
    """Decorate a callable as an executable tool."""
    if callable(name_or_fn):
        return Tool(name_or_fn, name=name, description=description)

    resolved_name = name or (name_or_fn if isinstance(name_or_fn, str) else None)

    def decorator(fn: "Callable[..., Any]") -> "Tool":
        return Tool(fn, name=resolved_name, description=description)

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
        except Exception as exc:  # noqa: BLE001
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


def _unwrap_type(annotation: "Any") -> "Any":
    origin = get_origin(annotation)
    if origin is Annotated:
        args = get_args(annotation)
        return _unwrap_type(args[0]) if args else annotation
    if origin in (Union, UnionType):
        non_none = [a for a in get_args(annotation) if a is not type(None)]
        if len(non_none) == 1:
            return _unwrap_type(non_none[0])
    return annotation


def _resolve_injector(hint: "Any") -> "Callable[[ToolContext], Any]":
    unwrapped = _unwrap_type(hint)
    if unwrapped is ToolContext or (isinstance(unwrapped, type) and issubclass(unwrapped, ToolContext)):
        return lambda ctx: ctx
    if isinstance(unwrapped, type) and issubclass(unwrapped, ASGIConnection):
        return lambda ctx: ctx.request
    return lambda ctx: ctx
