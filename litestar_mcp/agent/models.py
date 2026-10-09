from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import uuid4

from litestar_mcp.core.exceptions import MissingDependencyError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import httpx2


@dataclass(slots=True)
class ModelDelta:
    """Incremental chunk emitted by a ModelClient during an agent turn."""

    event_type: str
    text: str | None = None
    thought: str | None = None
    tool_name: str | None = None
    call_id: str | None = None
    arguments: dict[str, Any] | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@runtime_checkable
class ModelClient(Protocol):
    """Asynchronous protocol implemented by all LLM model adapters."""

    def stream_turn(
        self,
        *,
        system_instruction: str | None,
        messages: list[Any],
        tools: list[Any],
    ) -> AsyncIterator[ModelDelta]:
        """Stream an agent turn yielding text, thoughts, tool calls, and usage deltas."""
        ...


class MockModelClient:
    """Deterministic scriptable model client for fast offline unit tests."""

    def __init__(self, responses: list[list[ModelDelta]] | None = None) -> None:
        self.responses: list[list[ModelDelta]] = responses or []
        self.call_history: list[dict[str, Any]] = []

    async def stream_turn(
        self,
        *,
        system_instruction: str | None,
        messages: list[Any],
        tools: list[Any],
    ) -> AsyncIterator[ModelDelta]:
        """Record turn inputs and yield queued scripted responses."""
        self.call_history.append(
            {
                "system_instruction": system_instruction,
                "messages": messages,
                "tools": tools,
            }
        )
        if self.responses:
            for delta in self.responses.pop(0):
                yield delta


def _build_contents(messages: list[Any], types: Any) -> list[Any]:
    """Convert AgentMessages into Google GenAI Content objects."""
    contents: list[Any] = []
    for msg in messages:
        role = getattr(msg, "role", "user")
        content_text = getattr(msg, "content", "")
        tool_calls = getattr(msg, "tool_calls", None) or []
        tool_results = getattr(msg, "tool_results", None) or []

        parts: list[Any] = []
        if content_text:
            parts.append(types.Part.from_text(text=content_text))

        parts.extend(
            types.Part.from_function_call(
                name=call.get("name", ""),
                args=call.get("arguments", {}),
            )
            for call in tool_calls
        )

        parts.extend(
            types.Part.from_function_response(
                name=result.get("name", ""),
                response={"result": result.get("content", "")},
            )
            for result in tool_results
        )

        genai_role = "model" if role in ("assistant", "model") else "user"
        contents.append(types.Content(role=genai_role, parts=parts))
    return contents


def _build_function_declarations(tools: list[Any], types: Any) -> list[Any]:
    """Convert tools into Google GenAI FunctionDeclarations."""
    declarations: list[Any] = []
    for t in tools:
        schema = getattr(t, "input_schema", None)
        if callable(getattr(t, "to_json_schema", None)):
            schema = t.to_json_schema()
        name = getattr(t, "name", None) or getattr(t, "__name__", "unnamed_tool")
        description = getattr(t, "description", None) or getattr(t, "__doc__", "")
        parameters: dict[str, Any] | None = None
        if isinstance(schema, dict):
            parameters = schema.get("parameters") or schema

        declarations.append(
            types.FunctionDeclaration(
                name=name,
                description=description,
                parameters=parameters,
            )
        )
    return declarations


def _parse_part_deltas(part: Any, *, has_chunk_text: bool) -> list[ModelDelta]:
    """Extract thought, text, and tool_call deltas from a single candidate part."""
    part_deltas: list[ModelDelta] = []
    thought_attr = getattr(part, "thought", None)
    part_text = getattr(part, "text", None)
    fn_call = getattr(part, "function_call", None)

    if thought_attr is True and isinstance(part_text, str) and part_text:
        part_deltas.append(ModelDelta(event_type="thought", thought=part_text))
    elif isinstance(thought_attr, str) and thought_attr:
        part_deltas.append(ModelDelta(event_type="thought", thought=thought_attr))
    elif not has_chunk_text and isinstance(part_text, str) and part_text and fn_call is None:
        part_deltas.append(ModelDelta(event_type="delta", text=part_text))

    if fn_call is not None:
        call_id = getattr(fn_call, "id", None) or uuid4().hex
        part_deltas.append(
            ModelDelta(
                event_type="tool_call",
                tool_name=fn_call.name,
                call_id=call_id,
                arguments=dict(fn_call.args or {}),
            )
        )
    return part_deltas


def _parse_chunk_deltas(chunk: Any) -> list[ModelDelta]:
    """Parse text, thoughts, tool calls, and usage from a stream chunk."""
    deltas: list[ModelDelta] = []
    chunk_text = getattr(chunk, "text", None)
    has_chunk_text = bool(isinstance(chunk_text, str) and chunk_text)
    if has_chunk_text:
        deltas.append(ModelDelta(event_type="delta", text=chunk_text))

    candidates = getattr(chunk, "candidates", None) or []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None) or []
        for part in parts:
            deltas.extend(_parse_part_deltas(part, has_chunk_text=has_chunk_text))

    usage = getattr(chunk, "usage_metadata", None)
    if usage is not None:
        prompt_tokens = getattr(usage, "prompt_token_count", None)
        completion_tokens = getattr(usage, "candidates_token_count", None)
        deltas.append(
            ModelDelta(
                event_type="usage",
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
        )
    return deltas


class GoogleGenAIClient:
    """First-party Google GenAI model client wrapping google.genai with httpx2 support."""

    def __init__(
        self,
        model: str = "gemini-2.5-flash",
        *,
        api_key: str | None = None,
        vertexai: bool | None = None,
        project: str | None = None,
        location: str | None = None,
        thinking_budget: int | None = None,
        temperature: float | None = None,
        http_client: httpx2.AsyncClient | None = None,
        http_options: Any | None = None,
        client: Any | None = None,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.vertexai = vertexai
        self.project = project
        self.location = location
        self.thinking_budget = thinking_budget
        self.temperature = temperature
        self.http_client = http_client
        self.http_options = http_options
        self._client = client

    def _build_http_options(self, types_module: Any) -> Any | None:
        """Build google.genai.types.HttpOptions wired with httpx2.AsyncClient when configured."""
        if self.http_options is not None:
            return self.http_options
        if self.http_client is not None:
            http_options_cls = getattr(types_module, "HttpOptions", None)
            if http_options_cls is not None:
                return http_options_cls(httpx_async_client=self.http_client)
            return {"httpx_async_client": self.http_client}
        return None

    def _ensure_client(self) -> tuple[Any, Any]:
        """Lazily load google.genai and initialize client."""
        if self._client is not None:
            types_module = import_module("google.genai.types")
            return self._client, types_module

        try:
            genai_module = import_module("google.genai")
            types_module = import_module("google.genai.types")
        except ImportError as exc:
            raise MissingDependencyError(
                package="google-genai",
                extra="google-genai",
            ) from exc

        client_kwargs: dict[str, Any] = {}
        if self.api_key is not None:
            client_kwargs["api_key"] = self.api_key
        if self.vertexai is not None:
            client_kwargs["vertexai"] = self.vertexai
        if self.project is not None:
            client_kwargs["project"] = self.project
        if self.location is not None:
            client_kwargs["location"] = self.location
        resolved_http_options = self._build_http_options(types_module)
        if resolved_http_options is not None:
            client_kwargs["http_options"] = resolved_http_options

        self._client = genai_module.Client(**client_kwargs)
        return self._client, types_module

    async def stream_turn(
        self,
        *,
        system_instruction: str | None,
        messages: list[Any],
        tools: list[Any],
    ) -> AsyncIterator[ModelDelta]:
        """Stream an agent turn using Google GenAI SDK."""
        client, types = self._ensure_client()
        contents = _build_contents(messages, types)
        function_declarations = _build_function_declarations(tools, types)
        genai_tools = [types.Tool(function_declarations=function_declarations)] if function_declarations else None

        config_kwargs: dict[str, Any] = {}
        if system_instruction:
            config_kwargs["system_instruction"] = system_instruction
        if genai_tools:
            config_kwargs["tools"] = genai_tools
        if self.temperature is not None:
            config_kwargs["temperature"] = self.temperature
        if self.thinking_budget is not None and hasattr(types, "ThinkingConfig"):
            config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=self.thinking_budget)

        config = types.GenerateContentConfig(**config_kwargs)
        response_stream = await client.aio.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=config,
        )

        async for chunk in response_stream:
            for delta in _parse_chunk_deltas(chunk):
                yield delta
