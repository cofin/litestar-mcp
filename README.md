<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/banner-inline-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset="docs/assets/banner-inline-light.svg">
    <img alt="Litestar MCP" src="docs/assets/banner-inline-light.svg" width="760">
  </picture>

  <p><strong>Model Context Protocol (MCP), Multi-Agent Runtime, Durable DAG Workflows, and A2A 1.0 Interoperability for Litestar.</strong></p>

  <p>
    <a href="https://pypi.org/project/litestar-mcp/"><img alt="PyPI Version" src="https://img.shields.io/pypi/v/litestar-mcp?color=202235&labelColor=0b0d14"></a>
    <a href="https://pypi.org/project/litestar-mcp/"><img alt="Python Versions" src="https://img.shields.io/pypi/pyversions/litestar-mcp?color=202235&labelColor=0b0d14"></a>
    <a href="https://cofin.github.io/litestar-mcp/"><img alt="Documentation" src="https://img.shields.io/badge/docs-cofin.github.io%2Flitestar--mcp-DCB834?labelColor=0b0d14"></a>
    <a href="https://opensource.org/licenses/MIT"><img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-202235?labelColor=0b0d14"></a>
    <a href="https://discord.gg/litestar"><img alt="Discord" src="https://img.shields.io/badge/discord-Litestar-5865F2?labelColor=0b0d14&logo=discord&logoColor=white"></a>
  </p>

  <p>
    <a href="https://cofin.github.io/litestar-mcp/getting-started/">Getting Started</a>
    &middot;
    <a href="https://cofin.github.io/litestar-mcp/architecture/">Architecture &amp; Diagrams</a>
    &middot;
    <a href="https://cofin.github.io/litestar-mcp/learn/">MCP Core</a>
    &middot;
    <a href="https://cofin.github.io/litestar-mcp/queues/">Tasks, Queues &amp; Workflows</a>
    &middot;
    <a href="https://cofin.github.io/litestar-mcp/agents/">Agents &amp; A2A</a>
    &middot;
    <a href="https://cofin.github.io/litestar-mcp/security/">Security</a>
    &middot;
    <a href="https://cofin.github.io/litestar-mcp/reference/">API Reference</a>
  </p>
</div>

---

## Overview

`litestar-mcp` integrates Litestar web applications with the Model Context Protocol (MCP), multi-agent orchestration, distributed task queues, and the Agent2Agent (A2A) 1.0 protocol. Mark existing Litestar routes with `mcp_tool=True`, `mcp_resource=True`, or `mcp_prompt=True` — or author standalone servers with the `MCP` wrapper — and every tool and prompt call dispatches in-process through Litestar's ASGI pipeline so your authentication middleware, guards, dependency injection (`Provide` and Dishka `FromDishka[T]`), DTOs, and OpenTelemetry spans execute without duplication.

## Capability Matrix

| Capability | Primary Symbols | Documentation |
| :--- | :--- | :--- |
| **Route Marking & Decorators** | `LitestarMCP`, `MCP`, `MCPConfig`, `mcp_tool`, `mcp_resource` (`uri=`), `mcp_prompt`, `MCPOptKeys`, opt-in `outputSchema` / `structuredContent`, `SEP-1865` `io.modelcontextprotocol/ui` (`_meta.ui`, `title`, `icons`), `PromptController` | [MCP Core Guide](https://cofin.github.io/litestar-mcp/learn/marking-routes/) |
| **Dual-Mode Streamable HTTP & CLI** | Stateless `2026-07-28` (`server/discover`, SEP-2575 headers, `POST subscriptions/listen`) and session-aware `2025-11-25` / `2025-06-18` / `2025-03-26` (`initialize`, `Mcp-Session-Id`, `GET /mcp` SSE, `resources/subscribe`, `logging/setLevel`, `ping`, `DELETE /mcp`), plus `litestar mcp stdio`, `bridge`, `export-tool-spec`, `list-tools`, `list-resources`, `list-skills`, `run` | [Transport & Discovery](https://cofin.github.io/litestar-mcp/learn/discovery/) |
| **Agent Skills (`SEP-2640`)** | `MCPSkillsConfig` (`expose_as_prompts`, `list_changed`), `SkillCatalog`, `FilesystemSkillSource`, `SkillSource`, `Skill`, `SkillFile`, `SkillController` | [Agent Skills Guide](https://cofin.github.io/litestar-mcp/learn/skills/) |
| **Distributed MCP Tasks & Queues** | `MCPTaskConfig`, `QueueTaskExecutor`, `MCPTaskExecutor`, `MCPTaskStore`, `MCPInputRequiredResult`, `PrincipalSnapshot`, `progress_token` forwarding | [Distributed Task Queues](https://cofin.github.io/litestar-mcp/queues/distributed-task-queues/) |
| **Durable DAG Workflows** | `DynamicWorkflow`, `WorkflowNode`, `WorkflowEngine`, `DurableWorkflowEngine`, `WorkflowStore`, `WorkflowRun`, `NodeRecord` | [Durable Workflows](https://cofin.github.io/litestar-mcp/queues/durable-workflows/) |
| **Multi-Agent Runtime & Streaming** | `Agent`, `AgentGroup`, `AgentRuntime`, `TurnBudget`, `GoogleGenAIClient`, `SessionStore`, `AgentChatController`, `QueuedAgentChatController` | [Agents & Runtime](https://cofin.github.io/litestar-mcp/agents/runtime-and-budgets/) |
| **A2A 1.0 & Protocol Bridges** | `LitestarA2A`, `A2AConfig` (JSON-RPC 2.0 + `HTTP+JSON` REST, `/{tenant:str}` + `X-A2A-Tenant`), `agent_to_mcp`, `mcp_to_tools`, `discover_mcp_tools`, `agent_to_a2a` | [A2A 1.0 Protocol](https://cofin.github.io/litestar-mcp/agents/a2a-protocol/) |
| **Security & Observability** | Litestar auth middleware, `litestar-security` (`MCPConfig(auth=required(...))` + RFC 9728 metadata), `MCPSessionStore` owner isolation, `subscription_authorizer`, guards/scopes, RFC 5424 server logging (`MCPRequestContext.log()`), `TelemetryConfig` | [Security & Operations](https://cofin.github.io/litestar-mcp/security/) |

---

## Installation

Install the core package with `uv`, `pip`, `pdm`, or `poetry`:

```bash
# uv (recommended)
uv add litestar-mcp

# pip
pip install litestar-mcp

# pdm
pdm add litestar-mcp

# poetry
poetry add litestar-mcp
```

### Optional Extras

`litestar-mcp` keeps its core dependency footprint focused on Litestar. Enable optional integrations by installing the corresponding extra:

| Extra | Install Command | Purpose |
| :--- | :--- | :--- |
| `queues` | `uv add "litestar-mcp[queues]"` | Distributed MCP Tasks (`QueueTaskExecutor`), durable DAG workflows (`DurableWorkflowEngine`), and `QueuedAgentChatController` on `litestar-queues>=0.13.0` |
| `a2a` | `uv add "litestar-mcp[a2a]"` | Official `a2a-sdk` 1.0 plugin (`LitestarA2A`) with JSON-RPC 2.0, `HTTP+JSON` REST bindings, and `agent_to_a2a` bridge |
| `google-genai` | `uv add "litestar-mcp[google-genai]"` | First-class `GoogleGenAIClient` model adapter (defaults to `gemini-3.8-flash` with thought-signature round-tripping) |
| `opentelemetry` | `uv add "litestar-mcp[opentelemetry]"` | OpenTelemetry span emission and attribute recording via `TelemetryConfig` |

Combine multiple extras in a single install command when needed:

```bash
uv add "litestar-mcp[a2a,queues,google-genai,opentelemetry]"
```

---

## Quickstart

### Expose Existing Litestar Routes (LitestarMCP Plugin)

Pass `mcp_tool=True`, `mcp_resource=True`, or `mcp_prompt=True` (or an explicit string name) directly to Litestar route decorators. `LitestarMCP` infers MCP tool annotation hints (`mcp_annotations`) from the HTTP method (`GET` sets `readOnlyHint=True`, `PUT` sets `idempotentHint=True`, `DELETE` sets `destructiveHint=True`) and strips DI parameters (`Provide`, `FromDishka[T]`, `Request`, `State`, `ToolContext`) from public JSON Schemas while resolving them at invocation time:

```python
from litestar import Litestar, delete, get, post
from litestar.openapi.config import OpenAPIConfig
from litestar_mcp import LitestarMCP, MCPConfig


@get("/users", mcp_tool="list_users")
async def get_users(limit: int = 20) -> list[dict[str, object]]:
    """List active users in the workspace."""
    return [{"id": 1, "name": "Alice", "limit": limit}]


@post(
    "/reports/analyze",
    mcp_tool=True,
    mcp_annotations={"title": "Analyze Dataset", "idempotentHint": True},
)
async def analyze_data(dataset: str, threshold: float = 0.95) -> dict[str, object]:
    """Run statistical analysis over a named dataset."""
    return {"dataset": dataset, "threshold": threshold, "status": "ok"}


@delete("/users/{user_id:int}", mcp_tool="remove_user")
async def delete_user(user_id: int) -> dict[str, object]:
    """Delete a user account by ID."""
    return {"deleted": user_id}


@get("/config", mcp_resource="app_config")
async def get_app_config() -> dict[str, object]:
    """Return read-only application configuration metadata."""
    return {"environment": "production", "version": "2.0.0"}


app = Litestar(
    route_handlers=[get_users, analyze_data, delete_user, get_app_config],
    plugins=[LitestarMCP(MCPConfig(name="Workspace Service"))],
    openapi_config=OpenAPIConfig(title="Workspace Service", version="2.0.0"),
)
```

### Standalone Declarative Server (MCP Wrapper)

When building a dedicated MCP service, use the standalone `MCP` class. Its `@mcp.tool()`, `@mcp.resource()`, and `@mcp.prompt()` decorators accept full Litestar route kwargs (`dependencies`, `guards`, `opt`, DTOs) and expose `mcp.app` for the Litestar CLI:

```python
from litestar_mcp import MCP

mcp = MCP("math-service", instructions="Provides deterministic arithmetic tools.")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Return the sum of two integers."""
    return a + b


@mcp.resource("config://limits", name="service_limits")
def limits() -> dict[str, int]:
    """Return service rate and payload limits."""
    return {"max_batch": 100}


app = mcp.app

if __name__ == "__main__":
    mcp.run(port=8000)
```

### Dual-Mode Streamable HTTP & CLI

A single `/mcp` endpoint negotiates stateless MCP `2026-07-28` (`server/discover` with SEP-2575 `MCP-Protocol-Version` and `Mcp-Method` headers, plus `POST subscriptions/listen`) alongside session-aware MCP `2025-11-25`, `2025-06-18`, and `2025-03-26` (`initialize` issuing `Mcp-Session-Id`, `GET /mcp` Server-Sent Events with `Accept: text/event-stream`, `resources/subscribe`, `logging/setLevel`, `notifications/initialized`, `ping`, and `204 No Content` on `DELETE /mcp`). Tools can advertise JSON Schema 2020-12 `outputSchema` with automatic `structuredContent` population (`MCPConfig(auto_output_schema=True)` or `mcp_output_schema=True`) and attach `SEP-1865` MCP Apps metadata (`io.modelcontextprotocol/ui`, `text/html;profile=mcp-app`):

```bash
# MCP 2026-07-28 stateless discovery
curl -sX POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: server/discover' \
  -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{}}}}'

# MCP 2025-11-25 initialize handshake (returns Mcp-Session-Id response header)
curl -siX POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'

# Open session-scoped GET /mcp SSE notification stream
curl -N http://127.0.0.1:8000/mcp \
  -H 'Accept: text/event-stream' \
  -H 'MCP-Protocol-Version: 2025-11-25' \
  -H "Mcp-Session-Id: $SESSION_ID"

# Subscribe to resource updates and adjust RFC 5424 server log threshold
curl -sX POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2025-11-25' \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":2,"method":"resources/subscribe","params":{"uri":"litestar://openapi"}}'

curl -sX POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2025-11-25' \
  -H "Mcp-Session-Id: $SESSION_ID" \
  -d '{"jsonrpc":"2.0","id":3,"method":"logging/setLevel","params":{"level":"debug"}}'
```

For local desktop hosts and CI pipelines, `litestar-mcp` registers subcommands under `litestar mcp`:

```bash
# Serve the application over stdin/stdout in-process with full Litestar lifespan
litestar --app my_app:app mcp stdio

# Bridge local stdio to a remote Streamable HTTP deployment
litestar mcp bridge --base-url https://api.example.com --endpoint /mcp

# Inspect discovered tools, resources, and SEP-2640 skills from the terminal
litestar --app my_app:app mcp list-tools
litestar --app my_app:app mcp list-resources
litestar --app my_app:app mcp list-skills

# Export static {"tools": [...]} JSON schemas without booting an HTTP server
litestar --app my_app:app mcp export-tool-spec --indent 2
```

### Distributed MCP Tasks & Durable Workflows (litestar-queues)

Pair `LitestarMCP` with `QueuePlugin` from `litestar-queues` to execute long-running `io.modelcontextprotocol/tasks` and durable DAG workflows across worker processes. `QueueTaskExecutor` snapshots `request.user`, `request.auth`, and `litestar-security` `SecurityContext` at submission time, rehydrates the internal HTTP scope and Dishka `Scope.REQUEST` container on the worker, and supports cooperative cancellation plus multi-round `MCPInputRequiredResult` resume across replicas:

```python
from litestar import Litestar, post
from litestar.stores.memory import MemoryStore
from litestar.stores.registry import StoreRegistry
from litestar_queues import QueueConfig, QueuePlugin

from litestar_mcp import LitestarMCP, MCPConfig, MCPTaskConfig
from litestar_mcp.agent import DynamicWorkflow, WorkflowContext
from litestar_mcp.queues import DurableWorkflowEngine, QueueTaskExecutor


@post("/backfill", mcp_tool="run_backfill", mcp_task=True)
async def run_backfill(batch_size: int = 500) -> dict[str, object]:
    """Execute a background backfill task on a litestar-queues worker."""
    return {"processed": batch_size, "status": "completed"}


async def extract_step(ctx: WorkflowContext) -> dict[str, object]:
    """Extract source records for the workflow run."""
    return {"rows": 250}


pipeline = DynamicWorkflow("etl_pipeline")
pipeline.add_node("extract", handler=extract_step)

queue_plugin = QueuePlugin(QueueConfig(queue_backend="memory"))
task_executor = QueueTaskExecutor(queue="mcp", timeout=3600)
workflow_engine = DurableWorkflowEngine([pipeline])

app = Litestar(
    route_handlers=[run_backfill],
    plugins=[
        queue_plugin,
        workflow_engine,
        LitestarMCP(
            MCPConfig(
                tasks=MCPTaskConfig(executor=task_executor),
            )
        ),
    ],
    stores=StoreRegistry(
        stores={
            "litestar_mcp_tasks": MemoryStore(),
            "litestar_mcp_workflows": MemoryStore(),
        }
    ),
)
```

### Multi-Agent Runtime & GoogleGenAIClient

Orchestrate single agents or hierarchical `AgentGroup` coordinator-specialist trees under `AgentRuntime` with strict `TurnBudget` guardrails (`max_turns`, `max_tool_calls`, `timeout_seconds`) and `GoogleGenAIClient` (defaulting to `gemini-3.8-flash` with thought-signature round-tripping):

```python
from litestar import Litestar
from litestar_mcp.agent import (
    Agent,
    AgentChatController,
    AgentRuntime,
    GoogleGenAIClient,
    MemorySessionStore,
    TurnBudget,
)
from litestar_mcp.core import tool


@tool
def lookup_order(order_id: str) -> dict[str, str]:
    """Look up order shipment status by ID."""
    return {"order_id": order_id, "status": "in_transit"}


support_agent = Agent(
    name="support",
    description="Answers customer order inquiries.",
    instructions="Use lookup_order to verify shipment status before replying.",
    tools=[lookup_order],
)

runtime = AgentRuntime(
    support_agent,
    default_model=GoogleGenAIClient(),
    sessions=MemorySessionStore(),
    budget=TurnBudget(max_turns=6, max_tool_calls=8, timeout_seconds=30.0),
)

app = Litestar(route_handlers=[AgentChatController], plugins=[runtime])
```

### Official A2A 1.0 JSON-RPC + HTTP+JSON REST (LitestarA2A)

Mount `LitestarA2A` (or use the `agent_to_a2a` bridge) to expose an `Agent` or official `a2a-sdk` `AgentExecutor` over A2A 1.0 JSON-RPC 2.0 (`POST /a2a`), native A2A 1.0 `HTTP+JSON` REST routes (`/a2a/message:send`, `/a2a/message:stream`, `/a2a/tasks/{id}:cancel`, `/a2a/tasks/{id}:subscribe`), and `GET /.well-known/agent-card.json` with optional `/{tenant:str}` path and `X-A2A-Tenant` header multi-tenancy:

```python
from litestar import Litestar
from litestar_mcp.agent import agent_to_a2a

a2a_plugin = agent_to_a2a(
    support_agent,
    path="/a2a",
    base_url="https://agents.example.com",
    runtime=runtime,
)

app = Litestar(plugins=[runtime, a2a_plugin])
```

---

## Single-File Reference Examples (uv run & uvx)

The repository ships a 10-variant Notes reference application under [`docs/examples/notes/`](https://cofin.github.io/litestar-mcp/architecture/reference-examples/) comparing **SQLSpec** and **Advanced Alchemy** across **Litestar `Provide`** and **Dishka `FromDishka[T]`** dependency injection and four authentication tiers (`no-auth`, `JWT`, `Cloud Run`, and Google `IAP`). Every variant embeds a PEP 723 script block so `uv run` provisions dependencies automatically:

```bash
uv run docs/examples/notes/sqlspec/no_auth.py
uv run docs/examples/notes/sqlspec/jwt_auth_dishka.py
uv run docs/examples/notes/sqlspec/google_iap.py
uv run docs/examples/notes/advanced_alchemy/no_auth.py
```

See the [Single-File `uvx` & PEP 723 Guide](https://cofin.github.io/litestar-mcp/architecture/uvx-and-pep723/) for remote GitHub URL execution and `uvx` commands.

---

## Documentation

Full documentation, interactive architecture diagrams, and API reference are published at **[https://cofin.github.io/litestar-mcp/](https://cofin.github.io/litestar-mcp/)**:

- [**Getting Started**](https://cofin.github.io/litestar-mcp/getting-started/) — Installation, optional extras, 5-minute quickstart, standalone `MCP` servers, and core concepts
- [**Architecture & Examples**](https://cofin.github.io/litestar-mcp/architecture/) — Interactive system diagrams, Notes reference matrix, and single-file PEP 723 guide
- [**MCP Core**](https://cofin.github.io/litestar-mcp/learn/) — `MCPConfig`, route markers, `MCPOptKeys`, `outputSchema` and `structuredContent`, MCP Apps (`io.modelcontextprotocol/ui`), resources, prompts, Agent Skills (`SEP-2640` & `SkillCatalog`), RFC 5424 server logging, `2026-07-28` & `2025-11-25` Streamable HTTP, stdio, and bridge
- [**Tasks, Queues & Workflows**](https://cofin.github.io/litestar-mcp/queues/) — `io.modelcontextprotocol/tasks`, `QueueTaskExecutor`, `DurableWorkflowEngine`, `QueuedAgentChatController`, and multi-replica deployment
- [**Agents & A2A**](https://cofin.github.io/litestar-mcp/agents/) — `AgentRuntime`, `TurnBudget`, `GoogleGenAIClient`, `AgentGroup`, protocol bridges, `LitestarA2A`, and multi-tenancy
- [**Security & Operations**](https://cofin.github.io/litestar-mcp/security/) — Authentication middleware, `litestar-security` & RFC 9728 metadata, authorization guards/scopes, and OpenTelemetry
- [**API Reference & CLI**](https://cofin.github.io/litestar-mcp/reference/) — Curated API reference, `litestar mcp` CLI reference, contributor guide, and [Changelog](https://cofin.github.io/litestar-mcp/changelog/)

---

## Development & Conformance

```bash
git clone https://github.com/cofin/litestar-mcp.git
cd litestar-mcp

# Create the virtual environment with all extras and dev groups
make install

# Run linters, type checkers (mypy + pyright), slotscheck, and unit/example tests
make lint
make test

# Run the pinned MCP 2026-07-28 conformance suite (in-process and queue executors)
make conformance
make conformance-queue

# Run the documentation verification pipeline (docstring AST scan, CLI check, build, artifact verification)
make docs-check
```

See the [Contributing Guide](https://cofin.github.io/litestar-mcp/reference/contributing/) for full development workflow details.

## License

MIT License. See [LICENSE](LICENSE) for details.
