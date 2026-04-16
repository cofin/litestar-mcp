==========
Deployment
==========

This page collects operational guidance for running a Litestar-MCP app
in production — most notably the session-affinity story that any
horizontally-scaled deployment needs to get right.

Sticky routing on ``Mcp-Session-Id``
====================================

Each MCP session is identified by the ``Mcp-Session-Id`` header that
the plugin returns from ``initialize`` (see the streamable-HTTP
transport contract). Server-Sent-Event streams pin to the replica that
opened them because their event queues live in-process; any cross-replica
fanout requires a shared broker, which this plugin intentionally does
not assume.

For Cloud Run, GKE, or any horizontally-scaled deployment:

1. **Session affinity on the** ``Mcp-Session-Id`` **header.** Configure
   the load balancer to hash or pin on this header rather than on a
   cookie — MCP clients do not use cookies, so cookie affinity silently
   fails open.
2. **Shared session store.** Configure
   :class:`~litestar_mcp.MCPConfig` with a shared ``session_store``
   (Redis, or the SQL-backed store from the ``advanced_alchemy`` /
   ``sqlspec`` reference families) so session metadata survives replica
   restarts and any replica can resolve a session id for stateless POST
   tool calls.
3. **Sticky only where it matters.** The GET SSE stream and any POST
   that expects a server-streamed response must land on the replica
   that owns the session. Pure POST → POST tool flows can land on any
   replica that reads the shared store.

Cloud Run
---------

Cloud Run supports session affinity via the load-balancer configuration
on the backing Serverless NEG. Enable it at the backend service and
select the ``Mcp-Session-Id`` header as the affinity key. Without
affinity, SSE streams terminate prematurely whenever the load balancer
round-robins a resumption request to a different revision instance.

Kubernetes / GKE
----------------

For a ``Service`` in front of a ``Deployment``, ``sessionAffinity:
ClientIP`` is a coarse fallback but is wrong for shared-NAT clients. A
service mesh (Istio, Linkerd) or an ingress controller that supports
header-based consistent hashing is the correct tool — point it at
``Mcp-Session-Id``.

When a session store is still required
--------------------------------------

Even with perfect sticky routing, a shared session store is still
needed whenever:

- a replica restarts mid-session (rolling deploy, OOM, SIGTERM);
- a client reconnects from a different IP (mobile roaming,
  load-balancer source-IP rotation);
- a stateless POST tool call is intentionally routed to any replica.

The reference families in ``docs/examples/notes/`` demonstrate both the
in-memory default (fine for demos and single-replica deployments) and
the upgrade path to a shared store.

.. seealso::

    - :doc:`configuration` — :class:`~litestar_mcp.MCPConfig` reference.
    - :doc:`uvx_guide` — single-file run reference for the shipped
      examples.
