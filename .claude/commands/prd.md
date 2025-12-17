---
description: Create a PRD with pattern learning and adaptive complexity
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, WebSearch, mcp__context7__resolve-library-id, mcp__context7__get-library-docs, mcp__sequential-thinking__sequentialthinking, mcp__pal__planner
---

# Intelligent PRD Creation Workflow

You are creating a Product Requirements Document for: **$ARGUMENTS**

## Intelligence Layer (ACTIVATE FIRST)

Before starting checkpoints:

1. **Read MCP Strategy**: Load `.claude/mcp-strategy.md` for tool selection
2. **Learn from Codebase**: Read 3-5 similar implementations
3. **Assess Complexity**: Determine simple/medium/complex
4. **Adapt Workflow**: Adjust checkpoint depth

## Critical Rules

1. **CONTEXT FIRST** - Read existing patterns before planning
2. **NO CODE MODIFICATION** - Planning only
3. **PATTERN LEARNING** - Identify 3-5 similar features
4. **ADAPTIVE DEPTH** - Simple=6, Medium=8, Complex=10+ checkpoints
5. **RESEARCH GROUNDED** - Minimum 2000+ words research
6. **COMPREHENSIVE PRD** - Minimum 3200+ words

---

## Checkpoint 0: Intelligence Bootstrap

**Load project intelligence:**

1. Read `CLAUDE.md`
2. Read `AGENTS.md`
3. Read `specs/guides/README.md`
4. Read `.claude/mcp-strategy.md`

**Learn from existing implementations:**

```bash
# Find similar features in litestar_mcp
grep -r "class.*" litestar_mcp/ | head -10

# Read 3 example files for patterns
```

**Assess complexity:**

- **Simple**: Single file, config change, bug fix -> 6 checkpoints
- **Medium**: New module, 2-3 files, new endpoint -> 8 checkpoints
- **Complex**: Architecture change, 5+ files, protocol extension -> 10+ checkpoints

**Output**: "Checkpoint 0 complete - Complexity: [level], Checkpoints: [count]"

---

## Checkpoint 1: Pattern Recognition

**Identify similar implementations:**

1. Search for related patterns in litestar_mcp/
2. Read at least 3 similar files
3. Extract naming patterns
4. Note testing patterns in tests/

**Document in workspace:**

```markdown
## Similar Implementations

1. `litestar_mcp/path/to/similar1.py` - Description
2. `litestar_mcp/path/to/similar2.py` - Description
3. `litestar_mcp/path/to/similar3.py` - Description

## Patterns Observed

- Class structure: ...
- Naming conventions: ...
- Error handling: ...
- Test patterns: ...
```

**Output**: "Checkpoint 1 complete - Patterns identified"

---

## Checkpoint 2: Workspace Creation

```bash
# Create slug from feature name (lowercase, hyphens)
mkdir -p specs/active/{slug}/research
mkdir -p specs/active/{slug}/tmp
mkdir -p specs/active/{slug}/patterns
```

Create workspace README:

```markdown
# {Feature Name} Workspace

**Created**: {date}
**Complexity**: {simple|medium|complex}
**Checkpoints**: {6|8|10+}

## Similar Features Analyzed
- [list from Checkpoint 1]

## Pattern Compliance
- Following: [list patterns being followed]
- New: [any new patterns being introduced]
```

**Output**: "Checkpoint 2 complete - Workspace at specs/active/{slug}/"

---

## Checkpoint 3: Intelligent Analysis

**Use appropriate tool based on complexity:**

- **Simple**: 10 structured thoughts
- **Medium**: Sequential thinking (15 thoughts)
- **Complex**: zen.planner or zen.thinkdeep

For litestar-mcp, always consider:
- MCP protocol compliance
- Litestar plugin architecture
- Async patterns
- Type annotation standards (stringified hints, no future annotations)
- Testing strategy (pytest-asyncio, 85% coverage)

**Document analysis in `specs/active/{slug}/research/analysis.md`**

**Output**: "Checkpoint 3 complete - Analysis using [tool]"

---

## Checkpoint 4: Research (2000+ words)

**Priority order for litestar-mcp:**

1. **Pattern Library**: `specs/guides/patterns/`
2. **Internal Guides**: `specs/guides/` (testing-patterns.md, plugin-architecture.md)
3. **AGENTS.md**: `AGENTS.md` for workflow + code standards
4. **Context7**: Litestar documentation
   ```python
   mcp__context7__get-library-docs(
       context7CompatibleLibraryID="/litestar/litestar",
       topic="plugins"
   )
   ```
5. **WebSearch**: MCP protocol specifics, best practices

**Write to**: `specs/active/{slug}/research/plan.md`

**Verify word count**: `wc -w specs/active/{slug}/research/plan.md`

**Output**: "Checkpoint 4 complete - Research ({word_count} words)"

---

## Checkpoint 5: Write PRD (3200+ words)

Create `specs/active/{slug}/prd.md` with:

```markdown
# {Feature Name} - Product Requirements Document

**Version**: 1.0
**Created**: {date}
**Complexity**: {simple|medium|complex}
**Checkpoint Depth**: {6|8|10+}

## Intelligence Context

### Similar Features Analyzed
[From Checkpoint 1]

### Patterns Being Followed
[List patterns with file references]

### New Patterns Introduced
[Any new patterns with rationale]

## Problem Statement

[What problem does this solve? Why is it needed?]

## Scope

### In Scope
- [Specific deliverables]

### Out of Scope
- [What is NOT included]

## Acceptance Criteria

[SPECIFIC, MEASURABLE criteria]

1. [ ] Criterion 1 with measurable outcome
2. [ ] Criterion 2 with testable requirement
...

## Technical Approach

### Architecture
[How does this fit into litestar-mcp architecture?]

### Pattern References
[Links to similar implementations]

### Key Components
[Main classes/modules to be created/modified]

## Testing Strategy

- Unit tests: tests/test_{feature}.py
- Integration tests: [if needed]
- Coverage target: 85%+
- Edge cases: [list key edge cases]

## Documentation Requirements

- API reference: docs/reference/{feature}.rst
- Usage guide: docs/usage/{feature}.rst
- Code examples: [list examples needed]

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| ... | ... | ... |
```

**Verify word count**: `wc -w specs/active/{slug}/prd.md`

**Output**: "Checkpoint 5 complete - PRD ({word_count} words)"

---

## Checkpoint 6: Task Breakdown

Create `specs/active/{slug}/tasks.md`:

Adapt phases to complexity:

**Simple (6 phases)**:
1. Planning (done)
2. Research
3. Implementation
4. Testing
5. Documentation
6. Archive

**Medium (8 phases)**:
1. Planning (done)
2. Deep Research
3. Core Implementation
4. Integration
5. Testing
6. Documentation
7. Knowledge Capture
8. Archive

**Complex (10+ phases)**:
1. Planning (done)
2. Architecture Analysis
3. Prototype
4. Core Implementation
5. Secondary Features
6. Integration
7. Testing - Unit
8. Testing - Integration
9. Documentation
10. Knowledge Capture
11. Re-validation
12. Archive

**Output**: "Checkpoint 6 complete - Tasks adapted to complexity"

---

## Checkpoint 7: Recovery Guide

Create `specs/active/{slug}/recovery.md`:

```markdown
# Recovery Guide - {Feature Name}

## Quick Resume

**Workspace**: specs/active/{slug}/
**Complexity**: {level}
**Current Phase**: Phase 1 - Planning Complete

## Intelligence Context

### Similar Features
[List from Checkpoint 1 - critical for maintaining consistency]

### Pattern Compliance
[Patterns being followed]

### Key Decisions Made
[Important decisions from planning]

## Resume Instructions

1. Read this file first
2. Read prd.md for requirements
3. Read tasks.md for current status
4. Consult AGENTS.md for code standards
5. Check specs/guides/ for relevant patterns

## Phase Status

- [x] Phase 1: Planning
- [ ] Phase 2: ...
...

## Files Modified

[To be updated during implementation]

## Test Status

[To be updated during testing]
```

**Output**: "Checkpoint 7 complete - Recovery guide with intelligence context"

---

## Checkpoint 8: Git Verification

```bash
# Verify no source code was modified
git status --porcelain litestar_mcp/ tests/ | grep -v "^??"
```

**Expected**: No output (no source changes)

**Output**: "Checkpoint 8 complete - No source code modified"

---

## Final Summary

```
PRD Phase Complete

Workspace: specs/active/{slug}/
Complexity: [simple|medium|complex]
Checkpoints: [6|8|10+] completed

Intelligence:
- Pattern library consulted
- Similar features analyzed: {count}
- Tool selection optimized for complexity

Files Created:
- prd.md ({word_count} words)
- tasks.md ({phase_count} phases)
- recovery.md
- research/plan.md ({research_words} words)
- research/analysis.md

Next: Run `/implement {slug}`
```
