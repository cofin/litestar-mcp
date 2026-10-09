"""Provider-neutral agent specifications and multi-agent group abstractions."""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import msgspec
from litestar.exceptions import ImproperlyConfiguredException

from litestar_mcp.core.tools import Tool, ToolCall, ToolResult, tool

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from litestar_mcp.agent.models import ModelClient
    from litestar_mcp.mcp.skill_controller import SkillController

__all__ = ("TRANSFER_TOOL_NAME", "Agent", "AgentGroup", "AgentMessage")

TRANSFER_TOOL_NAME = "transfer_to_agent"


class AgentMessage(msgspec.Struct, kw_only=True):
    """Provider-neutral conversation message."""

    role: "Literal['system', 'user', 'assistant', 'tool']"
    content: "str" = ""
    tool_calls: "list[ToolCall]" = msgspec.field(default_factory=list)
    tool_results: "list[ToolResult]" = msgspec.field(default_factory=list)
    agent_name: "str | None" = None


@dataclass(slots=True)
class Agent:
    """Agent definition with instructions, tools, skills, and an optional model binding."""

    name: "str"
    description: "str" = ""
    instructions: "str" = ""
    tools: "Sequence[Tool | Callable[..., Any]]" = ()
    skills: "Sequence[SkillController | type[SkillController]]" = ()
    model: "ModelClient | str | None" = None
    _tool_set: "tuple[Tool, ...]" = field(init=False, repr=False, default=())
    _skill_instances: "tuple[SkillController, ...]" = field(init=False, repr=False, default=())

    def __post_init__(self) -> "None":
        """Instantiate skill classes once, normalize tools, and reject duplicate tool names."""
        skill_instances: list[Any] = [s() if isinstance(s, type) else s for s in self.skills]
        self._skill_instances = tuple(skill_instances)

        own_tools: list[Tool] = [t if isinstance(t, Tool) else tool()(t) for t in self.tools]

        all_tools: list[Tool] = list(own_tools)
        for s_inst in self._skill_instances:
            if hasattr(s_inst, "get_tools"):
                all_tools.extend(s_inst.get_tools())

        seen_names: set[str] = set()
        dupes: set[str] = set()
        for t in all_tools:
            if t.name == TRANSFER_TOOL_NAME:
                msg = f"Tool name {TRANSFER_TOOL_NAME!r} is reserved for agent transfer"
                raise ImproperlyConfiguredException(msg)
            if t.name in seen_names:
                dupes.add(t.name)
            seen_names.add(t.name)

        if dupes:
            msg = f"Agent {self.name!r} declares duplicate tool names: {sorted(dupes)}"
            raise ImproperlyConfiguredException(msg)

        self._tool_set = tuple(all_tools)

    @property
    def tool_set(self) -> "tuple[Tool, ...]":
        """Return the agent's own tools followed by skill tools."""
        return self._tool_set

    @property
    def skill_instances(self) -> "tuple[SkillController, ...]":
        """Return the instantiated skill controllers attached to this agent."""
        return self._skill_instances

    @property
    def combined_instructions(self) -> "str":
        """Return agent instructions joined with skill instructions by blank lines."""
        parts: list[str] = []
        if self.instructions:
            parts.append(self.instructions)
        for inst in self._skill_instances:
            if hasattr(inst, "get_instructions"):
                rules = inst.get_instructions()
                if rules:
                    parts.append(rules)
        return "\n\n".join(parts)


class AgentGroup:
    """Coordinator and specialist agents with a group-owned transfer_to_agent tool."""

    def __init__(self, coordinator: "Agent", specialists: "Sequence[Agent]" = ()) -> "None":
        all_agents = [coordinator, *specialists]
        names = [a.name for a in all_agents]
        if len(names) != len(set(names)):
            msg = f"Duplicate agent names in group: {names}"
            raise ImproperlyConfiguredException(msg)
        for s in specialists:
            if s is coordinator:
                msg = f"Specialist {s.name!r} cannot be the coordinator"
                raise ImproperlyConfiguredException(msg)

        self.coordinator = coordinator
        self.specialists: dict[str, Agent] = {s.name: s for s in specialists}
        self._transfer_tool: Tool | None = None

        if self.specialists:
            specialist_names = tuple(self.specialists.keys())
            target_literal = Literal[specialist_names]  # type: ignore[valid-type]

            def transfer_to_agent(target_agent: str, reason: str = "") -> dict[str, Any]:
                return {
                    "status": "transferred",
                    "target_agent": target_agent,
                    "reason": reason,
                }

            transfer_to_agent.__annotations__ = {
                "target_agent": target_literal,
                "reason": str,
                "return": dict[str, Any],
            }

            self._transfer_tool = tool(
                name=TRANSFER_TOOL_NAME,
                description=f"Transfer control to a specialist agent. Available agents: {', '.join(specialist_names)}",
            )(transfer_to_agent)

    def tools_for(self, agent: "Agent") -> "tuple[Tool, ...]":
        """Return tools available to an agent in this group."""
        if agent is self.coordinator and self._transfer_tool is not None:
            return (*agent.tool_set, self._transfer_tool)
        return agent.tool_set

    def get_agent(self, name: "str") -> "Agent":
        """Retrieve an agent by name from coordinator or specialist roster."""
        if name == self.coordinator.name:
            return self.coordinator
        if name in self.specialists:
            return self.specialists[name]
        msg = f"Agent '{name}' not found in group"
        raise KeyError(msg)

    def resolve_transfer(self, call: "ToolCall") -> "Agent | None":
        """Resolve a tool call into an agent transfer target if applicable."""
        if call.name != TRANSFER_TOOL_NAME:
            return None
        target = call.arguments.get("target_agent")
        if isinstance(target, str) and target in self.specialists:
            return self.specialists[target]
        return None
