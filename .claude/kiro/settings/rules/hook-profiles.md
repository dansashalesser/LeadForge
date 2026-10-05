# Hook Profiles — Graduated Automation Levels

Control which hooks fire based on the `SDD_PROFILE` environment variable. This allows lighter enforcement for prototyping and stricter enforcement for production-bound work.

## Profiles

### minimal
- Git hooks: doc-sync and harness-updater run normally
- Session hooks: stop-hook.sh skips all checks
- Use for: rapid prototyping, exploratory work, small fixes

### standard (default)
- Git hooks: all hooks run normally
- Session hooks: all checks run (harness update check, memory health)
- Use for: normal development workflow

### strict
- Git hooks: all hooks run normally
- Session hooks: all checks run
- Additional: pre-commit verification gate recommended (manual — add `/kiro:verify quick` to your workflow before committing)
- Use for: production-bound code, release preparation

## Configuration

Set the profile via environment variable:
```bash
export SDD_PROFILE=minimal    # lightweight
export SDD_PROFILE=standard   # default (same as unset)
export SDD_PROFILE=strict     # maximum enforcement
```

Or per-session:
```bash
SDD_PROFILE=strict git commit -m "release prep"
```

## Implementation in Hooks

All hook scripts check `SDD_PROFILE` at the top:

```bash
# Profile guard — skip if profile is below threshold
SDD_PROFILE="${SDD_PROFILE:-standard}"
if [ "$SDD_PROFILE" = "minimal" ]; then
  exit 0  # skip this hook entirely
fi
```

Git hooks (post-commit) run at all profile levels — they are infrastructure, not enforcement. Only session hooks (stop-hook.sh) respect the minimal profile skip. The one unconditional exit is the self-commit guard: commits whose subject starts with `docs: auto-sync` (the hook's own doc-sync commits) skip every stage at every profile level.

Note: `post-commit` has a third stage that runs at every profile level — one fully-detached background job runs the doc-sync agent, then the harness-updater agent, then stages, commits, and **pushes** only `*.md` files. `git commit` returns immediately (output goes to `.git/post-commit-docsync.log`), but the job still touches the network and the remote at all profiles, including `minimal`. That stage is serialized on `.git/post-commit-docsync.lock` (atomic `mkdir`) at every profile level: if a previous run still holds the lock, the new run logs `=== skipped <date>: another doc-sync run is active ===` and exits without running the agents, so rapid successive commits never spawn parallel agents that race on the git index. A lock older than 30 minutes is stolen; a normal exit removes it via an `EXIT` trap.

## Defaults

If `SDD_PROFILE` is unset or empty, it defaults to `standard` — preserving current behavior. No existing workflows are affected by this addition.
