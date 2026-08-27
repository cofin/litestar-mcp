# Litestar MCP & A2A

A lightweight, multi-protocol toolkit that integrates Litestar web applications with both the **Model Context Protocol (MCP)** and the **Agent-to-Agent (A2A)** protocol. Expose marked routes as MCP tools, resources, and prompts or as A2A skills and agent cards over Streamable HTTP, SSE, and JSON-RPC 2.0.

[![PyPI - Version](https://img.shields.io/pypi/v/litestar-mcp)](https://pypi.org/project/litestar-mcp/)
[![Python Version](https://img.shields.io/pypi/pyversions/litestar-mcp)](https://pypi.org/project/litestar-mcp/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## Overview

`litestar-mcp` connects Litestar applications to AI models and autonomous agent networks:
- **Model Context Protocol (MCP)**: Exposes routes and callables as tools, resources, and prompts for LLM tool calling.
- **Agent-to-Agent Protocol (A2A)**: Connects autonomous agents with dynamic agent card discovery (`/.well-known/agent-card.json`), task lifecycles, and real-time SSE streaming.
- **Multi-Protocol Coexistence**: Run MCP and A2A concurrently on the same Litestar server with zero route collisions and optional auto-export of MCP tools as A2A skills.

## Features

- **Protocol-Agnostic Core** — Unified JSON-RPC 2.0 engine, SSE streaming, signature introspection, and msgspec validation.
- **Full MCP Primitives** — Tools, resources (with RFC 6570 templates), and prompts.
- **Full A2A Primitives** — Dynamic agent cards, skill discovery, task lifecycles (`tasks/send`, `tasks/get`, `tasks/cancel`), and real-time streaming (`tasks/sendSubscribe`).
- **Zero-Boilerplate Standalone Runners** — High-level `MCP` and `Agent` runners for fast scripting.
- **Type Safe** — Strict typing with dataclasses, msgspec models, and full PEP 585/604 compliance.
- **Built-in CLI** — Modular `litestar mcp` and `litestar a2a` subcommands.
- **OIDC & Bearer Auth** — Built-in token validation and JWKS caching.

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
    # 4. Boot the server using Server-Sent Events (SSE)
    mcp.run(port=8000)
```

The standalone decorators accept Litestar route-handler keyword arguments such as `dependencies`, `guards`, `response_headers`, `responses`, `summary`, `tags`, DTO options, hooks, and arbitrary extra kwargs stored in `handler.opt`. The `name` keyword names the MCP primitive; use `route_name` to set Litestar's route-handler name separately.

### A2A Standalone Agent

Build autonomous agents exposing skills and dynamic agent cards (`/.well-known/agent-card.json`):

```python
from litestar_mcp import Agent

agent = Agent(name="MathAgent", description="Performs calculations")

@agent.skill(name="add", description="Add two integers")
def add(a: int, b: int) -> int:
    return a + b

app = agent.app

if __name__ == "__main__":
    agent.run(port=8000)
```

### Multi-Protocol Coexistence

Run both MCP and A2A protocols on the same Litestar server with automatic tool-to-skill export:

```python
from litestar import Litestar, get
from litestar_mcp import A2AConfig, A2APlugin, LitestarMCP, MCPConfig

@get("/tools/multiply", mcp_tool="multiply", mcp_description="Multiply numbers")
def multiply(a: int, b: int) -> int:
    return a * b

@get("/skills/greet", opt={"a2a_skill": "greet", "a2a_description": "Greet person"})
def greet(name: str) -> str:
    return f"Hello, {name}!"

mcp = LitestarMCP(config=MCPConfig(base_path="/mcp"))
a2a = A2APlugin(config=A2AConfig(base_path="/a2a", auto_export_mcp_tools=True))

app = Litestar(route_handlers=[multiply, greet], plugins=[mcp, a2a])
```

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

## Architecture

`litestar-mcp` is designed symmetrically around a protocol-agnostic core:

- `litestar_mcp.core`: Shared JSON-RPC 2.0 engine, SSE streaming, signature introspection, msgspec serialization, cursor pagination, and common typing.
- `litestar_mcp.mcp`: Model Context Protocol (MCP) transport, `LitestarMCP` plugin, `MCPConfig`, stdio bridge, OIDC auth, and standalone `MCP` runner.
- `litestar_mcp.a2a`: Agent-to-Agent (A2A) protocol transport, `A2APlugin`, `A2AConfig`, `AgentCard` manifests, task stores, and standalone `Agent` runner.
- `litestar_mcp.cli`: Modular Click CLI groups (`litestar mcp` and `litestar a2a`).
- `litestar_mcp`: Top-level re-exports for backward compatibility and rapid prototyping.

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
- `GET /.well-known/agent-card.json` - Agent metadata card
- `GET /.well-known/oauth-protected-resource` - OAuth protected resource metadata when auth is configured

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
| `guards` | `list[Any] \| None` | `None` | Litestar guards applied to the MCP router |
| `allowed_origins` | `list[str] \| None` | `None` | Exact additional Origins; present Origins must be same-origin or allowlisted |
| `include_operations` | `list[str] \| None` | `None` | Only expose matching operation names |
| `exclude_operations` | `list[str] \| None` | `None` | Exclude matching operation names |
| `include_tags` | `list[str] \| None` | `None` | Only expose routes with matching OpenAPI tags |
| `exclude_tags` | `list[str] \| None` | `None` | Exclude routes with matching OpenAPI tags |
| `auth` | `MCPAuthConfig \| None` | `None` | Metadata for `/.well-known/oauth-protected-resource` discovery |
| `tasks` | `bool \| MCPTaskConfig` | `False` | Enable the `io.modelcontextprotocol/tasks` extension |
| `cache_ttl_ms` | `int` | `0` | Conservative cache lifetime for discovery/list/resource results |
| `cache_scope` | `"private" \| "public"` | `"private"` | Cache sharing policy |
| `subscription_max_streams` | `int` | `10000` | Maximum concurrent `subscriptions/listen` streams |
| `subscription_keepalive_seconds` | `float` | `15.0` | Subscription keepalive interval |
| `subscription_channels` | `ChannelsPlugin \| None` | `None` | Optional cross-worker notification fan-out |
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

### Path B — Built-in MCPAuthBackend

For OIDC workloads, install the built-in `MCPAuthBackend`:

```python
from litestar import Litestar
from litestar.middleware import DefineMiddleware
from litestar_mcp import LitestarMCP, MCPAuthBackend, MCPConfig, OIDCProviderConfig
from litestar_mcp.auth import MCPAuthConfig

app = Litestar(
    route_handlers=[...],
    plugins=[LitestarMCP(MCPConfig(auth=MCPAuthConfig(
        issuer="https://company.okta.com",
        audience="api://mcp-tools",
    )))],
    middleware=[DefineMiddleware(
        MCPAuthBackend,
        providers=[OIDCProviderConfig(
            issuer="https://company.okta.com",
            audience="api://mcp-tools",
        )],
        user_resolver=lambda claims, app: MyUser(sub=claims["sub"]),
    )],
)
```

JWKS auto-discovery, caching, and `clock_skew` tolerance are built in.
See `docs/examples/notes/sqlspec/cloud_run_jwt.py` for the full pattern.

### Path C — Composable OIDC Factory

`create_oidc_validator()` returns an async callable for use as
`MCPAuthBackend(token_validator=...)` or inside your own middleware:

```python
from litestar_mcp import create_oidc_validator

validator = create_oidc_validator(
    "https://cloud.google.com/iap",
    "/projects/PROJECT_NUMBER/global/backendServices/SERVICE_ID",
    algorithms=("ES256",),
    jwks_cache_ttl=1800,
)
```

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

## License

MIT License. See [LICENSE](LICENSE) for details.

## Contributing

Contributions welcome! Please see our [contribution guide](docs/contribution-guide.rst) for details.
