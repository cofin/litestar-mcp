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
- Essential MCP endpoints

.. code-block:: python

    from litestar import Litestar, get
    from litestar_mcp import LitestarMCP, MCPConfig

    @get("/")
    async def hello() -> dict[str, str]:
        return {"message": "Hello from Litestar!"}

    app = Litestar(
        route_handlers=[hello],
        plugins=[LitestarMCP(MCPConfig(debug_mode=True))]
    )

**Running the example:**

.. code-block:: bash

    cd docs/examples/basic/
    uv run python main.py

**Available endpoints:**

- ``http://127.0.0.1:8000/`` - Hello endpoint
- ``http://127.0.0.1:8000/mcp/`` - MCP server info
- ``http://127.0.0.1:8000/mcp/resources`` - Available resources
- ``http://127.0.0.1:8000/mcp/tools`` - Available tools

Advanced Example
================

Location: ``docs/examples/advanced/``

A SQLite-backed task management application that demonstrates:

- SQLite persistence via ``advanced-alchemy``
- Dependency injection for MCP tools and resources
- CRUD operations exposed through MCP-marked Litestar routes
- Real-world application structure with a small service layer

**Features:**

- Persist tasks in SQLite with ``advanced-alchemy``
- Retrieve task metadata through an MCP resource
- List, create, complete, and delete tasks through MCP tools
- Share the same injected service across tools and resources

.. code-block:: python

    from advanced_alchemy.extensions.litestar import SQLAlchemyAsyncConfig
    from litestar import get
    from litestar.di import Provide

    async def provide_task_service() -> TaskService:
        await ensure_database_ready()
        return TaskService()

    @get(
        "/api/info",
        mcp_resource="api_info",
        dependencies={"task_service": Provide(provide_task_service)},
    )
    async def get_api_info(task_service: TaskService) -> dict[str, Any]:
        return {
            "storage_backend": task_service.storage_backend,
            "tasks_count": await task_service.count_tasks(),
        }

**Running the example:**

.. code-block:: bash

    cd docs/examples/advanced/
    uv run python main.py

**Dependencies:**

- ``litestar``
- ``uvicorn``
- ``advanced-alchemy``
- ``aiosqlite``

**Custom MCP Tools:**

- ``list_tasks`` - List stored tasks with optional completion filtering
- ``get_task`` - Retrieve a specific task by ID
- ``create_task`` - Create a new task
- ``complete_task`` - Mark a task as completed
- ``delete_task`` - Delete a specific task

**Custom MCP Resources:**

- ``api_info`` - Inspect API metadata and SQLite-backed storage details
- ``task_schema`` - Read the task schema exposed to MCP clients

**Testing the Task System:**

.. code-block:: bash

    # Create a task via MCP tool
    curl -X POST http://127.0.0.1:8000/mcp/tools/create_task \\
      -H 'Content-Type: application/json' \\
      -d '{"title": "Write release notes", "description": "Summarize the latest changes"}'

    # Inspect SQLite-backed API metadata via MCP resource
    curl http://127.0.0.1:8000/mcp/resources/api_info

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
