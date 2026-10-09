"""Model Context Protocol plugin, transports, and CLI."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litestar_mcp.mcp.prompt_controller import PromptController, PromptMessage, prompt
    from litestar_mcp.mcp.skill_controller import SkillController

__all__ = (
    "PromptController",
    "PromptMessage",
    "SkillController",
    "prompt",
)


def __getattr__(name: str) -> Any:
    if name in {"PromptController", "PromptMessage", "prompt"}:
        from litestar_mcp.mcp import prompt_controller

        return getattr(prompt_controller, name)
    if name == "SkillController":
        from litestar_mcp.mcp.skill_controller import SkillController

        return SkillController
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
