# SDD Harness — Scheduled Tasks Reference

All scheduled tasks run **locally** via the OS-level scheduler and the daily orchestrator. They work on any machine, have full access to gitignored files (`~/.claude/skills/`, `.claude/memory/`), and require no GitHub App or cloud authentication.

---

## Active Scheduled Tasks

All tasks are wired into `scripts/orchestration/daily-orchestrator.sh`. The orchestrator fires at 18:00 via launchd (macOS) or crontab (Linux), once daily. On WSL/Windows, Task Scheduler (`setup-global-orchestrator.sh`) fires at 18:00 and repeats every 4h for the rest of the day (6x/day total) — each sub-routine self-gates on its own last-run state, so 5 of 6 fires are cheap no-ops. Catch-up runs if the machine was off: the session-start hook fires the per-repo runner in the background if the state file is >24h stale.

Each routine's stderr is captured to a per-run buffer; on a non-zero exit, the buffer is appended to `logs/orchestrator-errors.log` (in addition to the usual one-line summary in `logs/orchestrator.log`) so a failing run leaves a diagnosable trace instead of a silent exit=1.

The orchestrator itself is fail-loud: every non-dry-run invocation logs a `run started (mode=...)` line to `logs/orchestrator.log` and, via an `EXIT` trap, a `run finished exit=<code> repos=<count>` line — so a crash before the repo loop even starts (bad args, missing `projects.txt`) leaves a diagnosable trace in `logs/orchestrator-errors.log` instead of looking identical to a zero-work success.

**Every routine below runs under a stricter behavioural envelope than an interactive session.** All of them invoke `SDD_HEADLESS=1 claude --print --permission-mode bypassPermissions`, which trips `headless-envelope-hook.sh` at `SessionStart`. That injects six constraints — one unit of work; no push, rewrite, or force git; writes confined to report files and `.claude/memory/`; a two-strike loop guard that escalates into the report instead of retrying a third time; honest partial reporting; no new dependencies.

Two of those constraints defer to an explicit instruction in a routine's own prompt, so writing a new routine does not mean fighting the envelope:

- **Committing** is allowed when the routine prompt says to commit. Pushing is not, under any prompt.
- **Writing harness artifacts** (`skills/`, `agents/`, `hooks/`, `.claude/behaviors/`, …) is allowed when the routine prompt names that artifact class as its output — which is how *Bi-Weekly Harness Health* repairs `SKILL.md` files and how *Daily Maintenance* step E drafts `BEHAVIOR.md` specs. Permission for one class never extends to another.

Full text and the `SDD_SKIP_HEADLESS_ENVELOPE=1` per-runner opt-out: [docs/hooks/README.md](../hooks/README.md#headless-envelope-hooksh).

---

### Fleet Harness Sync
**Mechanism:** Inline in `scripts/orchestration/daily-orchestrator.sh` (harness-level section, runs **before** the per-repo runners)
**Cadence:** Once per calendar day. State tracked as a timestamp in `$SDD_HARNESS/.last-harness-sync`; the gate compares day strings (`cut -dT -f1`) rather than using GNU-only `date -d`.
**Scope:** Harness-level — syncs into every registered project (or just `--repo <path>`, which is forwarded to `update.sh`)

**What it does:** Runs `update.sh` so registered projects pick up harness changes with no human step. Nothing else ever ran `update.sh`: `stop-hook.sh` only prints a `Run: update.sh` nudge and then waits for someone to act on it, so a harness fix could sit unapplied in an installed project indefinitely — which is how a `settings.json` broken by an old template survived for months in an installed repo.

Guards:
- **Parse before sync** — `bash -n "$HARNESS_DIR/update.sh"` must pass first. A syntax error in `update.sh` would otherwise be run across the whole fleet; on failure the sync is skipped and the reason is logged to both `logs/orchestrator.log` and `logs/orchestrator-errors.log`.
- **Retry on failure** — `.last-harness-sync` is written only on exit 0, so a failed sync doesn't consume the day's window; stderr is appended to `logs/orchestrator-errors.log`.
- **Dry run** — `--dry-run` prints a `[would-sync]` line and changes nothing.

**Opt-out:** `SDD_SKIP_HARNESS_SYNC=1` env var.

---

### Daily Maintenance
**Runner:** `.claude/scripts/orchestration/daily-runner.sh`
**Prompt:** `.claude/scripts/routines/daily-maintenance-prompt.md`
**Cadence:** Every day (idempotent; skips if already ran today)
**Scope:** Every registered repo

**What it does (five steps, error-isolated):**
- **A** — Judge + Reflect + Housekeep: score the previous day's observations via the session-quality rubric; convert drain entries into memory updates; archive `observations.md` if >50 entries. The interactive `/kiro:daily-maintenance` pipeline runs the judge **three times** and reconciles the deltas by median; the headless routine prompt still writes one `[judge]` observation and defers scoring to step D
- **B** — Session Quality Assessment: collect git activity; score session 1–5; append `[session-quality]` observation
- **C** — Keep Rate Evaluation: find Claude-co-authored commits older than 7 days; compute lines still in HEAD; append `[keep-rate]` observation
- **D** — Trust Score update: run `trust_score.py auto-score` after B and C are written, so all signals (`[session-charge]`, `[memory-gap]`, `[session-quality]`, `[keep-rate]`) are visible to the scorer. `auto-score` is deterministic and submits a **single** sample, so the multi-sample spread gate is skipped and the record reports `"spread": null` — the gate applies only when `apply` is given one `--delta` per judge run
- **E** — Skill Augmentation (Sleep-Phase Knowledge Seeding): invoke `skill-augment-agent` with today's judge verdict. The agent collects `[seed-target:]` observations written by the `action-capture.sh` hook during the Wake phase (failed Bash commands), maps them to skill domains, and loads today's `type: feedback` memories (user corrections) as highest-trust evidence ranked above the LLM judge, generates synthetic worked examples (Dreaming), and applies up to 3 evidence-backed skill improvements. Logs each as `[skill-update]`. Idempotent — skips if `[skill-update]` entries already exist for today. This step closes the full Wake→Sleep cycle: struggles during active sessions automatically become targeted skill updates overnight.

**Channel summary (optional):** after the run, `daily-runner.sh` posts the last ~20 lines of output to your chat channels via `scripts/integrations/channels/notify.py` (title `Daily maintenance — <repo> (exit=<code>)`). No-op unless `~/.env.channels` exists, so the call stays unconditional. Opt out with `SDD_SKIP_CHANNEL_NOTIFY=1`. See [docs/integrations/channels](../integrations/channels/README.md).

**Opt-out:** `rm .claude/scripts/orchestration/daily-runner.sh` in that repo; or `SDD_SKIP_ROUTINE=1` to skip registration at install time.

---

### Daily Security Scan
**Runner:** `.claude/scripts/routines/security-report-runner.sh`
**Cadence:** Every day (`MIN_GAP_DAYS=1`; applies to every repo)
**Scope:** Every registered repo

**What it does:**
- Static security scan of recent git changes using the `ai-security-workflow` skill
- Checks for OWASP patterns (injection sinks, XSS vectors, broken auth), exposed secrets, and unsafe patterns introduced in the last commit window
- Writes a dated report to `.claude/reports/security/<date>-security-report.md`
- Visible in the dashboard **Scheduled Tasks** section with last-run status, artifact diff, and any findings headline
- Race-safe via `mkdir` lock; a lock older than 2h (left by a killed run) is auto-removed on the next run
- Retries automatically on failure — `STATE_FILE` is only written after a successful run (exit 0), so a failed scan doesn't consume the gap-days window; full stdout is also tee'd to `.claude/memory/.last-security-report-output.log` since the orchestrator wrapper that calls this runner redirects its stdout to `/dev/null` and only captures stderr

**Opt-out:** `SDD_SKIP_SECURITY_REPORT=1` env var.

---

### Daily Briefing Synthesis
**Runner:** `.claude/scripts/routines/daily-briefing-runner.sh`
**Command:** `.claude/commands/kiro/daily-briefing.md` (`/kiro:daily-briefing`)
**Prompt:** `.claude/scripts/routines/daily-briefing-prompt.md`
**Cadence:** Every day (`DAILY_BRIEFING_GAP_DAYS`, default 1; force with `DAILY_BRIEFING_FORCE=1`)
**Scope:** Every registered repo

**What it does:**
- Synthesizes a prioritized status digest from `.claude/memory/manager/{projects,people}.md` plus whatever live sources are actually connected this session (git, GitHub, Jira/Confluence, Slack)
- Applies the `synthesizing-daily-briefings` skill's Capture Filter (enables future action / reveals a pattern over time / helps someone else), P0–P3 priority tiers, evidence-required wording (distinguishes a reported concern from a confirmed issue), and dedup across sources
- Writes Logseq-ready markdown to `.claude/reports/daily-briefings/<date>.md` and appends durable findings back to `projects.md`/`people.md` for the next run
- **Deterministic (no LLM call) ledger-bootstrap guard**: the ledger itself is only ever created interactively via `/kiro:daily-briefing`'s Phase 1 — if neither `projects.md` nor `people.md` exists yet, the runner writes a `<date>-SKIPPED.md` note directly and exits 0 rather than asking a headless session to invent a briefing from nothing
- Race-safe via `mkdir` lock; a lock older than 2h (left by a killed run) is auto-removed on the next run
- Retries automatically on failure — the state file is only written after a successful run (exit 0); full stdout is also tee'd to `.claude/memory/.last-daily-briefing-output.log` since the orchestrator wrapper that calls this runner redirects its stdout to `/dev/null` and only captures stderr

**Opt-out:** `SDD_SKIP_DAILY_BRIEFING=1` env var.

---

### Risk-Zone Reseed
**Runner:** `.claude/scripts/routines/risk-zone-reseed-runner.sh`
**Prompt:** `.claude/scripts/routines/risk-zone-reseed-prompt.md`
**Cadence:** Weekly (`RISK_ZONE_GAP_DAYS`, default 7; force with `RISK_ZONE_FORCE=1`) — zones drift slowly, unlike the daily briefing's cadence
**Scope:** Every registered repo

**What it does:**
- Refreshes `.claude/steering/risk-zones.md`, the single data source consumed by `risk-zone-edit-gate-hook.sh` (edit-time gate) and `pr-risk-tier-hook.sh` (PR label tier)
- Scores every file changed in the last 90 days on three signals: **churn** (commit count), a **test-coverage proxy** (does a matching `*.test.*`/`test_*` file exist), and **blast radius** via `gitnexus impact --direction upstream` on the file's top-level symbol when the `gitnexus` CLI is installed — skipped gracefully, and noted `(impact: not scored)`, when it isn't
- Assigns each file a zone: **red** (HIGH/CRITICAL impact, or high churn with no test file), **yellow** (MEDIUM impact, or moderate churn with a test file), **green** (everything else)
- Merges with the existing map rather than overwriting it — any row with a trailing `<!-- pinned -->` comment keeps its human-assigned zone regardless of the freshly computed score
- Race-safe via `mkdir` lock (stale locks older than 2h auto-removed); skips with no write if `.claude/memory/` doesn't exist or the directory isn't a git repo; the cadence state file is only updated after a successful (exit 0) run

**Opt-out:** `SDD_SKIP_RISK_ZONE=1` env var.

---

### Startup Payload Audit
**Runner:** `.claude/scripts/routines/startup-payload-audit.sh`
**Cadence:** Every day (own state-file guard `.claude/memory/.last-startup-payload-audit`; deterministic — no LLM call)
**Scope:** Every registered repo

**What it does:**
- Measures the **fixed per-session token tax** — the layered `CLAUDE.md` + `@imports` + `.claude/rules/*` + auto-loaded `MEMORY.md` that load before you do anything. RTK/lean-ctx/Headroom only reduce *runtime* cost and never see this startup payload.
- Estimates per-file tokens and age, flags files over the budget (`SDD_STARTUP_PAYLOAD_BUDGET`, default 8000), stale files (`SDD_STARTUP_STALE_DAYS`, default 45), and **ghost references** (`@paths` in CLAUDE.md that don't resolve)
- **Ratchet (2026-09-30):** keeps a per-repo ceiling in `.claude/reports/context/startup-payload-ceiling.json` that only goes down. A run at or under the ceiling lowers it to the new total; a run above it is flagged `over_ceiling` with a `delta`, a warning goes to stderr, and the ceiling stays put. The fixed budget still applies as an absolute cap; the ceiling catches creep below it. Accept intended growth with `--rebaseline`. A corrupt ceiling file fails the run rather than silently rebaselining
- Writes `.claude/reports/context/startup-payload.json`
- Surfaced in the dashboard **Budget & Efficiency → Context Health** tab as a Startup Payload card (tokens, ceiling with its delta vs the last run, budget status, stale/ghost counts, top files) and as a card on the **Scheduled Tasks** tab. The ceiling tile reads `—` / "ceiling (not yet set)" for reports written before the ratchet existed, and the card turns amber with an `above ceiling` badge when `over_ceiling` is set
- Enforces the harness's own "read on demand, not upfront" rule by measuring whether it's actually followed. See the `context-optimization` skill (Startup vs Runtime axis).

**Opt-out:** `SDD_SKIP_STARTUP_AUDIT=1` env var. Force a run with `--force`; accept growth with `--rebaseline`. Tests: `scripts/routines/startup-payload-audit.test.sh`.

---

### RTK Net-Effect
**Runner:** `.claude/scripts/routines/rtk-net-effect-runner.sh`
**Cadence:** Every day (own state-file guard `.claude/memory/.last-rtk-net-effect-run`; deterministic — no LLM call)
**Scope:** Every registered repo

**What it does:**
- Wraps `.claude/scripts/utils/rtk-net-effect.py`, which measures RTK's **global** effect from `~/.claude/projects/**/*.jsonl`, not just its local savings: exact-match Bash rerun rate and Read reread rate within the same session — RTK's own figure is local savings on one command's output, and cannot see whether the agent, missing detail it needed, reran the command or re-read the file later
- Writes `.claude/memory/rtk-net-effect.json`; on no data in the lookback window, leaves the prior snapshot in place rather than overwriting it with an empty one
- Read by the dashboard's RTK layer note (Headroom tab), which appends both rates alongside its existing savings figure instead of reporting savings alone
- Not a Scheduled Tasks tab card — it feeds the RTK layer note directly, not `_scheduled_task_registry()`

**Opt-out:** `SDD_SKIP_RTK_NET_EFFECT=1` env var. Lookback window: `SDD_RTK_NET_EFFECT_DAYS` (default 30).

---

### Weekly Hook/MCP-Config Audit
**Runner:** `.claude/scripts/routines/hook-config-audit-runner.sh`
**Cadence:** Weekly (`HOOK_CONFIG_AUDIT_GAP_DAYS`, default 7; deterministic — no LLM call)
**Scope:** Every registered repo

**What it does:**
- Wraps `.claude/scripts/utils/hook-config-audit.py`, which sweeps a repo's own `.claude/hooks/` and `settings.json`/`settings.local.json`/`.mcp.json` — the harness's own hooks and MCP config, not user content — for three findings: (1) secrets, by shelling out to `scan-pii.sh`'s OPF engine per hook/config file; (2) network-exfil patterns, `curl`/`wget` calls in hook scripts to a host outside a small allowlist; (3) over-broad permission grants, a `permissions.allow`/`deny` entry with no scoping argument or an argument that is just `*`
- Neither `hooks/claude/scan-pii.sh` (content-only) nor `hooks/claude/skill-permissions-gate.sh` (fires only on new `SKILL.md` writes) sweeps existing hooks/config, so nothing previously re-checked them as the hook count grew
- Writes `.claude/reports/security/hook-config-audit.json`
- Wired into `orchestration/daily-orchestrator.sh` `run_one()`

**Opt-out:** `SDD_SKIP_HOOK_CONFIG_AUDIT=1` env var.

---

### Macro-Eval Sweep
**Runner:** `.claude/scripts/routines/macro-eval-runner.sh`
**Prompt:** `.claude/scripts/routines/macro-eval-prompt.md`
**Cadence:** ~Twice weekly (MIN_GAP_DAYS=3; override with `MACRO_EVAL_GAP_DAYS`)
**Scope:** Every registered repo (no-ops if Raindrop MCP is unreachable)

**What it does:**
- Pulls Raindrop Workshop traces from the last 4 days
- Clusters failure patterns by type and impact
- Writes a dated report to `.claude/reports/macro-evals/YYYY-MM-DD.md`
- Posts annotations to Workshop for top failing patterns
- Race-safe via `mkdir` lock; a lock older than 2h (left by a killed run) is auto-removed on the next run

**Opt-out:** `SDD_SKIP_MACRO_EVAL=1` env var; or preflight writes a `*-SKIPPED.md` report if MCP unreachable.

---

### Tool-Failure Review
**Runner:** `.claude/scripts/routines/tool-failure-review-runner.sh`
**Command:** `.claude/commands/kiro/tool-failure-review.md` (`/kiro:tool-failure-review`)
**Cadence:** ~Twice weekly (MIN_GAP_DAYS=3; override with `TOOL_FAILURE_GAP_DAYS`; force with `TOOL_FAILURE_FORCE=1`)
**Scope:** Every registered repo (no-ops unless the ledger has a promotable signature)

**What it does:**
- Reads the per-repo tool-failure ledger `.claude/memory/tool-failures.jsonl` (populated by the `tool-failure-capture.sh` PostToolUseFailure hook)
- Promotes recurring, understood failures (count ≥ `TOOL_FAILURE_MIN_COUNT`, default 3, open, not yet promoted) into durable memory + `ERRORS.md`, diagnosing *why* the command shape keeps failing
- Marks promoted entries resolved so the `tool-failure-recall.sh` PreToolUse hook stops warning about them
- Self-paces via MIN_GAP_DAYS and a `mkdir` lock; pre-flights that the `claude` CLI is on PATH before running; a lock older than 2h (left by a killed run) is auto-removed on the next run

This is the **review** stage of the tool-failure-memory loop (capture → recall → review). See the `tool-failure-memory` skill.

**Opt-out:** `SDD_SKIP_TOOL_FAILURE_REVIEW=1` env var.

---

### Code-Review Learning Sweep
**Runner:** `.claude/scripts/routines/code-review-learning-runner.sh`
**Prompt:** `.claude/scripts/routines/code-review-learning-prompt.md`
**Cadence:** Weekly (`CODE_REVIEW_LEARNING_GAP_DAYS`, default 7; force with `CODE_REVIEW_LEARNING_FORCE=1`)
**Scope:** Every registered repo (no-ops unless there's a merged PR with a logged automated review not yet processed)

**What it does:**
- Discovers merged PRs with a logged `.claude/memory/pr-reviews/pr-<n>.md` (written by `scripts/pr/log_review.sh` via the `gitnexus-pr-review` skill, backgrounded from `scripts/pr/detect_base_and_create.sh` when the PR is created) not yet processed, via `gh pr view --json state`. `detect_base_and_create.sh` now reads the new PR's number back from `gh pr list --json number` instead of parsing it out of the printed URL — the URL is free text, the number is a field — and writes a placeholder `## Evidence` section into the PR body stating that no before/after probe was run, since this path fires headless on `git push` where none can be captured. `pr-evidence-hook.sh` cannot cover it: the `gh pr create` here runs inside the script, not as a Bash tool call, so no `PreToolUse` event fires. If the number cannot be read back, the script says so and exits without inventing one
- For each: diffs the logged review against real human review activity (`gh api .../comments`, `.../reviews`) to find **missed** flags, **false positives**, or **convention gaps**
- **Low-risk** findings (team conventions, dismissed-flag patterns) are written directly into `.claude/memory/` as `project`/`feedback` facts
- **Higher-risk** findings (changes to the `code-reviewer` skill's methodology) are never auto-applied — only reported to `docs/code-review-learning-report.md` for human approval
- Race-safe via `mkdir` lock; marks processed PRs in `.claude/memory/.code-review-learning-processed` only on a successful run; a lock older than 2h (left by a killed run) is auto-removed on the next run

**Opt-out:** `SDD_SKIP_CODE_REVIEW_LEARNING=1` env var.

---

### Weekly Skill-Curator Sweep
**Runner:** `.claude/scripts/routines/skill-curator-runner.sh`
**Prompt:** `.claude/scripts/routines/skill-curator-prompt.md`
**Cadence:** Weekly (MIN_GAP_DAYS=7; override with `SKILL_CURATOR_GAP_DAYS`; force with `SKILL_CURATOR_FORCE=1`)
**Scope:** Harness repo only (exits 0 in non-harness repos via `docs/scheduled-tasks/` guard)

**What it does:**
0. **Forward-pattern intake** — reads `.claude/memory/forward-patterns.md` from every registered repo; surfaces confidence 4–5 entries as incorporate/promote/defer candidates; included in the curation report
1. **Skill quality audit** — scores all `~/.claude/skills/*/SKILL.md` against four SkillOS dimensions; flags low-quality candidates and duplicate pairs
2. **Description budget audit** — measures description field length; flags >150 chars for compression
3. **Memory governance health** — checks five compaction-discipline hook failure modes
4. Writes `reports/skill-curation-report.md` (full weekly snapshot, replaced each run)
- Race-safe via `mkdir` lock; a lock older than 2h (left by a killed run) is auto-removed on the next run
- Retries automatically on failure — `STATE_FILE` is only written after a successful run (exit 0), so a failed sweep doesn't consume the gap-days window; full stdout is also tee'd to `.claude/memory/.last-skill-curator-output.log` since the orchestrator wrapper that calls this runner redirects its stdout to `/dev/null` and only captures stderr

**How to use:** After the routine runs, invoke `/skill-curator` locally to review findings and apply approved changes (merge/compress/delete) with human approval at every step. Alternatively, in the dashboard's companion mode, use the **Skill Changes** tab's "🔍 Analyze & Propose" / "✅ Apply Approved" buttons — propose writes a numbered proposal to `.claude/memory/.skill-curator-proposal.md` via a headless `claude --print` session (logged to `logs/skill-curator-propose.log`, polled every 3s by the dashboard); apply backs up `~/.claude/skills/` to `.dashboard/skill-backups/skills-<timestamp>.tar.gz` first, then executes the approved subset per the typed instruction (default `"apply all"`), logs to `logs/skill-curator-apply.log`, appends the curation log entry to `reports/skill-curation-report.md`, and deletes the pending proposal. A "🔍 Re-analyze" button re-runs propose once a proposal is showing.

**Opt-out:** `SDD_SKIP_SKILL_CURATOR=1` env var.

---

### Bi-Weekly Harness Health
**Runner:** `.claude/scripts/routines/harness-health-runner.sh`
**Prompt:** `.claude/scripts/routines/harness-health-prompt.md`
**Cadence:** Bi-weekly (MIN_GAP_DAYS=13; override with `HARNESS_HEALTH_GAP_DAYS`; force with `HARNESS_HEALTH_FORCE=1`)
**Scope:** Harness repo only (exits 0 in non-harness repos via `docs/scheduled-tasks/` guard)

**What it does:**
1. **CLAUDE.md review** — reads all repos in `$SDD_HARNESS/projects.txt`; audits for stale instructions, model-assumption drift, and over-constraining rules from pre-Claude-4.x habits; rates each repo `clean` / `minor` / `needs-update`; writes `docs/claudemd-review-report.md`. This is the *harness-wide* pass. Its *per-repo* counterpart is the `/claudemd-review` global command (`commands/global/claudemd-review.md`), which `session-start-hook.sh` fires when a single repo's `.claude/memory/.last-claudemd-review` is >14 days stale; that command audits only the current repo (adding a 200-line size budget, an "inferable from the manifest" filter, and an `@AGENTS.md` import/dedup check) and writes `.claude/memory/claudemd-review-report.md` — do not confuse the two report paths.
2. **Iterative skill repair** — reads `reports/skill-curation-report.md` for low-quality flags; applies a Review→Repair→Validate loop (up to 3 skills per run, max 3 repair iterations per skill); writes repaired `SKILL.md` files directly; appends a `## Iterative Repair Run — [date]` section to the curation report
3. **Token spend attribution** — the runner executes `scripts/utils/token-forensics.py --days 14` itself and substitutes the output into the prompt's `FORENSICS_PLACEHOLDER`, then has the session read it via the `auditing-token-spend` skill and name **one** cause. Catches harness overhead — routine cadence, agent fan-out width, an unbounded tool injecting large output early — before a usage limit does. Appends a `## Token Spend — [date]` block to `reports/harness-health-report.md`. Reports only what is anomalous: a stable profile is a one-line "no change", because a phase that always finds a problem stops being read. It changes no code and no cadence.
- The script is run by the **runner**, not by the model. A headless session merely *told* to invoke a script can skip it silently, and the phase would then report on nothing while appearing to have run. A missing script or non-zero exit is substituted as a visible marker so the phase can say "no data" but can never fabricate figures.
- The automation split in that output carries a `method` label. It currently reads `proxy (sessions under 5min)` because `isSidechain` is never `True` in this transcript format — subagent turns are not separable, so the figure is a stand-in and is labelled as one. It is never reported as a measured 0%.
- Race-safe via `mkdir` lock; a lock older than 2h (left by a killed run) is auto-removed on the next run

**How to use:** `git pull` after the routine runs, then read `docs/claudemd-review-report.md` and the Token Spend block in `reports/harness-health-report.md`. Stalled skills in the repair report need manual intervention — invoke the relevant skill locally with domain context the automated run couldn't supply. For an on-demand spend audit between runs, invoke `auditing-token-spend` directly.

**Opt-out:** `SDD_SKIP_HARNESS_HEALTH=1` env var.

---

### Drift Review
**Mechanism:** Inline in `scripts/orchestration/daily-orchestrator.sh` (harness-level section)
**Cadence:** Gated on elapsed days since the last **successful** run (`DRIFT_REVIEW_GAP_DAYS`, default 7), not day-of-week — a day-of-week gate can only ever fire on Wednesday, so a machine asleep/logged-out through every trigger window that day silently loses the whole week; an elapsed-days gate is self-healing, firing on whichever day the orchestrator next actually runs, if due. State tracked as a timestamp in `$SDD_HARNESS/.last-drift-review`. The elapsed-days math is done with `python3` (`datetime.date.fromisoformat`), not `date -d` — `date -d` is GNU-only, so on macOS the epoch lookup always failed, the comparison was skipped, and this "weekly" review fired a full `claude --print` session on **every** orchestrator run.
**Scope:** Harness-level (not per-repo)

**What it does:** Invokes the `repo-drift-review` skill to sweep the SDD harness for drift. Auto-fixes what it can. Writes `$SDD_HARNESS/reports/drift-review-report.md` — the report moved out of `docs/` into the harness's git-ignored `reports/` directory, so a generated sweep no longer lands in tracked documentation or gets picked up by doc-sync. The state file is only updated on a successful run (exit 0); both stdout and stderr are captured and, on failure, appended to `logs/orchestrator-errors.log`.

---

### Fleet Registration Check
**Mechanism:** Inline in `scripts/orchestration/daily-orchestrator.sh`, immediately after the per-repo loop — full-fleet runs only, skipped under `--dry-run` and under `--repo <path>`
**Cadence:** Every fleet run (no gap-days gate — it is a cheap directory scan, no LLM call)
**Scope:** Harness-level

**What it does:** Runs `scripts/utils/check-fleet-registration.sh --quiet` to find repos that carry a harness install (`.claude/scripts/orchestration/daily-runner.sh`) but are absent from `projects.txt`. Such a repo receives zero routines and appears nowhere: the dashboard only renders repos it is told about, so an unregistered repo is not *shown as failing*, it is simply not shown — absence is invisible unless something looks for it, so it is looked for on every fleet run.

Scan roots are **derived** from `projects.txt` (the parent directory of each registered repo) rather than stored anywhere, so `projects.txt` remains the only file on disk that records a fleet path.

**Never a gate.** On a finding it logs a one-line `orchestrator: unregistered harness repo(s) found` summary to `logs/orchestrator.log` and the full detail to `logs/orchestrator-errors.log`; the orchestrator's own exit code is unaffected. Run it by hand with `bash scripts/utils/check-fleet-registration.sh`.

---

## OS Scheduler Setup

| OS | Scheduler | Registered by | Remove with |
|---|---|---|---|
| **macOS** | launchd LaunchAgent | `install.sh` / `update.sh` | `launchctl unload -w ~/Library/LaunchAgents/com.sdd.daily-orchestrator.plist` |
| **WSL / Windows** | Windows Task Scheduler | `install.sh` / `update.sh` | `schtasks.exe /Delete /TN "SDD Daily Orchestrator" /F` |
| **Linux** | crontab | `install.sh` / `update.sh` | `crontab -l \| grep -vF sdd-daily-orchestrator \| crontab -` |

Registration is automatic and idempotent — re-running `install.sh` or `update.sh` is safe.

**macOS — sleep guard.** The LaunchAgent's `ProgramArguments` wraps the orchestrator in `caffeinate -i` (`/bin/bash -lc "caffeinate -i <orchestrator>"`), so the unattended 18:00 fire is not cut short by idle or display sleep partway through the run. `caffeinate` is a built-in macOS binary — no third-party app and no `sudo`.

### Preflight — registration is not execution

Each setup script now proves the orchestrator can actually *run* under its scheduler, instead of trusting that registration succeeded. `launchctl load` returning 0 only means the job was registered: with the harness under `~/Documents`, launchd (which holds no Full Disk Access) was refused at exec time with `Operation not permitted`, exiting 126 every day for four days while setup reported success and `launchctl list` showed the job present.

| OS | Preflight | On failure |
|---|---|---|
| **macOS** | Registers a throwaway probe LaunchAgent (`com.sdd.orchestrator-preflight`) that runs `daily-orchestrator.sh --dry-run` in the same launchd context, waits up to 30s for its exit code, then boots it out | **exit 1**. Names TCC explicitly when `$HARNESS_DIR` is under `~/Documents`, `~/Desktop` or `~/Downloads`, and gives both fixes: move the harness somewhere unprotected (e.g. `~/GitHub/`) and re-run `install.sh`, or grant Full Disk Access to `/bin/bash` in System Settings → Privacy & Security |
| **Linux** | Runs `--dry-run` under an approximated cron environment (`env -i`, `PATH=/usr/local/bin:/usr/bin:/bin`) — cron's near-empty environment is its version of the same silent failure | **exit 1**, printing the captured stderr and the usual cause (a command only on PATH for interactive shells) |
| **WSL / Windows** | Runs `--dry-run` through the same `wsl.exe -d <distro> -- bash -lc` path the scheduled task uses | **warn only** — the matching check would verify Windows-side execution via `schtasks /Run` + `LastTaskResult`; that is not implemented because it could not be tested, and gating installs on untested Windows behaviour is worse than reporting. Verify by hand with the `/Query` line the script prints |

The preflight also runs on the **"already registered / already loaded"** path, not just on fresh registration — that is exactly the state a silently-dead job reports.

TCC grants are per-machine and never travel with a clone, so a fleet can be silently dead on a brand-new machine behind a green install. That is the case this exists to catch.

The dashboard's **Scheduled Tasks** tab shows live status for each task, scoped to whichever repo's dashboard is open: schedule, last run + exit code, artifact path, diff vs. previous run, and the reasoning excerpt from the artifact. Harness-only routines (skill-curator, harness-health) always show the harness's own state regardless of which repo is open; per-repo routines show that repo's state file and log entries. It also surfaces the OS scheduler's last-launch exit status — and when that scheduler cannot run, it renders as a **full-width red banner above the routine cards**, naming TCC explicitly on exit 126. That status used to be small yellow text inside the scheduler card, beside a page of routines each showing a calm `PENDING` badge; a scheduler that cannot run is the one fact that invalidates everything under it.

---

## Adding a New Scheduled Task

1. Create `scripts/routines/<name>-prompt.md` — prompt template with `TODAY_PLACEHOLDER`
2. Create `scripts/routines/<name>-runner.sh` — copy the `scripts/routines/macro-eval-runner.sh` pattern; set `MIN_GAP_DAYS`; add a harness guard (`docs/scheduled-tasks/`) if harness-only
3. Add a call block in `run_one()` in `scripts/orchestration/daily-orchestrator.sh` with a `SDD_SKIP_<NAME>` opt-out guard
4. Add a `chmod +x` line in **both** `install.sh` and `update.sh` (the runner loop lists every `*-runner.sh` by name)
5. Add an entry to `_scheduled_task_registry()` in `scripts/utils/dashboard.py` so the routine shows up as a card on the dashboard's **Scheduled Tasks** tab (set `runner_log_token` to match how the orchestrator logs it)
6. Sync both to `.claude/scripts/`: `cp scripts/routines/<name>-* .claude/scripts/routines/`
7. Document it in this file

---

_Last synced: 2026-09-30_

