===============
ADK Integration
===============

Google ADK 2.8.0 with MCP Python SDK 1.29.1 cannot consume this package's
modern-only MCP ``2026-07-28`` endpoint. That client still sends
``initialize`` for MCP ``2025-11-25``. There is no compatibility handshake
or session mode to enable on this server.

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

ADK constructs remote MCP clients with ``McpToolset`` and
``StreamableHTTPConnectionParams``. The following examples show client
construction only. They do not establish a connection or demonstrate
compatibility with this server; use them once ADK supports the modern protocol.

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

The reviewed environment contains Google ADK 2.8.0, MCP SDK 1.29.1 and
A2A SDK 1.1.2. Four ADK MCP interoperability tests are explicitly skipped
because of the lifecycle mismatch; the passing server-start harness is not
an interoperability result. Reassess this boundary with real client calls
when upgrading ADK.

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
      - Bearer headers alone cannot resolve the lifecycle mismatch
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

Modern MCP clients call ``server/discover``. Running an ADK agent locally
behind an application service is independent of remote MCP interoperability;
the application owns its runner, model credentials and session lifecycle.
A2A is a separate optional integration backed by the official SDK; see
:doc:`a2a` for configuring an ``AgentCard`` and ``RequestHandler``. The real
official A2A SDK client is integration-tested. ADK ``RemoteA2aAgent`` support
has not been established by a supported-client test and is not promised here.

Production Persistence Hardening
================================

For high-availability or multi-replica production deployments of ADK and Litestar MCP:

- MCP request processing itself needs no sticky routing.
- A shared Litestar Store can persist MCP task records. It does not distribute
  the process-local task runner, input queue or cancellation queue, or resume
  executions after restart. Applications must arrange worker ownership,
  routing and recovery before using tasks across replicas.
- Configure ``subscription_channels`` with a shared Channels backend when
  notifications must fan out across workers. Subscription streams have no
  replay.
