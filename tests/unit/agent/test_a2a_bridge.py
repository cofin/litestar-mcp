"""Unit tests for A2A protocol bridge."""

import importlib
import sys
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from litestar_mcp.a2a import LitestarA2A
from litestar_mcp.agent.bridges.a2a import agent_to_a2a
from litestar_mcp.agent.runtime import AgentRuntime, TurnResponse
from litestar_mcp.agent.spec import Agent
from litestar_mcp.core.exceptions import MissingDependencyError


def test_agent_to_a2a_requires_base_url() -> None:
    """agent_to_a2a requires explicit base_url parameter."""
    agent = Agent(name="test_agent")
    with pytest.raises(TypeError):
        cast("Any", agent_to_a2a)(agent)


def test_agent_to_a2a_creates_plugin() -> None:
    """agent_to_a2a returns a configured LitestarA2A instance."""
    agent = Agent(name="assistant", description="Assistant agent")
    plugin = agent_to_a2a(agent, path="/a2a", base_url="https://api.example.com")
    assert isinstance(plugin, LitestarA2A)
    assert plugin.agent_card.name == "assistant"
    assert plugin.config.path == "/a2a"


def test_agent_to_a2a_missing_dependency(monkeypatch: pytest.MonkeyPatch) -> None:
    """Importing the A2A bridge raises MissingDependencyError when the a2a SDK is unavailable."""
    monkeypatch.delitem(sys.modules, "litestar_mcp.agent.bridges.a2a", raising=False)
    monkeypatch.setitem(sys.modules, "a2a.server.agent_execution", None)

    with pytest.raises(MissingDependencyError, match="a2a-sdk"):
        importlib.import_module("litestar_mcp.agent.bridges.a2a")


@pytest.mark.anyio
async def test_agent_to_a2a_executor_invokes_runtime() -> None:
    """A2A bridge executor dispatches user input through AgentRuntime."""
    from a2a.server.agent_execution import RequestContext

    agent = Agent(name="worker")
    mock_runtime = AsyncMock(spec=AgentRuntime)
    mock_runtime.run_turn.return_value = TurnResponse(
        turn_id="turn-1",
        session_id="session-1",
        output="Processed answer",
        agent_name="worker",
    )

    plugin = agent_to_a2a(
        agent,
        base_url="https://api.example.com",
        runtime=mock_runtime,
    )

    executor = cast("Any", plugin.request_handler).agent_executor
    queue = AsyncMock()
    ctx = RequestContext(
        call_context=AsyncMock(),
        task_id="t-123",
        context_id="ctx-456",
    )
    setattr(ctx, "get_user_input", lambda: "Hello agent")

    await executor.execute(ctx, queue)
    assert mock_runtime.run_turn.called
    req = mock_runtime.run_turn.call_args[0][0]
    assert req.user_message == "Hello agent"
    assert req.session_id == "ctx-456"
