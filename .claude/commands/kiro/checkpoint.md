---
description: Create, compare, list, or restore named workflow checkpoints
allowed-tools: Read, Bash, Glob
argument-hint: <save|compare|list|restore> [name]
---

# Checkpoint Management

## Parse Arguments
- Action: `$1` (required: `save`, `compare`, `list`, or `restore`)
- Name: `$2` (required for save/compare/restore)

## Validate Arguments

If `$1` is empty, show usage:
```
Usage: /kiro:checkpoint <action> [name]

Actions:
  save <name>     Create a checkpoint at current state
  compare <name>  Show changes since checkpoint
  list            Show all checkpoints
  restore <name>  Soft reset to checkpoint (requires confirmation)
```

## Execute Action

### save

Create a git tag marking the current state:

```bash
git tag -a "sdd-checkpoint/$2" -m "SDD checkpoint: $2 ($(date -u +%Y-%m-%dT%H:%M:%SZ))"
```

Confirm to user:
```
Checkpoint saved: sdd-checkpoint/{name}
Compare later with: /kiro:checkpoint compare {name}
```

### compare

Show what changed since the checkpoint:

```bash
# Files changed since checkpoint
git diff --stat "sdd-checkpoint/$2"..HEAD

# Commits since checkpoint
git log --oneline "sdd-checkpoint/$2"..HEAD
```

Display results as a structured comparison:
- Files added/modified/deleted since checkpoint
- Commits made since checkpoint
- Summary of scope of changes

### list

Show all SDD checkpoints:

```bash
git tag -l "sdd-checkpoint/*" --sort=-creatordate --format="%(creatordate:short) %(refname:short) %(subject)"
```

Display as a table:
```
Date        Checkpoint                    Message
──────────  ───────────────────────────   ──────────────────
2026-03-31  sdd-checkpoint/task-1.2-done  SDD checkpoint: task-1.2-done
2026-03-30  sdd-checkpoint/start          SDD checkpoint: start
```

If no checkpoints exist: "No checkpoints found. Create one with `/kiro:checkpoint save <name>`"

### restore

**This is a destructive operation — require explicit user confirmation.**

Before executing:
1. Show the user what will change: `git diff --stat HEAD.."sdd-checkpoint/$2"`
2. Warn: "This will soft reset to checkpoint {name}. Uncommitted changes will be preserved as unstaged."
3. Ask for explicit confirmation: "Type 'yes' to confirm restore."

Only after confirmation:
```bash
git reset --soft "sdd-checkpoint/$2"
```

**Do NOT execute restore without user confirmation.**

## Integration Notes

After completing a spec task, consider suggesting:
```
Tip: Save progress with /kiro:checkpoint save task-{N}-done
```
