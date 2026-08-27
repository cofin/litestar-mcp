"""Configuration classes and options for Litestar A2A integration."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from litestar_mcp.a2a.types import (
    AgentCapabilities,
    AgentProvider,
    SecurityRequirement,
    SecurityScheme,
)


@dataclass(frozen=True, slots=True)
class A2AOptKeys:
    """Configuration keys used in Litestar route handler `opt` mappings for A2A discovery."""

    skill: str = "a2a_skill"
    description: str = "a2a_description"
    tags: str = "a2a_tags"


@dataclass
class A2AConfig:
    """Configuration for the Litestar A2A Plugin."""

    name: str = "A2A Agent"
    description: str | None = None
    version: str = "1.0.0"
    base_url: str = ""
    base_path: str = "/a2a"
    stream_path: str = "/stream"
    agent_card_path: str = "/.well-known/agent-card.json"
    include_in_schema: bool = True
    register_agent_card: bool = True
    auto_export_mcp_tools: bool = False
    capabilities: AgentCapabilities = field(default_factory=lambda: AgentCapabilities(streaming=True, stateful=True))
    provider: AgentProvider | None = None
    security_schemes: dict[str, SecurityScheme] = field(default_factory=dict)
    security: list[SecurityRequirement] = field(default_factory=list)
    documentation_url: str | None = None
    guards: list[Any] | None = None
    route_opt: Mapping[str, Any] | None = None
    opt_keys: A2AOptKeys = field(default_factory=A2AOptKeys)
