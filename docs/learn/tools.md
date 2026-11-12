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

`generate_schema_for_handler` (cached via `cached_schema_for_handler`) introspects the route handler's `parsed_fn_signature` and builds a JSON Schema 2020-12 (`"https://json-schema.org/draft/2020-12/schema"`) `inputSchema`:

- **Scalar & Path/Query Parameters**: Path, query, and scalar arguments become top-level `properties` in `inputSchema`. Parameters without default values are listed in `required`.
- **`Annotated[T, Parameter(...)]` Constraints**: Litestar `Parameter` metadata (`title`, `description`, `gt`, `ge`, `lt`, `le`, `min_length`, `max_length`, `pattern`, `examples`) and custom `mcp_header` (`x-mcp-header`) extensions are unwrapped and emitted directly on the property schema.
- **Structured Body Models (`data`)**: Handlers declaring a `data` parameter typed as a `msgspec.Struct`, `@dataclass`, `TypedDict` (both `typing.TypedDict` and `typing_extensions.TypedDict`, synthesized via `typed_dict_to_json_schema` honoring `Required`, `NotRequired`, and `total=False`), `pydantic.BaseModel`, or `attrs` class have their model fields expanded into `inputSchema`. Callers can pass structured fields either nested under `"data"` or flattened at the top level of `arguments`.
- **DI Exclusion**: `generate_schema_for_handler` strips Litestar internal DI parameters (`Provide`, `Dependency(skip_validation=True)`, `Request`, `State`, `ToolContext`, and Dishka `FromDishka[T]`) while keeping user-facing query, path, header (`x-mcp-header`), and body parameters in `inputSchema`.

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

## Structured Outputs and Output Schemas

MCP tools can publish a JSON Schema 2020-12 `outputSchema` in `tools/list` and automatically return typed `structuredContent` in `tools/call`. Control output schema emission globally via `MCPConfig(auto_output_schema=True)` (`False` by default) or per tool via `output_schema: dict[str, Any] | bool | None = None` on `@mcp_tool`, `@mcp.tool`, `@tool`, and route `opt` `mcp_output_schema` (`MCPOptKeys.output_schema`):

| `output_schema` Value | Global `auto_output_schema` | Behavior |
| :--- | :--- | :--- |
| `dict[str, Any]` | `False` or `True` | Emits the explicit dictionary verbatim as `outputSchema` in `tools/list`. |
| `True` | `False` or `True` | Calls `generate_output_schema_for_handler` (`type_to_output_schema`) to synthesize an object `outputSchema` from the handler's return annotation (`msgspec.Struct`, `@dataclass`, `TypedDict`, `pydantic.BaseModel`, `attrs`, or typed `dict`). |
| `False` | `False` or `True` | Explicitly opts the route out of `outputSchema` advertisement and automatic `structuredContent` population, even when `MCPConfig(auto_output_schema=True)` is set. |
| `None` (default) | `True` | Synthesizes `outputSchema` automatically when the return annotation resolves to an object schema; silently omits `outputSchema` for scalar, `list`, or `MCPToolResult` return annotations. |
| `None` (default) | `False` | Omits `outputSchema` unless explicitly declared per route. |

- **Root Object Unwrapping (`msgspec.Struct`)**: `msgspec.json.schema_components` emits a root `$ref` pointing into `$defs`. `unwrap_root_object_schema` hoists the root struct definition inline so `outputSchema` always exposes `"type": "object"` at the root while keeping nested `$defs` intact for child models.
- **Automatic `structuredContent` & `type_encoders`**: Whenever a tool declares an object `outputSchema`, returning a mapping or structured model (`msgspec.Struct`, `@dataclass`, `TypedDict`, `pydantic.BaseModel`, or `attrs`) populates both `structuredContent` and the JSON text entry in `content`. Merged application and route `type_encoders` apply consistently to both `content` serialization and `structuredContent` conversion (including `MCPToolResult(structured_content=...)`).
- **`MCPToolResult` Passthrough & Non-Object Guardrails**: Standalone `@mcp.tool` handlers returning `MCPToolResult` pass the envelope through directly without JSON-wrapping it as text. When a handler returns `MCPToolResult(content=[...])` leaving `structured_content` unset (`None`), caller control is preserved without injecting synthetic fields. If a tool advertises an object `outputSchema` and returns a non-object value (such as a bare `list` or primitive) at runtime, `tools/call` reports a tool error with `isError: true`.

```python
import msgspec
from litestar import Litestar, get

from litestar_mcp import LitestarMCP, MCPConfig, mcp_tool


class OrderSummary(msgspec.Struct):
    order_id: str
    total_cents: int


@get("/orders/{order_id:str}", mcp_tool="get_order")
async def get_order(order_id: str) -> OrderSummary:
    """Return a typed order summary with synthesized outputSchema."""
    return OrderSummary(order_id=order_id, total_cents=4200)


@get("/orders/ping")
@mcp_tool("orders_ping", output_schema=False)
async def orders_ping() -> dict[str, str]:
    """Opt out of automatic outputSchema synthesis for a lightweight probe."""
    return {"status": "ok"}


app = Litestar(
    route_handlers=[get_order, orders_ping],
    plugins=[LitestarMCP(MCPConfig(auto_output_schema=True))],
)
```

## MCP Apps UI Metadata and Stacked Decorators

Tools support SEP-1865 presentation and interactive UI metadata via `title`, `icons`, and `meta` across `@mcp_tool`, `@mcp.tool`, `@tool`, and route `opt` keys `mcp_tool_title`, `mcp_tool_icons`, and `mcp_tool_meta` (`MCPOptKeys`). Route `opt` `mcp_tool_meta` shallow-merges over decorator `meta`, emitting `title`, `icons`, and `_meta` on `tools/list` entries.

- **Linking Tools to MCP Apps (`io.modelcontextprotocol/ui`)**: Declare `meta={"ui": {"resourceUri": "ui://widgets/orders", "visibility": ["model", "app"]}}` to pair a tool with an interactive HTML resource (`text/html;profile=mcp-app`). Set `"visibility": ["app"]` for UI-triggered helper tools hidden from LLM agent bridges (`mcp_to_tools`) by default, and pass `MCPConfig(filter_app_only_tools=True)` when wire `tools/list` responses should also omit app-only tools (stamping `cacheScope: "private"` while keeping `tools/call` active for embedded views).
- **Inspecting Client UI Support (`ui_capabilities`)**: Inside an active MCP call, read `get_mcp_request_context().ui_capabilities` to check the client's negotiated `io.modelcontextprotocol/ui` capability dictionary (such as supported `mimeTypes`) and tailor fallback output when a host does not render MCP Apps.
- **Stacked Decorator Isolation (`MetadataRegistry`)**: `MetadataRegistry` keys attached metadata weakly by target callable and primitive role (`"tool"`, `"resource"`, `"prompt"`), isolating even nested closures sharing a `__qualname__` so stacking `@mcp_tool` and `@mcp_resource` on the same Litestar route handler keeps tool `title`, `icons`, `meta`, and `output_schema` strictly separated from resource `uri`, `mime_type`, `title`, `icons`, and `meta`.

```python
from litestar import get

from litestar_mcp import get_mcp_request_context, mcp_resource, mcp_tool


@get("/widgets/orders")
@mcp_tool(
    "open_orders_widget",
    title="Open Orders Widget",
    meta={"ui": {"resourceUri": "ui://widgets/orders", "visibility": ["model", "app"]}},
)
@mcp_resource(
    "orders_widget_html",
    uri="ui://widgets/orders",
    mime_type="text/html;profile=mcp-app",
    title="Orders Widget View",
)
async def orders_widget() -> dict[str, object]:
    """Expose independent tool and UI resource metadata from one handler."""
    ctx = get_mcp_request_context(None)
    return {"uiSupported": ctx is not None and ctx.ui_capabilities is not None}
```

### Paired Structured Tool and UI Resource Example

The runnable snippet below pairs a `ReportSummary` `msgspec.Struct` tool (`output_schema=True`, `title`, and `meta={"ui": {"resourceUri": "ui://reports/view.html"}}`) with a static `ui://` HTML resource (`text/html;profile=mcp-app`):

```python title="docs/examples/snippets/mcp_apps_and_structured.py"
--8<-- "docs/examples/snippets/mcp_apps_and_structured.py:example"
```

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
| **Non-object runtime return when object `outputSchema` is declared** | `result: {"content": [{"type": "text", "text": "..."}], "isError": true}` | HTTP `200 OK` (`isError: true`) |
| **Unknown or filtered tool name** (or missing `params.name`) | `error: {"code": -32602, "message": "Tool not found: <name>"}` | HTTP `400 Bad Request` (`INVALID_PARAMS`, `-32602`) |
| **Missing required client capability** (`mcp_required_client_capabilities` or `task_support="required"`) | `error: {"code": -32021, "message": "...", "data": {"requiredCapabilities": {...}}}` | HTTP `400 Bad Request` (`MISSING_REQUIRED_CLIENT_CAPABILITY`, `-32021`) |
