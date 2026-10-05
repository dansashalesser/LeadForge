---
description: Sync documentation with code changes — prevents drift in specs, steering, and any .md referencing changed code
allowed-tools: Read, Task, Glob, Bash, Grep
---

# Kiro Sync Docs — Documentation Drift Prevention

## Detect Changes

Collect all changed files (uncommitted + staged + recent commits):

```bash
git diff --name-only          # uncommitted
git diff --cached --name-only # staged
git diff HEAD~1 --name-only   # last commit
```

Combine, deduplicate, and filter out `.md` files and `.claude/` paths. `.claude/` is regenerated output (rebuilt by `install.sh` / `update.sh`) and is gitignored; changes to the harness **source** tree (`agents/`, `commands/`, `hooks/`, `kiro/`, `scripts/`, `rules/`, `templates/`, `skills/`, `CLAUDE.md`) are handled by the harness updater.

If no source files changed, report "No source changes detected — docs are current."

## Invoke Subagent

Delegate documentation sync to doc-sync agent:

Use the Task tool to invoke the Subagent:

```
Task(
  subagent_type="doc-sync",
  description="Sync docs with code changes",
  prompt="""
The following source files have changed:
{changed_files}

Find ALL .md files in the repo that document behavior affected by these changes.
Update only sections directly affected. Do not restructure unrelated sections.
Also check .claude/steering/*.md for new patterns not yet captured.

Skip: .venv/, .git/, __pycache__/
"""
)
```

## Display Result

Show Subagent summary to user:

### Docs Synced:
- Files updated (list)
- Sections changed (brief)
- Steering updates (if any)

### No Changes:
- "No source changes detected — docs are current."

## Notes

- This command catches ALL changes: uncommitted, staged, and recently committed
- The stop-hook runs this automatically at session end — this command is for manual trigger
- The post-commit git hook is a safety net that also triggers doc sync on commit; it runs doc sync, then the harness updater, then auto-commits and pushes **only** the `.md` files they touched — all inside one detached background job, so `git commit` returns immediately and output lands in `.git/post-commit-docsync.log` (each agent bounded by `timeout 900`)
- That background job holds `.git/post-commit-docsync.lock` (created with an atomic `mkdir`) for its whole run. A commit made while a previous run is still active logs `=== skipped <date>: another doc-sync run is active ===` and runs no agents — its changes are picked up by the in-flight run or the next commit. This prevents parallel runs from racing on the git index and dropping each other's commits. A lock left by a killed run is stolen after 30 minutes; clear one by hand with `rmdir .git/post-commit-docsync.lock`
- Excludes .claude/ paths — regenerated output, gitignored (harness updater handles SDD-SETUP-GUIDE separately, keyed off the top-level source tree)
- Excludes .md-only changes to avoid infinite loops
