"""OpenTelemetry observability package for litestar-mcp."""

from litestar_mcp.core.observability._spans import SpanManager
from litestar_mcp.core.observability.config import TelemetryConfig
from litestar_mcp.core.observability.semantics import (
    ERROR_TYPE,
    GEN_AI_AGENT_NAME,
    GEN_AI_CONVERSATION_ID,
    GEN_AI_OPERATION_NAME,
    GEN_AI_PROVIDER_NAME,
    GEN_AI_REQUEST_MODEL,
    GEN_AI_TOOL_CALL_ID,
    GEN_AI_TOOL_NAME,
    GEN_AI_USAGE_INPUT_TOKENS,
    GEN_AI_USAGE_OUTPUT_TOKENS,
    LITESTAR_MCP_AGENT_GROUP,
    LITESTAR_MCP_TURN_ID,
)

__all__ = (
    "ERROR_TYPE",
    "GEN_AI_AGENT_NAME",
    "GEN_AI_CONVERSATION_ID",
    "GEN_AI_OPERATION_NAME",
    "GEN_AI_PROVIDER_NAME",
    "GEN_AI_REQUEST_MODEL",
    "GEN_AI_TOOL_CALL_ID",
    "GEN_AI_TOOL_NAME",
    "GEN_AI_USAGE_INPUT_TOKENS",
    "GEN_AI_USAGE_OUTPUT_TOKENS",
    "LITESTAR_MCP_AGENT_GROUP",
    "LITESTAR_MCP_TURN_ID",
    "SpanManager",
    "TelemetryConfig",
)
