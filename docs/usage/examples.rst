========
Examples
========

This section provides links to the example applications and code snippets demonstrating various uses of the Litestar MCP Plugin.

Available Examples
------------------

The examples are located in the ``examples/`` directory of the project repository:

Basic Example
~~~~~~~~~~~~~

The basic example demonstrates minimal MCP integration:

- **Location**: ``docs/examples/basic/``
- **Features**: Simple plugin setup with marked routes
- **Demonstrates**: Tool and resource exposure through route marking

See the :doc:`../examples` section for detailed code and explanation.

Advanced Example
~~~~~~~~~~~~~~~~

The advanced example shows more complex usage patterns:

- **Location**: ``docs/examples/advanced/``
- **Features**: Complex route handlers, dependency injection, error handling
- **Demonstrates**: Real-world integration patterns

Code Snippets
-------------

Quick Start
~~~~~~~~~~~

.. code-block:: python

    from litestar import Litestar, get
    from litestar_mcp import LitestarMCP

    @get("/hello", mcp_tool="say_hello")
    async def hello() -> dict:
        return {"message": "Hello from MCP!"}

    app = Litestar(
        route_handlers=[hello],
        plugins=[LitestarMCP()]
    )

Tool with Parameters
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python

    @get("/greet/{name:str}", mcp_tool="greet_user")
    async def greet_user(name: str) -> dict:
        return {"greeting": f"Hello, {name}!"}

Resource Example
~~~~~~~~~~~~~~~~

.. code-block:: python

    @get("/api/schema", mcp_resource="api_schema")
    async def get_api_schema() -> dict:
        return {
            "openapi": "3.0.0",
            "info": {"title": "My API", "version": "1.0.0"}
        }

Running the Examples
--------------------

To run any of the examples:

.. code-block:: bash

    # Navigate to example directory
    cd docs/examples/basic/

    # Run with uv
    uv run python main.py

    # Or run with python directly
    python main.py

Once running, you can access the MCP endpoints at:

- ``http://localhost:8000/mcp`` - MCP Streamable HTTP endpoint
- ``http://localhost:8000/.well-known/mcp-server.json`` - MCP server manifest
- ``http://localhost:8000/.well-known/agent-card.json`` - Agent metadata document

Testing MCP Integration
-----------------------

You can test the MCP endpoints using curl:

.. code-block:: bash

    # Initialize the server
    curl -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"curl","version":"1.0"}}}'

    # List available tools
    curl -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}'

    # Execute a tool
    curl -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"say_hello","arguments":{"name":"Litestar"}}}'

    # List resources
    curl -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":4,"method":"resources/list","params":{}}'

    # Read the OpenAPI resource
    curl -X POST http://localhost:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{"jsonrpc":"2.0","id":5,"method":"resources/read","params":{"uri":"litestar://openapi"}}'
