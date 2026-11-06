"""Protocol-agnostic primitives shared by the MCP and A2A integrations."""

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from litestar_mcp.core.context import ToolContext as ToolContext
    from litestar_mcp.core.observability import (
        SpanManager as SpanManager,
    )
    from litestar_mcp.core.observability import (
        TelemetryConfig as TelemetryConfig,
    )
    from litestar_mcp.core.serialization import (
        decode_json as decode_json,
    )
    from litestar_mcp.core.serialization import (
        encode_json as encode_json,
    )
    from litestar_mcp.core.serialization import (
        from_json as from_json,
    )
    from litestar_mcp.core.serialization import (
        to_json as to_json,
    )
    from litestar_mcp.core.tools import (
        Tool as Tool,
    )
    from litestar_mcp.core.tools import (
        ToolCall as ToolCall,
    )
    from litestar_mcp.core.tools import (
        ToolResult as ToolResult,
    )
    from litestar_mcp.core.tools import (
        tool as tool,
    )

__all__ = (
    "SpanManager",
    "TelemetryConfig",
    "Tool",
    "ToolCall",
    "ToolContext",
    "ToolResult",
    "decode_json",
    "encode_json",
    "from_json",
    "to_json",
    "tool",
)

_MODULE_MAP: dict[str, str] = {
    "SpanManager": "litestar_mcp.core.observability",
    "TelemetryConfig": "litestar_mcp.core.observability",
    "Tool": "litestar_mcp.core.tools",
    "ToolCall": "litestar_mcp.core.tools",
    "ToolContext": "litestar_mcp.core.context",
    "ToolResult": "litestar_mcp.core.tools",
    "decode_json": "litestar_mcp.core.serialization",
    "encode_json": "litestar_mcp.core.serialization",
    "from_json": "litestar_mcp.core.serialization",
    "to_json": "litestar_mcp.core.serialization",
    "tool": "litestar_mcp.core.tools",
}


def __getattr__(name: str) -> Any:
    """Lazily import core attributes on demand."""
    if mod_path := _MODULE_MAP.get(name):
        mod = importlib.import_module(mod_path)
        return getattr(mod, name)

    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> list[str]:
    """Return available module attributes including lazy exports."""
    return sorted({*globals(), *__all__})
