"""Structural tests for Chapter 6 reference-docs-and-uvx-guides.

Guards the five documentation entrypoints that make the notes example
family discoverable from the top-level docs surface and that keep the
``uvx run`` story as the primary MCP-facing invocation.

If any of these files disappear or drift away from the expected
headings, the reference-examples chooser regresses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

DOCS_DIR = Path(__file__).resolve().parents[2] / "docs"
NOTES_DIR = DOCS_DIR / "examples" / "notes"
USAGE_DIR = DOCS_DIR / "usage"

EXPECTED_FILES: tuple[Path, ...] = (
    NOTES_DIR / "README.md",
    NOTES_DIR / "advanced_alchemy" / "README.md",
    NOTES_DIR / "sqlspec" / "README.md",
    USAGE_DIR / "reference_examples.rst",
    USAGE_DIR / "uvx_guide.rst",
)


@pytest.mark.parametrize("path", EXPECTED_FILES, ids=lambda p: str(p.relative_to(DOCS_DIR)))
def test_reference_doc_file_present(path: Path) -> None:
    assert path.is_file(), f"Expected docs file {path} to exist"


README_FILES = (
    NOTES_DIR / "README.md",
    NOTES_DIR / "advanced_alchemy" / "README.md",
    NOTES_DIR / "sqlspec" / "README.md",
)


@pytest.mark.parametrize("path", README_FILES, ids=lambda p: str(p.relative_to(DOCS_DIR)))
def test_readme_has_variants_and_uvx(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert "uvx" in text, f"{path} must mention 'uvx'"
    # Accept either 'Variants' or 'Variant matrix' as the variants heading.
    assert "Variant" in text, f"{path} must contain a 'Variants' heading"


def test_reference_examples_links_both_families() -> None:
    text = (USAGE_DIR / "reference_examples.rst").read_text(encoding="utf-8")
    assert "advanced_alchemy" in text, "reference_examples.rst must link the AA family"
    assert "sqlspec" in text, "reference_examples.rst must link the SQLSpec family"
    # Comparison block must name all four auth modes.
    for needle in ("no-auth", "JWT", "Cloud Run", "IAP"):
        assert needle in text, f"reference_examples.rst missing comparison label: {needle!r}"


def test_uvx_guide_contains_template_and_pitfalls() -> None:
    text = (USAGE_DIR / "uvx_guide.rst").read_text(encoding="utf-8")
    assert "uvx" in text
    assert "--from litestar-mcp" in text, "uvx_guide.rst must show the uvx --from template"
    assert "Common pitfalls" in text or "Common Pitfalls" in text


def test_usage_index_links_reference_examples() -> None:
    text = (USAGE_DIR / "index.rst").read_text(encoding="utf-8")
    assert "reference_examples" in text, "usage/index.rst must link to reference_examples"
    assert "uvx_guide" in text, "usage/index.rst must include uvx_guide in its toctree"
