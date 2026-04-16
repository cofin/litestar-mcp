"""Structural test for the usage-guide-rewrite restructure.

Guards the six focused ``docs/usage/`` pages that replace the original
flat structure (`configuration.rst`, `marking-routes.rst`, `examples.rst`).

If any of these pages disappear, the literalinclude-backed docs grid will
regress.
"""

from __future__ import annotations

from pathlib import Path

import pytest

USAGE_DIR = Path(__file__).resolve().parents[2] / "docs" / "usage"

EXPECTED_PAGES = (
    "configuration.rst",
    "marking_routes.rst",
    "tools_and_resources.rst",
    "discovery.rst",
    "auth.rst",
    "framework_integration.rst",
)


@pytest.mark.parametrize("page", EXPECTED_PAGES)
def test_usage_page_present(page: str) -> None:
    path = USAGE_DIR / page
    assert path.is_file(), f"Expected docs/usage/{page} to exist"


def test_usage_index_present() -> None:
    assert (USAGE_DIR / "index.rst").is_file()


def test_no_code_block_python_in_usage_pages() -> None:
    """Every Python example in docs/usage/ must use ``literalinclude``."""
    offenders: list[str] = []
    for rst_path in USAGE_DIR.glob("*.rst"):
        text = rst_path.read_text(encoding="utf-8")
        if ".. code-block:: python" in text:
            offenders.append(rst_path.name)
    assert not offenders, f"code-block:: python found in: {offenders}"
