---
title: System Architecture & Interactive Diagrams
---

# System Architecture & Interactive Diagrams

`litestar-mcp` unifies Model Context Protocol (MCP) servers, durable background task and DAG workflow execution, multi-agent orchestration, and Agent-to-Agent (A2A 1.0) interoperability inside a single Litestar ASGI lifecycle.

This page collects all six interactive architectural diagrams and links each subsystem to its implementation guide and API reference.

---

## Request & Transport Dispatch Pipeline

`MCPController` mounts `POST /mcp`, `GET /mcp` (with `Accept: text/event-stream`, gated by `MCPConfig.enable_get_sse` and `MCPConfig.allowed_origins`), and `DELETE /mcp` at `MCPConfig.base_path` (default `/mcp`), delegating JSON-RPC framing and negotiation across `SUPPORTED_PROTOCOL_VERSIONS = ("2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26")` to `MCPHandlerService`. Stateless `2026-07-28` requests (`server/discover`, SEP-2575 routing headers, and `POST subscriptions/listen`) remain headerless with respect to session state, while session-aware `2025-11-25`, `2025-06-18`, and `2025-03-26` clients receive an owner-bound `Mcp-Session-Id` (`MCPSessionStore`) on `initialize`, listen on `GET /mcp` SSE streams, manage `resources/subscribe` and `resources/unsubscribe` (honoring `MCPConfig.subscription_authorizer`), adjust RFC 5424 server log verbosity via `logging/setLevel` (`await MCPRequestContext.log(...)` emitting `notifications/message`), call `ping`, and close sessions via `DELETE /mcp`. Terminal tools (`litestar mcp stdio`, `bridge`, `export-tool-spec`, `list-tools`, `list-resources`, `list-skills`, and `run`) reuse the same compiled registry and ASGI lifecycle.

When a client calls `tools/call` or `prompts/get`, `MCPHandlerService` dispatches an in-memory ASGI request through the host `Litestar` router rather than opening a loopback TCP socket or bypassing the framework. Your authentication middleware, route guards, `Provide` factories, Dishka `FromDishka[T]` providers, DTO validators, lifecycle hooks, and OpenTelemetry spans execute on every invocation.

--8<-- "tools/docs/includes/diagram-request-pipeline.html"

- **Deep-Dive Guides**: [Core Architectural Concepts](../getting-started/concepts.md) · [Streamable HTTP & Discovery](../learn/discovery.md) · [Stdio & HTTP Bridge](../learn/stdio-and-bridge.md)
- **API Reference**: [`LitestarMCP` & `MCPConfig`](../reference/plugin-and-config.md)

---

## Primitives & Discovery Compilation

At application startup (`on_app_init`), `LitestarMCP` inspects every registered Litestar route handler, `PromptController`, `SkillController`, and `MCPSkillsConfig` source to populate the central `Registry` and `SkillCatalog`:

- **Tools**: Compiles route signatures, `msgspec.Struct`, Pydantic, `@dataclass`, and `TypedDict` models, and `Provide` dependencies into JSON Schema 2020-12 via `generate_schema_for_handler` while stripping internal DI parameters (`Request`, `State`, `ToolContext`, and Dishka-resolved types). Synthesizes opt-in `outputSchema` (`MCPConfig.auto_output_schema`, `output_schema=`, `mcp_output_schema`, hoisting root `msgspec.Struct` `$ref` schemas to `"type": "object"`) with automatic `structuredContent` population, infers `mcp_annotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`) from HTTP verbs, and attaches `SEP-1865` presentation and UI metadata (`UI_EXTENSION = "io.modelcontextprotocol/ui"`, `_meta.ui`, `title`, `icons`).
- **Resources & Templates**: Separates static custom-scheme URIs (`uri="ui://..."`, `docs://...`, `app://...` in `resources/list`) from parameterized `{var}` RFC 6570 Level 1 templates (`resources/templates/list`), preserves raw non-charset MIME parameters such as `UI_MIME_TYPE = "text/html;profile=mcp-app"` along with `contents[0]._meta`, and manages owner-scoped `resources/subscribe` and `POST subscriptions/listen` notifications gated by `MCPConfig.subscription_authorizer`.
- **Prompts**: Extracts `PromptArgument` and `_meta` (`meta`) descriptors from route handlers or `PromptController` methods, normalizes return values into MCP message sequences, and optionally projects visible skills into `prompts/list` and `prompts/get` when `MCPSkillsConfig(expose_as_prompts=True)` is set.
- **Agent Skills (`SEP-2640`)**: Exports `SkillCatalog` at `litestar_mcp.SkillCatalog` with request-optional DI (`skill_catalog`), composing static `SKILL.md` directories (`FilesystemSkillSource`), dynamic request-aware `SkillSource` providers, and `SkillController` endpoints under `io.modelcontextprotocol/skills` (`skills/list` with tag/query filtering, `skills/get`, `notifications/skills/list_changed` when `list_changed=True`), `skill://<name>/...` resources, and `litestar mcp list-skills`.

--8<-- "tools/docs/includes/diagram-primitives-and-discovery.html"

- **Deep-Dive Guides**: [Marking Routes & Decorators](../learn/marking-routes.md) · [Tools & Annotations](../learn/tools.md) · [Resources & Templates](../learn/resources.md) · [Prompts & PromptController](../learn/prompts.md) · [Agent Skills (SEP-2640)](../learn/skills.md)
- **API Reference**: [Primitives & Skills API](../reference/primitives-and-skills.md)

---

## MCP Tasks & Distributed Queue Execution

The `io.modelcontextprotocol/tasks` capability lets clients request asynchronous execution on any tool by supplying `params.task`. `MCPTaskStore` persists `TaskRecord` metadata (including `progress_token`, bound `session_id`, and RFC 5424 `log_level`) in the `litestar_mcp_tasks` store namespace so promoted tasks forward `notifications/progress` and `MCPRequestContext.log()` (`notifications/message`) events seamlessly across executors:

- **`QueueTaskExecutor` (`litestar-mcp[queues]`)**: Auto-attaches when `QueuePlugin` from `litestar-queues` is present on the Litestar app (or when passed explicitly to `MCPTaskConfig(executor=...)`). Captures a `PrincipalSnapshot` (`request.user`, `request.auth`, `litestar-security` `SecurityContext` scopes/roles, and Dishka `Scope.REQUEST` context) alongside task progress and logging metadata so background worker replicas rehydrate the exact caller identity, stream progress updates, and honor cooperative `tasks/cancel` polling plus multi-round `MCPInputRequiredResult` / `tasks/update` human-in-the-loop resume across distinct replicas.
- **Built-in In-Process Task Execution (`executor="in_process"`)**: Default zero-dependency task runner in `MCPTaskStore` / `MCPHandlerService` using an in-process AnyIO task group for local development, stdio servers, and single-worker deployments while forwarding `progress_token` and session log streams identically.

--8<-- "tools/docs/includes/diagram-mcp-tasks-and-queues.html"

- **Deep-Dive Guides**: [MCP Tasks & Human-in-the-Loop](../queues/mcp-tasks.md) · [Distributed Task Queues (`QueueTaskExecutor`)](../queues/distributed-task-queues.md) · [Multi-Replica Deployment](../queues/deployment.md)
- **API Reference**: [Tasks, Queues & Workflows API](../reference/tasks-and-queues.md)

---

## Durable DAG Workflows (DurableWorkflowEngine)

`DynamicWorkflow` defines a Directed Acyclic Graph (DAG) of `WorkflowNode` steps (wrapping either an `Agent` or an async Python callable) with topological wave dependencies and conditional `WorkflowNode(condition=...)` branching.

The same `DynamicWorkflow` definition runs in-process via `WorkflowEngine` or across distributed worker replicas via `DurableWorkflowEngine` on `litestar-queues`:

- **Conflict-Free Persistence (`WorkflowStore`)**: Persists a write-once `NodeRecord` per completed node (`{prefix}:{run_id}:node:{node_name}`) and a monotonic `WorkflowRun` document in `litestar_mcp_workflows`, preventing sibling fan-in write races.
- **Forever-Unique Node Scheduling**: Schedules ready nodes with `unique_key="wf-node:{run_id}:{node}"` and `unique_until="forever"`. If a node fails transiently and retries via `litestar-queues`, `DurableWorkflowEngine` releases the reservation with `service.reset_task_identity()` before re-raising.
- **Self-Rearming Per-Run Watchdog**: Schedules `wf-watchdog:{run_id}:{tick}` tasks that inspect `ready_nodes()` and heal stalled runs if a worker process crashes mid-wave.

--8<-- "tools/docs/includes/diagram-durable-workflows.html"

- **Deep-Dive Guides**: [Durable DAG Workflows](../queues/durable-workflows.md) · [Tasks, Queues & Workflows Overview](../queues/index.md)
- **API Reference**: [`DurableWorkflowEngine` & `WorkflowStore`](../reference/tasks-and-queues.md) · [`DynamicWorkflow` & `WorkflowEngine`](../reference/agents-and-workflows.md)

---

## Multi-Agent Runtime, Handoffs & Protocol Bridges

`AgentRuntime` manages `Agent` and `AgentGroup` execution inside your Litestar application:

- **`TurnBudget` & `GoogleGenAIClient`**: Enforces per-turn ceilings (`max_turns`, `max_tool_calls`, `timeout_seconds`) while driving `ModelClient` implementations. `GoogleGenAIClient` defaults to `gemini-3.8-flash` and preserves Gemini 3/2.5 `thought_signature` bytes across multi-turn tool calls.
- **`AgentGroup` Handoffs**: Coordinates a primary agent and specialist sub-agents by injecting a synthetic `Literal`-typed `transfer_to_agent` tool without mutating the underlying `Agent.tools` lists.
- **Owner-Scoped Sessions & SSE Streaming**: `SessionStore` (`MemorySessionStore` or `LitestarStoreSessionStore`) isolates conversation history by authenticated `owner_id`. `AgentChatController` and `QueuedAgentChatController` expose ready-to-mount REST and Server-Sent Events (SSE) endpoints.
- **Bidirectional Protocol Bridges**: `agent_to_mcp`, `mcp_to_tools(..., include_app_only=False)`, `discover_mcp_tools(..., include_app_only=False)`, and `agent_to_a2a` connect Litestar routes, remote MCP servers, local agents, and A2A 1.0 endpoints via in-process `execute_tool` ASGI DI dispatch while honoring `MCPConfig` operation/tag filters and `SEP-1865` `_meta.ui.visibility` rules.

--8<-- "tools/docs/includes/diagram-agent-runtime-and-bridges.html"

- **Deep-Dive Guides**: [Agent Runtime & Turn Budgets](../agents/runtime-and-budgets.md) · [Agent Groups & Handoffs](../agents/groups-and-handoffs.md) · [Sessions & SSE Streaming](../agents/sessions-and-streaming.md) · [Queued Agent Turns](../queues/queued-agent-turns.md) · [Protocol Bridges](../agents/protocol-bridges.md)
- **API Reference**: [Agents & In-Process Workflows API](../reference/agents-and-workflows.md) · [A2A 1.0 & Protocol Bridges API](../reference/a2a-and-bridges.md)

---

## A2A 1.0 Interoperability & Multi-Tenancy (LitestarA2A)

`LitestarA2A` (`litestar-mcp[a2a]`) integrates the official `a2a-sdk` 1.0 runtime (`AgentCard`, `AgentSkill`, `AgentExecutor`, `DefaultRequestHandler`, `TaskStore`, `ServerCallContext`) directly into Litestar:

- **Dual Transport Bindings**: Serves JSON-RPC 2.0 at `POST /a2a` and, when `enable_http_json=True`, mounts native A2A 1.0 `HTTP+JSON` REST routes (`POST /a2a/message:send`, `POST /a2a/message:stream`, `GET /a2a/tasks`, `GET /a2a/tasks/{id}`, `POST /a2a/tasks/{id}:cancel`, `POST /a2a/tasks/{id}:subscribe`, and push notification config endpoints) while advertising both interfaces in `/.well-known/agent-card.json`.
- **Split Route Security**: Applies `A2AConfig.route_opt` to execution routes while keeping discovery public (or separately scoped) via `A2AConfig.agent_card_route_opt`.
- **Multi-Tenant Context Resolution**: Extracts tenant identity with precedence `request payload > /{tenant:str} URL path > X-A2A-Tenant header` and populates `ServerCallContext.tenant` via `A2AConfig.context_builder`.

--8<-- "tools/docs/includes/diagram-a2a-interoperability.html"

- **Deep-Dive Guides**: [A2A 1.0 Protocol & HTTP+JSON Binding](../agents/a2a-protocol.md) · [Multi-Tenant A2A Routing](../agents/multi-tenancy.md) · [First-Party `litestar-security` Integration](../security/litestar-security.md)
- **API Reference**: [`LitestarA2A` & `A2AConfig`](../reference/a2a-and-bridges.md)

---

## Runnable Reference Examples & PEP 723 Single-File Apps

Every subsystem above is accompanied by runnable, pytest-verified reference applications and single-file PEP 723 scripts:

- **[Reference Examples & Notes Matrix](reference-examples.md)**: Full comparison of the `advanced_alchemy` and `sqlspec` Notes reference application families across 10 database/auth variants (`no_auth`, `jwt_auth`, `cloud_run_jwt`, `google_iap`).
- **[Single-File `uvx` & PEP 723 Guide](uvx-and-pep723.md)**: Zero-setup execution of standalone `litestar-mcp` scripts using inline PEP 723 dependency metadata, `uv run`, and `litestar mcp bridge`.
