---
title: Agent Runtime & Turn Budgets
---

# Agent Runtime & Turn Budgets

`litestar_mcp.agent` provides a guarded execution runtime (`AgentRuntime`), declarative agent specifications (`Agent`), type-driven tools (`@tool` and `ToolContext`), strict execution budgets (`TurnBudget`), and streaming model adapters (`ModelClient`, `GoogleGenAIClient`, and `MockModelClient`).

---

## Defining an Agent

An `Agent` (`litestar_mcp.agent.Agent`) encapsulates instructions, direct tools, reusable `SkillController` classes or instances, and an optional model binding:

| Field | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `name` | `str` | *(required)* | Unique agent identifier within an application or `AgentGroup`. |
| `description` | `str` | `""` | Human-readable summary of the agent's purpose and capabilities (also used for A2A `AgentCard` generation). |
| `instructions` | `str` | `""` | Base system instructions guiding the agent's behavior. |
| `tools` | `Sequence[Tool or Callable[..., Any]]` | `()` | `Tool` instances or plain functions (automatically wrapped via `tool()`) exposed directly to the agent. |
| `skills` | `Sequence[SkillController or type[SkillController]]` | `()` | `SkillController` classes or instances contributing domain `@tool` methods and `instructions`. |
| `model` | `ModelClient or str or None` | `None` | Bound `ModelClient` instance or model identifier string (for example `"gemini-3.8-flash"`). Falls back to `AgentRuntime.default_model`. |

During `__post_init__`, `Agent`:

1. Instantiates any `SkillController` classes passed in `skills` once (`agent.skill_instances`).
2. Combines direct `tools` with every tool returned by `skill.get_tools()` into `agent.tool_set`.
3. Joins `agent.instructions` and non-empty `skill.get_instructions()` blocks with blank lines via `agent.combined_instructions`.
4. Rejects duplicate tool names or any user tool named `"transfer_to_agent"` (reserved for `AgentGroup` handoffs) with `ImproperlyConfiguredException`.

---

## Authoring Tools

Decorate functions or `SkillController` methods with `@tool` (`litestar_mcp.tool` / `litestar_mcp.core.tools.tool`) to compile a callable into a `Tool` with a JSON Schema 2020-12 `parameters` contract and a `msgspec` argument validation struct:

```python title="docs/examples/agent/coffee_shop_agent.py"
--8<-- "docs/examples/agent/coffee_shop_agent.py:24:68"
```

### Type-Based ToolContext and Dependency Injection

When `@tool` builds the tool's public JSON Schema (`Tool.parameters`), it inspects parameter type annotations and strips internal context and dependency injection parameters so the LLM only sees domain arguments:

- **`ToolContext` (`litestar_mcp.core.context.ToolContext`)**: Any parameter annotated as `ToolContext` receives the active turn's `ToolContext` instance. You can also access it anywhere inside an executing tool via `current_tool_context()`.
- **`ASGIConnection` / `Request`**: Any parameter annotated as `Request` or `ASGIConnection` receives `ctx.request`.
- **Universal DI (`Provide`, `Dependency`, `FromDishka[T]`)**: Parameters matched by controller/tool `dependencies={"svc": Provide(...)}`, `Annotated[T, Dependency(skip_validation=True)]`, or Dishka `FromDishka[T]` are stripped from `Tool.parameters` (`tool.stripped_parameters`) and resolved inside an `AsyncExitStack` on each `await tool.execute(arguments, context=ctx)` call.
- **Sync and Async Callables**: Async functions are awaited directly; synchronous functions are offloaded via `anyio.to_thread.run_sync`.

`ToolContext` carries the caller's security and session metadata across HTTP requests, A2A tasks, and queued worker turns:

| `ToolContext` Attribute / Method | Description |
| :--- | :--- |
| `user` / `auth` | Authenticated principal (`connection.user`) and auth payload (`connection.auth`). |
| `tenant_id` | Tenant identifier extracted from `scope`, `user`, `auth`, or `connection.state`. |
| `roles` / `scopes` | Tuples of role and scope strings extracted from `user` or `auth`. |
| `session_id` / `turn_id` | Active conversation session ID and turn execution ID. |
| `request` | Originating Litestar connection (or `None` when invoked outside HTTP). |
| `state` | Mutable per-invocation dictionary shared across tools. |
| `container` / `dishka_container` | Active DI container resolved from `request.state` or `app.state`. |
| `ToolContext.from_connection(request)` | Constructs a `ToolContext` populated from a Litestar `Request` or `ASGIConnection`. |
| `owner_key()` | Returns `"<tenant_or_default>:<user_id>"` (checking `id`, `sub`, `username`, `pk`) for authenticated users, or `None` for anonymous callers. |

---

## AgentRuntime & Litestar Plugin Integration

`AgentRuntime` (`litestar_mcp.agent.AgentRuntime`) implements Litestar's `InitPluginProtocol` and manages the multi-turn model invocation loop, parallel tool execution (`execute_tool_calls`), session locking/persistence, history compaction, and OpenTelemetry span emission.

Registering `AgentRuntime` in `Litestar(plugins=[...])` automatically registers the `"runtime"` dependency provider (`Provide(self._provide_runtime, sync_to_thread=False)`) consumed by `AgentChatController` and custom route handlers:

```python title="docs/examples/agent/coffee_shop_agent.py"
--8<-- "docs/examples/agent/coffee_shop_agent.py:120:126"
```

### Constructor Parameters

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `target` | `Agent or AgentGroup` | *(required)* | Single `Agent` or multi-agent `AgentGroup` executed by the runtime. |
| `default_model` | `ModelClient or None` | `None` | Fallback `ModelClient` used when an `Agent.model` is `None`. Defaults to `GoogleGenAIClient()` if unset. |
| `budget` | `TurnBudget or None` | `TurnBudget()` | Per-turn execution limits (`max_turns`, `max_tool_calls`, `timeout_seconds`). |
| `sessions` | `SessionStore or None` | `MemorySessionStore()` | Conversation history store keyed by `(owner, session_id)`. |
| `span_manager` | `SpanManager or None` | `SpanManager()` | OpenTelemetry GenAI span manager for agent turns and tool executions. |
| `compaction_hook` | `CompactionHook or None` | `None` | Optional sync or async `(list[AgentMessage]) -> list[AgentMessage]` callback invoked before each model iteration to summarize or prune history. |
| `stream_buffer` | `int` | `64` | Capacity of the in-memory `anyio` object stream buffering `AgentStreamFrame` events. |

### Execution Methods

 You can invoke `AgentRuntime` directly from Python code, background tasks, or custom Litestar handlers using `TurnRequest`:

- **`await runtime.run_turn(TurnRequest(...)) -> TurnResponse`**: Runs the turn loop to completion and returns a `TurnResponse(session_id=..., turn_id=..., output=..., agent_name=..., messages=..., token_usage=...)`.
- **`runtime.stream(TurnRequest(...)) -> AsyncIterator[AgentStreamFrame]`**: Streams typed `AgentStreamFrame` objects (`session`, `thought`, `delta`, `tool_call`, `tool_result`, `agent_transfer`, `complete`, `error`) as the turn progresses.
- **`async with runtime.open_stream(TurnRequest(...)) as receive_stream:`**: Opens a background `anyio` task group running `runtime.produce(turn, send)` and yields a `MemoryObjectReceiveStream[AgentStreamFrame]`.
- **`await runtime.get_history(context, session_id) -> list[AgentMessage] | None`**: Loads conversation history scoped to `context.owner_key()` (or `ANONYMOUS_OWNER`).

---

## TurnBudget Guardrails

Every turn executed by `AgentRuntime` is bounded by a `TurnBudget` (`litestar_mcp.agent.TurnBudget`) to prevent runaway tool loops, unbounded token consumption, or hung upstream model calls:

| Field | Type | Default | Guardrail Behavior |
| :--- | :--- | :--- | :--- |
| `max_turns` | `int` | `15` | Maximum number of model-invocation iterations within a single turn. If the model continues requesting tool calls after `max_turns` iterations without producing a final assistant response, `AgentRuntime` raises `BudgetExceededError`. |
| `max_tool_calls` | `int` | `50` | Maximum cumulative tool calls across all iterations of a single turn. Exceeding `max_tool_calls` aborts the turn before executing the excess batch and raises `BudgetExceededError`. |
| `timeout_seconds` | `float` | `300.0` | Wall-clock deadline (in seconds) enforced via `anyio.fail_after` across model streaming (`_next_before`) and parallel tool execution (`_execute_tools`). Timeouts are converted into `BudgetExceededError`. |

All three fields must be strictly positive (`> 0`); passing `0` or a negative value raises `ValueError` at `TurnBudget` construction time.

When `BudgetExceededError` (`litestar_mcp.agent.BudgetExceededError`, a subclass of `LitestarMCPError`) is raised:

1. `AgentRuntime.produce` records the error on the active OpenTelemetry span, emits a terminal `AgentStreamFrame(event_type="error", payload={"error": ...})`, and re-raises `BudgetExceededError`.
2. `AgentChatController.create_turn` (`POST /agent/turns`) catches `BudgetExceededError` and translates it into an HTTP `504 Gateway Timeout` response.

---

## ModelClient Protocol & MockModelClient

`AgentRuntime` communicates with LLMs through the runtime-checkable `ModelClient` protocol (`litestar_mcp.agent.ModelClient`):

```python
from collections.abc import AsyncIterator, Sequence
from typing import Protocol
from litestar_mcp.agent import AgentMessage, ModelDelta
from litestar_mcp.core.tools import Tool


class ModelClient(Protocol):
    def stream_turn(
        self,
        *,
        system_instruction: str | None,
        messages: Sequence[AgentMessage],
        tools: Sequence[Tool],
    ) -> AsyncIterator[ModelDelta]: ...
```

Each yielded `ModelDelta` (`litestar_mcp.agent.ModelDelta`) carries an `event_type` (`"delta"`, `"thought"`, `"tool_call"`, or `"usage"`), incremental `text` / `thought`, tool call metadata (`tool_name`, `call_id`, `arguments`, `provider_metadata`), or token counts (`prompt_tokens`, `completion_tokens`, `thought_tokens`).

### Deterministic Testing with MockModelClient

For fast offline unit and integration tests without network calls or API keys, pass a `MockModelClient` (`litestar_mcp.agent.MockModelClient`) pre-loaded with scripted `ModelDelta` sequences per model iteration:

```python
from litestar_mcp.agent import Agent, AgentRuntime, MockModelClient, ModelDelta, TurnRequest
from litestar_mcp.core.tools import tool


@tool
def get_weather(city: str) -> dict[str, str]:
    """Return current weather for a city."""
    return {"city": city, "forecast": "Sunny, 22C"}


mock_model = MockModelClient(
    responses=[
        [
            ModelDelta(
                event_type="tool_call",
                tool_name="get_weather",
                call_id="call_1",
                arguments={"city": "Seattle"},
            )
        ],
        [
            ModelDelta(event_type="thought", text="Formatting weather report for Seattle."),
            ModelDelta(event_type="delta", text="It is Sunny and 22C in Seattle."),
            ModelDelta(event_type="usage", prompt_tokens=24, completion_tokens=10, thought_tokens=6),
        ],
    ]
)

agent = Agent(name="weather_bot", tools=[get_weather], model=mock_model)
runtime = AgentRuntime(agent)
```

`mock_model.call_history` records every `{"system_instruction": ..., "messages": [...], "tools": [...]}` invocation so tests can assert exact prompt assembly and tool schemas.

---

## GoogleGenAIClient

`GoogleGenAIClient` (`litestar_mcp.agent.GoogleGenAIClient`, enabled via `litestar-mcp[google-genai]`) wraps the official `google-genai` SDK with native support for Gemini thinking models, Vertex AI, and shared `httpx2.AsyncClient` connection pooling:

```python title="docs/examples/agent/coffee_shop_agent.py"
--8<-- "docs/examples/agent/coffee_shop_agent.py:94:118"
```

### Configuration & Capabilities

| Parameter | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `model` | `str` | `"gemini-3.8-flash"` | Gemini model identifier passed to `client.aio.models.generate_content_stream`. |
| `api_key` | `str or None` | `None` | Gemini Developer API key (reads `GEMINI_API_KEY` / `GOOGLE_API_KEY` from environment when omitted). |
| `vertexai` | `bool or None` | `None` | Enable Vertex AI endpoint routing (`provider_name` reports `"gcp.vertex_ai"` when `True`, `"gcp.gemini"` otherwise). |
| `project` / `location` | `str or None` | `None` | Google Cloud project ID and region for Vertex AI requests. |
| `thinking_budget` | `int or None` | `None` | Token budget passed to `google.genai.types.ThinkingConfig(thinking_budget=...)`. |
| `thinking_level` | `str or None` | `None` | Reasoning level passed to `ThinkingConfig(thinking_level=...)`. |
| `include_thoughts` | `bool or None` | `None` | Request reasoning traces in candidate parts (`ModelDelta(event_type="thought", ...)`). |
| `temperature` | `float or None` | `None` | Sampling temperature passed to `GenerateContentConfig`. |
| `http_client` | `httpx2.AsyncClient or None` | `None` | Shared `httpx2.AsyncClient` wired into `google.genai.types.HttpOptions(httpx_async_client=...)`. |
| `http_options` | `Any or None` | `None` | Explicit `google.genai.types.HttpOptions` override. |
| `client` | `Any or None` | `None` | Pre-constructed `google.genai.Client` instance. |

### Thought-Signature Preservation Across Function Calls

Gemini thinking models emit an opaque `thought_signature` on `Part(function_call=...)` chunks and require that exact signature to be echoed back in conversation history on subsequent function-response turns. `GoogleGenAIClient` handles this round-trip automatically:

1. When parsing a streaming candidate part containing a `function_call`, `_parse_part_deltas` captures `part.thought_signature` and `function_call.id` inside `ModelDelta.provider_metadata["google"]`.
2. `AgentRuntime` stores `provider_metadata` on the `ToolCall` inside the assistant `AgentMessage` persisted in `SessionStore`.
3. On subsequent iterations and follow-up turns, `_build_contents` reconstructs `types.Part(function_call=types.FunctionCall(id=..., name=..., args=...), thought_signature=google.get("thought_signature"))` alongside the correlated `types.FunctionResponse(id=..., name=..., response=...)`, preserving reasoning continuity across multi-step tool chains.
