"""Unit tests for PromptController, PromptMessage, and @prompt."""

from __future__ import annotations

import pytest

from litestar_mcp.mcp.prompt_controller import PromptController, PromptMessage, prompt


class SqlPromptController(PromptController):
    """Prompt controller for SQL optimization templates."""

    prefix = "sql"
    instructions = "Always explain execution plans clearly."

    @prompt(title="Explain Query", description="Explain a SQL query plan.")
    def explain_query(self, query: str, dialect: str = "postgres") -> str:
        """Return a prompt asking to explain a SQL query."""
        return f"Explain ({dialect}): {query}"

    @prompt(title="Async Critique", description="Critique a schema design.")
    async def critique_schema(self, ddl: str) -> list[PromptMessage]:
        """Return structured prompt messages for schema critique."""
        return [
            PromptMessage.user(f"Review DDL:\n{ddl}"),
            PromptMessage.assistant("I will check normalization and indexing."),
        ]


def test_prompt_message_helpers_and_mcp_dict() -> None:
    """Verify PromptMessage factory constructors and wire dictionary format."""
    user_msg = PromptMessage.user("Hello")
    assert user_msg.role == "user"
    assert user_msg.to_mcp_dict() == {
        "role": "user",
        "content": {"type": "text", "text": "Hello"},
    }

    named_msg = PromptMessage(role="assistant", content="Hi", name="helper")
    assert named_msg.to_mcp_dict() == {
        "role": "assistant",
        "name": "helper",
        "content": {"type": "text", "text": "Hi"},
    }


@pytest.mark.asyncio
async def test_prompt_controller_discovery_and_execution() -> None:
    """Verify PromptController discovers @prompt methods, prefixes names, and injects instructions."""
    ctrl = SqlPromptController()
    prompts = ctrl.get_prompts()
    assert len(prompts) == 2

    by_name = {p.name: p for p in prompts}
    assert "sql/explain_query" in by_name
    assert "sql/critique_schema" in by_name

    explain_reg = by_name["sql/explain_query"]
    assert explain_reg.title == "Explain Query"
    assert explain_reg.description == "Explain a SQL query plan."
    args = explain_reg.get_arguments()
    assert len(args) == 2
    assert args[0]["name"] == "query"
    assert args[0]["required"] is True
    assert args[1]["name"] == "dialect"
    assert args[1]["required"] is False

    assert explain_reg.fn is not None
    sync_messages = explain_reg.fn(query="SELECT 1")
    assert len(sync_messages) == 2
    assert sync_messages[0]["role"] == "system"
    assert sync_messages[0]["content"]["text"] == "Always explain execution plans clearly."
    assert sync_messages[1]["role"] == "user"
    assert sync_messages[1]["content"]["text"] == "Explain (postgres): SELECT 1"

    critique_reg = by_name["sql/critique_schema"]
    assert critique_reg.fn is not None
    async_messages = await critique_reg.fn(ddl="CREATE TABLE t (id INT);")
    assert len(async_messages) == 3
    assert async_messages[0]["role"] == "system"
    assert async_messages[1]["role"] == "user"
    assert async_messages[2]["role"] == "assistant"
