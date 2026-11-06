"""Post-build pipeline for documentation artifacts, inventories, and legacy redirects."""

import ast
import posixpath
import re
import shutil
import sys
import zlib
from collections.abc import Sequence
from pathlib import Path

import msgspec

import litestar_mcp
from litestar_mcp.__metadata__ import __version__

__all__ = [
    "DEFAULT_DOCS_DIR",
    "DEFAULT_SITE_DIR",
    "LEGACY_REDIRECTS",
    "PACKAGE_ROOT",
    "SITE_URL",
    "InventoryEntry",
    "build_llms_files",
    "build_objects_inv",
    "compact_site_html",
    "main",
    "run_postbuild",
    "write_legacy_redirects",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = REPO_ROOT / "litestar_mcp"
DEFAULT_SITE_DIR = REPO_ROOT / "site"
DEFAULT_DOCS_DIR = REPO_ROOT / "docs"
SITE_URL = "https://cofin.github.io/litestar-mcp"

SNIPPET_INCLUDE_RE = re.compile(r'^(\s*)--8<--\s+"([^"]+)"\s*$', re.MULTILINE)
DIRECTIVE_RE = re.compile(r"^:::\s+(litestar_mcp(?:\.[A-Za-z0-9_]+)*)\s*$", re.MULTILINE)
PRE_OR_TEXTAREA_RE = re.compile(r"(<(?:pre|textarea)\b[^>]*>.*?</(?:pre|textarea)\b[^>]*>)", re.DOTALL | re.IGNORECASE)
LEADING_INDENT_RE = re.compile(r"\n[ \t]+")
MULTI_BLANK_LINES_RE = re.compile(r"\n\s*\n+")
TRAILING_WS_RE = re.compile(r"[ \t]+\n")

LEGACY_REDIRECTS: dict[str, str] = {
    "contribution-guide": "reference/contributing/",
    "usage": "learn/",
    "usage/configuration": "learn/configuration/",
    "usage/marking_routes": "learn/marking-routes/",
    "usage/tools": "learn/tools/",
    "usage/resources": "learn/resources/",
    "usage/prompts": "learn/prompts/",
    "usage/skills": "learn/skills/",
    "usage/discovery": "learn/discovery/",
    "usage/bridge": "learn/stdio-and-bridge/",
    "usage/standalone_app": "getting-started/standalone/",
    "usage/framework_integration": "getting-started/concepts/",
    "usage/reference_examples": "architecture/reference-examples/",
    "usage/uvx_guide": "architecture/uvx-and-pep723/",
    "usage/deployment": "queues/deployment/",
    "usage/agents": "agents/",
    "usage/a2a": "agents/a2a-protocol/",
    "usage/auth": "security/authentication/",
    "usage/security": "security/authorization-and-scopes/",
    "usage/migration_0_14": "changelog/",
    "usage/migration_0_12": "changelog/",
    "security/migration-0-14": "changelog/",
    "security/migration-0-12": "changelog/",
    "reference/plugin": "reference/plugin-and-config/",
    "reference/config": "reference/plugin-and-config/",
    "reference/handlers": "reference/primitives-and-skills/",
    "reference/types": "reference/primitives-and-skills/",
    "reference/agent": "reference/agents-and-workflows/",
    "reference/a2a": "reference/a2a-and-bridges/",
    "reference/bridge": "reference/a2a-and-bridges/",
    "reference/stdio": "reference/a2a-and-bridges/",
}

DEFAULT_REFERENCE_SYMBOLS: tuple[tuple[str, str, str], ...] = (
    ("litestar_mcp", "py:module", "reference/"),
    ("litestar_mcp.LitestarMCP", "py:class", "reference/plugin-and-config/"),
    ("litestar_mcp.MCP", "py:class", "reference/plugin-and-config/"),
    ("litestar_mcp.MCPConfig", "py:class", "reference/plugin-and-config/"),
    ("litestar_mcp.MCPOptKeys", "py:class", "reference/plugin-and-config/"),
    ("litestar_mcp.MCPTaskConfig", "py:class", "reference/plugin-and-config/"),
    ("litestar_mcp.MCPSkillsConfig", "py:class", "reference/plugin-and-config/"),
    ("litestar_mcp.mcp_tool", "py:function", "reference/primitives-and-skills/"),
    ("litestar_mcp.mcp_resource", "py:function", "reference/primitives-and-skills/"),
    ("litestar_mcp.mcp_prompt", "py:function", "reference/primitives-and-skills/"),
    ("litestar_mcp.PromptController", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.SkillController", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.Skill", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.SkillFile", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.SkillCatalog", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.SkillSource", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.FilesystemSkillSource", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.MCPToolResult", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.MCPResourceLink", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.MCPBlobResource", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.MCPInputRequiredResult", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.Registry", "py:class", "reference/primitives-and-skills/"),
    ("litestar_mcp.MCPTaskStore", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.TaskRecord", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.MCPTaskExecutor", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.ToolTaskCall", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.QueueTaskExecutor", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.DurableWorkflowEngine", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.WorkflowStore", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.WorkflowRun", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.NodeRecord", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.TaskDependencyChain", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.QueuedAgentChatController", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.QueuedTurnManager", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.QueuedTurn", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.AgentRegistry", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.queues.PrincipalSnapshot", "py:class", "reference/tasks-and-queues/"),
    ("litestar_mcp.agent.Agent", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.AgentGroup", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.AgentRuntime", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.TurnBudget", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.AgentChatController", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.SessionStore", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.MemorySessionStore", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.LitestarStoreSessionStore", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.ANONYMOUS_OWNER", "py:data", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.ChatRequest", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.TurnResult", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.SessionHistory", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.SessionNotFoundError", "py:exception", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.BudgetExceededError", "py:exception", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.AgentStreamFrame", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.ModelClient", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.GoogleGenAIClient", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.MockModelClient", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.DynamicWorkflow", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.WorkflowNode", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.WorkflowEngine", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.WorkflowContext", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.agent.StepResult", "py:class", "reference/agents-and-workflows/"),
    ("litestar_mcp.a2a.LitestarA2A", "py:class", "reference/a2a-and-bridges/"),
    ("litestar_mcp.a2a.A2AConfig", "py:class", "reference/a2a-and-bridges/"),
    ("litestar_mcp.agent.agent_to_mcp", "py:function", "reference/a2a-and-bridges/"),
    ("litestar_mcp.agent.mcp_to_tools", "py:function", "reference/a2a-and-bridges/"),
    ("litestar_mcp.agent.discover_mcp_tools", "py:function", "reference/a2a-and-bridges/"),
    ("litestar_mcp.agent.agent_to_a2a", "py:function", "reference/a2a-and-bridges/"),
    ("litestar_mcp.MCPStdioContext", "py:class", "reference/a2a-and-bridges/"),
    ("litestar_mcp.run_stdio", "py:function", "reference/a2a-and-bridges/"),
    ("litestar_mcp.run_stdio_async", "py:function", "reference/a2a-and-bridges/"),
    ("litestar_mcp.run_stdio_streamable_http_bridge", "py:function", "reference/a2a-and-bridges/"),
    ("litestar_mcp.MCPStreamableHTTPClient", "py:class", "reference/a2a-and-bridges/"),
    ("litestar_mcp.core.Tool", "py:class", "reference/core-and-observability/"),
    ("litestar_mcp.core.ToolCall", "py:class", "reference/core-and-observability/"),
    ("litestar_mcp.core.ToolResult", "py:class", "reference/core-and-observability/"),
    ("litestar_mcp.core.tool", "py:function", "reference/core-and-observability/"),
    ("litestar_mcp.core.ToolContext", "py:class", "reference/core-and-observability/"),
    ("litestar_mcp.core.ToolArgumentError", "py:exception", "reference/core-and-observability/"),
    ("litestar_mcp.core.TelemetryConfig", "py:class", "reference/core-and-observability/"),
    ("litestar_mcp.core.SpanManager", "py:class", "reference/core-and-observability/"),
    ("litestar_mcp.core.encode_json", "py:function", "reference/core-and-observability/"),
    ("litestar_mcp.core.decode_json", "py:function", "reference/core-and-observability/"),
    ("litestar_mcp.core.to_json", "py:function", "reference/core-and-observability/"),
    ("litestar_mcp.core.from_json", "py:function", "reference/core-and-observability/"),
    ("litestar_mcp.core.LitestarMCPError", "py:exception", "reference/core-and-observability/"),
    ("litestar_mcp.core.MissingDependencyError", "py:exception", "reference/core-and-observability/"),
    ("litestar_mcp.core.BridgeConnectionError", "py:exception", "reference/core-and-observability/"),
    ("litestar_mcp.core.BridgeMessageTooLargeError", "py:exception", "reference/core-and-observability/"),
    ("litestar_mcp.JSONRPCRequest", "py:class", "reference/core-and-observability/"),
    ("litestar_mcp.JSONRPCError", "py:class", "reference/core-and-observability/"),
    ("litestar_mcp.JSONRPCErrorException", "py:exception", "reference/core-and-observability/"),
    ("litestar_mcp.SubscriptionManager", "py:class", "reference/core-and-observability/"),
)


class InventoryEntry(msgspec.Struct, frozen=True, gc=False):
    """Single Sphinx objects.inv v2 entry."""

    name: str
    """Fully qualified Python symbol name."""

    domain_role: str
    """Sphinx domain and role (`py:module`, `py:class`, `py:function`, `py:method`, `py:exception`)."""

    priority: int
    """Search priority (`1` for primary symbols)."""

    uri: str
    """Relative documentation URI and anchor."""

    dispname: str = "-"
    """Display name override (`-` when identical to `name`)."""


def compact_site_html(site_dir: Path = DEFAULT_SITE_DIR) -> int:
    """Compact redundant blank lines and leading/trailing whitespace outside `<pre>` and `<textarea>` blocks.

    Args:
        site_dir: Built site directory containing `.html` files.

    Returns:
        Total bytes saved across all compacted HTML files.
    """
    total_saved = 0
    for html_file in sorted(site_dir.rglob("*.html")):
        raw = html_file.read_text(encoding="utf-8")
        parts = PRE_OR_TEXTAREA_RE.split(raw)
        compacted_parts: list[str] = []
        for idx, segment in enumerate(parts):
            if idx % 2 == 1:
                compacted_parts.append(segment)
            else:
                cleaned = TRAILING_WS_RE.sub("\n", segment)
                cleaned = LEADING_INDENT_RE.sub("\n", cleaned)
                cleaned = MULTI_BLANK_LINES_RE.sub("\n", cleaned)
                compacted_parts.append(cleaned)
        compacted = "".join(compacted_parts)
        saved = len(raw.encode("utf-8")) - len(compacted.encode("utf-8"))
        if saved > 0:
            html_file.write_text(compacted, encoding="utf-8")
            total_saved += saved
    return total_saved


def build_objects_inv(
    site_dir: Path = DEFAULT_SITE_DIR,
    docs_dir: Path = DEFAULT_DOCS_DIR,
    package_root: Path = PACKAGE_ROOT,
) -> Path:
    """Write a Sphinx v2 `objects.inv` file to `site/objects.inv`.

    Args:
        site_dir: Built documentation site directory.
        docs_dir: Documentation source directory.
        package_root: Root of the `litestar_mcp` Python package.

    Returns:
        Path to the written `objects.inv` file.
    """
    entries = _collect_inventory_entries(docs_dir=docs_dir, package_root=package_root)
    header = (
        f"# Sphinx inventory version 2\n"
        f"# Project: litestar-mcp\n"
        f"# Version: {__version__}\n"
        f"# The remainder of this file is compressed using zlib.\n"
    ).encode()
    payload_lines = [f"{e.name} {e.domain_role} {e.priority} {e.uri} {e.dispname}\n" for e in entries]
    compressed = zlib.compress("".join(payload_lines).encode("utf-8"), level=9)
    site_dir.mkdir(parents=True, exist_ok=True)
    inv_path = site_dir / "objects.inv"
    inv_path.write_bytes(header + compressed)
    return inv_path


def build_llms_files(
    site_dir: Path = DEFAULT_SITE_DIR,
    docs_dir: Path = DEFAULT_DOCS_DIR,
    site_url: str = SITE_URL,
) -> tuple[Path, Path]:
    """Generate `site/llms.txt` and `site/llms-full.txt`.

    Args:
        site_dir: Built documentation site directory.
        docs_dir: Documentation source directory.
        site_url: Canonical base URL for generated links.

    Returns:
        Tuple of (`llms.txt` path, `llms-full.txt` path).
    """
    llms_index_lines = [
        "# Litestar MCP",
        "",
        "> Model Context Protocol (MCP 2026-07-28 & 2025-06-18), Agent Runtime, Durable Workflows, and A2A 1.0 Integration for Litestar.",
        "",
        "## Documentation Index",
        "",
    ]

    full_sections: list[str] = ["# Litestar MCP — Complete Documentation Corpus", ""]

    md_files = sorted(path for path in docs_dir.rglob("*.md") if "examples" not in path.relative_to(docs_dir).parts)
    for md_path in md_files:
        rel_md = md_path.relative_to(docs_dir).as_posix()
        raw_md = md_path.read_text(encoding="utf-8")
        expanded_md = _expand_snippets(raw_md)
        title_match = re.search(r"^#\s+(.+)$", expanded_md, re.MULTILINE) or re.search(
            r"^title:\s*(.+)$", expanded_md, re.MULTILINE
        )
        title = title_match.group(1).strip().strip("\"'") if title_match else rel_md
        if rel_md == "index.md":
            url_path = ""
        elif rel_md.endswith("/index.md"):
            url_path = rel_md.removesuffix("index.md")
        else:
            url_path = rel_md.removesuffix(".md") + "/"
        llms_index_lines.append(f"- [{title}]({site_url.rstrip('/')}/{url_path})")
        full_sections.append(f"---\n\n<!-- Source: docs/{rel_md} -->\n\n{expanded_md.strip()}\n")

    site_dir.mkdir(parents=True, exist_ok=True)
    llms_txt_path = site_dir / "llms.txt"
    llms_full_path = site_dir / "llms-full.txt"
    llms_txt_path.write_text("\n".join(llms_index_lines).strip() + "\n", encoding="utf-8")
    llms_full_path.write_text("\n".join(full_sections).strip() + "\n", encoding="utf-8")
    return llms_txt_path, llms_full_path


def write_legacy_redirects(site_dir: Path = DEFAULT_SITE_DIR) -> int:
    """Write directory `/index.html` and flat `.html` redirect stubs for all 31 moved legacy URLs.

    Args:
        site_dir: Built documentation site directory.

    Returns:
        Count of redirect files written.
    """
    written = 0
    for old_slug, new_target in sorted(LEGACY_REDIRECTS.items()):
        depth = len(Path(old_slug).parts)
        dir_rel_target = ("../" * depth) + new_target
        dir_stub_path = site_dir / old_slug / "index.html"
        dir_stub_path.parent.mkdir(parents=True, exist_ok=True)
        dir_stub_path.write_text(_render_redirect_html(dir_rel_target, new_target), encoding="utf-8")
        written += 1

        flat_rel = f"{old_slug}.html"
        flat_dir = posixpath.dirname(flat_rel)
        flat_rel_target = posixpath.relpath(new_target, start=flat_dir or ".")
        if new_target.endswith("/") and not flat_rel_target.endswith("/"):
            flat_rel_target += "/"
        flat_stub_path = site_dir / flat_rel
        if not flat_stub_path.exists():
            flat_stub_path.parent.mkdir(parents=True, exist_ok=True)
            flat_stub_path.write_text(_render_redirect_html(flat_rel_target, new_target), encoding="utf-8")
            written += 1
    return written


def run_postbuild(site_dir: Path = DEFAULT_SITE_DIR, docs_dir: Path = DEFAULT_DOCS_DIR) -> None:
    """Execute the full post-build artifact and redirect pipeline.

    Args:
        site_dir: Built documentation site directory.
        docs_dir: Documentation source directory.
    """
    site_dir.mkdir(parents=True, exist_ok=True)
    for excluded_subdir in ("examples", "_build"):
        excluded_path = site_dir / excluded_subdir
        if excluded_path.exists():
            shutil.rmtree(excluded_path)
    compact_site_html(site_dir=site_dir)
    (site_dir / ".nojekyll").write_text("", encoding="utf-8")
    build_objects_inv(site_dir=site_dir, docs_dir=docs_dir)
    build_llms_files(site_dir=site_dir, docs_dir=docs_dir)
    write_legacy_redirects(site_dir=site_dir)


def main(argv: Sequence[str] | None = None) -> int:
    """Run `postbuild.py` from the CLI.

    Args:
        argv: Optional CLI argument sequence.

    Returns:
        Exit code (`0` on success).
    """
    _ = argv
    run_postbuild()
    sys.stdout.write("Postbuild completed: objects.inv, llms.txt, llms-full.txt, .nojekyll, and legacy redirects.\n")
    return 0


def _ast_symbol_roles(package_root: Path) -> dict[str, str]:
    """Scan `litestar_mcp/**/*.py` via AST to map dotted symbols to Sphinx roles without importing extras."""
    roles: dict[str, str] = {"litestar_mcp": "py:module"}
    if not package_root.exists():
        return roles

    for py_path in sorted(package_root.rglob("*.py")):
        rel_parts = list(py_path.relative_to(package_root.parent).with_suffix("").parts)
        mod_name = ".".join(rel_parts[:-1]) if rel_parts[-1] == "__init__" else ".".join(rel_parts)
        roles[mod_name] = "py:module"
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        except SyntaxError:
            continue

        for node in tree.body:
            if isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
                is_exc = any(
                    (isinstance(b, ast.Name) and b.id.endswith(("Error", "Exception")))
                    or (isinstance(b, ast.Attribute) and b.attr.endswith(("Error", "Exception")))
                    for b in node.bases
                ) or node.name.endswith(("Error", "Exception"))
                role = "py:exception" if is_exc else "py:class"
                roles[f"{mod_name}.{node.name}"] = role
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and not child.name.startswith("_"):
                        roles[f"{mod_name}.{node.name}.{child.name}"] = "py:method"
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
                roles[f"{mod_name}.{node.name}"] = "py:function"
    return roles


def _collect_inventory_entries(docs_dir: Path, package_root: Path) -> list[InventoryEntry]:
    """Collect Sphinx inventory entries from default symbols, AST scan, and `docs/reference/*.md` directives."""
    entries: dict[str, InventoryEntry] = {}
    ast_roles = _ast_symbol_roles(package_root)

    for dotted, role, page_uri in DEFAULT_REFERENCE_SYMBOLS:
        entries[dotted] = InventoryEntry(name=dotted, domain_role=role, priority=1, uri=f"{page_uri}#{dotted}")

    for dotted, role in ast_roles.items():
        if dotted not in entries:
            page_uri = _default_page_for_symbol(dotted)
            entries[dotted] = InventoryEntry(name=dotted, domain_role=role, priority=1, uri=f"{page_uri}#{dotted}")

    ref_pages = (
        ("plugin-and-config.md", "reference/plugin-and-config/"),
        ("primitives-and-skills.md", "reference/primitives-and-skills/"),
        ("tasks-and-queues.md", "reference/tasks-and-queues/"),
        ("agents-and-workflows.md", "reference/agents-and-workflows/"),
        ("a2a-and-bridges.md", "reference/a2a-and-bridges/"),
        ("core-and-observability.md", "reference/core-and-observability/"),
    )

    for filename, page_uri in ref_pages:
        page_path = docs_dir / "reference" / filename
        if not page_path.exists():
            continue
        content = page_path.read_text(encoding="utf-8")
        for dotted in DIRECTIVE_RE.findall(content):
            role = ast_roles.get(dotted) or _infer_role_by_name(dotted)
            entries[dotted] = InventoryEntry(name=dotted, domain_role=role, priority=1, uri=f"{page_uri}#{dotted}")
            short_name = dotted.rsplit(".", 1)[-1]
            if short_name in litestar_mcp.__all__:
                root_dotted = f"litestar_mcp.{short_name}"
                entries[root_dotted] = InventoryEntry(
                    name=root_dotted,
                    domain_role=role,
                    priority=1,
                    uri=f"{page_uri}#{dotted}",
                )

    return sorted(entries.values(), key=lambda item: item.name)


def _default_page_for_symbol(dotted: str) -> str:
    """Map a dotted `litestar_mcp` symbol to its default reference page URI."""
    page_uri = "reference/primitives-and-skills/"
    if dotted.startswith("litestar_mcp.queues") or ".tasks" in dotted:
        page_uri = "reference/tasks-and-queues/"
    elif dotted.startswith("litestar_mcp.a2a") or ".bridges" in dotted or ".stdio" in dotted or ".bridge" in dotted:
        page_uri = "reference/a2a-and-bridges/"
    elif dotted.startswith("litestar_mcp.agent"):
        page_uri = "reference/agents-and-workflows/"
    elif dotted.startswith(("litestar_mcp.core", "litestar_mcp.security", "litestar_mcp.utils")):
        page_uri = "reference/core-and-observability/"
    elif any(k in dotted for k in (".config", ".plugin", ".app")):
        page_uri = "reference/plugin-and-config/"
    return page_uri


def _infer_role_by_name(dotted: str) -> str:
    """Infer a Sphinx domain role from a dotted identifier when AST resolution is unavailable."""
    tail = dotted.rsplit(".", 1)[-1]
    if tail.endswith(("Error", "Exception")):
        return "py:exception"
    if tail and tail[0].isupper():
        return "py:class"
    return "py:function"


def _extract_snippet_section(text: str, section: str) -> str:
    """Extract a named `--8<-- [start:section]` or `# start-example` block from a source file."""
    pymdown_re = re.compile(
        rf"#\s*--8<--\s*\[start:{re.escape(section)}\]\s*\n(.*?)#\s*--8<--\s*\[end:{re.escape(section)}\]",
        re.DOTALL,
    )
    match = pymdown_re.search(text)
    if match:
        return match.group(1).rstrip()
    if section == "example":
        marker_re = re.compile(r"#\s*start-example\s*\n(.*?)#\s*end-example", re.DOTALL)
        marker_match = marker_re.search(text)
        if marker_match:
            return marker_match.group(1).rstrip()
    return text.rstrip()


def _expand_snippets(markdown_text: str) -> str:
    """Expand `--8<-- "path[:section]"` snippet directives into inline file contents."""

    def _replace(match: re.Match[str]) -> str:
        indent = match.group(1)
        spec = match.group(2)
        if ":" in spec:
            rel_path, section = spec.split(":", 1)
        else:
            rel_path, section = spec, ""
        target = REPO_ROOT / rel_path
        if not target.exists():
            return f"{indent}[Included file: {rel_path}]"
        if target.suffix == ".html":
            return f"{indent}[Interactive Architecture Diagram: {target.name}]"
        raw_body = target.read_text(encoding="utf-8")
        body = _extract_snippet_section(raw_body, section) if section else raw_body.rstrip()
        return "\n".join(f"{indent}{line}" if line else "" for line in body.splitlines())

    return SNIPPET_INCLUDE_RE.sub(_replace, markdown_text)


def _render_redirect_html(rel_target: str, canonical_target: str) -> str:
    """Render a hash-preserving HTML redirect stub."""
    canonical_url = f"{SITE_URL.rstrip('/')}/{canonical_target.lstrip('/')}"
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        f'  <meta http-equiv="refresh" content="0; url={rel_target}">\n'
        f'  <link rel="canonical" href="{canonical_url}">\n'
        "  <title>Redirecting...</title>\n"
        "  <script>\n"
        f'    window.location.replace("{rel_target}" + (window.location.hash || ""));\n'
        "  </script>\n"
        "</head>\n"
        "<body>\n"
        f'  <p>Redirecting to <a href="{rel_target}">{rel_target}</a>...</p>\n'
        "</body>\n"
        "</html>\n"
    )


if __name__ == "__main__":
    raise SystemExit(main())
