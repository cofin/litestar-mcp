---
title: 5-Minute Quickstart
---

# 5-Minute Quickstart

This walkthrough attaches `LitestarMCP` to a Litestar application, exposes a tool, a resource, and a prompt, inspects the built-in `litestar://openapi` resource, and tests the `/mcp` endpoint using both `2026-07-28` (`server/discover`) and `2025-06-18` (`initialize`) Streamable HTTP requests.

---

## Install litestar-mcp and an ASGI Server

=== "uv"

    ```bash
    uv add litestar-mcp uvicorn
    ```

=== "pip"

    ```bash
    pip install litestar-mcp uvicorn
    ```

=== "pdm"

    ```bash
    pdm add litestar-mcp uvicorn
    ```

=== "poetry"

    ```bash
    poetry add litestar-mcp uvicorn
    ```

---

## Register LitestarMCP and Mark Routes

Every Litestar route decorator (`@get`, `@post`, `@put`, `@patch`, `@delete`) forwards extra keyword arguments into `handler.opt`. Passing `mcp_tool`, `mcp_resource`, or `mcp_prompt` exposes the route over `/mcp` while keeping its normal REST endpoint active.

Minimal plugin registration requires a single entry in `plugins`:

```python title="docs/examples/snippets/configuration_minimal.py"
--8<-- "docs/examples/snippets/configuration_minimal.py:example"
```

Create `app.py` with a tool (`mcp_tool`), a resource (`mcp_resource`), and a prompt (`mcp_prompt`):

```python title="app.py"
from typing import Any

from litestar import Litestar, get, post

from litestar_mcp import LitestarMCP, MCPConfig


@post("/notes", mcp_tool="create_note")
async def create_note(title: str, body: str) -> dict[str, str]:
    """Create a note and return its record."""
    return {"id": "note-1", "title": title, "body": body}


@get("/config", mcp_resource="app_config", mcp_resource_uri="app://config/runtime")
async def get_runtime_config() -> dict[str, Any]:
    """Return read-only runtime configuration."""
    return {"environment": "development", "version": "1.0.0"}


@post("/prompts/summarize", mcp_prompt="summarize_note")
async def summarize_note(title: str, body: str) -> list[dict[str, str]]:
    """Build a summarization prompt for a note."""
    return [
        {
            "role": "user",
            "content": f"Summarize the note titled '{title}':\n\n{body}",
        }
    ]


app = Litestar(
    route_handlers=[create_note, get_runtime_config, summarize_note],
    plugins=[LitestarMCP(MCPConfig(name="Notes Quickstart"))],
)
```

Start the server with the Litestar CLI:

```bash
litestar --app app:app run --reload
```

---

## Probe /mcp with curl (Dual-Version Streamable HTTP)

`LitestarMCP` supports both the `2026-07-28` stateless `server/discover` flow and the `2025-06-18` `initialize` lifecycle on `/mcp`.

=== "MCP 2026-07-28 (server/discover)"

    ```bash
    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2026-07-28" \
      -H "Mcp-Method: server/discover" \
      -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "server/discover",
        "params": {
          "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}
          }
        }
      }' | jq .
    ```

=== "MCP 2025-06-18 (initialize)"

    ```bash
    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2025-06-18" \
      -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
          "protocolVersion": "2025-06-18",
          "capabilities": {},
          "clientInfo": {"name": "curl-client", "version": "1.0.0"}
        }
      }' | jq .
    ```

---

## List & Call Primitives and Read litestar://openapi

=== "List & Call Tools"

    ```bash
    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2026-07-28" \
      -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' | jq .

    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2026-07-28" \
      -d '{
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
          "name": "create_note",
          "arguments": {"title": "Architecture", "body": "In-process ASGI dispatch"}
        }
      }' | jq .
    ```

=== "Read Resources & litestar://openapi"

    Every `LitestarMCP` application automatically exposes its OpenAPI schema at the built-in `litestar://openapi` resource URI alongside your custom resources:

    ```bash
    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2026-07-28" \
      -d '{
        "jsonrpc": "2.0",
        "id": 4,
        "method": "resources/read",
        "params": {"uri": "litestar://openapi"}
      }' | jq .
    ```

=== "Get Prompts"

    ```bash
    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2026-07-28" \
      -d '{
        "jsonrpc": "2.0",
        "id": 5,
        "method": "prompts/get",
        "params": {
          "name": "summarize_note",
          "arguments": {"title": "Architecture", "body": "In-process ASGI dispatch"}
        }
      }' | jq .
    ```

---

## Export Tool Specs or Launch Over Stdio from the CLI

Use the built-in `litestar mcp` CLI group to inspect compiled tool JSON Schemas or connect desktop MCP hosts without starting an HTTP server:

```bash
litestar --app app:app mcp export-tool-spec --indent 2
litestar --app app:app mcp stdio
```

---

## Next Steps

- [Standalone MCP Application](standalone.md): Build dedicated MCP servers using the declarative `MCP` wrapper.
- [Core Architectural Concepts](concepts.md): Learn how internal ASGI dispatch, Dishka/Litestar dependency injection, and tag filtering work.
- [MCP Core Guides](../learn/index.md): Configure tool annotations (`mcp_annotations`), RFC 6570 resource templates, `PromptController`, and Agent Skills (`SEP-2640`).
