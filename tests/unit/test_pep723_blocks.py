"""Tests for PEP 723 inline-script metadata blocks in runnable examples.

These tests pin the contract asserted by ``tools/ci/validate_pep723_blocks.py``:
every runnable reference example under ``docs/examples/`` must expose a
``# /// script`` block that declares ``requires-python`` and a
``dependencies`` list containing ``litestar-mcp`` (optionally with extras).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]

from tools.ci.validate_pep723_blocks import EXAMPLES, extract_block, validate_file


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: str(p.relative_to(Path.cwd())) if p.is_absolute() else str(p))
def test_pep723_block_present_and_parses(path: Path) -> None:
    """Every runnable example declares a parseable PEP 723 block."""
    assert path.is_file(), f"missing example entrypoint: {path}"
    body = extract_block(path.read_text(encoding="utf-8"))
    assert body is not None, f"{path}: no `# /// script` block"
    data = tomllib.loads(body)
    assert isinstance(data, dict)

    requires = data.get("requires-python")
    assert isinstance(requires, str) and requires.strip(), f"{path}: `requires-python` must be a non-empty string"
    assert any(marker in requires for marker in (">=3.10", ">=3.11", ">=3.12")), (
        f"{path}: `requires-python` must target >=3.10 or stricter (got {requires!r})"
    )

    deps = data.get("dependencies")
    assert isinstance(deps, list) and deps, f"{path}: `dependencies` must be non-empty list"
    assert all(isinstance(d, str) for d in deps), f"{path}: all deps must be strings"
    base_names = {d.split("[")[0].strip() for d in deps}
    assert "litestar-mcp" in base_names, (
        f"{path}: `dependencies` must include `litestar-mcp` (got {sorted(base_names)})"
    )


def test_validate_file_returns_no_errors_for_shipped_examples() -> None:
    """The CI validator agrees with the shipped example set."""
    errors: list[str] = []
    for path in EXAMPLES:
        errors.extend(validate_file(path))
    assert errors == [], "\n".join(errors)


def test_extract_block_handles_missing_block(tmp_path: Path) -> None:
    """A file without a ``# /// script`` block returns ``None``."""
    script = tmp_path / "no_block.py"
    script.write_text('"""no pep 723 here."""\n\nprint("hi")\n', encoding="utf-8")
    assert extract_block(script.read_text(encoding="utf-8")) is None


def test_extract_block_round_trip(tmp_path: Path) -> None:
    """A well-formed block parses back to the declared metadata."""
    script = tmp_path / "ok.py"
    script.write_text(
        '"""Demo."""\n'
        "\n"
        "# /// script\n"
        '# requires-python = ">=3.10"\n'
        "# dependencies = [\n"
        '#   "litestar-mcp",\n'
        "# ]\n"
        "# ///\n"
        "\n"
        'print("ok")\n',
        encoding="utf-8",
    )
    body = extract_block(script.read_text(encoding="utf-8"))
    assert body is not None
    parsed = tomllib.loads(body)
    assert parsed["requires-python"] == ">=3.10"
    assert parsed["dependencies"] == ["litestar-mcp"]
