"""Unit tests for documentation tooling, docstring scanner, CLI generator, postbuild, and artifact verifier."""

import re
import sys
import zlib
from html.parser import HTMLParser
from pathlib import Path

import pytest
from tools.docs.generate_cli_docs import DEFAULT_OUTPUT as DEFAULT_CLI_OUTPUT
from tools.docs.generate_cli_docs import main as cli_docs_main
from tools.docs.generate_cli_docs import render_cli_markdown
from tools.docs.postbuild import DEFAULT_REFERENCE_SYMBOLS, LEGACY_REDIRECTS, compact_site_html, run_postbuild
from tools.docs.scan_public_docstrings import scan_package
from tools.docs.verify_docs_artifacts import REQUIRED_INVENTORY_SYMBOLS, verify_site

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]
INCLUDES_DIR = REPO_ROOT / "tools" / "docs" / "includes"
FORBIDDEN_CORE_DIAGRAM_SYMBOLS = (
    "InProcessTaskExecutor",
    "ToolAnnotations",
    "mcp_tool_output_schema",
    "ToolContext.report_progress",
    "CompositeSkillSource",
    "skills://",
    "skills/load",
    "skills/read_file",
    "tasks/result",
    "tasks/list",
    "notifications/tasks/status",
    "default_ttl",
    "max_tasks",
    "Checks ready_nodes()",
    "StepResult(next_nodes)",
    "WorkflowEngine.run(context)",
    "--url",
)
FORBIDDEN_AGENT_DIAGRAM_SYMBOLS = (
    "POST /chat/stream",
    "StreamChunk",
    "max_steps",
    "max_duration_s",
    "transfer_to_&lt;agent&gt;",
    "max_handoffs",
    "generate() &amp; stream()",
    "output_schema",
    "mcp_to_tools(app)",
    "LitestarMCP / MCPClient",
    "Agent.run()",
    "base_path=",
    "path param &gt; header",
    'context.state["litestar_request"]',
)


@pytest.mark.parametrize(
    ("source_code", "expected_rule"),
    [
        (
            'def greet() -> str:\n    """Return greeting."""\n    return \'hi\'\n',
            "missing-module-docstring",
        ),
        (
            '"""Module doc."""\n\nclass PublicWidget:\n    pass\n',
            "missing-class-docstring",
        ),
        (
            '"""Module doc."""\n\ndef public_action() -> None:\n    pass\n',
            "missing-function-docstring",
        ),
        (
            '"""Module doc."""\nfrom dataclasses import dataclass\n\n@dataclass\nclass Cfg:\n    """Config."""\n    port: int = 8000\n',
            "missing-field-docstring",
        ),
        (
            '"""Module doc."""\nfrom dataclasses import dataclass\n\n@dataclass\nclass Cfg:\n    """Config.\n\n    Attributes:\n        port: TCP port.\n    """\n    port: int = 8000\n    """TCP port."""\n',
            "hybrid-duplicate-attributes",
        ),
        (
            '"""Module doc."""\nfrom typing import overload\n\n@overload\ndef parse(x: int) -> int:\n    """Overload doc."""\n    ...\n\ndef parse(x: int | str) -> int | str:\n    """Parse input."""\n    return x\n',
            "overload-has-docstring",
        ),
        (
            '"""Module doc."""\n\ndef build() -> None:\n    """Build :class:`MCPConfig` instance."""\n    return None\n',
            "rst-directive-in-docstring",
        ),
    ],
)
def test_scan_public_docstrings_rules(tmp_path: Path, source_code: str, expected_rule: str) -> None:
    """Verify `scan_package` detects each of the 7 AST docstring and field hygiene rules."""
    sample = tmp_path / "sample.py"
    sample.write_text(source_code, encoding="utf-8")
    findings = scan_package(tmp_path)
    rules = {finding.kind for finding in findings}
    assert expected_rule in rules


def test_litestar_mcp_public_docstrings_have_zero_findings() -> None:
    """Verify 100% of `litestar_mcp/**` passes public docstring and PEP 257 field docstring checks."""
    findings = scan_package(REPO_ROOT / "litestar_mcp")
    assert findings == []


@pytest.mark.parametrize(
    "subcommand",
    ["stdio", "bridge", "export-tool-spec", "list-tools", "list-resources", "run"],
)
def test_generate_cli_docs_includes_all_subcommands(subcommand: str) -> None:
    """Verify `render_cli_markdown` documents every `litestar mcp` subcommand without heading backticks."""
    rendered = render_cli_markdown()
    assert f"### litestar mcp {subcommand}" in rendered
    assert f"### `litestar mcp {subcommand}`" not in rendered
    assert "Sentinel.UNSET" not in rendered
    assert "[bold" not in rendered


def test_generate_cli_docs_check_matches_generated_file() -> None:
    """Verify `tools/docs/generated/cli-reference.md` exists and matches `generate_cli_docs.py --check`."""
    assert DEFAULT_CLI_OUTPUT.is_file()
    assert cli_docs_main(["--check"]) == 0


def test_legacy_redirects_contains_all_29_moved_routes() -> None:
    """Verify `LEGACY_REDIRECTS` maps all 31 moved legacy URLs including retired migration slugs."""
    assert len(LEGACY_REDIRECTS) == 31
    assert LEGACY_REDIRECTS["usage"] == "learn/"
    assert LEGACY_REDIRECTS["contribution-guide"] == "reference/contributing/"
    assert LEGACY_REDIRECTS["usage/configuration"] == "learn/configuration/"
    assert LEGACY_REDIRECTS["usage/migration_0_14"] == "changelog/"
    assert LEGACY_REDIRECTS["usage/migration_0_12"] == "changelog/"
    assert LEGACY_REDIRECTS["security/migration-0-14"] == "changelog/"
    assert LEGACY_REDIRECTS["security/migration-0-12"] == "changelog/"
    assert LEGACY_REDIRECTS["reference/plugin"] == "reference/plugin-and-config/"


@pytest.mark.parametrize(
    "expected_symbol",
    [
        "litestar_mcp.agent.ANONYMOUS_OWNER",
        "litestar_mcp.agent.ChatRequest",
        "litestar_mcp.agent.TurnResult",
        "litestar_mcp.agent.SessionHistory",
        "litestar_mcp.agent.SessionNotFoundError",
        "litestar_mcp.agent.BudgetExceededError",
        "litestar_mcp.agent.AgentStreamFrame",
        "litestar_mcp.core.ToolArgumentError",
        "litestar_mcp.core.SpanManager",
        "litestar_mcp.core.encode_json",
        "litestar_mcp.core.decode_json",
        "litestar_mcp.core.to_json",
        "litestar_mcp.core.from_json",
        "litestar_mcp.core.LitestarMCPError",
        "litestar_mcp.core.MissingDependencyError",
        "litestar_mcp.core.BridgeConnectionError",
        "litestar_mcp.core.BridgeMessageTooLargeError",
    ],
)
def test_default_reference_symbols_include_agent_and_core_exports(expected_symbol: str) -> None:
    """Verify `DEFAULT_REFERENCE_SYMBOLS` omits removed symbols and includes `agent` and `core` exports."""
    symbol_names = {entry[0] for entry in DEFAULT_REFERENCE_SYMBOLS}
    assert "litestar_mcp.ToolAnnotations" not in symbol_names
    assert "litestar_mcp.InProcessTaskExecutor" not in symbol_names
    assert expected_symbol in symbol_names


@pytest.mark.parametrize(
    ("old_slug", "expected_relative_target"),
    [
        ("contribution-guide", "../reference/contributing/"),
        ("usage", "../learn/"),
        ("usage/configuration", "../../learn/configuration/"),
        ("usage/marking_routes", "../../learn/marking-routes/"),
        ("reference/plugin", "../../reference/plugin-and-config/"),
        ("reference/a2a", "../../reference/a2a-and-bridges/"),
    ],
)
def test_postbuild_writes_depth_accurate_redirect_stubs(
    tmp_path: Path,
    old_slug: str,
    expected_relative_target: str,
) -> None:
    """Verify `run_postbuild` calculates 1-segment and 2-segment relative redirect paths accurately."""
    site_dir = tmp_path / "site"
    docs_dir = tmp_path / "docs"
    site_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    (site_dir / "index.html").write_text("<html><body><h1>Litestar MCP</h1></body></html>\n", encoding="utf-8")
    (docs_dir / "index.md").write_text("# Litestar MCP\n\nOverview.\n", encoding="utf-8")

    run_postbuild(site_dir=site_dir, docs_dir=docs_dir)

    stub_path = site_dir / old_slug / "index.html"
    assert stub_path.is_file()
    stub_html = stub_path.read_text(encoding="utf-8")
    assert f'url={expected_relative_target}"' in stub_html
    assert (site_dir / f"{old_slug}.html").is_file()


@pytest.mark.parametrize("required_symbol", list(REQUIRED_INVENTORY_SYMBOLS))
def test_postbuild_generates_valid_objects_inv_and_llms_files(
    tmp_path: Path,
    required_symbol: str,
) -> None:
    """Verify `run_postbuild` generates valid Sphinx v2 `objects.inv`, `llms.txt`, and `llms-full.txt`."""
    site_dir = tmp_path / "site"
    docs_dir = tmp_path / "docs"
    site_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    (site_dir / "index.html").write_text(
        "<html>\n\n  <body>\n    <h1>Litestar MCP</h1>   \n\n  </body>\n</html>\n",
        encoding="utf-8",
    )
    (docs_dir / "index.md").write_text("# Litestar MCP\n\nWelcome.\n", encoding="utf-8")

    saved = compact_site_html(site_dir=site_dir)
    assert saved > 0

    run_postbuild(site_dir=site_dir, docs_dir=docs_dir)

    assert (site_dir / ".nojekyll").is_file()
    assert (site_dir / "llms.txt").is_file()
    assert (site_dir / "llms-full.txt").is_file()

    inv_bytes = (site_dir / "objects.inv").read_bytes()
    assert inv_bytes.startswith(b"# Sphinx inventory version 2\n")
    lines = inv_bytes.splitlines(keepends=True)
    decompressed = zlib.decompress(b"".join(lines[4:])).decode("utf-8")
    assert required_symbol in decompressed

    findings = verify_site(site_dir=site_dir, docs_dir=docs_dir)
    assert findings == []


@pytest.mark.parametrize(
    ("bad_html", "expected_rule"),
    [
        (
            "<html><body><p>::: litestar_mcp.MCPConfig</p></body></html>",
            "unrendered-mkdocstrings-directive",
        ),
        (
            '<html><body><p>--8<-- "docs/examples/snippets/quick_start_plugin.py"</p></body></html>',
            "unrendered-snippet",
        ),
        (
            "<html><body><p>See :class:`LitestarMCP` for details.</p></body></html>",
            "raw-sphinx-role",
        ),
    ],
)
def test_verify_site_detects_unrendered_directives_snippets_and_roles(
    tmp_path: Path,
    bad_html: str,
    expected_rule: str,
) -> None:
    """Verify `verify_site` detects raw `:::`, `--8<--`, and Sphinx `:class:` leaks in built HTML."""
    site_dir = tmp_path / "site"
    docs_dir = tmp_path / "docs"
    site_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    (docs_dir / "index.md").write_text("# Litestar MCP\n", encoding="utf-8")
    (site_dir / "index.html").write_text(bad_html, encoding="utf-8")

    run_postbuild(site_dir=site_dir, docs_dir=docs_dir)
    (site_dir / "index.html").write_text(bad_html, encoding="utf-8")

    findings = verify_site(site_dir=site_dir, docs_dir=docs_dir)
    rules = {finding.rule for finding in findings}
    assert expected_rule in rules


@pytest.mark.parametrize(
    ("bad_html", "expected_rule"),
    [
        (
            "<html><body><pre><code>\x23 start-example\nx = 1</code></pre></body></html>",
            "leaked-snippet-marker",
        ),
        (
            "<html><body><h2>1. Getting Started</h2></body></html>",
            "heading-format-violation",
        ),
        (
            "<html><body><h3>Using <code>LitestarMCP</code></h3></body></html>",
            "heading-format-violation",
        ),
        (
            '<html><body><table><tbody><tr class="doc-section-item"><td>x</td></tr></tbody></table></body></html>',
            "missing-table-caption",
        ),
    ],
)
def test_verify_site_detects_snippet_markers_and_heading_violations(
    tmp_path: Path,
    bad_html: str,
    expected_rule: str,
) -> None:
    """Verify `verify_site` catches leaked snippet markers, numbered/backticked headings, and uncaptioned tables."""
    site_dir = tmp_path / "site"
    docs_dir = tmp_path / "docs"
    site_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    (docs_dir / "index.md").write_text("# Litestar MCP\n", encoding="utf-8")
    (site_dir / "index.html").write_text(bad_html, encoding="utf-8")
    run_postbuild(site_dir=site_dir, docs_dir=docs_dir)
    (site_dir / "index.html").write_text(bad_html, encoding="utf-8")

    findings = verify_site(site_dir=site_dir, docs_dir=docs_dir)
    assert expected_rule in {f.rule for f in findings}


def test_verify_site_ignores_directives_and_roles_inside_code_and_script_elements(tmp_path: Path) -> None:
    """Verify `verify_site` ignores `:::`, `:class:`, `h3.doc-heading`, and captioned attribute tables."""
    site_dir = tmp_path / "site"
    docs_dir = tmp_path / "docs"
    site_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    (docs_dir / "index.md").write_text("# Litestar MCP\n", encoding="utf-8")
    safe_html = (
        "<html><body>"
        "<pre><code>::: litestar_mcp.MCPConfig</code></pre>"
        "<script >const role = ':class:`LitestarMCP`';</script >"
        '<h3 class="doc display-name doc-heading"><code>LitestarMCP</code></h3>'
        '<table><caption class="doc-section-title">Attributes</caption>'
        '<tbody><tr class="doc-section-item"><td><code>base_path</code></td></tr></tbody></table>'
        "</body></html>"
    )
    (site_dir / "index.html").write_text(safe_html, encoding="utf-8")
    run_postbuild(site_dir=site_dir, docs_dir=docs_dir)
    (site_dir / "index.html").write_text(safe_html, encoding="utf-8")

    assert verify_site(site_dir=site_dir, docs_dir=docs_dir) == []


def test_zensical_toml_and_pyproject_configuration() -> None:
    """Verify `zensical.toml` and `pyproject.toml` configure the 7-tab docs architecture and Markdown safety."""
    zensical_data = tomllib.loads((REPO_ROOT / "zensical.toml").read_text(encoding="utf-8"))
    project = zensical_data["project"]
    assert project["site_name"] == "Litestar MCP"
    assert isinstance(project["exclude_docs"], str)
    assert "examples/**" in project["exclude_docs"]
    assert "_build/**" in project["exclude_docs"]
    assert project["extra_css"] == ["stylesheets/extra.css"]
    assert len(project["nav"]) == 7

    pyproject_data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "*.md" in pyproject_data["tool"]["ruff"]["format"]["exclude"]
    assert "pygments.styles" not in pyproject_data["project"].get("entry-points", {})

    extra_css = (REPO_ROOT / "docs" / "stylesheets" / "extra.css").read_text(encoding="utf-8")
    assert "--md-default-bg-color: #0f172a;" in extra_css
    assert "flex-wrap: nowrap;" in extra_css
    assert "caption.doc-section-title" in extra_css
    assert ".md-nav__item:only-child)" in extra_css

    attr_template = (
        REPO_ROOT / "tools" / "docs" / "templates" / "python" / "material" / "docstring" / "attributes.html.jinja"
    ).read_text(encoding="utf-8")
    assert '<caption class="doc-section-title">' in attr_template


@pytest.mark.parametrize(
    "asset_rel_path",
    [
        "docs/stylesheets/extra.css",
        "docs/assets/logo.svg",
        "docs/assets/favicon.svg",
        "docs/assets/favicon.png",
        "docs/assets/banner-inline-light.svg",
        "docs/assets/banner-inline-dark.svg",
        "docs/assets/badge-light.svg",
        "docs/assets/badge-dark.svg",
        "docs/assets/og-social-light.png",
        "docs/assets/og-social-dark.png",
        "tools/docs/templates/python/material/attribute.html.jinja",
        "tools/docs/templates/python/material/docstring/attributes.html.jinja",
        "tools/docs/templates/python/material/summary/attributes.html.jinja",
    ],
)
def test_branding_assets_and_templates_exist(asset_rel_path: str) -> None:
    """Verify official `litestar-org/branding` assets, `extra.css`, and Griffe templates exist on disk."""
    assert (REPO_ROOT / asset_rel_path).is_file()


class _SvgBlockValidator(HTMLParser):
    """Validate SVG root attributes, balanced tags, and required accessibility elements."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.root_attrs: dict[str, str | None] = {}
        self.seen_tags: set[str] = set()
        self.open_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record opening tag and SVG root attributes."""
        if tag == "svg" and not self.root_attrs:
            self.root_attrs = dict(attrs)
        self.seen_tags.add(tag)
        self.open_stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Record self-closing SVG element."""
        self.seen_tags.add(tag)

    def handle_endtag(self, tag: str) -> None:
        """Verify LIFO closing tag balance."""
        assert self.open_stack and self.open_stack[-1] == tag
        self.open_stack.pop()

    @classmethod
    def validate(cls, svg_block: str) -> "_SvgBlockValidator":
        """Parse an SVG block and assert well-formed tag balance and required accessibility attributes."""
        parser = cls()
        parser.feed(svg_block)
        parser.close()
        assert parser.open_stack == []
        assert parser.root_attrs.get("viewbox")
        assert parser.root_attrs.get("role") == "img"
        assert parser.root_attrs.get("aria-labelledby")
        assert {"title", "desc"}.issubset(parser.seen_tags)
        return parser


@pytest.mark.parametrize(
    "diagram_name",
    [
        "diagram-request-pipeline.html",
        "diagram-primitives-and-discovery.html",
        "diagram-mcp-tasks-and-queues.html",
        "diagram-durable-workflows.html",
    ],
)
def test_core_and_queue_svg_diagrams_have_current_symbols_and_unnumbered_titles(diagram_name: str) -> None:
    """Core, primitives, tasks, and workflow SVG diagrams contain zero stale symbols or numbered titles."""
    content = (INCLUDES_DIR / diagram_name).read_text(encoding="utf-8")
    assert not re.search(r">\s*\d+\.\s+[A-Z]", content), f"{diagram_name} contains numbered box title"
    stale_symbols = [symbol for symbol in FORBIDDEN_CORE_DIAGRAM_SYMBOLS if symbol in content]
    assert stale_symbols == [], f"{diagram_name} still contains stale symbols: {stale_symbols}"
    svg_blocks = re.findall(r"<svg\b.*?</svg>", content, flags=re.DOTALL)
    assert svg_blocks, f"{diagram_name} contains no <svg> blocks"
    assert [_SvgBlockValidator.validate(svg_block) for svg_block in svg_blocks]


@pytest.mark.parametrize(
    "diagram_name",
    [
        "diagram-agent-runtime-and-bridges.html",
        "diagram-a2a-interoperability.html",
    ],
)
def test_agent_and_a2a_svg_diagrams_have_current_symbols_and_unnumbered_titles(diagram_name: str) -> None:
    """Agent runtime and A2A SVG diagrams contain zero stale symbols or numbered box titles."""
    content = (INCLUDES_DIR / diagram_name).read_text(encoding="utf-8")
    assert not re.search(r">\s*\d+\.\s+[A-Z]", content), f"{diagram_name} contains numbered box title"
    stale_symbols = [symbol for symbol in FORBIDDEN_AGENT_DIAGRAM_SYMBOLS if symbol in content]
    assert stale_symbols == [], f"{diagram_name} still contains stale symbols: {stale_symbols}"
    svg_blocks = re.findall(r"<svg\b.*?</svg>", content, flags=re.DOTALL)
    assert svg_blocks, f"{diagram_name} contains no <svg> blocks"
    assert [_SvgBlockValidator.validate(svg_block) for svg_block in svg_blocks]
