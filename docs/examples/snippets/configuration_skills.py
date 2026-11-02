"""Snippet: serving Agent Skills over MCP from a directory."""

from pathlib import Path

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig, MCPSkillsConfig

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def build() -> "Litestar":
    # start-example
    config = MCPConfig(skills=MCPSkillsConfig(paths=[SKILLS_ROOT]))
    app = Litestar(plugins=[LitestarMCP(config)])
    # end-example
    return app
