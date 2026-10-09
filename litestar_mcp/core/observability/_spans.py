"""Lazy OpenTelemetry SpanManager implementation with zero-overhead no-op fallback."""

from __future__ import annotations

import logging
from contextlib import contextmanager, suppress
from importlib import import_module
from typing import TYPE_CHECKING, Any

from litestar_mcp.core.observability.config import TelemetryConfig
from litestar_mcp.core.observability.semantics import (
    AI_AGENT_ACTION,
    AI_AGENT_GROUP,
    AI_AGENT_NAME,
    AI_MODEL_NAME,
    AI_MODEL_PROVIDER,
    AI_TOOL_CALL_ID,
    AI_TOOL_NAME,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_logger = logging.getLogger("litestar_mcp.observability")


class SpanManager:
    """Lazy OpenTelemetry span manager with graceful degradation.

    Provides zero-overhead no-op execution when OpenTelemetry is not installed
    or when spans are disabled.
    """

    __slots__ = (
        "_enabled",
        "_provider_factory",
        "_resource_attributes",
        "_span_kind",
        "_status_cls",
        "_status_code_cls",
        "_trace_api",
        "_tracer",
        "_tracer_name",
    )

    def __init__(self, telemetry: TelemetryConfig | None = None) -> None:
        telemetry = telemetry or TelemetryConfig()
        self._enabled = bool(telemetry.enable_spans)
        self._provider_factory = telemetry.provider_factory
        self._resource_attributes = dict(telemetry.resource_attributes or {})
        self._tracer_name = telemetry.tracer_name
        self._trace_api: Any | None = None
        self._status_cls: Any | None = None
        self._status_code_cls: Any | None = None
        self._span_kind: Any | None = None
        self._tracer: Any | None = None
        if self._enabled:
            self._resolve_api()

    @property
    def is_enabled(self) -> bool:
        """Return True once OpenTelemetry spans are available and active."""
        return bool(self._enabled and self._tracer)

    def start_agent_span(
        self,
        agent_name: str = "",
        action: str = "turn",
        *,
        agent_group: str | None = None,
        model: str | None = None,
        provider: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        """Start a span representing an agent turn with standard AI semantic conventions."""
        if not self._enabled:
            return None
        attrs: dict[str, Any] = {
            AI_AGENT_NAME: agent_name,
            AI_AGENT_ACTION: action,
        }
        if agent_group:
            attrs[AI_AGENT_GROUP] = agent_group
        if model:
            attrs[AI_MODEL_NAME] = model
        if provider:
            attrs[AI_MODEL_PROVIDER] = provider
        if attributes:
            attrs.update(attributes)
        attrs.update(self._resource_attributes)
        return self._start_span(f"agent.{agent_name}.{action}", attrs)

    def start_tool_span(
        self,
        tool_name: str = "",
        call_id: str | None = None,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> Any:
        """Start a span representing a tool execution."""
        if not self._enabled:
            return None
        attrs: dict[str, Any] = {
            AI_TOOL_NAME: tool_name,
        }
        if call_id:
            attrs[AI_TOOL_CALL_ID] = call_id
        if attributes:
            attrs.update(attributes)
        attrs.update(self._resource_attributes)
        return self._start_span(f"tool.{tool_name}.execute", attrs)

    def start_span(self, name: str, attributes: dict[str, Any] | None = None) -> Any:
        """Start a generic span with custom attributes."""
        if not self._enabled:
            return None
        merged = dict(self._resource_attributes)
        if attributes:
            merged.update(attributes)
        return self._start_span(name, merged)

    def end_span(self, span: Any, error: Exception | None = None) -> None:
        """Close a span and record errors when provided."""
        if span is None:
            return
        with suppress(Exception):
            if error:
                if hasattr(span, "record_exception"):
                    span.record_exception(error)
                if self._status_cls and self._status_code_cls and hasattr(span, "set_status"):
                    status = self._status_cls(self._status_code_cls.ERROR, str(error))
                    span.set_status(status)
            span.end()

    @contextmanager
    def span(self, name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
        """Context manager for tracing blocks with automatic end and error capture."""
        s = self.start_span(name, attributes)
        try:
            yield s
        except Exception as exc:
            self.end_span(s, error=exc)
            raise
        else:
            self.end_span(s)

    def _start_span(self, name: str, attributes: dict[str, Any]) -> Any:
        tracer = self._get_tracer()
        if tracer is None:
            return None
        span_kind = self._span_kind
        if span_kind is None:
            return tracer.start_span(name=name, attributes=attributes)
        return tracer.start_span(name=name, attributes=attributes, kind=span_kind)

    def _get_tracer(self) -> Any:
        if not self._enabled:
            return None
        if self._tracer is None:
            self._resolve_api()
        return self._tracer

    def _resolve_api(self) -> None:
        provider = None
        if self._provider_factory is not None:
            with suppress(Exception):
                provider = self._provider_factory()

        try:
            trace = import_module("opentelemetry.trace")
            status_module = import_module("opentelemetry.trace.status")
        except ImportError:
            if provider is not None and hasattr(provider, "get_tracer"):
                self._tracer = provider.get_tracer(self._tracer_name)
                return
            _logger.debug("OpenTelemetry import failed - disabling spans")
            self._enabled = False
            self._tracer = None
            return

        span_kind_cls = trace.SpanKind
        status_cls = status_module.Status
        status_code_cls = status_module.StatusCode

        if provider is not None and hasattr(provider, "get_tracer"):
            self._tracer = provider.get_tracer(self._tracer_name)
        else:
            self._tracer = trace.get_tracer(self._tracer_name)

        self._trace_api = trace
        self._status_cls = status_cls
        self._status_code_cls = status_code_cls
        self._span_kind = span_kind_cls.INTERNAL


__all__ = ("SpanManager",)
