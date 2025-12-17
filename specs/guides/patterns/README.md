# Pattern Library

This directory contains reusable patterns extracted from completed litestar-mcp features.

## How Patterns Are Captured

1. During implementation, new patterns are documented in workspace `tmp/new-patterns.md`
2. During review (Docs & Vision agent), patterns are extracted to this directory
3. Future PRD phases consult this library first
4. AGENTS.md is updated with pattern summaries

## Pattern Categories

### Architectural Patterns

Patterns for structuring litestar-mcp components:

- **Plugin Pattern**: How LitestarMCP integrates with Litestar
- **Controller Pattern**: How MCPController exposes REST endpoints
- **Registry Pattern**: How tools and resources are registered
- **Executor Pattern**: How tool execution is handled

### Type Handling Patterns

Patterns for type annotations (CRITICAL for litestar-mcp):

- **Stringified Hints**: `"Optional[MCPConfig]"` not `Optional[MCPConfig]`
- **TYPE_CHECKING Imports**: Conditional imports for type-only references
- **No Future Annotations**: Never use `from __future__ import annotations`
- **No Union Pipe**: Use `Union[A, B]` not `A | B`

### Testing Patterns

Patterns for pytest tests:

- **Class-Based Tests**: `class TestFeatureName:`
- **Async Tests**: `async def test_name(self) -> None:`
- **Fixture Patterns**: Shared fixtures in conftest.py
- **Integration Tests**: Real Litestar app testing

### Error Handling Patterns

Patterns for exceptions:

- **Litestar Inheritance**: Inherit from `ImproperlyConfiguredException`
- **Context Messages**: Include helpful context in error messages
- **Exception Chaining**: Use `raise ... from` when appropriate

### CLI Patterns

Patterns for Click CLI commands:

- **LitestarGroup**: Use for command groups
- **Context Object**: Pass plugin via ctx.obj
- **Rich Output**: Use rich for formatted output

## Using Patterns

### When Starting a Feature

1. Search this directory for similar patterns
2. Read pattern documentation before implementation
3. Follow established conventions
4. Note any deviations with rationale

### During Implementation

```python
# Example: Following Plugin Pattern
from litestar.plugins import InitPluginProtocol, CLIPlugin

class MyFeature(InitPluginProtocol, CLIPlugin):
    """Following plugin pattern from litestar_mcp/plugin.py."""

    def on_app_init(self, app_config: "AppConfig") -> "AppConfig":
        # Implementation following established pattern
        return app_config
```

### Adding New Patterns

1. Document in workspace `tmp/new-patterns.md` during implementation
2. Include: pattern name, use case, code example, rationale
3. Docs & Vision agent extracts to this directory
4. AGENTS.md updated with pattern summary

## Pattern File Structure

Each pattern file should contain:

```markdown
# Pattern Name

## Purpose
What problem does this pattern solve?

## When to Use
Scenarios where this pattern applies.

## Implementation
Code example with explanation.

## Variations
Common variations or adaptations.

## Related Patterns
Links to related patterns.

## Examples in Codebase
- `litestar_mcp/file.py:line` - Description
```

## Cross-Reference

- **Quick Reference**: `AGENTS.md` (workflow + standards)
- **Detailed Guides**: `specs/guides/` (full explanations)
- **Pattern Library**: This directory (extracted patterns)

## Maintenance

This directory is maintained automatically by the Docs & Vision agent:

1. New patterns extracted during feature review
2. Pattern files created with full documentation
3. Cross-references updated
4. Consistency validated

Manual updates are discouraged - let the workflow system capture knowledge naturally through feature development.
