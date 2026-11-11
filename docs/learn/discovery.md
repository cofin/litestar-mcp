# Discovery & Dual-Mode Streamable HTTP

`LitestarMCP` serves a dual-mode Streamable HTTP transport on a single configurable endpoint (`MCPConfig.base_path`, defaulting to `/mcp`) that negotiates stateless `2026-07-28` discovery alongside session-aware `2025-11-25`, `2025-06-18`, and `2025-03-26` handshakes out of the box:

```python
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2026-07-28",
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
)
```

```python title="docs/examples/snippets/discovery_endpoints.py"
--8<-- "docs/examples/snippets/discovery_endpoints.py:example"
```

## Comparing Stateless 2026-07-28 and Session-Aware 2025 Streamable HTTP

Both protocol families share the same `/mcp` route and internal ASGI execution pipeline, while separating stateless `2026-07-28` header-verified calls from `Mcp-Session-Id` Streamable HTTP sessions:

| Feature | MCP `2026-07-28` (Stateless Discovery + SEP-2575) | MCP `2025-11-25`, `2025-06-18` & `2025-03-26` (Session-Aware Handshake) |
| :--- | :--- | :--- |
| **Capability Discovery Method** | `server/discover` (stateless, cacheable `POST /mcp`, zero `Mcp-Session-Id`) | `initialize` issuing `Mcp-Session-Id` (`MCPSessionStore`, `MCPConfig.session_ttl_seconds`) followed by `notifications/initialized` |
| **`initialize` and `ping` Behavior** | Rejected with HTTP `404 Not Found` and JSON-RPC `METHOD_NOT_FOUND` (`-32601`) when `2026-07-28` headers or `_meta` are present | Handled natively (`initialize` returns `protocolVersion`, `capabilities`, `serverInfo`, `instructions`; `ping` returns `{}`) |
| **Server-Initiated SSE Stream** | `POST /mcp` (`subscriptions/listen` with camelCase notification filters) | `GET /mcp` with `Accept: text/event-stream` and `Mcp-Session-Id` (`MCPConfig.enable_get_sse = True`) |
| **Resource Subscriptions & Logging Level** | `subscriptions/listen` (`resourceSubscriptions`, `logLevel`) and per-request `params._meta["io.modelcontextprotocol/logLevel"]` | Session-scoped `resources/subscribe`, `resources/unsubscribe`, and `logging/setLevel` persisted on `MCPSession` |
| **Client notifications (`notifications/*`)** | Not used; subscriptions use `POST /mcp` (`subscriptions/listen`) | JSON-RPC notifications without an `id` field return HTTP `202 Accepted` with an empty body |
| **Session Termination (`DELETE /mcp`)** | Stateless no-op returning HTTP `204 No Content` | Deletes the `Mcp-Session-Id` record, closes bound SSE streams, and returns HTTP `204 No Content` |
| **Required Request `_meta`** | `params._meta["io.modelcontextprotocol/protocolVersion"]` and `params._meta["io.modelcontextprotocol/clientCapabilities"]` (plus optional `params._meta["io.modelcontextprotocol/logLevel"]`) | Optional (`params.protocolVersion`, `params.capabilities`, `params.clientInfo` passed on `initialize`) |
| **Required HTTP Routing Headers (SEP-2575)** | `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` (for named targets), and `Mcp-Param-*` (for header-annotated tool fields) | `Mcp-Session-Id` after `initialize` (plus optional `MCP-Protocol-Version`) |

## Stateless 2026-07-28 Discovery (server/discover) & SEP-2575 Headers

When a client communicates using MCP `2026-07-28`—indicated by calling `server/discover`, sending `MCP-Protocol-Version: 2026-07-28`, sending `Mcp-Method` / `Mcp-Name`, or including `io.modelcontextprotocol/*` keys in `params._meta`—`MCPController` enforces the full stateless `2026-07-28` and SEP-2575 contract:

1. **Mandatory `params._meta` Envelope**: Every request must carry `params._meta["io.modelcontextprotocol/protocolVersion"]` (matching the `MCP-Protocol-Version` header) and `params._meta["io.modelcontextprotocol/clientCapabilities"]` (a JSON object). Clients may also supply per-request `params._meta["io.modelcontextprotocol/logLevel"]` to adjust RFC 5424 server log emission without stateful session storage.
2. **`Mcp-Method` Header**: Must exactly match the JSON-RPC `method` field (`server/discover`, `tools/list`, `tools/call`, `resources/read`, etc.).
3. **`Mcp-Name` Header**: Required on target-addressed methods, and must match the target parameter in `params`:
   - `tools/call` -> `params.name`
   - `resources/read` -> `params.uri`
   - `prompts/get` -> `params.name`
   - `tasks/get`, `tasks/update`, `tasks/cancel` -> `params.taskId`
   - `skills/get`, `resources/directory/read` -> `params.uri`
4. **`Mcp-Param-<Custom-Name>` Headers**: When a tool input property declares `x-mcp-header` (via `Parameter(..., extra={"x-mcp-header": "Tenant-Id"})` or explicit `input_schema`), the client must mirror that argument's value in the `Mcp-Param-<Custom-Name>` HTTP header. Non-ASCII header values can use the MCP Base64 sentinel encoding `=?base64?<payload>?=` (`decode_mcp_header_value`).
5. **`initialize` and `ping` Rejection**: Sending `initialize` or `ping` with `2026-07-28` headers or `_meta` returns HTTP `404 Not Found` with JSON-RPC `METHOD_NOT_FOUND` (`-32601`).

=== "2026-07-28 (server/discover)"

    ```bash
    curl -sS -X POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "Accept: application/json, text/event-stream" \
      -H "MCP-Protocol-Version: 2026-07-28" \
      -H "Mcp-Method: server/discover" \
      -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {
          "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {},
            "io.modelcontextprotocol/clientInfo": {"name": "curl", "version": "1.0"}
          }
        }
      }'
    ```

=== "2025-11-25 (initialize and GET SSE)"

    ```bash
    curl -sSi -X POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "Accept: application/json, text/event-stream" \
      -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
          "protocolVersion": "2025-11-25",
          "capabilities": {},
          "clientInfo": {"name": "claude-code", "version": "1.0"}
        }
      }'

    curl -N http://127.0.0.1:8000/mcp \
      -H "Accept: text/event-stream" \
      -H "MCP-Protocol-Version: 2025-11-25" \
      -H "Mcp-Session-Id: <session-id-from-initialize>"
    ```

=== "2025-06-18 (initialize)"

    ```bash
    curl -sS -X POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
          "protocolVersion": "2025-06-18",
          "capabilities": {},
          "clientInfo": {"name": "inspector", "version": "1.0"}
        }
      }'
    ```

### Protocol Negotiation Error Codes

| Error Constant | JSON-RPC Code | HTTP Status | Trigger Condition |
| :--- | :--- | :--- | :--- |
| `INVALID_PARAMS` | `-32602` | `400 Bad Request` | Missing or non-object `params._meta`, missing `protocolVersion` / `clientCapabilities` in `2026-07-28` mode, invalid `logging/setLevel` threshold, or disallowed `Origin` header (`403 Forbidden`). |
| `HEADER_MISMATCH` | `-32020` | `400 Bad Request` | `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, or `Mcp-Param-*` header does not match the JSON-RPC body or bound session version. |
| `MISSING_REQUIRED_CLIENT_CAPABILITY` | `-32021` | `400 Bad Request` | Tool or task method requires a client capability not declared in `clientCapabilities` (error `data.requiredCapabilities` lists the required capabilities). |
| `UNSUPPORTED_PROTOCOL_VERSION` | `-32022` | `400 Bad Request` | `protocolVersion` is not in `SUPPORTED_PROTOCOL_VERSIONS` (error `data.supportedVersions` lists `["2026-07-28", "2025-11-25", "2025-06-18", "2025-03-26"]`). |
| `METHOD_NOT_FOUND` | `-32601` | `404 Not Found` | Unknown JSON-RPC method, or `initialize` / `ping` invoked with `2026-07-28` metadata/headers. |

## Session-Aware 2025-11-25, 2025-06-18, and 2025-03-26 Lifecycle

Clients targeting `"2025-11-25"`, `"2025-06-18"`, or `"2025-03-26"` (such as Claude Code, Cursor, VS Code, MCP Inspector, or standard Streamable HTTP SDKs) use session-backed Streamable HTTP on `/mcp` without SEP-2575 routing headers:

1. **`POST /mcp` (`initialize`) & `Mcp-Session-Id`**: Negotiates `protocolVersion` (echoing `"2025-11-25"`, `"2025-06-18"`, or `"2025-03-26"` when requested, or defaulting unrecognized `initialize` versions cleanly to `"2025-11-25"`), persists an owner-bound `MCPSession` in `MCPSessionStore` (`"litestar_mcp_sessions"`, expiring after `MCPConfig.session_ttl_seconds = 3600.0`), attaches the `Mcp-Session-Id` response header, and returns `capabilities`, `serverInfo`, and optional `instructions`. Subsequent `2025-*` requests attach `Mcp-Session-Id` (unknown or expired IDs fail with HTTP `404 Not Found`; cross-principal mismatches fail with HTTP `403 Forbidden`).
2. **`POST /mcp` (`notifications/initialized` and `ping`)**: JSON-RPC notifications without an `"id"` field return HTTP `202 Accepted` with an empty body (`notifications/initialized` with an `"id"` returns `{}`), and `ping` returns `{}`.
3. **`GET /mcp` Server-Sent Events (`enable_get_sse`)**: When `MCPConfig.enable_get_sse` is `True` (default), sending `GET /mcp` with `Accept: text/event-stream` and a valid `Mcp-Session-Id` opens a long-lived `text/event-stream` channel forwarding session-scoped `notifications/resources/updated`, `notifications/message`, `notifications/progress`, `notifications/tasks`, and catalog `list_changed` events:

| `GET /mcp` Condition | HTTP Status | Details |
| :--- | :--- | :--- |
| `enable_get_sse=True`, `Accept` includes `text/event-stream`, valid session and `Origin` | `200 OK` | Opens `text/event-stream` response with periodic `: keepalive` frames (`MCPConfig.subscription_keepalive_seconds`). |
| `enable_get_sse=False` or no `Accept` header sent | `405 Method Not Allowed` | Clean Streamable HTTP probe response indicating `GET` SSE is disabled or omitted `Accept`. |
| Explicit `Accept` header omits `text/event-stream` (and `*/*`) | `406 Not Acceptable` | Client explicitly requested a non-SSE media type on the event endpoint. |
| Disallowed `Origin` header or cross-principal `Mcp-Session-Id` | `403 Forbidden` | Enforces `MCPConfig.allowed_origins` DNS-rebinding guard and session `owner_id` isolation. |
| Unknown or expired `Mcp-Session-Id` | `404 Not Found` | Prompts the Streamable HTTP client to re-run `initialize` and establish a fresh session. |
| Active streams hit `MCPConfig.subscription_max_streams` | `503 Service Unavailable` | Rejects excess concurrent SSE listeners cleanly when backpressure limits are reached. |

4. **Session Subscriptions & Logging (`resources/subscribe`, `logging/setLevel`)**: Active `2025-*` sessions invoke `resources/subscribe` and `resources/unsubscribe` (gated by `MCPConfig.subscription_authorizer` when configured) to manage per-URI `notifications/resources/updated` feeds, and call `logging/setLevel` to persist their RFC 5424 severity threshold on `MCPSession.log_level`.
5. **`DELETE /mcp` Session Termination**: Sending `DELETE /mcp` with `Mcp-Session-Id` deletes the session record from `MCPSessionStore`, closes any active SSE streams bound to that session, and returns HTTP `204 No Content` (stateless `DELETE /mcp` without `Mcp-Session-Id` remains a `204 No Content` no-op).

## Metadata & Well-Known Endpoints

The MCP specification does not publish a `/.well-known/mcp-server.json` document; all MCP capability discovery flows through `POST /mcp` (`server/discover` or `initialize`). When your application integrates complementary discovery protocols, they mount their own well-known routes alongside `/mcp`:

- **OAuth 2.0 Protected Resource Metadata (RFC 9728)**: Served at `/.well-known/oauth-protected-resource` when configured via `litestar-security`'s `ProtectedResourceConfig`—see [First-Party `litestar-security`](../security/litestar-security.md).
- **A2A 1.0 Agent Card**: Served at `/.well-known/agent-card.json` when the `LitestarA2A` plugin is enabled—see [A2A 1.0 Protocol](../agents/a2a-protocol.md).
