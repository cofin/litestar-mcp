---
title: Installation & Extras
---

# Installation & Extras

`litestar-mcp` targets Python `3.10+` and Litestar `2.15+`. The base package has zero heavy optional runtime dependencies so you only install the protocol adapters and queue backends your service uses.

---

## Base Package (`litestar-mcp`)

The base package includes `LitestarMCP`, the standalone `MCP` application wrapper, dual-version Streamable HTTP (`2026-07-28` and `2025-06-18`), stdio & HTTP bridge CLI subcommands, Agent Skills (`SEP-2640`), in-process `MCPTaskStore` / `InProcessTaskExecutor`, `Agent` / `AgentGroup` / `AgentRuntime`, and `DynamicWorkflow` / `WorkflowEngine`.

=== "uv"

    ```bash
    uv add litestar-mcp
    ```

=== "pip"

    ```bash
    pip install litestar-mcp
    ```

=== "pdm"

    ```bash
    pdm add litestar-mcp
    ```

=== "poetry"

    ```bash
    poetry add litestar-mcp
    ```

---

## Optional Extras Matrix

| Extra | Package Specifier | Unlocked Modules & Classes | Underlying Dependencies |
| :--- | :--- | :--- | :--- |
| **`queues`** | `litestar-mcp[queues]` | `litestar_mcp.queues` (`QueueTaskExecutor`, `DurableWorkflowEngine`, `WorkflowStore`, `TaskDependencyChain`, `QueuedAgentChatController`, `QueuedTurnManager`, `PrincipalSnapshot`) | `litestar-queues>=0.2.0` |
| **`a2a`** | `litestar-mcp[a2a]` | `litestar_mcp.a2a` (`LitestarA2A`, `A2AConfig`) and `agent_to_a2a` bridge | `a2a-sdk>=1.0.0a1` |
| **`google-genai`** | `litestar-mcp[google-genai]` | `GoogleGenAIClient` (default model `gemini-3.8-flash` with Gemini 3/2.5 thought-signature preservation) | `google-genai>=1.0.0` |
| **`opentelemetry`** | `litestar-mcp[opentelemetry]` | OpenTelemetry span and metric emission via `TelemetryConfig` (`litestar_mcp.core.telemetry`) | `opentelemetry-api>=1.20.0` |
| **`all`** | `litestar-mcp[all]` | All optional integrations (`queues`, `a2a`, `google-genai`, `opentelemetry`) | Union of all extras above |

---

## Installing Individual Extras

### Distributed Tasks & Durable Workflows (`queues`)

Install `litestar-mcp[queues]` to run MCP Tasks (`QueueTaskExecutor`), durable DAG workflows (`DurableWorkflowEngine`), and queued agent turns (`QueuedAgentChatController`) on `litestar-queues`:

=== "uv"

    ```bash
    uv add "litestar-mcp[queues]"
    ```

=== "pip"

    ```bash
    pip install "litestar-mcp[queues]"
    ```

=== "pdm"

    ```bash
    pdm add "litestar-mcp[queues]"
    ```

=== "poetry"

    ```bash
    poetry add "litestar-mcp[queues]"
    ```

### Official A2A 1.0 Interoperability (`a2a`)

Install `litestar-mcp[a2a]` to expose `LitestarA2A` with dual JSON-RPC 2.0 and `HTTP+JSON` REST bindings and `agent_to_a2a`:

=== "uv"

    ```bash
    uv add "litestar-mcp[a2a]"
    ```

=== "pip"

    ```bash
    pip install "litestar-mcp[a2a]"
    ```

=== "pdm"

    ```bash
    pdm add "litestar-mcp[a2a]"
    ```

=== "poetry"

    ```bash
    poetry add "litestar-mcp[a2a]"
    ```

### Google GenAI Model Client (`google-genai`)

Install `litestar-mcp[google-genai]` to use `GoogleGenAIClient` with `Agent` and `AgentRuntime`:

=== "uv"

    ```bash
    uv add "litestar-mcp[google-genai]"
    ```

=== "pip"

    ```bash
    pip install "litestar-mcp[google-genai]"
    ```

=== "pdm"

    ```bash
    pdm add "litestar-mcp[google-genai]"
    ```

=== "poetry"

    ```bash
    poetry add "litestar-mcp[google-genai]"
    ```

### OpenTelemetry Tracing (`opentelemetry`)

Install `litestar-mcp[opentelemetry]` to emit OpenTelemetry spans for MCP JSON-RPC requests, tool calls, and agent turns:

=== "uv"

    ```bash
    uv add "litestar-mcp[opentelemetry]"
    ```

=== "pip"

    ```bash
    pip install "litestar-mcp[opentelemetry]"
    ```

=== "pdm"

    ```bash
    pdm add "litestar-mcp[opentelemetry]"
    ```

=== "poetry"

    ```bash
    poetry add "litestar-mcp[opentelemetry]"
    ```

### All Extras (`all`) and ASGI Server

For full-featured development alongside Uvicorn:

=== "uv"

    ```bash
    uv add "litestar-mcp[all]" uvicorn
    ```

=== "pip"

    ```bash
    pip install "litestar-mcp[all]" uvicorn
    ```

=== "pdm"

    ```bash
    pdm add "litestar-mcp[all]" uvicorn
    ```

=== "poetry"

    ```bash
    poetry add "litestar-mcp[all]" uvicorn
    ```
