"""OpenTelemetry observability package for litestar-mcp."""

from __future__ import annotations

from litestar_mcp.core.observability._spans import SpanManager
from litestar_mcp.core.observability.config import TelemetryConfig
from litestar_mcp.core.observability.semantics import (
    AI_AGENT_ACTION,
    AI_AGENT_GROUP,
    AI_AGENT_NAME,
    AI_MODEL_NAME,
    AI_MODEL_PROVIDER,
    AI_SESSION_ID,
    AI_TOKENS_COMPLETION,
    AI_TOKENS_PROMPT,
    AI_TOKENS_TOTAL,
    AI_TOOL_CALL_ID,
    AI_TOOL_NAME,
    AI_TURN_ID,
)

__all__ = (
    "AI_AGENT_ACTION",
    "AI_AGENT_GROUP",
    "AI_AGENT_NAME",
    "AI_MODEL_NAME",
    "AI_MODEL_PROVIDER",
    "AI_SESSION_ID",
    "AI_TOKENS_COMPLETION",
    "AI_TOKENS_PROMPT",
    "AI_TOKENS_TOTAL",
    "AI_TOOL_CALL_ID",
    "AI_TOOL_NAME",
    "AI_TURN_ID",
    "SpanManager",
    "TelemetryConfig",
)
