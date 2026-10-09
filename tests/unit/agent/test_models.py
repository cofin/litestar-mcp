"""Unit tests for ModelClient protocol, MockModelClient, and GoogleGenAIClient."""

import sys
from collections.abc import AsyncIterator
from typing import Any, cast

import httpx2
import pytest
from google.genai import types

from litestar_mcp.agent.models import (
    GoogleGenAIClient,
    MockModelClient,
    ModelDelta,
)
from litestar_mcp.agent.spec import AgentMessage
from litestar_mcp.core.exceptions import MissingDependencyError
from litestar_mcp.core.tools import Tool, ToolCall, ToolResult


class FakeStreamClient:
    """Fake Google GenAI SDK client that captures call parameters and yields canned chunks."""

    def __init__(self, chunks: list[Any]) -> None:
        """Initialize fake client with canned stream chunks."""
        self.chunks = chunks
        self.recorded_calls: list[dict[str, Any]] = []

        class FakeModels:
            """Models namespace containing generate_content_stream."""

            def __init__(self, outer: "FakeStreamClient") -> None:
                """Bind to outer FakeStreamClient instance."""
                self._outer = outer

            async def generate_content_stream(self, **kwargs: Any) -> AsyncIterator[Any]:
                """Record call kwargs and yield chunks."""
                self._outer.recorded_calls.append(kwargs)

                async def _gen() -> AsyncIterator[Any]:
                    for chunk in self._outer.chunks:
                        yield chunk

                return _gen()

        class FakeAio:
            """Aio namespace containing models."""

            def __init__(self, outer: "FakeStreamClient") -> None:
                """Bind to outer FakeStreamClient instance."""
                self.models = FakeModels(outer)

        self.aio = FakeAio(self)


def test_model_delta_attributes() -> None:
    """ModelDelta stores all event attributes accurately."""
    delta = ModelDelta(
        event_type="delta",
        text="Hello",
        prompt_tokens=10,
        completion_tokens=5,
        thought_tokens=2,
        provider_metadata={"google": {"sig": "abc"}},
    )
    assert delta.event_type == "delta"
    assert delta.text == "Hello"
    assert delta.prompt_tokens == 10
    assert delta.completion_tokens == 5
    assert delta.thought_tokens == 2
    assert delta.provider_metadata == {"google": {"sig": "abc"}}


@pytest.mark.anyio
async def test_mock_model_client() -> None:
    """MockModelClient records history and returns queued scripted responses."""
    responses = [
        [
            ModelDelta(event_type="thought", text="Thinking about greeting"),
            ModelDelta(event_type="delta", text="Hello world!"),
        ],
        [
            ModelDelta(event_type="tool_call", tool_name="search", call_id="c1", arguments={"q": "litestar"}),
        ],
    ]
    client = MockModelClient(responses=responses)

    chunks_turn1 = [
        chunk
        async for chunk in client.stream_turn(
            system_instruction="Be helpful",
            messages=[],
            tools=[],
        )
    ]
    assert len(chunks_turn1) == 2
    assert chunks_turn1[0].text == "Thinking about greeting"
    assert chunks_turn1[1].text == "Hello world!"
    assert len(client.call_history) == 1

    chunks_turn2 = [
        chunk
        async for chunk in client.stream_turn(
            system_instruction="Be helpful",
            messages=[],
            tools=[],
        )
    ]
    assert len(chunks_turn2) == 1
    assert chunks_turn2[0].tool_name == "search"


def test_google_genai_client_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """GoogleGenAIClient raises MissingDependencyError when google.genai cannot be imported."""
    monkeypatch.setitem(sys.modules, "google.genai", None)
    client = GoogleGenAIClient()
    client._client = None
    with pytest.raises(MissingDependencyError) as exc_info:
        client._ensure_client()
    assert exc_info.value.package == "google-genai"


def test_google_client_defaults_and_http_options() -> None:
    """GoogleGenAIClient defaults to gemini-3.8-flash and configures HttpOptions and provider_name."""
    client_default = GoogleGenAIClient()
    assert client_default.model == "gemini-3.8-flash"
    assert client_default.provider_name == "gcp.gemini"

    client_vertex = GoogleGenAIClient(vertexai=True)
    assert client_vertex.provider_name == "gcp.vertex_ai"

    mock_http_client = httpx2.AsyncClient()
    client_with_http = GoogleGenAIClient(http_client=mock_http_client)
    http_opts = client_with_http._build_http_options(types)
    assert isinstance(http_opts, types.HttpOptions)
    assert http_opts.httpx_async_client is mock_http_client


@pytest.mark.anyio
async def test_google_history_round_trips_thought_signature_and_call_id() -> None:
    """Gemini function calls preserve thought_signature and provider id in follow-up turn."""
    chunk1 = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="call-xyz",
                                name="add",
                                args={"a": 1, "b": 2},
                            ),
                            thought_signature=b"test_thought_signature_bytes",
                        ),
                    ],
                ),
            ),
        ],
    )
    fake_sdk = FakeStreamClient([chunk1])
    client = GoogleGenAIClient(client=fake_sdk)

    deltas = [
        d
        async for d in client.stream_turn(
            system_instruction=None,
            messages=[AgentMessage(role="user", content="compute 1+2")],
            tools=[],
        )
    ]

    assert len(deltas) == 1
    call_delta = deltas[0]
    assert call_delta.event_type == "tool_call"
    assert call_delta.tool_name == "add"
    assert call_delta.provider_metadata["google"]["call_id"] == "call-xyz"
    assert call_delta.provider_metadata["google"]["thought_signature"] == b"test_thought_signature_bytes"

    tool_call_obj = ToolCall(
        name=call_delta.tool_name,
        call_id=call_delta.call_id or "",
        arguments=call_delta.arguments or {},
        provider_metadata=call_delta.provider_metadata,
    )
    assistant_msg = AgentMessage(
        role="assistant",
        content="",
        tool_calls=[tool_call_obj],
    )
    tool_result_obj = ToolResult(
        call_id=tool_call_obj.call_id,
        name="add",
        content={"result": 3},
    )
    tool_msg = AgentMessage(
        role="tool",
        content="",
        tool_results=[tool_result_obj],
    )

    chunk2 = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[types.Part(text="Result is 3")],
                ),
            ),
        ],
    )
    fake_sdk.chunks = [chunk2]

    _ = [
        d
        async for d in client.stream_turn(
            system_instruction=None,
            messages=[
                AgentMessage(role="user", content="compute 1+2"),
                assistant_msg,
                tool_msg,
            ],
            tools=[],
        )
    ]

    recorded_call = fake_sdk.recorded_calls[1]
    contents: list[types.Content] = recorded_call["contents"]
    assert len(contents) == 3

    model_content = contents[1]
    assert model_content.role == "model"
    assert model_content.parts is not None
    assert len(model_content.parts) == 1
    fn_call_part = model_content.parts[0]
    assert fn_call_part.function_call is not None
    assert fn_call_part.function_call.id == "call-xyz"
    assert fn_call_part.function_call.name == "add"
    assert fn_call_part.thought_signature == b"test_thought_signature_bytes"

    tool_content = contents[2]
    assert tool_content.role == "user"
    assert tool_content.parts is not None
    assert len(tool_content.parts) == 1
    fn_resp_part = tool_content.parts[0]
    assert fn_resp_part.function_response is not None
    assert fn_resp_part.function_response.id == "call-xyz"
    assert fn_resp_part.function_response.name == "add"
    assert fn_resp_part.function_response.response == {"result": {"result": 3}}


@pytest.mark.anyio
async def test_function_call_without_provider_id_omits_response_id() -> None:
    """ToolResult for function call without provider id omits function_response id."""
    fake_sdk = FakeStreamClient([])
    client = GoogleGenAIClient(client=fake_sdk)

    call_obj = ToolCall(name="query", call_id="local_id_only", arguments={})
    res_obj = ToolResult(name="query", call_id="local_id_only", content="ok")

    _ = [
        d
        async for d in client.stream_turn(
            system_instruction=None,
            messages=[
                AgentMessage(role="assistant", tool_calls=[call_obj]),
                AgentMessage(role="tool", tool_results=[res_obj]),
            ],
            tools=[],
        )
    ]

    contents = fake_sdk.recorded_calls[0]["contents"]
    assert len(contents) == 2
    resp_part = contents[1].parts[0]
    assert resp_part.function_response.id is None
    assert resp_part.function_response.response == {"result": "ok"}


@pytest.mark.anyio
async def test_error_result_sends_error_response() -> None:
    """ToolResult with is_error=True formats response payload under error key."""
    fake_sdk = FakeStreamClient([])
    client = GoogleGenAIClient(client=fake_sdk)

    call_obj = ToolCall(name="fail_op", call_id="cid", arguments={})
    res_obj = ToolResult(name="fail_op", call_id="cid", content="failure message", is_error=True)

    _ = [
        d
        async for d in client.stream_turn(
            system_instruction=None,
            messages=[
                AgentMessage(role="assistant", tool_calls=[call_obj]),
                AgentMessage(role="tool", tool_results=[res_obj]),
            ],
            tools=[],
        )
    ]

    contents = fake_sdk.recorded_calls[0]["contents"]
    resp_part = contents[1].parts[0]
    assert resp_part.function_response.response == {"error": "failure message"}


@pytest.mark.anyio
async def test_google_contents_skip_empty_and_fold_system() -> None:
    """System messages are folded into system_instruction and empty messages produce no Content."""
    fake_sdk = FakeStreamClient([])
    client = GoogleGenAIClient(client=fake_sdk)

    messages = [
        AgentMessage(role="system", content="System instruction A"),
        AgentMessage(role="assistant", content=""),
        AgentMessage(role="system", content="System instruction B"),
        AgentMessage(role="user", content="User prompt"),
    ]

    _ = [
        d
        async for d in client.stream_turn(
            system_instruction="Base prompt",
            messages=messages,
            tools=[],
        )
    ]

    recorded_call = fake_sdk.recorded_calls[0]
    config: types.GenerateContentConfig = recorded_call["config"]
    assert config.system_instruction == "Base prompt\n\nSystem instruction A\n\nSystem instruction B"

    contents: list[types.Content] = recorded_call["contents"]
    assert len(contents) == 1
    assert contents[0].role == "user"
    assert contents[0].parts is not None
    assert contents[0].parts[0].text == "User prompt"


@pytest.mark.anyio
async def test_declarations_use_parameters_json_schema() -> None:
    """FunctionDeclarations use parameters_json_schema for tools with schema properties."""
    fake_sdk = FakeStreamClient([])
    client = GoogleGenAIClient(client=fake_sdk)

    def tool_with_args(name: str, count: int) -> str:
        """Run tool with args."""
        return f"{name}:{count}"

    def tool_without_args() -> str:
        """Run tool without args."""
        return "none"

    t1 = Tool(tool_with_args)
    t2 = Tool(tool_without_args)

    _ = [
        d
        async for d in client.stream_turn(
            system_instruction=None,
            messages=[],
            tools=[t1, t2],
        )
    ]

    recorded_call = fake_sdk.recorded_calls[0]
    config: types.GenerateContentConfig = recorded_call["config"]
    assert config.tools is not None
    first_tool = cast("Any", config.tools[0])
    declarations = first_tool.function_declarations
    assert declarations is not None
    assert len(declarations) == 2

    d1 = declarations[0]
    assert d1.name == "tool_with_args"
    assert d1.parameters_json_schema is not None
    assert "properties" in d1.parameters_json_schema

    d2 = declarations[1]
    assert d2.name == "tool_without_args"
    assert d2.parameters_json_schema is None


@pytest.mark.anyio
async def test_usage_counted_once_per_model_call() -> None:
    """Multiple stream chunks with cumulative usage produce exactly one final usage delta."""
    u1 = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=10,
        candidates_token_count=5,
        thoughts_token_count=2,
    )
    u2 = types.GenerateContentResponseUsageMetadata(
        prompt_token_count=10,
        candidates_token_count=15,
        thoughts_token_count=6,
    )
    chunk1 = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text="hello ")]))],
        usage_metadata=u1,
    )
    chunk2 = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[types.Part(text="world")]))],
        usage_metadata=u2,
    )
    fake_sdk = FakeStreamClient([chunk1, chunk2])
    client = GoogleGenAIClient(client=fake_sdk)

    deltas = [d async for d in client.stream_turn(system_instruction=None, messages=[], tools=[])]
    usage_deltas = [d for d in deltas if d.event_type == "usage"]
    assert len(usage_deltas) == 1
    final_usage = usage_deltas[0]
    assert final_usage.prompt_tokens == 10
    assert final_usage.completion_tokens == 15
    assert final_usage.thought_tokens == 6


@pytest.mark.anyio
async def test_thought_parts_stream_as_thoughts() -> None:
    """Candidate parts with thought=True or thought string emit thought deltas."""
    chunk = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(text="Thinking step 1", thought=True),
                        types.Part(text="Answering directly"),
                    ],
                )
            )
        ]
    )
    fake_sdk = FakeStreamClient([chunk])
    client = GoogleGenAIClient(client=fake_sdk)

    deltas = [d async for d in client.stream_turn(system_instruction=None, messages=[], tools=[])]
    assert len(deltas) == 2
    assert deltas[0].event_type == "thought"
    assert deltas[0].text == "Thinking step 1"
    assert deltas[1].event_type == "delta"
    assert deltas[1].text == "Answering directly"


@pytest.mark.anyio
async def test_thinking_config_includes_only_set_fields() -> None:
    """ThinkingConfig is built with only the non-None thinking parameters."""
    fake_sdk = FakeStreamClient([])
    client = GoogleGenAIClient(
        client=fake_sdk,
        thinking_budget=2048,
        include_thoughts=True,
    )

    _ = [d async for d in client.stream_turn(system_instruction=None, messages=[], tools=[])]
    recorded_call = fake_sdk.recorded_calls[0]
    config: types.GenerateContentConfig = recorded_call["config"]
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 2048
    assert config.thinking_config.include_thoughts is True
    assert config.thinking_config.thinking_level is None
