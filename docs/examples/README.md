# Litestar MCP Plugin Examples

This directory contains examples demonstrating the Litestar MCP Plugin integration with practical usage patterns.

## Examples Overview

### 📁 basic/

**Hello World Example**

- ✅ Minimal MCP plugin setup (just 3 lines!)
- ✅ Simple REST API endpoints
- ✅ OpenAPI schema access via MCP
- ✅ Shows how to mark routes for MCP exposure

**Best for**: Getting started, understanding core concepts

### 📁 advanced/

**Task Management API Example**

- ✅ Multiple MCP tools for CRUD operations
- ✅ MCP resources for schemas and API info
- ✅ Mix of GET, POST, and DELETE endpoints
- ✅ Demonstrates both tools and resources in a practical context

**Best for**: Learning comprehensive MCP integration patterns

## Quick Start Guide

### 1. Setup

All examples require the base dependencies:

```bash
uv add litestar uvicorn
```

### 2. Running Examples

Each example directory contains:

- `main.py` - The application code
- `README.md` - Detailed setup instructions

```bash
cd examples/basic/
uv run python main.py
```

## MCP Features Demonstrated

The current implementation provides:

| Feature | Basic | Advanced |
|---------|-------|----------|
| Plugin integration | ✅ | ✅ |
| OpenAPI resource | ✅ | ✅ |
| Route marking with kwargs | ✅ | ✅ |
| MCP endpoints | ✅ | ✅ |
| Multiple MCP tools | - | ✅ |
| Multiple MCP resources | - | ✅ |
| CRUD operations via MCP | - | ✅ |

## How Route Marking Works

Mark your routes to expose them via MCP:

```python
from litestar import get, post
from litestar_mcp import LitestarMCP

# Expose as MCP tool (executable)
@get("/users", mcp_tool="list_users")
async def get_users() -> list[dict]:
    """List users - executable via MCP."""
    return [{"id": 1, "name": "Alice"}]

# Expose as MCP resource (readable)
@get("/schema", mcp_resource="user_schema")
async def get_user_schema() -> dict:
    """User schema - readable via MCP."""
    return {"type": "object", "properties": {}}

# Regular route - not exposed to MCP
@get("/health")
async def health_check() -> dict:
    return {"status": "ok"}

app = Litestar(
    route_handlers=[get_users, get_user_schema, health_check],
    plugins=[LitestarMCP()]
)
```

## Common MCP Interactions

### Exploring the Application

> **AI**: "What's available in this application?"
>
> **Response**: *AI can access the OpenAPI resource to understand all endpoints*

### Using Marked Routes

> **AI**: "List the users in the system"
>
> **Response**: *AI can execute the `list_users` tool if the route was marked with `mcp_tool`*

### Accessing Schemas

> **AI**: "What's the structure of user data?"
>
> **Response**: *AI can read the `user_schema` resource if the route was marked with `mcp_resource`*

## Development Workflow

### 1. Start Simple

Begin with the basic example to understand core concepts:

```bash
cd examples/basic/
uv run python main.py
```

### 2. Build Your Own

Use the basic example as a template:

```python
from litestar import Litestar, get
from litestar_mcp import LitestarMCP, MCPConfig

# Mark routes you want exposed to MCP
@get("/data", mcp_tool="get_data")
async def get_data() -> dict:
    return {"data": "example"}

@get("/info", mcp_resource="app_info")
async def get_info() -> dict:
    return {"name": "My App", "version": "1.0"}

# Configure the plugin
config = MCPConfig(
    name="My Application",
    base_path="/mcp"
)

app = Litestar(
    route_handlers=[get_data, get_info],
    plugins=[LitestarMCP(config)]
)
```

## Troubleshooting

### Common Issues

**Import errors**: Make sure you have all dependencies installed

```bash
uv add litestar uvicorn
```

**MCP endpoints not working**: Check that the plugin is properly added to your Litestar app

**Routes not appearing in MCP**: Ensure you've marked them with `mcp_tool` or `mcp_resource` kwargs

### Getting Help

1. **Check MCP endpoints**: Visit `http://127.0.0.1:8000/mcp/` to see server info
2. **Test resources**: Visit `http://127.0.0.1:8000/mcp/resources` to see available resources
3. **Test tools**: Visit `http://127.0.0.1:8000/mcp/tools` to see available tools
4. **Main documentation**: See the main README.md in the repository root

## Configuration Options

The plugin supports minimal configuration:

```python
from litestar_mcp import MCPConfig

config = MCPConfig(
    base_path="/mcp",              # Base path for MCP endpoints
    include_in_schema=False,       # Include MCP routes in OpenAPI schema
    name=None,                     # Override server name (uses OpenAPI title by default)
)
```

## Next Steps

After working through these examples:

1. **Read the main documentation**: See README.md in the repository root
2. **Mark your own routes**: Add `mcp_tool` or `mcp_resource` to your endpoints
3. **Test with AI models**: Use MCP clients to interact with your application

Happy building with Litestar and MCP! 🚀
