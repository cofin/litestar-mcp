---
title: Plugin & Configuration API
---

# Plugin & Configuration API

API reference for the `LitestarMCP` plugin, the standalone `MCP` application wrapper, `MCPConfig` and its nested configuration dataclasses (`MCPOptKeys`, `MCPTaskConfig`, `MCPSkillsConfig`), tool-call lifecycle hook protocols, and the Streamable HTTP `MCPController`.

For architectural guidance and configuration examples, see [Configuration & Route Filtering](../learn/configuration.md) and [Standalone MCP Applications](../getting-started/standalone.md).

---

## Plugin & Standalone Application

::: litestar_mcp.mcp.plugin.LitestarMCP

::: litestar_mcp.mcp.app.MCP

---

## Configuration Models

::: litestar_mcp.mcp.config.MCPConfig

::: litestar_mcp.mcp.config.MCPOptKeys

::: litestar_mcp.mcp.config.MCPTaskConfig

::: litestar_mcp.mcp.config.MCPSkillsConfig

---

## Tool-Call Lifecycle Hooks

::: litestar_mcp.mcp.config.BeforeToolCallHook

::: litestar_mcp.mcp.config.AfterToolCallHook

---

## Streamable HTTP Transport Controller

::: litestar_mcp.mcp.routes.MCPController
