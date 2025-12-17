---
description: Explore the litestar-mcp codebase with intelligent context
allowed-tools: Read, Glob, Grep, Bash, mcp__context7__resolve-library-id, mcp__context7__get-library-docs
---

# Intelligent Codebase Exploration

You are exploring the litestar-mcp codebase for: **$ARGUMENTS**

## Exploration Strategy

### Step 1: Understand the Query

Determine what type of exploration is needed:

- **Architecture**: How components fit together
- **Pattern**: How similar features are implemented
- **API**: Specific endpoints or handlers
- **Testing**: Test patterns and fixtures
- **Integration**: How litestar-mcp integrates with Litestar

### Step 2: Load Project Context

```python
# Always start with these
Read("CLAUDE.md")
Read("AGENTS.md")
```

### Step 3: Targeted Search

**For architecture questions:**
```bash
# Core plugin architecture
cat litestar_mcp/plugin.py
cat litestar_mcp/config.py
cat litestar_mcp/routes.py
```

**For pattern questions:**
```bash
# Find similar implementations
grep -r "class.*{pattern}" litestar_mcp/
grep -r "def.*{pattern}" litestar_mcp/
```

**For API questions:**
```bash
# Route handlers
grep -r "@get\|@post\|@put\|@delete" litestar_mcp/
cat litestar_mcp/routes.py
```

**For testing questions:**
```bash
# Test patterns
ls tests/
cat tests/conftest.py
grep -r "class Test\|async def test_" tests/ | head -20
```

### Step 4: Deep Dive

Based on search results, read relevant files completely.

### Step 5: Provide Summary

Structure your response:

```markdown
## Exploration Results: {topic}

### Overview
[Brief summary of what was found]

### Key Files
- `litestar_mcp/file.py:line` - Description
- `tests/test_file.py:line` - Description

### Patterns Identified
1. Pattern name - where it's used
2. Pattern name - where it's used

### Code Examples
[Relevant code snippets]

### Related Documentation
- docs/reference/...
- specs/guides/...

### Next Steps
[Suggestions for further exploration or action]
```

## Quick Reference

### Project Structure
```
litestar_mcp/
├── plugin.py      # Main plugin (LitestarMCP)
├── config.py      # Configuration (MCPConfig)
├── routes.py      # MCP API controller
├── schema.py      # MCP schemas (MCPTool, MCPResource)
├── registry.py    # Tool/resource registry
├── executor.py    # Tool execution
├── decorators.py  # @mcp_tool, @mcp_resource
├── cli.py         # CLI commands
├── filters.py     # Handler filtering
├── http_client.py # HTTP client for tool calls
├── sse.py         # Server-Sent Events
└── typing.py      # Type definitions
```

### Test Structure
```
tests/
├── conftest.py       # Shared fixtures
├── test_plugin.py    # Plugin tests
├── test_config.py    # Configuration tests
├── test_routes.py    # Route handler tests
├── test_executor.py  # Executor tests
├── test_cli.py       # CLI tests
└── ...
```

### Documentation
```
docs/
├── reference/   # API documentation
├── usage/       # Usage guides
└── examples/    # Code examples
```

## MCP Protocol Reference

For MCP protocol questions, use Context7:

```python
mcp__context7__get-library-docs(
    context7CompatibleLibraryID="/modelcontextprotocol/docs",
    topic="{specific_topic}"
)
```

Or WebSearch for latest specifications:

```python
WebSearch(query="Model Context Protocol {topic} specification 2025")
```
