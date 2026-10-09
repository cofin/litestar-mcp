"""Unit tests for OpenTelemetry observability and SpanManager."""

from typing import Any

from litestar_mcp.core.observability import SpanManager, TelemetryConfig
from litestar_mcp.core.observability.semantics import (
    GEN_AI_AGENT_NAME,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_OPERATION_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    LITESTAR_MCP_AGENT_GROUP,
    LITESTAR_MCP_TURN_ID,
)


def test_span_manager_disabled_by_default() -> None:
    """SpanManager is disabled by default matching sqlspec convention."""
    config = TelemetryConfig()
    assert not config.enable_spans
    manager = SpanManager(config)
    assert not manager.is_enabled

    span = manager.start_span("test.span")
    assert span is None

    agent_span = manager.start_agent_span(agent_name="tester")
    assert agent_span is None

    tool_span = manager.start_tool_span(tool_name="adder")
    assert tool_span is None

    manager.end_span(span)

    with manager.span("context.span") as s:
        assert s is None

    with manager.use(span) as active:
        assert active is None


def test_span_manager_enabled_with_mock_tracer() -> None:
    """SpanManager populates GenAI semantic convention attributes."""

    class MockSpan:
        def __init__(self, name: str, attributes: dict[str, Any]) -> None:
            self.name = name
            self.attributes = dict(attributes)
            self.ended = False
            self.exceptions: list[Exception] = []

        def end(self) -> None:
            self.ended = True

        def record_exception(self, exc: Exception) -> None:
            self.exceptions.append(exc)

        def set_status(self, status: Any) -> None:
            pass

        def set_attribute(self, key: str, value: Any) -> None:
            self.attributes[key] = value

    class MockTracer:
        def start_span(self, name: str, attributes: dict[str, Any], kind: Any = None) -> MockSpan:
            return MockSpan(name, attributes)

    class MockProvider:
        def get_tracer(self, name: str) -> MockTracer:
            return MockTracer()

    config = TelemetryConfig(
        enable_spans=True,
        provider_factory=MockProvider,
        resource_attributes={"service.name": "litestar-mcp-test"},
    )
    manager = SpanManager(config)
    assert manager.is_enabled

    agent_span = manager.start_agent_span(
        agent_name="analyst",
        agent_group="research",
        session_id="sess-123",
        turn_id="turn-456",
        model="gemini-3.8-flash",
    )
    assert agent_span is not None
    assert agent_span.name == "invoke_agent analyst"
    assert agent_span.attributes[GEN_AI_OPERATION_NAME] == "invoke_agent"
    assert agent_span.attributes[GEN_AI_AGENT_NAME] == "analyst"
    assert agent_span.attributes[LITESTAR_MCP_AGENT_GROUP] == "research"
    assert agent_span.attributes[GEN_AI_CONVERSATION_ID] == "sess-123"
    assert agent_span.attributes[LITESTAR_MCP_TURN_ID] == "turn-456"
    assert agent_span.attributes[GEN_AI_REQUEST_MODEL] == "gemini-3.8-flash"
    assert agent_span.attributes["service.name"] == "litestar-mcp-test"

    manager.set_usage(agent_span, prompt_tokens=100, completion_tokens=25)
    assert agent_span.attributes[GEN_AI_USAGE_INPUT_TOKENS] == 100
    assert agent_span.attributes[GEN_AI_USAGE_OUTPUT_TOKENS] == 25

    manager.end_span(agent_span)
    assert agent_span.ended

    tool_span = manager.start_tool_span(
        tool_name="calculator",
        call_id="call-1",
    )
    assert tool_span is not None
    assert tool_span.name == "execute_tool calculator"
    assert tool_span.attributes[GEN_AI_OPERATION_NAME] == "execute_tool"
    assert tool_span.attributes[GEN_AI_TOOL_NAME] == "calculator"
    assert tool_span.attributes[GEN_AI_TOOL_CALL_ID] == "call-1"
    manager.end_span(tool_span)
    assert tool_span.ended
