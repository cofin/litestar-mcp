"""Execution budget guards and exceptions for AgentRuntime."""

from __future__ import annotations

from dataclasses import dataclass

from litestar_mcp.core.exceptions import LitestarMCPError


class BudgetExceededError(LitestarMCPError):
    """Raised when an agent turn exceeds its configured turn, tool-call, or timeout budget."""


@dataclass(slots=True)
class TurnBudget:
    """Configurable execution limits for a single agent turn."""

    max_turns: int = 15
    max_tool_calls: int = 50
    timeout_seconds: float = 300.0

    def __post_init__(self) -> None:
        if self.max_turns <= 0:
            msg = "max_turns must be positive"
            raise ValueError(msg)
        if self.max_tool_calls <= 0:
            msg = "max_tool_calls must be positive"
            raise ValueError(msg)
        if self.timeout_seconds <= 0:
            msg = "timeout_seconds must be positive"
            raise ValueError(msg)


__all__ = (
    "BudgetExceededError",
    "TurnBudget",
)
