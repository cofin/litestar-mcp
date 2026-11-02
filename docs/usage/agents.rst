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
included inside nested :class:`~litestar.Router` or Dishka ``DishkaRouter``
trees, attached to an :class:`~litestar_mcp.agent.Agent`, or bridged to A2A
``AgentSkill`` cards.

Universal Dependency Injection
------------------------------

Tools, :class:`~litestar_mcp.SkillController` methods, and
:class:`~litestar_mcp.PromptController` methods support four dependency
injection mechanisms across HTTP routes, MCP ``tools/call`` / ``prompts/get``,
and standalone :class:`~litestar_mcp.agent.AgentRuntime` turns:

1. **Litestar native DI** — ``dependencies={"svc": Provide(...)}`` on the
   controller, router, or app (including ``Dependency()`` parameter markers).
2. **Dishka** — ``FromDishka[Service]`` annotations on ``@tool`` and
   ``@prompt`` methods. Controller and router route handlers are automatically
   wrapped with ``dishka.integrations.litestar.inject`` when Dishka parameters
   are present, and standalone ``Tool.invoke`` resolves ``FromDishka[T]`` from
   the request or app container.
3. **Third-party DI markers** — ``Annotated[Service, Inject()]`` (Wireup,
   svcs, punq, Lagom, Dependency Injector) or ``service: Service = Depends(fn)``
   markers are excluded from the public JSON schema and resolved at invocation
   time against the active container or callable provider.
4. **Custom resolver hook** — pass ``container=...`` or
   ``dependency_resolver=...`` on :class:`~litestar_mcp.core.context.ToolContext`
   to plug any custom container into standalone tool execution.

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

Agents without an explicit ``model`` use the runtime's ``default_model``; when that is
unset too, the runtime builds a default :class:`~litestar_mcp.agent.GoogleGenAIClient`.
Register the :class:`~litestar_mcp.agent.AgentRuntime` in ``Litestar(plugins=[...])`` to
provide it as the ``runtime`` dependency of :class:`~litestar_mcp.agent.AgentChatController`.

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

Durable Workflows
=================

For multi-step, stateful agent pipelines that survive worker crashes, node retries,
and distributed scaling, Litestar MCP provides durable workflow orchestration
on top of ``litestar-queues``. Install the optional ``queues`` extra to use it:

.. code-block:: bash

    pip install "litestar-mcp[queues]"

.. literalinclude:: /examples/snippets/durable_workflow.py
    :language: python
    :caption: ``docs/examples/snippets/durable_workflow.py``

Register :class:`~litestar_mcp.queues.DurableWorkflowEngine` next to ``QueuePlugin``;
no other wiring is required. During app init the engine:

- validates every :class:`~litestar_mcp.agent.workflow.DynamicWorkflow` DAG and rejects
  duplicate workflow names,
- attaches its task dependencies to the ``QueuePlugin`` configuration (any
  ``task_dependency_resolver`` you set keeps running first),
- stores run and node records (:class:`~litestar_mcp.queues.WorkflowRun`,
  :class:`~litestar_mcp.queues.NodeRecord`) in the application's ``stores`` registry under
  ``litestar_mcp_workflows``, so configuring ``Litestar(stores=...)`` once (for example a
  Redis-backed ``StoreRegistry``) makes workflow state shared across replicas, and
- provides itself to route handlers as the ``workflow_engine`` dependency.

Start runs with ``await workflow_engine.start("workflow_name", initial_state={})`` and inspect
them with ``await workflow_engine.snapshot(run_id)``, which returns ``(WorkflowRun, WorkflowContext)``.

Execution Semantics & Guarantees:

- **Idempotent Scheduling**: Workflow nodes are registered with ``unique_until="forever"``
  and enqueued with keys matching ``wf-node:{run_id}:{node}``. Redelivered tasks or concurrent
  advances never double-schedule an in-flight or completed step. Node reservations are automatically
  released upon reaching a terminal status (``completed`` or ``failed``).
- **At-least-once Node Execution**: If a worker crashes while executing a step before recording its
  outcome to the store, ``litestar-queues``' stale requeue recovers the job and re-runs the node.
- **JSON-Serializable Outputs**: Step results must be JSON-serializable. Returning non-serializable
  data fails the node and records a descriptive error.
- **Fail-Fast Cascading**: When a node fails or exceeds retries, its dependent downstream nodes
  are skipped, and the overall run transitions to ``status="failed"``.
- **Per-Run Watchdog & Timeouts**: If a worker crashes between persisting a node record and
  enqueuing the subsequent advance task, the watchdog task (scheduled with ``run_after=watchdog_interval``
  and per-tick key ``wf-watchdog:{run_id}:{tick}``) detects the stalled run and triggers advance.
  Runs exceeding ``run_timeout_seconds`` are transitioned to ``failed`` with a timeout error and
  all reservations released.
- **Non-Immediate Backend Requirement**: Because ``litestar-queues``' ``immediate`` execution backend
  runs enqueued tasks synchronously inline, enabling the watchdog on an immediate backend is rejected
  with an :class:`~litestar.exceptions.ImproperlyConfiguredException` to prevent infinite recursion.
  Use ``local``, ``redis``, or ``sqlspec`` execution backends when watchdog is enabled.

Runnable Coffee Shop Example
============================

The repository provides a complete, runnable example in
``docs/examples/agent/coffee_shop_agent.py`` demonstrating a neighborhood barista
agent group with custom tools, prompts, and streaming chat:

.. literalinclude:: /examples/agent/coffee_shop_agent.py
    :language: python
    :caption: ``docs/examples/agent/coffee_shop_agent.py``
