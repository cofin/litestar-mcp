from __future__ import annotations

from typing import Any

from litestar_mcp.core.observability import SpanManager, TelemetryConfig


def test_span_manager_disabled_noop() -> None:
    config = TelemetryConfig(enable_spans=False)
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


def test_span_manager_enabled_with_mock_tracer() -> None:
    class MockSpan:
        def __init__(self, name: str, attributes: dict[str, Any]) -> None:
            self.name = name
            self.attributes = attributes
            self.ended = False
            self.exceptions: list[Exception] = []

        def end(self) -> None:
            self.ended = True

        def record_exception(self, exc: Exception) -> None:
            self.exceptions.append(exc)

        def set_status(self, status: Any) -> None:
            pass

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
        model="gemini-2.5-pro",
    )
    assert agent_span is not None
    assert agent_span.name == "agent.analyst.turn"
    assert agent_span.attributes["ai.agent.name"] == "analyst"
    assert agent_span.attributes["ai.agent.group"] == "research"
    assert agent_span.attributes["ai.model.name"] == "gemini-2.5-pro"
    assert agent_span.attributes["service.name"] == "litestar-mcp-test"
    manager.end_span(agent_span)
    assert agent_span.ended
