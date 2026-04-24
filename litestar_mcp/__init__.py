"""Litestar Model Context Protocol Integration Plugin.

A lightweight plugin that exposes Litestar routes as MCP tools and resources
via JSON-RPC 2.0 over Streamable HTTP. Mark a route handler by passing
``mcp_tool="name"`` or ``mcp_resource="name"`` directly to the Litestar
decorator — Litestar funnels unknown kwargs into ``handler.opt`` automatically,
so no ``opt={...}`` wrapper or ``@mcp_tool`` / ``@mcp_resource`` second
decorator is needed. The stacked decorator form is retained for parity (useful
when you need ``output_schema`` / ``annotations`` / ``scopes`` /
``task_support``) but the kwarg form is the recommended approach.
"""

from litestar_mcp.__metadata__ import __version__
from litestar_mcp.auth import (
    DefaultJWKSCache,
    JWKSCache,
    MCPAuthBackend,
    MCPAuthConfig,
    OIDCProviderConfig,
    TokenValidator,
    create_oidc_validator,
)
from litestar_mcp.config import MCPConfig, MCPOptKeys
from litestar_mcp.plugin import LitestarMCP
from litestar_mcp.routes import MCPController
from litestar_mcp.utils import mcp_prompt, mcp_resource, mcp_tool

__all__ = (
    "DefaultJWKSCache",
    "JWKSCache",
    "LitestarMCP",
    "MCPAuthBackend",
    "MCPAuthConfig",
    "MCPConfig",
    "MCPController",
    "MCPOptKeys",
    "OIDCProviderConfig",
    "TokenValidator",
    "__version__",
    "create_oidc_validator",
    "mcp_prompt",
    "mcp_resource",
    "mcp_tool",
)
