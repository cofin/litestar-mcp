---
title: Distributed Task Queues
---

# Distributed Task Queues (`QueueTaskExecutor`)

When `litestar-mcp[queues]` is installed and `litestar-queues` `QueuePlugin` is registered on your `Litestar` application, `LitestarMCP` automatically distributes background MCP Tasks across `litestar-queues` workers via `QueueTaskExecutor`.

--8<-- "tools/docs/includes/diagram-mcp-tasks-and-queues.html"

---

## Zero-Config Auto-Enabling

Registering `QueuePlugin` and `LitestarMCP` on the same `Litestar` application is all that is required:

```python title="docs/examples/snippets/mcp_tasks_queue.py"
--8<-- "docs/examples/snippets/mcp_tasks_queue.py:example"
```

During `on_app_init`, `LitestarMCP._configure_task_runtime()` inspects `app_config.plugins`:

1. If a `QueuePlugin` is present and `MCPConfig.tasks` is not explicitly `False`, `LitestarMCP` enables `MCPTaskConfig` automatically.
2. When `MCPTaskConfig.executor` is `None`, `LitestarMCP` instantiates `QueueTaskExecutor()` and calls `executor.configure(app_config)`, which attaches `QueueTaskExecutor.resolve_task_dependencies` to `QueuePlugin` through `TaskDependencyChain`.
3. If `MCPTaskConfig.store` is `None`, `MCPTaskStore` binds to the `"litestar_mcp_tasks"` store entry in the application's `StoreRegistry` (`Litestar(stores=...)`).
4. On application startup (`on_startup`), `LitestarMCP` binds `MCPTaskRuntime(app=app, task_store=self._task_store, run_attempt=service.run_task_attempt)` onto `QueueTaskExecutor`.

---

## Explicit QueueTaskExecutor Configuration

To customize the default queue name, per-attempt execution timeout, or custom principal loader callback, pass an explicit `QueueTaskExecutor` to `MCPTaskConfig(executor=...)`:

```python
from typing import Any
from litestar import Litestar
from litestar_queues import QueuePlugin

from litestar_mcp import LitestarMCP, MCPConfig, MCPTaskConfig
from litestar_mcp.queues import QueueTaskExecutor


async def load_user_from_db(owner_id: str) -> dict[str, Any]:
    """Load an application user model from the task owner identifier."""
    raw_id = owner_id.removeprefix("user:")
    return {"id": raw_id, "roles": ["analyst"]}


app = Litestar(
    plugins=[
        QueuePlugin(),
        LitestarMCP(
            MCPConfig(
                tasks=MCPTaskConfig(
                    executor=QueueTaskExecutor(
                        queue="mcp_default",
                        timeout=1800,
                        principal_loader=load_user_from_db,
                    ),
                ),
            ),
        ),
    ],
)
```

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `queue` | `str` | `"mcp"` | Default `litestar-queues` queue lane for `run_mcp_tool_task` (`litestar_mcp.mcp.run_tool_task`) attempts when not overridden on the controller or route. |
| `timeout` | `int` | `3600` | Maximum duration in seconds allowed for a single tool execution attempt before the worker times out the job. |
| `principal_loader` | `Callable[[str], Awaitable[Any]] or None` | `None` | Optional async callback receiving `ToolTaskCall.owner_id` and returning the user object to bind to `request.scope["user"]` in the worker. When omitted, `LitestarMCP` rehydrates `user`, `auth`, and `litestar_security` automatically from the captured snapshot and route dependencies. |

---

## Plugin, Controller & Route Overrides

You can control task execution and queue routing at every layer of your Litestar application:

| Layer | Configuration | Effect |
| :--- | :--- | :--- |
| **Plugin (Disable Tasks)** | `MCPConfig(tasks=False)` | Disables the `io.modelcontextprotocol/tasks` extension completely, even when `QueuePlugin` is registered on the application. |
| **Plugin (Force In-Process)** | `MCPConfig(tasks=MCPTaskConfig(executor="in_process"))` (or `executor=False`) | Keeps `io.modelcontextprotocol/tasks` enabled using process-local `asyncio.Task` runners instead of auto-wiring `QueueTaskExecutor`. |
| **Router / Controller (`opt`)** | `Controller.opt = {"mcp_task": True, "mcp_queue": "heavy"}` | Enables task promotion and routes all tools on the controller to the `"heavy"` queue lane unless overridden on a specific handler. |
| **Route / Decorator (Custom Queue)** | `@mcp_tool(task=True, queue="heavy")` or `@post(..., mcp_task=True, mcp_queue="heavy")` | Routes task attempts for this specific tool to the `"heavy"` queue lane (`ToolTaskCall.queue = "heavy"`). |
| **Route / Decorator (Force In-Process)** | `@mcp_tool(task=True, queue=False)` or `@mcp_tool(task=True, queue="in_process")` | Forces this specific task-capable tool to run in-process within the web worker even when `QueueTaskExecutor` is active globally. |

```python
from litestar import Controller, post

from litestar_mcp import mcp_tool


class AnalyticsController(Controller):
    """Controller routing background MCP tasks to the heavy worker lane by default."""

    path = "/analytics"
    opt = {"mcp_task": True, "mcp_queue": "heavy"}

    @post("/backfill", mcp_tool="run_backfill")
    async def run_backfill(self, dataset: str) -> dict[str, str]:
        """Run on the controller's default 'heavy' queue."""
        return {"dataset": dataset, "status": "backfilled"}

    @mcp_tool("quick_ping", task=True, queue=False)
    @post("/ping")
    async def quick_ping(self) -> dict[str, str]:
        """Override the controller queue to run as an in-process background task."""
        return {"status": "ok"}
```

---

## Automatic Security, Principal & Dependency Rehydration in Workers

When a task-augmented `tools/call` runs in a separate `litestar-queues` worker process (`litestar queues run` / `litestar --app app:app queues run`), the original HTTP connection is no longer present. `LitestarMCP` and `QueueTaskExecutor` automatically capture the caller's security context at enqueue time and reconstruct an internal ASGI request scope inside the worker:

```mermaid
flowchart LR
    Call["POST /mcp (tools/call)"] --> Snap["extract_principal_snapshot (ToolTaskCall)"]
    Snap --> Queue["litestar-queues (mcp-task:{id}:{attempt})"]
    Queue --> Scope["Worker blank_http_scope + rehydrate_security_scope"]
    Scope --> Handler["Route Guards + Provide + Dishka Scope.REQUEST"]
```

### Principal & SecurityContext Snapshot

Before submitting a `ToolTaskCall` to `QueueTaskExecutor`, `MCPHandlerService` calls `extract_principal_snapshot(scope.get("user"), scope.get("auth"), scope.get("litestar_security"), context.owner_id)`:

- **User Attributes & Typed Structs**: Extracts standard identity fields (`id`, `sub`, `user_id`, `account_id`, `username`, `name`, `email`, `is_active`, `roles`, `scopes`, `permissions`, `hives`, `tenant`, `tenant_id`) or serializes `msgspec.Struct` and `@dataclass` user models alongside a `"__user_type__": "module:QualName"` tag.
- **`litestar-security` `SecurityContext`**: When `litestar-security` is active on the request, serializes `Principal` (`account_id`, `subject`, `mechanisms`, `attributes`, `restrictions`), `AuthorizationSnapshot` (`roles`, `permissions`, `scopes`, `attributes`, `tenant_roles`), and `CredentialRestrictions` (`scopes`, `tenant_ids`, `roles`) under `"__litestar_security__"`.
- **Standard `request.auth`**: When custom Litestar authentication middleware populates `scope["auth"]`, serializes the claims payload under `"__auth__"`.

### Worker Scope Rehydration

When the worker claims `run_mcp_tool_task` (`RUN_TOOL_TASK = "litestar_mcp.mcp.run_tool_task"`):

1. `MCPHandlerService.run_task_attempt()` creates a synthesized HTTP scope via `blank_http_scope(app)`.
2. `rehydrate_security_scope(scope, call.principal, call.owner_id)` restores:
   - `scope["user"]`: Reconstructed as the original `msgspec.Struct` / `@dataclass` class (when `__user_type__` is importable), a `litestar-security` `Principal` wrapper, or a `PrincipalProxy` supporting both dictionary (`request.user["id"]`) and attribute (`request.user.id`) lookups.
   - `scope["auth"]` and `scope["litestar_security"]`: Reconstructed as `SecurityContext(principal=..., authorization=AuthorizationSnapshot(...), restrictions=(CredentialRestrictions(...), ...))` (or fallback value objects when `litestar-security` is not installed).
3. **Existing `Provide` User Dependencies**: If `principal_loader` was not supplied on `QueueTaskExecutor`, `_rehydrate_user_from_handler_dependencies()` inspects the target route handler's Litestar `dependencies` for a provider named `current_user`, `user`, or `principal`. If found, it invokes that provider with any matching parameters (`request`, `connection`, `state`, `scope`, `owner_id`, `user_id`, `principal_id`) and binds the resolved domain user onto `request.scope["user"]`.
4. **Litestar `Provide` & Dishka `Scope.REQUEST` Containers**: `execute_tool` runs the synthesized request through the full Litestar handler pipeline. Route guards, `Provide` dependencies, and Dishka `FromDishka[T]` request-scoped containers resolve identically to a live HTTP request.

!!! note
    Standalone `litestar-queues` workers (`WorkerConfig(placement="external")`) must keep `WorkerConfig.run_app_lifespan=True` (the default in `litestar-queues`) so `LitestarMCP.on_startup` binds `MCPTaskRuntime` and initializes application lifespan resources (such as database pools and Dishka containers) before the worker claims tasks.

---

## Internal Job Keys, Cooperative Cancellation & Cross-Replica Resume

`QueueTaskExecutor` coordinates multi-attempt execution and cancellation across stateless web and worker replicas through `MCPTaskStore` and `QueueService`:

### Job Dispatch Keys

- **Task Record Key**: `mcp-task:{task_id}` stores the JSON-encoded `TaskRecord`, including `dispatchInternal` (the serialized `ToolTaskCall` envelope) and `requestStateInternal`.
- **Queue Attempt Key**: Each attempt is enqueued on `litestar-queues` with `key=f"mcp-task:{call.task_id}:{call.attempt}"`, ensuring idempotent scheduling per attempt number (`attempt` starts at `1`).
- **Active Job Pointer Key**: After `QueueService.enqueue()` returns a queue job UUID, `QueueTaskExecutor._enqueue_attempt()` persists that UUID under `mcp-task:{task_id}:job` in `MCPTaskStore` with the task's TTL (`expires_in`).

### Cooperative Cancellation

When ANY web replica receives `tasks/cancel` for `taskId`:

1. `MCPHandlerService.tasks_cancel` delegates to `QueueTaskExecutor.cancel(record)`.
2. `QueueTaskExecutor.cancel` reads the active queue job UUID from `MCPTaskStore.get_job_id(record.task_id)` (`mcp-task:{task_id}:job`).
3. It invokes `await service.cancel_task(UUID(job_id), include_running=True)` on `litestar-queues`.
4. Inside the worker executing `run_mcp_tool_task`, a background watcher coroutine (`_cancel_on_signal`) awaits `get_current_task_context().wait_cancelled()`. When the durable cancellation signal arrives, `_cancel_on_signal` cancels the AnyIO `CancelScope` wrapping `run_attempt`.
5. `MCPTaskStore.mark_cancelled(record.task_id)` transitions the task record to `status="cancelled"` and emits `notifications/tasks`.

### Cross-Replica Input-Required Resumption

Because `QueueTaskExecutor` does not hold open an idle worker slot while waiting for human input:

1. When a tool returns `MCPInputRequiredResult`, the attempt finishes cleanly (`retries=0`) after persisting `status="input_required"`, `inputRequests`, `requestStateInternal`, and `dispatchInternal` (`ToolTaskCall` with `attempt=1`) in the shared `MCPTaskStore`.
2. When the client sends `tasks/update` to **any** web replica, `MCPHandlerService.tasks_update` validates the responses via `task_store.update()`, loads the persisted `ToolTaskCall` from `task_store.get_dispatch(task_id)`, merges `input_responses`, sets `request_state=record.request_state`, increments `attempt=dispatch.attempt + 1`, saves the updated `ToolTaskCall`, and calls `await executor.resume(call)`.
3. `QueueTaskExecutor.resume(call)` enqueues a fresh job under `key=f"mcp-task:{call.task_id}:{call.attempt}"` and updates `mcp-task:{task_id}:job`. Any available worker replica claims the resumed attempt with `get_mcp_request_context().input_responses` and `request_state` populated.
