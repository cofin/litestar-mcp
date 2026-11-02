==========
Deployment
==========

MCP 2026-07-28 requests are stateless and can be routed to any healthy
replica. There is no ``Mcp-Session-Id``, sticky routing, GET event stream, or
replay cursor.

Application state
=================

State that must outlive one request needs an explicit handle:

- use application-defined IDs in tool arguments for domain workflows;
- use ``requestState`` for an MRTR retry, after authenticating and
  integrity-protecting it;
- use Tasks extension IDs for durable long-running operations;
- use subscription request IDs only to correlate live notification streams.

The default Tasks Store is process-local and intended for development. Pass a
persistent Litestar :class:`~litestar.stores.base.Store` through
:class:`~litestar_mcp.MCPTaskConfig` for multi-replica task retrieval.

Subscriptions
=============

Each ``subscriptions/listen`` request owns its POST-response SSE stream. The
default notification bus is process-local. For cross-worker fan-out, pass an
already-configured Litestar ``ChannelsPlugin`` as
``MCPConfig(subscription_channels=channels)``. Graceful shutdown closes active
streams; clients open a new subscription because replay and
``Last-Event-ID`` are not supported.

Security
========

A missing ``Origin`` header is accepted. A present Origin must match the
request's own origin or an exact value in ``allowed_origins``. Terminate TLS
at a trusted proxy, preserve the public scheme and host, and apply ordinary
Litestar authentication and guards to the MCP router.

Multi-replica deployments
=========================

Deploying Litestar MCP horizontally across multiple replicas requires shared
infrastructure for persistent state, notifications, and queue execution:

- **Shared State Store**: Use a shared Litestar :class:`~litestar.stores.redis.RedisStore`
  for both the MCP Tasks store (:class:`~litestar_mcp.MCPTaskConfig`) and the durable
  workflow store (:class:`~litestar_mcp.queues.WorkflowStore`). This ensures tasks
  and workflow runs created on one replica can be polled, advanced, or inspected
  from any replica.
- **Cross-Worker Notification Channels**: Configure ``MCPConfig(subscription_channels=channels)``
  backed by Redis channels via Litestar's ``ChannelsPlugin``. Task status updates,
  cancellation signals, and subscriptions are broadcast across all workers.
- **Durable Queue Backend**: Configure ``litestar-queues`` with a durable persistent
  backend (such as Redis or SQLSpec / PostgreSQL) rather than memory.
- **Worker Placement**: In development, ``WorkerConfig(placement="server")`` runs
  in-process background workers alongside the web server. In production, configure
  ``WorkerConfig(placement="external")`` and run separate worker processes executing
  the same Litestar application via ``litestar workers run``.
- **Conformance Testing**: Contributors can run ``make conformance-queue`` (which sets
  ``MCP_CONFORMANCE_TASK_EXECUTOR=queue``) to run the official MCP conformance suite
  against queue-backed task execution.

.. seealso::

    - :doc:`configuration`
    - :doc:`migration_0_12`
    - :doc:`security`
