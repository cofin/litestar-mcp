# Basic Litestar MCP Example

This is the simplest possible example of integrating the Litestar MCP Plugin with a Litestar application.

## What This Example Demonstrates

- ✅ Basic MCP plugin integration
- ✅ Streamable HTTP and JSON-RPC transport
- ✅ Marking routes for MCP exposure using kwargs
- ✅ OpenAPI schema access via MCP resources

## Running the Example

1. **Install dependencies**:
   ```bash
   uv add litestar uvicorn
   ```

2. **Run the application**:
   ```bash
   uv run python main.py
   ```

3. **Test the MCP endpoints**:
   - The application will be available at `http://localhost:8000`
   - The MCP transport surface is available at `http://localhost:8000/mcp`

## Available MCP Resources

### OpenAPI Resource (`openapi`)
The application's OpenAPI schema is automatically available as an MCP resource:
```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"resources/read","params":{"uri":"litestar://openapi"}}'
```

This returns the complete OpenAPI specification for your application.

## Available MCP Tools

This basic example doesn't mark any routes as MCP tools. To add MCP tools, mark your routes like this:

```python
@get("/users", mcp_tool="list_users")
async def get_users() -> list[dict]:
    """List all users in the system."""
    return [{"id": 1, "name": "Alice"}]
```

## Available MCP Endpoints

Test the MCP integration with these endpoints:

```bash
# Initialize the MCP server
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'

# List available resources
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"resources/list","params":{}}'

# Open the SSE stream for server notifications
curl http://localhost:8000/mcp \
  -H "Accept: text/event-stream"

# List available tools
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/list","params":{}}'

# Get the OpenAPI schema
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":4,"method":"resources/read","params":{"uri":"litestar://openapi"}}'
```

## Testing with Curl

You can test the regular API endpoints directly:

```bash
# Get greeting
curl http://localhost:8000/

# Get status
curl http://localhost:8000/status
```

## Adding Your Own MCP Integration

To expose your own routes to MCP, mark them with kwargs:

```python
from litestar import get, post
from litestar_mcp import LitestarMCP

# Expose as an MCP tool (executable)
@get("/data", mcp_tool="get_data")
async def get_data(query: str) -> dict:
    """Get data based on query."""
    return {"query": query, "results": []}

# Expose as an MCP resource (readable)
@get("/schema", mcp_resource="api_schema")
async def get_schema() -> dict:
    """Get the API schema."""
    return {"type": "object", "properties": {}}

app = Litestar(
    route_handlers=[get_data, get_schema],
    plugins=[LitestarMCP()]
)
```

## Next Steps

Once you have this basic example running:

1. **Try marking routes**: Add `mcp_tool` or `mcp_resource` kwargs to your route handlers
2. **Test with AI models**: Use an MCP client to interact with your marked routes
3. **Build your own**: Use this as a template for your applications

For more complex examples, explore the main README.md in the repository root.
