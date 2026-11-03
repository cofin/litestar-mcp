"""Unit tests for documentation tooling, docstring scanner, CLI generator, postbuild, and artifact verifier."""

import sys
import zlib
from pathlib import Path

import pytest
from tools.docs.generate_cli_docs import DEFAULT_OUTPUT as DEFAULT_CLI_OUTPUT
from tools.docs.generate_cli_docs import main as cli_docs_main
from tools.docs.generate_cli_docs import render_cli_markdown
from tools.docs.postbuild import LEGACY_REDIRECTS, compact_site_html, run_postbuild
from tools.docs.scan_public_docstrings import scan_package
from tools.docs.verify_docs_artifacts import REQUIRED_INVENTORY_SYMBOLS, verify_site

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[2]


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
    """Verify `render_cli_markdown` documents every `litestar mcp` subcommand cleanly."""
    rendered = render_cli_markdown()
    assert f"### `litestar mcp {subcommand}`" in rendered
    assert "Sentinel.UNSET" not in rendered
    assert "[bold" not in rendered


def test_generate_cli_docs_check_matches_generated_file() -> None:
    """Verify `tools/docs/generated/cli-reference.md` exists and matches `generate_cli_docs.py --check`."""
    assert DEFAULT_CLI_OUTPUT.is_file()
    assert cli_docs_main(["--check"]) == 0


def test_legacy_redirects_contains_all_29_moved_routes() -> None:
    """Verify `LEGACY_REDIRECTS` maps all 29 moved legacy `.rst` URLs including `usage`."""
    assert len(LEGACY_REDIRECTS) == 29
    assert LEGACY_REDIRECTS["usage"] == "learn/"
    assert LEGACY_REDIRECTS["contribution-guide"] == "reference/contributing/"
    assert LEGACY_REDIRECTS["usage/configuration"] == "learn/configuration/"
    assert LEGACY_REDIRECTS["reference/plugin"] == "reference/plugin-and-config/"


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


def test_verify_site_ignores_directives_and_roles_inside_code_and_script_elements(tmp_path: Path) -> None:
    """Verify `verify_site` ignores `:::` and `:class:` occurrences inside `<code>`, `<pre>`, and `<script>` tags."""
    site_dir = tmp_path / "site"
    docs_dir = tmp_path / "docs"
    site_dir.mkdir(parents=True)
    docs_dir.mkdir(parents=True)
    (docs_dir / "index.md").write_text("# Litestar MCP\n", encoding="utf-8")
    safe_html = (
        "<html><body>"
        "<pre><code>::: litestar_mcp.MCPConfig</code></pre>"
        "<script >const role = ':class:`LitestarMCP`';</script >"
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
    assert project["exclude_docs"] == "examples/**"
    assert project["extra_css"] == ["stylesheets/extra.css"]
    assert len(project["nav"]) == 7

    pyproject_data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert "*.md" in pyproject_data["tool"]["ruff"]["format"]["exclude"]
    assert "pygments.styles" not in pyproject_data["project"].get("entry-points", {})


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
