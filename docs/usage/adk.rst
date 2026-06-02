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

Because MCP is stateful and maintains active HTTP sessions, you must clean up connections when shutting down:

.. literalinclude:: /examples/snippets/adk_snippets.py
    :language: python
    :caption: ``docs/examples/snippets/adk_snippets.py``
    :pyobject: run_and_cleanup

Compatibility Matrix
====================

The following matrix distinguishes features tested with Google ADK 1.x from native MCP protocol features:

.. list-table::
    :widths: 30 20 50
    :header-rows: 1

    * - Feature
      - Supported in ADK
      - Verification Path / Note
    * - Tool Discovery
      - Yes
      - Verified via ``McpToolset.get_tools()``
    * - Tool Execution
      - Yes
      - Verified via calling tool wrapper ``run_async()``
    * - Auth Propagation
      - Yes
      - Verified via ``Authorization`` bearer header in connection parameters
    * - Resource Listing
      - Yes
      - Verified via ``McpToolset.list_resources()``
    * - Resource Reading
      - Yes
      - Verified via ``McpToolset.read_resource()`` (by resource name)
    * - Resource Templates
      - No (Direct MCP)
      - Covered by direct MCP tests (``tests/integration/test_resources_templates.py``)
    * - Completion
      - No (Direct MCP)
      - Covered by direct MCP tests (``tests/integration/test_resources_templates.py``)
    * - SSE Replay Streams
      - No (Direct MCP)
      - Covered by direct MCP tests (``tests/integration/test_streamable_http_session.py``)
    * - Task Augmentation
      - No (Direct MCP)
      - Covered by direct MCP tests (``tests/unit/test_tasks.py``)

MCP vs A2A Protocol Boundary
============================

While the plugin publishes an agent metadata card (at ``/.well-known/agent-card.json``) used by MCP discovery clients, **it does not advertise or support the A2A protocol from this endpoint**.

Full Agent-to-Agent (A2A) protocol compatibility requires:
- A separate A2A routing tree.
- A dedicated A2A agent card endpoint.
- Skill execution pipelines aligned with the A2A spec.

Treating A2A as distinct from MCP prevents client-side handshake confusion.

Production Persistence Hardening
================================

For high-availability or multi-replica production deployments of ADK and Litestar MCP:
- State such as task lists, SSE replay events, and active sessions should be backed by a persistent storage tier.
- This plugin supports a persistent storage layer to manage these needs across server instances. Refer to `docs/usage/persistence.rst` for detail on configuring persistence once available.
