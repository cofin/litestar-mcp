"""Snippet: serving Agent Skills over MCP from a directory."""

from pathlib import Path

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig, MCPSkillsConfig

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"


def build() -> "Litestar":
    # start-example
    config = MCPConfig(
        skills=MCPSkillsConfig(
            paths=[SKILLS_ROOT],
            directory_read=True,
            max_files_per_skill=512,
            max_bytes_per_skill=16_777_216,
        ),
    )
    app = Litestar(route_handlers=[], plugins=[LitestarMCP(config)])
    # end-example
    return app
