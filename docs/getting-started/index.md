---
title: Getting Started
---

# Getting Started with Litestar MCP

`litestar-mcp` provides three composable entry points depending on whether you are augmenting an existing Litestar API, authoring a standalone MCP server, or orchestrating multi-agent workflows and A2A 1.0 services.

---

## Entry-Point Decision Matrix

| Entry Point | Primary Class | Best For | Key Capabilities |
| :--- | :--- | :--- | :--- |
| **Litestar Plugin** | `LitestarMCP(MCPConfig(...))` | Existing Litestar applications and REST APIs | Exposes marked routes (`mcp_tool`, `mcp_resource`, `mcp_prompt`), opt-in `outputSchema` and automatic `structuredContent`, `SEP-1865` MCP Apps (`io.modelcontextprotocol/ui`), `PromptController`, `SkillController`, and `SkillCatalog` (`MCPSkillsConfig`) over dual-mode `/mcp` (`2026-07-28` and `2025-11-25` with `Mcp-Session-Id` and `GET /mcp` SSE) and `litestar mcp stdio` while sharing middleware, guards, and DI |
| **Standalone Wrapper** | `MCP("server-name")` | Dedicated MCP servers and local stdio utilities | Declarative `@mcp.tool(output_schema=...)`, `@mcp.resource("ui://...")` (`io.modelcontextprotocol/ui`), and `@mcp.prompt()` decorators serving `2026-07-28` and `2025-11-25` via `.run(transport="streamable-http")` or `.run(transport="stdio")` with full access to `.app` |
| **Agent & A2A Runtime** | `AgentRuntime` & `LitestarA2A` | Multi-agent orchestration, durable DAGs, and A2A 1.0 endpoints | `Agent`, `AgentGroup`, `TurnBudget`, `GoogleGenAIClient` (`gemini-3.8-flash`), `DurableWorkflowEngine`, visibility-aware `mcp_to_tools`, and A2A 1.0 JSON-RPC + `HTTP+JSON` REST routes |

---

## Quick Comparison

=== "LitestarMCP Plugin"

    ```python title="docs/examples/snippets/configuration_minimal.py"
    --8<-- "docs/examples/snippets/configuration_minimal.py:example"
    ```

=== "Standalone MCP Wrapper"

    ```python title="docs/examples/snippets/standalone_minimal.py"
    --8<-- "docs/examples/snippets/standalone_minimal.py:example"
    ```

---

## Guided Learning Tracks

<div class="lm-grid">
  <div class="lm-card">
    <div>
      <h3>Installation &amp; Extras</h3>
      <p>Install base <code>litestar-mcp</code> or enable optional extras for <code>queues</code>, <code>a2a</code>, <code>google-genai</code>, and <code>opentelemetry</code> with <code>uv</code>, <code>pip</code>, <code>pdm</code>, or <code>poetry</code>.</p>
    </div>
    <a href="installation/">Installation &amp; Extras &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3>5-Minute Quickstart</h3>
      <p>Attach <code>LitestarMCP</code> to a Litestar app, expose typed tools (<code>outputSchema</code> and <code>structuredContent</code>), <code>ui://</code> MCP Apps, prompts, and <code>litestar://openapi</code>, inspect skills from the CLI, and test with <code>2026-07-28</code> and <code>2025-11-25</code> (<code>Mcp-Session-Id</code>, <code>GET /mcp</code> SSE, <code>resources/subscribe</code>, <code>logging/setLevel</code>) requests.</p>
    </div>
    <a href="quickstart/">5-Minute Quickstart &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3>Standalone MCP Application</h3>
      <p>Build a declarative MCP server with <code>@mcp.tool()</code>, <code>@mcp.resource()</code>, and <code>@mcp.prompt()</code> and run it over Streamable HTTP or in-process stdio with <code>MCPStdioContext</code>.</p>
    </div>
    <a href="standalone/">Standalone MCP App &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3>Core Architectural Concepts</h3>
      <p>Understand internal ASGI dispatch, plugin ordering, universal dependency injection (<code>Provide</code>, <code>Dependency</code>, <code>FromDishka[T]</code>, <code>ToolContext</code>), and tag filtering.</p>
    </div>
    <a href="concepts/">Core Concepts &rarr;</a>
  </div>
</div>
