"""Model Context Protocol plugin, transports, and CLI."""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litestar_mcp.mcp.content import PromptMessage
    from litestar_mcp.mcp.prompt_controller import PromptController, prompt
    from litestar_mcp.mcp.skill_controller import SkillController

__all__ = (
    "PromptController",
    "PromptMessage",
    "SkillController",
    "prompt",
)

_EXPORT_MODULE_MAP = {
    "PromptMessage": "litestar_mcp.mcp.content",
    "PromptController": "litestar_mcp.mcp.prompt_controller",
    "prompt": "litestar_mcp.mcp.prompt_controller",
    "SkillController": "litestar_mcp.mcp.skill_controller",
}


def __getattr__(name: str) -> Any:
    module_path = _EXPORT_MODULE_MAP.get(name)
    if module_path is not None:
        module = importlib.import_module(module_path)
        return getattr(module, name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)
