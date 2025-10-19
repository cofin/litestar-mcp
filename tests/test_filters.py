"""Tests for MCP filtering logic."""

from typing import Any

from litestar import get

from litestar_mcp.config import MCPConfig
from litestar_mcp.filters import should_include_handler


class TestFilteringBasics:
    """Tests for basic filtering behavior."""

    def test_no_filters_includes_all(self) -> None:
        """Test that no filters means all handlers included."""

        @get("/test")
        async def test_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig()
        assert should_include_handler(test_handler, config) is True

    def test_empty_include_operations_excludes_all(self) -> None:
        """Test that empty include_operations list excludes all handlers."""

        @get("/test")
        async def test_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(include_operations=[])
        assert should_include_handler(test_handler, config) is False

    def test_empty_exclude_operations_includes_all(self) -> None:
        """Test that empty exclude_operations list has no effect."""

        @get("/test")
        async def test_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(exclude_operations=[])
        assert should_include_handler(test_handler, config) is True


class TestOperationFiltering:
    """Tests for operation-based filtering."""

    def test_include_operations_filters_correctly(self) -> None:
        """Test that include_operations only includes specified operations."""

        @get("/included")
        async def included_handler() -> "dict[str, str]":
            return {}

        @get("/excluded")
        async def excluded_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(include_operations=["included_handler"])
        assert should_include_handler(included_handler, config) is True
        assert should_include_handler(excluded_handler, config) is False

    def test_exclude_operations_removes_specified(self) -> None:
        """Test that exclude_operations removes specified operations."""

        @get("/normal")
        async def normal_handler() -> "dict[str, str]":
            return {}

        @get("/excluded")
        async def excluded_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(exclude_operations=["excluded_handler"])
        assert should_include_handler(normal_handler, config) is True
        assert should_include_handler(excluded_handler, config) is False

    def test_exclude_overrides_include_operations(self) -> None:
        """Test that exclude_operations overrides include_operations."""

        @get("/conflict")
        async def conflict_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(
            include_operations=["conflict_handler"],
            exclude_operations=["conflict_handler"],
        )
        assert should_include_handler(conflict_handler, config) is False


class TestTagFiltering:
    """Tests for tag-based filtering."""

    def test_include_tags_requires_match(self) -> None:
        """Test that include_tags requires at least one matching tag."""

        @get("/public", tags=["public"])
        async def public_handler() -> "dict[str, str]":
            return {}

        @get("/internal", tags=["internal"])
        async def internal_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(include_tags=["public"])
        assert should_include_handler(public_handler, config) is True
        assert should_include_handler(internal_handler, config) is False

    def test_include_tags_or_logic(self) -> None:
        """Test that include_tags uses OR logic (any matching tag)."""

        @get("/multi", tags=["public", "v2"])
        async def multi_tag_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(include_tags=["public", "api"])
        assert should_include_handler(multi_tag_handler, config) is True

    def test_exclude_tags_removes_any_match(self) -> None:
        """Test that exclude_tags removes handlers with any matching tag."""

        @get("/admin", tags=["admin"])
        async def admin_handler() -> "dict[str, str]":
            return {}

        @get("/public", tags=["public"])
        async def public_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(exclude_tags=["admin"])
        assert should_include_handler(admin_handler, config) is False
        assert should_include_handler(public_handler, config) is True

    def test_exclude_tags_overrides_include_tags(self) -> None:
        """Test that exclude_tags overrides include_tags."""

        @get("/conflict", tags=["public", "admin"])
        async def conflict_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(include_tags=["public"], exclude_tags=["admin"])
        assert should_include_handler(conflict_handler, config) is False

    def test_empty_include_tags_excludes_all(self) -> None:
        """Test that empty include_tags list excludes all handlers."""

        @get("/test", tags=["public"])
        async def test_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(include_tags=[])
        assert should_include_handler(test_handler, config) is False

    def test_no_tags_with_include_tags_excludes(self) -> None:
        """Test that handler without tags is excluded when include_tags is set."""

        @get("/no-tags")
        async def no_tags_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(include_tags=["public"])
        assert should_include_handler(no_tags_handler, config) is False

    def test_no_tags_with_exclude_tags_includes(self) -> None:
        """Test that handler without tags is included when only exclude_tags is set."""

        @get("/no-tags")
        async def no_tags_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(exclude_tags=["admin"])
        assert should_include_handler(no_tags_handler, config) is True


class TestFilterPrecedence:
    """Tests for filter precedence rules."""

    def test_tags_before_operations_precedence(self) -> None:
        """Test that tag filtering is applied before operation filtering."""

        @get("/endpoint", tags=["internal"])
        async def internal_tagged_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(
            include_tags=["public"],
            include_operations=["internal_tagged_handler"],
        )
        assert should_include_handler(internal_tagged_handler, config) is False

    def test_complex_filter_combination(self) -> None:
        """Test all four filter types working together."""

        @get("/test", tags=["public", "api"])
        async def test_handler() -> "dict[str, str]":
            return {}

        config = MCPConfig(
            include_tags=["public"],
            exclude_tags=["internal"],
            include_operations=["test_handler"],
            exclude_operations=["other_handler"],
        )
        assert should_include_handler(test_handler, config) is True

    def test_exclude_wins_in_all_scenarios(self) -> None:
        """Test that exclusions always win over inclusions."""

        @get("/test", tags=["public"])
        async def handler1() -> "dict[str, Any]":
            return {}

        @get("/test2", tags=["public"])
        async def handler2() -> "dict[str, Any]":
            return {}

        config1 = MCPConfig(include_operations=["handler1"], exclude_operations=["handler1"])
        assert should_include_handler(handler1, config1) is False

        config2 = MCPConfig(include_tags=["public"], exclude_tags=["public"])
        assert should_include_handler(handler2, config2) is False
