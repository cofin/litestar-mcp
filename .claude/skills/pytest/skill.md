# pytest Skill for litestar-mcp

Quick reference for pytest patterns used in litestar-mcp testing.

## Context7 Lookup

```python
mcp__context7__resolve-library-id(libraryName="pytest")
# Returns: /pytest-dev/pytest

mcp__context7__get-library-docs(
    context7CompatibleLibraryID="/pytest-dev/pytest",
    topic="asyncio",
    mode="code"
)
```

## Test Structure

### Class-Based Tests (Preferred)

```python
class TestMyFeature:
    """Tests for my feature."""

    def test_sync_operation(self) -> None:
        """Test synchronous operation."""
        result = sync_function()
        assert result == expected

    async def test_async_operation(self) -> None:
        """Test asynchronous operation."""
        result = await async_function()
        assert result == expected
```

### Function-Based Tests

```python
def test_simple_case() -> None:
    """Test simple case."""
    assert function() == expected

async def test_async_case() -> None:
    """Test async case."""
    result = await async_function()
    assert result == expected
```

## Fixtures

### conftest.py

```python
import pytest
from litestar import Litestar
from litestar.testing import TestClient

from litestar_mcp import LitestarMCP
from litestar_mcp.config import MCPConfig

@pytest.fixture
def mcp_config() -> MCPConfig:
    """Provide test MCP configuration."""
    return MCPConfig(
        base_path="/mcp",
        name="test-server",
    )

@pytest.fixture
def mcp_plugin(mcp_config: MCPConfig) -> LitestarMCP:
    """Provide test MCP plugin."""
    return LitestarMCP(config=mcp_config)

@pytest.fixture
def app(mcp_plugin: LitestarMCP) -> Litestar:
    """Provide test Litestar application."""
    return Litestar(
        plugins=[mcp_plugin],
        route_handlers=[test_handler],
    )

@pytest.fixture
def client(app: Litestar) -> TestClient:
    """Provide test client."""
    return TestClient(app)
```

### Async Fixtures

```python
@pytest.fixture
async def async_resource() -> AsyncGenerator["Resource", None]:
    """Provide async resource with cleanup."""
    resource = await Resource.create()
    yield resource
    await resource.cleanup()
```

## Assertions

### Basic Assertions

```python
assert result == expected
assert result is not None
assert isinstance(result, MyClass)
assert "key" in result
assert len(result) == 3
```

### Exception Testing

```python
def test_raises_error() -> None:
    """Test that function raises expected error."""
    with pytest.raises(ValueError, match="invalid value"):
        function_that_raises()

def test_raises_with_context() -> None:
    """Test exception with context check."""
    with pytest.raises(MCPConfigError) as exc_info:
        invalid_config()
    assert "configuration" in str(exc_info.value)
```

### Async Exception Testing

```python
async def test_async_raises() -> None:
    """Test async function raises."""
    with pytest.raises(AsyncError):
        await async_function_that_raises()
```

## Parametrize

```python
@pytest.mark.parametrize(
    ("input_value", "expected"),
    [
        ("a", 1),
        ("b", 2),
        ("c", 3),
    ],
)
def test_parametrized(input_value: str, expected: int) -> None:
    """Test with multiple inputs."""
    assert process(input_value) == expected
```

## Markers

### Built-in Markers

```python
@pytest.mark.skip(reason="Not implemented yet")
def test_future_feature() -> None:
    pass

@pytest.mark.skipif(condition, reason="Condition not met")
def test_conditional() -> None:
    pass

@pytest.mark.xfail(reason="Known bug")
def test_known_failure() -> None:
    pass
```

### Custom Markers (litestar-mcp)

```python
@pytest.mark.slow
def test_slow_operation() -> None:
    """Mark slow tests."""
    pass

@pytest.mark.integration
def test_integration() -> None:
    """Mark integration tests."""
    pass

@pytest.mark.unit
def test_unit() -> None:
    """Mark unit tests."""
    pass
```

## Mocking

```python
from unittest.mock import AsyncMock, MagicMock, patch

def test_with_mock() -> None:
    """Test with mocked dependency."""
    mock_service = MagicMock()
    mock_service.get_data.return_value = {"key": "value"}

    result = function_using_service(mock_service)
    assert result == expected
    mock_service.get_data.assert_called_once()

async def test_async_mock() -> None:
    """Test with async mock."""
    mock_client = AsyncMock()
    mock_client.fetch.return_value = {"data": "value"}

    result = await async_function(mock_client)
    assert result == expected
```

### Patch Decorator

```python
@patch("litestar_mcp.module.external_function")
def test_with_patch(mock_func: MagicMock) -> None:
    """Test with patched function."""
    mock_func.return_value = "mocked"

    result = function_that_calls_external()
    assert result == "mocked"
```

## Running Tests

```bash
# Run all tests
uv run pytest tests/ -v

# Run specific file
uv run pytest tests/test_plugin.py -v

# Run specific class
uv run pytest tests/test_plugin.py::TestLitestarMCP -v

# Run specific test
uv run pytest tests/test_plugin.py::TestLitestarMCP::test_method -v

# Run with pattern
uv run pytest tests/ -k "mcp" -v

# Run with coverage
uv run pytest tests/ --cov=litestar_mcp --cov-report=term-missing

# Run in parallel
uv run pytest tests/ -n auto
```

## Coverage

```bash
# Basic coverage
uv run pytest tests/ --cov=litestar_mcp

# With missing lines
uv run pytest tests/ --cov=litestar_mcp --cov-report=term-missing

# HTML report
uv run pytest tests/ --cov=litestar_mcp --cov-report=html
```

## Project-Specific Test Files

- `tests/conftest.py` - Shared fixtures
- `tests/test_plugin.py` - Plugin tests
- `tests/test_config.py` - Configuration tests
- `tests/test_routes.py` - Route handler tests
- `tests/test_executor.py` - Executor tests
- `tests/test_cli.py` - CLI tests
- `tests/test_registry.py` - Registry tests
