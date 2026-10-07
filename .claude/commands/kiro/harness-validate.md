---
description: Check structural integrity of the SDD harness — broken references, missing files, convention violations
allowed-tools: Read, Task, Glob, Bash
---

# Kiro Harness Validate — Structural Integrity Check

## Usage

```
/kiro:harness-validate
```

No arguments required. Validates the harness installation in the current project.

## Pre-Check

Verify `.claude/` directory exists. If not, report: "No harness installed. Run install.sh first."

## Deterministic Checks (run first)

Before delegating to the agent, run the hardcoded-path guard and fold its result into the report. This is a hard gate — any violation is a CRITICAL finding:

```
bash .claude/scripts/utils/check-no-hardcoded-paths.sh
```

It scans harness sources — `*.sh`, `*.py`, `*.json` and `*.template`, plus the generated-but-gitignored `.claude/settings.json` by name — for machine/user-specific absolute paths and the hardcoded `$HOME/.claude/sdd-harness` root. Config counts as code: the guard originally scanned only `*.sh`/`*.py` and excluded `.claude/**`, so `.claude/settings.json` was invisible on both counts while holding 23 absolute hook paths. Every path must instead be self-located via `scripts/lib/resolve-harness-dir.sh`, read from `scripts/lib/harness-pointer.sh`, or computed from `$HARNESS_DIR`. If it exits non-zero, report each offending `file:line` and do not issue a "pass" verdict.

The same guard runs from the harness repo's `.git/hooks/pre-commit`, installed by `install.sh` / `update.sh`, so this step should normally already be clean. That hook runs a **second** guard alongside it — `scripts/utils/check-no-regex.py`, which extends the repo-wide regex ban to Python embedded in shell heredocs where ruff's TID251 cannot reach — and blocks the commit if either fails. It is not part of this command's deterministic block, so a stale `scripts/utils/no-regex-debt.txt` entry surfaces at commit time rather than here; run it by hand with `python3 scripts/utils/check-no-regex.py` (or `--list` for violators without a verdict) if you want that answer during validation.

Then run the fleet roster check and fold its result in as a WARNING-level finding (not a gate — an unregistered repo is a roster gap, not structural corruption):

```
bash .claude/scripts/utils/check-fleet-registration.sh
```

It reports repos that carry a harness install but are missing from `projects.txt`, and therefore receive no scheduled routines and appear on no dashboard. Report each `UNREGISTERED:` path.

## Invoke Subagent

Delegate to harness-validate-agent:

```
Task(
  subagent_type="harness-validate-agent",
  description="Validate harness structural integrity",
  prompt="""
Check the SDD harness installation in this project for structural integrity:

1. Verify all command → agent references are valid
2. Verify all agent → template references exist, and validate the settings templates and the live `.claude/settings.json` as strict JSON via `scripts/setup/check-settings-json.sh` (non-zero exit is a blocker — Claude Code drops every permission rule and hook in a malformed settings file without warning)
2b. Run `python3 scripts/setup/reconcile-settings-templates.py --check` (harness repo only). It asserts `hooks(harness template) == hooks(project template) + HARNESS_ONLY`. Non-zero exit is a blocker: drift here means a hook fires in every installed repo but not in the one where it is written and tested, or the reverse. Permissions are excluded on purpose — the two templates *should* differ there. Fix by adding shared hooks to `templates/settings.json.template` and running `--sync`, never by editing the harness template directly.
3. Check memory file caps (hot-memory <50 lines, patterns <70 lines, observations <50 entries)
4. Verify L0 headers on all steering and memory files
5. Check rule file consistency
6. Check cross-artifact spec consistency for completed feature specs (no orphan tasks, no uncovered requirements, no ungrounded design elements)

Report issues by severity and category.
"""
)
```

## Display Result

Show the validation report to the user.

## Append Trace

After receiving result, append to `.claude/memory/trace.log`:
```
YYYY-MM-DD HH:MM | harness-validate | haiku | {outcome} | fast
```

## Notes

- Run this after updating the harness (`update.sh`) to catch any structural drift
- Run this before starting a new spec to ensure the harness is healthy
- This is a read-only operation — it reports issues but does not fix them
- Tier 3 (Haiku) agent — this is mechanical validation work

_Last synced: 2026-09-30_
