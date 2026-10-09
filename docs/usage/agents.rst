======
Agents
======

Litestar MCP provides high-level agent primitives designed to eliminate repetitive
boilerplate when building multi-agent systems, structured prompts, and domain skills.
These abstractions integrate directly with Litestar's dependency injection, guards,
route handlers, and streaming responses.

Architecture Overview
=====================

The agent framework centers around three core layers:

1. **Controllers (:class:`~litestar_mcp.PromptController`, :class:`~litestar_mcp.SkillController`)**:
   Class-based boundaries mirroring Litestar's controller architecture to package
   prompts, tools, instructions, and authorization guards. Controller guards and DI
   apply directly to internal MCP route handlers.
2. **Agent Hierarchy (:class:`~litestar_mcp.agent.Agent`, :class:`~litestar_mcp.agent.AgentGroup`)**:
   Coordinator and specialist agent trees featuring automated, Literal-typed
   ``transfer_to_agent`` tools and non-mutating specialist isolation.
3. **Model & Runtime Layer (:class:`~litestar_mcp.agent.GoogleGenAIClient`, :class:`~litestar_mcp.agent.AgentRuntime`)**:
   Streaming model execution using ``gemini-3.8-flash``, thought signature preservation,
   turn deadlines, owner-scoped session storage, and typed Server-Sent Events (SSE).

Prompt Controllers
==================

:class:`~litestar_mcp.PromptController` allows declaring class-based
MCP prompt groups with class-level configurable attributes:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py`` - ``CoffeePromptController``
    :pyobject: CoffeePromptController

Because the MCP specification limits prompt messages to the ``user`` and ``assistant``
roles, class-level ``instructions`` are prepended to the first user message rather
than emitted as a forbidden ``system`` role. Controller-level ``guards`` and
dependency injection providers are preserved across both ``prompts/list`` and
``prompts/get``.

Skill Controllers
=================

A :class:`~litestar_mcp.SkillController` encapsulates domain tools,
prompts, instructions, and guards into a reusable unit:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py`` - ``BaristaSkillController``
    :pyobject: BaristaSkillController

Skill controllers compile each method into an internal Litestar route handler,
retaining method guards, type-based context injection, and dependency parameters.
Skill controllers can be mounted on :class:`~litestar_mcp.LitestarMCP`,
attached to an :class:`~litestar_mcp.agent.Agent`, or bridged to A2A ``AgentSkill`` cards.

Agents and Agent Groups
=======================

An :class:`~litestar_mcp.agent.Agent` bundles instructions, tools, skills, and model configuration.
An :class:`~litestar_mcp.agent.AgentGroup` coordinates multi-agent interactions, automatically
synthesizing a non-mutating ``transfer_to_agent`` tool for the coordinator to route turns to specialists:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py`` - ``setup_coffee_agent_group``
    :pyobject: setup_coffee_agent_group

Google GenAI Client
===================

:class:`~litestar_mcp.agent.GoogleGenAIClient` provides first-party integration with
``google-genai``, defaulting to ``gemini-3.8-flash`` and supporting Vertex AI
endpoints, shared ``httpx2.AsyncClient`` transports, thinking budgets, and
automatic thought signature round-tripping on follow-up function-calling turns:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py`` - ``create_model_client``
    :pyobject: create_model_client

Sessions and Runtime
====================

:class:`~litestar_mcp.agent.AgentRuntime` manages turn execution loops, tool invocations,
and turn deadlines. History persistence is managed by pluggable :class:`~litestar_mcp.agent.sessions.SessionStore`
implementations:

- :class:`~litestar_mcp.agent.sessions.MemorySessionStore`: In-process LRU cache with TTL eviction and per-session concurrency locks.
- :class:`~litestar_mcp.agent.sessions.LitestarStoreSessionStore`: Distributed persistence backed by any Litestar :class:`Store` (Redis, SQLite, file).

Sessions are strictly owner-scoped to prevent IDOR vulnerabilities. Anonymous sessions
require server-generated IDs.

Realtime SSE Streaming
======================

Mounting :class:`~litestar_mcp.agent.AgentChatController` provides HTTP endpoints
and live Server-Sent Events (SSE) streaming with ``Last-Event-ID`` cursor recovery:

- ``POST /agent/turns``: Synchronous turn execution returning full aggregated output.
- ``GET /agent/turns/{turn_id}/stream``: Realtime SSE event stream emitting typed frames
  (``thought``, ``delta``, ``tool_call``, ``tool_result``, ``complete``) with keepalive pings.

Runnable Coffee Shop Example
============================

The repository provides a complete, runnable example in
``docs/examples/agent/coffee_shop_agent.py`` demonstrating a neighborhood barista
agent group with custom tools, prompts, and streaming chat:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py``
