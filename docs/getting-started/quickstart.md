---
title: 5-Minute Quickstart
---

# 5-Minute Quickstart

This walkthrough attaches `LitestarMCP` to a Litestar application, exposes a typed tool (`outputSchema` and `structuredContent`), a static resource and `SEP-1865` MCP App view (`io.modelcontextprotocol/ui`), and a prompt, inspects the built-in `litestar://openapi` resource and CLI discovery commands (`litestar mcp list-skills`), and probes `/mcp` across both stateless `2026-07-28` (`server/discover`) and session-aware `2025-11-25` (`initialize`, `Mcp-Session-Id`, `GET /mcp` SSE, `resources/subscribe`, and `logging/setLevel`) Streamable HTTP requests.

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

Create `app.py` with a typed tool (`mcp_tool` + `mcp_output_schema=True` synthesizing a JSON Schema 2020-12 `"type": "object"` `outputSchema` and automatic `structuredContent` alongside linked `_meta.ui`), a static custom-scheme resource (`mcp_resource_uri`), a `SEP-1865` `ui://` HTML resource (`text/html;profile=mcp-app`, advertising `io.modelcontextprotocol/ui`), and a prompt (`mcp_prompt`):

```python title="app.py"
from typing import Any

import msgspec
from litestar import Litestar, get, post

from litestar_mcp import LitestarMCP, MCPConfig


class NoteRecord(msgspec.Struct):
    id: str
    title: str
    body: str


@post(
    "/notes",
    mcp_tool="create_note",
    mcp_output_schema=True,
    mcp_tool_meta={"ui": {"resourceUri": "ui://notes/viewer", "visibility": ["model", "app"]}},
)
async def create_note(title: str, body: str) -> NoteRecord:
    """Create a note and return its typed record."""
    return NoteRecord(id="note-1", title=title, body=body)


@get("/config", mcp_resource="app_config", mcp_resource_uri="app://config/runtime")
async def get_runtime_config() -> dict[str, Any]:
    """Return read-only runtime configuration."""
    return {"environment": "development", "version": "1.0.0"}


@get(
    "/widgets/notes",
    media_type="text/html;profile=mcp-app",
    mcp_resource="notes_viewer",
    mcp_resource_uri="ui://notes/viewer",
)
async def get_notes_viewer() -> str:
    """Render an interactive MCP App view for created notes."""
    return "<html><body><h1>Notes Viewer</h1></body></html>"


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
    route_handlers=[create_note, get_runtime_config, get_notes_viewer, summarize_note],
    plugins=[LitestarMCP(MCPConfig(name="Notes Quickstart"))],
)
```

Start the server with the Litestar CLI:

```bash
litestar --app app:app run --reload
```

---

## Probe /mcp with curl (Dual-Mode Streamable HTTP)

`LitestarMCP` supports stateless `2026-07-28` (`server/discover` and SEP-2575 headers) alongside session-aware `2025-11-25`, `2025-06-18`, and `2025-03-26` (`initialize`, `Mcp-Session-Id`, `GET /mcp` SSE, `resources/subscribe`, and `logging/setLevel`) on `/mcp`.

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

=== "MCP 2025-11-25 (initialize, Mcp-Session-Id & GET SSE)"

    Set `SESSION_ID` from the `Mcp-Session-Id` header returned by `initialize`, confirm initialization (`202 Accepted`), open the `GET /mcp` SSE stream in a second terminal, and send `resources/subscribe` and `logging/setLevel` requests:

    ```bash
    curl -siX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "Accept: application/json, text/event-stream" \
      -d '{
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
          "protocolVersion": "2025-11-25",
          "capabilities": {},
          "clientInfo": {"name": "curl-client", "version": "1.0.0"}
        }
      }'

    curl -siX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2025-11-25" \
      -H "Mcp-Session-Id: $SESSION_ID" \
      -d '{"jsonrpc":"2.0","method":"notifications/initialized"}'

    curl -N http://127.0.0.1:8000/mcp \
      -H "Accept: text/event-stream" \
      -H "MCP-Protocol-Version: 2025-11-25" \
      -H "Mcp-Session-Id: $SESSION_ID"

    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2025-11-25" \
      -H "Mcp-Session-Id: $SESSION_ID" \
      -d '{
        "jsonrpc": "2.0",
        "id": 2,
        "method": "resources/subscribe",
        "params": {"uri": "app://config/runtime"}
      }' | jq .

    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2025-11-25" \
      -H "Mcp-Session-Id: $SESSION_ID" \
      -d '{
        "jsonrpc": "2.0",
        "id": 3,
        "method": "logging/setLevel",
        "params": {"level": "debug"}
      }' | jq .
    ```

---

## List & Call Primitives and Read litestar://openapi

=== "List & Call Tools"

    ```bash
    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2026-07-28" \
      -H "Mcp-Method: tools/list" \
      -d '{
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {
          "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}
          }
        }
      }' | jq .

    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2026-07-28" \
      -H "Mcp-Method: tools/call" \
      -H "Mcp-Name: create_note" \
      -d '{
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
          "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}
          },
          "name": "create_note",
          "arguments": {"title": "Architecture", "body": "In-process ASGI dispatch"}
        }
      }' | jq .
    ```

=== "Read Resources & litestar://openapi"

    Every `LitestarMCP` application automatically exposes its OpenAPI schema at the built-in `litestar://openapi` resource URI alongside your custom `app://` and `ui://` resources:

    ```bash
    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2026-07-28" \
      -H "Mcp-Method: resources/read" \
      -H "Mcp-Name: litestar://openapi" \
      -d '{
        "jsonrpc": "2.0",
        "id": 4,
        "method": "resources/read",
        "params": {
          "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}
          },
          "uri": "litestar://openapi"
        }
      }' | jq .
    ```

=== "Get Prompts"

    ```bash
    curl -sX POST http://127.0.0.1:8000/mcp \
      -H "Content-Type: application/json" \
      -H "MCP-Protocol-Version: 2026-07-28" \
      -H "Mcp-Method: prompts/get" \
      -H "Mcp-Name: summarize_note" \
      -d '{
        "jsonrpc": "2.0",
        "id": 5,
        "method": "prompts/get",
        "params": {
          "_meta": {
            "io.modelcontextprotocol/protocolVersion": "2026-07-28",
            "io.modelcontextprotocol/clientCapabilities": {}
          },
          "name": "summarize_note",
          "arguments": {"title": "Architecture", "body": "In-process ASGI dispatch"}
        }
      }' | jq .
    ```

---

## Inspect Primitives, Export Tool Specs, or Launch Over Stdio from the CLI

Use the built-in `litestar mcp` CLI group to list discovered tools, resources, and `SEP-2640` skills, export compiled tool JSON Schemas (`inputSchema`, `outputSchema`, `_meta`), or connect desktop MCP hosts without starting an HTTP server:

```bash
litestar --app app:app mcp list-tools
litestar --app app:app mcp list-resources
litestar --app app:app mcp list-skills
litestar --app app:app mcp export-tool-spec --indent 2
litestar --app app:app mcp stdio
```

---

## Next Steps

- [Standalone MCP Application](standalone.md): Build dedicated MCP servers using the declarative `MCP` wrapper.
- [Core Architectural Concepts](concepts.md): Learn how internal ASGI dispatch, Dishka/Litestar dependency injection, and tag filtering work.
- [MCP Core Guides](../learn/index.md): Configure tool annotations (`mcp_annotations`), `outputSchema` and `structuredContent`, MCP Apps (`io.modelcontextprotocol/ui`), RFC 6570 resource templates, `PromptController`, RFC 5424 server logging, and Agent Skills (`SEP-2640` & `SkillCatalog`).
