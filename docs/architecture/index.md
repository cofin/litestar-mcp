---
title: System Architecture & Interactive Diagrams
---

# System Architecture & Interactive Diagrams

`litestar-mcp` unifies Model Context Protocol (MCP) servers, durable background task and DAG workflow execution, multi-agent orchestration, and Agent-to-Agent (A2A 1.0) interoperability inside a single Litestar ASGI lifecycle.

This page collects all six interactive architectural diagrams and links each subsystem to its implementation guide and API reference.

---

## 1. Request & Transport Dispatch Pipeline

`MCPController` mounts at `MCPConfig.base_path` (default `/mcp`) and delegates JSON-RPC framing and version negotiation to `MCPHandlerService`. Both `2026-07-28` stateless discovery (`server/discover` + SEP-2575 headers) and `2025-06-18` initialization (`initialize`, `ping`, `notifications/initialized`, `DELETE /mcp`) share the same registry and internal ASGI execution path.

When a client calls `tools/call` or `prompts/get`, `MCPHandlerService` dispatches an in-memory ASGI request through the host `Litestar` router rather than opening a loopback TCP socket or bypassing the framework. Your authentication middleware, route guards, `Provide` factories, Dishka `FromDishka[T]` providers, DTO validators, lifecycle hooks, and OpenTelemetry spans execute on every invocation.

--8<-- "tools/docs/includes/diagram-request-pipeline.html"

- **Deep-Dive Guides**: [Core Architectural Concepts](../getting-started/concepts.md) · [Streamable HTTP & Discovery](../learn/discovery.md) · [Stdio & HTTP Bridge](../learn/stdio-and-bridge.md)
- **API Reference**: [`LitestarMCP` & `MCPConfig`](../reference/plugin-and-config.md)

---

## 2. Primitives & Discovery Compilation

At application startup (`on_app_init`), `LitestarMCP` inspects every registered Litestar route handler, `PromptController`, `SkillController`, and `MCPSkillsConfig` source to populate the central `Registry`:

- **Tools**: Compiles route signatures, `msgspec.Struct` / Pydantic / `@dataclass` models, and `Provide` dependencies into JSON Schema while stripping internal DI parameters (`Request`, `State`, `ToolContext`, and Dishka-resolved types). Infers `ToolAnnotations` (`readOnlyHint`, `destructiveHint`, `idempotentHint`) from the HTTP verb unless overridden.
- **Resources & Templates**: Separates static URIs (`resources/list`) from parameterized RFC 6570 Level 1 templates (`resources/templates/list`) and manages `resources/subscribe` change notifications.
- **Prompts**: Extracts `PromptArgument` metadata from route handlers or `PromptController` methods and normalizes return values into MCP message sequences.
- **Agent Skills (`SEP-2640`)**: Combines static `SKILL.md` directories (`FilesystemSkillSource`), dynamic request-aware `SkillSource` providers, and `SkillController` endpoints under `io.modelcontextprotocol/skills` (`skills/list`, `skills/load`, `skills/read_file`) and `skills://` fallback resources.

--8<-- "tools/docs/includes/diagram-primitives-and-discovery.html"

- **Deep-Dive Guides**: [Marking Routes & Decorators](../learn/marking-routes.md) · [Tools & Annotations](../learn/tools.md) · [Resources & Templates](../learn/resources.md) · [Prompts & PromptController](../learn/prompts.md) · [Agent Skills (SEP-2640)](../learn/skills.md)
- **API Reference**: [Primitives & Skills API](../reference/primitives-and-skills.md)

---

## 3. MCP Tasks & Distributed Queue Execution

The `io.modelcontextprotocol/tasks` capability lets clients request asynchronous execution on any tool by supplying `params.task`. `MCPTaskStore` persists `TaskRecord` metadata in the `litestar_mcp_tasks` store namespace and delegates execution to a pluggable `MCPTaskExecutor`:

- **`QueueTaskExecutor` (`litestar-mcp[queues]`)**: Auto-attaches when `QueuePlugin` from `litestar-queues` is present on the Litestar app (or when passed explicitly to `MCPTaskConfig(executor=...)`). Captures a `PrincipalSnapshot` (`request.user`, `request.auth`, `litestar-security` `SecurityContext` scopes/roles, and Dishka `Scope.REQUEST` context) so background worker replicas rehydrate the exact caller identity. Supports cooperative `tasks/cancel` polling and multi-round `MCPInputRequiredResult` / `tasks/update` human-in-the-loop resume across distinct replicas.
- **`InProcessTaskExecutor`**: Default zero-dependency executor using an in-process AnyIO `TaskGroup` for local development, stdio servers, and single-worker deployments.

--8<-- "tools/docs/includes/diagram-mcp-tasks-and-queues.html"

- **Deep-Dive Guides**: [MCP Tasks & Human-in-the-Loop](../queues/mcp-tasks.md) · [Distributed Task Queues (`QueueTaskExecutor`)](../queues/distributed-task-queues.md) · [Multi-Replica Deployment](../queues/deployment.md)
- **API Reference**: [Tasks, Queues & Workflows API](../reference/tasks-and-queues.md)

---

## 4. Durable DAG Workflows (`DurableWorkflowEngine`)

`DynamicWorkflow` defines a Directed Acyclic Graph (DAG) of `WorkflowNode` steps (wrapping either an `Agent` or an async Python callable) with topological wave dependencies and conditional `StepResult(next_nodes=[...])` branching.

The same `DynamicWorkflow` definition runs in-process via `WorkflowEngine` or across distributed worker replicas via `DurableWorkflowEngine` on `litestar-queues`:

- **Conflict-Free Persistence (`WorkflowStore`)**: Persists a write-once `NodeRecord` per completed node (`node:{run_id}:{node_name}`) and a monotonic `WorkflowRun` document in `litestar_mcp_workflows`, preventing sibling fan-in write races.
- **Forever-Unique Node Scheduling**: Schedules ready nodes with `unique_key="wf-node:{run_id}:{node}"` and `unique_Window=0`. If a node fails transiently and retries via `litestar-queues`, `DurableWorkflowEngine` releases the reservation with `service.reset_task_identity()` before re-raising.
- **Self-Rearming Per-Run Watchdog**: Schedules `wf-watchdog:{run_id}:{tick}` tasks that inspect `ready_nodes()` and heal stalled runs if a worker process crashes mid-wave.

--8<-- "tools/docs/includes/diagram-durable-workflows.html"

- **Deep-Dive Guides**: [Durable DAG Workflows](../queues/durable-workflows.md) · [Tasks, Queues & Workflows Overview](../queues/index.md)
- **API Reference**: [`DurableWorkflowEngine` & `WorkflowStore`](../reference/tasks-and-queues.md) · [`DynamicWorkflow` & `WorkflowEngine`](../reference/agents-and-workflows.md)

---

## 5. Multi-Agent Runtime, Handoffs & Protocol Bridges

`AgentRuntime` manages `Agent` and `AgentGroup` execution inside your Litestar application:

- **`TurnBudget` & `GoogleGenAIClient`**: Enforces per-turn ceilings (`max_steps`, `max_tool_calls`, `max_tokens`, `max_duration_s`) while driving `ModelClient` implementations. `GoogleGenAIClient` defaults to `gemini-3.8-flash` and preserves Gemini 3/2.5 `thought_signature` bytes across multi-turn tool calls.
- **`AgentGroup` Handoffs**: Coordinates a primary agent and specialist sub-agents by injecting synthetic `transfer_to_<agent>` tools without mutating the underlying `Agent.tools` lists, guarded by `max_handoffs`.
- **Owner-Scoped Sessions & SSE Streaming**: `SessionStore` (`MemorySessionStore` or `LitestarStoreSessionStore`) isolates conversation history by authenticated `owner_id`. `AgentChatController` and `QueuedAgentChatController` expose ready-to-mount REST and Server-Sent Events (SSE) endpoints.
- **Bidirectional Protocol Bridges**: `agent_to_mcp`, `mcp_to_tools`, `discover_mcp_tools`, and `agent_to_a2a` connect Litestar routes, remote MCP servers, local agents, and A2A 1.0 endpoints without duplicate glue code.

--8<-- "tools/docs/includes/diagram-agent-runtime-and-bridges.html"

- **Deep-Dive Guides**: [Agent Runtime & Turn Budgets](../agents/runtime-and-budgets.md) · [Agent Groups & Handoffs](../agents/groups-and-handoffs.md) · [Sessions & SSE Streaming](../agents/sessions-and-streaming.md) · [Queued Agent Turns](../queues/queued-agent-turns.md) · [Protocol Bridges](../agents/protocol-bridges.md)
- **API Reference**: [Agents & In-Process Workflows API](../reference/agents-and-workflows.md) · [A2A 1.0 & Protocol Bridges API](../reference/a2a-and-bridges.md)

---

## 6. A2A 1.0 Interoperability & Multi-Tenancy (`LitestarA2A`)

`LitestarA2A` (`litestar-mcp[a2a]`) integrates the official `a2a-sdk` 1.0 runtime (`AgentCard`, `AgentSkill`, `AgentExecutor`, `DefaultRequestHandler`, `TaskStore`, `ServerCallContext`) directly into Litestar:

- **Dual Transport Bindings**: Serves JSON-RPC 2.0 at `POST /a2a` and, when `enable_http_json=True`, mounts native A2A 1.0 `HTTP+JSON` REST routes (`POST /a2a/message:send`, `POST /a2a/message:stream`, `GET /a2a/tasks`, `GET /a2a/tasks/{id}`, `POST /a2a/tasks/{id}:cancel`, `POST /a2a/tasks/{id}:subscribe`, and push notification config endpoints) while advertising both interfaces in `/.well-known/agent-card.json`.
- **Split Route Security**: Applies `A2AConfig.route_opt` to execution routes while keeping discovery public (or separately scoped) via `A2AConfig.agent_card_route_opt`.
- **Multi-Tenant Context Resolution**: Extracts tenant identity from `/{tenant:str}` path parameters or the `X-A2A-Tenant` request header (with path parameters taking precedence) and populates `ServerCallContext.tenant` via `A2AConfig.context_builder`.

--8<-- "tools/docs/includes/diagram-a2a-interoperability.html"

- **Deep-Dive Guides**: [A2A 1.0 Protocol & HTTP+JSON Binding](../agents/a2a-protocol.md) · [Multi-Tenant A2A Routing](../agents/multi-tenancy.md) · [First-Party `litestar-security` Integration](../security/litestar-security.md)
- **API Reference**: [`LitestarA2A` & `A2AConfig`](../reference/a2a-and-bridges.md)
