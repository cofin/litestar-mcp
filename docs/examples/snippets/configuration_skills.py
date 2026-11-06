"""Snippet: serving Agent Skills over MCP from a directory."""

# --8<-- [start:example]
from pathlib import Path

from litestar import Litestar

from litestar_mcp import LitestarMCP, MCPConfig, MCPSkillsConfig

SKILLS_ROOT = Path(__file__).resolve().parents[1] / "skills"

config = MCPConfig(skills=MCPSkillsConfig(paths=[SKILLS_ROOT]))
app = Litestar(plugins=[LitestarMCP(config)])
# --8<-- [end:example]


def build() -> Litestar:
    """Return the configured Litestar application for snippet smoke testing."""
    return app
