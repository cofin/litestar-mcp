---
description: Re-bootstrap or update AI development infrastructure
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
---

# Bootstrap Alignment Mode

This command re-bootstraps or updates the AI development infrastructure.

## Current Bootstrap Status

Checking existing configuration...

### Inventory

```bash
# Check commands
ls .claude/commands/

# Check agents
ls .claude/agents/

# Check skills
ls -d .claude/skills/*/ 2>/dev/null || echo "No skills"

# Check infrastructure
test -f .claude/mcp-strategy.md && echo "MCP Strategy: exists" || echo "MCP Strategy: MISSING"
test -d specs/guides/patterns && echo "Pattern Library: exists" || echo "Pattern Library: MISSING"
test -f specs/guides/quality-gates.yaml && echo "Quality Gates: exists" || echo "Quality Gates: MISSING"
```

## Alignment Report

Based on inventory, identify:

### Core Commands (Expected)
- [ ] prd.md - Intelligent PRD creation
- [ ] plan.md - Quick planning (exists)
- [ ] implement.md - Implementation workflow (exists)
- [ ] test.md - Testing workflow (exists)
- [ ] review.md - Review workflow (exists)
- [ ] explore.md - Codebase exploration
- [ ] fix-issue.md - GitHub issue fixing
- [ ] bootstrap.md - This command

### Core Agents (Expected)
- [ ] planner.md - PRD/planning agent (exists)
- [ ] expert.md - Implementation agent (exists)
- [ ] testing.md - Testing agent (exists)
- [ ] docs-vision.md - Documentation agent (exists)

### Intelligence Infrastructure (Expected)
- [ ] .claude/mcp-strategy.md - Tool selection guide
- [ ] specs/guides/patterns/ - Pattern library
- [ ] specs/guides/quality-gates.yaml - Quality gates
- [ ] .claude/skills/litestar/ - Litestar skill

## Update Options

Based on what's missing, offer options:

1. **Full Refresh**: Update all components to latest templates
2. **Add Missing**: Only create missing components
3. **Update Specific**: Choose specific components to update

## Execution

After user selection, create/update components while:

1. **Preserving custom content** - Read existing before overwriting
2. **Merging improvements** - Add new features to existing
3. **Maintaining consistency** - Keep project-specific patterns

## Verification

After updates:

```bash
# Verify all components
echo "=== Commands ==="
ls .claude/commands/

echo "=== Agents ==="
ls .claude/agents/

echo "=== Skills ==="
ls -d .claude/skills/*/ 2>/dev/null

echo "=== Infrastructure ==="
test -f .claude/mcp-strategy.md && echo "MCP Strategy: OK"
test -d specs/guides/patterns && echo "Pattern Library: OK"
test -f specs/guides/quality-gates.yaml && echo "Quality Gates: OK"
```

## Report

```markdown
## Bootstrap Alignment Complete

### Updated Components
- [list what was updated]

### Preserved Custom Content
- [list what was preserved]

### New Features Added
- [list new intelligent features]

### Next Steps
1. Review updated CLAUDE.md
2. Test commands with `/explore test`
3. Start development with `/prd [feature]`
```
