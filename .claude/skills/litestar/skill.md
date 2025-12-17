# Litestar Framework Skill

Quick reference for Litestar patterns used in litestar-mcp.

## Context7 Lookup

```python
mcp__context7__resolve-library-id(libraryName="litestar")
# Returns: /litestar/litestar

mcp__context7__get-library-docs(
    context7CompatibleLibraryID="/litestar/litestar",
    topic="plugins",
    mode="code"
)
```

## Plugin Architecture

### InitPluginProtocol

```python
from typing import TYPE_CHECKING

from litestar.plugins import InitPluginProtocol

if TYPE_CHECKING:
    from litestar.config.app import AppConfig

class MyPlugin(InitPluginProtocol):
    """Plugin that modifies app configuration at startup."""

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        """Called during app initialization.

        Args:
            app_config: The application configuration to modify.

        Returns:
            Modified application configuration.
        """
        # Add route handlers
        app_config.route_handlers.append(MyController)

        # Add middleware
        app_config.middleware = list(app_config.middleware or [])
        app_config.middleware.append(my_middleware)

        # Add on_shutdown hooks
        app_config.on_shutdown = list(app_config.on_shutdown or [])
        app_config.on_shutdown.append(cleanup_function)

        return app_config
```

### CLIPlugin

```python
from typing import TYPE_CHECKING

from litestar.plugins import CLIPlugin

if TYPE_CHECKING:
    from click import Group

class MyCLIPlugin(CLIPlugin):
    """Plugin that adds CLI commands."""

    def on_cli_init(self, cli: "Group") -> None:
        """Register CLI commands.

        Args:
            cli: The Click command group to add commands to.
        """
        from my_module.cli import my_command_group

        cli.add_command(my_command_group)
```

### Combined Plugin

```python
from litestar.plugins import CLIPlugin, InitPluginProtocol

class LitestarMCP(InitPluginProtocol, CLIPlugin):
    """Combined plugin with app init and CLI support."""

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        # App initialization
        return app_config

    def on_cli_init(self, cli: "Group") -> None:
        # CLI commands
        pass
```

## Route Handlers

### Basic Routes

```python
from litestar import get, post, put, delete

@get("/items")
async def list_items() -> "list[dict[str, Any]]":
    """List all items."""
    return [{"id": 1, "name": "Item 1"}]

@post("/items")
async def create_item(data: "ItemCreate") -> "Item":
    """Create a new item."""
    return Item(**data)

@get("/items/{item_id:int}")
async def get_item(item_id: int) -> "Item":
    """Get item by ID."""
    return Item(id=item_id, name="Item")
```

### Route Options (opt)

```python
@get("/users", opt={"mcp_tool": "list_users"})
async def get_users() -> "list[dict[str, Any]]":
    """Route with MCP tool annotation."""
    pass

@get("/config", opt={"mcp_resource": "app_config"})
async def get_config() -> "dict[str, Any]":
    """Route with MCP resource annotation."""
    pass
```

## Dependency Injection

```python
from litestar.di import Provide

def provide_config() -> "MCPConfig":
    """Provide configuration instance."""
    return MCPConfig()

# In router/controller
router = Router(
    path="/api",
    route_handlers=[...],
    dependencies={
        "config": Provide(provide_config, sync_to_thread=False)
    }
)

# In handler
@get("/endpoint")
async def handler(config: "MCPConfig") -> "dict[str, Any]":
    """Handler with injected dependency."""
    return {"base_path": config.base_path}
```

## Controllers

```python
from litestar import Controller, get, post

class MCPController(Controller):
    """MCP REST API controller."""

    path = "/mcp"
    tags = ["mcp"]

    @get("/")
    async def get_server_info(self) -> "dict[str, Any]":
        """Get server information."""
        return {"name": "litestar-mcp", "version": "1.0.0"}

    @get("/tools")
    async def list_tools(
        self,
        discovered_tools: "dict[str, BaseRouteHandler]",
    ) -> "list[MCPTool]":
        """List available MCP tools."""
        return [...]
```

## Exception Handling

```python
from litestar.exceptions import (
    HTTPException,
    ImproperlyConfiguredException,
    NotFoundException,
    ValidationException,
)

class MCPConfigError(ImproperlyConfiguredException):
    """Raised when MCP configuration is invalid."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"MCP configuration error: {detail}")

# Usage
if not config.name:
    raise MCPConfigError("Server name is required")
```

## JSON Serialization

```python
from litestar.serialization import encode_json, decode_json

# Serialize to bytes
data = {"key": "value"}
json_bytes = encode_json(data)

# Deserialize from bytes
result = decode_json(json_bytes)
```

## Testing

```python
from litestar.testing import TestClient

def test_endpoint() -> None:
    """Test endpoint with TestClient."""
    app = Litestar(route_handlers=[my_handler])

    with TestClient(app) as client:
        response = client.get("/endpoint")
        assert response.status_code == 200
        assert response.json() == {"expected": "data"}
```

## Project-Specific Files

Key litestar-mcp implementations:

- `litestar_mcp/plugin.py` - Main LitestarMCP plugin
- `litestar_mcp/routes.py` - MCPController
- `litestar_mcp/config.py` - MCPConfig dataclass
- `litestar_mcp/registry.py` - MCPToolRegistry
- `litestar_mcp/executor.py` - Tool execution
- `litestar_mcp/cli.py` - CLI commands
