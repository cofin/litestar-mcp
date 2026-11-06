"""Validator for ``# --8<-- [start:<section>]`` / ``# --8<-- [end:<section>]`` marker blocks.

Walks ``docs/examples/`` and, for every Python file under the root, verifies that
no disallowed inline ``#`` comments exist and compiles each extracted snippet
section in isolation as standalone Python.

Usage::

    uv run python tools/ci/validate_doc_markers.py

Exits ``0`` when every file has zero disallowed comments and every marker block
is syntactically valid standalone Python, ``1`` otherwise.
"""

import re
import sys
import textwrap
import tokenize
from io import StringIO
from pathlib import Path

START_MARKER = "# start-example"
END_MARKER = "# end-example"
SECTION_START_RE = re.compile(r"^\s*#\s*--8<--\s*\[start:([a-zA-Z0-9_-]+)\]\s*$")
SECTION_END_RE = re.compile(r"^\s*#\s*--8<--\s*\[end:([a-zA-Z0-9_-]+)\]\s*$")


def iter_marker_blocks(text: str) -> list[tuple[int, int, str]]:
    """Return ``(start_line, end_line, block_source)`` tuples for each marker pair.

    ``start_line`` and ``end_line`` are 1-based. ``block_source`` is the raw
    text between the marker lines (not including the marker lines themselves).
    """
    blocks: list[tuple[int, int, str]] = []
    lines = text.splitlines()
    has_section_markers = any(SECTION_START_RE.match(line) or SECTION_END_RE.match(line) for line in lines)

    if has_section_markers:
        i = 0
        while i < len(lines):
            start_match = SECTION_START_RE.match(lines[i])
            if start_match is not None:
                section_name = start_match.group(1)
                start = i
                j = i + 1
                while j < len(lines):
                    end_match = SECTION_END_RE.match(lines[j])
                    if end_match is not None and end_match.group(1) == section_name:
                        break
                    j += 1
                if j >= len(lines):
                    msg = f"unterminated # --8<-- [start:{section_name}] at line {start + 1}"
                    raise ValueError(msg)
                block = "\n".join(lines[start + 1 : j])
                blocks.append((start + 1, j + 1, block))
                i = j + 1
            elif SECTION_END_RE.match(lines[i]) is not None:
                msg = f"unmatched {lines[i].strip()} at line {i + 1}"
                raise ValueError(msg)
            else:
                i += 1
        return blocks

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


def find_disallowed_comments(text: str) -> list[tuple[int, str]]:
    """Return ``(line_no, comment_text)`` for any non-marker, non-PEP-723 comment."""
    disallowed: list[tuple[int, str]] = []
    in_pep723 = False

    for tok in tokenize.generate_tokens(StringIO(text).readline):
        if tok.type != tokenize.COMMENT:
            continue
        comment_str = tok.string.strip()
        line_stripped = tok.line.strip()

        if not in_pep723 and comment_str == "# /// script" and line_stripped == comment_str:
            in_pep723 = True
            continue

        if in_pep723 and tok.line.lstrip().startswith("#"):
            if comment_str == "# ///" and line_stripped == comment_str:
                in_pep723 = False
            continue

        if SECTION_START_RE.match(tok.line) is not None or SECTION_END_RE.match(tok.line) is not None:
            continue

        disallowed.append((tok.start[0], tok.string))

    return disallowed


def validate_file(path: Path) -> list[str]:
    """Return a list of error messages for ``path``. Empty list means OK."""
    errors: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"{path}: cannot read: {exc}"]

    try:
        disallowed = find_disallowed_comments(text)
    except tokenize.TokenError as exc:
        return [f"{path}: tokenize error: {exc}"]

    for line_no, comment_text in disallowed:
        errors.append(f"{path}:{line_no}: disallowed inline comment: {comment_text}")

    try:
        blocks = iter_marker_blocks(text)
    except ValueError as exc:
        errors.append(f"{path}: {exc}")
        return errors

    for start_line, _end_line, block in blocks:
        snippet = textwrap.dedent(block)
        try:
            compile(snippet, f"<{path}:{start_line}>", "exec")
        except SyntaxError as exc:
            errors.append(f"{path}:{start_line}: snippet does not compile: {exc}")
    return errors


def _has_markers(text: str) -> bool:
    return any(SECTION_START_RE.match(line) or SECTION_END_RE.match(line) for line in text.splitlines()) or (
        START_MARKER in text and END_MARKER in text
    )


def walk(root: Path) -> list[Path]:
    """Return every ``.py`` file under ``root`` when at least one marker file exists."""
    results: list[Path] = []
    has_marker_file = False
    for path in sorted(root.rglob("*.py")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        results.append(path)
        if _has_markers(text):
            has_marker_file = True
    return results if has_marker_file else []


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
