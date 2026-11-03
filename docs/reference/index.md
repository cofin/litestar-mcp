---
title: API Reference
---

# API Reference

This reference documents the public Python API, CLI subcommands, and contributor workflow for `litestar-mcp`. Every class, protocol, struct, decorator, and function listed in these chapters is rendered directly from static AST and PEP 257 attribute docstrings.

---

## Package Boundaries & Import Surface

`litestar-mcp` is organized into focused subpackages so optional integrations (`litestar-queues`, `a2a-sdk`, `google-genai`, `opentelemetry-api`, `dishka`, `litestar-security`) are never imported unless configured by your application.

| Package Namespace | Optional Extra | Responsibilities & Primary Exports |
| :--- | :--- | :--- |
| `litestar_mcp` | Base package | Top-level convenience re-exports (`LitestarMCP`, `MCP`, `MCPConfig`, `MCPOptKeys`, `MCPTaskConfig`, `MCPSkillsConfig`, `MCPController`, `PromptController`, `SkillController`, `@mcp_tool`, `@mcp_resource`, `@mcp_prompt`, `@tool`, `@prompt`, `Skill`, `SkillSource`, `FilesystemSkillSource`, `MCPToolResult`, `MCPResourceLink`, `MCPBlobResource`, `MCPInputRequiredResult`, `MCPRequestContext`, `MCPStdioContext`) and lazy resolution for `LitestarA2A`, `A2AConfig`, and `litestar_mcp.agent`. |
| `litestar_mcp.core` | Base package (`otel` optional) | Protocol-agnostic primitives: `Tool`, `ToolCall`, `ToolResult`, `ToolArgumentError`, `@tool`, `ToolContext`, JSON-RPC 2.0 models (`JSONRPCRequest`, `JSONRPCError`, `JSONRPCErrorException`), SSE `SubscriptionManager`, OpenTelemetry `TelemetryConfig` and `SpanManager`, and core exceptions (`LitestarMCPError`, `MissingDependencyError`, `BridgeConnectionError`, `BridgeMessageTooLargeError`). |
| `litestar_mcp.mcp` | Base package | MCP server runtime, route discovery, `Registry`, `MCPHandlerService`, internal ASGI execution (`execute_tool`, `execute_handler_response`, `blank_http_scope`), `MCPTaskStore` and `MCPTaskRuntime`, Agent Skills (`SkillCatalog`, `SkillIntegrityError`), `stdio` transport (`ASGIStreamingTransport`, `run_stdio`, `run_stdio_async`), Streamable HTTP bridge (`run_bridge`, `run_stdio_streamable_http_bridge`), and `MCPStreamableHTTPClient`. |
| `litestar_mcp.queues` | `litestar-mcp[queues]` | Distributed execution on `litestar-queues`: `QueueTaskExecutor`, `run_mcp_tool_task`, `DurableWorkflowEngine`, `WorkflowStore`, `WorkflowRun`, `NodeRecord`, `TaskDependencyChain`, `QueuedAgentChatController`, `QueuedTurnManager`, `QueuedTurn`, `AgentRegistry`, `PrincipalSnapshot`, and `run_agent_turn`. |
| `litestar_mcp.agent` | Base package (`google` / `a2a` optional) | Multi-agent orchestration and in-process workflows: `Agent`, `AgentGroup`, `AgentMessage`, `AgentRuntime`, `TurnRequest`, `TurnResponse`, `TurnBudget`, `BudgetExceededError`, `AgentChatController`, `SessionStore` (`MemorySessionStore`, `LitestarStoreSessionStore`), `ModelClient` (`GoogleGenAIClient`, `MockModelClient`), `AgentStreamFrame`, `DynamicWorkflow`, `WorkflowNode`, `WorkflowEngine`, and `litestar_mcp.agent.bridges`. |
| `litestar_mcp.a2a` | `litestar-mcp[a2a]` | Agent-to-Agent (A2A 1.0) protocol plugin (`LitestarA2A`, `A2AConfig`) mounting JSON-RPC 2.0 (`POST /a2a`), optional `HTTP+JSON` REST routes, and `/.well-known/agent-card.json` discovery around `a2a-sdk`. |
| `litestar_mcp.utils` | Base package | Route and function decorators (`mcp_tool`, `mcp_resource`, `mcp_prompt`), schema description bounds validation (`validate_tool_descriptions`), and handler signature introspection helpers. |

---

## Reference Chapters

<div class="lm-grid">
  <div class="lm-card">
    <div>
      <h3><a href="plugin-and-config.md">Plugin &amp; Configuration</a></h3>
      <p><code>LitestarMCP</code> plugin lifecycle, standalone <code>MCP</code> application wrapper, <code>MCPConfig</code>, <code>MCPOptKeys</code>, <code>MCPTaskConfig</code>, <code>MCPSkillsConfig</code>, tool-call hooks, and <code>MCPController</code>.</p>
    </div>
    <a href="plugin-and-config.md">Plugin &amp; Config API &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="primitives-and-skills.md">Primitives, Controllers &amp; Skills</a></h3>
      <p><code>@mcp_tool</code>, <code>@mcp_resource</code>, <code>@mcp_prompt</code>, <code>PromptController</code>, <code>SkillController</code>, Agent Skills (<code>SkillSource</code>, <code>FilesystemSkillSource</code>, <code>SkillCatalog</code>), content blocks, <code>Registry</code>, and <code>MCPHandlerService</code>.</p>
    </div>
    <a href="primitives-and-skills.md">Primitives &amp; Skills API &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="tasks-and-queues.md">Tasks, Queues &amp; Durable Workflows</a></h3>
      <p><code>MCPTaskExecutor</code>, <code>MCPTaskRuntime</code>, <code>MCPTaskStore</code>, <code>TaskRecord</code>, <code>QueueTaskExecutor</code>, <code>DurableWorkflowEngine</code>, <code>WorkflowStore</code>, <code>TaskDependencyChain</code>, <code>QueuedAgentChatController</code>, and <code>QueuedTurnManager</code>.</p>
    </div>
    <a href="tasks-and-queues.md">Tasks &amp; Queues API &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="agents-and-workflows.md">Agents, Sessions &amp; Workflows</a></h3>
      <p><code>Agent</code>, <code>AgentGroup</code>, <code>AgentRuntime</code>, <code>TurnBudget</code>, <code>AgentChatController</code>, <code>SessionStore</code> backends, <code>GoogleGenAIClient</code>, <code>AgentStreamFrame</code>, <code>DynamicWorkflow</code>, and <code>WorkflowEngine</code>.</p>
    </div>
    <a href="agents-and-workflows.md">Agents &amp; Workflows API &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="a2a-and-bridges.md">A2A 1.0, Bridges &amp; Transports</a></h3>
      <p><code>LitestarA2A</code>, <code>A2AConfig</code>, bidirectional MCP and A2A bridges (<code>agent_to_mcp</code>, <code>mcp_to_tools</code>, <code>discover_mcp_tools</code>, <code>agent_to_a2a</code>), <code>MCPStdioContext</code>, HTTP bridge runners, and <code>MCPStreamableHTTPClient</code>.</p>
    </div>
    <a href="a2a-and-bridges.md">A2A &amp; Bridges API &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="core-and-observability.md">Core Primitives &amp; Observability</a></h3>
      <p>Core <code>Tool</code> and <code>@tool</code>, <code>ToolContext</code>, <code>TelemetryConfig</code>, <code>SpanManager</code>, JSON-RPC 2.0 structures, <code>SubscriptionManager</code>, exception hierarchy, and internal ASGI executors.</p>
    </div>
    <a href="core-and-observability.md">Core &amp; Observability API &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="cli.md">CLI Reference</a></h3>
      <p>Auto-generated reference and practical recipes for <code>litestar mcp stdio</code>, <code>litestar mcp bridge</code>, <code>litestar mcp export-tool-spec</code>, <code>list-tools</code>, <code>list-resources</code>, and <code>run</code>.</p>
    </div>
    <a href="cli.md">CLI Reference &rarr;</a>
  </div>

  <div class="lm-card">
    <div>
      <h3><a href="contributing.md">Contributing Guide</a></h3>
      <p>Local workspace setup, running unit and protocol conformance suites, type-checking and linting gates, and the documentation verification pipeline.</p>
    </div>
    <a href="contributing.md">Contributing Guide &rarr;</a>
  </div>
</div>

---

## Release History

See the [Changelog](../changelog.md) for release notes, new capabilities, and migration notes across versions `v0.1.0` through `v0.18.0`.
