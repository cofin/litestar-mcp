======================
Standalone Application
======================

The ``MCP`` class provides a high-level, developer-friendly entry point around Litestar and the ``LitestarMCP`` plugin, offering a declarative, simplified API.

It is the recommended entry point for projects where the primary purpose is exposing Model Context Protocol (MCP) tools, resources, and prompts, and you want to avoid boilerplates.

Basic Setup
-----------

To get started, instantiate the ``MCP`` class with a name and optional instructions.

.. literalinclude:: /examples/snippets/standalone_minimal.py
    :language: python
    :start-after: # [start-setup]
    :end-before: # [end-setup]

Decorators
----------

The application class provides dedicated decorators to expose Python functions as MCP primitives:

Exposing Tools
~~~~~~~~~~~~~~

Use the ``@mcp.tool()`` decorator to register executable functions. The signature arguments are automatically analyzed and exposed as JSON Schema to the client.

.. literalinclude:: /examples/snippets/standalone_minimal.py
    :language: python
    :pyobject: add

Exposing Resources
~~~~~~~~~~~~~~~~~~

Use the ``@mcp.resource()`` decorator to expose read-only data assets using URI templates (RFC 6570).

.. literalinclude:: /examples/snippets/standalone_resource.py
    :language: python
    :pyobject: get_status

Exposing Prompts
~~~~~~~~~~~~~~~~

Use the ``@mcp.prompt()`` decorator to expose pre-defined templates or instruction sets for LLMs.

.. literalinclude:: /examples/snippets/standalone_prompt.py
    :language: python
    :pyobject: explain

Accessing the Litestar App
--------------------------

The ``MCP`` instance lazily instantiates the underlying ``Litestar`` application when the ``.app`` property is accessed. This ensures that all route handlers registered via decorators are captured.

.. literalinclude:: /examples/snippets/standalone_app.py
    :language: python
    :start-after: # [start-app]
    :end-before: # [end-app]

You can pass standard Litestar arguments (such as custom plugins, guards, or middleware) directly to the ``MCP`` constructor, and they will be forwarded to the ``Litestar`` instance.

.. literalinclude:: /examples/snippets/standalone_custom.py
    :language: python

Running the Server
------------------

The ``MCP`` class provides a ``.run()`` method to programmatically start the server.

By default, the server runs using the Server-Sent Events (SSE) transport by programmatically executing the standard Litestar CLI command.

.. literalinclude:: /examples/snippets/standalone_run.py
    :language: python
    :start-after: # [start-run]
    :end-before: # [end-run]

Exposing the Application
~~~~~~~~~~~~~~~~~~~~~~~~

Because the SSE server is executed via the Litestar CLI (which starts uvicorn in subprocesses for worker scaling and reload features), the application instance *must* be importable from disk.

You **must** expose the underlying Litestar application instance globally (e.g. ``app = mcp.app``) so that the CLI and worker processes can discover it. If the import path cannot be resolved, a ``RuntimeError`` will be raised.

Passing CLI Arguments
~~~~~~~~~~~~~~~~~~~~~

Any keyword arguments passed to ``mcp.run()`` when using SSE transport are mapped and forwarded directly to the corresponding Litestar CLI options (e.g., ``port`` becomes ``--port``, ``reload`` becomes ``--reload``).

Stdio Transport
~~~~~~~~~~~~~~~

To run the server over standard input/output (Stdio) for integration with local MCP clients (such as Claude Desktop), set the ``transport`` parameter to ``"stdio"``.

.. literalinclude:: /examples/snippets/standalone_run_stdio.py
    :language: python
    :start-after: # [start-run-stdio]
    :end-before: # [end-run-stdio]

When running over Stdio, the server manually drives the ASGI application's lifespan, ensuring that all dynamic startup and shutdown hooks registered by other plugins execute correctly.
