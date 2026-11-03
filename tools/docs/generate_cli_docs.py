"""Deterministic Click CLI reference generator for litestar mcp."""

import argparse
import inspect
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import click
import msgspec

from litestar_mcp.mcp.cli import mcp_group

__all__ = [
    "DEFAULT_OUTPUT",
    "CliParamDoc",
    "main",
    "render_cli_markdown",
    "render_cli_reference",
    "write_cli_docs",
]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT = REPO_ROOT / "tools" / "docs" / "generated" / "cli-reference.md"
RICH_TAG_RE = re.compile(r"\[/?[a-zA-Z0-9_#\s.-]+\]")

COMMAND_CATEGORIES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Transports & Proxies",
        "Commands for serving the Litestar application over in-process stdio or proxying stdio JSON-RPC frames to a remote Streamable HTTP endpoint.",
        ("stdio", "bridge"),
    ),
    (
        "Specification & Schema Export",
        "Commands for exporting deterministic MCP tool manifests and validating description length bounds in CI pipelines.",
        ("export-tool-spec",),
    ),
    (
        "Discovery & Interactive Execution",
        "Commands for inspecting discovered MCP tools and resources or invoking a tool directly from the terminal.",
        ("list-tools", "list-resources", "run"),
    ),
)


class CliParamDoc(msgspec.Struct, frozen=True, gc=False):
    """Extracted documentation for a single Click option or argument."""

    flags: str
    """Formatted CLI option flags or positional argument name."""

    type_name: str
    """Formatted parameter type label."""

    default: str
    """Formatted default value or required marker."""

    description: str
    """Cleaned single-line parameter help text."""

    is_argument: bool = False
    """Whether the parameter is a positional argument rather than an option."""


def render_cli_markdown() -> str:
    """Introspect `litestar_mcp.mcp.cli.mcp_group` and render deterministic Markdown.

    Returns:
        Deterministic Markdown document covering all `litestar mcp` subcommands.
    """
    lines: list[str] = [
        "All commands run under the Litestar CLI `mcp` group:",
        "",
        "```bash",
        "litestar --app app:app mcp <command> [OPTIONS]",
        "```",
        "",
        "| Command | Category | Summary |",
        "| --- | --- | --- |",
    ]

    grouped_names: set[str] = set()
    for category_title, _, cmd_names in COMMAND_CATEGORIES:
        for cmd_name in cmd_names:
            cmd = mcp_group.commands.get(cmd_name)
            if cmd is None:
                continue
            grouped_names.add(cmd_name)
            summary = _first_sentence(_clean_help(cmd.help or cmd.short_help or ""))
            lines.append(f"| [`litestar mcp {cmd_name}`](#litestar-mcp-{cmd_name}) | {category_title} | {summary} |")

    for cmd_name, cmd in sorted(mcp_group.commands.items()):
        if cmd_name not in grouped_names:
            summary = _first_sentence(_clean_help(cmd.help or cmd.short_help or ""))
            lines.append(f"| [`litestar mcp {cmd_name}`](#litestar-mcp-{cmd_name}) | Additional | {summary} |")

    lines.append("")

    for category_title, category_desc, cmd_names in COMMAND_CATEGORIES:
        lines.extend([f"## {category_title}", "", category_desc, ""])
        for cmd_name in cmd_names:
            cmd = mcp_group.commands.get(cmd_name)
            if cmd is None:
                continue
            lines.extend(_render_command_section(cmd_name, cmd))

    remaining = [name for name in sorted(mcp_group.commands) if name not in grouped_names]
    if remaining:
        lines.extend(["## Additional Commands", ""])
        for cmd_name in remaining:
            lines.extend(_render_command_section(cmd_name, mcp_group.commands[cmd_name]))

    return "\n".join(lines).rstrip() + "\n"


def render_cli_reference() -> str:
    """Alias for `render_cli_markdown`."""
    return render_cli_markdown()


def write_cli_docs(output_path: Path = DEFAULT_OUTPUT) -> Path:
    """Render and write the CLI reference Markdown file to disk.

    Args:
        output_path: Destination file path.

    Returns:
        Path to the written Markdown file.
    """
    rendered = render_cli_markdown()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return output_path


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for generating or checking `tools/docs/generated/cli-reference.md`.

    Args:
        argv: Optional CLI argument sequence.

    Returns:
        Exit code (`0` on success, `1` when `--check` detects drift).
    """
    parser = argparse.ArgumentParser(description="Generate or verify tools/docs/generated/cli-reference.md.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output Markdown path.")
    parser.add_argument("--check", action="store_true", help="Verify output matches on-disk file without writing.")
    args = parser.parse_args(list(argv) if argv is not None else None)

    rendered = render_cli_markdown()
    output_path: Path = args.output
    if args.check:
        if not output_path.exists():
            sys.stderr.write(f"Missing generated CLI reference: {output_path}\n")
            return 1
        current = output_path.read_text(encoding="utf-8")
        if current != rendered:
            sys.stderr.write(
                f"Out-of-date CLI reference at {output_path}. Run `uv run python tools/docs/generate_cli_docs.py`.\n"
            )
            return 1
        return 0

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return 0


def _clean_help(text: str) -> str:
    """Strip Rich markup tags and normalize docstring indentation."""
    cleaned = inspect.cleandoc(text)
    return RICH_TAG_RE.sub("", cleaned).strip()


def _first_sentence(text: str) -> str:
    """Extract the first paragraph from cleaned help text."""
    if not text:
        return ""
    first_para = text.split("\n\n", maxsplit=1)[0].replace("\n", " ").strip()
    return first_para.replace("|", "/")


def _is_unset_default(value: Any) -> bool:
    """Return True if a Click parameter default is None or a Click Sentinel."""
    if value is None:
        return True
    type_name = type(value).__name__
    repr_str = repr(value)
    return "Sentinel" in type_name or "UNSET" in repr_str


def _format_default(param: click.Parameter) -> str:
    """Format a Click parameter default value cleanly without Sentinel leaks."""
    if getattr(param, "required", False):
        return "*required*"
    default = param.default
    if _is_unset_default(default):
        return "`False`" if isinstance(param, click.Option) and param.is_flag else "`None`"
    if isinstance(default, (bool, int, float)):
        return f"`{default}`"
    if isinstance(default, (str, Path)):
        text = str(default)
        return f'`"{text}"`' if text else '`""`'
    if isinstance(default, (list, tuple)) and not default:
        return "`()`"
    return f"`{default!r}`"


def _format_param_type(param: click.Parameter) -> str:
    """Format a Click parameter type signature for Markdown tables."""
    if isinstance(param, click.Option) and param.is_flag:
        return "`bool`"
    param_type = param.type
    if isinstance(param_type, click.Choice):
        return "`Choice`"
    if isinstance(param_type, click.Path):
        return "`Path`"
    name = getattr(param_type, "name", None) or type(param_type).__name__.lower()
    return f"`{name}`"


def _extract_params(cmd: click.Command) -> tuple[list[CliParamDoc], list[CliParamDoc]]:
    """Extract positional arguments and options from a Click command."""
    arguments: list[CliParamDoc] = []
    options: list[CliParamDoc] = []
    for param in cmd.params:
        if isinstance(param, click.Argument):
            arg_name = param.human_readable_name or param.name or "ARG"
            arguments.append(
                CliParamDoc(
                    flags=f"`{arg_name}`",
                    type_name=_format_param_type(param),
                    default=_format_default(param),
                    description="Positional command argument.",
                    is_argument=True,
                )
            )
        elif isinstance(param, click.Option):
            if param.hidden:
                continue
            opts = [*param.opts, *param.secondary_opts]
            flags = ", ".join(f"`{opt}`" for opt in opts)
            desc = _clean_help(param.help or "").replace("\n", " ").replace("|", "/")
            if isinstance(param.type, click.Choice):
                choice_list = ", ".join(f"`{c}`" for c in param.type.choices)
                desc = f"{desc} (Choices: {choice_list})" if desc else f"Choices: {choice_list}"
            options.append(
                CliParamDoc(
                    flags=flags,
                    type_name=_format_param_type(param),
                    default=_format_default(param),
                    description=desc or "Command option.",
                )
            )
    return arguments, options


def _build_usage_line(cmd_name: str, cmd: click.Command) -> str:
    """Construct the canonical usage synopsis for a subcommand."""
    parts = ["litestar", "mcp", cmd_name]
    has_options = any(isinstance(p, click.Option) and not p.hidden for p in cmd.params)
    if has_options:
        parts.append("[OPTIONS]")
    for param in cmd.params:
        if isinstance(param, click.Argument):
            label = param.human_readable_name or (param.name or "ARG").upper()
            if param.nargs == -1:
                parts.append(f"[{label}]...")
            elif param.required:
                parts.append(label)
            else:
                parts.append(f"[{label}]")
    return " ".join(parts)


def _render_command_section(cmd_name: str, cmd: click.Command) -> list[str]:
    """Render a single Click subcommand section at H3 depth."""
    help_text = _clean_help(cmd.help or cmd.short_help or "")
    arguments, options = _extract_params(cmd)
    lines: list[str] = [f"### `litestar mcp {cmd_name}`", ""]
    if help_text:
        lines.extend([help_text, ""])

    lines.extend(["```bash", _build_usage_line(cmd_name, cmd), "```", ""])

    if arguments:
        lines.extend(["**Arguments**", "", "| Argument | Type | Default | Description |", "| --- | --- | --- | --- |"])
        lines.extend(f"| {arg.flags} | {arg.type_name} | {arg.default} | {arg.description} |" for arg in arguments)
        lines.append("")

    if options:
        lines.extend(["**Options**", "", "| Option | Type | Default | Description |", "| --- | --- | --- | --- |"])
        lines.extend(f"| {opt.flags} | {opt.type_name} | {opt.default} | {opt.description} |" for opt in options)
        lines.append("")

    return lines


if __name__ == "__main__":
    raise SystemExit(main())
