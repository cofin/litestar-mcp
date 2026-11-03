---
title: A2A 1.0, Protocol Bridges & Transports API
---

# A2A 1.0, Protocol Bridges & Transports API

API reference for the Agent-to-Agent (A2A 1.0) protocol plugin (`A2AConfig`, `LitestarA2A`), bidirectional MCP and A2A protocol bridges (`agent_to_mcp`, `mcp_to_tools`, `discover_mcp_tools`, `agent_to_a2a`), in-process `stdio` serving (`MCPStdioContext`, `ASGIStreamingTransport`, `run_stdio_async`, `run_stdio`), the stdio-to-HTTP proxy bridge (`run_stdio_streamable_http_bridge`, `run_bridge`), and `MCPStreamableHTTPClient`.

For architectural guides, see [Protocol Bridges](../agents/protocol-bridges.md), [A2A 1.0 Protocol & HTTP+JSON Binding](../agents/a2a-protocol.md), [Multi-Tenant A2A Routing](../agents/multi-tenancy.md), and [Stdio, HTTP Bridge & Tool Spec](../learn/stdio-and-bridge.md).

---

## A2A 1.0 Plugin & Configuration (`litestar_mcp.a2a`)

::: litestar_mcp.a2a.config.A2AConfig

::: litestar_mcp.a2a.plugin.LitestarA2A

---

## Bidirectional Protocol Bridges (`litestar_mcp.agent.bridges`)

::: litestar_mcp.agent.bridges.mcp.agent_to_mcp

::: litestar_mcp.agent.bridges.mcp.mcp_to_tools

::: litestar_mcp.agent.bridges.mcp.discover_mcp_tools

::: litestar_mcp.agent.bridges.a2a.agent_to_a2a

---

## In-Process Stdio Transport (`litestar_mcp.mcp.stdio`)

::: litestar_mcp.mcp.stdio.MCPStdioContext

::: litestar_mcp.mcp.stdio.ASGIStreamingTransport

::: litestar_mcp.mcp.stdio.run_stdio_async

::: litestar_mcp.mcp.stdio.run_stdio

---

## Stdio-to-Streamable HTTP Bridge (`litestar_mcp.mcp.bridge`)

::: litestar_mcp.mcp.bridge.run_stdio_streamable_http_bridge

::: litestar_mcp.mcp.bridge.run_bridge

---

## Streamable HTTP Client (`litestar_mcp.mcp.client`)

::: litestar_mcp.mcp.client.MCPStreamableHTTPClient

::: litestar_mcp.mcp.client.MCPClientError
