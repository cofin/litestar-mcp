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
