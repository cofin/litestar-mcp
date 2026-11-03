---
title: Migrating to v0.12 Stateless MCP
---

# Migrating to `v0.12` Stateless MCP

This page preserves the historical migration reference for upgrading from `v0.11.x` stateful session/SSE transports to the `v0.12.0` stateless MCP `2026-07-28` architecture (`io.modelcontextprotocol/tasks`, `MCPInputRequiredResult`, and `subscriptions/listen`).

!!! note "Dual-Version Compatibility in `v0.17.0+`"
    While `v0.12.0` through `v0.16.x` accepted only `2026-07-28` (`server/discover`), `v0.17.0` added dual-version stateless Streamable HTTP negotiation on `/mcp`. Current releases (`v0.17.0+`) serve both `2026-07-28` (`server/discover` + SEP-2575 headers) and `2025-06-18` / `2025-03-26` (`initialize`, `notifications/initialized`, `ping`, and `DELETE /mcp`) on the same stateless endpoint. See [Migrating to `v0.14`–`v0.18`](migration-0-14.md) and [Dual-Version Discovery](../learn/discovery.md) for current protocol negotiation details.

---

## 1. Stateless MCP `2026-07-28` Transport

When targeting the `2026-07-28` protocol revision:

- Send one independent `POST /mcp` request per JSON-RPC operation; no server-side `Mcp-Session-Id` state or `Last-Event-ID` replay buffer is required.
- Pass protocol version, client capabilities, and optional client identity inside `params._meta` (`io.modelcontextprotocol/protocolVersion`, `io.modelcontextprotocol/clientCapabilities`, `io.modelcontextprotocol/clientInfo`) on every request.
- Send matching SEP-2575 routing headers:
  - `MCP-Protocol-Version: 2026-07-28`
  - `Mcp-Method: <jsonrpc-method>`
  - `Mcp-Name: <target-name-or-uri>` for `tools/call`, `resources/read`, `prompts/get`, `tasks/get`, `tasks/update`, and `tasks/cancel`.
  - Use the `=?base64?<urlsafe-base64>?=` encoding sentinel for header values containing non-ASCII or control characters.
- Call `server/discover` for stateless capability discovery.

---

## 2. `MCPConfig` Stateless Transport Settings

`v0.12.0` removed the legacy stateful session and idle-stream settings (`session_store`, `session_max_idle_seconds`, `sse_max_streams`, and `sse_max_idle_seconds`) and replaced them with stateless caching and subscription stream controls:

```python title="docs/examples/snippets/configuration_stateless.py"
--8<-- "docs/examples/snippets/configuration_stateless.py"
```

- `allowed_origins` is an exact additional allowlist of `Origin` values accepted alongside the request's own origin. Requests without an `Origin` header remain valid; requests with an untrusted `Origin` header are rejected with HTTP `403 Forbidden`.

---

## 3. MCP Tasks Extension (`io.modelcontextprotocol/tasks`)

Background task execution moved to the opt-in `io.modelcontextprotocol/tasks` extension configured via `MCPConfig(tasks=True)` or `MCPConfig(tasks=MCPTaskConfig(...))`:

- Clients opt into task-augmented execution per request by passing `params.task` (and advertising `"io.modelcontextprotocol/tasks": {}` in client capabilities).
- Replace legacy `tasks/result` and `tasks/list` calls with:
  - `tasks/get`: Poll the current `TaskRecord` status (`working`, `input_required`, `completed`, `failed`, `cancelled`) and terminal result.
  - `tasks/update`: Supply interactive `inputResponses` when a task is paused in `status == "input_required"`.
  - `tasks/cancel`: Request cooperative cancellation of an in-flight task.
- `task_support` (`"optional"`, `"required"`, `"forbidden"`) is enforced as a server-side policy on each tool:
  - `"optional"` tools execute as a background task when `params.task` is provided, or fall back to synchronous execution when omitted.
  - `"required"` tools reject calls that do not opt into `io.modelcontextprotocol/tasks` with JSON-RPC error `-32021` (`MISSING_REQUIRED_CLIENT_CAPABILITY`).

---

## 4. Multi-Round-Trip Requests (MRTR) & `MCPInputRequiredResult`

Tools, resources, and prompts can pause to collect interactive client input by returning `MCPInputRequiredResult(input_requests=..., request_state=...)`:

1. When the client retries the call (or submits `tasks/update` on an `input_required` task), read `get_mcp_request_context().input_responses` and `get_mcp_request_context().request_state`.
2. Because `requestState` is round-tripped through the client, protect any authorization-sensitive state with AEAD or HMAC-SHA256 bound to the authenticated principal (`owner_id`), original method/arguments, and an expiration timestamp.
3. Never treat `clientInfo` as an authorization identity. See [Authorization, Guards, Scopes & Input Safety](authorization-and-scopes.md) for a complete HMAC `requestState` sealing example.

---

## 5. Caching Hints & `subscriptions/listen`

- **Cache Metadata (`ttlMs` & `cacheScope`)**: Discovery (`server/discover`), catalog list (`*/list`), and `resources/read` results include `ttlMs` (`MCPConfig.cache_ttl_ms`) and `cacheScope` (`MCPConfig.cache_scope`, defaulting to `"private"`).
- **Multiplexed SSE Subscriptions (`subscriptions/listen`)**: Clients subscribe to catalog list changes, resource URIs, or task status updates over a single `POST /mcp` (`method: "subscriptions/listen"`) Server-Sent Events stream:
  - The initial SSE event acknowledges the accepted filter and assigns a subscription ID.
  - Subsequent `notifications/tasks`, `notifications/resources/updated`, and `notifications/*_list_changed` frames carry the subscription ID.
  - Active streams emit periodic keepalive comments (`MCPConfig.subscription_keepalive_seconds=15.0`) and fan out across multi-worker deployments when `MCPConfig.subscription_channels` is bound to a Litestar `ChannelsPlugin` backend.
