"""Snippet: projecting Agent Skills as MCP prompts and injecting SkillCatalog."""

# --8<-- [start:example]
from pathlib import Path
from typing import Any

from litestar import Litestar, get

from litestar_mcp import LitestarMCP, MCPConfig, MCPSkillsConfig, SkillCatalog

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


@get("/skills-catalog")
async def list_skills_catalog(skill_catalog: SkillCatalog) -> dict[str, Any]:
    skills = await skill_catalog.list_skills()
    return {
        "skills": [
            {
                "name": skill.name,
                "description": skill.description,
                "tags": list(skill.tags),
            }
            for skill in skills
        ]
    }


config = MCPConfig(skills=MCPSkillsConfig(paths=[SKILLS_ROOT], expose_as_prompts=True, list_changed=True))
app = Litestar(route_handlers=[list_skills_catalog], plugins=[LitestarMCP(config)])
# --8<-- [end:example]


def build(skills_root: Path | None = None) -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    if skills_root is None:
        return app
    isolated_config = MCPConfig(
        skills=MCPSkillsConfig(paths=[skills_root], expose_as_prompts=True, list_changed=True),
    )
    return Litestar(route_handlers=[list_skills_catalog], plugins=[LitestarMCP(isolated_config)])
