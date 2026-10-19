"""Tests for MCPConfig."""

from dataclasses import fields
from pathlib import Path
from typing import TYPE_CHECKING, Any, get_type_hints

import pytest
from litestar import Litestar
from litestar.testing import TestClient

import litestar_mcp
from litestar_mcp import (
    FilesystemSkillSource,
    LitestarMCP,
    MCPConfig,
    MCPSkillsConfig,
    Skill,
    SkillFile,
    SkillIntegrityError,
    SkillSource,
)
from tests.unit.conftest import mcp_post

if TYPE_CHECKING:
    from collections.abc import Sequence

    from litestar import Request


class _StubSkillSource:
    """Minimal SkillSource implementation for configuration tests."""

    async def list_skills(self, request: "Request[Any, Any, Any]", /) -> "Sequence[Skill]":
        del request
        return ()

    async def get_skill(self, request: "Request[Any, Any, Any]", name: "str", /) -> "Skill | None":
        del request, name
        return None

    async def read_file(self, request: "Request[Any, Any, Any]", skill: "Skill", file: "SkillFile", /) -> "bytes":
        del request, skill, file
        msg = "not found"
        raise LookupError(msg)


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
    assert config.sources == ()


def test_skills_config_rejects_empty_paths() -> "None":
    """Test MCPSkillsConfig rejects empty paths when sources is also empty."""
    with pytest.raises(ValueError, match="paths or sources"):
        MCPSkillsConfig(paths=[])


def test_skills_config_requires_paths_or_sources() -> "None":
    """Test MCPSkillsConfig requires at least one path or source."""
    with pytest.raises(ValueError, match="paths or sources"):
        MCPSkillsConfig()


def test_skills_config_accepts_sources_without_paths() -> "None":
    """Test MCPSkillsConfig accepts sources when paths is omitted."""
    source = _StubSkillSource()
    config = MCPSkillsConfig(sources=[source])
    assert config.paths == ()
    assert config.sources == (source,)


def test_skills_config_rejects_objects_that_are_not_sources() -> "None":
    """Test MCPSkillsConfig rejects entries in sources that do not implement SkillSource."""
    with pytest.raises(TypeError, match="SkillSource"):
        MCPSkillsConfig(sources=[object()])  # type: ignore[list-item]


def test_skills_config_appends_sources_after_v015_fields() -> "None":
    """Test MCPSkillsConfig keeps the 0.15.0 field order as a prefix and appends sources."""
    field_names = tuple(f.name for f in fields(MCPSkillsConfig))
    assert field_names == (
        "paths",
        "directory_read",
        "max_files_per_skill",
        "max_bytes_per_skill",
        "sources",
    )


def test_skills_config_type_hints_resolve_at_runtime() -> "None":
    """Test get_type_hints resolves MCPSkillsConfig and MCPConfig at runtime."""
    skills_hints = get_type_hints(MCPSkillsConfig)
    mcp_hints = get_type_hints(MCPConfig)
    assert "sources" in skills_hints
    assert "skills" in mcp_hints


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
    del tmp_path
    app = Litestar(plugins=[LitestarMCP(MCPConfig())])
    client = TestClient(app=app)
    response = mcp_post(client, "server/discover")
    assert response.status_code == 200
    body = response.json()
    assert "result" in body


def test_root_exports_skills_config() -> "None":
    """Test MCPSkillsConfig is exported from litestar_mcp."""
    assert "MCPSkillsConfig" in litestar_mcp.__all__


def test_root_exports_skill_source_api() -> "None":
    """Test SkillSource, FilesystemSkillSource, Skill, SkillFile, and SkillIntegrityError are exported."""
    expected = {
        "FilesystemSkillSource",
        "MCPSkillsConfig",
        "Skill",
        "SkillFile",
        "SkillIntegrityError",
        "SkillSource",
    }
    assert expected <= set(litestar_mcp.__all__)
    assert FilesystemSkillSource is not None
    assert Skill is not None
    assert SkillFile is not None
    assert SkillIntegrityError is not None
    assert SkillSource is not None
