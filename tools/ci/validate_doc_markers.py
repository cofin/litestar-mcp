"""Validator for ``# start-example`` / ``# end-example`` marker blocks.

Walks ``docs/examples/`` and, for every Python file containing at least one
marker pair, extracts the block between the markers, dedents it (so a block
that sits inside a function body renders at column 0), and compiles the result
as standalone Python. A block that depends on a symbol defined *outside* the
marker will fail to compile in isolation — that is intentional. If a snippet
cannot stand on its own, the marker is wrong.

Usage::

    uv run python tools/ci/validate_doc_markers.py

Exits ``0`` when every marker block is syntactically valid standalone Python,
``1`` otherwise. Prints a per-file summary in either case.
"""

import sys
import textwrap
from pathlib import Path

START_MARKER = "# start-example"
END_MARKER = "# end-example"


def iter_marker_blocks(text: str) -> list[tuple[int, int, str]]:
    """Return ``(start_line, end_line, block_source)`` tuples for each marker pair.

    ``start_line`` and ``end_line`` are 1-based. ``block_source`` is the raw
    text between the marker lines (not including the marker lines themselves).
    """
    blocks: list[tuple[int, int, str]] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if START_MARKER in lines[i]:
            start = i
            j = i + 1
            while j < len(lines) and END_MARKER not in lines[j]:
                j += 1
            if j >= len(lines):
                msg = f"unterminated {START_MARKER} at line {start + 1}"
                raise ValueError(msg)
            block = "\n".join(lines[start + 1 : j])
            blocks.append((start + 1, j + 1, block))
            i = j + 1
        else:
            i += 1
    return blocks


def validate_file(path: Path) -> list[str]:
    """Return a list of error messages for ``path``. Empty list means OK."""
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: cannot read: {exc}"]

    try:
        blocks = iter_marker_blocks(text)
    except ValueError as exc:
        return [f"{path}: {exc}"]

    for start_line, _end_line, block in blocks:
        snippet = textwrap.dedent(block)
        try:
            compile(snippet, f"<{path}:{start_line}>", "exec")
        except SyntaxError as exc:
            errors.append(f"{path}:{start_line}: snippet does not compile: {exc}")
    return errors


def walk(root: Path) -> list[Path]:
    """Return every ``.py`` file under ``root`` that contains a marker pair."""
    results: list[Path] = []
    for path in sorted(root.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if START_MARKER in text and END_MARKER in text:
            results.append(path)
    return results


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint. Returns process exit code."""
    argv = argv if argv is not None else sys.argv[1:]
    root = Path(argv[0]) if argv else Path("docs/examples")
    if not root.exists():
        sys.stdout.write(f"validate_doc_markers: root not found: {root}\n")
        return 1

    files = walk(root)
    if not files:
        sys.stdout.write(f"validate_doc_markers: no marker blocks found under {root}\n")
        return 1

    all_errors: list[str] = []
    for path in files:
        errors = validate_file(path)
        status = "OK" if not errors else "FAIL"
        sys.stdout.write(f"  [{status}] {path}\n")
        all_errors.extend(errors)

    if all_errors:
        sys.stdout.write("\nErrors:\n")
        for err in all_errors:
            sys.stdout.write(f"  {err}\n")
        return 1

    sys.stdout.write(f"\nvalidate_doc_markers: {len(files)} file(s) passed.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
