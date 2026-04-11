# Litestar MCP Plugin Examples

Two runnable example applications demonstrating how to expose Litestar routes as Model Context Protocol (MCP) tools and resources.

> **How MCP is served:** `LitestarMCP` mounts a single JSON-RPC 2.0 endpoint at `POST /mcp`. Tool calls and resource reads are JSON-RPC *methods* (`tools/call`, `resources/read`), **not** REST paths. You will not find `GET /mcp/tools` or `GET /mcp/resources/<name>` — those don't exist.

## Examples Overview

### 📁 `docs/examples/basic/`

**Hello-World, one of each primitive**

- One MCP **tool** (`add`) — executable, input schema derived from path params
- One MCP **resource** (`pi`) — static, cacheable, no parameters
- One plain route (`/status`) — demonstrates that not every endpoint needs MCP
- Default `LitestarMCP()` config, no custom plumbing

**Best for:** Getting started. Understanding the tool-vs-resource distinction in <50 lines.

### 📁 `docs/examples/advanced/`

**SQLite-backed task management, modeled on advanced-alchemy's upstream Litestar example**

- CRUD tools (`list_tasks`, `get_task`, `create_task`, `complete_task`, `delete_task`) backed by SQLite via `advanced-alchemy`
- Per-request `TaskService` wired through `providers.create_service_dependencies` — the same dependency resolves for regular HTTP handlers *and* for MCP tool calls
- Static `api_info` resource (cacheable) + generated `task_schema` resource (from `Task.model_json_schema()`)
- Pagination, title search, and `completedIn` collection filtering through AA's filter dependencies
- Pydantic schemas — `app.type_encoders` picked up automatically via Litestar's auto-discovered `PydanticPlugin`

**Best for:** Real-world patterns — dependency injection, service layers, controllers, persistent state.

## Feature Matrix

| Feature                                 | Basic | Advanced |
| --------------------------------------- | :---: | :------: |
| `LitestarMCP` plugin integration        |   ✅  |    ✅    |
| Built-in `openapi` resource             |   ✅  |    ✅    |
| Route marking (`mcp_tool` / `mcp_resource`) | ✅ |    ✅    |
| MCP tool with input parameters          |   ✅  |    ✅    |
| Multiple tools / resources              |   —   |    ✅    |
| Dependency injection into MCP handlers  |   —   |    ✅    |
| SQLite persistence via advanced-alchemy |   —   |    ✅    |
| Controllers + service layer             |   —   |    ✅    |
| Pagination / search / collection filters |  —  |    ✅    |

## Setup

All examples require the base dependencies:

```bash
uv add litestar uvicorn
```

The advanced example additionally needs:

```bash
uv add advanced-alchemy aiosqlite pydantic
```

## Running

Both examples are serve-only ASGI apps — `main.py` only constructs `app`, it doesn't call `uvicorn.run`. Start them with `uvicorn` directly:

Both examples can be served two ways:

```bash
# Option A — uvicorn directly
uv run uvicorn main:app --reload

# Option B — Litestar's own CLI runner (nicer banner, --debug flag, reload)
uv run litestar --app main:app run --reload --debug
```

### Basic example

```bash
cd docs/examples/basic/
uv run uvicorn main:app --reload
```

Useful URLs once it's up:

- `http://127.0.0.1:8000/` — plain root
- `http://127.0.0.1:8000/status` — plain health route (not MCP)
- `http://127.0.0.1:8000/schema/swagger` — Litestar-generated API docs
- `POST http://127.0.0.1:8000/mcp` — MCP JSON-RPC 2.0 endpoint (tools + resources)

Confirm the MCP surface:

```bash
# List tools (JSON-RPC)
curl -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

# Call the add tool
curl -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"add","arguments":{"a":2,"b":3}}}'

# Read the pi resource
curl -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":3,"method":"resources/read","params":{"uri":"litestar://pi"}}'
```

Plain (non-MCP) routes are served normally:

```bash
curl http://127.0.0.1:8000/          # root
curl http://127.0.0.1:8000/status    # plain health route
```

### Advanced example

```bash
cd docs/examples/advanced/
uv run uvicorn main:app --reload
```

The database (`db.sqlite3`) is created and seeded on first startup via `on_startup=[seed_initial_tasks]`.

Useful URLs once it's up:

- `http://127.0.0.1:8000/` — plain root
- `http://127.0.0.1:8000/health` — plain health route (not MCP)
- `http://127.0.0.1:8000/schema/swagger` — Litestar-generated API docs for the task CRUD surface
- `http://127.0.0.1:8000/tasks` — regular HTTP access to the same handlers that back the MCP tools (pagination, title search, and `?completedIn=true|false` all work here)
- `POST http://127.0.0.1:8000/mcp` — MCP JSON-RPC 2.0 endpoint

See `docs/examples.rst` for the full tool/resource catalog and sample JSON-RPC payloads.

## Offline Invocation: the `litestar mcp` CLI

`LitestarMCP` registers a `mcp` sub-group under Litestar's own CLI, so you can introspect and run MCP tools and resources without starting a server. This is useful for scripting, smoke tests, and quick debugging.

```bash
# Show the group help
uv run litestar --app main:app mcp

# List everything the app exposes
uv run litestar --app main:app mcp list-tools
uv run litestar --app main:app mcp list-resources

# Run a tool — handler kwargs become --flags and are coerced to their
# declared type (int, float, bool, str, Path). Complex types accept a JSON string.
uv run litestar --app main:app mcp run add --a 2 --b 3
# => {"a": 2, "b": 3, "result": 5}

# Read a resource — resources have no parameters, just name them
uv run litestar --app main:app mcp run pi
# => {"name": "pi", "value": 3.141592653589793, "description": "..."}
```

### Limits

- Tools that depend on request-scoped framework resources (`Request`, `State`, `scope`, `headers`, …) can't run offline — the CLI isn't a request context. The CLI will print a `NotCallableInCLIContextError` explaining which dependency is incompatible and ask you to invoke that tool over HTTP instead.
- `mcp run` looks up tools *and* resources by name — you'll get an error for names not in either registry.
- The `advanced/` example's tools all depend on a per-request `db_session` provided by `SQLAlchemyPlugin`, so most of them are HTTP-only. Run them via `POST /mcp` as shown above instead.

## How Route Marking Works

Mark a handler with `mcp_tool=` (executable) or `mcp_resource=` (readable) to expose it through MCP. Unknown kwargs on Litestar's route decorators land on `handler.opt`, where the plugin discovers them:

```python
from litestar import Litestar, get
from litestar_mcp import LitestarMCP


# Expose as an MCP tool — clients invoke this via tools/call
@get("/users", mcp_tool="list_users")
async def list_users() -> list[dict]:
    return [{"id": 1, "name": "Alice"}]


# Expose as an MCP resource — clients read this via resources/read
@get("/schema", mcp_resource="user_schema")
async def user_schema() -> dict:
    return {"type": "object", "properties": {}}


# Regular route — not exposed to MCP
@get("/health")
async def health_check() -> dict:
    return {"status": "ok"}


app = Litestar(
    route_handlers=[list_users, user_schema, health_check],
    plugins=[LitestarMCP()],
)
```

The equivalent, more explicit form — useful inside `Controller` classes or when you already pass an `opt` dict — is `@get("/users", opt={"mcp_tool": "list_users"})`. Both work; the plugin reads from `handler.opt` either way.

### Tool vs resource: which do I want?

| Use a **resource** (`mcp_resource=...`) for | Use a **tool** (`mcp_tool=...`) for |
| ------------------------------------------- | ----------------------------------- |
| Read-only data the client may cache         | Executable operations and mutations |
| Schemas, metadata, static configuration     | Queries that take input parameters  |
| Calls that take no parameters               | Anything that changes state         |

## Configuration

The plugin accepts `MCPConfig` for overrides:

```python
from litestar_mcp import LitestarMCP, MCPConfig

app = Litestar(
    route_handlers=[...],
    plugins=[
        LitestarMCP(
            MCPConfig(
                name="My API Server",   # Override server name (defaults to OpenAPI title)
                base_path="/mcp",       # Change the JSON-RPC endpoint mount path
                include_in_schema=False,  # Keep /mcp out of the OpenAPI schema
            ),
        ),
    ],
)
```

## Troubleshooting

**Routes not appearing under `tools/list` or `resources/list`** — make sure the handler is registered in `route_handlers` *and* carries `mcp_tool=` / `mcp_resource=` (or the `opt={...}` equivalent). Tool/resource discovery happens during app startup, so when driving through `TestClient`, use `with TestClient(app=app) as client:` so the ASGI lifespan fires.

**"Tool execution failed: Unsupported type: ..."** — the MCP result encoder honors whatever type encoders Litestar has registered for the app (Pydantic is auto-discovered, msgspec/dataclass/stdlib types are built in). If you return a custom object from a tool, register a `type_encoders` entry on the `Litestar(...)` constructor.

**HTTP 404 on `GET /mcp/tools` or `GET /mcp/resources`** — expected. MCP is JSON-RPC 2.0 over a single `POST /mcp`; there are no per-tool / per-resource REST paths.

## Next Steps

- Start from `docs/examples/basic/` and mark one of your own routes.
- Read the upstream advanced-alchemy pattern in `docs/examples/advanced/main.py` for DI + service-layer guidance.
- The main docs (`docs/` or `https://docs.litestar-mcp.litestar.dev/`) cover auth, scopes, and the full plugin configuration surface.
