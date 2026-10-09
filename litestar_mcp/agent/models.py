"""Model client abstractions and Google GenAI SDK integration."""

from dataclasses import dataclass, field
from importlib import import_module
from typing import TYPE_CHECKING, Any, Literal, Protocol, runtime_checkable
from uuid import uuid4

from litestar_mcp.core.exceptions import MissingDependencyError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    import httpx2

    from litestar_mcp.agent.spec import AgentMessage
    from litestar_mcp.core.tools import Tool

ModelEventType = Literal["delta", "thought", "tool_call", "usage"]

__all__ = (
    "GoogleGenAIClient",
    "MockModelClient",
    "ModelClient",
    "ModelDelta",
    "ModelEventType",
)


@dataclass(slots=True)
class ModelDelta:
    """Incremental chunk emitted by a ModelClient during an agent turn."""

    event_type: "ModelEventType"
    text: "str | None" = None
    thought: "str | None" = None
    tool_name: "str | None" = None
    call_id: "str | None" = None
    arguments: "dict[str, Any] | None" = None
    prompt_tokens: "int | None" = None
    completion_tokens: "int | None" = None
    provider_metadata: "dict[str, dict[str, Any]]" = field(default_factory=dict)
    thought_tokens: "int | None" = None


@runtime_checkable
class ModelClient(Protocol):
    """Asynchronous protocol implemented by all LLM model adapters."""

    def stream_turn(
        self,
        *,
        system_instruction: "str | None",
        messages: "Sequence[AgentMessage]",
        tools: "Sequence[Tool]",
    ) -> "AsyncIterator[ModelDelta]":
        """Stream an agent turn yielding text, thoughts, tool calls, and usage deltas."""
        ...


class MockModelClient:
    """Deterministic scriptable model client for fast offline unit tests."""

    def __init__(self, responses: "list[list[ModelDelta]] | None" = None) -> None:
        """Initialize mock client with optional response queue."""
        self.responses: list[list[ModelDelta]] = responses or []
        self.call_history: list[dict[str, Any]] = []

    async def stream_turn(
        self,
        *,
        system_instruction: "str | None",
        messages: "Sequence[AgentMessage]",
        tools: "Sequence[Tool]",
    ) -> "AsyncIterator[ModelDelta]":
        """Record turn inputs and yield queued scripted responses."""
        self.call_history.append(
            {
                "system_instruction": system_instruction,
                "messages": list(messages),
                "tools": list(tools),
            }
        )
        if self.responses:
            for delta in self.responses.pop(0):
                yield delta


def _build_contents(
    messages: "Sequence[AgentMessage]",
    types: "Any",
) -> "tuple[list[str], list[Any]]":
    """Translate history into Gemini contents, returning folded system texts and non-empty contents."""
    system_texts: list[str] = []
    contents: list[Any] = []
    provider_ids: dict[str, str | None] = {}

    for message in messages:
        if message.role == "system":
            if message.content:
                system_texts.append(message.content)
            continue

        parts: list[Any] = []
        if message.content:
            parts.append(types.Part(text=message.content))

        for call in message.tool_calls:
            google = call.provider_metadata.get("google", {})
            provider_ids[call.call_id] = google.get("call_id")
            parts.append(
                types.Part(
                    function_call=types.FunctionCall(
                        id=google.get("call_id"),
                        name=call.name,
                        args=call.arguments,
                    ),
                    thought_signature=google.get("thought_signature"),
                )
            )

        for result in message.tool_results:
            response = {"error": result.content} if result.is_error else {"result": result.content}
            parts.append(
                types.Part(
                    function_response=types.FunctionResponse(
                        id=provider_ids.get(result.call_id),
                        name=result.name,
                        response=response,
                    )
                )
            )

        if parts:
            role = "model" if message.role == "assistant" else "user"
            contents.append(types.Content(role=role, parts=parts))

    return system_texts, contents


def _build_function_declarations(tools: "Sequence[Tool]", types: "Any") -> "list[Any]":
    """Convert tools into Google GenAI FunctionDeclarations using parameters_json_schema."""
    declarations: list[Any] = []
    for t in tools:
        params = t.parameters if hasattr(t, "parameters") else {}
        if isinstance(params, dict) and params.get("properties"):
            declarations.append(
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description or "",
                    parameters_json_schema=params,
                )
            )
        else:
            declarations.append(
                types.FunctionDeclaration(
                    name=t.name,
                    description=t.description or "",
                )
            )
    return declarations


def _parse_part_deltas(part: "Any") -> "list[ModelDelta]":
    """Extract thought, text, and tool_call deltas from a single candidate part."""
    part_deltas: list[ModelDelta] = []
    thought_attr = getattr(part, "thought", False)
    part_text = getattr(part, "text", None)
    fn_call = getattr(part, "function_call", None)

    if thought_attr is True and isinstance(part_text, str) and part_text:
        part_deltas.append(ModelDelta(event_type="thought", text=part_text, thought=part_text))
    elif isinstance(thought_attr, str) and thought_attr:
        part_deltas.append(ModelDelta(event_type="thought", text=thought_attr, thought=thought_attr))
    elif isinstance(part_text, str) and part_text and fn_call is None:
        part_deltas.append(ModelDelta(event_type="delta", text=part_text))

    if fn_call is not None:
        call_id = getattr(fn_call, "id", None) or uuid4().hex
        thought_sig = getattr(part, "thought_signature", None)
        part_deltas.append(
            ModelDelta(
                event_type="tool_call",
                tool_name=fn_call.name,
                call_id=call_id,
                arguments=dict(fn_call.args or {}),
                provider_metadata={
                    "google": {
                        "call_id": getattr(fn_call, "id", None),
                        "thought_signature": thought_sig,
                    }
                },
            )
        )
    return part_deltas


def _parse_chunk_deltas(chunk: "Any") -> "tuple[list[ModelDelta], Any | None]":
    """Parse text, thoughts, and tool calls from a stream chunk, returning usage metadata."""
    deltas: list[ModelDelta] = []
    candidates = getattr(chunk, "candidates", None) or []
    has_parts = False

    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if content is None:
            continue
        parts = getattr(content, "parts", None) or []
        for part in parts:
            has_parts = True
            deltas.extend(_parse_part_deltas(part))

    if not has_parts:
        chunk_text = getattr(chunk, "text", None)
        if isinstance(chunk_text, str) and chunk_text:
            deltas.append(ModelDelta(event_type="delta", text=chunk_text))

    usage = getattr(chunk, "usage_metadata", None)
    return deltas, usage


class GoogleGenAIClient:
    """First-party Google GenAI model client wrapping google.genai with httpx2 support."""

    def __init__(
        self,
        model: "str" = "gemini-3.8-flash",
        *,
        api_key: "str | None" = None,
        vertexai: "bool | None" = None,
        project: "str | None" = None,
        location: "str | None" = None,
        thinking_budget: "int | None" = None,
        thinking_level: "str | None" = None,
        include_thoughts: "bool | None" = None,
        temperature: "float | None" = None,
        http_client: "httpx2.AsyncClient | None" = None,
        http_options: "Any | None" = None,
        client: "Any | None" = None,
    ) -> None:
        """Initialize Google GenAI client with configuration."""
        self.model = model
        self.api_key = api_key
        self.vertexai = vertexai
        self.project = project
        self.location = location
        self.thinking_budget = thinking_budget
        self.thinking_level = thinking_level
        self.include_thoughts = include_thoughts
        self.temperature = temperature
        self.http_client = http_client
        self.http_options = http_options
        self._client = client

    @property
    def provider_name(self) -> "str":
        """Return canonical OpenTelemetry provider name."""
        return "gcp.vertex_ai" if self.vertexai else "gcp.gemini"

    def _build_http_options(self, types_module: "Any") -> "Any | None":
        """Build google.genai.types.HttpOptions wired with httpx2.AsyncClient when configured."""
        if self.http_options is not None:
            return self.http_options
        if self.http_client is not None:
            http_options_cls = getattr(types_module, "HttpOptions", None)
            if http_options_cls is not None:
                return http_options_cls(httpx_async_client=self.http_client)
        return None

    def _ensure_client(self) -> "tuple[Any, Any]":
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
        system_instruction: "str | None",
        messages: "Sequence[AgentMessage]",
        tools: "Sequence[Tool]",
    ) -> "AsyncIterator[ModelDelta]":
        """Stream an agent turn using Google GenAI SDK."""
        client, types = self._ensure_client()
        system_texts, contents = _build_contents(messages, types)
        function_declarations = _build_function_declarations(tools, types)
        genai_tools = [types.Tool(function_declarations=function_declarations)] if function_declarations else None

        folded_instruction: str | None = None
        instruction_parts = [s for s in [system_instruction, *system_texts] if s]
        if instruction_parts:
            folded_instruction = "\n\n".join(instruction_parts)

        config_kwargs: dict[str, Any] = {}
        if folded_instruction:
            config_kwargs["system_instruction"] = folded_instruction
        if genai_tools:
            config_kwargs["tools"] = genai_tools
        if self.temperature is not None:
            config_kwargs["temperature"] = self.temperature

        thinking_kwargs: dict[str, Any] = {}
        if self.thinking_budget is not None:
            thinking_kwargs["thinking_budget"] = self.thinking_budget
        if self.thinking_level is not None:
            thinking_kwargs["thinking_level"] = self.thinking_level
        if self.include_thoughts is not None:
            thinking_kwargs["include_thoughts"] = self.include_thoughts
        if thinking_kwargs and hasattr(types, "ThinkingConfig"):
            config_kwargs["thinking_config"] = types.ThinkingConfig(**thinking_kwargs)

        config = types.GenerateContentConfig(**config_kwargs)
        response_stream = await client.aio.models.generate_content_stream(
            model=self.model,
            contents=contents,
            config=config,
        )

        last_usage: Any = None
        async for chunk in response_stream:
            deltas, usage = _parse_chunk_deltas(chunk)
            if usage is not None:
                last_usage = usage
            for delta in deltas:
                yield delta

        if last_usage is not None:
            yield ModelDelta(
                event_type="usage",
                prompt_tokens=getattr(last_usage, "prompt_token_count", None),
                completion_tokens=getattr(last_usage, "candidates_token_count", None),
                thought_tokens=getattr(last_usage, "thoughts_token_count", None),
            )
