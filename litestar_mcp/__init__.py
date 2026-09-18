"""Litestar Model Context Protocol Integration Plugin.

A lightweight plugin that exposes Litestar routes as MCP tools, resources,
and prompts via JSON-RPC 2.0 over Streamable HTTP. Mark a route handler by
passing ``mcp_tool="name"``, ``mcp_resource="name"``, or
``mcp_prompt="name"`` directly to the Litestar decorator — Litestar funnels
unknown kwargs into ``handler.opt`` automatically, so no ``opt={...}``
wrapper or ``@mcp_tool`` / ``@mcp_resource`` / ``@mcp_prompt`` second
decorator is needed. The stacked decorator form is retained for parity
(useful when you need an explicit ``input_schema`` / ``output_schema``,
``annotations``, ``scopes``, or task/MRTR policy) but the kwarg form is the
recommended approach. Standalone prompts not bound to a route handler can
also be registered via ``LitestarMCP(prompts=[...])`` after decoration with
``@mcp_prompt``. ``A2AConfig`` and ``LitestarA2A`` resolve lazily from
``litestar_mcp.a2a`` so importing ``litestar_mcp`` never imports ``a2a-sdk``.
"""

from typing import Any

from litestar_mcp.__metadata__ import __version__
from litestar_mcp.core.exceptions import (
    BridgeConnectionError,
    BridgeMessageTooLargeError,
    LitestarMCPError,
    MissingDependencyError,
)
from litestar_mcp.mcp.app import MCP
from litestar_mcp.mcp.config import (
    AfterToolCallHook,
    BeforeToolCallHook,
    MCPConfig,
    MCPOptKeys,
    MCPSkillsConfig,
    MCPTaskConfig,
)
from litestar_mcp.mcp.content import MCPBlobResource, MCPInputRequiredResult, MCPResourceLink, MCPToolResult
from litestar_mcp.mcp.plugin import LitestarMCP
from litestar_mcp.mcp.routes import MCPController
from litestar_mcp.mcp.service import MCPRequestContext, get_mcp_request_context
from litestar_mcp.mcp.stdio import MCPStdioContext
from litestar_mcp.utils import mcp_prompt, mcp_resource, mcp_tool

__all__ = (
    "MCP",
    "AfterToolCallHook",
    "BeforeToolCallHook",
    "BridgeConnectionError",
    "BridgeMessageTooLargeError",
    "LitestarMCP",
    "LitestarMCPError",
    "MCPBlobResource",
    "MCPConfig",
    "MCPController",
    "MCPInputRequiredResult",
    "MCPOptKeys",
    "MCPRequestContext",
    "MCPResourceLink",
    "MCPSkillsConfig",
    "MCPStdioContext",
    "MCPTaskConfig",
    "MCPToolResult",
    "MissingDependencyError",
    "__version__",
    "get_mcp_request_context",
    "mcp_prompt",
    "mcp_resource",
    "mcp_tool",
)

_A2A_EXPORTS = frozenset({"A2AConfig", "LitestarA2A"})


def __getattr__(name: "str") -> "Any":
    if name in _A2A_EXPORTS:
        from litestar_mcp import a2a

        return getattr(a2a, name)
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


def __dir__() -> "list[str]":
    return sorted({*globals(), *_A2A_EXPORTS})
