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
        plugins=[LitestarMCP(MCPConfig(debug_mode=True))]
    )

**Running the example:**

.. code-block:: bash

    cd examples/basic/
    uv run python main.py

**Available endpoints:**

- ``http://127.0.0.1:8000/`` - Hello endpoint
- ``http://127.0.0.1:8000/mcp/`` - MCP server info
- ``http://127.0.0.1:8000/mcp/resources`` - Available resources
- ``http://127.0.0.1:8000/mcp/tools`` - Available tools

Advanced Example
================

Location: ``examples/advanced/``

A memory utility application that demonstrates:

- Custom resource handlers
- Custom tool handlers
- SQLite integration
- AI-usable memory system

**Features:**

- Save text memories with tags
- Retrieve memories by ID
- Search memories by tags
- Custom MCP tools for AI interaction

.. code-block:: python

    from litestar_mcp.handlers import CustomResourceHandler, CustomToolHandler

    class MemoryResourceHandler(CustomResourceHandler):
        async def get_content(self, app, resource_name):
            memories = search_memories(limit=50)
            return {"memories": [memory.dict() for memory in memories]}

    class MemoryToolHandler(CustomToolHandler):
        async def execute(self, app, tool_name, arguments):
            if tool_name == "save_memory":
                content = arguments.get("content")
                memory = save_memory(content)
                return ToolResult(success=True, data=memory.dict())

**Running the example:**

.. code-block:: bash

    cd examples/advanced/
    uv run python main.py

**Custom MCP Tools:**

- ``save_memory`` - Save a new memory with optional tags
- ``get_memory`` - Retrieve a specific memory by ID
- ``search_memories`` - Search memories by tags or get recent memories

**Custom MCP Resources:**

- ``memories`` - Access all stored memories

**Testing the Memory System:**

.. code-block:: bash

    # Save a memory via MCP tool
    curl -X POST http://127.0.0.1:8000/mcp/tools/save_memory \\
      -H 'Content-Type: application/json' \\
      -d '{"content": "Important meeting note", "tags": "work,meeting"}'

    # Get all memories via MCP resource
    curl http://127.0.0.1:8000/mcp/resources/memories

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
