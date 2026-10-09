from __future__ import annotations

from typing import Any

import pytest

from litestar_mcp.agent.models import (
    GoogleGenAIClient,
    MockModelClient,
    ModelDelta,
)
from litestar_mcp.agent.spec import AgentMessage
from litestar_mcp.agent.tools import Tool
from litestar_mcp.core.exceptions import MissingDependencyError


def test_model_delta() -> None:
    delta = ModelDelta(
        event_type="delta",
        text="Hello",
        prompt_tokens=10,
        completion_tokens=5,
    )
    assert delta.event_type == "delta"
    assert delta.text == "Hello"
    assert delta.prompt_tokens == 10
    assert delta.completion_tokens == 5


@pytest.mark.asyncio
async def test_mock_model_client() -> None:
    responses = [
        [
            ModelDelta(event_type="thought", thought="Thinking about greeting"),
            ModelDelta(event_type="delta", text="Hello "),
            ModelDelta(event_type="delta", text="world!"),
        ],
        [
            ModelDelta(event_type="tool_call", tool_name="search", call_id="c1", arguments={"q": "litestar"}),
        ],
    ]
    client = MockModelClient(responses=responses)

    chunks_turn1 = [chunk async for chunk in client.stream_turn(system_instruction="Be helpful", messages=[], tools=[])]

    assert len(chunks_turn1) == 3
    assert chunks_turn1[0].thought == "Thinking about greeting"
    assert "".join([c.text or "" for c in chunks_turn1]) == "Hello world!"
    assert len(client.call_history) == 1
    assert client.call_history[0]["system_instruction"] == "Be helpful"

    chunks_turn2 = [chunk async for chunk in client.stream_turn(system_instruction="Be helpful", messages=[], tools=[])]

    assert len(chunks_turn2) == 1
    assert chunks_turn2[0].tool_name == "search"
    assert chunks_turn2[0].arguments == {"q": "litestar"}


def test_google_genai_client_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    monkeypatch.setitem(sys.modules, "google.genai", None)
    client = GoogleGenAIClient()
    client._client = None
    with pytest.raises(MissingDependencyError) as exc_info:
        client._ensure_client()
    assert exc_info.value.package == "google-genai"


class MockFnCall:
    def __init__(self, n: str, a: dict[str, Any]) -> None:
        self.name = n
        self.args = a
        self.id = "fn-1"


class MockPart:
    def __init__(self, text: str = "", thought: str | bool = "", function_call: Any = None) -> None:
        self.text = text
        self.thought = thought
        self.function_call = function_call

    @classmethod
    def from_text(cls, text: str) -> MockPart:
        return cls(text=text)

    @classmethod
    def from_function_call(cls, name: str, args: dict[str, Any]) -> MockPart:
        return cls(function_call=MockFnCall(name, args))

    @classmethod
    def from_function_response(cls, name: str, response: dict[str, Any]) -> MockPart:
        return cls(text=str(response))


class MockContent:
    def __init__(self, role: str, parts: list[Any]) -> None:
        self.role = role
        self.parts = parts


class MockCandidate:
    def __init__(self, parts: list[Any]) -> None:
        self.content = MockContent("model", parts)


class MockUsage:
    prompt_token_count = 15
    candidates_token_count = 25


class MockChunk:
    def __init__(self, text: str = "", candidates: list[Any] | None = None, usage: Any = None) -> None:
        self.text = text
        self.candidates = candidates or []
        self.usage_metadata = usage


class MockFnDeclaration:
    def __init__(self, name: str, description: str, parameters: Any) -> None:
        self.name = name
        self.description = description
        self.parameters = parameters


class MockTool:
    def __init__(self, function_declarations: list[Any]) -> None:
        self.function_declarations = function_declarations


class MockConfig:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class MockThinkingConfig:
    def __init__(self, thinking_budget: int) -> None:
        self.thinking_budget = thinking_budget


class MockHttpOptions:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs


class MockTypesModule:
    Part = MockPart
    Content = MockContent
    FunctionDeclaration = MockFnDeclaration
    Tool = MockTool
    GenerateContentConfig = MockConfig
    ThinkingConfig = MockThinkingConfig
    HttpOptions = MockHttpOptions


class MockModels:
    async def generate_content_stream(self, **gen_kwargs: Any) -> Any:
        async def _stream() -> Any:
            yield MockChunk(text="Hello ")
            yield MockChunk(
                candidates=[
                    MockCandidate([MockPart(text="Boolean thought reasoning", thought=True)]),
                    MockCandidate([MockPart(thought="Internal reasoning")]),
                    MockCandidate([MockPart.from_function_call("calculate", {"x": 5})]),
                ],
                usage=MockUsage(),
            )

        return _stream()


class MockAio:
    models = MockModels()


class MockGenaiClient:
    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.aio = MockAio()


class MockGenaiModule:
    Client = MockGenaiClient


@pytest.mark.asyncio
async def test_google_genai_client_stream_turn_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    import httpx2

    monkeypatch.setitem(sys.modules, "google.genai", MockGenaiModule)
    monkeypatch.setitem(sys.modules, "google.genai.types", MockTypesModule)

    async with httpx2.AsyncClient() as http_client:
        client = GoogleGenAIClient(
            model="gemini-2.5-pro",
            api_key="fake-key",
            thinking_budget=1024,
            temperature=0.7,
            http_client=http_client,
        )

        raw_client, _ = client._ensure_client()
        assert "http_options" in raw_client.kwargs
        assert raw_client.kwargs["http_options"].kwargs["httpx_async_client"] is http_client

        test_tool = Tool(
            name="calculate",
            description="Run calculation",
            fn=lambda x: x * 2,
            parameters={"type": "object", "properties": {"x": {"type": "integer"}}},
        )

        messages = [
            AgentMessage.user("Hello"),
            AgentMessage.assistant("Thinking...", tool_calls=[{"name": "calculate", "arguments": {"x": 5}}]),
            AgentMessage(role="tool", content="10", tool_results=[{"name": "calculate", "content": "10"}]),
        ]

        deltas = [
            d
            async for d in client.stream_turn(
                system_instruction="Be accurate",
                messages=messages,
                tools=[test_tool],
            )
        ]

        assert any(d.event_type == "delta" and d.text == "Hello " for d in deltas)
        assert any(d.event_type == "thought" and d.thought == "Boolean thought reasoning" for d in deltas)
        assert any(d.event_type == "thought" and d.thought == "Internal reasoning" for d in deltas)
        assert any(d.event_type == "tool_call" and d.tool_name == "calculate" for d in deltas)
        assert any(d.event_type == "usage" and d.prompt_tokens == 15 for d in deltas)
