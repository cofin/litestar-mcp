===============
Getting Started
===============

Installation
------------

Install from PyPI using pip:

.. code-block:: bash

    pip install litestar-mcp

Or using uv:

.. code-block:: bash

    uv add litestar-mcp

Basic Usage
-----------

The simplest way to add MCP support to your Litestar application:

.. code-block:: python

    from litestar import Litestar, get
    from litestar_mcp import LitestarMCP

    @get("/")
    async def hello() -> dict[str, str]:
        return {"message": "Hello from Litestar!"}

    # Add MCP plugin with default configuration
    app = Litestar(
        route_handlers=[hello],
        plugins=[LitestarMCP()]
    )

That's it! Your application now speaks MCP over a single JSON-RPC 2.0
endpoint at ``POST /mcp``. Clients dispatch on the ``method`` field:

- ``initialize`` - Handshake (capabilities, server info)
- ``tools/list`` - Enumerate tools registered from marked routes
- ``tools/call`` - Invoke a tool by name with its arguments
- ``resources/list`` - Enumerate resources (includes the built-in ``openapi`` resource)
- ``resources/read`` - Read a resource by ``uri`` (e.g. ``litestar://openapi``)

There is **no** ``GET /mcp/tools`` or ``GET /mcp/resources/<name>``; MCP is
JSON-RPC, not REST, so all interaction is ``POST /mcp`` with a JSON-RPC
envelope. See :doc:`examples` for worked ``curl`` examples.

Marking Routes for MCP Exposure
--------------------------------

To expose your routes as MCP tools or resources, mark them using kwargs:

.. code-block:: python

    from litestar import Litestar, get, post
    from litestar_mcp import LitestarMCP

    # Mark a route as an MCP tool (executable)
    @get("/users", mcp_tool="list_users")
    async def get_users() -> list[dict]:
        """List all users in the system."""
        return [{"id": 1, "name": "Alice"}]

    # Mark a route as an MCP resource (readable data)
    @get("/schema", mcp_resource="user_schema")
    async def get_user_schema() -> dict:
        """Get the user data schema."""
        return {"type": "object", "properties": {"id": "integer", "name": "string"}}

    # Regular routes are not exposed to MCP
    @get("/health")
    async def health_check() -> dict:
        return {"status": "ok"}

    app = Litestar(
        route_handlers=[get_users, get_user_schema, health_check],
        plugins=[LitestarMCP()]
    )

Configuration
-------------

Customize the MCP integration with ``MCPConfig``:

.. code-block:: python

    from litestar_mcp import MCPConfig, LitestarMCP

    config = MCPConfig(
        base_path="/api/mcp",         # Change base path (default: "/mcp")
        include_in_schema=True,       # Include MCP routes in OpenAPI (default: False)
        name="My API Server",         # Override server name (default: from OpenAPI)
    )

    app = Litestar(
        route_handlers=[...],
        plugins=[LitestarMCP(config)]
    )

Resources vs Tools
------------------

**Use Resources (mcp_resource) for:**

- Read-only data that AI models need to reference
- Static information like schemas, documentation, configuration
- Data that doesn't require parameters to retrieve

**Use Tools (mcp_tool) for:**

- Operations that perform actions or mutations
- Dynamic queries that need input parameters
- Any operation that changes state

Testing Your Integration
------------------------

Start your application and drive the MCP endpoint with JSON-RPC:

.. code-block:: bash

    # Start your app
    uvicorn myapp:app --reload

    # Enumerate tools
    curl -X POST http://localhost:8000/mcp \
      -H 'Content-Type: application/json' \
      -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'

    # Enumerate resources
    curl -X POST http://localhost:8000/mcp \
      -H 'Content-Type: application/json' \
      -d '{"jsonrpc":"2.0","id":2,"method":"resources/list"}'

    # Read the built-in OpenAPI resource
    curl -X POST http://localhost:8000/mcp \
      -H 'Content-Type: application/json' \
      -d '{"jsonrpc":"2.0","id":3,"method":"resources/read","params":{"uri":"litestar://openapi"}}'

You should see JSON-RPC 2.0 responses describing your application's MCP
capabilities.

For interactive debugging with a real MCP client, point the official
`MCP Inspector <https://github.com/modelcontextprotocol/inspector>`_ at
your running server — it speaks Streamable HTTP natively and gives you
a browser UI for every tool and resource:

.. code-block:: bash

    # In another terminal, with uvicorn still running:
    npx @modelcontextprotocol/inspector

In the Inspector UI, select **Transport: Streamable HTTP**,
**URL: http://127.0.0.1:8000/mcp**, click **Connect**, and you'll get
clickable tabs for Tools, Resources, and Prompts plus a raw JSON-RPC
log panel. See :doc:`examples` for a walk-through with the shipped
example apps.

You can also introspect and run tools offline without starting a server
using Litestar's CLI:

.. code-block:: bash

    uv run litestar --app myapp:app mcp list-tools
    uv run litestar --app myapp:app mcp list-resources
    uv run litestar --app myapp:app mcp run <tool-or-resource-name>

See :doc:`examples` for the full CLI surface.

Built-in Resources
------------------

The plugin automatically provides one built-in resource:

- ``openapi`` - Your application's OpenAPI schema (always available)

Examples
--------

See the ``docs/examples/`` directory for complete working examples:

- ``docs/examples/basic/`` - Simple integration with marked routes
- ``docs/examples/advanced/`` - SQLite-backed task management with dependency injection

Next Steps
----------

- :doc:`examples` - See practical usage examples
- :doc:`usage/index` - Learn more about configuration options
- :doc:`reference/index` - API reference documentation
