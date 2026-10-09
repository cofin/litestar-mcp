from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from litestar_mcp.agent.tools import Tool, tool

if TYPE_CHECKING:
    from collections.abc import Sequence

    from litestar_mcp.agent.models import ModelClient


@dataclass(slots=True)
class AgentMessage:
    """Canonical conversation message representation across heterogeneous models."""

    role: str
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    agent_name: str | None = None

    @classmethod
    def user(cls, content: str) -> AgentMessage:
        """Create a user role agent message."""
        return cls(role="user", content=content)

    @classmethod
    def assistant(
        cls,
        content: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        agent_name: str | None = None,
    ) -> AgentMessage:
        """Create an assistant role agent message."""
        return cls(
            role="assistant",
            content=content,
            tool_calls=tool_calls or [],
            agent_name=agent_name,
        )

    @classmethod
    def system(cls, content: str) -> AgentMessage:
        """Create a system role agent message."""
        return cls(role="system", content=content)

    def to_dict(self) -> dict[str, Any]:
        """Convert message to dictionary representation."""
        data: dict[str, Any] = {
            "role": self.role,
            "content": self.content,
        }
        if self.tool_calls:
            data["tool_calls"] = self.tool_calls
        if self.tool_results:
            data["tool_results"] = self.tool_results
        if self.agent_name:
            data["agent_name"] = self.agent_name
        return data


@dataclass(slots=True)
class Agent:
    """An autonomous agent specification with instructions, tools, skills, and model binding."""

    name: str
    description: str = ""
    instructions: str = ""
    tools: list[Any] = field(default_factory=list)
    skills: list[Any] = field(default_factory=list)
    model: ModelClient | str | None = None

    def get_all_tools(self) -> list[Tool]:
        """Collect and normalize all tools from direct list and attached skills."""
        collected: list[Tool] = []
        for t in self.tools:
            if isinstance(t, Tool):
                collected.append(t)
            elif hasattr(t, "__tool_instance__") and isinstance(t.__tool_instance__, Tool):
                collected.append(t.__tool_instance__)
            elif callable(t):
                collected.append(tool()(t))

        for s in self.skills:
            inst = s() if isinstance(s, type) else s
            if hasattr(inst, "get_tools"):
                for st in inst.get_tools():
                    if isinstance(st, Tool):
                        collected.append(st)
                    elif hasattr(st, "__tool_instance__") and isinstance(st.__tool_instance__, Tool):
                        collected.append(st.__tool_instance__)
                    elif callable(st):
                        collected.append(tool()(st))
        return collected

    def get_combined_instructions(self) -> str:
        """Combine agent-level instructions with grounding rules from attached skills."""
        parts: list[str] = []
        if self.instructions:
            parts.append(self.instructions)
        for s in self.skills:
            inst = s() if isinstance(s, type) else s
            if hasattr(inst, "get_instructions"):
                skill_rules = inst.get_instructions()
                if skill_rules:
                    parts.append(skill_rules)
        return "\n\n".join(parts)


class AgentGroup:
    """Hierarchical multi-agent group coordinating specialist agents.

    Automatically synthesizes a transfer_to_agent tool for the coordinator,
    routing turns to specialists based on their declared domain capabilities.
    """

    def __init__(self, coordinator: Agent, specialists: Sequence[Agent] = ()) -> None:
        self.coordinator = coordinator
        self.specialists: dict[str, Agent] = {s.name: s for s in specialists}
        self._inject_transfer_tool()

    def _inject_transfer_tool(self) -> None:
        """Synthesize and attach transfer_to_agent tool to coordinator."""
        if not self.specialists:
            return

        specialist_names = list(self.specialists.keys())

        def transfer_to_agent(target_agent: str, reason: str = "") -> dict[str, Any]:
            if target_agent not in self.specialists:
                valid = ", ".join(self.specialists.keys())
                return {"status": "error", "message": f"Unknown agent '{target_agent}'. Valid agents: {valid}"}
            return {
                "status": "transferred",
                "target_agent": target_agent,
                "reason": reason,
            }

        transfer_tool = tool(
            name="transfer_to_agent",
            description=f"Transfer control to a specialist agent. Available agents: {', '.join(specialist_names)}",
        )(transfer_to_agent)

        transfer_tool.parameters["properties"]["target_agent"]["enum"] = specialist_names

        existing_names = [getattr(t, "name", getattr(t, "__name__", None)) for t in self.coordinator.tools]
        if "transfer_to_agent" not in existing_names:
            self.coordinator.tools.append(transfer_tool)

    def get_agent(self, name: str) -> Agent:
        """Retrieve an agent by name from coordinator or specialist roster."""
        if name == self.coordinator.name:
            return self.coordinator
        if name in self.specialists:
            return self.specialists[name]
        msg = f"Agent '{name}' not found in group"
        raise KeyError(msg)
