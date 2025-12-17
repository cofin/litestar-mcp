# MCP Tool Strategy for litestar-mcp

This document guides intelligent tool selection based on task complexity and type.

## Tool Selection by Task Type

### Complex Architectural Decisions

When making significant design decisions (new module architecture, protocol extensions, plugin patterns):

1. **Primary**: `mcp__pal__thinkdeep`
   - Multi-step investigation with hypothesis testing
   - Evidence-based analysis with confidence tracking
   - Expert validation for complex conclusions

2. **Secondary**: `mcp__pal__planner`
   - For multi-phase project planning
   - Migration strategy design
   - Complex feature breakdown

3. **Fallback**: `mcp__sequential-thinking__sequentialthinking`
   - Step-by-step logical breakdown
   - Linear problem decomposition

### Library Documentation Lookup

When needing framework/library reference:

1. **Primary**: `mcp__context7__get-library-docs`
   ```python
   # For Litestar
   mcp__context7__get-library-docs(
       context7CompatibleLibraryID="/litestar/litestar",
       topic="plugins",
       mode="code"
   )
   ```

2. **Fallback**: `WebSearch`
   - For latest updates not in Context7
   - Community discussions
   - Issue resolutions

### Code Analysis

When reviewing or understanding code:

1. **Primary**: `mcp__pal__analyze`
   - Architecture review
   - Performance analysis
   - Code quality assessment

2. **Secondary**: Local tools
   - `Grep` for pattern search
   - `Glob` for file discovery
   - `Read` for content inspection

### Debugging

When investigating issues:

1. **Primary**: `mcp__pal__debug`
   - Systematic hypothesis testing
   - Root cause analysis
   - Evidence tracking

2. **Secondary**: Standard debugging
   - Run tests with verbose output
   - Add temporary logging
   - Inspect state

### Collaborative Thinking

When brainstorming or need second opinion:

1. **Primary**: `mcp__pal__chat`
   - General discussion
   - Idea validation
   - Alternative approaches

## Complexity-Based Selection

### Simple Features (6 checkpoints)

**Characteristics**:
- Single file change
- Clear requirements
- Follows existing patterns exactly
- CRUD operations

**Tool Strategy**:
- Manual analysis acceptable
- Basic Grep/Glob for pattern finding
- Direct implementation

### Medium Features (8 checkpoints)

**Characteristics**:
- 2-3 files modified
- New endpoint or module
- Some pattern adaptation needed
- Integration with existing code

**Tool Strategy**:
- Use `mcp__sequential-thinking__sequentialthinking` with 12-15 thoughts
- Context7 for framework patterns
- Pattern library consultation

### Complex Features (10+ checkpoints)

**Characteristics**:
- Architecture impact
- 5+ files modified
- New patterns introduced
- Protocol extension

**Tool Strategy**:
- Use `mcp__pal__thinkdeep` or `mcp__pal__planner`
- Deep Context7 research
- WebSearch for protocol specs
- Pattern library update required

## Tool Reference

### Reasoning Tools

| Tool | Use Case | When to Use |
|------|----------|-------------|
| `mcp__sequential-thinking__sequentialthinking` | Linear problem breakdown | Medium complexity, step-by-step analysis |
| `mcp__pal__thinkdeep` | Deep investigation | Complex bugs, architecture decisions |
| `mcp__pal__planner` | Project planning | Multi-phase features, migrations |

### Research Tools

| Tool | Use Case | When to Use |
|------|----------|-------------|
| `mcp__context7__get-library-docs` | Framework docs | Litestar patterns, API reference |
| `WebSearch` | Latest info | Recent updates, community solutions |
| `WebFetch` | Specific pages | GitHub issues, official docs |

### Analysis Tools

| Tool | Use Case | When to Use |
|------|----------|-------------|
| `mcp__pal__analyze` | Code review | Architecture, performance, security |
| `mcp__pal__debug` | Bug investigation | Root cause analysis, systematic debugging |
| `mcp__pal__chat` | Discussion | Brainstorming, validation |

## litestar-mcp Specific Guidance

### Litestar Framework

```python
# Always use for Litestar patterns
mcp__context7__resolve-library-id(libraryName="litestar")
# Returns: /litestar/litestar

mcp__context7__get-library-docs(
    context7CompatibleLibraryID="/litestar/litestar",
    topic="plugins",  # or "routing", "dependency-injection", etc.
    mode="code"
)
```

### MCP Protocol

```python
# For MCP protocol questions
WebSearch(query="Model Context Protocol specification {topic} 2025")

# Or check official docs
mcp__context7__resolve-library-id(libraryName="model context protocol")
```

### Testing Patterns

```python
# For pytest patterns
mcp__context7__resolve-library-id(libraryName="pytest")

mcp__context7__get-library-docs(
    context7CompatibleLibraryID="/pytest-dev/pytest",
    topic="asyncio",
    mode="code"
)
```

## Decision Flowchart

```
Start Task
    │
    ▼
Is it a bug fix?
    │
    ├─Yes─► Use mcp__pal__debug
    │
    └─No──► Is it a feature?
               │
               ├─Yes─► Assess Complexity
               │          │
               │          ├─Simple─► Manual + Grep/Glob
               │          │
               │          ├─Medium─► Sequential Thinking
               │          │
               │          └─Complex─► ThinkDeep or Planner
               │
               └─No──► Is it research?
                          │
                          ├─Yes─► Context7 + WebSearch
                          │
                          └─No──► mcp__pal__chat for discussion
```
