# Testing Patterns for litestar-mcp

Comprehensive testing patterns for litestar-mcp using pytest with asyncio support.

## Overview

litestar-mcp testing follows these principles:
- Class-based test organization
- Async/await support with pytest-asyncio
- Fixture-based dependency injection
- Edge case coverage (empty, errors, boundaries, async patterns)
- 85% minimum coverage target
- Integration testing with real Litestar apps

## Core Patterns

### Class-Based Test Organization

**Pattern**: Group related tests in classes for better organization.

**Example**:
```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from litestar.testing import TestClient

class TestMCPPlugin:
    """Tests for MCP plugin functionality."""

    def test_plugin_initialization(self) -> None:
        """Test plugin initializes with correct defaults."""
        from litestar_mcp import LitestarMCP, MCPConfig

        plugin = LitestarMCP(config=MCPConfig())
        assert plugin.config.base_path == "/mcp"
        assert plugin.config.include_in_schema is False

    async def test_async_tool_execution(self, test_client: "TestClient") -> None:
        """Test async tool execution via REST API."""
        response = test_client.post(
            "/mcp/tools/my_tool",
            json={"arguments": {"name": "test"}},
        )
        assert response.status_code == 200
```

**When to use**: Always. Provides clear organization and allows sharing fixtures within the class.

### Async Test Pattern

**Pattern**: Use `async def` for tests that involve async operations.

**Example**:
```python
async def test_async_handler_execution(self, test_client: "TestClient") -> None:
    """Test execution of async route handlers."""
    response = test_client.post("/mcp/tools/async_tool", json={})
    assert response.status_code == 200
    data = response.json()
    assert "content" in data
```

**When to use**: When testing async route handlers, async tool execution, or any async operations.

### Fixture Patterns

**Pattern**: Use fixtures for reusable test dependencies.

**Example**:
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
        """Test tool handler."""
        return {"message": f"Hello, {name}"}

    return Litestar(
        route_handlers=[test_handler],
        plugins=[LitestarMCP(config=MCPConfig())],
    )

@pytest.fixture
def test_client(mcp_app: Litestar) -> TestClient:
    """Create test client for MCP app."""
    return TestClient(app=mcp_app)
```

**When to use**: For creating test apps, clients, and other reusable test dependencies.

### Edge Case Testing

**Pattern**: Always test edge cases beyond happy path.

**Example**:
```python
class TestMCPEdgeCases:
    """Edge case tests for MCP functionality."""

    async def test_empty_tools_list(self, test_client: "TestClient") -> None:
        """Test handling of empty tools list."""
        response = test_client.get("/mcp/tools")
        assert response.status_code == 200
        data = response.json()
        assert data["tools"] == []

    async def test_invalid_tool_name(self, test_client: "TestClient") -> None:
        """Test error handling for nonexistent tool."""
        response = test_client.post("/mcp/tools/nonexistent", json={})
        assert response.status_code == 404

    async def test_missing_required_parameters(self, test_client: "TestClient") -> None:
        """Test error when required parameters are missing."""
        response = test_client.post(
            "/mcp/tools/tool_with_params",
            json={"arguments": {}},  # Missing required params
        )
        assert response.status_code in [400, 422]
        data = response.json()
        assert "error" in data or "detail" in data

    async def test_cli_context_request_injection(self) -> None:
        """Test that request-dependent tools execute with synthesized Request."""
        from litestar import Litestar, Request, get
        from litestar_mcp.executor import execute_tool

        @get("/needs-request")
        async def handler_with_request(request: Request) -> dict:
            """Handler requiring request."""
            return {"path": request.url.path}

        app = Litestar(route_handlers=[handler_with_request])

        result = await execute_tool(handler_with_request, app, {})
        assert result["path"] == "/needs-request"
```

**When to use**: Always. Edge cases are where bugs hide.

**Common edge cases**:
- Empty inputs/results
- Missing required parameters
- Invalid parameter types
- Error conditions
- Boundary values
- Async edge cases
- CLI context limitations

## Advanced Patterns

### Integration Testing with Real Litestar App

**Pattern**: Test the full stack with a real Litestar application.

**Example**:
```python
async def test_full_mcp_integration(self) -> None:
    """Test complete MCP workflow."""
    from litestar import Litestar, get
    from litestar.testing import TestClient
    from litestar_mcp import LitestarMCP, MCPConfig

    @get("/users", opt={"mcp_tool": "list_users"})
    async def list_users() -> "list[dict]":
        """List users."""
        return [{"id": 1, "name": "Alice"}]

    app = Litestar(
        route_handlers=[list_users],
        plugins=[LitestarMCP(config=MCPConfig())],
    )

    with TestClient(app=app) as client:
        # Test tool discovery
        response = client.get("/mcp/tools")
        assert response.status_code == 200
        tools = response.json()["tools"]
        assert len(tools) == 1
        assert tools[0]["name"] == "list_users"

        # Test tool execution
        response = client.post("/mcp/tools/list_users", json={})
        assert response.status_code == 200
        content = response.json()["content"]
        assert len(content) > 0
```

**When to use**: To verify end-to-end functionality with real Litestar components.

### Testing MCP Protocol Compliance

**Pattern**: Verify responses match MCP protocol specification.

**Example**:
```python
async def test_mcp_tool_response_format(self, test_client: "TestClient") -> None:
    """Test tool response matches MCP protocol format."""
    response = test_client.post("/mcp/tools/my_tool", json={"arguments": {}})
    assert response.status_code == 200
    data = response.json()

    # Verify MCP protocol compliance
    assert "content" in data
    assert isinstance(data["content"], list)
    assert len(data["content"]) > 0
    assert data["content"][0]["type"] == "text"
    assert "text" in data["content"][0]
```

**When to use**: When implementing or modifying MCP protocol endpoints.

## Test Commands

```bash
# Run all tests
uv run pytest tests/

# Run with coverage
uv run pytest tests/ --cov=litestar_mcp --cov-report=term-missing

# Run specific file
uv run pytest tests/test_plugin.py -v

# Run single test
uv run pytest tests/test_plugin.py::TestMCPPlugin::test_tool_execution -v

# Run tests matching pattern
uv run pytest tests/ -k "mcp" -v

# Check coverage threshold
uv run pytest tests/ --cov=litestar_mcp --cov-fail-under=85
```

## Coverage Requirements

- Minimum: 85%
- Target: 90%+
- Focus: Core business logic, edge cases, error paths

## Related Patterns

- See AGENTS.md → Testing Patterns section
- See [Error Handling](error-handling.md) for error testing patterns
- See [Plugin Architecture](plugin-architecture.md) for testing plugin initialization

---

**This guide is automatically updated** by the Docs & Vision agent as new testing patterns are discovered.
