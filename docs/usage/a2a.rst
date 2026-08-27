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

.. code-block:: python

    from litestar_mcp import Agent

    agent = Agent(name="ResearchAgent", description="Conducts online research")

    @agent.skill(name="summarize", description="Summarize a block of text")
    def summarize(text: str) -> str:
        """Summarize text content."""
        return f"Summary: {text[:100]}..."

    if __name__ == "__main__":
        agent.run(host="127.0.0.1", port=8000)

Access the underlying Litestar application at any time via ``agent.app``.

Using A2APlugin in Litestar
===========================

For larger applications, mount ``A2APlugin`` directly on your Litestar app:

.. code-block:: python

    from litestar import Litestar, get
    from litestar_mcp import A2AConfig, A2APlugin

    @get("/skills/calc", opt={"a2a_skill": "calc", "a2a_description": "Perform calculation"})
    def calc(a: int, b: int) -> dict[str, int]:
        return {"result": a + b}

    config = A2AConfig(
        name="ComputeAgent",
        version="1.0.0",
        base_path="/a2a",
    )

    app = Litestar(
        route_handlers=[calc],
        plugins=[A2APlugin(config=config)],
    )

Task Execution Context
======================

Skills can inject ``TaskContext`` to emit intermediate thoughts, report status transitions, or emit multi-part artifacts during execution:

.. code-block:: python

    from litestar import post
    from litestar_mcp import TaskContext, Artifact, TextPart

    @post("/skills/analyze", opt={"a2a_skill": "analyze"})
    async def analyze_task(query: str, ctx: TaskContext) -> dict[str, str]:
        await ctx.thought("Parsing query arguments...")
        await ctx.report_status("working", message="Analysis in progress")
        await ctx.emit_artifact(
            Artifact(name="report.txt", parts=[TextPart(text="Initial findings")])
        )
        return {"status": "complete"}

Multi-Protocol Coexistence
==========================

You can mount both ``LitestarMCP`` and ``A2APlugin`` on the same Litestar instance. Setting ``auto_export_mcp_tools=True`` in ``A2AConfig`` automatically surfaces all registered MCP tools in the agent card and allows A2A clients to invoke them directly:

.. code-block:: python

    from litestar import Litestar, get
    from litestar_mcp import A2AConfig, A2APlugin, LitestarMCP, MCPConfig

    @get("/tools/multiply", mcp_tool="multiply", mcp_description="Multiply numbers")
    def multiply(a: int, b: int) -> int:
        return a * b

    mcp = LitestarMCP(config=MCPConfig(base_path="/mcp"))
    a2a = A2APlugin(config=A2AConfig(base_path="/a2a", auto_export_mcp_tools=True))

    app = Litestar(route_handlers=[multiply], plugins=[mcp, a2a])

CLI Commands
============

Litestar MCP provides dedicated CLI commands for managing A2A agents:

.. code-block:: bash

    # List all discovered skills on the application
    litestar a2a list-skills

    # Inspect the dynamic agent card in human-readable or JSON format
    litestar a2a card
    litestar a2a card --json
