# Recovery Guide: {Feature Name}

## To Resume Work

1. **Read this document first**
2. Read [prd.md](prd.md) for full requirements and MCP protocol details
3. Check [tasks.md](tasks.md) for current progress
4. Review [research/](research/) for Expert findings
5. Check [progress.md](progress.md) if exists (running log)

## Current Status

**Phase**: {Current phase number and name}
**Last Updated**: {Date}
**Completed**: {X/Y tasks}

## Files Modified

{List of litestar_mcp files changed so far:}
- litestar_mcp/{module}.py - {description of changes}
- tests/test_{feature}.py - {description of tests}
- docs/reference/{feature}.rst - {documentation updates}

## Next Steps

{Specific next actions to continue the work:}
1. {Next immediate task}
2. {Following task}
3. {Subsequent task}

## Agent-Specific Instructions

### For Expert Agent

**Start Here**:
1. Read PRD (prd.md) for MCP protocol requirements
2. Read AGENTS.md for all litestar-mcp patterns (MANDATORY)
3. Review research questions in PRD
4. Document research findings in research/ before implementing

**litestar-mcp Code Standards (from AGENTS.md)**:
- Type hints: Stringified for non-builtins: `"Optional[str]"`, `"MCPTool"`
- NEVER use `from __future__ import annotations`
- NEVER use `|` union syntax (use `typing.Union`, `typing.Optional`)
- Imports: Only nested when needed to prevent import errors
- Comments: Only in docstrings, never inline
- JSON: Use Litestar's `encode_json()`/`decode_json()`
- Errors: Inherit from Litestar exceptions (`ImproperlyConfiguredException`, etc.)

**Implementation Checklist**:
- [ ] Follow ALL AGENTS.md patterns (MANDATORY)
- [ ] Research MCP protocol specifications
- [ ] Research Litestar plugin patterns
- [ ] Review similar code in litestar_mcp/
- [ ] Write tests as you implement
- [ ] Update workspace continuously (tasks.md, recovery.md)
- [ ] Self-test: `uv run pytest tests/test_{feature}.py`
- [ ] Will auto-invoke Testing agent when implementation complete
- [ ] Will auto-invoke Docs & Vision for documentation and archival

**Common Patterns**:
```python
# Plugin pattern
class LitestarMCP(InitPluginProtocol, CLIPlugin):
    def on_app_init(self, app_config: "AppConfig") -> "AppConfig": ...
    def on_cli_init(self, cli: "Group") -> None: ...

# Route marking
@mcp_tool(name="tool_name")
@get("/endpoint")
async def my_tool(param: str) -> dict: ...

# Error handling
class MyMCPError(ImproperlyConfiguredException):
    def __init__(self, context: str) -> None:
        super().__init__(f"Error: {context}")

# CLI integration
@click.group(cls=LitestarGroup, name="mcp")
def mcp_group(ctx: "click.Context") -> None: ...
```

### For Testing Agent

**Testing Strategy for litestar-mcp**:

```bash
# Run all tests
uv run pytest tests/

# Run with coverage
uv run pytest tests/ --cov=litestar_mcp --cov-report=term-missing

# Run specific test file
uv run pytest tests/test_{feature}.py -v

# Run single test method
uv run pytest tests/test_{feature}.py::TestClass::test_method -v

# Check coverage threshold
uv run pytest tests/ --cov=litestar_mcp --cov-fail-under=85
```

**Test Patterns** (from AGENTS.md):
```python
class TestFeature:
    """Tests for feature functionality."""

    def test_basic_operation(self) -> None:
        """Test basic feature operation."""
        result = feature_function(input_data)
        assert result == expected

    async def test_async_operation(self) -> None:
        """Test async feature operation."""
        result = await async_feature_function(input_data)
        assert result == expected

    async def test_error_handling(self, test_client: TestClient) -> None:
        """Test error conditions."""
        response = test_client.post("/mcp/tools/tool", json={})
        assert response.status_code in [400, 404, 500]
```

**Test Coverage Requirements**:
- Unit tests for core logic
- Integration tests with real Litestar app
- Edge cases: empty inputs, errors, boundaries, async patterns
- MCP protocol compliance
- CLI tests if applicable
- Minimum 85% coverage

### For Docs & Vision Agent

**Documentation System for litestar-mcp**:

- System: Sphinx with .rst files
- Location: docs/
- Build: `make docs` → docs/_build/
- Serve: `make docs-serve` → http://localhost:8002

**Complete Workflow**:

1. **Quality Gate**:
   - Verify all PRD acceptance criteria met
   - BLOCK if any criterion not met
   - Request fixes from Expert

2. **Documentation**:
   - Update docs/reference/{feature}.rst
   - Update docs/usage/examples.rst
   - Validate examples work
   - Build docs without errors

3. **Knowledge Capture** (CRITICAL):
   - Analyze implementation for new patterns
   - Extract error handling, testing, MCP integration patterns
   - Update AGENTS.md with patterns and examples
   - Update specs/guides/ with detailed guides

4. **Re-validation** (CRITICAL):
   - Re-run tests: `uv run pytest tests/`
   - Rebuild docs: `make docs`
   - Verify pattern consistency
   - BLOCK if re-validation fails

5. **Cleanup & Archive**:
   - Clean tmp/ directory
   - Move to specs/archive/
   - Generate completion report

## Blockers

{Any blockers or dependencies - update as discovered:}
- {Blocker description}
- {Dependency waiting on}

## Questions

{Open questions for user or other agents:}
- {Question about MCP protocol interpretation}
- {Question about Litestar integration approach}
- {Question about backward compatibility}

## Progress Log

{Running log of changes - append as work progresses:}

### {Date} - Expert
- {Change description}
- {Implementation note}

### {Date} - Testing
- {Test added}
- {Coverage achieved}

### {Date} - Docs & Vision
- {Documentation updated}
- {Pattern captured}
