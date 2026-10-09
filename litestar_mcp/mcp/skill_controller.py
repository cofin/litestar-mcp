"""Class-based controller unifying tools, prompts, instructions, and guards into a cohesive skill."""

import inspect
from collections.abc import Sequence
from typing import Any

from litestar_mcp.core.tools import Tool
from litestar_mcp.mcp.prompt_controller import PromptController
from litestar_mcp.mcp.tool_handlers import build_controller_tool_handler

__all__ = ("SkillController",)


class SkillController(PromptController):
    """Prompt controller that also serves @tool methods as MCP tools and exposes them to agents.

    Attributes:
        name: Skill identifier, also the default prompt prefix and A2A skill id.
        description: Skill description for A2A cards.
        tags: Optional categorization tags for discovery.
        examples: Example requests for A2A cards.
    """

    name: str = ""
    description: str = ""
    tags: Sequence[str] | None = None
    examples: tuple[str, ...] = ()

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Attach a controller route handler for every Tool declared on the class."""
        super().__init_subclass__(**kwargs)
        for value in list(vars(cls).values()):
            if isinstance(value, Tool):
                setattr(cls, f"_mcp_tool_{value.name}", build_controller_tool_handler(value))

    def qualify_prompt_name(self, name: str) -> str:
        """Namespace prompts under prefix, falling back to name."""
        namespace = self.prefix or self.name
        return f"{namespace}/{name}" if namespace else name

    def get_tools(self) -> list[Tool]:
        """Return the controller's tools bound to this instance."""
        tools: list[Tool] = []
        seen: set[str] = set()
        for base in type(self).__mro__:
            for name, value in vars(base).items():
                if name not in seen and isinstance(value, Tool):
                    seen.add(name)
                    bound_tool = getattr(self, name)
                    if isinstance(bound_tool, Tool):
                        tools.append(bound_tool)
        return tools

    def get_instructions(self) -> str:
        """Return the skill instructions or an empty string."""
        return self.instructions or ""

    def to_agent_skill(self) -> dict[str, Any]:
        """Project the controller metadata to an A2A AgentSkill dictionary."""
        return {
            "id": self.name or type(self).__name__.lower(),
            "name": self.name or type(self).__name__,
            "description": self.description or inspect.cleandoc(type(self).__doc__ or ""),
            "tags": list(self.tags or ()),
            "examples": list(self.examples or ()),
        }
