"""Tests for MCPConfig."""

from pathlib import Path

import pytest

from litestar_mcp import MCPConfig, MCPSkillsConfig


class TestMCPConfig:
    """Test suite for MCPConfig."""

    def test_config_defaults(self) -> "None":
        """Test default configuration values."""
        config = MCPConfig()
        assert config.base_path == "/mcp"

    def test_config_custom_values(self) -> "None":
        """Test custom configuration values."""
        config = MCPConfig(base_path="/api/mcp")
        assert config.base_path == "/api/mcp"


def test_skills_config_defaults(tmp_path: Path) -> "None":
    """Test MCPSkillsConfig defaults."""
    config = MCPSkillsConfig(paths=[tmp_path])
    assert config.directory_read is True
    assert config.max_files_per_skill == 512
    assert config.max_bytes_per_skill == 16_777_216


def test_skills_config_rejects_empty_paths() -> "None":
    """Test MCPSkillsConfig rejects empty paths."""
    with pytest.raises(ValueError, match="paths"):
        MCPSkillsConfig(paths=[])


@pytest.mark.parametrize(
    "max_files_per_skill,max_bytes_per_skill,match",
    [
        (0, 512, "max_files_per_skill must be positive"),
        (512, 0, "max_bytes_per_skill must be positive"),
    ],
)
def test_skills_config_rejects_non_positive_limits(
    tmp_path: Path,
    max_files_per_skill: int,
    max_bytes_per_skill: int,
    match: str,
) -> "None":
    """Test MCPSkillsConfig rejects non-positive limits."""
    with pytest.raises(ValueError, match=match):
        MCPSkillsConfig(
            paths=[tmp_path],
            max_files_per_skill=max_files_per_skill,
            max_bytes_per_skill=max_bytes_per_skill,
        )


def test_mcp_config_skills_defaults_to_none() -> "None":
    """Test MCPConfig skills defaults to None."""
    config = MCPConfig()
    assert config.skills is None


def test_mcp_config_accepts_skills_config(tmp_path: Path) -> "None":
    """Test MCPConfig accepts MCPSkillsConfig."""
    skills_config = MCPSkillsConfig(paths=[tmp_path])
    config = MCPConfig(skills=skills_config)
    assert config.skills is not None
    assert config.skills.paths == (tmp_path,)


def test_skills_config_coerces_str_paths(tmp_path: Path) -> "None":
    """Test MCPSkillsConfig coerces string paths to Path objects."""
    config = MCPSkillsConfig(paths=[str(tmp_path)])  # type: ignore[list-item]
    assert config.paths == (tmp_path,)


def test_skills_config_rejects_a_bare_string_for_paths(tmp_path: Path) -> "None":
    """A bare string is not a sequence of paths; it must not be shredded per character."""
    with pytest.raises(ValueError, match="paths must be a sequence of paths"):
        MCPSkillsConfig(paths=str(tmp_path))  # type: ignore[arg-type]


def test_mcp_endpoint_still_serves_with_skills_field(tmp_path: Path) -> "None":
    """Test MCP endpoint works with skills field (regression guard for msgspec)."""
    from litestar import Litestar
    from litestar.testing import TestClient

    from litestar_mcp import LitestarMCP
    from tests.unit.conftest import mcp_post

    app = Litestar(plugins=[LitestarMCP(MCPConfig())])
    client = TestClient(app=app)
    response = mcp_post(client, "server/discover")
    assert response.status_code == 200
    body = response.json()
    assert "result" in body


def test_root_exports_skills_config() -> "None":
    """Test MCPSkillsConfig is exported from litestar_mcp."""
    import litestar_mcp

    assert "MCPSkillsConfig" in litestar_mcp.__all__
