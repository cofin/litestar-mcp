"""Verify litestar-mcp Pygments style entry points resolve correctly.

These tests guard the Pygments entry-point registration declared in
``pyproject.toml`` under ``[project.entry-points."pygments.styles"]``. The
styles themselves live in ``tools/sphinx_ext/pygments_styles.py`` and are
referenced from ``docs/conf.py`` via ``pygments_style`` /
``pygments_dark_style``.
"""

import pytest
from pygments.style import Style  # type: ignore[import-untyped]
from pygments.styles import get_style_by_name  # type: ignore[import-untyped]


@pytest.mark.parametrize("style_name", ["litestar-mcp-light", "litestar-mcp-dark"])
def test_style_resolves_via_entry_point(style_name: str) -> None:
    """The entry point lookup returns a subclass of ``pygments.style.Style``."""
    style_cls = get_style_by_name(style_name)
    assert issubclass(style_cls, Style)


def test_style_classes_import_directly() -> None:
    """Both style classes import from ``tools.sphinx_ext.pygments_styles``."""
    from tools.sphinx_ext.pygments_styles import LitestarMcpDarkStyle, LitestarMcpLightStyle

    assert LitestarMcpLightStyle.name == "litestar-mcp-light"
    assert LitestarMcpDarkStyle.name == "litestar-mcp-dark"
