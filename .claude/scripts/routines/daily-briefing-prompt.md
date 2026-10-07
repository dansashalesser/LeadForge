You are running the daily briefing routine for this repository. This invocation runs
headlessly (SDD_HEADLESS=1), so treat yourself as non-interactive: never ask a question,
never fabricate an answer to one either.

Today's date: TODAY_PLACEHOLDER

## Task

Read `.claude/commands/kiro/daily-briefing.md` and execute its pipeline end to end.

Critical rules:
1. **Bootstrap check first.** If `.claude/memory/manager/` doesn't exist or contains
   neither `projects.md` nor `people.md`, this is unbootstrapped — write
   `.claude/reports/daily-briefings/TODAY_PLACEHOLDER-SKIPPED.md` with one line
   ("no projects.md/people.md yet — run /kiro:daily-briefing interactively once to
   bootstrap"), print that it was skipped, and exit 0. Do not invent projects or people
   to produce a briefing anyway.
2. Follow `~/.claude/skill-library/synthesizing-daily-briefings/SKILL.md`'s five phases exactly, including the
   Capture Filter (Phase 5) and the evidence-required / dedup-across-sources rules
   (Phase 3).
3. For each source (git, GitHub, Jira/Confluence, Slack), check it's actually reachable
   this session before using it. If unreachable, say so plainly in the briefing rather
   than silently omitting that project/person's updates.
4. Run the skill's Phase 2b stalled-work sweep (`gh pr list --author @me --json ...`)
   even when nothing else changed — stalled work is exactly what a changes-only pass
   misses. Report it under `## Stalled`, or state why the sweep was skipped.
5. Write the dated report to `.claude/reports/daily-briefings/TODAY_PLACEHOLDER.md`.

End with a 2-line summary: number of P0/P1 items, and the report file path.
