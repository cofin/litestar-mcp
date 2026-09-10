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

Route Handler Options
~~~~~~~~~~~~~~~~~~~~~

The standalone decorators accept the same route-handler keyword arguments as Litestar's ``@get`` / ``@post`` decorators, including ``dependencies``, ``guards``, ``response_headers``, ``responses``, ``summary``, ``tags``, DTO options, hooks, and arbitrary extra keyword arguments that Litestar stores in ``handler.opt``. The ``name`` keyword is reserved for the MCP primitive name; use ``route_name`` when you need to set Litestar's route-handler name separately.

.. literalinclude:: /examples/snippets/standalone_dependencies.py
    :language: python

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

The default ``transport="streamable-http"`` starts the modern MCP HTTP
endpoint through the standard Litestar CLI. The old ``"sse"`` selector is
removed; SSE remains the response format for progress and subscriptions.

.. literalinclude:: /examples/snippets/standalone_run.py
    :language: python
    :start-after: # [start-run]
    :end-before: # [end-run]

Exposing the Application
~~~~~~~~~~~~~~~~~~~~~~~~

Because the HTTP server is executed via the Litestar CLI, the application
instance must be importable from disk. Install and configure your chosen ASGI
server separately; Uvicorn is not a required runtime dependency of this package.

You **must** expose the underlying Litestar application instance globally (e.g. ``app = mcp.app``) so that the CLI and worker processes can discover it. If the import path cannot be resolved, a ``RuntimeError`` will be raised.

Passing CLI Arguments
~~~~~~~~~~~~~~~~~~~~~

Keyword arguments passed to ``mcp.run()`` with ``transport="streamable-http"``
are forwarded to the corresponding Litestar CLI options (for example,
``port`` becomes ``--port`` and ``reload`` becomes ``--reload``).

Stdio Transport
~~~~~~~~~~~~~~~

To run the server over standard input/output (Stdio) for integration with local MCP clients (such as Claude Desktop), set the ``transport`` parameter to ``"stdio"``.

.. literalinclude:: /examples/snippets/standalone_run_stdio.py
    :language: python
    :start-after: # [start-run-stdio]
    :end-before: # [end-run-stdio]

Stdio enters native ``Litestar.lifespan()`` so application and plugin lifecycle
resources use Litestar's ordering. ``run_stdio_async(shutdown_timeout=5.0)``
bounds in-process request cleanup and post-startup application shutdown.
If that shutdown fails or times out, the original bridge/body error or
cancellation is preserved. Native startup unwind is outside this timeout;
application hooks must bound and shield their own rollback where needed.
``sse_read_timeout`` remains a useful stream read timeout, independent of cleanup.

Stdio does not have an HTTP header layer, so do not tunnel bearer headers through stdin. Resolve credentials from the host environment, operating-system profile, or another local mechanism, then pass the resulting identity with :class:`~litestar_mcp.MCPStdioContext`::

    from types import SimpleNamespace

    from litestar_mcp import MCPStdioContext

    stdio_context = MCPStdioContext(
        user=SimpleNamespace(id="local-user"),
        auth={"sub": "local-user"},
        session={"tenant": "local"},
        state={"profile": "developer"},
    )

    mcp.run(transport="stdio", stdio_context=stdio_context)

Tools, resources, prompts, guards, and task execution receive these values
through the synthetic Litestar request scope. ``session`` is a Litestar
application session, not MCP transport-session state. Task ownership uses an
explicit ``owner_id`` when set, otherwise ``user:<subject>`` from
``auth["sub"]`` or ``user.id`` / ``user.sub``. Anonymous requests have no
owner ID; there is no shared ``"stdio"`` owner. Require authenticated identity
for protected tasks instead of treating anonymous task handles as authorization.
