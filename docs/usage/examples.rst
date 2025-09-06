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

- **Location**: ``examples/basic/``
- **Features**: Simple plugin setup with marked routes
- **Demonstrates**: Tool and resource exposure through route marking

See the :doc:`../examples` section for detailed code and explanation.

Advanced Example
~~~~~~~~~~~~~~~~

The advanced example shows more complex usage patterns:

- **Location**: ``examples/advanced/``
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
    cd examples/basic/

    # Run with uv
    uv run python main.py

    # Or run with python directly
    python main.py

Once running, you can access the MCP endpoints at:

- ``http://localhost:8000/mcp/`` - Server info
- ``http://localhost:8000/mcp/tools`` - Available tools
- ``http://localhost:8000/mcp/resources`` - Available resources

Testing MCP Integration
-----------------------

You can test the MCP endpoints using curl:

.. code-block:: bash

    # Get server info
    curl http://localhost:8000/mcp/

    # List available tools
    curl http://localhost:8000/mcp/tools

    # Execute a tool (if it accepts GET requests)
    curl http://localhost:8000/mcp/tools/say_hello

    # List resources
    curl http://localhost:8000/mcp/resources

    # Get a specific resource
    curl http://localhost:8000/mcp/resources/api_schema
