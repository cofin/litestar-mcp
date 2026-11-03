"""Telemetry configuration for OpenTelemetry spans in litestar-mcp."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(slots=True)
class TelemetryConfig:
    """Configuration for OpenTelemetry observability in litestar-mcp."""

    enable_spans: bool = False
    """Whether to emit OpenTelemetry spans for MCP and agent operations."""
    provider_factory: "Callable[[], Any] | None" = None
    """Optional factory returning a custom OpenTelemetry `TracerProvider`."""
    resource_attributes: dict[str, Any] = field(default_factory=dict)
    """Extra OpenTelemetry resource attributes attached to emitted spans."""
    tracer_name: str = "litestar_mcp"
    """Instrumentation scope name passed to `get_tracer`."""


__all__ = ("TelemetryConfig",)
