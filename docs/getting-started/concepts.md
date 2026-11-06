---
title: Core Architectural Concepts
---

# Core Architectural Concepts

`litestar-mcp` is architected as a native Litestar plugin rather than a sidecar server. Every MCP primitive compiles from your Litestar route handlers and controllers, and every execution request flows through Litestar's internal ASGI pipeline.

---

## Internal ASGI Dispatch Pipeline

When an MCP client invokes `tools/call` or `prompts/get` on `/mcp` (or over `litestar mcp stdio`), `MCPHandlerService` constructs a synthetic ASGI scope and dispatches it directly through the host `Litestar` application:

--8<-- "tools/docs/includes/diagram-request-pipeline.html"

This design provides four structural guarantees:

1. **Single Security Boundary**: Authentication middleware, `MCPConfig.guards`, controller guards, and handler guards execute on MCP invocations identically to HTTP requests.
2. **Zero Loopback Overhead**: Dispatch happens in-memory via the ASGI callable without opening a loopback TCP socket or serializing headers over the network.
3. **Full Lifecycle Fidelity**: `before_request`, `after_request`, `after_response`, exception handlers, and OpenTelemetry spans run around every tool and prompt call.
4. **Header & Identity Forwarding**: Authorization headers, `request.user`, `request.auth`, `litestar-security` `SecurityContext`, and trace contexts propagate automatically from `/mcp` into the target handler scope.

---

## Plugin Registration & Ordering

Register `LitestarMCP` in your application's `plugins` list:

```python title="docs/examples/snippets/framework_litestar.py"
--8<-- "docs/examples/snippets/framework_litestar.py:example"
```

!!! important "Register `LitestarMCP` after route-mutating plugins"
    `LitestarMCP` discovers marked routes during `on_app_init`. If your application uses plugins that wrap or mutate route handlers (such as Dishka's `setup_dishka`, serialization plugins, or security plugins), register `LitestarMCP` after them (or call `setup_dishka(container, app)` on the constructed app) so their metadata is visible when the MCP `Registry` compiles schemas.

---

## Universal Dependency Injection

`litestar-mcp` supports five dependency injection mechanisms across route handlers, `PromptController`, `SkillController`, and `@mcp.tool()` functions. Internal DI parameters are automatically stripped from the public MCP JSON Schema so LLM clients only see user-supplied arguments:

| DI Pattern | Where Used | Schema Visibility | Runtime Resolution |
| :--- | :--- | :--- | :--- |
| **Litestar `Provide(...)`** | `dependencies={"svc": Provide(fn)}` on app, router, controller, or route | Stripped if return type is an internal service; included if provider takes user query/body arguments | Resolved by Litestar's native dependency graph per request |
| **Dishka `FromDishka[T]`** | Route handlers, `PromptController`, `SkillController`, background tasks | Always stripped from public MCP tool/prompt JSON Schema | Resolved from the active Dishka `Scope.REQUEST` container (including rehydrated worker scopes in `QueueTaskExecutor` and `DurableWorkflowEngine`) |
| **Litestar `Dependency(skip_validation=True)`** | `svc: MyService = Dependency(skip_validation=True)` on route handlers, `PromptController`, or `SkillController` | Stripped when resolved from internal `Provide` dependencies | Resolved by Litestar's native dependency graph per request |
| **`Request` & `State`** | `request: Request` and `state: State` parameters | Always stripped from public MCP JSON Schemas | Resolved from the internal ASGI scope |
| **`ToolContext`** | `ctx: ToolContext` parameter on any MCP tool handler | Always stripped from public MCP tool JSON Schema | Injected by `MCPHandlerService` and `MCPTaskExecutor`; provides `ctx.user`, `ctx.tenant_id`, `ctx.roles`, `ctx.scopes`, `ctx.session_id`, and `ctx.turn_id` (use `get_mcp_request_context().report_progress(...)` and `MCPInputRequiredResult` inside MCP tool handlers) |

---

## OpenAPI Integration

- **Marked Routes in OpenAPI**: Routes marked with `mcp_tool`, `mcp_resource`, or `mcp_prompt` remain normal Litestar handlers and appear in your application's OpenAPI documentation unless you set `include_in_schema=False` on the route decorator.
- **The `/mcp` Transport Route**: The `/mcp` JSON-RPC endpoint itself is excluded from OpenAPI by default (`MCPConfig.include_in_schema=False`). Set `MCPConfig(include_in_schema=True)` if you want `/mcp` listed in your OpenAPI specification.
- **Built-in `litestar://openapi` Resource**: `LitestarMCP` automatically registers the `litestar://openapi` resource URI, allowing MCP clients to read your application's live OpenAPI schema via `resources/read`.

---

## Tag and Operation Filtering

Use `MCPConfig` filtering options to expose a subset of marked routes on `/mcp` without modifying individual route decorators:

| `MCPConfig` Field | Type | Behavior |
| :--- | :--- | :--- |
| `include_tags` | `list[str] or None` | Only expose marked handlers that carry at least one matching Litestar `tags=[...]` entry |
| `exclude_tags` | `list[str] or None` | Hide any marked handler carrying a matching tag (takes precedence over `include_tags`) |
| `include_operations` | `list[str] or None` | Only expose marked handlers whose MCP tool/resource name or `operation_id` is in this set |
| `exclude_operations` | `list[str] or None` | Hide any marked handler whose MCP name or `operation_id` is in this set (takes precedence over `include_operations`) |

Filters apply consistently across discovery (`tools/list`, `resources/list`, `resources/templates/list`) and direct invocation (`tools/call`, `resources/read`).

!!! warning "Filtering is not access control"
    Tag and operation filters shape the public MCP catalog at startup. For per-user or per-tenant authorization, use Litestar authentication middleware, `MCPConfig(guards=[...])`, or first-party `litestar-security` route options (`MCPConfig(route_opt={"auth": required(...)})`).
