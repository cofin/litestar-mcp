========
Examples
========

This section covers the example applications included with Litestar MCP.

.. toctree::
   :hidden:
   :maxdepth: 1

   examples/README
   examples/basic/README

Basic Example
=============

Location: ``examples/basic/``

A minimal "Hello World" application that demonstrates:

- Basic MCP plugin setup
- Default configuration usage
- Essential MCP endpoints

.. code-block:: python

    from litestar import Litestar, get
    from litestar_mcp import LitestarMCP, MCPConfig

    @get("/")
    async def hello() -> dict[str, str]:
        return {"message": "Hello from Litestar!"}

    app = Litestar(
        route_handlers=[hello],
        plugins=[LitestarMCP(MCPConfig())]
    )

**Running the example:**

.. code-block:: bash

    cd examples/basic/
    uv run python main.py

**Available endpoints:**

- ``http://127.0.0.1:8000/`` - Hello endpoint
- ``http://127.0.0.1:8000/mcp/`` - MCP server info
- ``http://127.0.0.1:8000/mcp/messages`` - Unified MCP endpoint
- ``http://127.0.0.1:8000/mcp/resources`` - Available resources
- ``http://127.0.0.1:8000/mcp/tools`` - Available tools

Advanced Example
================

Location: ``examples/advanced/``

A task management application that demonstrates:

- Multiple MCP tools for CRUD operations
- MCP resources for schema and API info
- Mixed GET/POST/DELETE routes
- OpenAPI integration

**Features:**

- Create, list, and update tasks
- Query task schema as MCP resource
- Mix of tools and resources

.. code-block:: python

    from litestar import get, post

    @get("/tasks/schema", mcp_resource="task_schema")
    async def get_task_schema() -> dict:
        return {"type": "object", "properties": {"id": {"type": "integer"}}}

    @post("/tasks", mcp_tool="create_task")
    async def create_task(data: dict) -> dict:
        return {"id": 1, "title": data["title"]}

**Running the example:**

.. code-block:: bash

    cd examples/advanced/
    uv run python main.py

**MCP Tools:**

- ``create_task`` - Create a new task
- ``list_tasks`` - List tasks
- ``complete_task`` - Mark task complete

**MCP Resources:**

- ``task_schema`` - Task schema
- ``api_info`` - API metadata

**Testing the Task System:**

.. code-block:: bash

    # Create a task via MCP tool
    curl -X POST http://127.0.0.1:8000/mcp/tools/create_task \\
      -H 'Content-Type: application/json' \\
      -d '{"arguments": {"data": {"title": "Write MCP docs", "description": "Update examples"}}}'

    # Get schema via MCP resource
    curl http://127.0.0.1:8000/mcp/resources/task_schema

Example Use Cases
=================

**For AI Models:**

The MCP endpoints enable AI models to:

1. **Explore your API**: Discover available routes and their parameters
2. **Validate requests**: Check if endpoints exist before making requests
3. **Access data**: Retrieve application-specific information
4. **Execute tools**: Perform custom operations you define

**For Development:**

- **API Documentation**: MCP provides machine-readable API metadata
- **Testing**: Validate your application structure programmatically
- **Debugging**: Inspect application state and configuration
- **Integration**: Enable AI-powered development tools

Next Steps
==========

- Create your own MCP-enabled routes based on these examples
- Explore the :doc:`usage/marking-routes` guide
- Check the :doc:`reference/index` for API details
