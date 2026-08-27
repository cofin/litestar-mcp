====================================
Agent-to-Agent (A2A) Protocol Support
====================================

Litestar MCP provides comprehensive, first-class support for the **Agent-to-Agent (A2A)** protocol.
While the Model Context Protocol (MCP) enables LLMs to call local tools and read resources, the A2A protocol connects autonomous AI agents to collaborate directly over JSON-RPC 2.0 and Server-Sent Events (SSE).

Overview & Architecture
=======================

The A2A integration in Litestar MCP provides:

- **Agent Cards** (``/.well-known/agent-card.json``): Automatic discovery manifests defining agent capabilities, provider metadata, and registered skills.
- **Task Lifecycle** (``tasks/send``, ``tasks/get``, ``tasks/cancel``): Asynchronous and synchronous task tracking with pluggable task persistence.
- **Real-Time Streaming** (``tasks/sendSubscribe``): Multi-client SSE streams delivering status updates and artifacts as skills execute.
- **Multi-Protocol Coexistence**: Seamlessly run both ``LitestarMCP`` and ``A2APlugin`` on the same Litestar instance, with optional automatic export of MCP tools as A2A skills.
- **Standalone Agent Runner**: A zero-boilerplate ``Agent`` class mirroring ``MCP`` for fast scripting and direct execution.

Standalone Agent
================

For standalone agent scripts and microservices, the ``Agent`` class provides an intuitive interface:

.. literalinclude:: /examples/snippets/a2a_agent_minimal.py
    :language: python
    :caption: ``docs/examples/snippets/a2a_agent_minimal.py``

Access the underlying Litestar application at any time via ``agent.app``.

Using A2APlugin in Litestar
===========================

For larger applications, mount ``A2APlugin`` directly on your Litestar app:

.. literalinclude:: /examples/snippets/a2a_plugin_example.py
    :language: python
    :caption: ``docs/examples/snippets/a2a_plugin_example.py``

Task Execution Context
======================

Skills can inject ``TaskContext`` to emit intermediate thoughts, report status transitions, or emit multi-part artifacts during execution:

.. literalinclude:: /examples/snippets/a2a_task_context.py
    :language: python
    :caption: ``docs/examples/snippets/a2a_task_context.py``

Multi-Protocol Coexistence
==========================

You can mount both ``LitestarMCP`` and ``A2APlugin`` on the same Litestar instance. Setting ``auto_export_mcp_tools=True`` in ``A2AConfig`` automatically surfaces all registered MCP tools in the agent card and allows A2A clients to invoke them directly:

.. literalinclude:: /examples/snippets/a2a_coexistence.py
    :language: python
    :caption: ``docs/examples/snippets/a2a_coexistence.py``

CLI Commands
============

Litestar MCP provides dedicated CLI commands for managing A2A agents:

.. code-block:: bash

    # List all discovered skills on the application
    litestar a2a list-skills

    # Inspect the dynamic agent card in human-readable or JSON format
    litestar a2a card
    litestar a2a card --json
