=================
Migrating to 0.14
=================

Version 0.14 removes the non-standard MCP agent card. Use ``server/discover``
for MCP capability discovery. Subscription streams are now bounded by
``MCPConfig.stream_queue_capacity``; a subscriber that stops reading receives a
completion response and is disconnected, so clients must reconnect with a new
``subscriptions/listen`` request. Applications needing A2A should install
``litestar-mcp[a2a]`` and configure ``LitestarA2A`` with the official SDK's
``AgentCard`` and ``RequestHandler``.

Modern MCP only
---------------

The supported MCP revision is ``2026-07-28``. Replace initialization and
session negotiation with ``server/discover`` and provide protocol version,
client capabilities and identity metadata on every request. The transport
is POST-only; GET and DELETE return 405. Legacy ``Mcp-Session-Id`` and
``Last-Event-ID`` headers are ignored, with no session minting or event replay.

Change ``mcp.run(transport="sse")`` to
``mcp.run(transport="streamable-http")`` or omit the selector for the new
default. ``transport="stdio"`` remains available. Replace
``litestar_mcp.mcp.service.RequestContext`` with ``MCPRequestContext`` and
``litestar_mcp.mcp.tasks.InMemoryTaskStore`` with ``MCPTaskStore``. These MCP
aliases are removed; similarly named classes in the official A2A SDK are
separate APIs and remain valid.

Every MCP request must carry a string or integer-valued finite numeric ID.
Missing, null, boolean, container and fractional IDs return HTTP 400 JSON
with ``INVALID_REQUEST`` (``-32600``) and a null error ID before tool
dependencies, execution or stream allocation. Zero, negative integers,
empty strings and integer-valued numbers such as ``1.0`` remain valid.
Resource-not-found errors use ``INVALID_PARAMS`` (``-32602``).

Task configuration, shared Stores, subscriptions/Channels, cache hints,
guards, ``route_opt``, stream limits and stdio application-session injection
remain supported. A shared task Store persists records but does not distribute
local runners or input/cancel queues. Applications own worker coordination
and recovery. ADK 2.9.0 with MCP SDK 1.30.0 still uses the old lifecycle and
is unsupported; see :doc:`adk` for the test boundary.

A2A 1.0 only
------------

Install ``litestar-mcp[a2a]`` for the transport-neutral SDK dependency.
The adapter requires ``A2A-Version: 1.0`` (valid ``1.0.x`` patch versions
are accepted), official 1.0 method names and protobuf JSON. Missing/legacy
version headers and 0.3 methods are rejected; there is no conversion fallback.
Advertise a valid absolute JSONRPC 1.0 interface matching the configured
mount in the AgentCard.

Replace a one-argument ``context_builder(request)`` callback with
``context_builder(request, context)``. The second argument is the prepared
SDK call context with the requested tenant and protocol metadata. Return
the authorized context directly or asynchronously; the adapter preserves
its tenant and state. Activate requested, advertised extensions before the
first result/event so response headers reflect actual activation.

JSON-RPC notifications with an omitted ID receive HTTP 204 without handler
execution; explicit null A2A IDs still receive responses. Long-running
executors own their service lifetime beyond the HTTP request. See :doc:`a2a`
for the authorization, SDK handler, callback and distributed-execution
boundaries, including the example-local SDK 1.1.2 live-task access correction.

Streaming and stdio cleanup
--------------------------

MCP request progress uses a bounded channel with backpressure and preserves
accepted progress events before the terminal response. Subscription streams
retain their separate slow-consumer completion/disconnect policy. Both
``MCPConfig.stream_cleanup_timeout`` and
``A2AConfig.stream_cleanup_timeout`` default to five seconds; timeout is
reported as incomplete cooperative cleanup.

In-process stdio uses native ``Litestar.lifespan()``. The public manual
``app_lifespan`` helper is removed. ``run_stdio_async(shutdown_timeout=5.0)``
bounds request cleanup and shutdown after native lifespan entry succeeds;
the original bridge/body error or cancellation is preserved if shutdown
then fails or times out. Startup unwind follows native Litestar exception
and cancellation semantics. Application startup, lifespan and shutdown
hooks must bound and shield their own cleanup where needed.

``MCPStdioContext.session`` remains a Litestar application session, separate
from removed MCP protocol sessions. Anonymous stdio has no owner ID; it does
not receive a shared ``"stdio"`` owner. Supply a verified identity or explicit
``owner_id`` and require authentication for protected tasks.

Authentication
--------------

``litestar_mcp.auth`` (``MCPAuthBackend``, ``MCPAuthConfig``,
``OIDCProviderConfig``, ``create_oidc_validator``, ``JWKSCache``,
``DefaultJWKSCache``, ``TokenValidator``), ``MCPConfig.auth``,
``MCPConfig.register_oauth_protected_resource``, and the
``/.well-known/oauth-protected-resource`` route are removed with no
compatibility aliases. Authenticate MCP with the app's own Litestar
middleware. With litestar-security, declare the mechanism with
``MCPConfig(route_opt={"auth": required("api-key")})`` and publish the RFC
9728 document with ``SecurityConfig.protected_resource``
(``ProtectedResourceConfig``) instead of ``MCPConfig.auth``. MCP clients
locate the authorization server through
``WWW-Authenticate: Bearer resource_metadata=...``; litestar-security emits
that header only when the mechanism declares
``security_scheme=SecurityScheme(type="http", scheme="bearer")`` and only
for evaluator (not guard) failures. The runtime dependency is now
``litestar`` (not ``litestar[jwt]``); add ``litestar[jwt]`` yourself if your
app uses Litestar's JWT backends.

Package layout
--------------

The package is grouped into ``litestar_mcp.core`` (protocol-agnostic
primitives), ``litestar_mcp.mcp`` (the MCP plugin, transports, and CLI),
``litestar_mcp.a2a`` (the optional A2A adapter), and ``litestar_mcp.utils``.
Root imports such as ``from litestar_mcp import LitestarMCP, MCPConfig``
are unchanged; ``litestar_mcp.A2AConfig`` and ``litestar_mcp.LitestarA2A``
resolve lazily and raise ``MissingDependencyError`` when the ``a2a`` extra
is not installed. Deep module paths moved without compatibility aliases:

- ``litestar_mcp.bridge`` -> ``litestar_mcp.mcp.bridge``
- ``litestar_mcp.plugin`` -> ``litestar_mcp.mcp.plugin``
- ``litestar_mcp.config`` -> ``litestar_mcp.mcp.config``
- ``litestar_mcp.routes`` -> ``litestar_mcp.mcp.routes``
- ``litestar_mcp.app`` -> ``litestar_mcp.mcp.app``
- ``litestar_mcp.executor`` -> ``litestar_mcp.mcp.executor``
- ``litestar_mcp.registry`` -> ``litestar_mcp.mcp.registry``
- ``litestar_mcp.content`` -> ``litestar_mcp.mcp.content``
- ``litestar_mcp.tasks`` -> ``litestar_mcp.mcp.tasks``
- ``litestar_mcp.error_mapping`` -> ``litestar_mcp.mcp.error_mapping``
- ``litestar_mcp.cli`` -> ``litestar_mcp.mcp.cli``
- ``litestar_mcp.services.handler`` -> ``litestar_mcp.mcp.service``
- ``litestar_mcp.jsonrpc`` -> ``litestar_mcp.core.jsonrpc``
- ``litestar_mcp.sse`` -> ``litestar_mcp.core.sse``
- ``litestar_mcp.schema_builder`` -> ``litestar_mcp.core.schema_builder``
- ``litestar_mcp.typing`` -> ``litestar_mcp.core.typing``
- ``litestar_mcp.exceptions`` -> ``litestar_mcp.core.exceptions``
- ``litestar_mcp.utils.serialization`` -> ``litestar_mcp.core.serialization``
