# Litestar MCP Plugin

A lightweight plugin that integrates Litestar web applications with the Model Context Protocol (MCP) by exposing marked routes as MCP tools and resources through REST API endpoints.

[![PyPI - Version](https://img.shields.io/pypi/v/litestar-mcp)](https://pypi.org/project/litestar-mcp/)
[![Python Version](https://img.shields.io/pypi/pyversions/litestar-mcp)](https://pypi.org/project/litestar-mcp/)
[![License](https://img.shields.io/badge/license-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## Overview

This plugin automatically discovers routes marked with the `opt` attribute and exposes them as MCP-compatible REST endpoints. Routes marked with `mcp_tool="name"` become executable tools, while routes marked with `mcp_resource="name"` become readable resources.

## Features

- 🚀 **Zero Dependencies** - Only requires Litestar
- 📡 **REST API Endpoints** - No stdio transport or MCP libraries needed
- 🔧 **Simple Route Marking** - Use Litestar's `opt` attribute pattern
- 🛡️ **Type Safe** - Full type hints with dataclasses
- 📊 **Automatic Discovery** - Routes are discovered at app initialization
- 🎯 **OpenAPI Integration** - Server info derived from OpenAPI config

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
3. **MCP Endpoints**: The plugin adds REST endpoints under the configured base path (default `/mcp`)
4. **Server Info**: Server name and version are derived from your OpenAPI configuration

## MCP Endpoints

Once configured, your application exposes these MCP-compatible endpoints:

- `GET /mcp/` - Server info and capabilities
- `GET /mcp/resources` - List available resources
- `GET /mcp/resources/{name}` - Get specific resource content
- `GET /mcp/tools` - List available tools
- `POST /mcp/tools/{name}` - Execute a tool

**Built-in Resources:**

- `openapi` - Your application's OpenAPI schema (always available)

## Configuration

Configure the plugin using `MCPConfig`:

```python
from litestar_mcp import MCPConfig

config = MCPConfig()
```

**Configuration Options:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `base_path` | `str` | `"/mcp"` | Base path for MCP API endpoints |
| `include_in_schema` | `bool` | `False` | Whether to include MCP routes in OpenAPI schema |
| `name` | `str \| None` | `None` | Override server name. If None, uses OpenAPI title |

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

## Development

```bash
# Clone the repository
git clone https://github.com/litestar-org/litestar-mcp.git
cd litestar-mcp

# Install with development dependencies
uv install --dev

# Run tests
make test

# Run example
uv run python docs/examples/basic/main.py
```

## License

MIT License. See [LICENSE](LICENSE) for details.

## Contributing

Contributions welcome! Please see our [contributing guide](CONTRIBUTING.md) for details.
