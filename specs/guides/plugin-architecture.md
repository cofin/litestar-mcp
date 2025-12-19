# Plugin Architecture Patterns for litestar-mcp

Patterns for building Litestar plugins with MCP integration.

## Overview

litestar-mcp implements both `InitPluginProtocol` and `CLIPlugin`, uses a registry to track MCP metadata, and exposes discovery through dependency injection.

## Core Plugin Pattern

**Pattern**: Implement both protocols and attach an MCP router during app init.

**Example**:
```python
from typing import TYPE_CHECKING
from litestar.di import Provide
from litestar.plugins import CLIPlugin, InitPluginProtocol
from litestar.router import Router

if TYPE_CHECKING:
    from click import Group
    from litestar.config.app import AppConfig

class LitestarMCP(InitPluginProtocol, CLIPlugin):
    """Litestar plugin for Model Context Protocol integration."""

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        self._discover_mcp_routes(app_config.route_handlers)

        router = Router(
            path="/mcp",
            route_handlers=[MCPController],
            include_in_schema=False,
            dependencies={
                "config": Provide(lambda: self._config, sync_to_thread=False),
                "discovered_tools": Provide(self._tools_provider, sync_to_thread=False),
                "discovered_resources": Provide(self._resources_provider, sync_to_thread=False),
            },
        )
        app_config.route_handlers.append(router)
        return app_config

    def on_cli_init(self, cli: "Group") -> None:
        from litestar_mcp.cli import mcp_group
        cli.add_command(mcp_group)
```

**When to use**: For all Litestar plugin development.

## Route Discovery Pattern

**Pattern**: Scan handlers for `mcp_tool`/`mcp_resource` markers and store metadata in a registry.

**Example**:
```python
def _discover_mcp_routes(self, route_handlers: "Sequence[Any]") -> None:
    for handler in route_handlers:
        if isinstance(handler, BaseRouteHandler):
            if not should_include_handler(handler, self._config):
                continue

            pending = getattr(handler, "_mcp_pending", None)
            if not pending:
                pending = getattr(get_handler_function(handler), "_mcp_pending", None)

            if pending:
                self._registry.register(handler, pending["type"], pending["name"], pending.get("description"))
            elif handler.opt:
                if "mcp_tool" in handler.opt:
                    self._registry.register(handler, "tool", handler.opt["mcp_tool"])
                if "mcp_resource" in handler.opt:
                    self._registry.register(handler, "resource", handler.opt["mcp_resource"])

        if getattr(handler, "route_handlers", None):
            self._discover_mcp_routes(handler.route_handlers)
```

**When to use**: During `on_app_init()` to discover MCP-marked routes.

## Runtime Discovery Pattern

**Pattern**: Re-scan runtime handlers on startup to capture controller routes.

**Example**:
```python
async def rescan_runtime_routes(app: Any) -> None:
    runtime_handlers: list[Any] = []
    for route in app.routes:
        runtime_handlers.extend(route.route_handlers or [])
    if runtime_handlers:
        self._registry.rebuild(runtime_handlers)
```

**When to use**: When controller handlers are materialized after app construction.

## Dependency Registration Pattern

**Pattern**: Provide discovery via DI so controllers receive the latest registry results.

**Example**:
```python
def provide_discovered_tools(request: Any) -> dict[str, BaseRouteHandler]:
    self._ensure_runtime_discovery(request.app)
    return self._registry.list_tools()
```

**When to use**: To share plugin state with controllers and handlers.

## Related Patterns

- See AGENTS.md → Plugin Architecture section
- See [CLI Integration](cli-integration.md) for CLI patterns
- See [Testing Patterns](testing-patterns.md) for testing plugin initialization

---

**This guide is automatically updated** by the Docs & Vision agent.
