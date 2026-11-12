# MCP Protocol & Primitives Overview

`litestar-mcp` turns an existing Litestar application—or a standalone `MCP` wrapper—into a compliant Model Context Protocol server without duplicating business logic, validation schemas, or dependency injection wiring.

The plugin follows a direct compilation pipeline:

1. **Declare or Mark**: Tag existing Litestar route handlers with `mcp_tool`, `mcp_resource`, or `mcp_prompt` keyword arguments, apply `@mcp_tool` / `@mcp_resource` / `@mcp_prompt` decorators, or mount class-based `PromptController` and `SkillController` routers.
2. **Discover & Compile**: During `on_app_init`, `LitestarMCP` inspects `app.routes` and `MCPConfig`, builds JSON Schema 2020-12 contracts from `parsed_fn_signature`, infers MCP tool annotation hints from HTTP methods, and populates the `Registry` and `SkillCatalog`.
3. **Dispatch via Internal ASGI Scope**: Incoming JSON-RPC 2.0 operations and notification streams over Streamable HTTP (`POST /mcp`, `GET /mcp` SSE, `DELETE /mcp`) or in-process `stdio` execute through a synthesized internal HTTP scope (`execute_tool`, `execute_handler`), running your Litestar middleware, guards, `Provide` dependencies, and Dishka `Scope.REQUEST` containers identically across every transport.

--8<-- "tools/docs/includes/diagram-primitives-and-discovery.html"

## MCP Surface Primitives

| Primitive | Registration Surface | JSON-RPC Wire Methods | Primary Purpose |
| :--- | :--- | :--- | :--- |
| **Tools** | `mcp_tool=True` / `@mcp_tool` / `SkillController` (`@tool`) | `tools/list`, `tools/call` | Executable operations synthesizing JSON Schema 2020-12 `inputSchema` and opt-in `outputSchema` with automatic `structuredContent`, `SEP-1865` `title` / `icons` / `_meta.ui`, binary content blocks, or background MCP Tasks. |
| **Resources** | `mcp_resource=True` / `@mcp_resource(uri=...)` / `mcp_resource_template` | `resources/list`, `resources/templates/list`, `resources/read`, `resources/subscribe`, `resources/unsubscribe` | Read-only state, built-in `litestar://openapi`, static custom-scheme URIs vs `{var}` RFC 6570 Level 1 templates, raw MIME fidelity (`text/html;profile=mcp-app`), `contents[0]._meta`, and scoped change subscriptions. |
| **Prompts** | `mcp_prompt=True` / `@mcp_prompt` / `PromptController` (`@prompt`) | `prompts/list`, `prompts/get` | Templated model instructions and multi-message conversation starters normalized into `PromptMessage` sequences with `_meta` (`meta`), controller-level `instructions`, and optional `expose_as_prompts=True` skill projection. |
| **Agent Skills** | `MCPSkillsConfig` / `SkillCatalog` / `SkillController` | `skills/list`, `skills/get`, `resources/directory/read`, `resources/read`, `notifications/skills/list_changed` | Digest-locked `SKILL.md` instruction bundles (`io.modelcontextprotocol/skills`, SEP-2640) with `title` / `icons` / `tags` / `_meta`, tag and query filtering, top-level `SkillCatalog` DI, and `litestar mcp list-skills`. |

## Protocol Versions & Capabilities

`LitestarMCP` supports dual-mode Streamable HTTP negotiation on a single endpoint (`MCPConfig.base_path`, defaulting to `/mcp`):

- **Supported Protocol Versions**: `SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26")`.
- **Stateless `2026-07-28` Discovery**: Uses `server/discover`, enforces SEP-2575 request metadata (`params._meta["io.modelcontextprotocol/protocolVersion"]`, `params._meta["io.modelcontextprotocol/clientCapabilities"]`, optional `io.modelcontextprotocol/logLevel`) paired with HTTP routing headers (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, `Mcp-Param-*`), and streams filtered events via `POST /mcp` (`subscriptions/listen`).
- **Session-Aware `2025-11-25`, `2025-06-18` & `2025-03-26` Streamable HTTP**: Handles `initialize` issuing an owner-bound `Mcp-Session-Id` (`MCPSessionStore`), `202 Accepted` on `notifications/initialized`, session-scoped `GET /mcp` SSE (`Accept: text/event-stream`), `resources/subscribe` and `resources/unsubscribe`, `logging/setLevel`, `ping`, and `204 No Content` on `DELETE /mcp`.
- **Opt-In Capabilities & Extensions**:
  - `io.modelcontextprotocol/ui` (`UI_EXTENSION`): Advertised with `mimeTypes` (defaulting to `UI_MIME_TYPE = "text/html;profile=mcp-app"` when UI resources are registered or `MCPConfig.ui_mime_types` is configured) for `SEP-1865` MCP Apps.
  - `logging` (`capabilities.logging`): Advertised when `MCPConfig.logging=True` (`MCPConfig.default_log_level = "info"`), enabling `logging/setLevel` and RFC 5424 `notifications/message` delivery via `await MCPRequestContext.log(...)`.
  - `io.modelcontextprotocol/tasks`: Advertised when `MCPConfig.tasks` is enabled (or auto-wired with `litestar-queues` `QueuePlugin`), forwarding `progress_token`, `session_id`, and `log_level` across background runs.
  - `io.modelcontextprotocol/skills`: Advertised with `{"directoryRead": true, "listChanged": true}` (reflecting `directory_read` and `list_changed`) when `MCPConfig.skills` is configured.

## Core Guides in This Section

<div class="lm-grid">
  <div class="lm-card">
    <h3><a href="configuration.md">Configuration</a></h3>
    <p>Complete <code>MCPConfig</code>, <code>MCPOptKeys</code>, <code>MCPTaskConfig</code>, and <code>MCPSkillsConfig</code> reference covering <code>auto_output_schema</code>, <code>ui_mime_types</code>, <code>enable_get_sse</code>, <code>default_log_level</code>, <code>subscription_authorizer</code>, filtering, and pagination.</p>
  </div>
  <div class="lm-card">
    <h3><a href="marking-routes.md">Marking Routes &amp; Decorators</a></h3>
    <p>Compare route <code>opt</code> keyword arguments with <code>@mcp_tool</code>, <code>@mcp_resource</code>, and <code>@mcp_prompt</code>, SEP-1865 <code>title</code> / <code>icons</code> / <code>meta</code> keys, <code>mcp_output_schema</code>, docstring DI stripping, and universal DI.</p>
  </div>
  <div class="lm-card">
    <h3><a href="tools.md">Tools &amp; Annotations</a></h3>
    <p>Internal ASGI execution, JSON Schema 2020-12 synthesis (including <code>TypedDict</code>), opt-in <code>outputSchema</code> and automatic <code>structuredContent</code>, SEP-1865 <code>_meta.ui</code> linking, HTTP verb annotations, and <code>isError: true</code> semantics.</p>
  </div>
  <div class="lm-card">
    <h3><a href="resources.md">Resources &amp; URI Templates</a></h3>
    <p>Static custom-scheme <code>uri=</code> resources vs <code>{var}</code> RFC 6570 Level 1 templates, raw MIME fidelity (<code>text/html;profile=mcp-app</code>), <code>contents[0]._meta</code>, <code>litestar://openapi</code>, and <code>resources/subscribe</code>.</p>
  </div>
  <div class="lm-card">
    <h3><a href="prompts.md">Prompts &amp; PromptController</a></h3>
    <p>Standalone <code>@mcp_prompt</code> functions, route-bound prompts with <code>meta</code> (<code>_meta</code>), class-based <code>PromptController</code> with prepended <code>instructions</code> and guards, and <code>expose_as_prompts</code> skill projection.</p>
  </div>
  <div class="lm-card">
    <h3><a href="skills.md">Agent Skills (SEP-2640)</a></h3>
    <p>Static <code>SKILL.md</code> bundles, frontmatter metadata and tag/query filtering, <code>list_changed</code> notifications, dynamic <code>SkillSource</code> providers, top-level <code>SkillCatalog</code> DI, <code>list-skills</code> CLI, and <code>SkillController</code>.</p>
  </div>
  <div class="lm-card">
    <h3><a href="discovery.md">Discovery &amp; Protocol Versions</a></h3>
    <p>Dual-mode Streamable HTTP negotiation across stateless <code>2026-07-28</code> (<code>server/discover</code> + SEP-2575 headers) and session-aware <code>2025-11-25</code> / <code>2025-06-18</code> / <code>2025-03-26</code> (<code>Mcp-Session-Id</code>, <code>GET /mcp</code> SSE, <code>DELETE /mcp</code>).</p>
  </div>
  <div class="lm-card">
    <h3><a href="stdio-and-bridge.md">Stdio, HTTP Bridge &amp; Tool Spec</a></h3>
    <p>In-process <code>litestar mcp stdio</code> (<code>MCPStdioContext</code>), session-aware <code>litestar mcp bridge</code> forwarding, terminal inspection via <code>list-skills</code>, and static manifest export via <code>litestar mcp export-tool-spec</code>.</p>
  </div>
</div>
