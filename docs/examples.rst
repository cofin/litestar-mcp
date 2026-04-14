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

Location: ``docs/examples/basic/``

A minimal "Hello World" application that demonstrates:

- Basic MCP plugin setup
- Default configuration usage
- Streamable HTTP MCP transport on a Litestar route

.. code-block:: python

    from litestar import Litestar, get
    from litestar_mcp import LitestarMCP, MCPConfig

    @get("/")
    async def hello() -> dict[str, str]:
        return {"message": "Hello from Litestar!"}

    app = Litestar(
        route_handlers=[hello],
        plugins=[LitestarMCP(MCPConfig(name="Hello World API"))]
    )

**Running the example:**

.. code-block:: bash

    cd docs/examples/basic/
    uv run python main.py

**Available endpoints:**

- ``http://127.0.0.1:8000/`` - Hello endpoint
- ``http://127.0.0.1:8000/mcp`` - MCP Streamable HTTP endpoint
- ``http://127.0.0.1:8000/.well-known/mcp-server.json`` - MCP server manifest
- ``http://127.0.0.1:8000/.well-known/agent-card.json`` - Agent metadata document

Advanced Example
================

Location: ``docs/examples/advanced/``

A task management application that demonstrates:

- MCP tools exposed directly from Litestar route handlers
- MCP resources backed by standard Litestar endpoints
- Mixed GET / POST / DELETE handler discovery
- OpenAPI metadata feeding MCP discovery responses

**Features:**

- List tasks with optional filtering
- Create and complete tasks
- Delete tasks
- Expose task schema and API info as MCP resources

**Running the example:**

.. code-block:: bash

    cd docs/examples/advanced/
    uv run python main.py

**Exposed MCP Tools:**

- ``list_tasks`` - List tasks, optionally filtered by completion status
- ``get_task`` - Retrieve a specific task by ID
- ``create_task`` - Create a new task
- ``complete_task`` - Mark a task as completed
- ``delete_task`` - Delete a task by ID

**Exposed MCP Resources:**

- ``task_schema`` - Task model schema
- ``api_info`` - API capability summary

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
- **Debugging**: Inspect discovered tools and resources over MCP
- **Integration**: Enable AI-powered development tools

Next Steps
==========

- Create your own MCP-enabled routes based on these examples
- Explore the :doc:`usage/marking-routes` guide
- Check the :doc:`reference/index` for API details
