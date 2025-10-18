# Plugin Architecture Patterns for litestar-mcp

Patterns for building Litestar plugins with MCP integration.

## Overview

litestar-mcp implements both `InitPluginProtocol` for app initialization and `CLIPlugin` for command-line integration. This guide covers the essential patterns for plugin development.

## Core Plugin Pattern

**Pattern**: Implement both protocols for complete Litestar integration.

**Example**:
```python
from typing import TYPE_CHECKING
from litestar.plugins import CLIPlugin, InitPluginProtocol

if TYPE_CHECKING:
    from litestar.config.app import AppConfig
    from click import Group

class LitestarMCP(InitPluginProtocol, CLIPlugin):
    """Litestar plugin for Model Context Protocol integration."""

    def __init__(self, config: "MCPConfig") -> None:
        """Initialize plugin.

        Args:
            config: MCP plugin configuration.
        """
        self.config = config
        self.discovered_tools: "dict[str, BaseRouteHandler]" = {}
        self.discovered_resources: "dict[str, BaseRouteHandler]" = {}

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        """Initialize plugin during app startup.

        Args:
            app_config: The application configuration.

        Returns:
            Modified application configuration.
        """
        # Discover MCP routes
        self._discover_mcp_routes(app_config)

        # Register MCP controller
        app_config.route_handlers.append(MCPController)

        # Register dependencies
        app_config.dependencies.update({
            "discovered_tools": lambda: self.discovered_tools,
            "discovered_resources": lambda: self.discovered_resources,
        })

        return app_config

    def on_cli_init(self, cli: "Group") -> None:
        """Register CLI commands.

        Args:
            cli: The Click command group.
        """
        from litestar_mcp.cli import mcp_group
        cli.add_command(mcp_group)
```

**When to use**: For all Litestar plugin development.

## Route Discovery Pattern

**Pattern**: Scan route handlers for MCP markers during initialization.

**Example**:
```python
def _discover_mcp_routes(self, app_config: "AppConfig") -> None:
    """Discover routes marked for MCP exposure."""
    for handler in app_config.route_handlers:
        # Check opt dict
        if hasattr(handler, "opt") and handler.opt:
            if "mcp_tool" in handler.opt:
                tool_name = handler.opt["mcp_tool"]
                self.discovered_tools[tool_name] = handler

            if "mcp_resource" in handler.opt:
                resource_name = handler.opt["mcp_resource"]
                self.discovered_resources[resource_name] = handler

        # Check decorator metadata
        if hasattr(handler, "_mcp_metadata"):
            metadata = handler._mcp_metadata
            if metadata["type"] == "tool":
                self.discovered_tools[metadata["name"]] = handler
            elif metadata["type"] == "resource":
                self.discovered_resources[metadata["name"]] = handler
```

**When to use**: During `on_app_init()` to discover MCP-marked routes.

## Dependency Registration Pattern

**Pattern**: Register discovered routes as dependencies for controllers.

**Example**:
```python
app_config.dependencies.update({
    "discovered_tools": lambda: self.discovered_tools,
    "discovered_resources": lambda: self.discovered_resources,
})

# In controller
class MCPController(Controller):
    @get("/tools")
    async def list_tools(
        self,
        discovered_tools: "dict[str, BaseRouteHandler]",  # Injected
    ) -> "list[MCPTool]":
        """List all tools."""
        return [MCPTool(name=name) for name in discovered_tools]
```

**When to use**: To share plugin state with controllers and handlers.

## Related Patterns

- See AGENTS.md → Plugin Architecture section
- See [CLI Integration](cli-integration.md) for CLI patterns
- See [Testing Patterns](testing-patterns.md) for testing plugin initialization

---

**This guide is automatically updated** by the Docs & Vision agent.
