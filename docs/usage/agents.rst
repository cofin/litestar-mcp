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

1. **Controllers (:class:`~litestar_mcp.controllers.PromptController`, :class:`~litestar_mcp.controllers.SkillController`)**:
   Class-based boundaries mirroring Litestar's controller architecture to package
   prompts, tools, instructions, and authorization guards.
2. **Agent Hierarchy (:class:`~litestar_mcp.agent.Agent`, :class:`~litestar_mcp.agent.AgentGroup`)**:
   Coordinator and specialist agent trees featuring automated ``transfer_to_agent``
   synthesis and runtime model binding.
3. **Model & Runtime Layer (:class:`~litestar_mcp.agent.GoogleGenAIClient`, :class:`~litestar_mcp.agent.AgentRuntime`)**:
   Streaming model execution, guarded multi-turn loops, OpenTelemetry span tracking,
   and Server-Sent Events (SSE) streaming.

Prompt Controllers
==================

:class:`~litestar_mcp.controllers.PromptController` allows declaring class-based
MCP prompt groups with class-level configurable attributes:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py`` - ``CoffeePromptController``
    :pyobject: CoffeePromptController

Class-level ``instructions`` are automatically prepended as system messages when
prompt handlers return raw strings or lists of user messages.

Skill Controllers
=================

A :class:`~litestar_mcp.controllers.SkillController` encapsulates domain tools,
prompts, instructions, and guards into a reusable unit:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py`` - ``BaristaSkillController``
    :pyobject: BaristaSkillController

Skill controllers can be mounted directly on :class:`~litestar_mcp.LitestarMCP`,
attached to an :class:`~litestar_mcp.agent.Agent`, or exported to A2A ``AgentSkill`` cards.

Agents and Agent Groups
=======================

An :class:`~litestar_mcp.agent.Agent` bundles instructions, tools, skills, and model configuration.
An :class:`~litestar_mcp.agent.AgentGroup` coordinates multi-agent interactions, automatically
synthesizing a ``transfer_to_agent`` tool for the coordinator to route turns to specialists:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py`` - ``setup_coffee_agent_group``
    :pyobject: setup_coffee_agent_group

Google GenAI Client
===================

:class:`~litestar_mcp.agent.GoogleGenAIClient` provides first-party integration with
``google-genai``, supporting Gemini 2.5 Flash, Gemini 2.5 Pro, thinking budgets,
and streaming function calls:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py`` - ``create_model_client``
    :pyobject: create_model_client

Realtime SSE Streaming
======================

Mounting :class:`~litestar_mcp.agent.AgentChatController` provides HTTP endpoints
and live Server-Sent Events (SSE) streaming with ``Last-Event-ID`` cursor recovery:

- ``POST /agent/turns``: Synchronous turn execution returning full aggregated output.
- ``GET /agent/turns/{turn_id}/stream``: Realtime SSE event stream emitting typed frames
  (``thought``, ``delta``, ``tool_call``, ``tool_result``, ``complete``).

Runnable Coffee Shop Example
============================

The repository provides a complete, runnable example in
``docs/examples/agent/coffee_shop_agent.py`` demonstrating a neighborhood barista
agent group with custom tools, prompts, and streaming chat:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py``
