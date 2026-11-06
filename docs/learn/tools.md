# Tools & Tool Annotations

Tools are executable operations exposed via `tools/list` and `tools/call`. Any Litestar route handler marked with `mcp_tool=True` (or decorated with `@mcp_tool`) is compiled into an MCP tool specification with a JSON Schema 2020-12 `inputSchema` and optional MCP tool annotation dictionaries (`mcp_annotations` / `annotations`).

## Internal ASGI Tool Execution (execute_tool)

When a client invokes `tools/call`, `MCPHandlerService` does not call the underlying Python function in isolation. Instead, `execute_tool` synthesizes an internal ASGI HTTP scope targeted at the route handler and runs the full Litestar handler lifecycle:

1. **Argument Validation**: `_validate_tool_arguments` validates incoming `params.arguments` against the handler's `parsed_fn_signature` and `msgspec` type converters before dispatch.
2. **`before_tool_call` Hook**: If configured on `MCPConfig`, `before_tool_call` runs with `(tool_name, arguments, request)`.
3. **Guards**: Route-, controller-, router-, and app-level Litestar `guards` execute against the synthesized `Request` (preserving `request.user`, `request.auth`, headers, and `litestar-security` `SecurityContext`).
4. **Dependency Injection**: Litestar `Provide` and `Dependency(skip_validation=True)` dependencies, `Request`, `State`, Dishka `Scope.REQUEST` (`FromDishka[T]`) dependencies, and `ToolContext` bindings resolve cleanly.
5. **Handler Execution & `after_tool_call` Hook**: Sync and async handlers run, and `after_tool_call` receives the `result`, `exception`, and elapsed `duration`.

## JSON Schema 2020-12 Synthesis

`generate_json_schema_for_handler` (cached via `cached_schema_for_handler`) introspects the route handler's `parsed_fn_signature` and builds a JSON Schema 2020-12 (`"https://json-schema.org/draft/2020-12/schema"`) `inputSchema`:

- **Scalar & Path/Query Parameters**: Path, query, and scalar arguments become top-level `properties` in `inputSchema`. Parameters without default values are listed in `required`.
- **`Annotated[T, Parameter(...)]` Constraints**: Litestar `Parameter` metadata (`title`, `description`, `gt`, `ge`, `lt`, `le`, `min_length`, `max_length`, `pattern`, `examples`) and custom `mcp_header` (`x-mcp-header`) extensions are unwrapped and emitted directly on the property schema.
- **Structured Body Models (`data`)**: Handlers declaring a `data` parameter typed as a `msgspec.Struct`, `@dataclass`, `TypedDict`, `pydantic.BaseModel`, or `attrs` class have their model fields expanded into `inputSchema`. Callers can pass structured fields either nested under `"data"` or flattened at the top level of `arguments`.
- **DI Exclusion**: `generate_json_schema_for_handler` strips Litestar internal DI parameters (`Provide`, `Dependency(skip_validation=True)`, `Request`, `State`, `ToolContext`, and Dishka `FromDishka[T]`) while keeping user-facing query, path, header (`x-mcp-header`), and body parameters in `inputSchema`.

## Tool Annotations & Automatic HTTP Verb Inference

MCP tool annotation dictionaries (`annotations` / `mcp_annotations`) provide behavioral hints (`title`, `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`, and `scopes`) to LLM clients and approval UIs.

When `MCPConfig.infer_http_method_annotations` is `True` (the default), `litestar-mcp` automatically infers baseline tool annotations from the route handler's HTTP verb:

| HTTP Method | Inferred `readOnlyHint` | Inferred `destructiveHint` | Inferred `idempotentHint` |
| :--- | :--- | :--- | :--- |
| `GET`, `HEAD` | `True` | `False` | `True` |
| `PUT` | `False` | *(omitted)* | `True` |
| `DELETE` | `False` | `True` | `True` |
| `POST`, `PATCH` | `False` | *(omitted)* | *(omitted)* |

### Annotation Resolution Precedence

Explicit annotations merge over the HTTP-inferred defaults in the following order (later entries win):

1. Inferred HTTP verb defaults (when `MCPConfig.infer_http_method_annotations=True`).
2. `@mcp_tool(annotations={...})` dictionary.
3. Route `opt` `mcp_annotations={...}` dictionary.
4. `@mcp_tool(read_only=True | False)` shorthand (sets `annotations["readOnlyHint"]`).
5. Route `opt` `mcp_read_only=True | False` shorthand (sets `annotations["readOnlyHint"]`).
6. `@mcp_tool(scopes=[...])` (sets `annotations["scopes"]` if `"scopes"` was not already provided in `annotations`).

```python title="docs/examples/snippets/tool_annotations.py"
--8<-- "docs/examples/snippets/tool_annotations.py:example"
```

Set `MCPConfig(infer_http_method_annotations=False)` if you want `tools/list` to include `annotations` only when explicitly declared via `@mcp_tool` or `handler.opt`.

## Explicit Input Schemas (input_schema)

When a tool requires a custom JSON Schema 2020-12 contract—such as `additionalProperties: False`, conditional `oneOf` / `allOf` clauses, or custom `$defs`—pass `input_schema=` (and optionally `output_schema=`) to `@mcp_tool`:

```python title="docs/examples/snippets/tool_explicit_input_schema.py"
--8<-- "docs/examples/snippets/tool_explicit_input_schema.py:example"
```

The explicit `input_schema` replaces signature inference for both `tools/list` discovery and SEP-2575 `Mcp-Param-*` header validation.

## Pre-Task Interactive Input (task_input_before_start)

For task-capable tools (`task_support="optional"` or `"required"`) that must collect interactive user confirmation or elicitation before a background task is created, set `task_input_before_start=True` (or `mcp_task_input_before_start=True`):

```python title="docs/examples/snippets/tool_task_input_before_start.py"
--8<-- "docs/examples/snippets/tool_task_input_before_start.py:example"
```

1. On the initial `tools/call` (where `context.input_responses` is `None`), the handler executes synchronously and returns `MCPInputRequiredResult` (`resultType: "input_required"`) without allocating a task record.
2. When the client retries `tools/call` supplying `params.inputResponses` and `params.requestState`, `MCPHandlerService` creates the background `TaskRecord` and dispatches execution with the supplied responses bound to `get_mcp_request_context().input_responses`.

## Binary and Mixed Content Results

Ordinary JSON-serializable return values (`dict`, `list`, `msgspec.Struct`, primitives) are serialized into a single `{"type": "text", "text": "..."}` content block. To return binary payloads, resource links, or multi-block results with `structuredContent`, return `MCPToolResult`, `MCPResourceLink`, or `MCPBlobResource`:

```python title="docs/examples/snippets/tool_binary_content.py"
--8<-- "docs/examples/snippets/tool_binary_content.py:example"
```

- **`MCPResourceLink`**: Emits a `{"type": "resource_link", "name": ..., "uri": ..., "mimeType": ...}` block instructing the client to fetch the payload on demand via `resources/read`.
- **`MCPBlobResource`**: Base64-encodes `data` into an embedded `{"type": "resource", "resource": {"uri": ..., "mimeType": ..., "blob": ...}}` block, subject to `MCPConfig.max_blob_bytes` (default `25 MiB`).
- **`MCPToolResult`**: Combines multiple content blocks (`content=[...]`), optional `structured_content={"...": ...}`, and an explicit `is_error` flag.

## Tool Error Contract (isError vs Protocol Errors)

Per the MCP specification (including SEP-2663), tool execution and argument validation failures are reported **inside the tool result** with `isError: true` so the LLM can inspect the failure and self-correct, whereas protocol-level routing failures return a JSON-RPC `error` object:

| Failure Condition | Wire Representation | Status / Error Code |
| :--- | :--- | :--- |
| **Missing or invalid tool argument** (`_validate_tool_arguments`) | `result: {"content": [{"type": "text", "text": "{\"error\":\"Invalid tool arguments\",\"errors\":[{\"path\":\"/arguments/...\",\"message\":\"...\"}]}"}], "isError": true}` | HTTP `200 OK` (`isError: true` with JSON Pointer paths) |
| **Non-object `params.arguments`** | `result: {"content": [{"type": "text", "text": "{\"error\":\"Tool arguments must be an object\"}"}], "isError": true}` | HTTP `200 OK` (`isError: true`) |
| **Handler raises HTTPException / Guard rejection / Exception** | `result: {"content": [{"type": "text", "text": "..."}], "isError": true}` | HTTP `200 OK` (`isError: true`) |
| **Unknown or filtered tool name** (or missing `params.name`) | `error: {"code": -32602, "message": "Tool not found: <name>"}` | HTTP `400 Bad Request` (`INVALID_PARAMS`, `-32602`) |
| **Missing required client capability** (`mcp_required_client_capabilities` or `task_support="required"`) | `error: {"code": -32021, "message": "...", "data": {"requiredCapabilities": {...}}}` | HTTP `400 Bad Request` (`MISSING_REQUIRED_CLIENT_CAPABILITY`, `-32021`) |
