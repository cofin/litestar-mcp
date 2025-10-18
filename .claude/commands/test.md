Invoke the Testing agent to create comprehensive test suites for a feature.

**What this does:**
- Reads implementation from `specs/active/{slug}/recovery.md`
- Creates unit and integration tests following pytest patterns
- Tests edge cases, async patterns, error conditions
- Validates 85% coverage target

**Usage:**
```
/test websocket-mcp-transport
```

**Or for most recent spec:**
```
/test
```

**The Testing agent will:**
1. Read PRD for acceptance criteria
2. Read recovery.md for implementation details
3. Read AGENTS.md for testing patterns
4. Create unit tests:
   - Class-based organization: `class TestFeature:`
   - Async support: `async def test_method(self) -> None:`
   - Fixtures for Litestar apps and clients
5. Create integration tests with real dependencies
6. Test edge cases:
   - Empty results
   - Error conditions
   - Boundary values
   - Async patterns
   - CLI context limitations (if applicable)
7. Validate coverage: `uv run pytest --cov=litestar_mcp --cov-fail-under=85`
8. Update workspace (tasks.md, recovery.md)

**Test Commands for litestar-mcp:**
```bash
# Run all tests
uv run pytest tests/

# Run with coverage
uv run pytest tests/ --cov=litestar_mcp --cov-report=term-missing

# Run specific file
uv run pytest tests/test_feature.py -v

# Run single test
uv run pytest tests/test_feature.py::TestClass::test_method -v
```

**Note:** This command is typically **not needed** manually because `/implement` automatically invokes the Testing agent.

Use this only if you need to:
- Re-create tests after manual changes
- Add additional test coverage
- Debug test failures

**After testing:**
- All tests passing ✅
- Coverage ≥ 85% ✅
- Ready for documentation phase
