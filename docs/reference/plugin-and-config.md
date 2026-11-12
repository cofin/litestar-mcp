---
title: Plugin & Configuration API
---

# Plugin & Configuration API

API reference for the `LitestarMCP` plugin, the standalone `MCP` application wrapper, `MCPConfig` (including `auto_output_schema`, `filter_app_only_tools`, `ui_mime_types`, `enable_get_sse`, `session_ttl_seconds`, `logging`, `default_log_level`, and `subscription_authorizer`) and its nested configuration dataclasses (`MCPOptKeys`, `MCPTaskConfig`, `MCPSkillsConfig` with `expose_as_prompts` and `list_changed`), protocol and session constants (`SUPPORTED_PROTOCOL_VERSIONS`, `UI_EXTENSION`, `UI_MIME_TYPE`, `MCP_SESSION_ID_HEADER`, `SESSION_STORE_NAME`), tool-call lifecycle hook protocols, the Streamable HTTP `MCPController`, `MCPHandlerService`, `MCPSession`, and `MCPSessionStore`. For the discovery `Registry`, see [Primitives, Controllers & Skills API](primitives-and-skills.md).

For architectural guidance and configuration examples, see [Configuration & Route Filtering](../learn/configuration.md) and [Standalone MCP Applications](../getting-started/standalone.md).

## Plugin & Standalone Application

::: litestar_mcp.mcp.plugin.LitestarMCP

::: litestar_mcp.mcp.app.MCP

## Configuration Models

::: litestar_mcp.mcp.config.MCPConfig

::: litestar_mcp.mcp.config.MCPOptKeys

::: litestar_mcp.mcp.config.MCPTaskConfig

::: litestar_mcp.mcp.config.MCPSkillsConfig

## Tool-Call Lifecycle Hooks

::: litestar_mcp.mcp.config.BeforeToolCallHook

::: litestar_mcp.mcp.config.AfterToolCallHook

## Streamable HTTP Transport & Handler Service

::: litestar_mcp.mcp.routes.MCPController

::: litestar_mcp.mcp.service.MCPHandlerService

::: litestar_mcp.mcp.service.MCPSession

::: litestar_mcp.mcp.service.MCPSessionStore

## Protocol & Session Constants

::: litestar_mcp.mcp.config.SUPPORTED_PROTOCOL_VERSIONS

::: litestar_mcp.mcp.config.UI_EXTENSION

::: litestar_mcp.mcp.config.UI_MIME_TYPE

::: litestar_mcp.mcp.routes.MCP_SESSION_ID_HEADER

::: litestar_mcp.mcp.service.SESSION_STORE_NAME
