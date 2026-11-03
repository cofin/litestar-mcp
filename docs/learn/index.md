# MCP Protocol & Primitives Overview

`litestar-mcp` turns an existing Litestar application—or a standalone `MCP` wrapper—into a compliant Model Context Protocol server without duplicating business logic, validation schemas, or dependency injection wiring.

The plugin follows a direct compilation pipeline:

1. **Declare or Mark**: Tag existing Litestar route handlers with `mcp_tool`, `mcp_resource`, or `mcp_prompt` keyword arguments, apply `@mcp_tool` / `@mcp_resource` / `@mcp_prompt` decorators, or mount class-based `PromptController` and `SkillController` routers.
2. **Discover & Compile**: During `on_app_init`, `LitestarMCP` inspects `app.routes` and `MCPConfig`, builds JSON Schema 2020-12 contracts from `parsed_fn_signature`, infers `ToolAnnotations` hints from HTTP methods, and populates the `Registry` and `SkillCatalog`.
3. **Dispatch via Internal ASGI Scope**: Incoming JSON-RPC 2.0 calls over Streamable HTTP (`POST /mcp`, `DELETE /mcp`) or in-process `stdio` execute through a synthesized internal HTTP scope (`execute_tool`, `execute_handler`), running your Litestar middleware, guards, `Provide` dependencies, and Dishka `Scope.REQUEST` containers identically across every transport.

--8<-- "tools/docs/includes/diagram-primitives-and-discovery.html"

## The Four MCP Surface Primitives

| Primitive | Registration Surface | JSON-RPC Wire Methods | Primary Purpose |
| :--- | :--- | :--- | :--- |
| **Tools** | `mcp_tool=True` / `@mcp_tool` / `SkillController` (`@tool`) | `tools/list`, `tools/call` | Executable operations that accept structured JSON arguments, run validation and guards, and return structured or binary content blocks (or promote long-running work to background MCP Tasks). |
| **Resources** | `mcp_resource=True` / `@mcp_resource` / `mcp_resource_template` | `resources/list`, `resources/templates/list`, `resources/read` | Read-only application state, schemas (`litestar://openapi`), binary projections, and parameterized RFC 6570 Level 1 URI templates (`app://workspaces/{workspace_id}/files/{file_id}`). |
| **Prompts** | `mcp_prompt=True` / `@mcp_prompt` / `PromptController` (`@prompt`) | `prompts/list`, `prompts/get` | Templated model instructions and multi-message conversation starters normalized into MCP `PromptMessage` sequences with optional controller-level `instructions`. |
| **Agent Skills** | `MCPSkillsConfig(paths=[...], sources=[...])` / `SkillController` | `skills/list`, `skills/get`, `resources/directory/read`, `resources/read` | Reusable `SKILL.md` instruction bundles and supporting files (`io.modelcontextprotocol/skills`, SEP-2640) served from digest-locked filesystem folders or dynamic request-aware `SkillSource` providers. |

## Protocol Versions & Capabilities

`LitestarMCP` supports dual-version stateless Streamable HTTP negotiation on a single endpoint (`MCPConfig.base_path`, defaulting to `/mcp`):

- **Supported Protocol Versions**: `SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-06-18", "2025-03-26")`.
- **Stateless `2026-07-28` Discovery**: Uses `server/discover` and enforces SEP-2575 request metadata (`params._meta["io.modelcontextprotocol/protocolVersion"]`, `params._meta["io.modelcontextprotocol/clientCapabilities"]`) paired with HTTP routing headers (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, `Mcp-Param-*`).
- **Session-Oriented `2025-06-18` & `2025-03-26` Handshake**: Supports `initialize`, `notifications/initialized`, `ping`, `202 Accepted` on `notifications/*`, and `204 No Content` on `DELETE /mcp` for broad ecosystem compatibility with existing MCP clients and inspectors.
- **Opt-In Extensions**:
  - `io.modelcontextprotocol/tasks`: Advertised when `MCPConfig.tasks` is enabled (or auto-wired when `litestar-queues` `QueuePlugin` is installed on the application).
  - `io.modelcontextprotocol/skills`: Advertised with `{"directoryRead": true | false}` when `MCPConfig.skills` is configured.

## Core Guides in This Section

<div class="lm-grid">
  <div class="lm-card">
    <h3><a href="configuration.md">Configuration</a></h3>
    <p>Complete <code>MCPConfig</code>, <code>MCPOptKeys</code>, <code>MCPTaskConfig</code>, and <code>MCPSkillsConfig</code> reference, operation/tag filtering precedence, opaque-cursor pagination, and lifecycle callbacks.</p>
  </div>
  <div class="lm-card">
    <h3><a href="marking-routes.md">Marking Routes &amp; Decorators</a></h3>
    <p>Compare route <code>opt</code> keyword arguments with <code>@mcp_tool</code>, <code>@mcp_resource</code>, and <code>@mcp_prompt</code>, description fallback precedence, docstring DI stripping, and universal dependency injection.</p>
  </div>
  <div class="lm-card">
    <h3><a href="tools.md">Tools &amp; Annotations</a></h3>
    <p>Internal ASGI execution, JSON Schema 2020-12 synthesis, automatic HTTP verb <code>ToolAnnotations</code> inference, explicit <code>input_schema</code>, binary content blocks, and the <code>isError: true</code> contract.</p>
  </div>
  <div class="lm-card">
    <h3><a href="resources.md">Resources &amp; URI Templates</a></h3>
    <p>Static resources, automatic <code>media_type</code> inference, RFC 6570 Level 1 URI templates, built-in <code>litestar://openapi</code>, <code>skill://</code> resolution order, and resource subscriptions.</p>
  </div>
  <div class="lm-card">
    <h3><a href="prompts.md">Prompts &amp; PromptController</a></h3>
    <p>Standalone <code>@mcp_prompt</code> functions, route-bound prompts, class-based <code>PromptController</code> with prepended <code>instructions</code> and guards, and return value normalization.</p>
  </div>
  <div class="lm-card">
    <h3><a href="skills.md">Agent Skills (SEP-2640)</a></h3>
    <p>Static filesystem <code>SKILL.md</code> bundles, startup validation, dynamic request-aware <code>SkillSource</code> implementations with SHA-256 verification, and <code>SkillController</code> unification.</p>
  </div>
  <div class="lm-card">
    <h3><a href="discovery.md">Discovery &amp; Protocol Versions</a></h3>
    <p>Dual-version Streamable HTTP negotiation across <code>2026-07-28</code> (<code>server/discover</code> + SEP-2575 headers) and <code>2025-06-18</code> / <code>2025-03-26</code> (<code>initialize</code>, <code>ping</code>, <code>DELETE /mcp</code>).</p>
  </div>
  <div class="lm-card">
    <h3><a href="stdio-and-bridge.md">Stdio, HTTP Bridge &amp; Tool Spec</a></h3>
    <p>In-process <code>litestar mcp stdio</code> (`MCPStdioContext`), remote <code>litestar mcp bridge</code> proxy flags, and static tool manifest export via <code>litestar mcp export-tool-spec</code>.</p>
  </div>
</div>
