"""Guard that ``# start-example`` / ``# end-example`` blocks stand alone.

Invokes ``tools/ci/validate_doc_markers.py`` against ``docs/examples`` and
asserts exit code ``0``. This catches copy-paste typos and snippets that
silently depend on symbols defined outside the marker block.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(REPO_ROOT))


def test_doc_example_markers_are_self_contained() -> None:
    from tools.ci.validate_doc_markers import main

    exit_code = main([str(REPO_ROOT / "docs" / "examples")])
    assert exit_code == 0
