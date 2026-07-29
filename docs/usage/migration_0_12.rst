=========================
Migrating to MCP 0.12.0
=========================

Version 0.12.0 is a modern-only break to MCP ``2026-07-28``. There is no
compatibility mode for initialize-era clients.

Transport
=========

- Send one independent ``POST`` for every request.
- Remove ``initialize``, ``notifications/initialized``, ``ping``, session
  headers, GET/DELETE transport calls, and ``Last-Event-ID``.
- Put protocol version, client capabilities, and optional client identity in
  ``params._meta`` on every request.
- Send matching ``MCP-Protocol-Version`` and ``Mcp-Method`` headers. Send
  ``Mcp-Name`` for ``tools/call``, ``resources/read``, ``prompts/get``,
  ``tasks/get``, ``tasks/update``, and ``tasks/cancel``.
- Use the ``=?base64?<base64>?=`` sentinel for header values that cannot be
  represented safely as visible ASCII.
- Call ``server/discover`` for current capabilities.

Configuration
=============

Remove ``session_store``, ``session_max_idle_seconds``, ``sse_max_streams``,
and ``sse_max_idle_seconds``. The replacements are:

.. literalinclude:: ../examples/snippets/configuration_stateless.py
   :language: python
   :caption: Stateless transport defaults

``allowed_origins`` is now an exact additional allowlist. An absent Origin
remains valid; a present Origin must be same-origin or allowlisted.

Tasks extension
===============

Tasks are disabled by default and move to
``io.modelcontextprotocol/tasks``. Clients opt in on every request. Replace
``tasks/result`` and ``tasks/list`` with ``tasks/get``; respond to outstanding
input through ``tasks/update`` and request cancellation through
``tasks/cancel``.

``task_support`` remains a server policy and is no longer emitted by
``tools/list``. An optional task-capable tool falls back to a synchronous
result for clients without the extension. A required tool returns ``-32021``
when the client did not opt in.

MRTR and request state
======================

Tools, resources, and prompts can return
:class:`~litestar_mcp.MCPInputRequiredResult`. On retry, read
``get_mcp_request_context().input_responses`` and ``request_state``.

``requestState`` is attacker-controlled. Protect authorization-sensitive
state with AEAD or an HMAC, bind it to the authenticated principal, original
method/arguments, and an expiry, and reject verification failures. Never use
``clientInfo`` as an authorization identity.

Caching and subscriptions
=========================

Discovery, list, and resource-read results always include ``ttlMs`` and
``cacheScope``. Subscribe with ``subscriptions/listen`` and filters for list
changes, resource URIs, or task IDs. The first event acknowledges the accepted
filter. Every subsequent notification carries the subscription ID. Streams
have keepalives and cancellation but no replay.
