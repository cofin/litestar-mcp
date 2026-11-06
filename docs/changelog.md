---
title: Changelog
---

# Changelog

All notable `litestar-mcp` changes are summarized here. Entries are grouped by release and focus on user-visible behavior, public API changes, compatibility notes, and protocol fixes.

---

## v0.19.0 (2026-11-06)

### Features

- **Add first-class optional route `auth` policy metadata across MCP and A2A** ([#107](https://github.com/cofin/litestar-mcp/pull/107))
  Added optional `auth` fields to `MCPConfig`, `A2AConfig`, and `agent_to_a2a(..., auth=...)` so applications can declare route-level authentication policies alongside `route_opt` without breaking positional `MCPConfig` compatibility.

- **Accept controller instances, `@prompt` callables, shorthand skill URIs, and lazy top-level exports** ([#107](https://github.com/cofin/litestar-mcp/pull/107))
  Updated `LitestarMCP` to accept pre-instantiated `PromptController` and `SkillController` objects alongside subclasses and `@prompt`-decorated callables in `MCPConfig.prompts`. Updated `SkillCatalog.get` to resolve `"skill://<name>"` shorthand URIs as well as `"skill://<name>/SKILL.md"`. Added lazy package re-exports for `litestar_mcp.agent` and `litestar_mcp.core` symbols, and converted `litestar_mcp.core.serialization` (`encode_json`, `decode_json`, `to_json`, `from_json`) into documented wrapper functions.

- **Extend A2A owner resolution, pre-startup MCP tool bridging, and CLI route filtering** ([#107](https://github.com/cofin/litestar-mcp/pull/107))
  Updated `_default_owner_resolver` in `litestar_mcp.agent.bridges.a2a` to fall back to `user_name` / `username` user attributes and mapping keys, enabled `mcp_to_tools(app)` to discover routes before `on_startup` lifespan execution, evicted expired locks in `MemorySessionStore`, and propagated `route_filter` / `include_tags` / `exclude_tags` options in `litestar mcp list-tools` and `litestar mcp export-tool-spec`.

### Bug Fixes

- **Preserve content-type parameters on `resources/read` responses** ([#107](https://github.com/cofin/litestar-mcp/pull/107), closes [#106](https://github.com/cofin/litestar-mcp/issues/106))
  Updated `_capture_asgi_response` and `_resource_content_from_response` so `resources/read` preserves full non-charset MIME parameters (such as `;profile=mcp-app`, `; variant=GFM`, and `;profile=mcp-schema`) declared on resource route `media_type` values while stripping transport `charset` parameters.

- **Traverse hoisted `$defs` schemas, encode `Mcp-Name` headers, and propagate queued turn context** ([#107](https://github.com/cofin/litestar-mcp/pull/107))
  Resolved `#/$defs/<name>` references across `iter_mcp_header_fields` and recursive `validate_tool_descriptions`, RFC 2047 base64-encoded non-ASCII `Mcp-Name` headers in `MCPStreamableHTTPClient`, forwarded `QueuedTurn.dynamic_context` and `PrincipalSnapshot` (`roles`, `scopes`) across `litestar-queues` worker turns, added `Last-Event-ID` SSE replay to `QueuedAgentChatController.stream_turn`, and populated `StepResult.error` on failed in-process `WorkflowEngine` steps.

### Documentation

- **Validate runnable examples, interactive diagrams, and all documentation sections** ([#107](https://github.com/cofin/litestar-mcp/pull/107))
  Added named `pymdownx.snippets` section slicing across runnable `docs/examples/**/*.py` modules, fixed SVG diagram bounding boxes and accessibility labels, aligned all guides and API reference pages (retiring legacy migration pages with static HTML redirects in `tools/docs/postbuild.py`), and configured `zensical.toml` `exclude_docs` as a multiline gitignore string.

### Changed

- **Update locked dependencies and pre-commit hooks** ([#107](https://github.com/cofin/litestar-mcp/pull/107))
  Updated `uv.lock` dependencies (including `google-genai`, `litestar-queues`, `sqlspec`, `ruff`, and `zensical`) and updated `ruff-pre-commit` to `v0.17.4` in `.pre-commit-config.yaml`.

---

## v0.18.0 (2026-11-03)

### Features

- **Support distributed MCP Tasks on `litestar-queues` via `QueueTaskExecutor`** ([#104](https://github.com/cofin/litestar-mcp/pull/104))
  Added `QueueTaskExecutor` so long-running MCP Tasks run on workers managed by `litestar-queues` (optional `queues` extra). Set it on `MCPTaskConfig(executor=...)` and register `QueuePlugin` on the same app; `LitestarMCP` attaches the executor's task dependencies to the queue during app init. `MCPTaskConfig.executor` is typed by the `MCPTaskExecutor` protocol, so the core package never imports `litestar-queues`. Supports fail-closed principal loading (`principal_loader`), cooperative cancellation, cross-replica resumption on `input_required`, and internal job dispatch decoupling. `MCPTaskStore.update` returns merged responses and `MCPTaskStore.create` takes a keyword-only `local`. The `queues` extra now requires `litestar-queues>=0.13.0`, whose standalone workers run the application lifespan, so MCP Tasks and durable workflows execute on every worker placement.

- **Support durable workflow execution on `litestar-queues` via `DurableWorkflowEngine`** ([#104](https://github.com/cofin/litestar-mcp/pull/104))
  Added `DurableWorkflowEngine`, `WorkflowStore`, `WorkflowRun`, and `NodeRecord` in `litestar_mcp.queues` for durable, distributed DAG workflows. `DurableWorkflowEngine(workflows)` is a Litestar plugin: registered next to `QueuePlugin` it attaches its task dependencies, stores records in the app's `stores` registry (`litestar_mcp_workflows`), and provides itself as the `workflow_engine` dependency. Provides idempotent node scheduling with reservation cleanup, at-least-once node execution, per-run watchdog ticks that recover lost advances, configurable run timeouts, and rejects immediate execution backends when the watchdog is active.

- **Infer configuration that previously had to be spelled out** ([#104](https://github.com/cofin/litestar-mcp/pull/104))
  - `mcp_tool`, `mcp_resource`, and `mcp_prompt` decorators default `name` to the function name, and the route opt keys accept `True` (`@get("/users", mcp_tool=True)`) to do the same.
  - Resources advertise the route's `media_type` as their MIME type when no `mcp_resource_mime_type` / `mime_type` override is set.
  - `MCPTaskConfig.store` defaults to the app's `stores` registry entry `litestar_mcp_tasks`, so a shared `StoreRegistry` covers MCP Tasks.
  - `AgentRuntime` is a Litestar plugin that provides itself as the `runtime` dependency used by `AgentChatController`.
  - `LitestarA2A` accepts an `AgentExecutor` in place of a `RequestHandler` and wraps it in `DefaultRequestHandler` with an `InMemoryTaskStore`.

### Documentation

- **Overhaul documentation, interactive architecture diagrams, and PEP 257 field docstrings** ([#105](https://github.com/cofin/litestar-mcp/pull/105))
  Migrated the documentation site to Zensical with a 7-tab architecture, interactive SVG architecture diagrams, PEP 257 field docstrings across all public structs, dataclasses, and classes, automated CLI reference generation, and Sphinx v2 `objects.inv` / `llms.txt` post-build verification.

---

## v0.17.0 (2026-11-02)

### Features

- **Support MCP `ToolAnnotations` via route opt keys and HTTP method inference** ([#103](https://github.com/cofin/litestar-mcp/pull/103), closes [#99](https://github.com/cofin/litestar-mcp/issues/99))
  Added `ToolAnnotations`, `MCPOptKeys.annotations` (`mcp_annotations`), `MCPOptKeys.read_only` (`mcp_read_only`), and `MCPConfig.infer_http_method_annotations` so Litestar routes and `@tool` callables serialize `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`, and `title` under `tools/list` `annotations`.

- **Support dual-version stateless Streamable HTTP negotiation** ([#103](https://github.com/cofin/litestar-mcp/pull/103), closes [#100](https://github.com/cofin/litestar-mcp/issues/100))
  Added `SUPPORTED_PROTOCOL_VERSIONS` (`2026-07-28`, `2025-06-18`, `2025-03-26`) so `/mcp` negotiates both `2026-07-28` (`server/discover` with SEP-2575 request header validation) and `2025-06-18` / `2025-03-26` (`initialize`, `notifications/initialized`, `ping`, `202 Accepted` notifications, and `204 No Content` on `DELETE /mcp`) on a single stateless endpoint.

- **Honor route handler description, strip docstring DI sections, and add `export-tool-spec` CLI** ([#103](https://github.com/cofin/litestar-mcp/pull/103), closes [#101](https://github.com/cofin/litestar-mcp/issues/101))
  Updated `extract_description_sources` and `strip_docstring_sections` to honor `handler.description` / `handler.summary` and strip Google, Sphinx, and NumPy parameter/return/raises docstring sections from top-level tool, resource, and prompt descriptions while preserving per-parameter descriptions in `inputSchema.properties`. Added `litestar mcp export-tool-spec` to export static `{"tools": [...]}` manifests without booting the ASGI server.

- **Support `LitestarA2A` route opt discovery, tenant resolution, and `HTTP+JSON` transport** ([#103](https://github.com/cofin/litestar-mcp/pull/103), closes [#102](https://github.com/cofin/litestar-mcp/issues/102))
  Added `A2AConfig.agent_card_route_opt`, `A2AConfig.tenant_header`, and `A2AConfig.enable_http_json` so `LitestarA2A` discovers `AgentCard` metadata from route `opt` dictionaries, propagates tenant context from `X-A2A-Tenant` headers and `/{tenant}/...` routes into `ServerCallContext.state["tenant"]`, and serves the A2A v1.0 `HTTP+JSON` REST binding alongside JSON-RPC 2.0.

---

## v0.16.0 (2026-10-18)

### Breaking Changes

- **Make `SkillCatalog` accessors asynchronous and request-aware**
  `SkillCatalog.skills`, `list_page`, `get_by_uri`, `read_file`, and `read_directory` are now `async` methods accepting an optional `Request` so dynamic `SkillSource` backends can resolve skills per caller. Direct callers inspecting a static filesystem snapshot can read `FilesystemSkillSource(paths).skills`.

### Features

- **Add request-aware `SkillSource` protocol and dynamic skill composition**
  Added the `SkillSource` protocol, `FilesystemSkillSource`, in-memory `Skill.build` / `Skill.from_content` and `SkillFile.from_content` constructors, and `MCPSkillsConfig.sources` so applications can serve caller-scoped, database-backed, or lazily loaded Agent Skills over MCP alongside static filesystem folders while preserving SHA-256 manifest integrity verification.

- **Support universal dependency injection and nested router discovery for tools and controllers**
  `@tool`, `SkillController`, and `PromptController` now resolve Litestar `Provide` / `Dependency` providers, Dishka `FromDishka[T]` dependencies, third-party `Annotated[T, Inject()]` / `Depends(fn)` markers, and custom `ToolContext(container=..., dependency_resolver=...)` hooks across HTTP endpoints, MCP `tools/call` / `prompts/get`, and standalone `AgentRuntime` turns. `LitestarMCP` also recursively discovers and deduplicates `SkillController` and `PromptController` classes mounted inside nested `Router` and `DishkaRouter` trees.

---

## v0.15.0 (2026-10-04)

### Features

- **Agent abstractions, class-based controllers, and Google GenAI runtime**
  Added high-level agent primitives integrating directly with Litestar:
  - Class-based `PromptController` and `SkillController` compile each method into an internal route handler preserving Litestar guards and dependency injection providers. Class instructions are automatically prepended to the first user message, adhering to MCP's user/assistant role constraint.
  - Core `Tool` and `@tool` abstraction with type-based context injection (`ToolContext`, `ASGIConnection`, `Request`) and strict `msgspec` argument validation.
  - Hierarchical `Agent` and `AgentGroup` models with non-mutating coordinator boundaries and `Literal`-typed `transfer_to_agent` tools.
  - `GoogleGenAIClient` defaulting to `gemini-3.8-flash` with automatic thought signature and provider call ID preservation, structured function response envelopes, thinking token tracking, and shared `httpx2` client support.
  - Pluggable `SessionStore` implementations (`MemorySessionStore` and `LitestarStoreSessionStore`) with per-session locks, TTL eviction, and strict owner scoping.
  - Realtime SSE chat streaming via `AgentChatController` with typed `AgentStreamFrame` structures, keepalive pings, and `Last-Event-ID` cursor recovery.
  - Concurrent in-process DAG execution engine via `DynamicWorkflow` and `WorkflowEngine` with ready queue scheduling and upstream output passing.
  - Protocol bridges for bidirectional MCP (`agent_to_mcp`, `mcp_to_tools`, `discover_mcp_tools`) and A2A 1.0 (`agent_to_a2a`).
  - OpenTelemetry observability using vendor-neutral GenAI semantic conventions (`invoke_agent`, `execute_tool`) with `enable_spans=False` default matching `sqlspec`.

---

## v0.14.0 (2026-09-14)

### Breaking Changes

- **Move the HTTP client to `httpx2` and drop the `bridge` extra**
  The runtime dependency on `httpx` is replaced by `httpx2`. The stdio bridge reads server-sent events with `httpx2`'s built-in `EventSource`, so the `bridge` extra and `httpx-sse` are gone, and `max_message_size` now bounds each incoming event as well as each stdin message.

- **Require modern MCP requests and canonical APIs**
  `MCP.run()` now defaults to `transport="streamable-http"` and rejects the removed `"sse"` selector. Use `MCPRequestContext` and `MCPTaskStore` instead of the removed MCP `RequestContext` and `InMemoryTaskStore` aliases. Missing, null, boolean, container, and fractional request IDs are rejected before tool dependency resolution, execution, or stream allocation. Current task, subscription, cache, and application-session settings remain available.

- **Remove the built-in OIDC auth backend and protected-resource manifest**
  Removed `litestar_mcp.auth`, `litestar_mcp.manifests`, `MCPConfig.auth`, `MCPConfig.register_oauth_protected_resource`, and `/.well-known/oauth-protected-resource`. Authentication is the app's Litestar middleware (`litestar-security` or a custom `AbstractAuthenticationMiddleware`); declare an opt-based policy with `MCPConfig.route_opt` and publish RFC 9728 metadata from the security plugin. The runtime dependency is `litestar` instead of `litestar[jwt]`.

- **Bound subscription queues**
  `MCPConfig.stream_queue_capacity` (default `256`) bounds each `subscriptions/listen` queue. A subscriber that falls behind receives the `resultType: "complete"` response for its subscription and is then closed instead of growing memory without limit.

- **Group the package into `core`, `mcp`, `a2a`, and `utils`**
  `litestar_mcp.core` holds the protocol-agnostic primitives (JSON-RPC, subscription streams, schema building, serialization, typing, exceptions); `litestar_mcp.mcp` holds the MCP plugin, configuration, routes, executor, registry, tasks, bridge, and CLI; `litestar_mcp.a2a` holds the optional A2A adapter; `litestar_mcp.utils` keeps the decorators and signature helpers. Root imports are unchanged and `litestar_mcp.A2AConfig` / `litestar_mcp.LitestarA2A` resolve lazily without importing `a2a-sdk` at package import time. Deep module paths moved without compatibility aliases.

### Features

- **Serve Agent Skills over MCP**
  Added `MCPSkillsConfig` and the opt-in `io.modelcontextprotocol/skills` extension, which loads a static catalog of `<path>/<name>/SKILL.md` folders once at startup. `skills/list` and `skills/get` return each skill's frontmatter and a SHA-256 manifest of its files, and `resources/directory/read` lists the entries directly under a skill directory when `directory_read` is enabled. Skill files themselves are served through the existing `resources/read` method under `skill://<name>/<relative-path>` URIs, alongside handler-declared resources.

- **Add Litestar-native in-process stdio serving**
  Added `litestar mcp stdio`, `run_stdio_async`, and a streaming ASGI transport. Standalone stdio now runs through the application's real MCP route, lifespan, middleware, guards, and dependency system; the HTTP bridge remains available for already-running servers.

- **Add standards-backed optional A2A support**
  Added `litestar-mcp[a2a]` and the narrow `litestar_mcp.a2a.A2AConfig` / `LitestarA2A` adapter around the official A2A 1.0 SDK. Litestar owns HTTP, JSON, SSE, guards, middleware, and disconnect cleanup; the optional extra uses bare `a2a-sdk`. The RPC route is exempt from CSRF, hidden from OpenAPI unless `A2AConfig.include_in_schema` is set, validates the request's `A2A-Version` header regardless of the configured context builder, reports `scope["user"]` objects that carry `is_authenticated` as such, and serves the agent card with `ETag` and `Cache-Control`. Only A2A 1.0 is accepted: requests must send `A2A-Version: 1.0` (or a `1.0.x` patch version) and use the 1.0 method names and protobuf JSON shapes. `A2AConfig.context_builder` receives `(request, context)` and may be synchronous or asynchronous. Notifications are answered with HTTP 204 without running the handler, while explicit null IDs still get a response. The card's JSONRPC interface URL may carry a proxy or `root_path` prefix ahead of `A2AConfig.path`. An authenticated principal without an `id`, `sub`, or `username` is refused unless a context builder supplies the SDK user, so it never shares the anonymous task owner. Requires `a2a-sdk` 1.1.4 or later.

- **Stream progress on the requesting response**
  A request carrying `_meta.progressToken` is answered on its own `text/event-stream` response: `MCPRequestContext.report_progress` emits `notifications/progress` there, followed by the JSON-RPC response. Progress is never fanned out to `subscriptions/listen` streams.

### Bug Fixes

- **Fix nested tool schemas and harden subscriptions**
  Tool input schemas now hoist the `$defs` of `msgspec` Structs and nested Pydantic models to the schema root so their `$ref` entries resolve, and `attrs` classes are described by their fields. Tool schemas are built once per handler instead of on every request. The cross-worker subscription listener skips undecodable events and resubscribes after a backend error instead of stopping silently, and streams closed at shutdown receive their terminal response. `schema_dump` caches encoder maps by content and returns JSON-safe values for Pydantic models. Unexpected JSON-RPC handler errors no longer send the exception message to the client.

- **Close streamed work and use native stdio lifespan**
  Request progress and A2A responses use bounded channels with backpressure and producer-owned iterator cleanup. Configurable cleanup deadlines report incomplete cancellation. Stdio enters native `Litestar.lifespan()`; the public manual `app_lifespan` helper is removed. Its shutdown timeout applies after successful startup; application hooks own bounded, shielded startup unwind.

---

## v0.13.2

### Bug Fixes

- **Resolve tool wire names from `Parameter(name=...)`** ([#93](https://github.com/cofin/litestar-mcp/pull/93), closes [#92](https://github.com/cofin/litestar-mcp/issues/92))
  A parameter whose wire alias came from `QueryParameter(name=...)` or `ParameterKwarg(name=...)` was advertised and dispatched under its Python name, so Litestar never read it and the handler ran as if the argument had never been sent. This made every `advanced-alchemy` filter provider silently inert over MCP. Wire names now resolve from `.name`, and header/cookie parameters are excluded from query wire-name resolution. Advertised names change for affected parameters (for example `category_name_in` becomes `categoryNameIn`). For compatibility, the Python name is still accepted at dispatch and rewritten to the wire name, logging a warning; the wire name wins when both are sent.

### Miscellaneous

- **Correct the contributor clone URL** ([#91](https://github.com/cofin/litestar-mcp/pull/91))
  The README development instructions now clone the canonical `cofin/litestar-mcp` repository.

---

## v0.13.1 (2026-08-24)

### Bug Fixes

- **Preserve native dispatch for controllers and request bodies** ([#94](https://github.com/cofin/litestar-mcp/pull/94))
  MCP tools declared on controllers now resolve path parameters without requiring route handlers to support weak references. Path metadata is cached per application so separate Litestar applications remain isolated. Omitted request bodies now use the handler's declared default, while explicit falsey JSON bodies such as `null`, `false`, `0`, empty strings, empty lists, and empty objects are passed through unchanged.

---

## v0.13.0 (2026-08-19)

### Features

- **Configure MCP router options and discovery route ownership** ([#88](https://github.com/cofin/litestar-mcp/pull/88))
  Added `MCPConfig.route_opt` for mounted-router policies and separate controls for registering the OAuth protected-resource metadata and agent-card discovery routes. Applications can now let another plugin own the RFC 9728 root path without a duplicate-route startup failure.

---

## v0.12.0 (2026-07-29)

### Breaking Changes

- **Stateless MCP 2026-07-28**
  MCP is now POST-only and request-scoped. The initialize handshake, sessions, ping, GET/DELETE transport handlers, replay, and the experimental MCP server manifest were removed. Every request carries protocol, client, method, name, and custom-header metadata; use `server/discover` for capabilities.

### Features

- **Tasks, subscriptions, and MRTR**
  Added filtered `subscriptions/listen` streams, the opt-in `io.modelcontextprotocol/tasks` extension, durable Litestar Store task records, and typed multi-round-trip `input_required` results for tools, resources, and prompts.

- **Concurrent stdio bridge**
  The bridge now forwards independent request-scoped POST streams concurrently, multiplexes subscription responses, maps cancellation to stream closure, and lazily maps annotated tool parameters to MCP headers.

---

## v0.11.1

### Bug Fixes

- **Hide MCP plugin discovery routes from OpenAPI by default**
  `MCPConfig.include_in_schema=False` now hides the plugin-owned `/.well-known/oauth-protected-resource`, `/.well-known/agent-card.json`, and `/.well-known/mcp-server.json` routes from generated OpenAPI schemas, matching the existing default behavior for the `/mcp` transport route. Marked application routes remain visible in OpenAPI unless the application hides them separately, and `include_in_schema=True` opts all MCP plugin routes back in.

---

## v0.11.0

### Features

- **Support binary resources and explicit tool content blocks**
  Adds `MCPResourceLink`, `MCPBlobResource`, and `MCPToolResult` helpers for protocol-shaped tool results. Marked resources can now advertise MIME metadata with `mcp_resource_mime_type` or `mcp_resource(..., mime_type=...)` and `resources/read` returns binary handler responses as base64 `blob` contents. Embedded blobs are capped by `MCPConfig.max_blob_bytes` before encoding; set it to `None` to rely on deployment or client limits instead.

---

## v0.10.0

### Features

- **Add a Litestar CLI stdio bridge for Streamable HTTP** ([#79](https://github.com/cofin/litestar-mcp/pull/79))
  Adds `litestar --app my_app:app mcp bridge` and the `litestar-mcp[bridge]` extra for stdio-only MCP clients that need to reach a running Litestar MCP app. The command is registered by the `LitestarMCP` CLI plugin, defaults to the loaded app's `MCPConfig.base_path`, and supports base URL overrides, explicit endpoints, discovery from `/.well-known/mcp-server.json`, static headers, token providers, stream errors, and session cleanup. It shields stdio output from accidental application `print` calls, reports unreachable endpoints with a bridge-owned error message, and caps each stdin JSON-RPC message at 16 MiB by default, configurable with `--max-message-size`. The bridge is implemented directly on `httpx` and optional `httpx-sse` and does not depend on the official `mcp` Python SDK or its server-side transport dependencies.

- **Support authenticated stdio principals** ([#76](https://github.com/cofin/litestar-mcp/pull/76), closes [#74](https://github.com/cofin/litestar-mcp/issues/74))
  Adds public `MCPStdioContext` support for `MCP.run(transport="stdio", stdio_context=...)`. Standalone stdio dispatch now seeds synthetic Litestar scopes with `user`, `auth`, copied `session`, copied `state`, and a resolved `owner_id` so tools, resources, prompts, guards, and task execution can run on behalf of a process-local principal.

- **Allow custom auth headers and token prefixes** ([#77](https://github.com/cofin/litestar-mcp/pull/77), closes [#75](https://github.com/cofin/litestar-mcp/issues/75))
  `MCPAuthBackend` now accepts `header_name` and `token_prefix` options. Existing `Authorization: Bearer` deployments keep the default behavior, while identity proxy deployments such as Google Cloud IAP and AWS ALB OIDC can validate raw or custom-prefixed JWTs from their provider-specific headers.

### Bug Fixes

- **Honor custom base paths in standalone internal routes** ([#79](https://github.com/cofin/litestar-mcp/pull/79))
  Standalone `MCP.tool()`, `MCP.resource()`, and `MCP.prompt()` internal dispatch routes now follow `MCPConfig.base_path` instead of hard-coding `/mcp/internal/...`.

- **Return the correct SSE content type for GET streams** ([#79](https://github.com/cofin/litestar-mcp/pull/79))
  `GET /mcp` now advertises `text/event-stream` so strict SSE clients accept the Streamable HTTP event stream.

- **Reduce routine lifecycle log noise** ([#76](https://github.com/cofin/litestar-mcp/pull/76), closes [#73](https://github.com/cofin/litestar-mcp/issues/73))
  Routine startup, registry callback, and router-invalidation lifecycle messages are now logged at debug level instead of warning level.

### Miscellaneous

- **Align reference and security documentation** ([#78](https://github.com/cofin/litestar-mcp/pull/78), [#79](https://github.com/cofin/litestar-mcp/pull/79))
  Adds reference coverage for `MCP`, `MCPStdioContext`, auth helpers, tool-call callback protocols, and the bridge API. Also adds security guidance for domain authorization, bridge identity boundaries, and safe file/path arguments.

---

## v0.9.0 (2026-06-29)

### Breaking Changes

- **Tighten standalone transport internals** ([#70](https://github.com/cofin/litestar-mcp/pull/70))
  Internal transport callers now pass a `RequestContext` to `JSONRPCRouter.dispatch()`. `litestar_mcp.routes.build_jsonrpc_router` is no longer exported, and handler-signature helpers moved to `litestar_mcp.utils.handler_signature`. Applications should prefer the public `MCP` and `LitestarMCP` entry points instead of private router construction.

### Features

- **Add a standalone MCP application surface** ([#70](https://github.com/cofin/litestar-mcp/pull/70), closes [#71](https://github.com/cofin/litestar-mcp/issues/71), [#72](https://github.com/cofin/litestar-mcp/issues/72))
  Adds `MCP(...)` for applications whose primary surface is an MCP server. Standalone apps can register `@mcp.tool`, `@mcp.resource`, and `@mcp.prompt` callables, lazily expose `mcp.app`, pass normal Litestar app and route-handler options, run SSE through the Litestar CLI, and run line-delimited stdio JSON-RPC while manually driving ASGI lifespan.

### Miscellaneous

- **Clean up runtime, docs, and optional dependency internals** ([#70](https://github.com/cofin/litestar-mcp/pull/70))
  Refreshes examples, snippet coverage, optional Dishka dependency-key handling, router caching/session plumbing, framework-native serialization paths, release tooling, and Python 3.14 CI coverage.

---

## v0.8.0 (2026-06-24)

### Features

- **Add tool-call observability callbacks** ([#69](https://github.com/cofin/litestar-mcp/pull/69), closes [#68](https://github.com/cofin/litestar-mcp/issues/68))
  Adds `MCPConfig.before_tool_call` and `MCPConfig.after_tool_call` hooks around `tools/call` dispatch for audit, metrics, and tracing use cases. The after hook receives either the result or exception plus elapsed dispatch duration, and hook failures are logged without changing tool-call behavior.

### Bug Fixes

- **Exclude Dishka-resolved provider params from tool inputs** ([#69](https://github.com/cofin/litestar-mcp/pull/69), closes [#67](https://github.com/cofin/litestar-mcp/issues/67))
  Litestar `Provide(...)` factory parameters whose annotated type can be resolved from `app.state.dishka_container` are no longer emitted as MCP tool arguments. Ordinary provider-declared inputs, such as pagination and filter values, still appear in schemas and dispatch.

---

## v0.7.2 (2026-06-11)

### Bug Fixes

- **Include provider-declared query parameters in MCP schemas** ([#65](https://github.com/cofin/litestar-mcp/pull/65), closes [#64](https://github.com/cofin/litestar-mcp/issues/64))
  Query parameters declared on Litestar dependency providers now appear in MCP tool input schemas and are forwarded during tool execution, so provider-backed pagination and aliases are discoverable and callable by MCP clients.

---

## v0.7.1 (2026-06-09)

### Bug Fixes

- **Fix published project URLs** ([#60](https://github.com/cofin/litestar-mcp/pull/60))
  Corrects package metadata links so the project URL points at the GitHub repository and the documentation URL points at the published docs site.

- **Return 202 for accepted Streamable HTTP notifications** (closes [#61](https://github.com/cofin/litestar-mcp/issues/61))
  Accepted JSON-RPC notifications over MCP Streamable HTTP now return `202 Accepted` with an empty body, matching the MCP transport requirement for accepted JSON-RPC notifications and responses sent via POST.

- **Apply filters to direct tool and resource invocation** (closes [#62](https://github.com/cofin/litestar-mcp/issues/62))
  `include_*` and `exclude_*` filters now gate direct `tools/call` and `resources/read` invocation in addition to list responses. A filtered tool, resource, or resource template is treated like an unknown name or URI. Filters narrow the MCP exposure surface; Litestar guards and auth middleware remain the access-control boundary.

---

## v0.7.0 (2026-06-07)

### Features

- **Paginate MCP list methods** ([#58](https://github.com/cofin/litestar-mcp/pull/58), closes [#47](https://github.com/cofin/litestar-mcp/issues/47))
  Adds opaque cursor pagination for MCP list methods so clients can page through tools, resources, resource templates, and prompts using `nextCursor`.

### Bug Fixes

- **Converge handler signature introspection** ([#58](https://github.com/cofin/litestar-mcp/pull/58), closes [#49](https://github.com/cofin/litestar-mcp/issues/49))
  Uses the same handler-signature introspection path for schema generation and execution-time argument handling, reducing drift between advertised input schemas and accepted call arguments.

- **Document the MCP primitive error contract** ([#58](https://github.com/cofin/litestar-mcp/pull/58), closes [#48](https://github.com/cofin/litestar-mcp/issues/48))
  Locks and documents the primitive-level error contract for tools, resources, and prompts. Handler HTTP status is preserved in error data where relevant instead of minting non-standard JSON-RPC codes.

### Miscellaneous

- **Document MCP Prompts end-to-end** ([#58](https://github.com/cofin/litestar-mcp/pull/58), closes [#56](https://github.com/cofin/litestar-mcp/issues/56))
  Adds Prompts coverage across the usage guide, API reference, README, and task-manager example.

---

## v0.6.0 (2026-06-04)

### Features

- **Add MCP Prompts support** ([#46](https://github.com/cofin/litestar-mcp/pull/46))
  Adds MCP Prompts support, including prompt discovery and retrieval via `prompts/list` and `prompts/get`.

### Bug Fixes

- **Unwrap `Annotated` parameters in input schemas** ([#53](https://github.com/cofin/litestar-mcp/pull/53), closes [#52](https://github.com/cofin/litestar-mcp/issues/52))
  Unwraps `Annotated[T, Parameter(...)]` declarations when generating MCP tool input schemas so Litestar parameter metadata does not hide the underlying value type.

### Miscellaneous

- **Update for Litestar 3 deprecations** ([#54](https://github.com/cofin/litestar-mcp/pull/54))
  Updates runtime, docs, and tests for Litestar 3 deprecation paths.

---

## v0.5.1 (2026-04-19)

### Bug Fixes

- **Restore full Litestar execution parity** ([#44](https://github.com/cofin/litestar-mcp/pull/44), closes [#41](https://github.com/cofin/litestar-mcp/issues/41), [#42](https://github.com/cofin/litestar-mcp/issues/42), [#43](https://github.com/cofin/litestar-mcp/issues/43))
  Runs MCP tool execution through the full Litestar request lifecycle for hooks, renamed fields, and path-parameter coercion.

---

## v0.5.0 (2026-04-19)

### Breaking Changes

- **Remove scope enforcement and `auth` extra** ([#40](https://github.com/cofin/litestar-mcp/pull/40))
  Removes inline scope enforcement so scopes are discovery metadata and Litestar guards are the access-control surface. The legacy `auth` extra was removed and auth dependencies now install with the core package.

### Features

- **Add consumer-readiness features** ([#40](https://github.com/cofin/litestar-mcp/pull/40))
  Adds structured tool/resource descriptions, resource templates, `resources/templates/list`, `completion/complete`, injectable JWKS cache support, and well-known discovery documents.

### Miscellaneous

- **Refresh docs and examples for consumer usage** ([#40](https://github.com/cofin/litestar-mcp/pull/40))
  Raises the supported Python floor to 3.10, renames bundled examples to `hello_world` and `task_manager`, adds example tests and snippet markers, and updates docs to prefer Litestar route kwargs such as `mcp_tool="name"`.

---

## v0.4.0 (2026-04-16)

### Features

- **Switch to Streamable HTTP** ([#32](https://github.com/cofin/litestar-mcp/pull/32))
  Replaces the legacy REST endpoint surface with MCP Streamable HTTP, using `GET /mcp` for SSE and `POST /mcp` for JSON-RPC requests.

### Miscellaneous

- **Add database integration test matrix** ([#34](https://github.com/cofin/litestar-mcp/pull/34))
  Adds database-backed integration coverage for Advanced Alchemy, SQLSpec, Dishka, and auth-mode combinations.

---

## v0.3.0 (2026-03-22)

### Features

- **Align with MCP JSON-RPC, transport, and auth specs** ([#13](https://github.com/cofin/litestar-mcp/pull/13))
  Adds MCP spec compliance for JSON-RPC 2.0, Streamable HTTP, and OAuth auth bridging.

---

## v0.2.2 (2025-09-30)

### Bug Fixes

- **Remove duplicate CLI `pass_context` decorator** ([#6](https://github.com/cofin/litestar-mcp/pull/6))
  Removes a duplicate `pass_context` decorator from CLI commands.

---

## v0.2.1 (2025-09-28)

### Features

- **Add Litestar CLI plugin integration** ([#5](https://github.com/cofin/litestar-mcp/pull/5))
  Implements Litestar CLI plugin integration.

---

## v0.2.0 (2025-09-27)

### Features

- **Add CLI interface** ([#4](https://github.com/cofin/litestar-mcp/pull/4))
  Adds the initial command-line interface.

---

## v0.1.0 (2025-09-06)

### Features

- **Initial release**
  Adds the initial Litestar MCP plugin with route marking via `mcp_tool` and `mcp_resource` kwargs, automatic route discovery, REST-based MCP endpoints, OpenAPI schema exposure, `MCPConfig`, and support for tools and resources.
