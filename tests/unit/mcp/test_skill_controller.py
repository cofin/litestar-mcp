"""Unit tests for SkillController."""

from __future__ import annotations

import pytest

from litestar_mcp.agent.context import ToolContext
from litestar_mcp.agent.tools import tool
from litestar_mcp.mcp.prompt_controller import prompt
from litestar_mcp.mcp.skill_controller import SkillController


class DatabaseSkillController(SkillController):
    """Database optimization and inspection skill."""

    name = "db_optimizer"
    description = "Inspects and optimizes SQL queries."
    instructions = "Never execute destructive DDL."
    tags = ("database", "sql")
    examples = ("Analyze slow query", "Inspect index usage")

    @tool(description="Estimate query cost.")
    async def estimate_cost(self, sql: str, ctx: ToolContext) -> dict[str, object]:
        """Return estimated cost for a query within the caller's tenant."""
        return {"sql": sql, "tenant": ctx.tenant_id, "cost": 42}

    @prompt(title="Optimization Checklist", description="Generate optimization checklist.")
    def checklist(self, engine: str) -> str:
        """Return checklist prompt for the given database engine."""
        return f"Provide checklist for {engine}"


@pytest.mark.asyncio
async def test_skill_controller_tools_prompts_and_a2a_projection() -> None:
    """Verify SkillController unifies tools, prompts, instructions, and A2A skill metadata."""
    skill = DatabaseSkillController()
    assert skill.get_instructions() == "Never execute destructive DDL."

    tools = skill.get_tools()
    assert len(tools) == 1
    cost_tool = tools[0]
    assert cost_tool.name == "estimate_cost"
    assert cost_tool.requires_context is True
    assert "ctx" not in cost_tool.input_schema["properties"]
    assert "sql" in cost_tool.input_schema["properties"]

    ctx = ToolContext(tenant_id="acme")
    result = await cost_tool.execute({"sql": "SELECT * FROM users"}, context=ctx)
    assert result == {"sql": "SELECT * FROM users", "tenant": "acme", "cost": 42}

    prompts = skill.get_prompts()
    assert len(prompts) == 1
    assert prompts[0].name == "db_optimizer/checklist"

    a2a_skill = skill.to_a2a_skill()
    assert a2a_skill["id"] == "db_optimizer"
    assert a2a_skill["name"] == "db_optimizer"
    assert a2a_skill["description"] == "Inspects and optimizes SQL queries."
    assert a2a_skill["tags"] == ["database", "sql"]
    assert a2a_skill["examples"] == ["Analyze slow query", "Inspect index usage"]
