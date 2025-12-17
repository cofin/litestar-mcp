# litestar-mcp Development Guides

This directory contains detailed development guides for litestar-mcp patterns and best practices. These guides are automatically updated by the Docs & Vision agent as new patterns are discovered during feature implementation.

## Guide Structure

Each guide focuses on a specific aspect of litestar-mcp development with:
- Pattern explanations with context
- When and why to use each pattern
- Complete working code examples
- Cross-references to AGENTS.md
- Related patterns and guides

## Available Guides

### Core Patterns
- [Testing Patterns](testing-patterns.md) - pytest, async tests, fixtures, edge cases
- [Plugin Architecture](plugin-architecture.md) - Litestar plugin integration patterns
- [CLI Integration](cli-integration.md) - Command-line interface patterns
- [Schema Generation](schema-generation.md) - Automatic JSON Schema patterns

### MCP Protocol
- [MCP Integration](mcp-integration.md) - Model Context Protocol implementation patterns
- [Tool Development](tool-development.md) - Creating MCP tools
- [Resource Development](resource-development.md) - Creating MCP resources

### Error Handling
- [Error Handling](error-handling.md) - Exception patterns and error responses

## Guide Update Process

These guides are maintained through the automated workflow:

1. **During Implementation**: Expert agent builds features following existing patterns
2. **During Documentation**: Docs & Vision agent extracts new patterns discovered
3. **Knowledge Capture**: New patterns added to `specs/guides/patterns/` and indexed in `AGENTS.md`
4. **Guide Updates**: Relevant guides updated with detailed explanations and cross-references
5. **Re-validation**: Consistency verified across AGENTS.md and all guides

## Using These Guides

**For Planning** (Planner agent):
- Reference guides to understand established patterns
- Identify which patterns apply to new features
- Include relevant guides in research questions for Expert

**For Implementation** (Expert agent):
- Consult guides for detailed pattern explanations
- Follow established patterns for consistency
- Discover when to deviate from patterns (with justification)

**For Testing** (Testing agent):
- Review testing-patterns.md for comprehensive test strategies
- Follow edge case patterns
- Apply async testing patterns

**For Documentation** (Docs & Vision agent):
- Extract new patterns not yet in guides
- Update relevant guides with new discoveries
- Ensure cross-references between AGENTS.md and guides

## Relationship to AGENTS.md

- **AGENTS.md**: Workflow + standards + quick pattern index (updated automatically)
- **specs/guides/**: Detailed explanations with full context (updated automatically)

Both are kept in sync by the Docs & Vision agent's re-validation process.

## Contributing New Patterns

Patterns are added automatically through the `/implement` workflow:

1. Feature is implemented with new pattern
2. Docs & Vision agent extracts the pattern
3. Pattern added to AGENTS.md
4. Relevant guide(s) updated with detailed explanation
5. Cross-references created
6. Re-validation ensures consistency

Manual pattern updates are discouraged - let the workflow system capture knowledge naturally through feature development.

---

**Last Updated**: 2025-10-18 (Workflow system installation)
**Maintained By**: Docs & Vision agent (automatic)
**Update Frequency**: After each feature implementation
