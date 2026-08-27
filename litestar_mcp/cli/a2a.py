"""CLI commands for A2A plugin integration."""

from litestar.cli._utils import LitestarGroup
from rich.console import Console
from rich.table import Table

try:
    import rich_click as click
except ImportError:
    import click  # type: ignore[no-redef]

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

from litestar import Litestar

from litestar_mcp.a2a.manifest import build_agent_card

if TYPE_CHECKING:
    from litestar_mcp.a2a.plugin import A2APlugin


def get_a2a_plugin(app: Litestar) -> "A2APlugin":
    """Retrieve the A2APlugin from the Litestar application."""
    from contextlib import suppress

    from litestar_mcp.a2a.plugin import A2APlugin

    with suppress(KeyError):
        return app.plugins.get(A2APlugin)
    msg = "Failed to initialize A2A commands. The required A2APlugin is missing."
    raise RuntimeError(msg)


@click.group(cls=LitestarGroup, name="a2a")
def a2a_group() -> None:
    """Manage A2A agents and skills."""


_command: Callable[..., Callable[[Callable[..., Any]], Callable[..., Any]]] = cast("Any", a2a_group.command)


@_command(name="list-skills")
def list_skills(app: Litestar) -> None:
    """List all registered A2A skills."""
    plugin = get_a2a_plugin(app)
    console = Console()
    table = Table(title=f"A2A Skills ({plugin.config.name})")
    table.add_column("ID", style="cyan")
    table.add_column("Name", style="green")
    table.add_column("Description", style="white")
    table.add_column("Tags", style="magenta")

    for reg in plugin.registry.skills:
        table.add_row(
            reg.id,
            reg.name,
            reg.description or "",
            ", ".join(reg.tags) if reg.tags else "",
        )
    console.print(table)


@_command(name="card")
@click.option("--json", "as_json", is_flag=True, help="Output full Agent Card in JSON format.")
def card(app: Litestar, as_json: bool = False) -> None:
    """Inspect the dynamic A2A Agent Card."""
    from litestar.serialization import encode_json
    from rich.json import JSON

    plugin = get_a2a_plugin(app)
    console = Console()
    agent_card = build_agent_card(
        app=app,
        registry=plugin.registry,
        name=plugin.config.name,
        description=plugin.config.description,
        version=plugin.config.version,
        base_url=plugin.config.base_url,
        auto_export_mcp_tools=plugin.config.auto_export_mcp_tools,
        capabilities=plugin.config.capabilities,
        provider=plugin.config.provider,
        security_schemes=plugin.config.security_schemes,
        security=plugin.config.security,
        documentation_url=plugin.config.documentation_url,
    )

    if as_json:
        console.print(JSON(encode_json(agent_card).decode("utf-8")))
    else:
        table = Table(title="Agent Card")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Name", agent_card.name)
        table.add_row("Description", agent_card.description or "None")
        table.add_row("Version", agent_card.version)
        table.add_row("Protocol Version", agent_card.protocol_version)
        table.add_row("URL", agent_card.url or "Dynamic")
        table.add_row("Skills Count", str(len(agent_card.skills)))
        console.print(table)
