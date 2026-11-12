# Stdio Transport, HTTP Bridge & Tool Spec Export

In addition to serving Streamable HTTP directly from your Litestar application, `litestar-mcp` registers a `litestar mcp` CLI command group with subcommands for **in-process stdio execution** (`stdio`), **remote Streamable HTTP bridging** (`bridge`), **static tool manifest export** (`export-tool-spec`), and **interactive CLI inspection** (`list-tools`, `list-resources`, `list-skills`, `run`).

## Choosing Between stdio and bridge

| Transport Mode | Command / Python API | How It Executes | Best For |
| :--- | :--- | :--- | :--- |
| **In-Process Stdio** | `litestar --app my_app:app mcp stdio` / `run_stdio(app)` / `run_stdio_async(app)` | Boots the Litestar ASGI lifespan in-process (without binding a TCP port) and routes newline-delimited JSON-RPC over `stdin`/`stdout` via `ASGIStreamingTransport`. | Single-file PEP 723 scripts (`uv run`), local desktop MCP hosts spawning the app directly, and test harnesses. |
| **HTTP-to-Stdio Bridge** | `litestar --app my_app:app mcp bridge` / `run_bridge(endpoint)` / `run_stdio_streamable_http_bridge(endpoint)` | Reads JSON-RPC from local `stdin` and forwards both stateless `2026-07-28` (`server/discover` + SEP-2575 headers) and session-aware `2025-11-25`, `2025-06-18`, and `2025-03-26` (`initialize` + `Mcp-Session-Id` + `GET` SSE + `DELETE` teardown) traffic over HTTP/SSE. | Connecting stdio-only MCP desktop clients to a remote, containerized, or multi-replica Litestar deployment. |

## In-Process Stdio (litestar mcp stdio)

Run your Litestar application as an in-process stdio MCP server using the Litestar CLI:

```bash
litestar --app my_app:app mcp stdio
```

During `litestar mcp stdio`, standard output is reserved exclusively for newline-delimited JSON-RPC frames; any accidental `print()` output from application code is redirected to `sys.stderr`.

### Programmatic Stdio & MCPStdioContext

You can also drive the in-process stdio loop directly from Python using `run_stdio`, `run_stdio_async`, or `MCP.run(transport="stdio")`. Pass an `MCPStdioContext` to pre-populate caller identity, task ownership, and request state on every synthesized ASGI scope, or pass `headers`, `token_provider`, `header_name`, and `token_prefix` to attach authentication headers verified by your Litestar middleware:

```python
from litestar import Litestar
from litestar_mcp import LitestarMCP, MCPStdioContext
from litestar_mcp.mcp.stdio import run_stdio

app = Litestar(route_handlers=[...], plugins=[LitestarMCP()])

stdio_context = MCPStdioContext(
    client_id="local-desktop",
    owner_id="user:local-dev",
    user={"id": "local-dev", "roles": ["admin"]},
    auth={"sub": "local-dev"},
    state={"workspace": "default"},
)

run_stdio(app, stdio_context=stdio_context)
```

| `MCPStdioContext` Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `client_id` | `str` | `"stdio"` | Default client identifier injected into `io.modelcontextprotocol/clientInfo`. |
| `owner_id` | `str or None` | `None` | Explicit task-owner identifier injected into `scope["litestar_mcp.owner_id"]` to scope background MCP Tasks created over stdio. |
| `user` | `Any` | `None` | Pre-populated `request.user` principal attached to every synthesized ASGI HTTP scope when no authentication middleware populates `scope["user"]`. |
| `auth` | `Any` | `None` | Pre-populated `request.auth` context attached to every synthesized ASGI HTTP scope. |
| `session` | `Mapping[str, Any] or None` | `None` | Optional session dictionary copied into `scope["session"]`. |
| `state` | `Mapping[str, Any] or None` | `None` | Optional initial state keys merged into `scope["state"]`. |

## HTTP-to-Stdio Bridge (litestar mcp bridge)

When your Litestar service runs remotely (or in a separate local process/container) and an MCP host only supports stdio, use `litestar mcp bridge`:

```bash
litestar --app my_app:app mcp bridge --base-url https://api.example.com
```

### Automatic Base Path Resolution

The bridge inspects the loaded application's `LitestarMCP` plugin configuration and appends `MCPConfig.base_path` to `--base-url` (or `http://{LITESTAR_HOST}:{LITESTAR_PORT}`, defaulting to `http://127.0.0.1:8000`):

```python title="docs/examples/snippets/bridge_custom_base_path.py"
--8<-- "docs/examples/snippets/bridge_custom_base_path.py:example"
```

For the application above (`MCPConfig(base_path="/api/mcp")`), running `litestar --app my_app:app mcp bridge` targets `http://127.0.0.1:8000/api/mcp` automatically.

### Dual-Version Session and Event Forwarding

`run_stdio_streamable_http_bridge` (`run_bridge` / `_StreamableHTTPBridgeClient`) adapts automatically to the protocol version requested by the local stdio host:

- **Session-Aware `2025-11-25`, `2025-06-18`, and `2025-03-26` Hosts**: When the stdio host sends `initialize`, the bridge records the negotiated protocol version and captures the `Mcp-Session-Id` response header. Subsequent `POST` calls forward `Mcp-Session-Id` and `MCP-Protocol-Version` cleanly without injecting `2026-07-28` `_meta` fields or SEP-2575 `Mcp-Method`, `Mcp-Name`, or `Mcp-Param-*` headers. After initialization, the bridge opens a background `GET` SSE listener (`Accept: text/event-stream` with `Mcp-Session-Id`) to relay server-initiated notifications (`notifications/resources/updated`, `notifications/message`, `notifications/progress`, `notifications/tasks`, and catalog `list_changed` events) onto `stdout`—cleanly ignoring `405 Method Not Allowed` when the upstream server sets `MCPConfig.enable_get_sse=False`—and sends a `DELETE` request with `Mcp-Session-Id` on shutdown.
- **Stateless `2026-07-28` Hosts**: When the stdio host uses `server/discover`, the bridge attaches `params._meta` and SEP-2575 headers (`MCP-Protocol-Version`, `Mcp-Method`, `Mcp-Name`, `Mcp-Param-*`), streams `POST` `subscriptions/listen` responses directly, and remains completely stateless with zero background `GET` or `DELETE` calls.

### litestar mcp bridge & litestar mcp stdio CLI Options

| CLI Flag | Applies To | Default | Description |
| :--- | :--- | :--- | :--- |
| `--endpoint <url>` | `bridge` | `None` | Full Streamable HTTP endpoint URL override (takes precedence over `--base-url` and `MCPConfig.base_path`). |
| `--base-url <url>` | `bridge` | `http://127.0.0.1:8000` | Base origin URL of the running Litestar app (`MCPConfig.base_path` is appended automatically). |
| `--header "Name: value"` | `bridge`, `stdio` | `()` | Static HTTP header attached to every forwarded request. Pass multiple times for multiple headers. |
| `--bearer-env <VAR>` | `bridge`, `stdio` | `None` | Environment variable name containing the bearer token. Mutually exclusive with `--bearer-cmd`. |
| `--bearer-cmd "<cmd>"` | `bridge`, `stdio` | `None` | Command executed to obtain a fresh bearer token on demand (and retried once automatically on `401 Unauthorized`). |
| `--header-name <name>` | `bridge`, `stdio` | `"Authorization"` | HTTP header name used when attaching the bearer token (for example, `"X-Goog-IAP-JWT-Assertion"` for Google IAP). |
| `--token-prefix <prefix>` | `bridge`, `stdio` | `"Bearer "` | Prefix prepended to the resolved token value (pass `""` for raw header tokens). |
| `--timeout <seconds>` | `bridge` | `30.0` | HTTP connection, write, and pool timeout in seconds. |
| `--sse-read-timeout <seconds>` | `bridge`, `stdio` | `300.0` | Maximum quiet-period read timeout in seconds for streaming SSE responses (`0` disables the quiet-period timeout). |
| `--max-message-size <bytes>` | `bridge`, `stdio` | `16777216` (`16 MiB`) | Maximum byte length for a single newline-delimited JSON-RPC message on `stdin` or a single SSE event from the server (`-1` disables the cap). |

### Desktop Client Configuration Example

```json
{
  "mcpServers": {
    "my-litestar-service": {
      "command": "litestar",
      "args": [
        "--app",
        "my_app:app",
        "mcp",
        "bridge",
        "--base-url",
        "https://api.example.com",
        "--bearer-env",
        "MCP_API_TOKEN"
      ]
    }
  }
}
```

## Static Tool Manifest Export (litestar mcp export-tool-spec)

Use `litestar mcp export-tool-spec` in CI pipelines, schema registries, or documentation builds to export the exact `tools/list` JSON specification (`name`, `description`, `inputSchema`, `outputSchema`, `annotations`) without starting an HTTP server:

```bash
litestar --app my_app:app mcp export-tool-spec \
  --output tools.json \
  --indent 2 \
  --strict-bounds
```

### Description Length Validation (--strict-bounds)

Passing `--strict-bounds` (or configuring `max_tool_description_length`, `max_param_description_length`, or `max_output_field_description_length` on `MCPConfig`) runs `validate_tool_descriptions` against every exported tool entry and exits non-zero if any description exceeds its character budget:

| Bound Option | `MCPConfig` Default | `--strict-bounds` Default | Validated Target |
| :--- | :--- | :--- | :--- |
| `max_tool_description_length` | `None` | `1000` characters | `tools[].description` |
| `max_param_description_length` | `None` | `750` characters | `tools[].inputSchema.properties.*.description` |
| `max_output_field_description_length` | `None` | `300` characters | `tools[].outputSchema.properties.*.description` |

You can also invoke `validate_tool_descriptions(tools, max_tool_description_length=1000, max_param_description_length=750, max_output_field_description_length=300)` directly in your unit test suite to enforce concise LLM tool descriptions in CI.
