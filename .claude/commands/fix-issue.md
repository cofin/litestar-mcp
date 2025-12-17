---
description: Fix a GitHub issue with pattern-guided implementation
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, WebSearch, WebFetch, mcp__context7__resolve-library-id, mcp__context7__get-library-docs, mcp__pal__debug
---

# GitHub Issue Fix Workflow

You are fixing GitHub issue: **$ARGUMENTS**

## Phase 1: Issue Analysis

### Step 1.1: Fetch Issue Details

```bash
# If argument is a number, fetch from GitHub
gh issue view {issue_number} --json title,body,labels,comments
```

Or if a URL is provided:
```python
WebFetch(url="{issue_url}", prompt="Extract issue title, description, reproduction steps, and expected behavior")
```

### Step 1.2: Categorize Issue

Determine issue type:
- **Bug**: Something doesn't work as expected
- **Feature**: New functionality request
- **Enhancement**: Improve existing functionality
- **Documentation**: Docs need updating

### Step 1.3: Assess Complexity

Based on issue details:
- **Simple**: Single file fix, clear solution
- **Medium**: Multiple files, needs investigation
- **Complex**: Architecture impact, needs PRD

**If Complex**: Recommend `/prd {issue_title}` instead.

---

## Phase 2: Investigation

### Step 2.1: Load Context

```python
Read("CLAUDE.md")
Read("AGENTS.md")
```

### Step 2.2: Search for Related Code

```bash
# Search for keywords from issue
grep -r "{keyword}" litestar_mcp/
grep -r "{keyword}" tests/
```

### Step 2.3: Understand Current Behavior

Read the relevant files completely to understand:
- Current implementation
- Related tests
- Edge cases

### Step 2.4: Root Cause Analysis

For bugs, use systematic debugging:

```python
mcp__pal__debug(
    step="Investigating issue #{issue_number}: {title}",
    step_number=1,
    total_steps=3,
    hypothesis="Initial hypothesis based on issue description",
    findings="What I found in the code...",
    confidence="exploring",
    next_step_required=True
)
```

---

## Phase 3: Implementation

### Step 3.1: Plan the Fix

Before coding, document:
- Root cause identified
- Proposed solution
- Files to modify
- Tests needed

### Step 3.2: Follow Code Standards

**MANDATORY** for litestar-mcp:

```python
# Type hints - stringified for non-builtins
def fix_function(param: "Optional[MCPConfig]") -> "dict[str, Any]":
    pass

# NO future annotations
# NO | union syntax (use Union[A, B])
# NO inline comments (use docstrings)
```

### Step 3.3: Make Changes

Edit files following existing patterns:
- Match existing code style
- Follow naming conventions
- Use Litestar patterns (encode_json, exceptions)

### Step 3.4: Update Tests

For bugs:
- Add regression test that would have caught the bug
- Ensure test fails before fix, passes after

```bash
# Run specific test
uv run pytest tests/test_{module}.py -v -k "{test_name}"
```

---

## Phase 4: Verification

### Step 4.1: Run Tests

```bash
# Run all tests
uv run pytest tests/ -v

# With coverage
uv run pytest tests/ --cov=litestar_mcp --cov-report=term-missing
```

### Step 4.2: Run Linting

```bash
# Full lint
make lint

# Quick check
make ruff-check
```

### Step 4.3: Type Check

```bash
make type-check
```

---

## Phase 5: Documentation

### Step 5.1: Update Docs if Needed

If behavior changed:
- Update relevant .rst files in docs/
- Update code examples
- Update CHANGELOG.md

### Step 5.2: Prepare Commit Message

```markdown
fix: {brief description}

Fixes #{issue_number}

- Root cause: {explanation}
- Solution: {what was changed}
- Tests: {what tests were added}
```

---

## Phase 6: Summary

```markdown
## Issue Fix Summary

**Issue**: #{issue_number} - {title}
**Type**: {bug|feature|enhancement|documentation}
**Complexity**: {simple|medium}

### Root Cause
{explanation}

### Changes Made
- `litestar_mcp/file.py:line` - Description
- `tests/test_file.py:line` - Added regression test

### Tests
- All tests passing
- Coverage: {percentage}%
- New tests: {list}

### Verification
- [ ] Tests pass
- [ ] Linting clean
- [ ] Type check passes
- [ ] Documentation updated (if needed)

### Ready for Review
{Yes/No - if No, explain what's pending}
```
