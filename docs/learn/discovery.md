# Discovery & Dual-Version Streamable HTTP

`LitestarMCP` serves a stateless Streamable HTTP transport on a single configurable endpoint (`MCPConfig.base_path`, defaulting to `/mcp`) that negotiates all three modern Model Context Protocol versions out of the box:

```python
SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2026-07-28",
    "2025-06-18",
    "2025-03-26",
)
```

```python title="docs/examples/snippets/discovery_endpoints.py"
--8<-- "docs/examples/snippets/discovery_endpoints.py"
```

## Comparing `2026-07-28` vs `2025-06-18` / `2025-03-26`

Both protocol families share the same `/mcp` route and internal ASGI execution pipeline, but differ in how capability discovery and per-request metadata are exchanged:

| Feature | MCP `2026-07-28` (Stateless Discovery + SEP-2575) | MCP `2025-06-18` & `2025-03-26` (Initialize Handshake) |
| :--- | :--- | :--- |
| **Capability Discovery Method** | `server/discover` (stateless, cacheable `POST /mcp`) | `initialize` followed by `notifications/initialized` |
| **`initialize` and `ping` Behavior** | Rejected with HTTP `404 Not Found` and JSON-RPC `METHOD_NOT_FOUND` (`-32601`) when `2026-07-28` headers or `_meta` are present | Handled natively (`initialize` returns `protocolVersion`, `capabilities`, `serverInfo`, `instructions`; `ping` returns `{}`) |
| **Client notifications (`notifications/*`)** | Not used; subscriptions use `POST /mcp` (`subscriptions/listen`) | JSON-RPC notifications without an `id` field return HTTP `202 Accepted` with an empty body |
| **Session Termination (`DELETE /mcp`)** | Returns HTTP `204 No Content` | Returns HTTP `204 No Content` |
| **Required Request `_meta`** | `params._meta["io.modelcontextprotocol/protocolVersion"]` and `params._meta["io.modelcontextprotocol/clientCapabilities"]` | Optional (`params.protocolVersion`, `params.capabilities`, `params.clientInfo` passed on `initialize`) |
| **Required HTTP Routing Headers (SEP-2575)** | `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name` (for named targets), and `Mcp-Param-*` (for header-annotated tool fields) | Optional |

## Stateless `2026-07-28` Discovery (`server/discover`) & SEP-2575 Headers

When a client communicates using MCP `2026-07-28`—indicated by calling `server/discover`, sending `MCP-Protocol-Version: 2026-07-28`, sending `Mcp-Method` / `Mcp-Name`, or including `io.modelcontextprotocol/*` keys in `params._meta`—`MCPController` enforces the full stateless `2026-07-28` and SEP-2575 contract:

1. **Mandatory `params._meta` Envelope**: Every request must carry `params._meta["io.modelcontextprotocol/protocolVersion"]` (matching the `MCP-Protocol-Version` header) and `params._meta["io.modelcontextprotocol/clientCapabilities"]` (a JSON object).
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
| `INVALID_PARAMS` | `-32602` | `400 Bad Request` | Missing or non-object `params._meta`, missing `protocolVersion` / `clientCapabilities` in `2026-07-28` mode, or disallowed `Origin` header (`403 Forbidden`). |
| `HEADER_MISMATCH` | `-32020` | `400 Bad Request` | `MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, or `Mcp-Param-*` header does not match the JSON-RPC body. |
| `MISSING_REQUIRED_CLIENT_CAPABILITY` | `-32021` | `400 Bad Request` | Tool or task method requires a client capability not declared in `clientCapabilities` (error `data.requiredCapabilities` lists the required capabilities). |
| `UNSUPPORTED_PROTOCOL_VERSION` | `-32022` | `400 Bad Request` | `protocolVersion` is not in `SUPPORTED_PROTOCOL_VERSIONS` (error `data.supportedVersions` lists `["2026-07-28", "2025-06-18", "2025-03-26"]`). |
| `METHOD_NOT_FOUND` | `-32601` | `404 Not Found` | Unknown JSON-RPC method, or `initialize` / `ping` invoked with `2026-07-28` metadata/headers. |

## `2025-06-18` and `2025-03-26` Handshake Lifecycle

Clients targeting `2025-06-18` or `2025-03-26` (such as Claude Desktop, MCP Inspector, or standard Streamable HTTP SDKs) interact with the exact same `/mcp` endpoint without SEP-2575 routing headers:

1. **`POST /mcp` (`initialize`)**: Negotiates `protocolVersion` (echoing `"2025-06-18"` or `"2025-03-26"` when requested, or defaulting to `"2025-06-18"` if an older version is requested via `initialize`) and returns `capabilities`, `serverInfo`, and optional `instructions`.
2. **`POST /mcp` (`notifications/initialized` or any `notifications/*`)**: When sent as a JSON-RPC notification (no `"id"` field), `MCPController` returns HTTP `202 Accepted` with an empty body. If sent with an `"id"`, `notifications/initialized` returns `{}`.
3. **`POST /mcp` (`ping`)**: Health-check probe returning `{}`.
4. **`DELETE /mcp`**: Stateless session termination endpoint returning HTTP `204 No Content` with `MCP-Protocol-Version: 2026-07-28`.

## Metadata & Well-Known Endpoints

The MCP specification does not publish a `/.well-known/mcp-server.json` document; all MCP capability discovery flows through `POST /mcp` (`server/discover` or `initialize`). When your application integrates complementary discovery protocols, they mount their own well-known routes alongside `/mcp`:

- **OAuth 2.0 Protected Resource Metadata (RFC 9728)**: Served at `/.well-known/oauth-protected-resource` when configured via `litestar-security`'s `ProtectedResourceConfig`—see [First-Party `litestar-security`](../security/litestar-security.md).
- **A2A 1.0 Agent Card**: Served at `/.well-known/agent-card.json` when the `LitestarA2A` plugin is enabled—see [A2A 1.0 Protocol](../agents/a2a-protocol.md).
