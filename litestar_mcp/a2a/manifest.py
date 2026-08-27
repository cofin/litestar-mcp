"""Dynamic Agent Card manifest generation conforming to A2A v1.0."""

from typing import TYPE_CHECKING, Any

from litestar_mcp.a2a.registry import A2ARegistry, _extract_docstring_summary
from litestar_mcp.a2a.types import (
    AgentCapabilities,
    AgentCard,
    AgentProvider,
    AgentSkill,
    SecurityRequirement,
    SecurityScheme,
)
from litestar_mcp.core import generate_schema_for_handler

if TYPE_CHECKING:
    from litestar import Litestar


def build_agent_card(
    app: "Litestar",
    registry: A2ARegistry,
    *,
    name: str = "A2A Agent",
    description: str | None = None,
    version: str = "1.0.0",
    base_url: str = "",
    auto_export_mcp_tools: bool = False,
    capabilities: AgentCapabilities | None = None,
    provider: AgentProvider | None = None,
    security_schemes: dict[str, SecurityScheme] | None = None,
    security: list[SecurityRequirement] | None = None,
    documentation_url: str | None = None,
) -> AgentCard:
    """Dynamically construct an AgentCard metadata structure."""
    skills: list[AgentSkill] = [reg.to_agent_skill() for reg in registry.skills]

    if auto_export_mcp_tools:
        mcp_registry = getattr(getattr(app, "state", None), "mcp_registry", None)
        if mcp_registry is None and hasattr(app, "plugins"):
            for plugin in app.plugins:
                reg = getattr(plugin, "_registry", None)
                if reg is not None and hasattr(reg, "tools"):
                    mcp_registry = reg
                    break

        if mcp_registry is not None and hasattr(mcp_registry, "tools"):
            existing_skill_ids = {s.id for s in skills}
            for tool_name, handler in mcp_registry.tools.items():
                if tool_name not in existing_skill_ids:
                    schema: dict[str, Any] = {}
                    doc: str | None = None
                    if handler is not None:
                        schema = generate_schema_for_handler(handler)
                        doc = getattr(getattr(handler, "fn", handler), "__doc__", None)
                    summary = _extract_docstring_summary(doc)
                    skills.append(
                        AgentSkill(
                            id=tool_name,
                            name=tool_name,
                            description=summary or tool_name,
                            input_schema=schema,
                            tags=["mcp"],
                        )
                    )

    return AgentCard(
        name=name,
        description=description,
        version=version,
        protocol_version="1.0",
        url=base_url,
        capabilities=capabilities or AgentCapabilities(streaming=True, stateful=True),
        skills=skills,
        security_schemes=security_schemes or {},
        security=security or [],
        provider=provider,
        documentation_url=documentation_url,
    )
