"""Tests for MCPConfig."""

from litestar_mcp import MCPConfig


class TestMCPConfig:
    """Test suite for MCPConfig."""

    def test_config_defaults(self) -> None:
        """Test default configuration values."""
        config = MCPConfig()
        assert config.base_path == "/mcp"

    def test_config_custom_values(self) -> None:
        """Test custom configuration values."""
        config = MCPConfig(base_path="/api/mcp")
        assert config.base_path == "/api/mcp"
