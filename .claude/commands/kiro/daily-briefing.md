---
description: Daily prioritized status briefing synthesized from projects.md/people.md plus whatever live sources are connected this session (git, GitHub, Jira/Confluence, Slack).
allowed-tools: Skill, Read, Write, Bash, Glob
argument-hint: "(no arguments — always runs against .claude/memory/manager/)"
---

# Daily Briefing

Read `~/.claude/skill-library/synthesizing-daily-briefings/SKILL.md` and follow its
five-phase workflow exactly (the skill lives in the on-demand library, reached by path).

## Step 0 — Preflight

Check `.claude/memory/manager/` exists and contains at least one of `projects.md` /
`people.md`.

- If the directory doesn't exist and this is an **interactive** session: proceed to the
  skill's Phase 1 bootstrap (ask the user whether to start tracking).
- If the directory doesn't exist and this is a **headless/scheduled** run
  (`SDD_HEADLESS=1`): write a one-line note to
  `.claude/reports/daily-briefings/<TODAY>-SKIPPED.md` ("no projects.md/people.md yet —
  run `/kiro:daily-briefing` interactively once to bootstrap") and exit 0. Never
  fabricate projects or people to produce a briefing.

## Step 1 — Run the Skill's Five Phases

Follow `synthesizing-daily-briefings` Phases 1–5 in order: load/bootstrap, gather signal
since last run, filter/tier/dedup, write the briefing, update the ledger.

## Step 2 — Report Location

Write the briefing to `.claude/reports/daily-briefings/<TODAY>.md`, leading with a
`## Summary` section (1-2 lines: P0/P1 count + the single most important item) before
the full Logseq-formatted body — the dashboard's Scheduled Tasks tab reads this heading
as the card's headline. Update `.claude/memory/manager/.last-briefing-run` with the
current timestamp only after a successful write.

## Step 3 — Close Out

Print a 2-line summary: number of P0/P1 items surfaced, and the report file path.
