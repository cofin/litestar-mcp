=================
Migrating to 0.14
=================

Version 0.14 removes the non-standard MCP agent card. Use ``server/discover``
for MCP capability discovery. Subscription streams are now bounded by
``MCPConfig.stream_queue_capacity``; a subscriber that stops reading receives a
completion response and is disconnected, so clients must reconnect with a new
``subscriptions/listen`` request. Applications needing A2A should install
``litestar-mcp[a2a]`` and configure ``LitestarA2A`` with the official SDK's
``AgentCard`` and ``RequestHandler``. ``A2AConfig.context_builder`` now accepts
a Litestar ``Request`` directly.

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
