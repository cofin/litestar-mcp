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

    from advanced_alchemy.extensions.litestar import (
        SQLAlchemyAsyncConfig,
        SQLAlchemyPlugin,
        providers,
        repository,
        service,
    )
    from litestar import Controller, Litestar, get

    class TaskService(service.SQLAlchemyAsyncRepositoryService[TaskRecord]):
        class Repo(repository.SQLAlchemyAsyncRepository[TaskRecord]):
            model_type = TaskRecord

        repository_type = Repo


    class TaskController(Controller):
        path = "/tasks"
        dependencies = providers.create_service_dependencies(
            TaskService,
            "task_service",
            filters={
                "pagination_type": "limit_offset",
                "id_filter": int,
                "search": "title",
                "search_ignore_case": True,
                "in_fields": {FieldNameType("completed", bool)},
            },
        )

        @get("/{task_id:int}", opt={"mcp_tool": "get_task"})
        async def get_task(self, task_service: TaskService, task_id: int) -> Task:
            obj = await task_service.get(
                task_id,
                error_messages={"not_found": f"Task {task_id} not found"},
            )
            return task_service.to_schema(obj, schema_type=Task)

``providers.create_service_dependencies`` wires the per-request
``TaskService`` through the Litestar DI container — ``LitestarMCP`` picks up
the same dependency when the tool is invoked over ``POST /mcp``, so tool
handlers get a live session without any MCP-specific plumbing.

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

- ``list_tasks`` - List stored tasks with pagination, title search, and
  ``completedIn`` collection filtering (``?completedIn=true``)
- ``get_task`` - Retrieve a specific task by ID
- ``create_task`` - Create a new task
- ``complete_task`` - Mark a task as completed
- ``delete_task`` - Delete a specific task

**Custom MCP Resources:**

- ``api_info`` - Static API metadata (name, version, features) — designed
  to be cacheable, so it deliberately avoids live database lookups
- ``task_schema`` - JSON Schema for the task model, generated directly
  from ``Task.model_json_schema()`` so it can't drift from the code

**Testing the Task System:**

MCP speaks JSON-RPC 2.0 over a single ``POST /mcp`` endpoint. Tool calls and
resource reads are both request methods, not REST paths:

.. code-block:: bash

    # Create a task via the create_task MCP tool
    curl -X POST http://127.0.0.1:8000/mcp \\
      -H 'Content-Type: application/json' \\
      -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
          "name": "create_task",
          "arguments": {
            "data": {
              "title": "Write release notes",
              "description": "Summarize the latest changes"
            }
          }
        }
      }'

    # Read the api_info MCP resource
    curl -X POST http://127.0.0.1:8000/mcp \\
      -H 'Content-Type: application/json' \\
      -d '{
        "jsonrpc": "2.0",
        "id": 2,
        "method": "resources/read",
        "params": {"uri": "litestar://api_info"}
      }'

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
