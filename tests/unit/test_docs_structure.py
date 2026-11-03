"""Structural and integrity tests for the Markdown documentation tree and zensical.toml navigation."""

import datetime as dt
import re
import sys
from pathlib import Path

import pytest
from tools.prepare_release import PRInfo, ReleaseInfo, RepoUser, _write_changelog_entry, build_changelog_entry

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"
NOTES_DIR = DOCS_DIR / "examples" / "notes"
ARCHITECTURE_DIR = DOCS_DIR / "architecture"

_FENCED_CODE_RE = re.compile(r"```.*?```", re.DOTALL)
_SNIPPET_INCLUDE_RE = re.compile(r'^\s*--8<--\s+"([^"]+)"', re.MULTILINE)
_MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)]+)\)")
_SPHINX_DIRECTIVE_OR_ROLE_RE = re.compile(
    r"(\.\.\s+(?:code-block|literalinclude|toctree|automodule|autoclass|autofunction)::|:(?:class|func|meth|mod|attr|exc|ref|doc|pep):`)"
)


def _collect_nav_pages(node: "object") -> "list[str]":
    """Recursively extract all Markdown page paths from a ``zensical.toml`` ``nav`` structure."""
    pages: list[str] = []
    if isinstance(node, str):
        pages.append(node)
    elif isinstance(node, list):
        for item in node:
            pages.extend(_collect_nav_pages(item))
    elif isinstance(node, dict):
        for value in node.values():
            pages.extend(_collect_nav_pages(value))
    return pages


def _load_nav_pages() -> "tuple[str, ...]":
    """Load the ordered tuple of Markdown page paths registered in ``zensical.toml``."""
    zensical_data = tomllib.loads((REPO_ROOT / "zensical.toml").read_text(encoding="utf-8"))
    return tuple(_collect_nav_pages(zensical_data["project"]["nav"]))


NAV_PAGES = _load_nav_pages()

REFERENCE_CHOOSER_FILES: "tuple[Path, ...]" = (
    NOTES_DIR / "README.md",
    NOTES_DIR / "advanced_alchemy" / "README.md",
    NOTES_DIR / "sqlspec" / "README.md",
    ARCHITECTURE_DIR / "reference-examples.md",
    ARCHITECTURE_DIR / "uvx-and-pep723.md",
)

NOTES_README_FILES: "tuple[Path, ...]" = (
    NOTES_DIR / "README.md",
    NOTES_DIR / "advanced_alchemy" / "README.md",
    NOTES_DIR / "sqlspec" / "README.md",
)


def test_zero_rst_files_remain_under_docs() -> "None":
    """Verify zero legacy ``.rst`` files remain anywhere under ``docs/``."""
    rst_files = sorted(str(path.relative_to(REPO_ROOT)) for path in DOCS_DIR.rglob("*.rst"))
    assert rst_files == []


@pytest.mark.parametrize("nav_page", NAV_PAGES)
def test_every_zensical_nav_page_exists_on_disk(nav_page: "str") -> "None":
    """Verify every Markdown page listed in ``zensical.toml`` ``nav`` exists under ``docs/``."""
    page_path = DOCS_DIR / nav_page
    assert page_path.is_file(), f"Missing nav page on disk: docs/{nav_page}"


def test_every_docs_markdown_page_is_registered_in_zensical_nav() -> "None":
    """Verify every ``.md`` file under ``docs/`` (excluding ``docs/examples/**``) is in ``zensical.toml`` ``nav``."""
    nav_set = set(NAV_PAGES)
    disk_pages = {
        path.relative_to(DOCS_DIR).as_posix()
        for path in DOCS_DIR.rglob("*.md")
        if "examples" not in path.relative_to(DOCS_DIR).parts
    }
    unregistered = sorted(disk_pages - nav_set)
    assert unregistered == [], f"Unregistered Markdown pages under docs/: {unregistered}"


@pytest.mark.parametrize("nav_page", NAV_PAGES)
def test_snippet_and_diagram_includes_resolve_to_existing_files(nav_page: "str") -> "None":
    """Verify every ``--8<-- "..."`` snippet or diagram include across ``docs/**/*.md`` exists on disk."""
    page_path = DOCS_DIR / nav_page
    assert page_path.is_file(), f"Missing page: {page_path}"
    content = page_path.read_text(encoding="utf-8")
    for match in _SNIPPET_INCLUDE_RE.finditer(content):
        raw_target = match.group(1).strip()
        file_part = raw_target.split(":", 1)[0].strip()
        target_path = REPO_ROOT / file_part
        assert target_path.is_file(), f"{nav_page} includes missing file {file_part!r} (from {raw_target!r})"


ALL_CHECKED_MD_PAGES: "tuple[str, ...]" = (
    *NAV_PAGES,
    *(p.relative_to(DOCS_DIR).as_posix() for p in NOTES_README_FILES),
)


@pytest.mark.parametrize("nav_page", ALL_CHECKED_MD_PAGES)
def test_relative_markdown_links_resolve_to_existing_pages(nav_page: "str") -> "None":
    """Verify every relative Markdown link across ``docs/**/*.md`` resolves to a valid page or directory index."""
    page_path = DOCS_DIR / nav_page
    assert page_path.is_file(), f"Missing page: {page_path}"
    stripped = _FENCED_CODE_RE.sub("", page_path.read_text(encoding="utf-8"))
    base_dir = page_path.parent

    for match in _MARKDOWN_LINK_RE.finditer(stripped):
        raw_target = match.group(1).strip()
        if raw_target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path_part = raw_target.split("#", 1)[0].strip().rstrip("/")
        if not path_part:
            continue
        candidates = (
            base_dir / path_part,
            base_dir / f"{path_part}.md",
            base_dir / path_part / "index.md",
            base_dir / path_part / "README.md",
        )
        assert any(candidate.is_file() for candidate in candidates), (
            f"Unresolved relative Markdown link in docs/{nav_page}: {raw_target!r}"
        )


@pytest.mark.parametrize("nav_page", ALL_CHECKED_MD_PAGES)
def test_zero_raw_sphinx_directives_or_roles_in_markdown_pages(nav_page: "str") -> "None":
    """Verify zero raw ``.. code-block::`` directives or ``:class:`` Sphinx roles appear in ``docs/**/*.md``."""
    page_path = DOCS_DIR / nav_page
    assert page_path.is_file(), f"Missing page: {page_path}"
    stripped = _FENCED_CODE_RE.sub("", page_path.read_text(encoding="utf-8"))
    match = _SPHINX_DIRECTIVE_OR_ROLE_RE.search(stripped)
    assert match is None, f"Raw Sphinx directive/role {match.group(0)!r} found in docs/{nav_page}"


@pytest.mark.parametrize(
    "path",
    REFERENCE_CHOOSER_FILES,
    ids=lambda p: p.relative_to(DOCS_DIR).as_posix(),
)
def test_reference_chooser_doc_files_present(path: "Path") -> "None":
    """Verify the five reference-example and ``uvx`` documentation entrypoints exist on disk."""
    assert path.is_file(), f"Expected reference docs file {path} to exist"


@pytest.mark.parametrize(
    "path",
    NOTES_README_FILES,
    ids=lambda p: p.relative_to(DOCS_DIR).as_posix(),
)
def test_notes_readmes_contain_variants_and_uvx(path: "Path") -> "None":
    """Verify each Notes README documents ``uvx`` and the variant matrix."""
    text = path.read_text(encoding="utf-8")
    assert "uvx" in text, f"{path} must mention 'uvx'"
    assert "Variant" in text, f"{path} must contain a 'Variants' heading"


def test_architecture_reference_examples_links_both_families_and_auth_modes() -> "None":
    """Verify ``docs/architecture/reference-examples.md`` links both persistence families and all 4 auth tiers."""
    text = (ARCHITECTURE_DIR / "reference-examples.md").read_text(encoding="utf-8")
    assert "advanced_alchemy" in text
    assert "sqlspec" in text
    for auth_mode in ("no-auth", "JWT", "Cloud Run", "IAP"):
        assert auth_mode in text, f"reference-examples.md missing comparison label: {auth_mode!r}"


def test_architecture_uvx_guide_contains_template_and_pitfalls() -> "None":
    """Verify ``docs/architecture/uvx-and-pep723.md`` documents ``uv run``, ``uvx``, ``mcp bridge``, and pitfalls."""
    text = (ARCHITECTURE_DIR / "uvx-and-pep723.md").read_text(encoding="utf-8")
    assert "uvx" in text
    assert "uv run docs/examples" in text
    assert "litestar --app my_app:app mcp bridge" in text
    assert "Common pitfalls" in text or "Common Pitfalls" in text


def test_architecture_index_links_reference_examples_and_uvx_guide() -> "None":
    """Verify ``docs/architecture/index.md`` links to ``reference-examples.md`` and ``uvx-and-pep723.md``."""
    text = (ARCHITECTURE_DIR / "index.md").read_text(encoding="utf-8")
    assert "reference-examples" in text
    assert "uvx-and-pep723" in text


def test_prepare_release_builds_and_inserts_markdown_changelog_entry(tmp_path: "Path") -> "None":
    """Verify ``prepare_release.py`` renders Keep-a-Changelog Markdown and inserts it into ``changelog.md``."""
    pr = PRInfo(
        url="https://github.com/cofin/litestar-mcp/pull/105",
        title="feat: add markdown changelog writer",
        clean_title="add markdown changelog writer",
        cc_type="feat",
        number=105,
        closes=[99],
        created_at=dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc),
        description="Added Keep-a-Changelog Markdown output.",
        user=RepoUser(login="cofin", id=1, type="User"),
    )
    release_info = ReleaseInfo(
        base="v0.18.0",
        release_tag="v0.19.0",
        version="0.19.0",
        pull_requests={"feat": [pr]},
        first_time_prs=[],
    )
    entry = build_changelog_entry(release_info)
    assert "## [0.19.0]" in entry
    assert "### Added" in entry
    assert (
        "- **add markdown changelog writer** ([#105](https://github.com/cofin/litestar-mcp/pull/105), [#99](https://github.com/cofin/litestar-mcp/issues/99))"
        in entry
    )

    sample_changelog = tmp_path / "changelog.md"
    sample_changelog.write_text("# Changelog\n\n## [0.18.0] - 2026-11-01\n\n- Initial entry\n", encoding="utf-8")
    _write_changelog_entry(entry, changelog_path=sample_changelog)
    updated = sample_changelog.read_text(encoding="utf-8")
    assert updated.index("## [0.19.0]") < updated.index("## [0.18.0]")
