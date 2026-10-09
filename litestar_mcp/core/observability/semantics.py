"""OpenTelemetry semantic convention constants for AI agent and tool operations."""

from __future__ import annotations

AI_AGENT_NAME = "ai.agent.name"
AI_AGENT_GROUP = "ai.agent.group"
AI_AGENT_ACTION = "ai.agent.action"
AI_TOOL_NAME = "ai.tool.name"
AI_TOOL_CALL_ID = "ai.tool.call_id"
AI_MODEL_NAME = "ai.model.name"
AI_MODEL_PROVIDER = "ai.model.provider"
AI_TOKENS_PROMPT = "ai.tokens.prompt"
AI_TOKENS_COMPLETION = "ai.tokens.completion"
AI_TOKENS_TOTAL = "ai.tokens.total"
AI_SESSION_ID = "ai.session.id"
AI_TURN_ID = "ai.turn.id"

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
)
