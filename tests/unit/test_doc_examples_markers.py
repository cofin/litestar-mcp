"""Guard that ``# --8<-- [start:<section>]`` / ``# --8<-- [end:<section>]`` blocks stand alone.

Invokes ``tools/ci/validate_doc_markers.py`` against ``docs/examples`` and
asserts exit code ``0``. This catches copy-paste typos, disallowed inline
comments, and snippets that fail to compile as standalone Python.
"""

from pathlib import Path

import pytest
from tools.ci.validate_doc_markers import (
    find_disallowed_comments,
    iter_marker_blocks,
    main,
    validate_file,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_doc_example_markers_are_self_contained() -> None:
    """All docs/examples files pass marker compilation and zero-comment validation."""
    exit_code = main([str(REPO_ROOT / "docs" / "examples")])
    assert exit_code == 0


def test_iter_marker_blocks_parses_pymdownx_snippet_sections() -> None:
    """iter_marker_blocks extracts # --8<-- [start:example] .. # --8<-- [end:example]."""
    source = '"""Module docstring."""\n# --8<-- [start:example]\nx = 41 + 1\n# --8<-- [end:example]\n'
    blocks = iter_marker_blocks(source)
    assert blocks == [(2, 4, "x = 41 + 1")]


@pytest.mark.parametrize(
    "source",
    [
        "# --8<-- [start:example]\nx = 1\n",
        "# --8<-- [start:example]\nx = 1\n# --8<-- [end:other]\n",
    ],
)
def test_iter_marker_blocks_rejects_unterminated_section(source: str) -> None:
    """iter_marker_blocks raises ValueError when # --8<-- [end:example] is missing or mismatched."""
    with pytest.raises(ValueError, match=r"unterminated.*start:example"):
        iter_marker_blocks(source)


def test_find_disallowed_comments_allows_pep723_and_markers_only(tmp_path: Path) -> None:
    """find_disallowed_comments permits PEP 723 headers and # --8<-- markers but rejects inline comments."""
    valid_source = (
        "# /// script\n"
        '# requires-python = ">=3.10"\n'
        "# ///\n"
        "# --8<-- [start:example]\n"
        'url = "https://example.com/#anchor"\n'
        "# --8<-- [end:example]\n"
    )
    assert find_disallowed_comments(valid_source) == []

    invalid_file = tmp_path / "bad_example.py"
    invalid_file.write_text(valid_source + "y = 2  # inline comment\n", encoding="utf-8")
    errors = validate_file(invalid_file)
    assert len(errors) == 1
    assert "disallowed inline comment" in errors[0]
