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

That's it! Your application now has MCP endpoints available at:

- ``/mcp/`` - Server information
- ``/mcp/resources`` - Available resources (includes OpenAPI schema)
- ``/mcp/tools`` - Available tools (from marked routes)

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

Start your application and test the MCP endpoints:

.. code-block:: bash

    # Start your app
    uvicorn myapp:app --reload

    # Test the MCP endpoints
    curl http://localhost:8000/mcp/
    curl http://localhost:8000/mcp/resources
    curl http://localhost:8000/mcp/tools

You should see JSON responses with your application's MCP capabilities.

Built-in Resources
------------------

The plugin automatically provides one built-in resource:

- ``openapi`` - Your application's OpenAPI schema (always available)

Examples
--------

See the ``examples/`` directory for complete working examples:

- ``examples/basic/`` - Simple integration with marked routes

Next Steps
----------

- :doc:`examples` - See practical usage examples
- :doc:`usage/index` - Learn more about configuration options
- :doc:`reference/index` - API reference documentation
