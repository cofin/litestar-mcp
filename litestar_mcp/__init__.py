"""Litestar Model Context Protocol Integration Plugin.

A lightweight plugin that exposes Litestar routes as MCP tools, resources,
and prompts via JSON-RPC 2.0 over Streamable HTTP. Mark a route handler by
passing ``mcp_tool="name"``, ``mcp_resource="name"``, or
``mcp_prompt="name"`` directly to the Litestar decorator — Litestar funnels
unknown kwargs into ``handler.opt`` automatically, so no ``opt={...}``
wrapper or ``@mcp_tool`` / ``@mcp_resource`` / ``@mcp_prompt`` second
decorator is needed. The stacked decorator form is retained for parity
(useful when you need ``output_schema`` / ``annotations`` / ``scopes`` /
``task_support``) but the kwarg form is the recommended approach. Standalone
prompts not bound to a route handler can also be registered via
``LitestarMCP(prompts=[...])`` after decoration with ``@mcp_prompt``.
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
