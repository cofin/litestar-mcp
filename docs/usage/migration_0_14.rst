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