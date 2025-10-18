# Litestar-MCP Agent Coordination Guide

Comprehensive guide for the litestar-mcp agent system, covering agent responsibilities, workflow patterns, code quality standards, MCP protocol integration, and development practices.

## Project Overview

**Project**: litestar-mcp - Litestar plugin for Model Context Protocol (MCP) integration
**Purpose**: Enable AI models to interact with Litestar applications via REST API and CLI
**Primary Language**: Python 3.9-3.13
**Framework**: Litestar 2.0+ (modern ASGI framework with advanced plugin system)
**Test Framework**: pytest with asyncio support, 85% coverage minimum
**Build Tool**: uv (modern Python package manager)
**Documentation**: Sphinx with .rst files in docs/

## Agent Responsibilities Matrix

| Responsibility | Planner | Expert | Testing | Docs & Vision |
|----------------|---------|--------|---------|---------------|
| **Research** | ✅ Primary | ✅ Implementation | ✅ Test patterns | ✅ Doc standards |
| **Planning** | ✅ Primary | ❌ | ❌ | ❌ |
| **Implementation** | ❌ | ✅ Primary | ✅ Tests only | ❌ |
| **Testing** | ❌ | ✅ Self-test | ✅ Primary | ✅ QA gate |
| **Documentation** | ✅ PRD/tasks | ✅ Code comments | ✅ Test docs | ✅ Primary |
| **Quality Gate** | ❌ | ❌ | ❌ | ✅ Primary |
| **Knowledge Capture** | ❌ | ❌ | ❌ | ✅ Primary |
| **Re-validation** | ❌ | ❌ | ❌ | ✅ Primary |
| **Cleanup & Archive** | ❌ | ❌ | ❌ | ✅ MANDATORY |
| **Workspace Mgmt** | ✅ Create | ✅ Update | ✅ Update | ✅ Archive |

## Workflow Phases

### Phase 1: Planning (`/plan`)

**Agent:** Planner
**Purpose:** Research-grounded planning and workspace creation

**Steps:**

1. Research MCP protocol, Litestar patterns (Context7, WebSearch)
2. Create structured plan with zen.planner
3. Create workspace in `specs/active/{requirement-slug}/`
4. Write PRD, tasks, recovery docs, README

**Output:**

```
specs/active/{requirement-slug}/
├── prd.md          # Product Requirements Document
├── tasks.md        # 7-phase implementation checklist
├── recovery.md     # Session resume guide
├── README.md       # Workspace overview
├── research/       # Research findings
└── tmp/            # Temporary files
```

**Hand off to:** Expert agent for implementation

### Phase 2: Implementation (`/implement`)

**Agent:** Expert (orchestrates full automated workflow)
**Purpose:** Write code and coordinate testing, docs, and archival

**Steps:**

1. Read workspace (prd.md, tasks.md, AGENTS.md)
2. Research implementation details (Context7, codebase patterns)
3. Implement following AGENTS.md standards (stringified types, no `|` syntax)
4. Self-test during development
5. Update workspace (tasks.md, recovery.md)
6. **AUTO-INVOKE Testing agent** (creates comprehensive test suite)
7. **AUTO-INVOKE Docs & Vision agent** (docs, QA, knowledge capture, archive)

**Tools Used:**

- zen.debug (systematic debugging)
- zen.thinkdeep (complex architectural decisions)
- zen.analyze (code quality analysis)
- Context7 (latest Litestar/MCP documentation)
- Task (invoke Testing and Docs & Vision agents)

**Output:**

- Production code in litestar_mcp/
- Comprehensive test suite (via Testing agent)
- Updated documentation (via Docs & Vision agent)
- Captured patterns in AGENTS.md (via Docs & Vision agent)
- Archived spec (via Docs & Vision agent)

**Hand off to:** None - workflow complete after Docs & Vision returns

### Phase 3: Testing (Auto-invoked by Expert)

**Agent:** Testing (invoked as subagent by Expert)
**Purpose:** Create comprehensive test suite

**Steps:**

1. Read implementation details from recovery.md
2. Consult AGENTS.md for testing patterns
3. Create unit tests (class-based, async support)
4. Create integration tests (real Litestar app)
5. Test edge cases (empty, errors, boundaries, async, CLI)
6. Verify coverage ≥ 85%
7. All tests must pass before returning
8. Update workspace

**Output:**

- Unit tests in tests/test_{feature}.py
- Integration tests
- Coverage report
- Return control to Expert with test report

### Phase 4: Documentation & Archive (Auto-invoked by Expert)

**Agent:** Docs & Vision (invoked as subagent by Expert)
**Purpose:** Documentation, quality gate, knowledge capture, archive

**Steps:**

1. **Quality Gate Validation**:
   - Verify all PRD acceptance criteria met
   - BLOCKS if any criterion not met

2. **Documentation**:
   - Update Sphinx API reference (docs/reference/)
   - Update usage guides (docs/usage/)
   - Validate code examples work
   - Build docs without errors

3. **Knowledge Capture** (CRITICAL):
   - Analyze implementation for new patterns
   - Extract error handling, testing, MCP integration patterns
   - **Update AGENTS.md** with new patterns and examples
   - **Update specs/guides/** with detailed guides

4. **Re-validation** (CRITICAL):
   - Re-run all tests after updates
   - Rebuild documentation
   - Verify pattern consistency
   - BLOCKS if re-validation fails

5. **Cleanup & Archive**:
   - Clean tmp/ directories
   - Move specs/active/{requirement} to specs/archive/
   - Generate completion report

**Output:**

- Complete Sphinx documentation
- Updated AGENTS.md with new patterns
- Updated specs/guides/ with examples
- Archived spec in specs/archive/
- Completion report

## **MANDATORY** Code Quality Standards (TOP PRIORITY)

### Type Annotation Standards (STRICT ENFORCEMENT)

**PROHIBITED**:

- `from __future__ import annotations`
- Union syntax with `|`: `str | None`, `int | float`

**MANDATORY**:

- Stringified type hints for non-builtins: `"SQLConfig"`, `"MCPTool"`, `"Optional[str]"`
- Use `typing.Optional[T]` instead of `T | None`
- Use `typing.Union[A, B]` instead of `A | B`
- Built-in generics stringified: `"list[str]"`, `"dict[str, int]"`
- Tuple `__all__` definitions: `__all__ = ("MyClass", "my_function")`

**Example**:

```python
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar_mcp.config import MCPConfig

def process_config(
    config: "Optional[MCPConfig]",
    app: "Litestar",
) -> "dict[str, Union[str, int]]":
    """Process MCP configuration.

    Args:
        config: Optional MCP configuration.
        app: Litestar application instance.

    Returns:
        Processed configuration dictionary.
    """
    pass
```

### Other Code Standards

- **Comments**: Only in docstrings, NEVER inline
- **Nested imports**: Only when required to prevent import errors
- **JSON serialization**: Use Litestar's `encode_json()`/`decode_json()`, NOT standard `json`
- **Error handling**: Inherit from Litestar exceptions

**Error Handling Pattern**:

```python
from litestar.exceptions import ImproperlyConfiguredException

class NotCallableInCLIContextError(ImproperlyConfiguredException):
    """Raised when a tool cannot be called from CLI context."""

    def __init__(self, handler_name: str, parameter_name: str) -> None:
        """Initialize error.

        Args:
            handler_name: Name of the handler that cannot be called.
            parameter_name: Name of the parameter causing the issue.
        """
        super().__init__(
            f"Tool '{handler_name}' cannot be called from the CLI because it depends on "
            f"the request-scoped dependency '{parameter_name}', which is not available in a CLI context."
        )
```

## Project Architecture

### Core MCP Plugin Architecture

The litestar-mcp plugin integrates the Model Context Protocol into Litestar applications through:

1. **Route Discovery**: At app initialization, scans all route handlers for the `opt` attribute or `_mcp_metadata`
2. **MCP Route Marking**: Routes marked with `mcp_tool="name"` or `mcp_resource="name"` are discovered
3. **REST Endpoint Exposure**: Adds MCP-compatible REST endpoints under `/mcp` (configurable)
4. **CLI Integration**: Provides `litestar mcp` commands for local tool execution
5. **Protocol Compliance**: Exposes tools and resources following MCP specification

### Key Files Structure

- **litestar_mcp/plugin.py** - Main plugin class (`LitestarMCP`) implementing `InitPluginProtocol` and `CLIPlugin`
- **litestar_mcp/routes.py** - MCP REST API controller (`MCPController`) with all endpoints
- **litestar_mcp/config.py** - Configuration dataclass (`MCPConfig`)
- **litestar_mcp/schema.py** - MCP protocol schemas (`MCPResource`, `MCPTool`, `ServerCapabilities`)
- **litestar_mcp/executor.py** - Core execution engine for invoking route handlers
- **litestar_mcp/cli.py** - CLI commands for local tool execution
- **litestar_mcp/schema_builder.py** - Automatic JSON Schema generation
- **litestar_mcp/decorators.py** - `@mcp_tool` and `@mcp_resource` decorators
- **tests/test_plugin.py** - Main plugin tests

### MCP Endpoints Exposed

- `GET /mcp/` - Server info and capabilities
- `GET /mcp/resources` - List available resources
- `GET /mcp/resources/{name}` - Get specific resource content
- `GET /mcp/tools` - List available tools
- `POST /mcp/tools/{name}` - Execute a tool

### CLI Commands

- `litestar mcp list-tools` - List all discovered tools
- `litestar mcp list-resources` - List all discovered resources
- `litestar mcp run <tool> [--args]` - Execute a tool locally

## litestar-mcp Specific Patterns

### Plugin Architecture

**Litestar Plugin Pattern**:

```python
from litestar.plugins import CLIPlugin, InitPluginProtocol
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litestar.config.app import AppConfig
    from click import Group

class LitestarMCP(InitPluginProtocol, CLIPlugin):
    """Litestar plugin for Model Context Protocol integration."""

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        """Initialize plugin during app startup.

        Args:
            app_config: The application configuration.

        Returns:
            Modified application configuration.
        """
        # Register routes, middleware, dependencies
        app_config.route_handlers.append(MCPController)
        return app_config

    def on_cli_init(self, cli: "Group") -> None:
        """Register CLI commands.

        Args:
            cli: The Click command group.
        """
        from litestar_mcp.cli import mcp_group
        cli.add_command(mcp_group)
```

### Route Marking Patterns

**Option 1: Using opt dict** (backward compatible):

```python
from litestar import get

@get("/users", opt={"mcp_tool": "list_users"})
async def get_users() -> "list[dict]":
    """List all users."""
    return [{"id": 1, "name": "Alice"}]

@get("/config", opt={"mcp_resource": "app_config"})
async def get_config() -> dict:
    """Get application configuration."""
    return {"debug": True}
```

**Option 2: Using decorators** (preferred):

```python
from litestar import get
from litestar_mcp import mcp_tool, mcp_resource

@mcp_tool(name="list_users")
@get("/users")
async def get_users() -> "list[dict]":
    """List all users."""
    return [{"id": 1, "name": "Alice"}]

@mcp_resource(name="app_config")
@get("/config")
async def get_config() -> dict:
    """Get application configuration."""
    return {"debug": True}
```

### CLI Integration Pattern

**LitestarGroup and Plugin Retrieval**:

```python
from litestar.cli._utils import LitestarGroup
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar_mcp.plugin import LitestarMCP

def get_mcp_plugin(app: "Litestar") -> "LitestarMCP":
    """Retrieve the MCP plugin from the application.

    Args:
        app: The Litestar application.

    Returns:
        The MCP plugin instance.

    Raises:
        RuntimeError: If the MCP plugin is not found.
    """
    from contextlib import suppress
    from litestar_mcp.plugin import LitestarMCP

    with suppress(KeyError):
        return app.plugins.get(LitestarMCP)
    raise RuntimeError("MCP plugin not found. Ensure LitestarMCP is registered in app.plugins")

@click.group(cls=LitestarGroup, name="mcp")
def mcp_group(ctx: "click.Context") -> None:
    """MCP commands."""
    plugin = get_mcp_plugin(ctx.obj.app)
    ctx.obj = {"app": ctx.obj, "plugin": plugin}
```

### Dependency Injection Pattern

**CLI Context Limitations**:

```python
from litestar_mcp import execute_tool

# Tools requiring request-scoped dependencies will fail in CLI context
unsupported_cli_deps = {"request", "socket", "headers", "cookies", "query", "body"}

# When executing from CLI, only app-scoped dependencies are available
async def execute_tool(handler: BaseRouteHandler, app: Litestar, tool_args: "dict[str, Any]") -> Any:
    """Execute a tool with dependency injection."""
    # Check for unsupported dependencies
    for dep_name in handler.resolve_dependencies():
        if dep_name in unsupported_cli_deps:
            raise NotCallableInCLIContextError(handler.name, dep_name)

    # Execute handler with available dependencies
    # ...
```

### Schema Generation Pattern

**Automatic JSON Schema from Function Signatures**:

```python
from litestar_mcp.schema_builder import generate_schema_for_handler

def generate_schema_for_handler(handler: BaseRouteHandler) -> "dict[str, Any]":
    """Generate JSON Schema for a handler's parameters.

    Args:
        handler: The route handler to generate schema for.

    Returns:
        JSON Schema dictionary.
    """
    sig = inspect.signature(handler.fn.value)
    properties = {}
    required = []

    for param_name, param in sig.parameters.items():
        # Skip DI parameters
        if param_name in handler.resolve_dependencies():
            continue

        # Generate schema for parameter type
        param_schema = type_to_json_schema(param.annotation)
        properties[param_name] = param_schema

        # Track required parameters
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    return {
        "type": "object",
        "properties": properties,
        "required": required,
    }
```

## Testing Patterns

### Class-Based Test Organization

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litestar import Litestar
    from litestar.testing import TestClient

class TestMCPPlugin:
    """Tests for MCP plugin functionality."""

    def test_plugin_initialization(self) -> None:
        """Test plugin initializes correctly."""
        plugin = LitestarMCP(config=MCPConfig())
        assert plugin.config.base_path == "/mcp"

    async def test_tool_execution(self, test_client: "TestClient") -> None:
        """Test MCP tool execution via REST API."""
        response = test_client.post(
            "/mcp/tools/my_tool",
            json={"arguments": {"name": "test"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert "content" in data
```

### Fixtures Pattern

```python
import pytest
from litestar import Litestar, get
from litestar.testing import TestClient
from litestar_mcp import LitestarMCP, MCPConfig

@pytest.fixture
def mcp_app() -> Litestar:
    """Create test Litestar app with MCP plugin."""
    @get("/test", opt={"mcp_tool": "test_tool"})
    async def test_handler(name: str) -> dict:
        """Test handler."""
        return {"message": f"Hello, {name}"}

    return Litestar(
        route_handlers=[test_handler],
        plugins=[LitestarMCP(config=MCPConfig())],
    )

@pytest.fixture
def test_client(mcp_app: Litestar) -> TestClient:
    """Create test client."""
    return TestClient(app=mcp_app)
```

### Async Test Pattern

```python
async def test_async_tool_execution(test_client: "TestClient") -> None:
    """Test async tool execution."""
    response = test_client.post("/mcp/tools/async_tool", json={})
    assert response.status_code == 200
```

### Edge Case Testing Pattern

```python
async def test_empty_tools_list(test_client: "TestClient") -> None:
    """Test handling of empty tools list."""
    response = test_client.get("/mcp/tools")
    assert response.json()["tools"] == []

async def test_invalid_tool_name(test_client: "TestClient") -> None:
    """Test error handling for invalid tool."""
    response = test_client.post("/mcp/tools/nonexistent", json={})
    assert response.status_code == 404

async def test_cli_context_error() -> None:
    """Test CLI context limitation error."""
    from litestar_mcp.executor import NotCallableInCLIContextError

    with pytest.raises(NotCallableInCLIContextError):
        await execute_tool(handler_with_request_dep, app, {})
```

## Common Development Commands

### Environment Setup

- `make install` - Fresh installation with dependencies and pre-commit hooks
- `make destroy` - Remove virtual environment completely
- `make upgrade` - Update all dependencies and pre-commit hooks

### Development Workflow

- `make dev-setup` - Complete development environment setup (install + lint + test)
- `make quick-test` - Run fast tests without coverage
- `make test` - Run full test suite with pytest
- `make coverage` - Run tests with coverage report (85% threshold)

### Code Quality

- `make lint` - Run all linting (ruff, mypy, pyright, slotscheck)
- `make ruff-check` - Run ruff linting only
- `make ruff-format` - Format code with ruff
- `make type-check` - Run mypy and pyright type checking
- `make pre-commit` - Run all pre-commit hooks

### Testing

- `make test-all` - Run complete test suite
- `make mcp-test` - Run MCP-specific protocol tests
- `make integration-test` - Run integration tests
- `make watch-test` - Run tests in watch mode

### Single Test Commands

- `uv run pytest tests/test_plugin.py::TestLitestarMCP::test_plugin_discovers_mcp_routes -v` - Run single test method
- `uv run pytest tests/test_plugin.py -k "mcp" -v` - Run tests matching pattern
- `uv run pytest tests/test_config.py -v` - Run all tests in specific file
- `uv run pytest tests/ --cov=litestar_mcp --cov-report=term-missing` - Run with coverage

### Documentation

- `make docs` - Build documentation with Sphinx
- `make docs-serve` - Serve docs locally at <http://localhost:8002>
- `make docs-linkcheck` - Check documentation links

### Building and Packaging

- `make build` - Build the package
- `make clean` - Clean temporary build artifacts

## Development Dependencies

- **uv**: Package management and virtual environments
- **ruff**: Linting and formatting (replaces black, isort, flake8)
- **mypy + pyright**: Type checking with strict mode
- **pytest**: Testing with coverage, asyncio, and parallel execution
- **sphinx**: Documentation generation
- **pre-commit**: Git hooks for code quality
- **click**: CLI framework
- **rich**: Rich console output

## Implementation Notes

- Uses Litestar's optimized `encode_json`/`decode_json` functions instead of standard `json` module
- Follows Litestar patterns for consistent serialization behavior
- Leverages Litestar's built-in debug capabilities rather than custom debug modes
- CLI bypasses HTTP security (intended for trusted environments only)
- Schema generation is validation-library agnostic with enhanced Pydantic/msgspec support

## Knowledge Capture System

This file (AGENTS.md) is automatically updated by the Docs & Vision agent after each feature implementation. New patterns discovered during implementation are:

1. Extracted from the implementation
2. Documented here with working examples
3. Cross-referenced in specs/guides/
4. Re-validated for consistency

This ensures the project's collective knowledge grows with each feature, making future development faster and more consistent.

## Project-Specific Patterns (Auto-Updated)

This section will be populated by the Docs & Vision agent as new patterns are discovered during feature implementation. Each pattern will include:

- Pattern name and category
- When and why to use it
- Working code example
- Related patterns and guides

---

**Last Updated**: 2025-10-18 (Workflow system installation)
**Coverage Target**: 85% minimum
**Python Versions**: 3.9-3.13
**Litestar Version**: 2.0+
