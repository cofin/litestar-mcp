"""Validate PEP 723 inline script metadata blocks in runnable examples.

Every runnable reference example under ``docs/examples/`` must declare its
dependencies inline via a :pep:`723` ``# /// script`` block so readers can
launch any variant with ``uv run <path>`` — no clone, no ``uv sync``, no
extras juggling.

The validator walks the fixed list of entrypoint files, parses each script
metadata block as TOML, and asserts:

1. The block exists and is well-formed.
2. ``requires-python`` is present and declares ``>=3.10`` (or stricter).
3. ``dependencies`` is a non-empty list that includes ``litestar-mcp``.

The script exits non-zero on any failure so it can be wired into CI and the
``validate-pep723`` Makefile target.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

try:
    import tomllib  # type: ignore[import-not-found]
except ModuleNotFoundError:  # pragma: no cover - Python 3.10 fallback
    import tomli as tomllib  # type: ignore[import-not-found, no-redef]

ROOT = Path(__file__).resolve().parents[2]

EXAMPLES: tuple[Path, ...] = (
    ROOT / "docs/examples/hello_world/main.py",
    ROOT / "docs/examples/task_manager/main.py",
    ROOT / "docs/examples/notes/advanced_alchemy/no_auth.py",
    ROOT / "docs/examples/notes/advanced_alchemy/no_auth_dishka.py",
    ROOT / "docs/examples/notes/advanced_alchemy/jwt_auth.py",
    ROOT / "docs/examples/notes/advanced_alchemy/jwt_auth_dishka.py",
    ROOT / "docs/examples/notes/sqlspec/no_auth.py",
    ROOT / "docs/examples/notes/sqlspec/no_auth_dishka.py",
    ROOT / "docs/examples/notes/sqlspec/jwt_auth.py",
    ROOT / "docs/examples/notes/sqlspec/jwt_auth_dishka.py",
    ROOT / "docs/examples/notes/sqlspec/google_iap.py",
    ROOT / "docs/examples/notes/sqlspec/cloud_run_jwt.py",
)

_BLOCK_RE = re.compile(
    r"(?ms)^# /// script\s*\n(?P<body>(?:^#(?: .*|)\n)+?)^# ///\s*$",
)


def extract_block(source: str) -> str | None:
    """Return the TOML body of the first ``# /// script`` block, if any."""
    match = _BLOCK_RE.search(source)
    if match is None:
        return None
    lines: list[str] = []
    for raw in match.group("body").splitlines():
        if raw == "#":
            lines.append("")
        elif raw.startswith("# "):
            lines.append(raw[2:])
        else:
            # Malformed line inside the block
            return None
    return "\n".join(lines) + "\n"


def validate_file(path: Path) -> list[str]:
    """Return a list of validation errors for ``path`` (empty == ok)."""
    errors: list[str] = []
    if not path.is_file():
        return [f"{path}: file not found"]
    source = path.read_text(encoding="utf-8")
    body = extract_block(source)
    if body is None:
        return [f"{path}: no `# /// script` PEP 723 block found"]
    try:
        data = tomllib.loads(body)
    except Exception as exc:  # noqa: BLE001 - surface parse error verbatim
        return [f"{path}: PEP 723 block is not valid TOML: {exc}"]

    requires = data.get("requires-python")
    if not isinstance(requires, str) or not requires.strip():
        errors.append(f"{path}: missing or empty `requires-python`")
    elif ">=3.10" not in requires and ">=3.11" not in requires and ">=3.12" not in requires:
        errors.append(f"{path}: `requires-python` must declare `>=3.10` or stricter (got {requires!r})")

    deps = data.get("dependencies")
    if not isinstance(deps, list) or not deps:
        errors.append(f"{path}: `dependencies` must be a non-empty list")
        return errors
    if not all(isinstance(d, str) for d in deps):
        errors.append(f"{path}: every entry in `dependencies` must be a string")
    if not any(d.split("[")[0].strip() == "litestar-mcp" for d in deps if isinstance(d, str)):
        errors.append(f"{path}: `dependencies` must include `litestar-mcp`")
    return errors


def main() -> int:
    all_errors: list[str] = []
    for path in EXAMPLES:
        all_errors.extend(validate_file(path))
    if all_errors:
        for err in all_errors:
            sys.stderr.write(err + "\n")
        sys.stderr.write(f"\n{len(all_errors)} PEP 723 validation error(s).\n")
        return 1
    sys.stdout.write(f"OK: {len(EXAMPLES)} PEP 723 block(s) validated\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
