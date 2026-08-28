===============
ADK Integration
===============

Litestar MCP can be consumed by Google's Agent Development Kit (ADK) as a remote Streamable HTTP MCP server. The integration allows your ADK-based agent applications to discover and invoke tools, as well as read resources exposed by your Litestar application.

.. note::
    Google ADK is an optional client integration. The ``google-adk`` package is not installed as a runtime dependency of ``litestar-mcp``.

Installation
============

For ADK application users, install ``google-adk`` in your client environment:

.. code-block:: bash

    pip install google-adk

For contributors running the compatibility test harness:

.. code-block:: bash

    uv sync --group test --group adk
    uv run pytest -m adk tests/integration/test_google_adk_mcp_toolset.py

Connecting from an ADK Agent
============================

Google ADK connects to remote MCP servers using `McpToolset` combined with `StreamableHTTPConnectionParams`.

Remote Connection Snippet
-------------------------

Here is how to set up the toolset connection in your ADK agent application:

.. literalinclude:: /examples/snippets/adk_snippets.py
    :language: python
    :caption: ``docs/examples/snippets/adk_snippets.py``
    :pyobject: connect_simple

Authentication Headers
----------------------

If your Litestar MCP server uses bearer authentication (see :doc:`auth`), pass the authorization headers in `StreamableHTTPConnectionParams`:

.. literalinclude:: /examples/snippets/adk_snippets.py
    :language: python
    :caption: ``docs/examples/snippets/adk_snippets.py``
    :pyobject: connect_with_auth

Cleanup
-------

MCP requests are stateless, but ADK's HTTP client still owns network
connections. Close the toolset during application shutdown:

.. literalinclude:: /examples/snippets/adk_snippets.py
    :language: python
    :caption: ``docs/examples/snippets/adk_snippets.py``
    :pyobject: run_and_cleanup

Compatibility Matrix
====================

Google ADK 2.3 still implements the initialize-era lifecycle and therefore
cannot connect to the modern-only ``2026-07-28`` endpoint. The table records
the compatibility boundary until ADK adds the stateless lifecycle:

.. list-table::
    :widths: 30 20 50
    :header-rows: 1

    * - Feature
      - Supported in ADK
      - Verification Path / Note
    * - Tool Discovery
      - No
      - ADK sends the removed ``initialize`` request
    * - Tool Execution
      - No
      - Blocked by the lifecycle mismatch
    * - Auth Propagation
      - No
      - Header propagation works, but initialization is rejected
    * - Resource Listing
      - No
      - Blocked by the lifecycle mismatch
    * - Resource Reading
      - No
      - Blocked by the lifecycle mismatch
    * - Resource Templates
      - No (Direct MCP)
      - Covered by direct MCP tests (``tests/integration/test_resources_templates.py``)
    * - Completion
      - No (Direct MCP)
      - Covered by direct MCP tests (``tests/integration/test_resources_templates.py``)
    * - Subscriptions
      - No (Direct MCP)
      - Covered by direct MCP tests (``tests/unit/test_subscriptions.py``)
    * - Tasks Extension
      - No (Direct MCP)
      - Covered by direct MCP tests (``tests/unit/test_tasks.py``)

MCP vs A2A Protocol Boundary
============================

MCP clients call ``server/discover``. A2A is an independent optional
integration backed by the official SDK; see :doc:`a2a` for configuring an
``AgentCard`` and ``RequestHandler`` for ADK ``RemoteA2aAgent`` clients.

Production Persistence Hardening
================================

For high-availability or multi-replica production deployments of ADK and Litestar MCP:

- MCP request processing itself needs no sticky routing.
- Configure Tasks with a shared Litestar Store when task handles must survive
  process restarts or move between replicas.
- Configure ``subscription_channels`` with a shared Channels backend when
  notifications must fan out across workers. Subscription streams have no
  replay.
