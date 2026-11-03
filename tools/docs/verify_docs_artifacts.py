"""Verification gate for documentation build artifacts, inventories, diagrams, and legacy redirects."""

import argparse
import re
import subprocess
import sys
import zlib
from collections.abc import Sequence
from pathlib import Path

import msgspec

from litestar_mcp.mcp.cli import mcp_group
from tools.docs.generate_cli_docs import DEFAULT_OUTPUT as DEFAULT_CLI_OUTPUT
from tools.docs.generate_cli_docs import main as cli_docs_main
from tools.docs.postbuild import DEFAULT_DOCS_DIR, DEFAULT_SITE_DIR, LEGACY_REDIRECTS, run_postbuild
from tools.docs.scan_public_docstrings import scan_package

__all__ = [
    "REQUIRED_INVENTORY_SYMBOLS",
    "ArtifactFinding",
    "main",
    "verify_docs_build",
    "verify_site",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_REFERENCE_HTML_BYTES = 240 * 1024

WARNING_PATTERN = re.compile(r"\b(WARNING|ERROR)\b")
CODE_OR_PRE_RE = re.compile(
    r"<(?:pre|code|textarea|script|style)\b[^>]*>.*?</(?:pre|code|textarea|script|style)>", re.DOTALL | re.IGNORECASE
)
RAW_DIRECTIVE_RE = re.compile(r"(?:^|>|\s):::\s+litestar_mcp\b")
RAW_SNIPPET_RE = re.compile(r"--8\s*<\s*--")
RAW_SPHINX_ROLE_RE = re.compile(r":(?:class|func|meth|mod|attr|exc|obj|data|const|ref|doc|pep):`[^`]+`")

REQUIRED_INVENTORY_SYMBOLS: tuple[str, ...] = (
    "litestar_mcp.LitestarMCP",
    "litestar_mcp.MCPConfig",
    "litestar_mcp.queues.QueueTaskExecutor",
    "litestar_mcp.queues.DurableWorkflowEngine",
    "litestar_mcp.agent.AgentRuntime",
    "litestar_mcp.a2a.LitestarA2A",
)


class ArtifactFinding(msgspec.Struct, frozen=True, gc=False):
    """Single documentation build artifact verification violation."""

    path: str
    """Relative path of the artifact with a violation."""

    rule: str
    """Identifier of the violated verification rule."""

    detail: str
    """Human-readable explanation of the failure."""


def verify_site(
    site_dir: Path = DEFAULT_SITE_DIR,
    docs_dir: Path = DEFAULT_DOCS_DIR,
) -> list[ArtifactFinding]:
    """Inspect a built `site/` directory and return structured artifact findings.

    Args:
        site_dir: Built documentation site directory.
        docs_dir: Documentation source directory.

    Returns:
        List of `ArtifactFinding` objects (`[]` when clean).
    """
    findings: list[ArtifactFinding] = []
    findings.extend(_check_required_artifacts(site_dir))
    findings.extend(_check_objects_inv(site_dir))
    findings.extend(_check_llms_files(site_dir))
    findings.extend(_check_html_files(site_dir))
    findings.extend(_check_legacy_redirects(site_dir))
    findings.extend(_check_cli_autodoc(docs_dir))
    return findings


def verify_docs_build(
    *,
    site_dir: Path = DEFAULT_SITE_DIR,
    docs_dir: Path = DEFAULT_DOCS_DIR,
    run_build: bool = True,
    strict_warnings: bool = True,
) -> list[str]:
    """Run the full documentation verification pipeline and return error strings.

    Args:
        site_dir: Built documentation site directory.
        docs_dir: Documentation source directory.
        run_build: Whether to execute `zensical build --clean` and `run_postbuild` first.
        strict_warnings: Whether to treat build warnings as failures.

    Returns:
        List of formatted error messages (`[]` on success).
    """
    errors: list[str] = []

    if run_build:
        docstring_findings = scan_package()
        if docstring_findings:
            errors.append(f"scan_public_docstrings reported {len(docstring_findings)} finding(s).")
        errors.extend(_run_zensical_build(strict_warnings=strict_warnings))
        if errors:
            return errors
        run_postbuild(site_dir=site_dir, docs_dir=docs_dir)

    findings = verify_site(site_dir=site_dir, docs_dir=docs_dir)
    errors.extend(f"[{f.rule}] {f.path}: {f.detail}" for f in findings)
    return errors


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for `verify_docs_artifacts.py`.

    Args:
        argv: Optional CLI argument sequence.

    Returns:
        Exit code (`0` on success, `1` on verification failure).
    """
    parser = argparse.ArgumentParser(description="Verify documentation build and artifacts.")
    parser.add_argument(
        "--strict-warnings",
        action="store_true",
        default=False,
        help="Fail if the documentation build emits any WARNING or ERROR log lines.",
    )
    parser.add_argument(
        "--no-build",
        action="store_true",
        default=True,
        help="Verify existing site/ artifacts without running zensical build.",
    )
    parser.add_argument(
        "--build",
        action="store_true",
        default=False,
        help="Run zensical build --clean and postbuild before verifying site/ artifacts.",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    errors = verify_docs_build(run_build=bool(args.build), strict_warnings=args.strict_warnings)
    if errors:
        for err in errors:
            sys.stderr.write(f"ERROR: {err}\n")
        return 1

    sys.stdout.write("Documentation verification passed with 0 errors.\n")
    return 0


def _run_zensical_build(strict_warnings: bool) -> list[str]:
    """Run `zensical build --clean` and inspect output for warnings or errors."""
    errors: list[str] = []
    proc = subprocess.run(
        [sys.executable, "-m", "zensical", "build", "--clean"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    combined_output = f"{proc.stdout}\n{proc.stderr}"
    if proc.returncode != 0:
        errors.append(f"zensical build failed with exit code {proc.returncode}:\n{combined_output}")
        return errors

    if strict_warnings:
        errors.extend(
            f"Documentation build warning/error detected: {line.strip()}"
            for line in combined_output.splitlines()
            if WARNING_PATTERN.search(line)
        )
    return errors


def _check_required_artifacts(site_dir: Path) -> list[ArtifactFinding]:
    """Verify required top-level files exist in `site/`."""
    findings: list[ArtifactFinding] = []
    for artifact in ("index.html", "objects.inv", "llms.txt", "llms-full.txt", ".nojekyll"):
        path = site_dir / artifact
        if not path.exists():
            findings.append(
                ArtifactFinding(
                    path=artifact,
                    rule="missing-artifact",
                    detail=f"Missing required site artifact: {artifact}",
                )
            )
        elif artifact != ".nojekyll" and path.stat().st_size == 0:
            findings.append(
                ArtifactFinding(
                    path=artifact,
                    rule="empty-artifact",
                    detail=f"Required site artifact is empty: {artifact}",
                )
            )
    return findings


def _check_objects_inv(site_dir: Path) -> list[ArtifactFinding]:
    """Verify `site/objects.inv` has a valid Sphinx v2 header, zlib payload, and core symbols."""
    findings: list[ArtifactFinding] = []
    inv_path = site_dir / "objects.inv"
    if not inv_path.exists():
        return findings

    inv_bytes = inv_path.read_bytes()
    if not inv_bytes.startswith(b"# Sphinx inventory version 2\n"):
        findings.append(
            ArtifactFinding(
                path="objects.inv",
                rule="invalid-inventory-header",
                detail="site/objects.inv does not have a valid Sphinx v2 header.",
            )
        )
        return findings

    lines = inv_bytes.splitlines(keepends=True)
    try:
        decompressed = zlib.decompress(b"".join(lines[4:])).decode("utf-8")
    except Exception as exc:  # noqa: BLE001
        findings.append(
            ArtifactFinding(
                path="objects.inv",
                rule="corrupt-inventory-payload",
                detail=f"Failed to decompress site/objects.inv payload: {exc}",
            )
        )
        return findings

    findings.extend(
        ArtifactFinding(
            path="objects.inv",
            rule="missing-inventory-symbol",
            detail=f"site/objects.inv is missing required symbol {symbol}.",
        )
        for symbol in REQUIRED_INVENTORY_SYMBOLS
        if symbol not in decompressed
    )
    return findings


def _check_llms_files(site_dir: Path) -> list[ArtifactFinding]:
    """Verify `site/llms-full.txt` contains zero unexpanded snippet directives."""
    findings: list[ArtifactFinding] = []
    llms_full = site_dir / "llms-full.txt"
    if llms_full.exists():
        text = llms_full.read_text(encoding="utf-8")
        if RAW_SNIPPET_RE.search(text):
            findings.append(
                ArtifactFinding(
                    path="llms-full.txt",
                    rule="unrendered-snippet",
                    detail="site/llms-full.txt contains unexpanded --8<-- snippet directives.",
                )
            )
    return findings


def _check_html_files(site_dir: Path) -> list[ArtifactFinding]:
    """Scan generated HTML files for unrendered directives, snippets, Sphinx roles, and size budgets."""
    findings: list[ArtifactFinding] = []
    if not site_dir.exists():
        return findings

    for html_file in sorted(site_dir.rglob("*.html")):
        rel_path = html_file.relative_to(site_dir).as_posix()
        raw_html = html_file.read_text(encoding="utf-8")
        stripped_html = CODE_OR_PRE_RE.sub("", raw_html)

        if RAW_DIRECTIVE_RE.search(stripped_html):
            findings.append(
                ArtifactFinding(
                    path=rel_path,
                    rule="unrendered-mkdocstrings-directive",
                    detail="HTML contains unrendered `::: litestar_mcp` directive.",
                )
            )
        if RAW_SNIPPET_RE.search(stripped_html) or RAW_SNIPPET_RE.search(raw_html):
            findings.append(
                ArtifactFinding(
                    path=rel_path,
                    rule="unrendered-snippet",
                    detail="HTML contains unrendered `--8<--` snippet include directive.",
                )
            )
        if RAW_SPHINX_ROLE_RE.search(stripped_html):
            findings.append(
                ArtifactFinding(
                    path=rel_path,
                    rule="raw-sphinx-role",
                    detail="HTML contains raw unrendered Sphinx role (`:class:`, `:func:`, `:meth:`, etc.).",
                )
            )
        if rel_path.startswith("reference/") and html_file.stat().st_size > MAX_REFERENCE_HTML_BYTES:
            findings.append(
                ArtifactFinding(
                    path=rel_path,
                    rule="reference-size-budget",
                    detail=f"Reference HTML size {html_file.stat().st_size} exceeds {MAX_REFERENCE_HTML_BYTES} bytes.",
                )
            )

    arch_html = site_dir / "architecture" / "index.html"
    if arch_html.exists() and "lm-diagram" not in arch_html.read_text(encoding="utf-8"):
        findings.append(
            ArtifactFinding(
                path="architecture/index.html",
                rule="missing-diagram-landmark",
                detail="site/architecture/index.html does not contain interactive `.lm-diagram` landmarks.",
            )
        )
    return findings


def _check_legacy_redirects(site_dir: Path) -> list[ArtifactFinding]:
    """Verify all 29 legacy redirect stubs exist and compute valid relative target URLs."""
    findings: list[ArtifactFinding] = []
    if not site_dir.exists():
        return findings

    full_site_built = (site_dir / "learn" / "index.html").exists()
    for old_slug, new_target in sorted(LEGACY_REDIRECTS.items()):
        dir_stub = site_dir / old_slug / "index.html"
        flat_stub = site_dir / f"{old_slug}.html"
        expected_rel = ("../" * len(Path(old_slug).parts)) + new_target
        if not dir_stub.exists():
            findings.append(
                ArtifactFinding(
                    path=f"{old_slug}/index.html",
                    rule="missing-redirect-stub",
                    detail=f"Missing legacy directory redirect stub: {old_slug}/index.html",
                )
            )
        else:
            stub_html = dir_stub.read_text(encoding="utf-8")
            if expected_rel not in stub_html:
                findings.append(
                    ArtifactFinding(
                        path=f"{old_slug}/index.html",
                        rule="invalid-redirect-target",
                        detail=f"Expected relative redirect target {expected_rel!r} in {old_slug}/index.html.",
                    )
                )
            if full_site_built:
                resolved = (dir_stub.parent / expected_rel).resolve()
                if not resolved.exists():
                    findings.append(
                        ArtifactFinding(
                            path=f"{old_slug}/index.html",
                            rule="broken-redirect-destination",
                            detail=f"Redirect target {expected_rel!r} does not resolve inside {site_dir}.",
                        )
                    )
        if not flat_stub.exists():
            findings.append(
                ArtifactFinding(
                    path=f"{old_slug}.html",
                    rule="missing-redirect-stub",
                    detail=f"Missing legacy flat redirect stub: {old_slug}.html",
                )
            )
    return findings


def _check_cli_autodoc(docs_dir: Path) -> list[ArtifactFinding]:
    """Verify `tools/docs/generated/cli-reference.md` is up to date when checking repository docs."""
    findings: list[ArtifactFinding] = []
    if docs_dir == DEFAULT_DOCS_DIR and DEFAULT_CLI_OUTPUT.exists():
        if cli_docs_main(["--check"]) != 0:
            findings.append(
                ArtifactFinding(
                    path="tools/docs/generated/cli-reference.md",
                    rule="stale-cli-reference",
                    detail="tools/docs/generated/cli-reference.md is out of sync with generate_cli_docs.py --check.",
                )
            )
        cli_text = DEFAULT_CLI_OUTPUT.read_text(encoding="utf-8")
        findings.extend(
            ArtifactFinding(
                path="tools/docs/generated/cli-reference.md",
                rule="missing-cli-subcommand",
                detail=f"CLI command 'litestar mcp {cmd_name}' is missing from cli-reference.md.",
            )
            for cmd_name in sorted(mcp_group.commands)
            if f"`litestar mcp {cmd_name}`" not in cli_text
        )
    return findings


if __name__ == "__main__":
    raise SystemExit(main())
