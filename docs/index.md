---
title: Litestar MCP
hide:
  - navigation
  - toc
---

<div class="lm-hero lm-home-hero">
  <img class="lm-hero-banner lm-hero-banner--light" src="assets/banner-inline-light.svg" alt="Litestar MCP">
  <img class="lm-hero-banner lm-hero-banner--dark" src="assets/banner-inline-dark.svg" alt="Litestar MCP">
  <p class="lm-hero-lead">
    <strong>Model Context Protocol (MCP), Multi-Agent Runtime, Durable DAG Workflows, and A2A 1.0 Interoperability for Litestar.</strong><br>
    Expose existing Litestar routes as MCP tools, resources, prompts, and Agent Skills without duplicating handlers, middleware, guards, or dependency injection.
  </p>
  <div class="lm-hero-actions">
    <a class="lm-cta lm-cta--primary" href="getting-started/">Getting Started</a>
    <a class="lm-cta" href="architecture/">System Architecture</a>
    <a class="lm-cta" href="reference/">API Reference</a>
  </div>
</div>

## Core Capabilities

<div class="lm-grid">
  <div class="lm-card">
    <div>
      <h3>Zero-Duplicate Route Exposure</h3>
      <p>Mark existing Litestar routes with <code>mcp_tool</code>, <code>mcp_resource</code>, or <code>mcp_prompt</code>. Tool and prompt calls execute through in-process ASGI dispatch so your authentication middleware, guards, DTOs, and lifecycle callbacks run identically.</p>
    </div>
    <a href="learn/marking-routes/">Route Marking &amp; Decorators &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3>Dual-Version Streamable HTTP &amp; Stdio</h3>
      <p>Serve stateless <code>2026-07-28</code> (<code>server/discover</code> and SEP-2575 capability headers) and <code>2025-06-18</code> (<code>initialize</code>, <code>ping</code>, <code>DELETE /mcp</code>) on one <code>/mcp</code> endpoint alongside in-process <code>litestar mcp stdio</code> and remote HTTP <code>bridge</code>.</p>
    </div>
    <a href="learn/discovery/">Transport &amp; Discovery &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3>Agent Skills &amp; Universal DI</h3>
      <p>Publish <code>io.modelcontextprotocol/skills</code> (SEP-2640) from static <code>SKILL.md</code> directories, dynamic request-aware <code>SkillSource</code> providers, or class-based <code>SkillController</code> and <code>PromptController</code> classes wired with <code>Provide</code> and <code>FromDishka[T]</code>.</p>
    </div>
    <a href="learn/skills/">Agent Skills Guide &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3>Distributed MCP Tasks &amp; Durable Workflows</h3>
      <p>Scale long-running <code>io.modelcontextprotocol/tasks</code> and <code>DynamicWorkflow</code> DAGs across worker replicas on <code>litestar-queues</code> with automatic principal, <code>SecurityContext</code>, and Dishka <code>Scope.REQUEST</code> rehydration plus multi-round <code>MCPInputRequiredResult</code> resume.</p>
    </div>
    <a href="queues/">Tasks, Queues &amp; Workflows &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3>Multi-Agent Runtime &amp; GoogleGenAIClient</h3>
      <p>Orchestrate <code>Agent</code> and <code>AgentGroup</code> hierarchies under <code>AgentRuntime</code> with strict <code>TurnBudget</code> ceilings, <code>GoogleGenAIClient</code> (defaulting to <code>gemini-3.8-flash</code> with thought-signature preservation), and owner-scoped <code>SessionStore</code> streaming.</p>
    </div>
    <a href="agents/">Agent Runtime &amp; Groups &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3>Official A2A 1.0 JSON-RPC + HTTP+JSON REST</h3>
      <p>Mount <code>LitestarA2A</code> on official <code>a2a-sdk</code> 1.0 primitives with dual JSON-RPC 2.0 and native <code>HTTP+JSON</code> REST bindings (<code>:send</code>, <code>:stream</code>, <code>:cancel</code>, <code>:subscribe</code>), split Agent Card security, and <code>/{tenant:str}</code> + <code>X-A2A-Tenant</code> multi-tenancy.</p>
    </div>
    <a href="agents/a2a-protocol/">A2A 1.0 Protocol &rarr;</a>
  </div>
</div>

---

## Request & Dispatch Architecture

Every MCP tool and prompt invocation passes through `MCPController` and `MCPHandlerService` into Litestar's internal ASGI router, ensuring a single source of truth for validation, security, dependency injection, and OpenTelemetry spans.

--8<-- "tools/docs/includes/diagram-request-pipeline.html"

---

## Choose Your Entry Point

`litestar-mcp` supports two complementary authoring styles that compile down to the exact same Litestar ASGI pipeline:

=== "LitestarMCP Plugin"

    Attach `LitestarMCP` to any existing `Litestar` application to expose selected routes over `/mcp` alongside your standard REST and OpenAPI endpoints:

    ```python title="docs/examples/snippets/framework_litestar.py"
    --8<-- "docs/examples/snippets/framework_litestar.py:example"
    ```

=== "Standalone MCP App"

    Use the declarative `MCP` wrapper when building a dedicated MCP server with `@mcp.tool()`, `@mcp.resource()`, and `@mcp.prompt()` decorators:

    ```python title="docs/examples/snippets/standalone_minimal.py"
    --8<-- "docs/examples/snippets/standalone_minimal.py:example"
    ```

---

## Installation

Install `litestar-mcp` and any optional integration extras (`a2a`, `queues`, `google-genai`, `opentelemetry`, or `all`):

=== "uv"

    ```bash
    uv add litestar-mcp
    uv add "litestar-mcp[all]"
    ```

=== "pip"

    ```bash
    pip install litestar-mcp
    pip install "litestar-mcp[all]"
    ```

=== "pdm"

    ```bash
    pdm add litestar-mcp
    pdm add "litestar-mcp[all]"
    ```

=== "poetry"

    ```bash
    poetry add litestar-mcp
    poetry add "litestar-mcp[all]"
    ```

---

## Explore by Domain

| Domain | Coverage |
| :--- | :--- |
| [**Architecture & Examples**](architecture/index.md) | Interactive HTML/SVG system diagrams, the 10-variant Notes reference application matrix (`advanced_alchemy` and `sqlspec`), and single-file PEP 723 `uv run` / `uvx` execution |
| [**Getting Started**](getting-started/index.md) | Entry-point comparison, optional extra matrix, step-by-step quickstart, standalone `MCP` server guide, and core architectural concepts |
| [**MCP Core**](learn/index.md) | `MCPConfig`, route markers, `MCPOptKeys` and tool annotations, resources and RFC 6570 URI templates, `PromptController`, Agent Skills (`SEP-2640`), Streamable HTTP discovery, stdio, and bridge |
| [**Tasks, Queues & Workflows**](queues/index.md) | `io.modelcontextprotocol/tasks`, `MCPInputRequiredResult`, distributed `QueueTaskExecutor`, `DurableWorkflowEngine` DAGs, `QueuedAgentChatController`, and multi-replica deployment |
| [**Agents & A2A**](agents/index.md) | `Agent`, `AgentRuntime`, `TurnBudget`, `GoogleGenAIClient`, `AgentGroup` handoffs, SSE session streaming, bidirectional protocol bridges, `LitestarA2A`, and multi-tenancy |
| [**Security & Operations**](security/index.md) | Litestar authentication middleware, first-party `litestar-security` & RFC 9728 OAuth Protected Resource Metadata, guards/scopes, and OpenTelemetry `TelemetryConfig` |
| [**Reference**](reference/index.md) | Curated API reference across all modules, auto-generated `litestar mcp` CLI documentation, contributor workflow, and release changelog |
