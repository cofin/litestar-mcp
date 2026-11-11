"""Structural and integrity tests for the Markdown documentation tree and zensical.toml navigation."""

import ast
import datetime as dt
import io
import re
import sys
import textwrap
import tokenize
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
_NUMBERED_HEADING_RE = re.compile(r"^#{2,3}\s+\d+\.", re.MULTILINE)
_BACKTICK_HEADING_RE = re.compile(r"^#{2,3}\s+[^\n]*`", re.MULTILINE)
_UNSLICED_SNIPPET_RE = re.compile(r'^\s*--8<--\s+"docs/examples/snippets/[^":]+\.py"', re.MULTILINE)
_INLINE_CODE_SPAN_RE = re.compile(r"`([^`]+)`")
_FENCED_PYTHON_BLOCK_RE = re.compile(
    r"^([ \t]*)```python[^\n]*\n(.*?)^\1```[ \t]*$",
    re.MULTILINE | re.DOTALL,
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


RETIRED_MIGRATION_NAV_PAGES: "frozenset[str]" = frozenset(
    {
        "security/migration-0-14.md",
        "security/migration-0-12.md",
    }
)


def _load_nav_pages() -> "tuple[str, ...]":
    """Load the ordered tuple of Markdown page paths registered in ``zensical.toml``."""
    zensical_data = tomllib.loads((REPO_ROOT / "zensical.toml").read_text(encoding="utf-8"))
    return tuple(
        page for page in _collect_nav_pages(zensical_data["project"]["nav"]) if page not in RETIRED_MIGRATION_NAV_PAGES
    )


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

CH04_GUIDE_PAGES: "tuple[str, ...]" = tuple(
    page
    for page in NAV_PAGES
    if page == "index.md"
    or page.startswith(("architecture/", "getting-started/", "learn/", "queues/", "agents/", "security/"))
)


def _collect_guide_fenced_python_blocks() -> "list[tuple[str, int, str]]":
    """Collect non-include fenced Python code blocks across ``CH04_GUIDE_PAGES``."""
    collected: list[tuple[str, int, str]] = []
    for nav_page in CH04_GUIDE_PAGES:
        raw_text = (DOCS_DIR / nav_page).read_text(encoding="utf-8")
        for idx, match in enumerate(_FENCED_PYTHON_BLOCK_RE.finditer(raw_text), start=1):
            lines = [ln for ln in match.group(2).splitlines() if not ln.lstrip().startswith("--8<--")]
            code = textwrap.dedent("\n".join(lines)).strip()
            if code:
                collected.append((nav_page, idx, code))
    return collected


GUIDE_FENCED_PYTHON_BLOCKS: "tuple[tuple[str, int, str], ...]" = tuple(_collect_guide_fenced_python_blocks())


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


@pytest.mark.parametrize("nav_page", CH04_GUIDE_PAGES)
def test_guide_pages_slice_snippet_includes_and_have_clean_headings_and_tables(nav_page: "str") -> "None":
    """Verify guide pages slice snippet includes with :example and have clean headings and table cells."""
    raw_text = (DOCS_DIR / nav_page).read_text(encoding="utf-8")
    assert _UNSLICED_SNIPPET_RE.search(raw_text) is None, f"Unsliced snippet include in docs/{nav_page}"

    stripped = _FENCED_CODE_RE.sub("", raw_text)
    assert _NUMBERED_HEADING_RE.search(stripped) is None, f"Numbered H2/H3 heading in docs/{nav_page}"
    assert _BACKTICK_HEADING_RE.search(stripped) is None, f"Backtick in H2/H3 heading in docs/{nav_page}"

    for line in stripped.splitlines():
        if line.lstrip().startswith("|"):
            assert r"\|" not in line, f"Escaped pipe in table row in docs/{nav_page}: {line!r}"
            for code_match in _INLINE_CODE_SPAN_RE.finditer(line):
                assert "|" not in code_match.group(1), (
                    f"Raw pipe inside inline code in table row in docs/{nav_page}: {code_match.group(0)!r}"
                )


def test_queues_and_agents_guides_use_current_v018_contracts() -> "None":
    """Verify docs/queues/ and docs/agents/ use litestar-queues>=0.13.0 and MCP 2026-07-28 contracts."""
    deployment_text = (DOCS_DIR / "queues" / "deployment.md").read_text(encoding="utf-8")
    assert "RedisBackendConfig" in deployment_text
    assert "max_concurrency=8" in deployment_text
    assert "litestar --app app:app queues run" in deployment_text
    assert "broker_url" not in deployment_text
    assert "litestar workers run" not in deployment_text
    assert "litestar --app app:app workers run" not in deployment_text

    mcp_tasks_text = (DOCS_DIR / "queues" / "mcp-tasks.md").read_text(encoding="utf-8")
    assert "InProcessTaskExecutor" not in mcp_tasks_text
    assert "| `tasks/result` |" not in mcp_tasks_text
    assert "| `tasks/list`" not in mcp_tasks_text

    turns_text = (DOCS_DIR / "queues" / "queued-agent-turns.md").read_text(encoding="utf-8")
    assert "Store-Backed Frame Replay" not in turns_text
    assert "Last-Event-ID" in turns_text


def test_security_docs_retire_legacy_migrations_and_use_litestar_security_v07() -> "None":
    """Verify legacy migration pages are removed and litestar-security guide uses v0.7+ configs."""
    security_dir = REPO_ROOT / "docs" / "security"
    assert not (security_dir / "migration-0-14.md").exists()
    assert not (security_dir / "migration-0-12.md").exists()

    index_text = (security_dir / "index.md").read_text(encoding="utf-8")
    assert "migration-0-14" not in index_text
    assert "migration-0-12" not in index_text
    assert "<h3>First-Party litestar-security Integration</h3>" in index_text

    ls_text = (security_dir / "litestar-security.md").read_text(encoding="utf-8")
    assert "TokenMechanism" not in ls_text
    assert "OAuthValidationConfig" not in ls_text
    assert "oauth_jwt" not in ls_text
    assert "APIKeyConfig" in ls_text
    assert "ProtectedResourceConfig" in ls_text
    assert "MCPConfig(\n    auth=required(" in ls_text or "MCPConfig(auth=required(" in ls_text
    assert '--8<-- "docs/examples/snippets/litestar_security_mcp.py:example"' in ls_text

    auth_text = (security_dir / "authentication.md").read_text(encoding="utf-8")
    assert "GoogleIAPConfig" in auth_text

    obs_text = (security_dir / "observability.md").read_text(encoding="utf-8")
    assert r"\|" not in obs_text


def test_guide_fenced_python_blocks_matched() -> "None":
    """Verify the fenced Python block collector matches at least one guide snippet."""
    checked_blocks = len(GUIDE_FENCED_PYTHON_BLOCKS)
    assert checked_blocks > 0


@pytest.mark.parametrize(
    ("nav_page", "block_index", "code"),
    GUIDE_FENCED_PYTHON_BLOCKS,
    ids=[f"{page}#{idx}" for page, idx, _ in GUIDE_FENCED_PYTHON_BLOCKS],
)
def test_guide_fenced_python_blocks_parse_with_ast(nav_page: "str", block_index: "int", code: "str") -> "None":
    """Verify every non-include fenced Python block parses cleanly without future imports or comments."""
    location = f"docs/{nav_page}#{block_index}"
    assert "from __future__ import annotations" not in code, f"{location} includes future annotations import"
    ast.parse(code, filename=location)
    comment_lines = [
        tok.start[0] for tok in tokenize.generate_tokens(io.StringIO(code).readline) if tok.type == tokenize.COMMENT
    ]
    assert comment_lines == [], f"{location} contains inline # comments on lines {comment_lines}"


@pytest.mark.parametrize(
    ("rel_path", "required_tokens", "forbidden_tokens"),
    (
        (
            "learn/resources.md",
            (
                "uri=",
                "text/html;profile=mcp-app",
                "io.modelcontextprotocol/ui",
                "contents[0]._meta",
                "resources/subscribe",
                "resources/unsubscribe",
                "notify_resources_list_changed",
                "subscription_authorizer",
                "resourcesListChanged",
                "resourceSubscriptions",
                "skillsListChanged",
                "logLevel",
            ),
            ('"notifications/resources/list_changed": true',),
        ),
        (
            "learn/tools.md",
            (
                "generate_schema_for_handler",
                "TypedDict",
                "outputSchema",
                "structuredContent",
                "auto_output_schema",
                "output_schema=False",
                "type_encoders",
                "isError",
                "io.modelcontextprotocol/ui",
                "ui_capabilities",
                "MetadataRegistry",
            ),
            ("generate_json_schema_for_handler",),
        ),
        (
            "learn/discovery.md",
            (
                '"2025-11-25"',
                "Mcp-Session-Id",
                "enable_get_sse",
                "text/event-stream",
                "resources/subscribe",
                "logging/setLevel",
                "io.modelcontextprotocol/logLevel",
            ),
            (),
        ),
    ),
    ids=("resources-guide", "tools-guide", "discovery-guide"),
)
def test_learn_guides_cover_mcp_2025_and_ui_contracts(
    rel_path: "str",
    required_tokens: "tuple[str, ...]",
    forbidden_tokens: "tuple[str, ...]",
) -> "None":
    """Verify resources, tools, and discovery guides document MCP 2025-11-25, schema, and UI contracts."""
    text = (DOCS_DIR / rel_path).read_text(encoding="utf-8")
    missing = [token for token in required_tokens if token not in text]
    assert missing == [], f"docs/{rel_path} missing required tokens: {missing}"
    present = [token for token in forbidden_tokens if token in text]
    assert present == [], f"docs/{rel_path} contains forbidden tokens: {present}"


@pytest.mark.parametrize(
    ("rel_path", "required_tokens", "forbidden_tokens"),
    (
        (
            "learn/skills.md",
            (
                "expose_as_prompts",
                "list_changed",
                "skillsListChanged",
                "get_mcp_request_context(None)",
                "from litestar_mcp import SkillCatalog",
                "list_skills",
                "read_skill_directory",
                "list-skills",
            ),
            ("_MISSING",),
        ),
        (
            "learn/configuration.md",
            (
                "auto_output_schema",
                "ui_mime_types",
                "enable_get_sse",
                "session_ttl_seconds",
                "default_log_level",
                "subscription_authorizer",
                "expose_as_prompts",
                "list_changed",
            ),
            (),
        ),
        (
            "learn/marking-routes.md",
            (
                "generate_schema_for_handler",
                "mcp_output_schema",
                "mcp_tool_title",
                "mcp_tool_icons",
                "mcp_tool_meta",
                "mcp_resource_title",
                "mcp_resource_icons",
                "mcp_resource_meta",
                "mcp_prompt_meta",
            ),
            ("generate_json_schema_for_handler",),
        ),
    ),
    ids=("skills-guide", "configuration-guide", "marking-routes-guide"),
)
def test_skills_config_and_marking_guides_cover_new_contracts(
    rel_path: "str",
    required_tokens: "tuple[str, ...]",
    forbidden_tokens: "tuple[str, ...]",
) -> "None":
    """Verify skills, configuration, and marking-routes guides document SEP-2640 and MCPOptKeys updates."""
    text = (DOCS_DIR / rel_path).read_text(encoding="utf-8")
    missing = [token for token in required_tokens if token not in text]
    assert missing == [], f"docs/{rel_path} missing required tokens: {missing}"
    present = [token for token in forbidden_tokens if token in text]
    assert present == [], f"docs/{rel_path} contains forbidden tokens: {present}"


@pytest.mark.parametrize(
    ("rel_path", "required_tokens", "forbidden_tokens"),
    (
        (
            "learn/prompts.md",
            (
                "mcp_prompt_meta",
                "meta=",
                "expose_as_prompts",
                "capabilities.prompts",
                "PromptController",
            ),
            (),
        ),
        (
            "learn/stdio-and-bridge.md",
            (
                "2025-11-25",
                "Mcp-Session-Id",
                "GET",
                "DELETE",
                "run_stdio_async",
                "list-skills",
            ),
            (),
        ),
        (
            "agents/protocol-bridges.md",
            (
                "include_app_only",
                "execute_tool",
                "should_include_handler",
                "visibility",
                "litestar_mcp.mcp.content",
                "litestar_mcp.mcp.executor.MCPToolErrorResult",
            ),
            ("wraps each handler's underlying callable in a `Tool(handler_fn, name=tool_name)`",),
        ),
    ),
    ids=("prompts-guide", "stdio-bridge-guide", "protocol-bridges-guide"),
)
def test_prompts_stdio_and_bridge_guides_cover_new_contracts(
    rel_path: "str",
    required_tokens: "tuple[str, ...]",
    forbidden_tokens: "tuple[str, ...]",
) -> "None":
    """Verify prompts, stdio/bridge, and protocol-bridges guides document metadata and execute_tool dispatch."""
    text = (DOCS_DIR / rel_path).read_text(encoding="utf-8")
    missing = [token for token in required_tokens if token not in text]
    assert missing == [], f"docs/{rel_path} missing required tokens: {missing}"
    present = [token for token in forbidden_tokens if token in text]
    assert present == [], f"docs/{rel_path} contains forbidden tokens: {present}"


@pytest.mark.parametrize(
    ("rel_path", "required_tokens", "forbidden_tokens"),
    (
        (
            "queues/mcp-tasks.md",
            (
                "progress_token",
                "session_id",
                "log_level",
                "status_message",
                "statusMessage",
                "report_progress",
                "ctx.log",
                "subscriptions/listen",
            ),
            ("InProcessTaskExecutor",),
        ),
        (
            "queues/deployment.md",
            (
                "2025-11-25",
                "litestar_mcp_sessions",
                "MCPSessionStore",
                "mcp-session:",
                "Mcp-Session-Id",
                "session_ttl_seconds",
                "enable_get_sse",
                "subscription_authorizer",
                "subscriptions/listen",
                "wait_published",
            ),
            (
                'with_namespace("litestar_mcp_sessions")',
                "Stateless `2026-07-28` & `2025-06-18` Routing",
            ),
        ),
        (
            "security/observability.md",
            (
                "default_log_level",
                "logging/setLevel",
                "ctx.log",
                "notifications/message",
                "io.modelcontextprotocol/logLevel",
                "set_logging_level",
                "emergency",
            ),
            (r"\|",),
        ),
    ),
    ids=("mcp-tasks-guide", "queues-deployment-guide", "security-observability-guide"),
)
def test_queues_and_security_guides_cover_logging_and_dual_transport(
    rel_path: "str",
    required_tokens: "tuple[str, ...]",
    forbidden_tokens: "tuple[str, ...]",
) -> "None":
    """Verify MCP tasks, deployment, and observability guides document progress, sessions, and RFC 5424 logs."""
    text = (DOCS_DIR / rel_path).read_text(encoding="utf-8")
    missing = [token for token in required_tokens if token not in text]
    assert missing == [], f"docs/{rel_path} missing required tokens: {missing}"
    present = [token for token in forbidden_tokens if token in text]
    assert present == [], f"docs/{rel_path} contains forbidden tokens: {present}"


@pytest.mark.parametrize(
    ("doc_path", "required_tokens"),
    (
        (
            REPO_ROOT / "README.md",
            (
                "2025-11-25",
                "Mcp-Session-Id",
                "GET /mcp",
                "resources/subscribe",
                "logging/setLevel",
                "outputSchema",
                "structuredContent",
                "io.modelcontextprotocol/ui",
                "list-skills",
            ),
        ),
        (
            DOCS_DIR / "index.md",
            (
                "2025-11-25",
                "Mcp-Session-Id",
                "GET /mcp",
                "outputSchema",
                "structuredContent",
                "io.modelcontextprotocol/ui",
                "SkillCatalog",
                "list-skills",
            ),
        ),
        (
            ARCHITECTURE_DIR / "index.md",
            (
                "2025-11-25",
                "Mcp-Session-Id",
                "GET /mcp",
                "logging/setLevel",
                "outputSchema",
                "structuredContent",
                "io.modelcontextprotocol/ui",
                "include_app_only",
                "list-skills",
            ),
        ),
    ),
    ids=("readme-contracts", "docs-index-contracts", "architecture-index-contracts"),
)
def test_landing_and_architecture_docs_cover_mcp_2025_and_cli_contracts(
    doc_path: "Path",
    required_tokens: "tuple[str, ...]",
) -> "None":
    """Verify README, docs home, and architecture overview document MCP 2025-11-25, UI, and CLI contracts."""
    text = doc_path.read_text(encoding="utf-8")
    rel_label = doc_path.relative_to(REPO_ROOT).as_posix()
    missing = [token for token in required_tokens if token not in text]
    assert missing == [], f"{rel_label} missing required tokens: {missing}"


@pytest.mark.parametrize(
    ("rel_path", "required_tokens"),
    (
        (
            "getting-started/index.md",
            (
                "2025-11-25",
                "Mcp-Session-Id",
                "GET /mcp",
                "outputSchema",
                "structuredContent",
                "io.modelcontextprotocol/ui",
                "SkillCatalog",
            ),
        ),
        (
            "getting-started/installation.md",
            (
                "2025-11-25",
                "GET /mcp",
                "resources/subscribe",
                "logging/setLevel",
                "io.modelcontextprotocol/ui",
                "outputSchema",
                "structuredContent",
                "SkillCatalog",
                "list-skills",
            ),
        ),
        (
            "getting-started/quickstart.md",
            (
                "2025-11-25",
                "Mcp-Session-Id",
                "text/event-stream",
                "resources/subscribe",
                "logging/setLevel",
                "outputSchema",
                "structuredContent",
                "io.modelcontextprotocol/ui",
                "list-skills",
            ),
        ),
    ),
    ids=("getting-started-index", "getting-started-installation", "getting-started-quickstart"),
)
def test_getting_started_docs_cover_mcp_2025_and_cli_contracts(
    rel_path: "str",
    required_tokens: "tuple[str, ...]",
) -> "None":
    """Verify getting-started guides document MCP 2025-11-25 streaming, UI, outputSchema, and list-skills."""
    text = (DOCS_DIR / rel_path).read_text(encoding="utf-8")
    missing = [token for token in required_tokens if token not in text]
    assert missing == [], f"docs/{rel_path} missing required tokens: {missing}"


@pytest.mark.parametrize(
    ("rel_path", "required_tokens"),
    (
        (
            "security/index.md",
            (
                "2025-11-25",
                "GET /mcp",
                "Mcp-Session-Id",
                "MCPSessionStore",
                "subscription_authorizer",
                "logging/setLevel",
                "MCPRequestContext.log",
            ),
        ),
        (
            "reference/index.md",
            (
                "SkillCatalog",
                "SUPPORTED_PROTOCOL_VERSIONS",
                "MCPLogLevel",
                "UI_EXTENSION",
                "type_to_output_schema",
                "list-skills",
                "v0.19.0",
            ),
        ),
        (
            "learn/index.md",
            (
                '("2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26")',
                "GET /mcp",
                "Mcp-Session-Id",
                "io.modelcontextprotocol/ui",
                "outputSchema",
                "structuredContent",
                "logging/setLevel",
                "listChanged",
                "list-skills",
            ),
        ),
    ),
    ids=("security-index-contracts", "reference-index-contracts", "learn-index-contracts"),
)
def test_security_reference_and_learn_index_cover_new_contracts(
    rel_path: "str",
    required_tokens: "tuple[str, ...]",
) -> "None":
    """Verify security, reference, and learn index pages cover MCP 2025-11-25, exports, and v0.19.0 changelog."""
    text = (DOCS_DIR / rel_path).read_text(encoding="utf-8")
    missing = [token for token in required_tokens if token not in text]
    assert missing == [], f"docs/{rel_path} missing required tokens: {missing}"


@pytest.mark.parametrize(
    ("rel_path", "required_directives"),
    (
        (
            "learn/tools.md",
            ('--8<-- "docs/examples/snippets/mcp_apps_and_structured.py:example"',),
        ),
        (
            "learn/skills.md",
            ('--8<-- "docs/examples/snippets/skills_prompts_and_catalog.py:example"',),
        ),
        (
            "reference/primitives-and-skills.md",
            (
                "::: litestar_mcp.utils.classify_resource_uri",
                "::: litestar_mcp.mcp.skills.filter_skills",
            ),
        ),
    ),
    ids=("tools-snippet-embed", "skills-snippet-embed", "primitives-reference-directives"),
)
def test_learn_guides_embed_ch09_snippet_slices(
    rel_path: "str",
    required_directives: "tuple[str, ...]",
) -> "None":
    """Verify learn guides embed ch09 snippet slices and primitives reference includes new directives."""
    text = (DOCS_DIR / rel_path).read_text(encoding="utf-8")
    missing = [directive for directive in required_directives if directive not in text]
    assert missing == [], f"docs/{rel_path} missing required directives: {missing}"
