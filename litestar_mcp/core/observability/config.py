"""Telemetry configuration for OpenTelemetry spans in litestar-mcp."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable


@dataclass(slots=True)
class TelemetryConfig:
    """Configuration for OpenTelemetry observability in litestar-mcp."""

    enable_spans: bool = False
    provider_factory: "Callable[[], Any] | None" = None
    resource_attributes: dict[str, Any] = field(default_factory=dict)
    tracer_name: str = "litestar_mcp"


__all__ = ("TelemetryConfig",)
