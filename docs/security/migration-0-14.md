---
title: Migrating to v0.14–v0.18
---

# Migrating to `v0.14`–`v0.18`

This guide summarizes the breaking changes, package layout reorganization, protocol upgrades, and zero-config ergonomics introduced across `litestar-mcp` versions `v0.14.0` through `v0.18.0`.

---

## 1. Release Highlights at a Glance (`v0.14`–`v0.18`)

| Release | Key Additions & Behavioral Changes |
| :--- | :--- |
| **`v0.18.0`** | Distributed MCP Tasks on `litestar-queues` (`QueueTaskExecutor`) with automatic `Principal` / `SecurityContext` / Dishka rehydration; distributed DAG workflows (`DurableWorkflowEngine`, `WorkflowStore`); zero-config route-opt inference (`mcp_tool=True`, route `media_type` inference, `litestar_mcp_tasks` store auto-discovery, `AgentRuntime` plugin, and `LitestarA2A` `AgentExecutor` wrapping). |
| **`v0.17.0`** | Dual-version stateless Streamable HTTP negotiation (`2026-07-28` `server/discover` + `2025-06-18` / `2025-03-26` `initialize`, `ping`, `202 Accepted` notifications, and `DELETE /mcp`); `ToolAnnotations` and HTTP verb annotation inference; docstring DI section stripping and `litestar mcp export-tool-spec`; `LitestarA2A` native `HTTP+JSON` REST binding (`enable_http_json=True`), `agent_card_route_opt`, and `/{tenant:str}` / `X-A2A-Tenant` multi-tenancy. |
| **`v0.16.0`** | Request-aware `SkillSource` protocol, `FilesystemSkillSource`, `Skill.build` / `Skill.from_content`, and async `SkillCatalog` methods; universal DI (`Provide`, `FromDishka[T]`, `Inject()`, `Depends(fn)`, `ToolContext`) and recursive nested `Router` / `DishkaRouter` controller discovery. |
| **`v0.15.0`** | Class-based `PromptController` and `SkillController`; `Agent`, `AgentGroup`, `AgentRuntime`, `TurnBudget`, and `GoogleGenAIClient` (`gemini-3.8-flash`); owner-scoped `SessionStore` and `AgentChatController`; in-process `DynamicWorkflow` / `WorkflowEngine`; bidirectional bridges (`agent_to_mcp`, `mcp_to_tools`, `discover_mcp_tools`, `agent_to_a2a`); OpenTelemetry `TelemetryConfig` & `SpanManager`. |
| **`v0.14.0`** | Modular package layout (`litestar_mcp.{core,mcp,a2a,utils}`); removal of `litestar_mcp.auth` in favor of Litestar middleware and `litestar-security`; migration from `httpx` + `httpx-sse` to `httpx2`; Agent Skills (`SEP-2640`) via `MCPSkillsConfig`; in-process `litestar mcp stdio` entering native `Litestar.lifespan()`; A2A 1.0 SDK (`litestar-mcp[a2a]`) integration. |

---

## 2. Package Layout Reorganization (`v0.14.0`–`v0.18.0`)

`litestar-mcp` is organized into six domain subpackages with strict optional-extra isolation:

- `litestar_mcp.core`: Protocol-agnostic primitives (`Tool`, `ToolCall`, `ToolResult`, `tool`, `ToolContext`, `JSONRPCRequest`, `JSONRPCError`, `JSONRPCErrorException`, `SubscriptionManager`, `TelemetryConfig`, `SpanManager`, exceptions).
- `litestar_mcp.mcp`: MCP plugin (`LitestarMCP`), standalone wrapper (`MCP`), `MCPConfig`, `MCPController`, `MCPHandlerService`, `Registry`, `PromptController`, `SkillController`, Agent Skills (`Skill`, `SkillFile`, `SkillCatalog`, `SkillSource`, `FilesystemSkillSource`), MCP Tasks (`MCPTaskStore`, `TaskRecord`, `MCPTaskExecutor`, `InProcessTaskExecutor`, `ToolTaskCall`), stdio (`MCPStdioContext`, `run_stdio`, `run_stdio_async`), bridge (`run_stdio_streamable_http_bridge`), and CLI (`mcp_group`).
- `litestar_mcp.agent`: Multi-agent specifications (`Agent`, `AgentGroup`), `AgentRuntime`, `TurnBudget`, `ModelClient` / `GoogleGenAIClient` / `MockModelClient`, `SessionStore`, `AgentChatController`, in-process `DynamicWorkflow` / `WorkflowEngine`, and protocol bridges (`agent_to_mcp`, `mcp_to_tools`, `discover_mcp_tools`, `agent_to_a2a`).
- `litestar_mcp.queues` *(optional `queues` extra)*: `QueueTaskExecutor`, `DurableWorkflowEngine`, `WorkflowStore`, `WorkflowRun`, `NodeRecord`, `TaskDependencyChain`, `QueuedAgentChatController`, `QueuedTurnManager`, `QueuedTurn`, `AgentRegistry`, and `PrincipalSnapshot`.
- `litestar_mcp.a2a` *(optional `a2a` extra)*: `LitestarA2A` and `A2AConfig`.
- `litestar_mcp.utils`: Route marker decorators (`mcp_tool`, `mcp_resource`, `mcp_prompt`), description rendering, URI template matching, and handler signature introspection.

### Deep Import Migration Map

Top-level imports from `litestar_mcp` (`from litestar_mcp import LitestarMCP, MCP, MCPConfig, ...`) remain unchanged. If your code imported internal module paths from `v0.13` or earlier, update them as follows:

| Legacy Import Path (`<= v0.13`) | Current Import Path (`v0.14`–`v0.18`) |
| :--- | :--- |
| `litestar_mcp.plugin` | `litestar_mcp.mcp.plugin` (or `from litestar_mcp import LitestarMCP`) |
| `litestar_mcp.config` | `litestar_mcp.mcp.config` (or `from litestar_mcp import MCPConfig, MCPOptKeys, MCPTaskConfig, MCPSkillsConfig`) |
| `litestar_mcp.app` | `litestar_mcp.mcp.app` (or `from litestar_mcp import MCP`) |
| `litestar_mcp.routes` | `litestar_mcp.mcp.routes` (or `from litestar_mcp import MCPController`) |
| `litestar_mcp.services.handler` | `litestar_mcp.mcp.service` (`MCPHandlerService`, `MCPRequestContext`, `get_mcp_request_context`) |
| `litestar_mcp.mcp.service.RequestContext` | `litestar_mcp.mcp.service.MCPRequestContext` (or `from litestar_mcp import MCPRequestContext`) |
| `litestar_mcp.tasks` / `InMemoryTaskStore` | `litestar_mcp.mcp.tasks.MCPTaskStore` |
| `litestar_mcp.executor` | `litestar_mcp.mcp.executor` |
| `litestar_mcp.registry` | `litestar_mcp.mcp.registry` |
| `litestar_mcp.content` | `litestar_mcp.mcp.content` |
| `litestar_mcp.error_mapping` | `litestar_mcp.mcp.error_mapping` |
| `litestar_mcp.bridge` | `litestar_mcp.mcp.bridge` |
| `litestar_mcp.cli` | `litestar_mcp.mcp.cli` |
| `litestar_mcp.jsonrpc` | `litestar_mcp.core.jsonrpc` |
| `litestar_mcp.sse` | `litestar_mcp.core.sse` |
| `litestar_mcp.schema_builder` | `litestar_mcp.core.schema_builder` |
| `litestar_mcp.typing` | `litestar_mcp.core.typing` |
| `litestar_mcp.exceptions` | `litestar_mcp.core.exceptions` |
| `litestar_mcp.utils.serialization` | `litestar_mcp.core.serialization` |

---

## 3. Removal of `litestar_mcp.auth` in Favor of Litestar Middleware & `litestar-security`

`v0.14.0` removed the plugin-specific `litestar_mcp.auth` and `litestar_mcp.manifests` modules (`MCPAuthBackend`, `MCPAuthConfig`, `OIDCProviderConfig`, `create_oidc_validator`, `JWKSCache`, `DefaultJWKSCache`, `TokenValidator`), `MCPConfig.auth`, `MCPConfig.register_oauth_protected_resource`, and the plugin-mounted `/.well-known/oauth-protected-resource` route. The base package also depends on `litestar` rather than `litestar[jwt]`.

| Removed `v0.13` Symbol / Option | `v0.14`–`v0.18` Replacement |
| :--- | :--- |
| `MCPConfig(auth=MCPAuthConfig(...))` | Install a Litestar `AbstractAuthenticationMiddleware`, `OAuth2PasswordBearerAuth` (`litestar[jwt]`), or `litestar-security` `SecurityPlugin` on the `Litestar` app. |
| Per-route MCP auth policy | `MCPConfig(route_opt={"auth": required("api-key")})` with `litestar-security`, or `MCPConfig(guards=[...])`. |
| `MCPConfig(register_oauth_protected_resource=True)` | `SecurityConfig(protected_resource=ProtectedResourceConfig(...))` in `litestar-security` combined with `SecurityScheme(type="http", scheme="bearer")` on the bearer mechanism. |
| `create_oidc_validator` / `DefaultJWKSCache` | `litestar-security` JWT/OIDC/IAP verifiers or a custom `AbstractAuthenticationMiddleware` (see `docs/examples/notes/shared/auth.py`). |

See [Authentication Middleware, JWT & Google IAP](authentication.md) and [First-Party `litestar-security` Integration](litestar-security.md) for complete examples.

---

## 4. Dual-Version Streamable HTTP (`v0.14.0` vs `v0.17.0`)

- **In `v0.14.0`**: `MCP.run()` changed its default transport from `"sse"` to `transport="streamable-http"` (removing `transport="sse"`), and `/mcp` initially required the stateless `2026-07-28` `server/discover` flow.
- **Restored in `v0.17.0`**: `/mcp` now negotiates **both** modern `2026-07-28` and initialize-era `2025-06-18` / `2025-03-26` (`SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-06-18", "2025-03-26")`) on a single stateless endpoint:
  - **`2026-07-28` clients**: Call `server/discover`, supply protocol metadata in `params._meta`, and send SEP-2575 headers (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, `Mcp-Param-*`).
  - **`2025-06-18` / `2025-03-26` clients**: Call `initialize`, `notifications/initialized` (receiving `202 Accepted`), and `ping`, and may send `DELETE /mcp` (receiving `204 No Content`).

Every JSON-RPC request (excluding notifications) must carry a string or integer-valued finite numeric `id`. Missing, `null`, boolean, container, or fractional request IDs are rejected with HTTP `400 Bad Request` and JSON-RPC `INVALID_REQUEST` (`-32600`) before dependency resolution or handler execution.

---

## 5. `httpx2`, Streaming Backpressure & Native Stdio Lifespan (`v0.14.0`)

### `httpx2` Replaces `httpx` and the `bridge` Extra

- The runtime HTTP client dependency is `httpx2` (replacing `httpx` and `httpx-sse`).
- Because `httpx2` includes built-in Server-Sent Events (`EventSource`) support, the `[bridge]` optional extra was removed; `litestar mcp bridge` and `MCPStreamableHTTPClient` work out of the box with base `litestar-mcp`.
- Custom transports passed to `run_stdio_streamable_http_bridge(..., transport=...)` or `MCPStreamableHTTPClient(..., transport=...)` must subclass `httpx2.AsyncBaseTransport`.
- `max_message_size` (`--max-message-size`, default `16 MiB`) now bounds each incoming SSE event from the remote server in addition to each newline-delimited stdin frame.

### Streaming Backpressure & Stdio Lifespan

- Request progress streams use a bounded channel (`MCPConfig.stream_queue_capacity=256`) with producer backpressure, while `subscriptions/listen` streams disconnect slow consumers cleanly when their queue fills.
- `MCPConfig.stream_cleanup_timeout` and `A2AConfig.stream_cleanup_timeout` default to `5.0` seconds.
- In-process stdio (`litestar mcp stdio` and `run_stdio_async(app, shutdown_timeout=5.0)`) enters native `Litestar.lifespan()`. The legacy manual `app_lifespan` helper was removed in `v0.14.0`.

---

## 6. Dynamic `SkillSource` & Asynchronous `SkillCatalog` (`v0.16.0`)

In `v0.14.0`, `MCPSkillsConfig(paths=[...])` loaded a static synchronous `SkillCatalog`. In `v0.16.0`, `MCPSkillsConfig(sources=[...])` added the request-aware `SkillSource` protocol, making `SkillCatalog` methods asynchronous:

| `v0.14`–`v0.15` Synchronous API | `v0.16`–`v0.18` Asynchronous Request-Aware API |
| :--- | :--- |
| `catalog.skills` | `await catalog.list_skills(request)` (or `FilesystemSkillSource(paths).skills` for static inspection) |
| `catalog.get(uri)` | `await catalog.get(request, uri)` / `await catalog.get_skill(request, name)` |
| `catalog.get_file(uri)` | `await catalog.get_file(request, uri)` |
| `catalog.list_directory(uri)` | `await catalog.list_directory(request, uri)` |
| `catalog.read_file(match)` | `await catalog.read_file(request, match)` |

---

## 7. A2A 1.0 JSON-RPC, `HTTP+JSON` REST & Multi-Tenancy (`v0.14.0`–`v0.18.0`)

- Install `litestar-mcp[a2a]` (`a2a-sdk>=1.1.4`).
- `LitestarA2A` enforces A2A 1.0 (`A2A-Version: 1.0` or `1.0.x`).
- **`v0.17.0` `HTTP+JSON` REST & Multi-Tenancy**:
  - Set `A2AConfig(enable_http_json=True)` (enabled by default) to mount native A2A 1.0 REST endpoints (`/a2a/message:send`, `/a2a/message:stream`, `/a2a/tasks/{id}`, `/a2a/tasks/{id}:cancel`, `/a2a/tasks/{id}:subscribe`) alongside `POST /a2a` JSON-RPC 2.0.
  - Set `A2AConfig(agent_card_route_opt={...})` to supply separate Litestar route `opt` entries (such as public discovery auth policies) for `/.well-known/agent-card.json` independent of `A2AConfig.route_opt`.
  - Multi-tenant requests resolve tenant identifiers from `/{tenant:str}/a2a/...` path prefixes or the `X-A2A-Tenant` header (`A2AConfig.tenant_header`) and pass the prepared `ServerCallContext` to `A2AConfig.context_builder(request, context)`.
- **`v0.18.0` Direct `AgentExecutor` & Bridge Support**:
  - `LitestarA2A(agent_card, request_handler, config=...)` accepts an `AgentExecutor` directly as `request_handler` (wrapping it in `DefaultRequestHandler` with an `InMemoryTaskStore` automatically), and `agent_to_a2a(agent, runtime=runtime)` returns a ready-to-mount `LitestarA2A` plugin.

---

## 8. Zero-Config `litestar-queues` Integration & Route-Opt Inference (`v0.18.0`)

Upgrading to `v0.18.0` lets you delete manual wiring across five common patterns:

1. **Zero-Config `QueueTaskExecutor`**: Registering `QueuePlugin` (`litestar-queues>=0.13.0`) on the same `Litestar` app as `LitestarMCP` automatically enables MCP Tasks and attaches `QueueTaskExecutor` unless `MCPConfig(tasks=False)` or `MCPTaskConfig(executor="in_process")` is explicitly set.
2. **Automatic `litestar_mcp_tasks` Store Resolution**: `MCPTaskConfig.store` defaults to resolving the `"litestar_mcp_tasks"` store from `app.stores` (`StoreRegistry`), falling back to `MemoryStore()`.
3. **Boolean Route Markers & Decorator Defaults**: `@get("/users", mcp_tool=True)`, `@get("/config", mcp_resource=True)`, and `@post("/summarize", mcp_prompt=True)` (as well as `@mcp_tool()`, `@mcp_resource()`, `@mcp_prompt()` without a `name` argument) automatically name the primitive after the Python handler function.
4. **Automatic Resource `media_type` Inheritance**: Route handlers marked with `mcp_resource` automatically advertise their Litestar `media_type` (for example, `@get("/report.pdf", media_type="application/pdf", mcp_resource=True)`) without requiring a separate `mcp_resource_mime_type` override.
5. **`AgentRuntime` & `DurableWorkflowEngine` as Litestar Plugins**: Register `AgentRuntime(agent)` and `DurableWorkflowEngine([workflow])` directly in `Litestar(plugins=[...])` to wire the `runtime` and `workflow_engine` dependencies automatically.
