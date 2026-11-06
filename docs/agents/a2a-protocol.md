---
title: A2A 1.0 Protocol & HTTP+JSON
---

# A2A 1.0 Protocol & HTTP+JSON

`LitestarA2A` (`litestar_mcp.a2a.LitestarA2A`, enabled via `litestar-mcp[a2a]`) provides a first-party, Litestar-native transport layer for the official Agent-to-Agent Python SDK (`a2a-sdk>=1.1.4`). It mounts `/.well-known/agent-card.json` discovery, A2A 1.0 JSON-RPC 2.0 (`POST /a2a`), and native A2A 1.0 `HTTP+JSON` REST routes without requiring Starlette, FastAPI, or Uvicorn.

--8<-- "tools/docs/includes/diagram-a2a-interoperability.html"

---

## Quickstart with LitestarA2A

Pass an official `a2a.types.AgentCard` and either an `a2a.server.request_handlers.RequestHandler` (such as `DefaultRequestHandler`) or an `a2a.server.agent_execution.AgentExecutor` directly to `LitestarA2A`:

```python title="docs/examples/snippets/a2a_plugin.py"
--8<-- "docs/examples/snippets/a2a_plugin.py:example"
```

When you pass an `AgentExecutor` instance instead of a `RequestHandler`, `LitestarA2A.__init__` automatically wraps it in `DefaultRequestHandler(agent_executor=request_handler, task_store=InMemoryTaskStore(), agent_card=agent_card)`.

---

## A2AConfig Reference

Configure mount paths, transport bindings, route security options, caching, and stream cleanup via `A2AConfig` (`litestar_mcp.a2a.A2AConfig`):

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `path` | `str` | `"/a2a"` | Base mount path for A2A JSON-RPC and `HTTP+JSON` routes. Must start with `/`; trailing slashes are normalized. |
| `agent_card_path` | `str` | `"/.well-known/agent-card.json"` | Public route serving the serialized `AgentCard`. Must start with `/`. |
| `guards` | `Sequence[Guard]` | `()` | Litestar route guards applied to JSON-RPC and `HTTP+JSON` execution routes (the public agent card route is unguarded by `A2AConfig.guards`). |
| `route_opt` | `dict[str, Any]` | `{}` | Extra Litestar `opt` entries merged into JSON-RPC and `HTTP+JSON` routes alongside `{"exclude_from_csrf": True}`. |
| `agent_card_route_opt` | `dict[str, Any]` | `{}` | Extra Litestar `opt` entries merged into the agent card route alongside `{"exclude_from_auth": True, "exclude_from_csrf": True}`. |
| `tenant_header` | `str or None` | `"X-A2A-Tenant"` | Fallback HTTP header inspected when neither the request payload nor `/{tenant:str}` URL path specifies a tenant. |
| `enable_http_json` | `bool` | `True` | Mount native A2A 1.0 `HTTP+JSON` REST routes alongside the JSON-RPC 2.0 endpoint. |
| `context_builder` | `Callable or None` | `None` | Optional `(request, context) -> ServerCallContext or Awaitable[ServerCallContext]` callback that authorizes and returns the authoritative `ServerCallContext`. |
| `include_in_schema` | `bool` | `False` | Whether to include A2A transport and card routes in the application's OpenAPI schema. |
| `agent_card_max_age` | `int` | `300` | `Cache-Control: public, max-age=<seconds>` header value on `/.well-known/agent-card.json`. Must be `>= 0`. |
| `stream_cleanup_timeout` | `float` | `5.0` | Positive finite timeout (in seconds) bounding cooperative SSE producer cleanup after a stream response closes. |

---

## Agent Card Discovery

At application initialization (`on_app_init`), `LitestarA2A` validates `agent_card` and pre-renders its ProtoJSON payload:

1. **Interface Validation (`_validate_card`)**: `agent_card.supported_interfaces` must advertise at least one absolute `http://` or `https://` URL for `protocol_version="1.0"` (or a `1.0.x` patch version) with `protocol_binding="JSONRPC"` or `"HTTP+JSON"` whose URL path ends with `A2AConfig.path`. Using a suffix check allows the `AgentCard` to advertise a public reverse-proxy URL or ASGI `root_path` prefix.
2. **SHA-256 `ETag` & `304 Not Modified`**: `LitestarA2A` computes a quoted SHA-256 `ETag` (`"<sha256>"`) over the serialized card bytes and sets `Cache-Control: public, max-age={agent_card_max_age}`. Incoming `GET /.well-known/agent-card.json` requests with a matching `If-None-Match` header (using RFC 9110 weak comparison, including `W/` prefixes and `*`) receive an empty `304 Not Modified` response.
3. **Route Collision Detection**: If any existing route on the Litestar application already occupies `A2AConfig.path` or `A2AConfig.agent_card_path`, `on_app_init` raises `ValueError("A2A route collision: ...")`.
4. **Skill Metadata & `agent_card_route_opt`**: When bridging agents with `agent_to_a2a`, attached `SkillController` instances automatically compile into `AgentSkill` entries on the card via `SkillController.to_agent_skill()`. When configuring `LitestarA2A` directly, you can tag routes and card handlers via `agent_card_route_opt` (for example `agent_card_route_opt={"a2a_skill": ...}` or public security policy markers) while keeping execution routes protected by `route_opt`.

!!! note
    An `AgentCard`'s `AgentSkill` entries describe high-level A2A task capabilities advertised to peer agents and are distinct from MCP Agent Skills (`SEP-2640` `SKILL.md` bundles served over `/mcp` via `skills/list` and `skills/get`).

---

## Dual Transport Bindings: JSON-RPC 2.0 & HTTP+JSON REST

Every request to an A2A execution endpoint must include the `A2A-Version: 1.0` header (`1.0.x` patch versions are also accepted). Missing, malformed, or legacy (`0.3`) version headers are rejected with `VersionNotSupportedError`.

### JSON-RPC 2.0 Binding

The JSON-RPC transport dispatches all 11 A2A 1.0 methods using protobuf JSON (`ParseDict` / `MessageToDict`):

| A2A 1.0 JSON-RPC Method | Handler Method | Response Type |
| :--- | :--- | :--- |
| `SendMessage` | `handler.on_message_send` | JSON-RPC `SendMessageResponse` (`task` or `message`) |
| `SendStreamingMessage` | `handler.on_message_send_stream` | `text/event-stream` (`ServerSentEvent`) |
| `GetTask` | `handler.on_get_task` | JSON-RPC `Task` (or `TaskNotFoundError`) |
| `ListTasks` | `handler.on_list_tasks` | JSON-RPC `ListTasksResponse` (strips `artifacts` when `include_artifacts=False`) |
| `CancelTask` | `handler.on_cancel_task` | JSON-RPC `Task` (or `TaskNotFoundError`) |
| `SubscribeToTask` | `handler.on_subscribe_to_task` | `text/event-stream` (`ServerSentEvent`) |
| `CreateTaskPushNotificationConfig` | `handler.on_create_task_push_notification_config` | JSON-RPC `TaskPushNotificationConfig` |
| `GetTaskPushNotificationConfig` | `handler.on_get_task_push_notification_config` | JSON-RPC `TaskPushNotificationConfig` |
| `ListTaskPushNotificationConfigs` | `handler.on_list_task_push_notification_configs` | JSON-RPC `ListTaskPushNotificationConfigsResponse` |
| `DeleteTaskPushNotificationConfig` | `handler.on_delete_task_push_notification_config` | JSON-RPC `null` result |
| `GetExtendedAgentCard` | `handler.on_get_extended_agent_card` | JSON-RPC `AgentCard` |

JSON-RPC notifications (well-formed requests without an `"id"` member) are declined with HTTP `204 No Content` before version checking and never invoke the handler. Requests with an explicit `"id": null` are treated as correlated requests and receive a JSON-RPC response.

### Native HTTP+JSON REST Binding

When `A2AConfig.enable_http_json` is `True` (the default), `LitestarA2A` mounts the full A2A 1.0 `HTTP+JSON` REST surface under `A2AConfig.path` (and its tenant-prefixed counterpart `/{base_prefix}/{tenant:str}...`), returning `application/a2a+json` on unary responses and structured REST error payloads (`build_rest_error_payload`) on failures:

| HTTP Method & Route Path | Tenant-Prefixed Route Path | A2A Operation |
| :--- | :--- | :--- |
| `GET /.well-known/agent-card.json` | *(public discovery)* | Public `AgentCard` with `ETag` and `Cache-Control` |
| `POST /a2a/message:send` | `POST /a2a/{tenant:str}/message:send` | `SendMessage` (`SendMessageRequest` JSON body) |
| `POST /a2a/message:stream` | `POST /a2a/{tenant:str}/message:stream` | `SendStreamingMessage` (SSE stream) |
| `GET /a2a/tasks` | `GET /a2a/{tenant:str}/tasks` | `ListTasks` (query parameters parsed via `proto_utils.parse_params`) |
| `GET /a2a/tasks/{id}` | `GET /a2a/{tenant:str}/tasks/{id}` | `GetTask` |
| `POST /a2a/tasks/{id}:cancel` | `POST /a2a/{tenant:str}/tasks/{id}:cancel` | `CancelTask` |
| `GET` or `POST /a2a/tasks/{id}:subscribe` | `GET` or `POST /a2a/{tenant:str}/tasks/{id}:subscribe` | `SubscribeToTask` (SSE stream) |
| `POST /a2a/tasks/{id}/pushNotificationConfigs` | `POST /a2a/{tenant:str}/tasks/{id}/pushNotificationConfigs` | `CreateTaskPushNotificationConfig` |
| `GET /a2a/tasks/{id}/pushNotificationConfigs` | `GET /a2a/{tenant:str}/tasks/{id}/pushNotificationConfigs` | `ListTaskPushNotificationConfigs` |
| `GET /a2a/tasks/{id}/pushNotificationConfigs/{push_id}` | `GET /a2a/{tenant:str}/tasks/{id}/pushNotificationConfigs/{push_id}` | `GetTaskPushNotificationConfig` |
| `DELETE /a2a/tasks/{id}/pushNotificationConfigs/{push_id}` | `DELETE /a2a/{tenant:str}/tasks/{id}/pushNotificationConfigs/{push_id}` | `DeleteTaskPushNotificationConfig` |
| `GET /a2a/extendedAgentCard` | `GET /a2a/{tenant:str}/extendedAgentCard` | `GetExtendedAgentCard` (authenticated extended card) |

```python title="docs/examples/snippets/a2a_http_json_multitenant.py"
--8<-- "docs/examples/snippets/a2a_http_json_multitenant.py:example"
```

---

## Resource Lifetime & Cooperative SSE Cleanup

Streaming A2A operations (`SendStreamingMessage` and `SubscribeToTask` across both JSON-RPC and `HTTP+JSON`) run inside a background producer task managed by `start_stream`, `prefetch_stream`, and `StreamCleanupMiddleware`:

1. **First-Event Prefetch**: `prefetch_stream` awaits the first event (or early error/header activation) before committing response headers so extension headers and initial setup errors are reflected accurately.
2. **Bounded Backpressure & Disconnect Cleanup**: A bounded `anyio` memory object stream (`capacity=1`) provides backpressure between the `RequestHandler` async generator and the ASGI response writer. When the client disconnects or the stream completes, `A2AConfig.stream_cleanup_timeout` (default `5.0` seconds) bounds the wait for the producer's `async with aclosing(stream):` finalizer.
3. **Executor Service Scopes**: Because Litestar HTTP route handlers return a `ServerSentEvent` response object before the stream finishes (and because background tasks may continue after `returnImmediately` or client disconnect), `AgentExecutor` implementations must open their own service or Dishka request scopes from the verified `context.call_context` rather than relying on request-scoped HTTP dependencies.
4. **Application Shutdown Hook**: `LitestarA2A.on_shutdown` automatically calls `await request_handler.aclose()` (when defined) during Litestar shutdown.

---

## Extension Activation

A2A 1.0 extensions declared in `AgentCard.capabilities.extensions` (`AgentExtension(uri=..., required=...)`) are negotiated per request via the `A2A-Extensions` HTTP header:

- **Required Extensions**: Before invoking `context_builder` or the handler, `LitestarA2A` checks that every `AgentExtension` with `required=True` is present in `context.requested_extensions`. If any required extension URI is missing, `LitestarA2A` raises `ExtensionSupportRequiredError`.
- **Activating Requested Extensions**: To activate one or more requested extensions for a call, store a `set[str]` of requested, advertised extension URIs in `context.state["a2a_activated_extensions"]` inside `context_builder` or inside your `RequestHandler` / `AgentExecutor` **before** returning a unary result or yielding the first SSE stream event:
  ```python
  EXTENSION_URI = "https://example.com/extensions/workspace-label"

  if EXTENSION_URI in context.requested_extensions:
      context.state["a2a_activated_extensions"] = {EXTENSION_URI}
  ```
- **Response Header Emission**: `LitestarA2A` validates that `context.state["a2a_activated_extensions"]` is a subset of `requested & advertised` safe header URIs and emits `A2A-Extensions: <comma-separated sorted URIs>` on the HTTP response.
