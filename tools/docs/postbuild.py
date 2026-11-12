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
    ("litestar_mcp.SUPPORTED_PROTOCOL_VERSIONS", "py:data", "reference/"),
    ("litestar_mcp.mcp.config.SUPPORTED_PROTOCOL_VERSIONS", "py:data", "reference/plugin-and-config/"),
    ("litestar_mcp.mcp.config.UI_EXTENSION", "py:data", "reference/plugin-and-config/"),
    ("litestar_mcp.mcp.config.UI_MIME_TYPE", "py:data", "reference/plugin-and-config/"),
    ("litestar_mcp.mcp.routes.MCP_SESSION_ID_HEADER", "py:data", "reference/plugin-and-config/"),
    ("litestar_mcp.mcp.service.SESSION_STORE_NAME", "py:data", "reference/plugin-and-config/"),
    ("litestar_mcp.mcp.config.MCPOptKeys.for_field", "py:method", "reference/plugin-and-config/"),
    ("litestar_mcp.mcp.service.MCPSession", "py:class", "reference/plugin-and-config/"),
    ("litestar_mcp.mcp.service.MCPSessionStore", "py:class", "reference/plugin-and-config/"),
    ("litestar_mcp.utils.classify_resource_uri", "py:function", "reference/primitives-and-skills/"),
    ("litestar_mcp.utils.get_all_mcp_metadata", "py:function", "reference/core-and-observability/"),
    ("litestar_mcp.mcp.skills.filter_skills", "py:function", "reference/primitives-and-skills/"),
    ("litestar_mcp.mcp.skills.SkillCatalog.bind_app", "py:method", "reference/primitives-and-skills/"),
    ("litestar_mcp.mcp.skills.SkillCatalog.notify_list_changed", "py:method", "reference/primitives-and-skills/"),
    ("litestar_mcp.mcp.service.MCPRequestContext.log", "py:method", "reference/primitives-and-skills/"),
    (
        "litestar_mcp.mcp.registry.Registry.notify_resources_list_changed",
        "py:method",
        "reference/primitives-and-skills/",
    ),
    ("litestar_mcp.mcp.registry.Registry.notify_skills_list_changed", "py:method", "reference/primitives-and-skills/"),
    ("litestar_mcp.core.schema_builder.typed_dict_to_json_schema", "py:function", "reference/core-and-observability/"),
    ("litestar_mcp.core.schema_builder.unwrap_root_object_schema", "py:function", "reference/core-and-observability/"),
    ("litestar_mcp.core.schema_builder.type_to_output_schema", "py:function", "reference/core-and-observability/"),
    (
        "litestar_mcp.core.schema_builder.generate_output_schema_for_handler",
        "py:function",
        "reference/core-and-observability/",
    ),
    (
        "litestar_mcp.core.schema_builder.cached_output_schema_for_handler",
        "py:function",
        "reference/core-and-observability/",
    ),
    ("litestar_mcp.core.sse.MCPLogLevel", "py:data", "reference/core-and-observability/"),
    ("litestar_mcp.core.sse.MCP_LOG_LEVELS", "py:data", "reference/core-and-observability/"),
    ("litestar_mcp.core.sse.LOG_LEVEL_SEVERITY", "py:data", "reference/core-and-observability/"),
    ("litestar_mcp.core.sse.validate_log_level", "py:function", "reference/core-and-observability/"),
    ("litestar_mcp.core.sse.log_level_allows", "py:function", "reference/core-and-observability/"),
    ("litestar_mcp.core.sse.SubscriptionManager.open_session_stream", "py:method", "reference/core-and-observability/"),
    (
        "litestar_mcp.core.sse.SubscriptionManager.add_resource_subscription",
        "py:method",
        "reference/core-and-observability/",
    ),
    (
        "litestar_mcp.core.sse.SubscriptionManager.remove_resource_subscription",
        "py:method",
        "reference/core-and-observability/",
    ),
    ("litestar_mcp.core.sse.SubscriptionManager.close_session", "py:method", "reference/core-and-observability/"),
    (
        "litestar_mcp.core.sse.SubscriptionManager.set_session_log_level",
        "py:method",
        "reference/core-and-observability/",
    ),
    (
        "litestar_mcp.core.sse.SubscriptionManager.get_session_log_level",
        "py:method",
        "reference/core-and-observability/",
    ),
    (
        "litestar_mcp.core.sse.SubscriptionManager.clear_session_log_level",
        "py:method",
        "reference/core-and-observability/",
    ),
    (
        "litestar_mcp.core.sse.SubscriptionManager.set_default_log_level",
        "py:method",
        "reference/core-and-observability/",
    ),
    ("litestar_mcp.mcp.tasks.MCPTaskStore.update_status_message", "py:method", "reference/tasks-and-queues/"),
    ("litestar_mcp.mcp.client.MCPStreamableHTTPClient.initialize", "py:method", "reference/a2a-and-bridges/"),
    ("litestar_mcp.mcp.client.MCPStreamableHTTPClient.notify", "py:method", "reference/a2a-and-bridges/"),
    ("litestar_mcp.mcp.client.MCPStreamableHTTPClient.open_event_stream", "py:method", "reference/a2a-and-bridges/"),
    ("litestar_mcp.mcp.client.MCPStreamableHTTPClient.subscribe_resource", "py:method", "reference/a2a-and-bridges/"),
    ("litestar_mcp.mcp.client.MCPStreamableHTTPClient.unsubscribe_resource", "py:method", "reference/a2a-and-bridges/"),
    ("litestar_mcp.mcp.client.MCPStreamableHTTPClient.set_logging_level", "py:method", "reference/a2a-and-bridges/"),
    ("litestar_mcp.mcp.client.MCPStreamableHTTPClient.terminate_session", "py:method", "reference/a2a-and-bridges/"),
    ("litestar_mcp.mcp.client.MCPStreamableHTTPClient.list_skills", "py:method", "reference/a2a-and-bridges/"),
    ("litestar_mcp.mcp.client.MCPStreamableHTTPClient.get_skill", "py:method", "reference/a2a-and-bridges/"),
    ("litestar_mcp.mcp.client.MCPStreamableHTTPClient.read_skill_directory", "py:method", "reference/a2a-and-bridges/"),
)


class InventoryEntry(msgspec.Struct, frozen=True, gc=False):
    """Single Sphinx objects.inv v2 entry."""

    name: str
    """Fully qualified Python symbol name."""

    domain_role: str
    """Sphinx domain and role (`py:module`, `py:class`, `py:function`, `py:method`, `py:data`, `py:exception`)."""

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
    entries = _collect_inventory_entries(docs_dir=docs_dir, package_root=package_root, site_dir=site_dir)
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
        "> Model Context Protocol (MCP 2026-07-28, 2025-11-25 & 2025-06-18), Agent Runtime, Durable Workflows, and A2A 1.0 Integration for Litestar.",
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


HTML_ID_RE = re.compile(r'\bid="([^"]+)"')


def _resolve_import_from_module(current_mod: str, is_package_init: bool, node: ast.ImportFrom) -> str | None:
    """Resolve an `ast.ImportFrom` statement to a dotted `litestar_mcp.*` module path."""
    if node.level == 0:
        return node.module if node.module and node.module.startswith("litestar_mcp") else None
    base_parts = current_mod.split(".") if is_package_init else current_mod.split(".")[:-1]
    drop = node.level - 1
    if drop > len(base_parts):
        return None
    prefix = base_parts[: len(base_parts) - drop]
    if node.module:
        prefix.extend(node.module.split("."))
    resolved = ".".join(prefix)
    return resolved if resolved.startswith("litestar_mcp") else None


def _record_imports_from_statements(
    stmts: Sequence[ast.stmt],
    *,
    mod_name: str,
    is_package_init: bool,
    aliases: dict[str, str],
) -> None:
    """Record module-level and `TYPE_CHECKING` re-export aliases into `aliases`."""
    for stmt in stmts:
        if isinstance(stmt, ast.ImportFrom):
            src_mod = _resolve_import_from_module(mod_name, is_package_init, stmt)
            if src_mod is None:
                continue
            for imported in stmt.names:
                if imported.name == "*":
                    continue
                local_name = imported.asname or imported.name
                src_symbol = f"{src_mod}.{imported.name}"
                local_symbol = f"{mod_name}.{local_name}"
                if local_symbol != src_symbol:
                    aliases[local_symbol] = src_symbol
        elif isinstance(stmt, ast.If):
            _record_imports_from_statements(
                stmt.body,
                mod_name=mod_name,
                is_package_init=is_package_init,
                aliases=aliases,
            )


def _ast_symbol_info(package_root: Path) -> tuple[dict[str, str], dict[str, str]]:
    """Scan `litestar_mcp/**/*.py` via AST for Sphinx symbol roles and re-export alias targets."""
    roles: dict[str, str] = {"litestar_mcp": "py:module"}
    aliases: dict[str, str] = {}
    if not package_root.exists():
        return roles, aliases

    for py_path in sorted(package_root.rglob("*.py")):
        rel_parts = list(py_path.relative_to(package_root.parent).with_suffix("").parts)
        is_init = rel_parts[-1] == "__init__"
        mod_name = ".".join(rel_parts[:-1]) if is_init else ".".join(rel_parts)
        roles[mod_name] = "py:module"
        try:
            tree = ast.parse(py_path.read_text(encoding="utf-8"), filename=str(py_path))
        except SyntaxError:
            continue

        _record_imports_from_statements(
            tree.body,
            mod_name=mod_name,
            is_package_init=is_init,
            aliases=aliases,
        )

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
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                if not node.target.id.startswith("_") and node.target.id != "TYPE_CHECKING":
                    roles[f"{mod_name}.{node.target.id}"] = "py:data"
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and not target.id.startswith("_") and target.id != "TYPE_CHECKING":
                        roles[f"{mod_name}.{target.id}"] = "py:data"
    return roles, aliases


def _resolve_alias_target(dotted: str, aliases: dict[str, str], roles: dict[str, str]) -> str:
    """Resolve `dotted` transitively through package re-export aliases to its canonical defining symbol."""
    current = dotted
    seen: set[str] = set()
    while current not in seen:
        seen.add(current)
        direct = aliases.get(current)
        if direct is not None and direct != current:
            if direct in roles and roles[direct] == "py:module" and current in roles and roles[current] != "py:module":
                break
            current = direct
            continue
        if "." in current:
            parent, member = current.rsplit(".", 1)
            parent_target = aliases.get(parent)
            if parent_target is not None and parent_target != parent:
                current = f"{parent_target}.{member}"
                continue
        break
    return current


def _load_built_reference_ids(site_dir: Path | None) -> dict[str, set[str]]:
    """Extract rendered HTML `id` attributes for mkdocstrings-built reference pages under `site_dir`."""
    page_ids: dict[str, set[str]] = {}
    if site_dir is None or not site_dir.exists():
        return page_ids
    candidates = sorted((site_dir / "reference").glob("*/index.html")) if (site_dir / "reference").exists() else []
    root_ref = site_dir / "reference" / "index.html"
    if root_ref.exists():
        candidates.append(root_ref)
    for html_file in candidates:
        html_text = html_file.read_text(encoding="utf-8")
        if 'class="doc doc-object' not in html_text and html_file != root_ref:
            continue
        rel_dir = html_file.parent.relative_to(site_dir).as_posix() + "/"
        page_ids[rel_dir] = set(HTML_ID_RE.findall(html_text))
    return page_ids


def _resolve_symbol_uri(
    dotted: str,
    fallback_page: str,
    *,
    rendered_uris: dict[str, str],
    aliases: dict[str, str],
    roles: dict[str, str],
    page_ids: dict[str, set[str]],
) -> str:
    """Resolve a documentation URI whose `#fragment`, when present, corresponds to a real rendered anchor."""
    uri = fallback_page
    if rendered_uris:
        if dotted in rendered_uris:
            uri = rendered_uris[dotted]
        else:
            canonical = _resolve_alias_target(dotted, aliases, roles)
            if canonical in rendered_uris:
                uri = rendered_uris[canonical]
            elif "." in canonical and canonical.rsplit(".", 1)[0] in rendered_uris:
                uri = rendered_uris[canonical.rsplit(".", 1)[0]]
            else:
                uri = fallback_page

    page, sep, frag = uri.partition("#")
    if sep and page in page_ids and frag not in page_ids[page]:
        parent_frag = frag.rsplit(".", 1)[0] if "." in frag else ""
        if parent_frag and parent_frag in page_ids[page]:
            return f"{page}#{parent_frag}"
        return page
    return uri


def _collect_inventory_entries(
    docs_dir: Path,
    package_root: Path,
    site_dir: Path | None = None,
) -> list[InventoryEntry]:
    """Collect Sphinx inventory entries from default symbols, AST scan, and `docs/reference/*.md` directives."""
    ast_roles, aliases = _ast_symbol_info(package_root)
    page_ids = _load_built_reference_ids(site_dir)

    ref_pages = (
        ("plugin-and-config.md", "reference/plugin-and-config/"),
        ("primitives-and-skills.md", "reference/primitives-and-skills/"),
        ("tasks-and-queues.md", "reference/tasks-and-queues/"),
        ("agents-and-workflows.md", "reference/agents-and-workflows/"),
        ("a2a-and-bridges.md", "reference/a2a-and-bridges/"),
        ("core-and-observability.md", "reference/core-and-observability/"),
    )

    directive_pages: dict[str, str] = {}
    rendered_uris: dict[str, str] = {}
    for filename, page_uri in ref_pages:
        page_path = docs_dir / "reference" / filename
        if not page_path.exists():
            continue
        content = page_path.read_text(encoding="utf-8")
        for dotted in DIRECTIVE_RE.findall(content):
            directive_pages[dotted] = page_uri
            rendered_uris[dotted] = f"{page_uri}#{dotted}"

    class_prefixes = {
        dotted: page_uri
        for dotted, page_uri in directive_pages.items()
        if (ast_roles.get(dotted) or _infer_role_by_name(dotted)) in {"py:class", "py:exception"}
    }
    for dotted, role in ast_roles.items():
        if role == "py:method" and "." in dotted:
            owner = dotted.rsplit(".", 1)[0]
            if owner in class_prefixes:
                rendered_uris[dotted] = f"{class_prefixes[owner]}#{dotted}"

    entries: dict[str, InventoryEntry] = {}

    for dotted, role, page_uri in DEFAULT_REFERENCE_SYMBOLS:
        resolved_uri = _resolve_symbol_uri(
            dotted,
            page_uri,
            rendered_uris=rendered_uris,
            aliases=aliases,
            roles=ast_roles,
            page_ids=page_ids,
        )
        entries[dotted] = InventoryEntry(name=dotted, domain_role=role, priority=1, uri=resolved_uri)

    for dotted, role in ast_roles.items():
        if dotted not in entries:
            fallback_page = _default_page_for_symbol(_resolve_alias_target(dotted, aliases, ast_roles))
            resolved_uri = _resolve_symbol_uri(
                dotted,
                fallback_page,
                rendered_uris=rendered_uris,
                aliases=aliases,
                roles=ast_roles,
                page_ids=page_ids,
            )
            entries[dotted] = InventoryEntry(name=dotted, domain_role=role, priority=1, uri=resolved_uri)

    for dotted, page_uri in directive_pages.items():
        role = ast_roles.get(dotted) or _infer_role_by_name(dotted)
        resolved_uri = _resolve_symbol_uri(
            dotted,
            page_uri,
            rendered_uris=rendered_uris,
            aliases=aliases,
            roles=ast_roles,
            page_ids=page_ids,
        )
        entries[dotted] = InventoryEntry(name=dotted, domain_role=role, priority=1, uri=resolved_uri)
        short_name = dotted.rsplit(".", 1)[-1]
        if short_name in litestar_mcp.__all__:
            root_dotted = f"litestar_mcp.{short_name}"
            entries[root_dotted] = InventoryEntry(
                name=root_dotted,
                domain_role=role,
                priority=1,
                uri=resolved_uri,
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
