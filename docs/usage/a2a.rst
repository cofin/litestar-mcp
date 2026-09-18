===============
A2A Integration
===============

Install the optional official SDK dependency:

.. code-block:: bash

    pip install "litestar-mcp[a2a]"

Build an official :class:`a2a.types.AgentCard` whose A2A 1.0 JSON-RPC interface
points at the configured mount, then supply an official
:class:`a2a.server.request_handlers.RequestHandler`:

.. literalinclude:: /examples/snippets/a2a_plugin.py
    :language: python
    :caption: Official A2A SDK adapter

The default RPC endpoint is ``/a2a`` and the card is served from
``/.well-known/agent-card.json`` with ``ETag`` and ``Cache-Control`` headers
(``A2AConfig.agent_card_max_age``). Both routes are exempt from CSRF and hidden
from the OpenAPI schema unless ``A2AConfig.include_in_schema`` is set. Requests
must send ``A2A-Version: 1.0``; valid ``1.0.x`` patch versions are also accepted.
Missing, malformed, 0.3, and unsupported major/minor versions are rejected.
Only the 1.0 method names and protobuf JSON shapes are accepted. The card
must advertise an absolute JSONRPC interface URL for protocol ``1.0`` whose
path matches ``A2AConfig.path``. Cards should describe the actual supported
input/output modes, :class:`a2a.types.AgentSkill` entries and capabilities.

.. note::

    A card's ``AgentSkill`` entries describe this agent's A2A task-handling
    capabilities and are unrelated to MCP skills — the filesystem-backed
    ``SKILL.md`` bundles served through ``skills/list`` / ``skills/get`` /
    ``resources/read`` documented in :doc:`skills`. A2A and the MCP skills
    extension are separate, independently configured features.

The extra installs the transport-neutral SDK without requiring Starlette,
FastAPI or Uvicorn. Choose an ASGI server separately for deployment. MCP
routes and the A2A agent card's skills are registered independently; share
business services between executors and MCP tools explicitly.

Support and ownership
=====================

.. list-table::
    :widths: 25 35 40
    :header-rows: 1

    * - Layer
      - Provided here
      - Application responsibility
    * - Litestar transport
      - ASGI routes, JSON, SSE, middleware, guards, lifecycle and disconnect cleanup
      - Configure authentication, route policy and the deployment server
    * - Official SDK
      - Protocol models, errors, RequestHandler and AgentExecutor contracts;
        DefaultRequestHandler task execution
      - Supply an executor, supported capabilities and appropriately scoped stores
    * - Durable execution
      - Adapter forwards task and subscription operations to the handler
      - Durable records, worker coordination, recovery and long-running service scopes
    * - Push notifications
      - Create/get/list/delete configuration methods reach the handler
      - Enable SDK stores/sender; enforce ownership, callback URL policy, credentials,
        retries and delivery guarantees

The JSON-RPC binding covers ``SendMessage``, ``SendStreamingMessage``,
``GetTask``, ``ListTasks``, ``CancelTask``, ``SubscribeToTask``,
``CreateTaskPushNotificationConfig``, ``GetTaskPushNotificationConfig``,
``ListTaskPushNotificationConfigs``, ``DeleteTaskPushNotificationConfig``, and
``GetExtendedAgentCard``. Availability depends on the configured handler and
advertised capabilities. There is no gRPC or HTTP/REST binding and no new
distributed backend. SDK client/executor integration tests cover task
completion, continuation after input-required, artifacts, cancellation,
subscription, pagination, push configuration and extended cards. These are
repository integration tests, not an external A2A conformance certification.

Authentication and context
==========================

Authenticate the RPC route with Litestar middleware and authorize access with
``A2AConfig.guards`` or ``A2AConfig.route_opt``. The public card route declares
``exclude_from_auth``; authentication middleware must honor that opt when
public discovery is wanted.
Card metadata does not enforce authorization.

``context_builder(request, context)`` accepts a Litestar request and a prepared
SDK ``ServerCallContext``. It may return the context synchronously or await
authorization before returning it. The prepared context contains the user,
requested tenant and extensions, plus ``state["auth"]``, ``state["headers"]``,
``state["litestar_state"]``, ``state["method"]`` and ``state["request_id"]``.
The returned context is authoritative: the adapter does not overwrite its
authorized tenant or state with untrusted request parameters.

The prepared user's ``user_name`` is taken from the first of ``id``, ``sub``,
``username`` or ``display_name`` found on the Litestar principal. A principal
that exposes none of them maps to the empty owner key the SDK uses for
anonymous callers, so such applications must supply a ``context_builder``
that sets the user themselves; otherwise the SDK's owner-scoped stores treat
every such caller as one owner.

Resolve tenant membership from authenticated application identity. A tenant
parameter, task ID or context ID alone grants no access. Enforce the same
principal/tenant boundary on task get/list/continue/cancel/subscribe and push
configuration operations. A scoped store alone is insufficient with SDK
``DefaultRequestHandler``: its live execution registry can bypass store lookups
for cancellation and subscription. The authenticated example in
``docs/examples/a2a_application/main.py`` uses a narrow handler authorization
boundary around these operations and scopes request services inside the
executor. That example's identity and callback sender are demonstrations;
production applications supply real identity validation and delivery policy.

Resource lifetime and streaming
===============================

Litestar HTTP dependency providers may close when the route handler returns,
before its SSE response finishes. Executors must open their own
application/Dishka resource scope using the verified call context, including
work that continues after ``returnImmediately`` or a client disconnect.
Do not pass a request-scoped session into work that outlives that request.
The plugin calls the handler's ``aclose()`` hook, when available, at app shutdown.

SSE production and iterator closure run in the same producer task. A bounded
channel provides backpressure; ``A2AConfig.stream_cleanup_timeout`` (default
``5.0`` seconds) bounds cooperative cleanup after the response ends. Expiry is
logged as incomplete cleanup. Application finalizers must tolerate cancellation;
the adapter cannot force arbitrary application work to finish.

Activate extensions by putting a set of requested, advertised URI strings in
``context.state["a2a_activated_extensions"]`` before returning a result or
yielding the first stream event. Only those activated extensions appear in
``A2A-Extensions`` response headers. Required advertised extensions must be
requested. Later activation cannot change already-sent response headers.

This request-only adapter declines well-formed JSON-RPC notifications (an
omitted ``id``) with HTTP 204 without executing them. An explicit null ID is
a correlated A2A request and receives a response. Malformed envelopes return
JSON-RPC errors. MCP has its own stricter request-ID contract; see
:doc:`migration_0_14`.
