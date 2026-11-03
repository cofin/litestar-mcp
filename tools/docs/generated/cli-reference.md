All commands run under the Litestar CLI `mcp` group:

```bash
litestar --app app:app mcp <command> [OPTIONS]
```

| Command | Category | Summary |
| --- | --- | --- |
| [`litestar mcp stdio`](#litestar-mcp-stdio) | Transports & Proxies | Serve this Litestar app's MCP endpoint to a local stdio client in-process. |
| [`litestar mcp bridge`](#litestar-mcp-bridge) | Transports & Proxies | Proxy local stdio JSON-RPC to this Litestar app's MCP endpoint. |
| [`litestar mcp export-tool-spec`](#litestar-mcp-export-tool-spec) | Specification & Schema Export | Export the MCP tools/list specification as formatted JSON. |
| [`litestar mcp list-tools`](#litestar-mcp-list-tools) | Discovery & Interactive Execution | List all available MCP tools. |
| [`litestar mcp list-resources`](#litestar-mcp-list-resources) | Discovery & Interactive Execution | List all available MCP resources. |
| [`litestar mcp run`](#litestar-mcp-run) | Discovery & Interactive Execution | Run a discovered MCP tool by name. |

## Transports & Proxies

Commands for serving the Litestar application over in-process stdio or proxying stdio JSON-RPC frames to a remote Streamable HTTP endpoint.

### `litestar mcp stdio`

Serve this Litestar app's MCP endpoint to a local stdio client in-process.

```bash
litestar mcp stdio [OPTIONS]
```

**Options**

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--header` | `text` | `None` | Static HTTP header as 'Name: value'. |
| `--bearer-env` | `text` | `None` | Environment variable containing the bearer token. |
| `--bearer-cmd` | `text` | `None` | Command whose stdout returns the bearer token. |
| `--header-name` | `text` | `"Authorization"` | Header used for bearer tokens. |
| `--token-prefix` | `text` | `"Bearer "` | Prefix prepended to bearer token values. |
| `--sse-read-timeout` | `float` | `300.0` | Subscription stream read timeout in seconds. |
| `--max-message-size` | `integer` | `16777216` | Maximum bytes for one JSON-RPC message, from stdin or from the server. Use -1 to disable the limit. |

### `litestar mcp bridge`

Proxy local stdio JSON-RPC to this Litestar app's MCP endpoint.

```bash
litestar mcp bridge [OPTIONS]
```

**Options**

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--endpoint` | `text` | `None` | Full MCP Streamable HTTP endpoint URL. Overrides --base-url and the app's MCPConfig.base_path. |
| `--base-url` | `text` | `None` | Base URL of the already-running Litestar app. Defaults to LITESTAR_HOST/LITESTAR_PORT or http://127.0.0.1:8000. |
| `--header` | `text` | `None` | Static HTTP header as 'Name: value'. |
| `--bearer-env` | `text` | `None` | Environment variable containing the bearer token. |
| `--bearer-cmd` | `text` | `None` | Command whose stdout returns the bearer token. |
| `--header-name` | `text` | `"Authorization"` | Header used for bearer tokens. |
| `--token-prefix` | `text` | `"Bearer "` | Prefix prepended to bearer token values. |
| `--timeout` | `float` | `30.0` | HTTP connect/write/pool timeout in seconds. |
| `--sse-read-timeout` | `float` | `300.0` | SSE stream read timeout in seconds. Use 0 for no quiet-period timeout. |
| `--max-message-size` | `integer` | `16777216` | Maximum bytes for one JSON-RPC message, from stdin or from the server. Use -1 to disable the limit. |

## Specification & Schema Export

Commands for exporting deterministic MCP tool manifests and validating description length bounds in CI pipelines.

### `litestar mcp export-tool-spec`

Export the MCP tools/list specification as formatted JSON.

```bash
litestar mcp export-tool-spec [OPTIONS]
```

**Options**

| Option | Type | Default | Description |
| --- | --- | --- | --- |
| `--output`, `-o` | `Path` | `None` | Write the exported tool specification JSON to this file instead of stdout. |
| `--indent` | `integer` | `2` | Number of spaces for JSON indentation. |
| `--strict-bounds`, `--no-strict-bounds` | `bool` | `False` | Validate tool, parameter, and output field description lengths against bounds. |

## Discovery & Interactive Execution

Commands for inspecting discovered MCP tools and resources or invoking a tool directly from the terminal.

### `litestar mcp list-tools`

List all available MCP tools.

Uses plain description so terminal output never shows ``##`` headers.

```bash
litestar mcp list-tools
```

### `litestar mcp list-resources`

List all available MCP resources.

Uses plain description so terminal output never shows ``##`` headers.

```bash
litestar mcp list-resources
```

### `litestar mcp run`

Run a discovered MCP tool by name.

```bash
litestar mcp run
```
