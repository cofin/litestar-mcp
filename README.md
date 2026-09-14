# Litestar MCP Plugin

A lightweight plugin that integrates Litestar web applications with the Model Context Protocol (MCP) by exposing marked routes as MCP tools, resources, and prompts over MCP Streamable HTTP and JSON-RPC.

[![PyPI - Version](https://img.shields.io/pypi/v/litestar-mcp)](https://pypi.org/project/litestar-mcp/)
[![Python Version](https://img.shields.io/pypi/pyversions/litestar-mcp)](https://pypi.org/project/litestar-mcp/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## Overview

This plugin automatically discovers Litestar routes marked for MCP and exposes them through an MCP-native transport surface. Pass `mcp_tool="name"`, `mcp_resource="name"`, or `mcp_prompt="name"` straight through to `@get` / `@post` / etc. — Litestar funnels unknown kwargs into `handler.opt`, so no second decorator or `opt={...}` wrapper is needed. Standalone prompt callables that are not bound to an HTTP route can be registered through `LitestarMCP(prompts=[...])`.

## Features

- **Protocol-Native Transport** — MCP Streamable HTTP with JSON-RPC requests and SSE streams.
- **Three MCP Primitives** — tools, resources, *and* prompts, with `prompts/list` and `prompts/get` driven by the same handler discovery as the rest of the surface.
- **Simple Route Marking** — pass `mcp_tool` / `mcp_resource` / `mcp_prompt` kwargs straight through to Litestar's route decorators, or register standalone prompts via `LitestarMCP(prompts=[...])`.
- **RFC 6570 URI Templates** — `mcp_resource_template="app://…/{var}"` dispatches concrete URIs to handlers with extracted vars.
- **First-Class Descriptions** — structured `mcp_description`, `mcp_agent_instructions`, `mcp_when_to_use`, `mcp_returns` kwargs.
- **Type Safe** — full type hints with dataclasses; `msgspec`-powered tool-argument validation.
- **Automatic Discovery** — routes are discovered at app initialization.
- **OpenAPI Integration** — server info derived from OpenAPI config.
- **Bring Your Own Auth** — MCP inherits the app's Litestar authentication middleware, including litestar-security or a custom `AbstractAuthenticationMiddleware`.
- **Optional Task Support** — the MCP Tasks extension with Litestar Store records; applications coordinate execution across workers.
- **Optional A2A 1.0** — official SDK models and handlers on native Litestar JSON-RPC/SSE routes, without required Starlette, FastAPI or Uvicorn dependencies.

## Quick Start

### Installation

```bash
pip install litestar-mcp
# or
uv add litestar-mcp
```

### Basic Usage

```python
from litestar import Litestar, get, post
from litestar.openapi.config import OpenAPIConfig
from litestar_mcp import LitestarMCP

# Mark routes for MCP exposure using the opt attribute
@get("/users", mcp_tool="list_users")
async def get_users() -> list[dict]:
    """List all users in the system."""
    return [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]

@post("/analyze", mcp_tool="analyze_data")
async def analyze_data(data: dict) -> dict:
    """Analyze the provided data and return insights."""
    return {"result": f"Analyzed {len(data)} items"}

@get("/config", mcp_resource="app_config")
async def get_app_config() -> dict:
    """Get the current application configuration."""
    return {"debug": True, "version": "1.0.0"}

# Add the MCP plugin to your Litestar app
app = Litestar(
    route_handlers=[get_users, analyze_data, get_app_config],
    plugins=[LitestarMCP()],
    openapi_config=OpenAPIConfig(title="My API", version="1.0.0"),
)
```

### Standalone Application (Alternative)

If you are building a standalone MCP server, you can use the ``MCP`` class which provides a simplified declarative API and programmatically boots the server using the standard Litestar CLI:

```python
from litestar_mcp import MCP

# 1. Initialize the application
mcp = MCP("my-mcp-server", instructions="Exposes utility tools.")

# 2. Register tools, resources, or prompts using decorators
@mcp.tool()
def add(a: int, b: int) -> int:
    """Calculate the sum of two integers."""
    return a + b

# 3. Expose the app globally so that the CLI can discover it
app = mcp.app

if __name__ == "__main__":
    # 4. Boot the modern Streamable HTTP server
    mcp.run(transport="streamable-http", port=8000)
```

The standalone decorators accept Litestar route-handler keyword arguments such as `dependencies`, `guards`, `response_headers`, `responses`, `summary`, `tags`, DTO options, hooks, and arbitrary extra kwargs stored in `handler.opt`. The `name` keyword names the MCP primitive; use `route_name` to set Litestar's route-handler name separately.

### With Configuration

```python
from litestar_mcp import LitestarMCP, MCPConfig

config = MCPConfig(
    base_path="/api/mcp",  # Change the base path
    name="Custom Server Name",  # Override server name
    include_in_schema=True,  # Include MCP routes in OpenAPI schema
)

app = Litestar(
    route_handlers=[get_users, analyze_data, get_app_config],
    plugins=[LitestarMCP(config)],
    openapi_config=OpenAPIConfig(title="My API", version="1.0.0"),
)
```

## Resources vs Tools: When to Use Each

### Use Resources (`mcp_resource`) for

- **Read-only data** that AI models need to reference
- **Static or semi-static information** like documentation, schemas, configurations
- **Data that doesn't require parameters** to retrieve
- **Reference material** that AI models should "know about"

**Examples:**

```python
@get("/schema", mcp_resource="database_schema")
async def get_schema() -> dict:
    """Database schema information."""
    return {"tables": ["users", "orders"], "relationships": [...]}

@get("/docs", mcp_resource="api_docs")
async def get_documentation() -> dict:
    """API documentation and usage examples."""
    return {"endpoints": [...], "examples": [...]}
```

### Use Tools (`mcp_tool`) for

- **Actions that perform operations** or mutations
- **Dynamic queries** that need input parameters
- **Operations that change state** in your application
- **Computations or data processing** tasks

**Examples:**

```python
@post("/users", mcp_tool="create_user")
async def create_user(user_data: dict) -> dict:
    """Create a new user account."""
    # Perform user creation logic
    return {"id": 123, "created": True}

@get("/search", mcp_tool="search_data")
async def search(query: str, limit: int = 10) -> dict:
    """Search through application data."""
    # Perform search with parameters
    return {"results": [...], "total": 42}
```

## How It Works

1. **Route Discovery**: At app initialization, the plugin scans all route handlers for the `opt` attribute
2. **Automatic Exposure**: Routes marked with `mcp_tool` or `mcp_resource` are automatically exposed
3. **MCP Transport**: The plugin adds a Streamable HTTP MCP endpoint under the configured base path (default `/mcp`)
4. **Server Info**: Server name and version are derived from your OpenAPI configuration

## MCP Endpoints

Once configured, your application exposes these MCP-compatible endpoints:

- `POST /mcp` - stateless MCP `2026-07-28` JSON-RPC and subscription streams
- `litestar --app my_app:app mcp stdio` - in-process stdio for desktop clients
- `litestar mcp bridge` - stdio proxy to a running Streamable HTTP server

Install `litestar-mcp[a2a]` to mount an official A2A SDK request handler and
agent card independently of MCP. The adapter accepts A2A 1.0 JSON-RPC requests
with `A2A-Version: 1.0`; it rejects missing/legacy versions and offers no 0.3
conversion, gRPC or REST binding. Applications supply authentication, durable
stores, executor resource scopes, worker coordination and push delivery policy.
See the [A2A guide](https://cofin.github.io/litestar-mcp/latest/usage/a2a.html).

Use `server/discover` instead of an initialize handshake:

```bash
curl -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'MCP-Protocol-Version: 2026-07-28' \
  -H 'Mcp-Method: server/discover' \
  -d '{"jsonrpc":"2.0","id":1,"method":"server/discover","params":{"_meta":{"io.modelcontextprotocol/protocolVersion":"2026-07-28","io.modelcontextprotocol/clientCapabilities":{},"io.modelcontextprotocol/clientInfo":{"name":"curl","version":"1"}}}}'
```

Every request is independent. There are no protocol sessions, sticky-routing
headers, GET/DELETE transport handlers, or SSE replay.

MCP IDs must be strings or finite integer-valued numbers. Missing, null,
boolean, container or fractional IDs fail before tool execution or stream
allocation. Progress streams apply bounded backpressure; slow subscription
consumers are completed and disconnected. Shared task Stores persist records
but do not distribute task execution or local input/cancel queues.

Google ADK 2.8.0 with MCP SDK 1.29.1 still sends the initialize-era lifecycle
and cannot consume this endpoint. ADK `RemoteA2aAgent` interoperability has
not been verified; local ADK agents and the tested official A2A SDK client are
separate integration paths. See the [0.14 migration guide](https://cofin.github.io/litestar-mcp/latest/usage/migration_0_14.html)
for removed aliases and configuration.

**Built-in Resources:**

- `litestar://openapi` - Your application's OpenAPI schema (always available via `resources/read`)

## Configuration

Configure the plugin using `MCPConfig`:

```python
from litestar_mcp import MCPConfig

config = MCPConfig()
```

**Configuration Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `base_path` | `str` | `"/mcp"` | Base path for the MCP Streamable HTTP endpoint |
| `include_in_schema` | `bool` | `False` | Whether to include MCP routes in OpenAPI schema |
| `name` | `str \| None` | `None` | Override server name. If None, uses OpenAPI title |
| `instructions` | `str \| None` | `None` | Server instructions returned to clients from `server/discover` |
| `guards` | `list[Any] \| None` | `None` | Litestar guards applied to the MCP router |
| `route_opt` | `dict[str, Any] \| None` | `None` | Route metadata merged onto the MCP handler, including litestar-security policies |
| `allowed_origins` | `list[str] \| None` | `None` | Exact additional Origins; present Origins must be same-origin or allowlisted |
| `include_operations` | `list[str] \| None` | `None` | Only expose matching operation names |
| `exclude_operations` | `list[str] \| None` | `None` | Exclude matching operation names |
| `include_tags` | `list[str] \| None` | `None` | Only expose routes with matching OpenAPI tags |
| `exclude_tags` | `list[str] \| None` | `None` | Exclude routes with matching OpenAPI tags |
| `tasks` | `bool \| MCPTaskConfig` | `False` | Enable the `io.modelcontextprotocol/tasks` extension |
| `opt_keys` | `MCPOptKeys` | `MCPOptKeys()` | Rename the `handler.opt` keys the plugin reads (`mcp_tool`, `mcp_resource`, ...) |
| `list_page_size` | `int` | `100` | Page size for `tools/list`, `resources/list`, `resources/templates/list`, and `prompts/list` |
| `cache_ttl_ms` | `int` | `0` | Conservative cache lifetime for discovery/list/resource results |
| `cache_scope` | `"private" \| "public"` | `"private"` | Cache sharing policy |
| `subscription_max_streams` | `int` | `10000` | Maximum concurrent `subscriptions/listen` streams |
| `subscription_keepalive_seconds` | `float` | `15.0` | Subscription keepalive interval |
| `subscription_channels` | `ChannelsPlugin \| None` | `None` | Optional cross-worker notification fan-out |
| `stream_queue_capacity` | `int` | `256` | Bounded subscription and request-progress queues |
| `stream_cleanup_timeout` | `float` | `5.0` | Deadline for cooperative response cleanup; expiry is logged |
| `max_blob_bytes` | `int \| None` | `26214400` | Maximum raw byte length for base64-embedded blobs; `None` disables the cap |
| `before_tool_call` | `BeforeToolCallHook \| None` | `None` | Observe each `tools/call` before dispatch |
| `after_tool_call` | `AfterToolCallHook \| None` | `None` | Observe each `tools/call` result, exception, and duration |

## Complete Example

```python
from litestar import Litestar, get, post, delete
from litestar.openapi.config import OpenAPIConfig
from litestar_mcp import LitestarMCP, MCPConfig

# Resources - read-only reference data
@get("/users/schema", mcp_resource="user_schema")
async def get_user_schema() -> dict:
    """User data model schema."""
    return {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"},
            "email": {"type": "string"}
        }
    }

@get("/api/info", mcp_resource="api_info")
async def get_api_info() -> dict:
    """API capabilities and information."""
    return {
        "version": "2.0.0",
        "features": ["user_management", "data_analysis"],
        "rate_limits": {"requests_per_minute": 1000}
    }

# Tools - actionable operations
@get("/users", mcp_tool="list_users")
async def list_users(limit: int = 10) -> dict:
    """List users with optional limit."""
    # Fetch users from database
    return {"users": [{"id": 1, "name": "Alice"}], "total": 1}

@post("/users", mcp_tool="create_user")
async def create_user(user_data: dict) -> dict:
    """Create a new user account."""
    # Create user logic
    return {"id": 123, "created": True, "user": user_data}

@post("/analyze", mcp_tool="analyze_dataset")
async def analyze_dataset(config: dict) -> dict:
    """Analyze data with custom configuration."""
    # Analysis logic
    return {"insights": [...], "metrics": {...}}

# Regular routes (not exposed to MCP)
@get("/health")
async def health_check() -> dict:
    return {"status": "healthy"}

# MCP configuration
mcp_config = MCPConfig(
    name="User Management API",
    base_path="/mcp"
)

# Create Litestar app
app = Litestar(
    route_handlers=[
        get_user_schema, get_api_info,  # Resources
        list_users, create_user, analyze_dataset,  # Tools
        health_check  # Regular route
    ],
    plugins=[LitestarMCP(mcp_config)],
    openapi_config=OpenAPIConfig(
        title="User Management API",
        version="2.0.0"
    ),
)
```

## Authentication

Authentication is a **Litestar middleware** concern. Apps with an existing auth
middleware get MCP authentication for free — `request.user` and `request.auth`
are populated before tool handlers run. Three integration paths:

### Path A — Bring Your Own Middleware

If your Litestar app already ships an `AbstractAuthenticationMiddleware` (or
Litestar's built-in JWT backends), MCP inherits it automatically:

```python
from litestar import Litestar
from litestar.middleware import DefineMiddleware
from litestar_mcp import LitestarMCP, MCPConfig

app = Litestar(
    route_handlers=[...],
    plugins=[LitestarMCP(MCPConfig())],
    middleware=[DefineMiddleware(YourAuthMiddleware)],  # MCP gets this for free
)
```

See `docs/examples/notes/sqlspec/google_iap.py` for a runnable example.

### litestar-security

Use litestar-security for policy evaluation and RFC 9728 protected-resource metadata. Pass route policy metadata with `MCPConfig(route_opt={"auth": required("api-key")})`; evaluator failures can emit the required `WWW-Authenticate` resource metadata.

### In-process stdio

Run `litestar --app my_app:app mcp stdio` to serve the application in-process to a desktop MCP client. Use `mcp bridge` when the MCP server is already running over Streamable HTTP.

Stdio uses native `Litestar.lifespan()`. Its `shutdown_timeout` bounds request
cleanup and shutdown after startup completes; application hooks own bounded,
cancellation-safe startup rollback. `MCPStdioContext.session` remains an
application session. Provide a verified identity for protected tasks;
anonymous stdio requests have no owner ID.

## Development

```bash
# Clone the repository
git clone https://github.com/cofin/litestar-mcp.git
cd litestar-mcp

# Install with development dependencies
make install

# Run the complete Python quality gate
make check-all

# Run the pinned MCP 2026-07-28 conformance suite
make conformance

# Build strict documentation and validate examples
make docs
make validate-examples validate-uvx validate-pep723

# Run example
uv run python docs/examples/hello_world/main.py
```

The conformance target owns Node.js `24.18.1` through `NODE_VERSION` in the
Makefile. When [nodenv](https://github.com/nodenv/nodenv) is available, the
target selects that version with `NODENV_VERSION`; otherwise it uses the
active `node`/`npm` installation. A local `.node-version` is ignored so
contributors can use nodenv without changing repository state.

The conformance runner reports passed, waived and failed scenarios separately.
Its pinned alpha validator has documented task-extension schema waivers in
`tools/ci/run_mcp_conformance.py`; `MCP_CONFORMANCE_STRICT=1` disables those
waivers. A waived check is not a protocol pass.

## License

MIT License. See [LICENSE](LICENSE) for details.

## Contributing

Contributions welcome! Please see our [contribution guide](docs/contribution-guide.rst) for details.
