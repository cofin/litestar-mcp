===============
Litestar MCP
===============

.. toctree::
    :titlesonly:
    :caption: Documentation
    :name: documentation
    :maxdepth: 2

    getting-started
    usage/index
    examples
    reference/index

.. toctree::
    :titlesonly:
    :caption: Development
    :name: development
    :maxdepth: 1

    contribution-guide
    changelog

Litestar plugin for Model Context Protocol (MCP) integration
============================================================

The Litestar MCP Plugin enables integration between Litestar web applications and the Model Context Protocol (MCP),
allowing AI models to interact with your marked application routes through a JSON-RPC 2.0 endpoint.

Features
--------

✨ **Simple Integration**: Mark routes with kwargs to expose them via MCP
🔧 **Lightweight**: Minimal configuration and dependencies
🚀 **Standards-Based**: Speaks MCP's JSON-RPC 2.0 protocol over a single HTTP endpoint
📊 **OpenAPI Integration**: Automatic OpenAPI schema exposure
🎯 **Type Safe**: Full type hints with dataclasses

Installation
------------

.. code-block:: bash

    pip install litestar-mcp

Quick Start
-----------

Add MCP capabilities to your Litestar application by marking routes:

.. code-block:: python

    from litestar import Litestar, get, post
    from litestar_mcp import LitestarMCP

    # Mark routes for MCP exposure using kwargs
    @get("/users", mcp_tool="list_users")
    async def get_users() -> list[dict]:
        """List all users - exposed as MCP tool."""
        return [{"id": 1, "name": "Alice"}]

    @get("/schema", mcp_resource="user_schema")
    async def get_schema() -> dict:
        """User schema - exposed as MCP resource."""
        return {"type": "object", "properties": {"id": "integer", "name": "string"}}

    # Regular routes are not exposed to MCP
    @get("/health")
    async def health_check() -> dict:
        return {"status": "ok"}

    # Add MCP plugin
    app = Litestar(
        route_handlers=[get_users, get_schema, health_check],
        plugins=[LitestarMCP()]
    )

Your application now exposes a single JSON-RPC 2.0 endpoint at ``POST /mcp``
that AI models can use to:

- 🔍 Discover marked routes via ``tools/list`` and ``resources/list``
- 📊 Access your application's OpenAPI schema via ``resources/read`` with ``uri=litestar://openapi``
- 🛠️ Execute marked tools via ``tools/call`` and read marked resources via ``resources/read``

Core Concepts
-------------

**Model Context Protocol (MCP)**
    An open standard that enables AI models to securely access and interact with external systems.

**Tools (mcp_tool)**
    Functions that AI models can execute - mark routes with ``mcp_tool="name"`` kwargs.

**Resources (mcp_resource)**
    Read-only data that AI models can access - mark routes with ``mcp_resource="name"`` kwargs.

**Route Marking**
    Use ``mcp_tool`` or ``mcp_resource`` kwargs in route decorators - Litestar automatically adds these to the route's opt dictionary.

How It Works
------------

1. **Mark Routes**: Add ``mcp_tool`` or ``mcp_resource`` kwargs to your route decorators
2. **Litestar Processing**: Litestar automatically moves these kwargs into the route handler's ``opt`` dictionary
3. **Plugin Discovery**: The plugin scans route handlers' opt dictionaries for MCP markers at app startup
4. **MCP Exposure**: Marked routes become addressable via the JSON-RPC 2.0 methods ``tools/call`` and ``resources/read``
5. **AI Interaction**: AI models can discover and interact with your marked routes

Kwargs to Opt Mechanism
-----------------------

Litestar automatically processes kwargs in route decorators and moves them into the route handler's ``opt`` dictionary:

.. code-block:: python

    # These are equivalent:
    @get("/users", mcp_tool="list_users")  # <- kwargs syntax (recommended)
    async def get_users() -> list[dict]: ...

    @get("/users", opt={"mcp_tool": "list_users"})  # <- opt dictionary syntax
    async def get_users() -> list[dict]: ...

The plugin discovers MCP-marked routes by scanning the ``opt`` dictionary of each route handler.

Available JSON-RPC Methods
--------------------------

Once configured, your application serves MCP at a single HTTP endpoint —
``POST /mcp`` (the path is configurable via ``MCPConfig.base_path``). All
interaction is a JSON-RPC 2.0 request with one of these ``method`` values:

- ``initialize`` - Handshake, returns server info and protocol capabilities
- ``ping`` - Liveness check
- ``tools/list`` - Enumerate tools registered from marked routes
- ``tools/call`` - Invoke a tool by ``name`` with its ``arguments``
- ``resources/list`` - Enumerate resources (always includes the built-in ``openapi`` resource)
- ``resources/read`` - Read a resource by ``uri`` (e.g. ``litestar://openapi`` or ``litestar://<marked_resource_name>``)

There are no REST paths like ``GET /mcp/tools`` or ``GET /mcp/resources/<name>``;
MCP is JSON-RPC, so the ``method`` field inside the request body dispatches
to the right handler.

What Makes This Different?
---------------------------

- **Route-Centric**: Mark individual routes for MCP exposure using simple kwargs
- **Minimal Setup**: Just add ``mcp_tool`` or ``mcp_resource`` kwargs to existing route handlers
- **Standards-Compliant**: Speaks MCP's native JSON-RPC 2.0 protocol; plugs straight into any MCP client
- **Litestar Native**: Built specifically for Litestar applications using the opt mechanism

Getting Started
---------------

Check out the :doc:`getting-started` guide to learn the basics, or explore our :doc:`examples` to see practical usage.

Community
---------

- **Discord**: `Join the Litestar Discord <https://discord.gg/litestar>`_
- **GitHub**: `litestar-org/litestar-mcp <https://github.com/litestar-org/litestar-mcp>`_

Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
