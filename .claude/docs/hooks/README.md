# SDD Harness — Hooks Reference

All hooks live in `.claude/hooks/` and are wired in `.claude/settings.json`.
Hook output is injected into Claude's context as system messages — Claude reads it before acting.

> Hook commands in both settings templates are written as `bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/<name>.sh"` — absolute when Claude Code exports `CLAUDE_PROJECT_DIR`, CWD-relative otherwise, machine-specific never. `templates/settings.harness.json.template` used to carry a `{{HARNESS_DIR}}` placeholder that `install.sh`/`update.sh` substituted with the install-time absolute path; moving the harness (or cloning onto a machine where the path differs) left every hook path dead and failing silently. Both installers now copy the template verbatim instead of running `sed` over it.

> `.claude/settings.json` must be strict JSON: no comments, nothing after the closing brace. Claude Code drops a malformed file whole and silently, so **every** hook below stops firing while the session looks normal. Keep notes in the `.claude/settings.notes.md` sidecar, and check a file with `scripts/setup/check-settings-json.sh .claude/settings.json`. `install.sh` and `update.sh` validate the templates and repair legacy comment-broken files automatically.

**Hook types used in this harness:**

| Type | When it fires |
|---|---|
| `SessionStart` | Once, before the first user message in each session |
| `Stop` | Once, when Claude finishes responding (end of turn) |
| `UserPromptSubmit` | Before each user prompt is submitted |
| `PreToolUse` | Before a specific tool is invoked |
| `PostToolUse` | After a specific tool returns |
| `PostToolUseFailure` | After a specific tool returns an error |
| `SubagentStart` | When a subagent is spawned — fires **inside the child**, matcher is the agent type |
| `PreCompact` | Before context compaction summarizes the conversation |

> **Not every event injects stdout.** Claude Code adds plain-text stdout as context only for `UserPromptSubmit`, `UserPromptExpansion`, `SessionStart`, and `PostModelSwitch`. For every other event, stdout goes to the debug log and Claude never sees it, and so does stderr on exit 0. Two consequences worth knowing before writing a hook: `SubagentStart` must emit JSON `hookSpecificOutput.additionalContext` (the `cat << 'RULES'` pattern used by most hooks here is silently discarded), and a `PostToolUse` warning must **exit 2** to reach Claude at all — the tool has already run, so exit 2 warns without blocking.

---

## Active Event Hooks

### `session-start-hook.sh`
**Event:** `SessionStart` — **Matcher:** _(all sessions)_

**Purpose:** On macOS, first clears `com.apple.macl` extended attributes from `.claude/hooks/` so that hook files modified by Claude Code's Write/Edit tools remain executable by subprocesses. (The Write/Edit tools set `com.apple.macl`, which blocks subsequent subprocess reads. `session-start-hook.sh` itself is immune — `update.sh` always refreshes it via `cp`, not the Write tool.) Next, reads `$HOME/.sdd-harness-root` — the single stored pointer to the harness (see `scripts/lib/harness-pointer.sh`). If that file is set but names a directory that no longer exists, the harness has moved and every cross-repo hook on the machine is inactive, so the hook prints `[HARNESS-POINTER-STALE]` naming the dead path and the fix (`bash <harness>/update.sh`) rather than degrading silently. Then self-heals `.claude/settings.json`: if the file exists and `scripts/setup/repair-settings-json.py` is reachable via that pointer, it runs the repair against the current project on **every** session start. This runs before the memory-bootstrap checks below, because a malformed settings file needs fixing whether or not the repo has memory yet; it is idempotent and cheap, so healthy files are read and left alone. Next, a headroom routing self-heal: `headroom-setup.sh` wires `ANTHROPIC_BASE_URL` into `~/.claude/settings.json` only after confirming the proxy is healthy, but nothing un-wires it if the proxy dies afterward, and headroom's own SessionStart hook re-asserts that routing every session, so a manual unset/edit doesn't stick. This hook runs `scripts/utils/headroom-unwire-if-dead.py` against `~/.claude/settings.json` (bounded ~1.5s connect probe, so a dead proxy adds no noticeable session-start latency) and it strips `ANTHROPIC_BASE_URL` when the routed host refuses the connection; a healthy or unrouted proxy is a silent no-op. Then ensures the daily maintenance pipeline doesn't go unrun: checks whether today's `[judge]` sentinel exists in `observations.md`. If the local `daily-runner.sh` is installed and its state file is stale (>24h or missing), fires the runner in the background without blocking session start. If no local runner is installed and maintenance is overdue, injects a reminder for Claude to run `/kiro:daily-maintenance` interactively. Also checks if the per-repo CLAUDE.md review is >2 weeks stale (`.claude/memory/.last-claudemd-review`) and asks Claude to run `/claudemd-review` if so. Finally, checks for a `.claude/memory/.steering-bootstrap-pending` sentinel (dropped by `install.sh` on fresh project installs with no steering files): if the sentinel exists and `.claude/steering/` still has no `.md` files, injects `[STEERING-BOOTSTRAP-DUE]` prompting Claude to run `/kiro:steering` now. Claude removes the sentinel after steering completes.

**Why it's needed:** The Task Scheduler fires at 11:30 IST daily, but the machine may be off or the WSL session closed at that time. The session-start hook is the catch-up path that guarantees maintenance runs at least once per developer day, with zero user friction. The steering bootstrap check ensures `/kiro:steering` runs in a real interactive session where the interview can happen — `install.sh` cannot run it directly from the shell.

**Output / side effect:**
- Nothing, if maintenance is current and no bootstrap pending (happy path)
- `[SDD-MAINTENANCE-CATCHUP]` — silently fires `daily-runner.sh` in background via `nohup`
- `[SDD-MAINTENANCE-DUE]` — injected reminder to run `/kiro:daily-maintenance` (no local runner case)
- `[CLAUDEMD-REVIEW-DUE]` — injected reminder to run `/claudemd-review` (>2 weeks stale); the command is the per-repo global command `commands/global/claudemd-review.md`, which audits the current repo's `CLAUDE.md`/`AGENTS.md`, writes `.claude/memory/claudemd-review-report.md`, and stamps `.claude/memory/.last-claudemd-review`
- `[STEERING-BOOTSTRAP-DUE]` — injected prompt to run `/kiro:steering` (fresh install, no steering files)
- `[SETTINGS-REPAIRED]` — emitted only when the repair actually changed something (the `OK ` no-op line is filtered out). Followed by a note that Claude Code parsed `settings.json` *before* the repair ran, so that session's permission rules and hooks stay inactive and return at the next session start. Nothing is printed for a healthy or absent `settings.json`.
- `[HARNESS-POINTER-STALE]` — `~/.sdd-harness-root` points at a directory that does not exist. Names the dead path and the fix (`bash <harness>/update.sh`). Nothing is printed when the pointer is absent (harness never installed globally) or valid.
- `[HEADROOM-SELFHEAL]` — emitted only when `ANTHROPIC_BASE_URL` was actually stripped because the routed headroom proxy refused the connection. Nothing is printed when routing is healthy, absent, or not pointed at a local proxy.
- `[SESSION-HANDOFF-AVAILABLE]` — if `.claude/memory/handoff/latest.md` exists and is <24h old, injects a reminder to silently read it before responding to the user's first message. The file is written by `scripts/session/write_handoff.py`, fired from `compaction-discipline-hook.sh` (PreCompact) and `gbrain-agent-spawn.sh` (PreToolUse Agent) — this hook only surfaces it, it never writes it.

**Respects:** `SDD_PROFILE=minimal` env var — skips entirely in minimal profile.

---

### `stop-hook.sh`
**Event:** `Stop` — **Matcher:** _(all sessions)_

**Purpose:** Nine end-of-session health checks, all lightweight:

1. **Harness update check** — compares the harness repo’s latest commit timestamp to `.claude/.last-harness-check`. Prints a `Run: update.sh` nudge if the harness has changes since last install.
2. **Memory health** — counts entries in `observations.md`. If >50, suggests `/kiro:housekeeping` to prune before the file bloats.
3. **Session signal detection** — runs `scripts/session/detect_reexplanation.py` on the session transcript in two passes (Haiku-based LLM). Drain pass: phrases like "I already told you", "you’re doing it again" → appends a `[memory-gap]` observation. Charge pass: unambiguous approval like "that’s perfect", "great work" → appends a `[session-charge]` observation. Both are written at most once per calendar day. The detector classifies through `claude --print` (the user's subscription), not the `anthropic` SDK, so it runs on `python3` and stdlib — the hook no longer routes it through `.venv-tools`. Because that is a nested headless session which fires this same `Stop` hook when it ends, the whole check is skipped when `SDD_HEADLESS=1` (set by the detector itself and by `scripts/routines/*-runner.sh`); that guard is what prevents recursion, and it also keeps routine sessions — which have no user in them to re-explain anything — out of the measurement. Failures are **not** swallowed: the detector emits a `[detector-down]` observation and exits 4, and the run is passed `--record-metric` so the drain count (including a measured zero) lands in `.claude/memory/metrics.jsonl`. The once-per-day idempotency guard matches either a `[memory-gap]` or a `[detector-down]` line for today, so a failed detector is not retried all day.
4. **Agent failure pattern** — scans `trace.log` for 3+ consecutive failures for the same agent type. Surfaces a `/kiro:evolve` nudge to investigate the friction pattern.
5. **Session depth tracking** — appends an ISO timestamp to `.claude/memory/.session-history`, keeping the last 30 entries. This file is read by the dashboard’s **Context Health** section to show sessions/week, a sessions/day trend chart, and tips for `/compact` and subagent delegation.
6. **Setup sequence capture** — reads `.claude/memory/.setup-session-buffer.log` (populated during the session by `setup-buffer-hook.sh`). If ≥2 setup commands were accumulated, appends them as a dated `bash` code block under `## <project> — <date>` in `.claude/memory/setup-knowledge.md` (creating the file if needed), then clears the buffer. Threshold of 2 prevents trivial one-off installs from polluting the knowledge file.
7. **learnings.jsonl promoter** — deterministic fallback for `reflect-agent`'s Step 6 (a prose instruction that attention decays on, so the file was never actually created). Once per day, ranks today's `observations.md` entries by tag priority (`judge` > `skill-update` > `skill-update-flagged`/`-repair` > `seed-target` > `memory-gap` > `loop-debt` > `stale-action-item`, else skipped as noise) and appends the top-ranked one as a JSON line to `.claude/memory/learnings.jsonl`. Skips if `reflect-agent` already wrote a curated entry for today — the human/LLM-curated entry always wins.
8. **Stale action-item escalator** — parses `.claude/memory/action-items.md`’s `- [ ] <desc> | due:YYYY-MM-DD` format and, once per day, appends a `[stale-action-item]` observation for the most-overdue item. Action-item due-dates were never mechanically checked before this — items sat silently past due until a human happened to re-read the file.
9. **Cache-cost dominance nudge** — reads `transcript_path` from the Stop hook’s stdin JSON, parses the transcript for `cache_read_input_tokens`/`cache_creation_input_tokens`/`input_tokens`/`output_tokens` and any `isCompactSummary`/`compactMetadata` marker. If cache tokens are ≥70% of the session total *and* the session compacted at least once, appends a `[cache-cost]` observation (once per day) and automatically invokes `scripts/session/write_handoff.py --trigger cache-cost` to write a resumable handoff snapshot to `.claude/memory/handoff/latest.md` — unconditionally, not gated on the user noticing a warning.

**Why it’s needed:** Session-end is the only consistent window to look back at what happened without adding latency to the conversation. These checks surface problems that accumulate across sessions rather than within them. Session depth tracking gives the dashboard a lightweight signal for context load without requiring transcript analysis. Setup sequence capture ensures environment bootstrap steps are never lost — they are captured automatically without requiring Claude to remember to save them. The learnings promoter and stale-action-item escalator close two gaps where memory files existed in spec/skill instructions but had no deterministic writer, so they sat empty indefinitely.

**Output / side effect:**
- Text nudges printed to Claude’s context (harness update, housekeeping)
- Appends `[memory-gap]` drain entries to `observations.md`, or a `[detector-down]` line when the detector could not run (skipped entirely under `SDD_HEADLESS=1`)
- Appends one `memory-gap` record per day to `.claude/memory/metrics.jsonl` via `scripts/session/record_metric.py` — a measured zero, so the dashboard can tell it apart from "never measured"
- Appends `[session-charge]` charge entries to `observations.md` (async, non-blocking)
- Appends ISO timestamp to `.claude/memory/.session-history` (always, at session end)
- Appends setup command block to `.claude/memory/setup-knowledge.md` and clears buffer (when ≥2 setup commands captured)
- Appends one ranked JSON line to `.claude/memory/learnings.jsonl` (at most once per day, only if reflect-agent hasn't already)
- Appends `[stale-action-item]` observation to `observations.md` (at most once per day, only if an overdue item exists)
- Appends `[cache-cost]` observation to `observations.md` and writes `.claude/memory/handoff/latest.md` (at most once per day, only if cache-ratio ≥70% after ≥1 compaction)

**Respects:** `SDD_PROFILE=minimal` env var — skips entirely.

**Path resolution:** Reads the harness root from `~/.sdd-harness-root` — the single stored pointer, written by `install.sh` / `update.sh` via `scripts/lib/harness-pointer.sh`. The two failure states are now distinguished: an **absent** pointer means the harness was never installed globally, and the hook exits silently (correct). A pointer naming a **non-existent** directory means the harness moved, which disables every cross-repo hook on the machine — the hook prints `[HARNESS-POINTER-STALE]` with the dead path and the fix (`bash <harness>/update.sh`) before exiting 0. Both cases used to be an indistinguishable silent `exit 0`, so a move could disable the harness fleet-wide with no symptom.

---

### `prompt-hook.sh`
**Event:** `UserPromptSubmit` — **Matcher:** _(all prompts)_

**Purpose:** Injects `.claude/memory/hot-memory.md` into the prompt as `--- Active Context (hot-memory) ---` … `--- End Active Context ---`.

**Cadence (not every prompt):** hot-memory is ~2k tokens and every injection stays in the transcript, so injecting on each prompt re-sends one more copy on every later API call. It now injects on the **1st prompt of a session**, then **every `SDD_HOT_MEMORY_EVERY` prompts** (default `10`), and on the **first prompt after a compaction** (the summary may have dropped it). Compactions are counted by scanning the transcript at `transcript_path` for `"compact_boundary"`; a change in that count resets the cadence to 1.

**Counter:** `.claude/memory/.prompt-hook/<session_id>.json` holds `{"n": …, "compacts": …}`. On the first prompt of a session the hook drops counter files untouched for 7 days. A `session_id` containing `/`, or one that is absent, is not used as a filename.

**Fail-open:** any failure to read the hook event, parse `SDD_HOT_MEMORY_EVERY`, read a corrupt counter, or write the counter **injects** — a missed injection is invisible, an extra one costs 2k tokens. The reason is printed to stderr as `[prompt-hook] <why> — injecting hot-memory`. A corrupt counter file is reset rather than left to inject forever. Exits silently when `hot-memory.md` is missing or empty.

**Decision logic** runs in an inline `python3` heredoc (stdlib only, no regex) and prints `inject` or `skip`; the shell wrapper only concatenates the file.

**Tests:** `hooks/claude/prompt-hook.test.sh`.

---

### `frontend-security-nudge.sh`
**Event:** `UserPromptSubmit` — **Matcher:** _(all prompts, keyword-gated)_

**Purpose:** Detects when the user is about to build frontend, UI, or design work and injects a reminder to invoke the `secure-agent-design` skill before starting. Fires on every prompt but exits in <5ms if no keywords match.

**Trigger logic:** Two conditions must BOTH be true:
1. **Build intent** — prompt contains: `build a`, `create a`, `add a`, `implement a`, `write a`, `scaffold`, `set up`
2. **Frontend subject** — prompt contains framework names (`react`, `vue`, `angular`, `svelte`, `nextjs`, `nuxt`, `remix`, `astro`…) or design keywords (`frontend`, `ui`, `ux`, `component`, `css`, `tailwind`, `form`, `button`, `modal`, `page`, `responsive`…)

**Why it's needed:** Security considerations (XSS, injection, input sanitization, prompt injection in agent-fed forms) are easiest to address before the first file is written. A question-only prompt like "how does React work?" does not trigger the nudge — only prompts with build intent.

**Output:** `╔══ Security Nudge ══╗` banner with the instruction to invoke `Skill("secure-agent-design")`. When the nudge fires, also appends a `[frontend-security-nudge]` observation to `.claude/memory/observations.md` (if present). Silent on non-matching prompts.

---

### `pr-mention-nudge.sh`
**Event:** `UserPromptSubmit` — **Matcher:** _(all prompts, keyword-gated)_

**Purpose:** Detects when the user's prompt mentions opening/creating/merging a PR (`\bpr\b`, `pull request`, `open a pr`, `create a pr`, `merge this`) and, if inside a git work tree, calls the shared `scripts/pr/detect_base_and_create.sh` to auto-detect the branch's true base and open a draft PR if one doesn't already exist.

**Why it's needed:** This is one of two trigger points (the other is `pr-auto-create-hook.sh` after a successful `git push`) for the PR-babysitting automation — catching the case where the user asks for a PR before pushing, or in a session where the push already happened earlier.

**Output:** `[PR-AUTO-CREATED] PR #N opened against <base> (auto-detected base)` on success, or `[PR-AUTO-CREATED] PR #N already open...` if one exists. Silent (exit 0) on non-matching prompts, outside a git tree, or if `gh` is not installed. If `.git/gh-stack` shows a stack is active for the branch (see `stacking-pull-requests` skill), runs `gh stack submit --auto` instead and outputs `[STACK-SUBMITTED] gh-stack layers submitted for <branch>` in place of the single-PR output.

---

### `doc-parse-nudge.sh`
**Event:** `UserPromptSubmit` — **Matcher:** _(all prompts, keyword-gated)_

**Purpose:** Detects when the user is about to build document-parsing or RAG pipeline workflows and injects a reminder to invoke the `document-parsing` skill. Fires on every prompt but exits in <5ms if no keywords match.

**Trigger logic:** Two conditions must BOTH be true:
1. **Build intent** — prompt contains: `build`, `create`, `set up`, `implement`, `add`, `write`, `design`, `scaffold`, `integrate`, `develop`
2. **Doc/RAG subject** — prompt mentions document formats (`pdf`, `docx`, `pptx`, `ocr`…) OR RAG/ingestion terms (`rag`, `embedding`, `vector store`, `chunk`, `ingest`, `pinecone`, `chroma`, `qdrant`…)

**Why it's needed:** Local PDF/DOCX/image ingestion has non-obvious format and OCR choices. The `document-parsing` skill covers liteparse, format selection (text vs JSON+bbox), OCR config, and RAG handoff patterns — easiest to apply before the first line of pipeline code is written.

**Output:** `╔══ doc/RAG work detected ══╗` banner with the instruction to invoke `Skill("document-parsing")`. Silent on non-matching prompts.

---

### `memory-discipline-hook.sh`
**Event:** `PreToolUse` — **Matcher:** `Write|Edit`

**Purpose:** Gates any write to a memory file (`.claude/memory/*.md` or `MEMORY.md`) with discipline rules, printed before the write executes. Claude sees the rules and can revise the content before proceeding.

**Rules enforced:**
- Store only reusable patterns, preferences, and heuristics — not case-specific findings
- Transfer test: "Would this help a _different_ future task?" If not → write to an artifact, not memory

**Why it's needed:** Without a gate, Claude naturally stores investigation outcomes and entity-specific facts in memory. These pollute future context with stale, non-transferable data and cause the memory system to drift toward a case-log rather than a reusable knowledge base.

**Output:** Rules banner (`╔══ Memory Discipline Gate ══╗`) injected before the write executes.

---

### `impeccable-detect-hook.sh`
**Event:** `PostToolUse` — **Matcher:** `Write|Edit`

**Purpose:** Runs [Impeccable](https://github.com/nicholasgasior/impeccable) anti-pattern detector on the written file immediately after any Write or Edit to a frontend file (`tsx`, `jsx`, `css`, `scss`, `less`, `vue`, `svelte`, `html`).

**Why it's needed:** Frontend anti-patterns (e.g., CSS property ordering, accessible-name violations, layout anti-patterns) accumulate silently across edits. Catching them at write time, rather than at PR review, costs far fewer tokens and less rework.

**Output:** Anti-pattern warnings with severity; green check if clean. Fails silently if Impeccable is not installed — install with `npm install -g impeccable@3.6.0` (the version `install.sh` pins; npm 10 has no release-age cooldown, so the pin is the guard).

---


### `skill-usage-tracker.sh`
**Event:** `PostToolUse` — **Matcher:** `Skill` — _(silent, never blocks, zero tokens)_

**Purpose:** Appends one JSON line (`{"ts","skill"}`) per Skill-tool invocation to the global log `$SDD_HARNESS/logs/skill-usage.jsonl`. Every repo's copy writes to the same absolute path, so usage is aggregated across all projects.

**Why it's needed:** The weekly `skill-curator` claims to prune "unused" skills but previously had no usage data — it guessed from file mtime (edit time, not use). This is the evidence layer: real fire counts + last-seen timestamps let the curator deprecate cold skills (no use in 30d) and archive dead ones (90d) on evidence, not guesswork. Pattern lifted from the Hermes agent's curator usage log.

**Output:** None (pure side-effect logging). The data surfaces in the **Skill Changes** dashboard tab (hot/cold stats, top-skills bars) and the weekly skill-curation report's **Usage Evidence** section. Skill name is charset-sanitized before write; non-`Skill` tools are ignored.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `PostToolUse` matcher `Skill` in `templates/settings.json.template`.

---


### `action-capture.sh`
**Event:** `PostToolUse` — **Matcher:** `Bash` — _(soft gate, never blocks)_

**Purpose:** After high-signal Bash commands complete, performs two functions: (1) prompts Claude to consider saving a memory observation from the outcome; (2) **automatically** writes Wake-phase weakness markers when commands fail.

**Signal types:**

| Signal | Trigger | Action |
|--------|---------|--------|
| `git-commit` | `^git commit` | Advisory banner — prompt to save workflow lesson |
| `test-failure` | `pytest` + FAILED/ERROR in output | Advisory banner — prompt to save breakage pattern |
| `deploy` | docker/kubectl/helm deploy commands | Advisory banner — prompt to save env gotcha |
| `struggle` | Any Bash with non-zero exit code (new) | **Auto-writes** `[seed-target:<domain>]` observation to `observations.md` + advisory banner |

**Wake-phase tagging (Sleep Cycle Protocol):** The `struggle` signal is the harness implementation of the paper's Wake-phase weakness identification (Behrouz et al., 2026). When any Bash command fails, the hook infers a skill domain from the command content, auto-writes a `[seed-target:<domain>]` entry to `.claude/memory/observations.md`, then emits an advisory banner. No Claude decision required — the observation is written unconditionally. The nightly `skill-augment-agent` (Step D of daily maintenance) consumes these entries as seeding targets for the Sleep phase.

**Domain inference table:**

| Command contains | Inferred domain |
|-----------------|----------------|
| `pip`, `npm`, `yarn`, `poetry`, `uv` | `dependency-management` |
| `docker`, `kubectl`, `helm` | `deployment-pipeline-design` |
| `git` | `git-advanced-workflows` |
| `curl`, `wget`, `http` | `api-patterns` |
| `python`, `.py` | `python-pro` |
| `node`, `npm`, `.ts`, `.tsx` | `nodejs-backend-patterns` |
| _(default)_ | `systematic-debugging` |

**Design principle:** "Memory from what agents DO, not just what they say" — extracted from Memori's architecture. The struggle extension adds automatic capture without requiring Claude to decide.

**Why it's needed:** Git commits, test failures, and deploys are high-signal moments. Failed commands are even higher-signal — they reveal exactly where skills are weak. Without automatic capture, these weakness signals evaporate at session end and the nightly improvement pipeline has no targets.

**Noise control:** Advisory signals only fire on the four specific patterns; silent on all other Bash commands. Test passes are intentionally excluded (low signal). The hook exits 0 always.

**Output:** `╔══ Action Capture ══╗` banner (advisory) for all signal types. `[seed-target:]` observation written silently to `observations.md` for `struggle` signal only.

**Location:** `~/.claude/hooks/action-capture.sh` (global — fires across all projects)

---

### `setup-buffer-hook.sh`
**Event:** `PostToolUse` — **Matcher:** `Bash`

**Purpose:** After every Bash command, checks whether the command matches a setup-like pattern and, if so, appends it to a per-session accumulation buffer (`.claude/memory/.setup-session-buffer.log`). The buffer is consumed at session end by `stop-hook.sh`, which writes the captured commands to `.claude/memory/setup-knowledge.md`.

**Patterns detected** (prefix-matched, case-insensitive):
- Package managers: `pip install`, `pip3 install`, `npm install`, `npm ci`, `yarn install`, `yarn add`, `pnpm install`, `pnpm add`, `brew install`, `apt-get install`, `apt install`, `cargo build`, `go mod download`, `bundle install`, `composer install`, `gem install`, `uv sync`, `uv pip install`, `poetry install`, `conda install`
- Docker: `docker-compose`, `docker compose`, `docker build`, `docker pull`
- Database setup: `createdb`, `dropdb`, `psql ... CREATE/DROP`, `mysql`
- Migrations: `python manage.py migrate`, `rails db:`, `alembic upgrade`, `prisma migrate`, `prisma db push`
- Environment: `source *.env`, `cp *.env* ...`, `export VAR=`
- Repo bootstrap: `git clone`, `git submodule update`, `make install`, `make setup`, `make init`, `make bootstrap`

**Noise control:** Skips multi-line commands (>3 lines) — these are typically inline scripts, not atomic setup steps. Silent on all non-matching commands.

**Why it's needed:** Environment bootstrap steps (dependencies, DB setup, env config) are the most-forgotten class of knowledge across sessions. Running the same `pip install` sequence when re-visiting a project, or onboarding someone, typically requires re-reading docs or asking. Automatic capture at the moment the commands run means `setup-knowledge.md` is always current without requiring Claude to remember to save them. Paired with `stop-hook.sh` (write) to keep the buffer-flush logic together at a natural session boundary.

**Output / side effect:** Appends command line to `.claude/memory/.setup-session-buffer.log`. No context output — silent capture.

---

### `revert-detect-hook.sh`
**Event:** `PostToolUse` — **Matcher:** `Bash`

**Purpose:** Immediately appends a `[revert]` drain observation to `observations.md` when Claude runs `git revert`, `git reset --hard/--mixed/HEAD`, or `git restore --` / `git checkout --`.

**Why it's needed:** The trust-battery Judge scores session quality at the end of each day. Revert events are significant negative signals (they indicate a plan that had to be walked back). Without real-time capture, the Judge has to infer reverts from transcript analysis — less reliable and potentially missed. Writing the observation at the moment the command runs gives the Judge concrete, timestamped evidence.

**Output / side effect:** Appends `YYYY-MM-DD [revert]: git-revert|git-reset|git-restore — <command>` to `observations.md`. De-duplicates within the same day.

---

### `pr-auto-create-hook.sh`
**Event:** `PostToolUse` — **Matcher:** `Bash`

**Purpose:** After a Bash command matching `git push` (and not a `--force`/`-f` push) succeeds, calls the shared `scripts/pr/detect_base_and_create.sh` to open a draft PR. Parses `tool_input.command` + `tool_response` from stdin via inline Python; bails silently if the push wasn't a plain non-force push, or if the response contains a failure signature (`rejected`, `failed to push`, `non-fast-forward`, `error:`, `could not read`, `permission denied`).

**Why it's needed:** Pairs with `pr-mention-nudge.sh` as the second trigger point for PR-babysitting automation — catches the common case of pushing a branch and expecting a PR to exist, without requiring the user to ask. Made possible by the `templates/settings.json.template` permission change that narrowed the deny rule from blanket `Bash(git push*)` to only force-push variants, so this hook can now observe successful ordinary pushes.

**Output:** `[PR-AUTO-CREATED] PR #N opened against <base> (auto-detected base)` on success, or `[PR-AUTO-CREATED] PR #N already open...` if one exists. Silent (exit 0) on force pushes, failed pushes, outside a git tree, if `gh` is not installed, or if `hooks.autopilot.enabled` is not `true` in git config — `detect_base_and_create.sh` itself gates on `git config --bool hooks.autopilot.enabled`, same opt-in flag as `pr-risk-tier-hook.sh` and the post-commit doc-sync push, so auto-creation defaults off in any worktree that hasn't run `git config --worktree hooks.autopilot.enabled true`. If `.git/gh-stack` shows a stack is active for the branch (see `stacking-pull-requests` skill), runs `gh stack submit --auto` instead and outputs `[STACK-SUBMITTED] gh-stack layers submitted for <branch>` in place of the single-PR output.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `PostToolUse` matcher `Bash` in `templates/settings.json.template`, alongside `revert-detect-hook.sh` / `setup-buffer-hook.sh`.

---

### `pr-evidence-hook.sh`
**Event:** `PreToolUse` — **Matcher:** `Bash`

**Purpose:**
1. Parses `tool_input.command` from stdin and tokenizes it with `shlex`, then matches the `gh pr create` verb token-by-token — following the lesson recorded in `git-destructive-guard-hook.sh`, so `cd x && gh pr create`, `bash -c '...'`, `GH_TOKEN=x gh ...`, an absolute `gh` path, and `gh --repo o/r pr create` all resolve to the same verb.
2. Resolves the PR body from `--body`/`-b` (inline or `=` form) or `--body-file`/`-F` (reads the file).
3. Checks the body for the literal `## Evidence` heading and emits a nudge when it is absent.
4. Emits the same nudge when the invocation supplies no inspectable body at all — `--fill`, `--fill-first`, or a bare `gh pr create` that opens an editor. Both reach the reviewer with no evidence section.
5. Stays silent when the body cannot be judged rather than guessing: `--body-file -` (stdin) and an unreadable file produce no output.

**Why it's needed:** `verification-before-completion` requires evidence for claims made in conversation, but that evidence stops at the PR boundary — `create-pr`, `iterate-pr`, and `pr-babysit` contained zero references to evidence artifacts, so the reviewer received the agent's description of its own work and nothing to check it against. The hook enforces the presence of the section; `create-pr` ("Attach Runtime Evidence") teaches what goes in it, including the load-bearing rule that the before-state must be captured while reproducing the problem, since after the fix it costs a revert and is therefore usually written from memory instead.

**Output / side effect:** Prints a `PR Evidence — missing proof` block to stdout naming which case fired (no inspectable `--body`, or `--body` without the heading), with the required format and the docs-only escape hatch. No files written.

**Soft by design:** always exits 0, never blocks. Not every PR has a visible surface, and a hard block would force fabricated evidence blocks on docs-only PRs. The hook checks that the heading exists; it cannot tell real evidence from a plausible-looking paragraph.

**Not covered by this hook, covered elsewhere:** PRs opened by `scripts/pr/detect_base_and_create.sh` (the push-triggered auto-create path used by `pr-auto-create-hook.sh` and `pr-mention-nudge.sh`) call `gh pr create` inside the script, not through the Bash tool, so no `PreToolUse` event fires. That script writes the `## Evidence` section itself — as an explicit "not captured, opened automatically on push" placeholder that must be replaced before the PR leaves draft. It is deliberately not a real evidence section: nothing ran a probe on that path, and inventing one would be the exact failure this hook exists to prevent.

**Tests:** `hooks/claude/pr-evidence-hook.test.sh` — 36 cases (nudge, quiet, false-positive guard, and an exit-code-0 block proving it never blocks). Asserts on emitted text, not exit codes, because a soft gate's exit code is constant.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `PreToolUse` matcher `Bash` in `templates/settings.json.template` and `templates/settings.harness.json.template`, alongside `git-destructive-guard-hook.sh` / `agent-commit-attribution-hook.sh`.

---
### `cheap-model-delegation-hook.sh`
**Event:** `PreToolUse` — **Matcher:** `Bash` — _(soft, never blocks)_

**Purpose:** Fires when a single Bash command reads 6+ files at once — `cat`/`head`/`tail` with 6+ path-looking non-flag args, typically from a glob expansion. Suggests delegating to the `cheap-model-delegation` skill (a haiku-tier subagent) instead of doing bulk mechanical extraction at the primary model's rate.

**Why it's needed:** Pulling one fact or summary per file across many files is mechanical work that doesn't need the primary model's reasoning budget. A `PreToolUse(Read)` hard gate already exists for single large files (`lean-ctx-nudge-hook.sh`), but bulk multi-file Bash reads can't be reliably judged as "extraction work" vs. "legitimate small multi-file read" from the command line alone, so this hook only nudges — it never blocks.

**Output:** A boxed `cheap-model-delegation Opportunity` banner naming the file count and pointing at `Skill("cheap-model-delegation")`. Silent (exit 0) on non-`cat`/`head`/`tail` commands, non-Bash tool calls, or fewer than 6 file args.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `PreToolUse` matcher `Bash` in `templates/settings.json.template`/`templates/settings.harness.json.template`. See the `cheap-model-delegation` skill.

---
### `pr-risk-tier-hook.sh`
**Event:** `PostToolUse` — **Matcher:** `Bash`

**Purpose:** Same trigger as `pr-auto-create-hook.sh` — a non-force `git push` — but for a branch that already has an open PR. Diffs the PR's files against its base branch, looks each one up in `.claude/steering/risk-zones.md`, takes the worst zone found, and labels the PR `risk:red` / `risk:yellow` / `risk:green` via `gh pr edit --add-label` (creating the label with an appropriate color if it doesn't exist yet).

**Why it's needed:** A reviewer opening a PR has no signal about blast radius or test coverage until they read the diff themselves. Surfacing it as a label makes it visible before that, and gives `pr-babysit` a cue to read review feedback more carefully on red/yellow PRs instead of fast-pathing.

**Output:** `[PR-RISK-TIER] PR #N labeled risk:<tier>. Touches: <file> (<zone>); ...` Silent (exit 0) on force pushes, no open PR, missing `gh`, no `risk-zones.md`, or when `hooks.autopilot.enabled` is not `true` in git config (see `git config --worktree hooks.autopilot.enabled true`, and `git config extensions.worktreeConfig true` once per repo) — this labeling step defaults off in any worktree that hasn't explicitly opted in.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `PostToolUse` matcher `Bash` in `templates/settings.json.template`, alongside `pr-auto-create-hook.sh`. See the `risk-zone-engine` skill.

---

### `sloppiness-warn-hook.sh`
**Event:** `PostToolUse` — **Matcher:** `Write|Edit|MultiEdit` — _(soft, never blocks)_

**Purpose:** Scores the single file just written against `scripts/quality/sloppiness-score.sh`'s Verbosity (exact-line-dedup ratio) and Erosion (branch-density) metrics — a dependency-free, non-LLM-judge proxy for clone detection and cyclomatic mass. Records the verbosity value to `.claude/memory/metrics.jsonl` (`sloppiness` metric) on every scoreable write, and prints a warning only when the file's verdict is `high-slop` (at/above the published AI-agent baseline of verbosity 0.33 / erosion 0.68).

**Why it's needed:** LLM-as-judge scoring of code quality on a 1-10 scale is close to random — see the `clean-code` skill's caution. A mechanical, deterministic proxy that fires automatically on every write catches duplicated blocks and deeply nested branches without asking a model to rate its own output, and feeds a trend the dashboard can plot over time.

**Output:** `[SLOPPINESS] <file> scored high-slop (...): <json> — consider deduplicating repeated blocks or flattening nested branches before moving on. Advisory only.` Silent (exit 0) on non-code files, files the score script can't read, or `elevated`/`clean` verdicts.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `PostToolUse` matcher `Write|Edit|MultiEdit` in `templates/settings.json.template`/`templates/settings.harness.json.template`, alongside `ruff-quality-gate-hook.sh`/`js-quality-gate-hook.sh`. See the `clean-code` skill and the "Session Quality" dashboard card.

---

### `tool-failure-capture.sh`
**Event:** `PostToolUseFailure` — **Matcher:** `Bash|mcp__.*`

**Purpose:** Records every failing Bash or MCP tool call into a per-repo ledger (`.claude/memory/tool-failures.jsonl`), keyed by a normalized command *signature*. Bash commands are collapsed — quoted strings → `<str>`, slash-bearing tokens → `<path>`, hashes → `<hash>`, numbers → `<n>` — so the *same kind* of failure clusters under one entry whose `count` climbs. MCP calls key on `tool_name(sorted,arg,keys)`. A failure that recurs after being marked `resolved` is automatically re-opened.

**Why it's needed:** Without a durable record, the harness re-runs the same broken command shape across sessions (wrong flag, missing dep, bad path) and re-learns the failure each time. This is the **capture** half of the tool-failure-memory loop — adapted from ReMe's procedural/tool memory, made deterministic via a hook so it fires on every failure, not when Claude remembers to.

**Output / side effect:** Upserts a JSON line into `.claude/memory/tool-failures.jsonl` (`{sig, tool, count, first_seen, last_seen, last_error, samples, status, remedy, promoted}`). No context output — silent capture. Paired with `tool-failure-recall.sh` (recall) and the `tool-failure-review` routine (promote-to-memory). See the `tool-failure-memory` skill.

---

### `tool-failure-recall.sh`
**Event:** `PreToolUse` — **Matcher:** `Bash|mcp__.*` — _(soft gate, never blocks)_

**Purpose:** Before a Bash/MCP call runs, computes the same signature as the capture hook and looks it up. If that shape has failed ≥2× and is still `open`, it injects a short advisory showing the failure count, last error, and any recorded remedy — then exits 0. This is the **recall** half that makes failures "not happen again."

**Noise control:** Warns at most once per session per signature (dedupe file `.claude/memory/.tool-failure-recall-seen`) and ignores entries whose last failure is older than 45 days. Silent on everything else. Tunable via `TOOL_FAILURE_MIN_FAILS` / `TOOL_FAILURE_MAX_AGE_DAYS`.

**Why it's needed:** Capturing failures is useless unless the memory surfaces *before* the repeat. The soft gate keeps Claude in control — it advises reconsidering, never blocks — so a genuinely-now-fixed command can still proceed.

**Output:** `⚠️  Tool-failure memory — this command shape has failed N× …` banner with signature, last error, and remedy. Silent (exit 0) when no recent open failure matches.

---

### `gbrain-agent-spawn.sh`
**Event:** `PreToolUse` — **Matcher:** `Agent`

**Purpose:** Injects model-tier selection and background-routing guidance before every subagent spawn. Part of the GBrain patterns suite. Also writes a deterministic session-handoff snapshot (`scripts/session/write_handoff.py --trigger agent-spawn`) of the *main* session's state to `.claude/memory/handoff/latest.md` before printing the rules banner, then nudges the caller (via the banner's "Session handoff" note) to pull relevant parts into the subagent's prompt.

**Scope correction (2026-08-30):** this section previously stated that hooks *cannot* inject content into a subagent's context. That was true of `PreToolUse:Agent`, which is all this hook has; it is **not** true in general — `SubagentStart` fires inside the child and injects via JSON `hookSpecificOutput.additionalContext`. Verified against Claude Code 2.1.221 with a probe subagent that read the injected block back verbatim, `agent_type` included. See `subagent-context-hook.sh`. Division of labour: `SubagentStart` carries always-true conventions straight into the child; this hook carries spawn-time decisions only the parent can make (which model, run mode, what context to hand down).

**Rules enforced:**
- **Model tiers:** Haiku for classification/validation, Sonnet for generation/synthesis (default), Opus only for high-stakes deep reasoning. Subagents should default to Sonnet — latency compounds in loops and Opus rarely adds value there.
- **Background routing:** Inline unless a pain signal fires (gateway restart, dropped state, >3 parallel agents, >5 min expected runtime, user frustration).
- **Memory-first brief:** Run `mcp__plugin_claude-mem_mcp-search__search` before writing the agent prompt, to avoid re-discovering known context.
- **Session handoff:** A fresh snapshot of the main session's state was just written to `.claude/memory/handoff/latest.md` — for a background, long-running, or parallel agent, pull the relevant parts into its prompt instead of re-deriving them.

**Why it's needed:** Without guidance, the default is to pick whatever model feels right (often Opus) and always run inline. Both choices compound token cost and latency across multi-agent sessions without a commensurate quality benefit.

**Output / side effect:** GBrain protocol banner injected before the Agent tool executes. Silently writes/overwrites `.claude/memory/handoff/latest.md` (best-effort — failures are swallowed with `|| true`).

---


### `gbrain-external-search.sh`
**Event:** `PreToolUse` — **Matcher:** `WebFetch|WebSearch`

**Purpose:** Reminds Claude to check `claude-mem` before calling external APIs. Part of the GBrain patterns suite.

**Why it's needed:** External web calls are expensive (tokens + latency) and often unnecessary — prior research sessions have already fetched and stored the relevant content in memory. A quick memory search first can avoid the external call entirely.

**Output:** 4-step memory-first checklist: search → semantic search if thin → get_observations for hits → external only if nothing useful found.

---

### `raindrop-best-practices.sh`
**Event:** `PreToolUse` — **Matcher:** `mcp__raindrop__`

**Purpose:** Before any Raindrop Workshop MCP tool call, injects active observability best practices that reduce token cost and improve cluster quality.

**Rules injected:**
1. **Batch facets** — run multiple analytical dimensions in one LLM call (not one call per dimension)
2. **Facet-first** — summarize each trace in 1-2 sentences before clustering (raw traces produce noisy clusters)
3. **Cap input** — preprocess to ≤128K tokens before LLM analysis (walk spans, deduplicate messages, drop scorer/metric spans)
4. **No-LLM classify** — at classification time use nearest-summary lookup (~100ms; no LLM call needed once a topic map exists)
5. **Long tail** — don't sample aggressively; bugs live in rare clusters (HDBSCAN with no pre-specified count; outliers → `no_match`, not forced)

**Why it's needed:** Raindrop trace analysis is token-heavy. Without guidance, the natural pattern is to feed raw trace payloads into LLMs one at a time — multiplying cost. These patterns compress the input surface and batch analytical work so trace evaluation runs at ~10–20% of the naïve cost.

**Output:** `╔══ Active Observability (Raindrop) ══╗` rules banner before every Raindrop MCP call.

---

### `gbrain-memory-write.sh`
**Event:** `PreToolUse` — **Matcher:** `mcp__plugin_claude-mem_mcp-search__save_observation`

**Purpose:** Injects compiled-truth structure guidance before every MCP memory write. Part of the GBrain patterns suite.

**Rules enforced:**
- **STATE zone (top):** Current synthesis. Rewrite in place when new evidence arrives. Every fact needs a source citation.
- **EVIDENCE / TIMELINE zone (bottom):** Append-only dated log. Never edit past entries.
- Source precedence: user statements > compiled truth > timeline entries > external sources.

**Why it's needed:** Without structure guidance, observations tend to be pure append-only logs. Over time, the STATE zone drifts (stale facts never get updated) while the TIMELINE grows unboundedly. The two-zone pattern keeps the current understanding clean and the historical record intact.

**Output:** Compiled-truth pattern banner injected before the save_observation call.

---

### `compaction-discipline-hook.sh`
**Event:** `PreCompact` — **Matcher:** _(all compactions)_

**Purpose:** Injects boundary-timing, state-preservation, and domain-aware compression principles before every context compaction. After printing the rules banner, also fires a deterministic (non-LLM) session-handoff snapshot (`scripts/session/write_handoff.py --trigger precompact`) to `.claude/memory/handoff/latest.md`, so working state survives even if the in-context summary drifts.

**Rules enforced:**
- **Timing:** Compact at workflow boundaries (end of phase, task completion) — not arbitrary message counts.
- **Preserve:** Working state, open questions, artifact paths, unresolved concerns, decisions + rationale.
- **Do not over-compress:** Never strip file paths, function names, error messages, specific values.
- **Fidelity requirements:** Unanswered-question tracking (answered/partial/unanswered + a "Pending Questions" subheading); root causes kept separate from ruled-out hypotheses (with file:line); files grouped into critical/referenced/mentioned tiers instead of a flat list; subagent/Task results treated as primary evidence to preserve in full; A-vs-B option comparisons preserved with which side won. Ported from claude-codex-settings' `intelligent-compact` plugin — concrete additions the rules above state only in the abstract.
- **Domain-aware strategy:** Code → chunk-level (keep signatures, drop implementations already acted on). Prose → sentence-level (keep topic sentences, drop elaboration). RAG results → query-aware (filter to last user intent). Conversation → keep decisions and user corrections, drop filler. Tool output → keep errors and key metrics, drop passing output.
- **Method:** Merge new content into existing summary sections — do not regenerate from scratch.

**Why it's needed:** Default compaction regenerates the summary from scratch on each pass. Research on consolidation loop failure modes ([faulty-memory](https://dylanzsz.github.io/faulty-memory/)) shows that full regeneration causes LLM sampling drift — each pass shifts content toward the model's prior, away from ground truth. Anchored iterative summarization (merge, not regenerate) is the mitigation. Domain-specific strategy selection is grounded in [Redis context pruning research](https://redis.io/blog/context-pruning-llm-tokens/) showing chunk-level pruning outperforms token-level for code (preserves syntactic validity), while sentence-level is better for prose.

**Output / side effect:** Compaction discipline banner injected before compaction executes. Silently writes/overwrites `.claude/memory/handoff/latest.md` (best-effort — failures are swallowed with `|| true`).

---

### `lean-ctx-nudge-hook.sh`
**Event:** `PreToolUse` — **Matcher:** `Read` (hard gate) — also **`PostToolUse`** — **Matcher:** `Write|Edit` (soft nudge, unchanged)

**Purpose:** Dual-mode, same script. On `PreToolUse(Read)`, a native Read of a large file (≥16 KB ≈ 4,000 tokens) is hard-denied before it spends context, naming the exact `ctx_read` mode to use instead. On `PostToolUse(Write|Edit)`, prints the same one-line mode suggestion after editing a large file — advisory only, since the write already happened and can't be un-spent.

**Mode selection logic:**
- Code files (`.py`, `.ts`, `.js`, `.go`, `.rs`, etc.) → `signatures` mode (~3–5% of full-file tokens)
- Text / docs (`.md`, `.txt`, `.rst`) → `reference` mode (quote-ready excerpts)
- Unknown types → `aggressive` mode (maximum compression)
- Data formats (`.json`, `.yaml`, `.toml`, `.lock`) → silently skipped (lean-ctx intentionally skips these)

**Why it's needed:** RTK handles Bash output compression automatically, and lean-ctx handles file reads — but only if Claude chooses `ctx_read` over the built-in Read tool. The original PostToolUse-only version was advisory-after-the-fact: the expensive Read had already happened by the time the nudge printed. Upgraded (2026-09, extraction pass) to a `PreToolUse` hard block on Read itself, closing that timing gap — modeled on Spotify Portal's "shunt" pattern ([engineering.atspotify.com](https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90)), but without Portal's second-model delegation: `ctx_read`'s deterministic compression modes already do the job locally, so no new model dependency was introduced. The mode guidance follows [Redis context pruning research](https://redis.io/blog/context-pruning-llm-tokens/): chunk-level for code, sentence-level for text, query-aware (`task` mode) for precision work.

**Output:** PreToolUse — `{"hookSpecificOutput": {"permissionDecision": "deny", ...}}` naming the exact `ctx_read` call to retry with. PostToolUse — unchanged `╔══ lean-ctx Opportunity (~N tokens) ══╗` banner. Exits silently for files under threshold.

**Escape hatch:** `LEAN_CTX_NUDGE_WARN_ONLY=1` downgrades the PreToolUse block to the same soft banner — for the rare case where native Read+Edit (not `ctx_read(mode="anchored")` → `ctx_patch`) is genuinely the right path for a large file.

---

### `skill-permissions-gate.sh`
**Event:** `PostToolUse` — **Matcher:** `Write|Edit` — _(soft gate, never blocks)_

**Purpose:** After any Write or Edit to a `*/skills/*/SKILL.md` path, prompts Claude to invoke `agent-permissions-design` and verify four dimensions before marking skill creation complete: (1) tool access — does the skill direct Claude to use destructive tools? (2) irreversible action gates — are high-risk steps preceded by a verification instruction? (3) scope boundary — are trigger conditions specific enough to prevent misfire? (4) external access — does the skill touch external services or credentials least-privilege?

**Design principle:** Skills are mini-agents — they direct Claude to take actions with tools. The same rigor applied to AI agent authorization systems (`agent-permissions-design`) applies to skill design: scope it, gate destructive actions, keep triggers tight. Extracted from the VentureBeat article "The AI agent bottleneck isn't model performance — it's permissions" (2026-05-29).

**Why it's needed:** Without this gate, newly created skills could instruct Claude to run shell commands, overwrite files, or call external APIs without scoping, verification steps, or explicit "Do Not Use" guards. The hook catches these at write time rather than during a later security audit.

**Noise control:** Only fires on `*/skills/*/SKILL.md` paths — silent on all other Write/Edit operations. Never blocks (exits 0 always).

**Output:** `╔══ Skill Permissions Gate ══╗` reminder banner listing the four review dimensions. Silent on no match.

**Location:** `$SDD_HARNESS/.claude/hooks/skill-permissions-gate.sh`

---

### `caveman-activate.js`
**Event:** `SessionStart` — **Matcher:** _(all sessions)_

**Purpose:** Activates Caveman response compression at the configured intensity level at the start of every session. Reads `~/.config/caveman/config.json` (or `CAVEMAN_DEFAULT_MODE` env var) to determine the level. Emits the full Caveman ruleset filtered to the active level as session context, anchoring Claude's response style for the entire session. Writes a flag file (`~/.claude/.caveman-active`) for statusline display.

**Default level:** `lite` — strips filler words, pleasantries, and hedging while keeping full technical substance. User can upgrade (`/caveman full`, `/caveman ultra`) or disable (`normal mode`) at any point mid-session.

**Levels:**
- `lite` — light trim: removes pleasantries and hedging; fragments allowed
- `full` — moderate compression: terse prose, short synonyms, no elaboration (default upstream)
- `ultra` — telegraphic: maximum brevity, every non-essential word removed
- `wenyan` — classical Chinese compression (stress test of brevity)

**Why it's needed:** Caveman only applies for a session if explicitly invoked with `/caveman`. A SessionStart hook makes it the default behavior without requiring the user to remember to activate it. The full ruleset is emitted (not a short summary) because the 2-sentence version drifts mid-session; full rules with examples anchor the style reliably even after compaction.

**Output:** `CAVEMAN MODE ACTIVE — level: lite` followed by the filtered ruleset. If statusline is not configured, appends a setup nudge.

**Location:** `~/.claude/hooks/caveman-activate.js` (global; ships in the harness at `hooks/global/` and is copied to `~/.claude/hooks/` by `install.sh`). Reads `skills/caveman/SKILL.md` at runtime for the ruleset, falling back to a hardcoded minimal set on standalone installs where that file isn't present.

---

### `address-check-hook.sh`
**Event:** `Stop` — **Matcher:** _(all turns)_ — _(log only, never blocks)_

**Purpose:** Verifies that every assistant response addressed the user as "Husband". Fires after each turn. Reads the latest session transcript, extracts the last assistant message, and checks for the word "husband" (case-insensitive).

**Outputs:**
- Nothing, if "Husband" present — exit 0, stop proceeds normally
- `[address-check] husband not found — compact needed` on stdout if "Husband" is absent — exit 0. This is a **mechanical log line only**: it is not fed back to Claude and does not block the stop, so there is no forced extra turn and no token cost.

**Why a hook and not a prompt:** CLAUDE.md instructions are subject to context degradation — Claude eventually stops following them as context fills. A Stop hook fires unconditionally after every turn regardless of context state. Absence of the term is a reliable early signal that CLAUDE.md is being ignored — the log line surfaces that signal so a human can decide to `/compact` before other rules degrade too.

**Why it no longer blocks:** the hook previously exited 2 to block the stop and inject a correction prompt telling Claude to `/compact`, re-read CLAUDE.md, and re-respond. That self-correcting loop cost a full extra turn every time it fired, so the hook was demoted to a passive log.

**Location:** `~/.claude/hooks/address-check-hook.sh` (global, all projects)

---

### `verification-retry-hook.sh`
**Event:** `Stop` — **Matcher:** _(all turns)_ — _(advisory by default, never blocks)_

**Purpose:** A Verification processor (Spotify Backstage AiKA pattern — see the `behavioral-modes` skill's "Composable Processors" section): checks the *claim* made about code, not the code itself. Walks the current turn's transcript for explicit success language ("tests pass", "all passing", "verified working", ...) and cross-checks it against any test-runner Bash command (`pytest`, `npm test`, `jest`, `go test`, `cargo test`, ...) that actually ran this turn. Flags a mismatch when the claim exists but either no test command ran, or the last one that did exited non-zero.

**Why it's needed:** Existing quality-gate hooks (`ruff-quality-gate-hook.sh`, `js-quality-gate-hook.sh`, `sloppiness-warn-hook.sh`) check code as it's written; nothing previously checked whether a stated "it works" claim at the end of a turn was actually backed by a passing verification step.

**Why advisory by default:** this repo already tried an always-blocking Stop hook — `address-check-hook.sh` used to exit 2 on every turn missing "Husband" — and demoted it to a passive log because blocking cost a full extra turn every time it fired. `VERIFICATION_RETRY_WARN_ONLY=1` (default) logs `[VERIFICATION-RETRY] (warn-only) ...` instead of blocking. Set `VERIFICATION_RETRY_WARN_ONLY=0` to opt into the real auto-retry: the hook then emits `{"decision": "block", "reason": "..."}`, forcing Claude to continue instead of ending the turn.

**Retry cap:** `VERIFICATION_RETRY_MAX` (default 2) rounds per user request, tracked in `.claude/memory/.verification-retry-state`. The counter resets on any turn that doesn't repeat the mismatch (verified, no claim made, or a fresh user message started); once the cap is hit, the hook gives up and logs `[VERIFICATION-RETRY] gave up after N round(s) ...` instead of blocking further, so a genuinely unfixable claim cannot loop forever.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `Stop` (all turns) in `templates/settings.json.template`/`templates/settings.harness.json.template`, alongside `stop-hook.sh`/`address-check-hook.sh`.

---

### `caveman-savings-hook.sh`
**Event:** `Stop` — **Matcher:** _(all turns)_

**Purpose:** When Caveman mode is active (`~/.claude/.caveman-active` or `~/.config/caveman/config.json`), once per calendar day takes the last real assistant response from the session transcript and asks a cheap Haiku call (`claude --print --model claude-haiku-4-5-20251001`) to re-expand it into normal, complete-sentence prose. Diffs the two response lengths to produce a real sample of how many tokens Caveman actually saves, instead of a guess. Both sides are measured via a word-count heuristic (~1.3 tokens/word) derived from visible response text — not `usage.output_tokens`, which on the "actual" side is contaminated by invisible extended-thinking tokens unrelated to response length. Guarded against self-recursion via `SDD_CAVEMAN_MEASURING=1` on the nested `claude` call, since that call would otherwise re-trigger this same Stop hook.

**Outputs:** Appends one JSON line to `.claude/memory/caveman-savings.jsonl` (`ts`, `mode`, `method`, `actual_tokens`, `baseline_tokens`, `saved_tokens`, `saved_pct`). Exits silently (no-op) if Caveman isn't active, `jq`/`claude` aren't on PATH, today's sample already ran (`.claude/memory/.last-caveman-savings-run`), or the transcript/expansion call comes back empty.

**Consumed by:** `scripts/utils/dashboard.py`'s `_read_caveman_savings()`, which feeds the Caveman layer in the dashboard's Budget & Efficiency → Compression Pipeline view and folds its estimated $ savings (at the Sonnet 4.6 *output* rate, ~$15/M, since this is response-side savings) into the combined-savings total alongside RTK and lean-ctx.

**Location:** ships in `hooks/claude/`; wired via `Stop` (all turns) in `templates/settings.harness.json.template` (harness-only for now — not shipped to consumer repos via `templates/settings.json.template`).

---

### `hook-added-notify.sh`
**Event:** `PostToolUse` — **Matcher:** `Write|Edit`

**Purpose:** Detects when a new hook file is written to `.claude/hooks/` and injects a reminder into Claude's context to document it in this file before the session ends.

**Why it's needed:** Hook files are written infrequently, which means documentation often lags. A real-time reminder at the moment of creation ensures the docs stay in sync without requiring a separate workflow step.

**Output:** Documentation reminder banner if the written file path matches `*.claude/hooks/*.sh` and the hook is not yet listed in `docs/hooks/README.md`.

---

### `protected-path-hook.sh`
**Event:** `PreToolUse` — **Matcher:** `Write|Edit`

**Purpose:** Guards writes to sensitive files. When a write targets a known-sensitive path, Claude is shown a confirmation banner and must pause to ask the user before proceeding.

**Paths covered:**
- `.env` and `.env.*` variants
- Cryptographic keys and certs: `.pem`, `.key`, `.p12`, `.pfx`, `.cert`, `.crt`, `.jks`, `.keystore`
- Credentials files: `credentials`, `.secrets`, `secrets`
- AWS credentials: `.aws/credentials`, `.aws/config`
- SSH directory: `.ssh/`

**Why it is needed:** LLM agents can write to any path they have access to. A misrouted write to `.env` or `.aws/credentials` could silently overwrite secrets. This hook adds an explicit human confirmation step before any write to a known-sensitive path pattern.

**Output:** Confirmation required banner (`╔══ Protected Path — Confirmation Required ══╗`) injected into Claude's context before the write. Claude must pause and explicitly ask the user before proceeding.

---

### `ledger-append-only.sh`
**Event:** `PreToolUse` — **Matcher:** `Write|Edit|MultiEdit`

**Purpose:** Hard-blocks (exit 2) Write/Edit/MultiEdit against the harness's own self-scored measurement ledgers. A metric an agent can also rewrite is not a measurement — modeled on `exo` (an autonomous-agent harness)'s single safety invariant that the agent cannot alter its own canonical event log.

**Paths covered (literal suffix match, no regex):**
- `.claude/memory/trust-score.jsonl`
- `.claude/memory/metrics.jsonl`
- `.claude/memory/caveman-savings.jsonl`
- `.claude/memory/learnings.jsonl`
- `.claude/memory/observations.md`

**Escape hatch:** `SDD_LEDGER_ROTATE=1` disables the block for that invocation, for housekeeping-agent's legitimate pruning/archival passes.

**What it does not block:** every real producer of these files appends via `>>`/`echo` from a Bash-run hook or routine script — a different tool (`Bash`), never seen by this matcher. Only Claude's own Write/Edit/MultiEdit tool calls against these exact files are blocked.

**Fail-closed on its own failures (2026-10-01):** if `python3` is missing or the event is malformed, the parse exits non-zero instead of collapsing to an empty path and `exit 0`. The hook then blocks whenever the raw event names a protected ledger (literal substring match) and allows everything else, so a broken interpreter cannot silently disable the block without also blocking every unrelated Write.

**Tests:** `hooks/claude/ledger-append-only.test.sh` (17 cases: block per protected file across all three tools, absolute-path match, allow on unrelated memory files and non-Write/Edit tools, the rotate escape hatch, and five fail-closed cases — malformed event, non-object `tool_input`, and `python3` off PATH).

**Why it is needed:** Without this hook, an agent under pressure to show improvement could quietly truncate or rewrite `trust-score.jsonl` or `learnings.jsonl` rather than earning the number honestly. The existing `protected-path-hook.sh` guards secrets, not the harness's own history.

**Output:** `BLOCKED: ...` message to stderr, `exit 2`. No output on allow (silent).

---

### `risk-zone-edit-gate-hook.sh`
**Event:** `PreToolUse` — **Matcher:** `Write|Edit|MultiEdit` — _(soft gate, never blocks)_

**Purpose:** Looks the target file up in `.claude/steering/risk-zones.md` (seeded weekly by `risk-zone-reseed-runner.sh` from git churn + test-file presence + `gitnexus impact`). If the file is flagged `red` or `yellow`, prints a banner naming the signal and asking Claude to check for characterization tests / run `gitnexus impact` before proceeding. Silent for `green` or unlisted files, or if the map doesn't exist yet.

**Why it's needed:** Blast radius and test coverage are the two things worth knowing before touching a file, and neither is visible from the file itself. A soft, not hard, gate — stale or wrong zone data must never block real work, same tradeoff `protected-path-hook.sh` makes for sensitive files.

**Output:** `╔══ Risk Zone: <zone> — <file> ══╗` banner naming the signal. See the `risk-zone-engine` skill.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `PreToolUse` matcher `Write|Edit|MultiEdit` in `templates/settings.json.template`, alongside `memory-discipline-hook.sh` / `protected-path-hook.sh` / `skill-validate-hook.sh`.

---

### `skill-validate-hook.sh`
**Event:** `PreToolUse` — **Matcher:** `Write|Edit`

**Purpose:** Quality gate for skill file authoring. Before any Write to `~/.claude/skills/<name>/SKILL.md`, validates that the YAML frontmatter is structurally correct and meets quality thresholds. Hard-blocks (exit 2) on errors; warns (exit 0) on vague phrasing.

**Rules enforced:**
- `name:` field must exist and be kebab-case (`[a-z][a-z0-9-]*`)
- `name:` value must match the file path slug (e.g. `name: my-skill` in `~/.claude/skills/my-skill/SKILL.md`)
- `description:` field must exist and be at least 25 characters
- Warns if description starts with vague starters: `a skill that`, `this skill`, `skill for`, `use this skill`, `provides`
- **Provenance (added 2026-09-01):** warns when a `SKILL.md` body points at a remote URL.
  Two severities, both advisory:
  - *remote instruction source* — a URL ending in `.md`/`.txt`/`.json`/`.yaml`/`.yml` on a line
    that also carries an adopt verb (`set up`, `install`, `read`, `fetch`, `load`, `follow`).
    This is the skill-supply-chain shape: the remote file can change after review.
  - *remote install* — a line carrying `curl`, `wget`, `npx`, or a pipe to `sh`/`bash`.
    Legitimate skills document installs (`agent-manager-skill` documents the Herdr one), so
    this notes the source rather than blocking it.

  Unlike the frontmatter rules, the provenance scan runs for **any** file named `SKILL.md`,
  not only those under `~/.claude/skills/` — a skill written anywhere carries the same risk.
  Implemented with substring tests only, no regex, per the repo-wide parsing ban.
- **Eval-verdict staleness (added 2026-09-06):** warns when a `SKILL.md` write no longer matches
  the verdict recorded beside it. `skill-eval-gate` Phase 6 writes `eval-verdict.json` into the
  skill's directory on PASS, carrying the sha256 of the instructions it measured. This hook
  hashes the incoming content and compares. Four findings, all advisory:
  - *changed since its PASS verdict* — hash mismatch. The recorded result describes different
    instructions; the edit ships unmeasured unless the gate is re-run.
  - *no eval-verdict.json* — an **existing** skill that never passed the gate. Deliberately
    silent when the `SKILL.md` does not yet exist on disk: a brand-new skill has not reached the
    gate yet, and warning there would fire on every creation and train the warning out.
  - *could not be read* / *records no skill_md_sha256* — a verdict file that cannot say which
    instructions it describes is treated as no verdict, never as an assumed pass.

  Hash, not date: a skill edited an hour after its eval has a same-day verdict that means
  nothing. Runs for any `SKILL.md`, like the provenance scan. No regex — `hashlib` and `json`.

**Exit codes:**
- `0` — valid (or file not in skills dir — hook is a no-op)
- `0` with warning banner — valid but description starts with a vague phrase, or a provenance or eval-verdict finding
- `2` — hard block: one or more errors must be fixed before write proceeds

**Tests:** `hooks/claude/skill-validate-hook.test.sh` (14 cases: both provenance severities, two
false-positive guards, frontmatter regressions, the non-`SKILL.md` no-op, and six eval-verdict
cases covering new/missing/current/stale/unreadable/hashless).

**Why it is needed:** Skill descriptions are the primary signal Claude uses to decide when to activate a skill. Vague, too-short, or mismatched names silently degrade trigger accuracy across all sessions. Catching these at write time is zero-cost compared to diagnosing misfired or missed skill activations later.

**Output:**
- On error: `╔══ Skill Quality Gate — BLOCKED ══╗` banner listing each error with the required frontmatter format
- On warning only: `╔══ Skill Quality Gate — WARNING ══╗` banner with advisory message
- On pass: no output (silent)

---

### `test-integrity-guard.sh`
**Event:** `PostToolUse` — **Matcher:** `Write|Edit|MultiEdit` — _(soft gate, never blocks)_

**Purpose:** After any Write/Edit/MultiEdit to a test file or a CI/coverage config, scans the change for "gradient-descent-to-green" signals — weakening the tests to make a red suite pass rather than fixing the code. Flags added skip/`xfail`/`@Disabled` markers, tautological or stub assertions (e.g. `assert True`), touched coverage thresholds (`--cov-fail-under`, `coverageThreshold`), and removed assertions. Prints a reminder asking Claude to confirm the change reflects a deliberate spec change rather than a shortcut to pass. Exits 0 always — advisory only, never blocks.

**Paths covered:** test files (`test_*`, `*_test.*`, `*.spec.*`, `*.test.*`, anything under a `test/`, `tests/`, `__tests__/` or `spec/` directory) and CI/coverage config (`pytest.ini`, `pyproject.toml`, `.coveragerc`, jest/vitest config, `.gitlab-ci.yml`, and YAML under `.github/workflows/`). A CamelCase `FooTest.java` at the repo root is **not** classified — Java tests are found via their `src/test/java/` directory, which is the Maven/Gradle convention.

**Why it's needed:** When a suite is red, the path of least resistance is to weaken the test, not fix the code — and that erodes the safety net silently. Catching the weakening at write time forces an explicit "is this a real spec change?" decision before the green checkmark is trusted. Pattern from Addy Osmani's "Agentic Code Review". Perrone's *What is Agentic Testing?* documents the same failure arriving from a new direction: an agentic test-repair tool that decides the app broke rather than its locator marks the test skipped and comments — silently dropping coverage. That is signal class 1 below.

**No regex (2026-09-03).** Every check in this hook used to be a Python `re` pattern, which put it in violation of the repo-wide ban in `ruff.toml` — a ban TID251 could not enforce here, because ruff only reads `.py` files and this Python lives in a shell heredoc. It is now literal-token membership plus `pathlib`. The tradeoff is deliberate: literal matching cannot express a word boundary, so `describe.skipBecause(` trips the `.skip(` probe. For a soft advisory that always exits 0, a rare extra line of output beats a pattern that silently matches the wrong span. `scripts/utils/check-no-regex.py` now enforces the ban across all shell files, and `hooks/claude/test-integrity-guard.test.sh` (39 cases) delegates its own no-regex assertion to that guard.

**Noise control:** Only fires on test files and CI/coverage config — silent on all other Write/Edit/MultiEdit operations. A skip marker already present in the *old* text is not an *added* skip and does not fire. Never blocks (exits 0 always).

**Output:** `⚠  test-integrity-guard — <filename>` banner listing the weakening signals detected, followed by the gradient-descent-to-green reminder. Silent on no match.

**Tests:** `hooks/claude/test-integrity-guard.test.sh` — 39 offline cases covering path classification, all four signal classes, false-positive resistance, and the always-exit-0 contract. `*.test.sh` is skipped by `install.sh`'s hook copy loop, so it stays in the harness repo.

---

### `ruff-quality-gate-hook.sh`
**Event:** `PostToolUse` — **Matcher:** `Write|Edit|MultiEdit` — _(soft gate, never blocks)_

**Purpose:** `CLAUDE.md`'s Quality Gates section states "`ruff check`: on every `.py` file write" as automated — but until this hook, nothing actually ran it. A `Bash(ruff check *)` permission entry only let Claude run it if it remembered to; there was no hook enforcing it. This hook runs `ruff check` on any `.py` file touched by a Write/Edit/MultiEdit and surfaces findings back into context. Silently no-ops if `ruff` isn't installed on the machine, or if the touched file isn't Python.

**Why it's needed:** Closes a real gap between what the harness's own docs claimed was automated and what an audit found was actually wired — extracted while auditing `github.com/memcode-ai/memcode` for the same "quality gate on write" pattern.

**Noise control:** Only fires on `.py` files with real `ruff check` findings — silent when clean, silent on non-Python files, silent when `ruff` isn't installed. Never blocks (exits 0 always).

**Repo config:** in the harness repo itself the rules come from `ruff.toml` at the root, which sets `line-length = 100` and — the one rule there that is not style — bans importing `re` or `regex` via `flake8-tidy-imports` `banned-api` (TID251). Added 2026-08-20 after a `(\d+)%` pattern read "24.5%" as 5 and the dashboard displayed 5% for weeks without erroring; every regex under `scripts/` was replaced with an explicit parser the same day. Values a program has to read back get emitted as structured data at the source instead — `scripts/session/record_metric.py` is the reference.

**Output:** `⚠  ruff-quality-gate — <filename>` banner with the raw `ruff check` findings. Silent on no findings.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `PostToolUse` matcher `Write|Edit|MultiEdit` in both `templates/settings.json.template` and `templates/settings.harness.json.template`.

---

### `js-quality-gate-hook.sh`
**Event:** `PostToolUse` — **Matcher:** `Write|Edit|MultiEdit` — _(soft gate, never blocks)_

**Purpose:** The sibling of `ruff-quality-gate-hook.sh` for the other half of the languages the harness installs into. `ruff check` fired on every `.py` write; **nothing fired on a `.ts`/`.tsx`/`.js`/`.jsx` write at all**, so agent-written TypeScript reached the human unlinted in every project the harness ships to. Runs on any JS/TS file touched by Write/Edit/MultiEdit:

1. Selects on extension — `.ts`, `.tsx`, `.mts`, `.cts`, `.js`, `.jsx`, `.mjs`, `.cjs`. Skips `.d.ts` (no runtime logic) and anything under `node_modules/`, `dist/`, `build/`, `.next/`, `coverage/`, `vendor/`.
2. Prefers `oxlint` (millisecond-scale, and the runner `dmmulroy/anti-slop` targets), falls back to `eslint`, no-ops silently when neither is installed.
3. Suppresses the linters' "0 problems" summary so a clean write prints nothing.
4. Adds an extra callout when the finding names an **anti-slop** rule (`no-chained-type-assertions`, `no-unknown-parameters/returns/type-aliases`, `no-unsafe-dictionary-type`, `no-known-value-widening`, `no-widen-then-assert`, `require-safety-comment-for-type-assertion`, `no-runtime-typeof`, `no-module-mocking`) — those mean type evidence was discarded, not that style drifted, so the fix is to recover the real type rather than silence the rule.
5. Independently of 1–4 — and even when neither `oxlint` nor `eslint` is installed — runs `tailwind_design_token_check()` when a `tailwind.config.{js,ts,mjs,cjs}` exists at the repo root: regex-only checks (no lint plugin required) for raw hex/rgb colors inside `className`, Tailwind arbitrary-value syntax (`w-[13px]`), and template-literal class names built with interpolation (breaks the JIT scanner's static-string requirement). Rules are the shadcn-ui/lint baseline; see `kiro/settings/rules/deterministic-enforcement.md`.

**Why it's needed:** Complexity linting and type-evidence linting are independent axes, and agent-written TypeScript fails the second far more often — reaching for a cast or `unknown` to quiet the compiler. Extracted from `github.com/dmmulroy/anti-slop`. The rules themselves are **vendored per repo by design** (`npx skills add dmmulroy/anti-slop --skill install-anti-slop`); this hook is only the enforcement point, and `guardrails-agent` is what proposes the rule set. Nothing is fabricated here — the hook runs whatever the repo actually configured.

**Noise control:** Silent on non-JS/TS files, on declaration files, on vendored/build paths, on clean results, and on machines with no JS linter. The Tailwind check is separately gated on `tailwind.config.*` existing, so it stays silent on non-Tailwind repos regardless of linter availability. Best-effort 20s wall-clock guard via `timeout`/`gtimeout` when present (eslint on a large project can be slow; oxlint cannot). Never blocks (exits 0 always).

**Output:** `⚠  js-quality-gate (oxlint|eslint) — <filename>` banner with raw linter findings, plus the anti-slop callout line when applicable. Silent on no findings.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `PostToolUse` matcher `Write|Edit|MultiEdit` in `templates/settings.json.template`, `templates/settings.harness.json.template`, and this repo's `.claude/settings.json`. Tests: `bash hooks/claude/js-quality-gate-hook.test.sh` (21 cases, stubs the linter so the suite passes with no JS toolchain installed).

---

### `headless-envelope-hook.sh`
**Event:** `SessionStart` — **Matcher:** `""` — _(context injection; cannot block)_

**Purpose:** Applies a **stricter** operating envelope to unattended runs only. Seven headless entry points — the six `scripts/routines/*-runner.sh` and `daily-orchestrator.sh`'s drift review — all invoke `SDD_HEADLESS=1 claude --print --permission-mode bypassPermissions`, so the least-supervised sessions in the harness were running with the widest permissions and no human backstop. Before this hook, `SDD_HEADLESS` was read only to *suppress* interactive behaviour (`stop-hook.sh`, `caveman-savings-hook.sh`, `scripts/utils/dashboard.py`); nothing anywhere read it to *tighten* behaviour. The injected envelope carries six rules:

1. **One unit of work** — do exactly what the routine prompt asks; record adjacent work in the report instead of starting it.
2. **No history-rewriting or publishing git** — no `push`, `reset --hard`, `rebase`, `--force`, branch/tag deletion; commit only when the routine prompt says to.
3. **Writes stay in the routine's lane** — report files and `.claude/memory/` are always fair game; `skills/`, `hooks/`, `agents/`, `commands/`, `templates/`, `CLAUDE.md`, `settings.json`, `.claude/behaviors/` must be *proposed* rather than edited **unless the routine prompt explicitly names that artifact class as its output**, and then only within the caps that prompt states. The carve-out is load-bearing, not softness: `harness-health-prompt.md` step 3 tells the agent to rewrite up to 3 `SKILL.md` files per run (gated on a ≥2-point score improvement), and `daily-maintenance-prompt.md` step E drafts up to 3 `BEHAVIOR.md` specs. A flat prohibition would have broken both. Permission for one artifact class never generalises to another.
4. **Two-strike loop guard** — same action fails twice → stop, write `ESCALATION: <what failed, what was tried, what a human should check>` into the report, move on. Never a third attempt.
5. **Report honestly** — partial completion is acceptable, fabricated completion is not; never widen scope to look productive.
6. **No new dependencies** — no package installs, MCP servers, cron or launchd entries; escalate per rule 4 instead.

**Why it's needed:** Extracted from the "global rules vs factory rules" separation in the AI-dark-factory walkthrough (`youtube.com/watch?v=eecUhBpTz_g`) — the rules that apply with a human watching are not the rules that should apply when nobody is. The two-strike guard comes from the same source's stated reason for keeping a human fail-safe: agents "go through an infinite loop of trying to fix a problem."

**Noise control:** Emits **zero bytes** in every interactive session. The gate is the environment (`SDD_HEADLESS=1`), not the event payload, and it is an exact-match test — `0`, empty, `true`, and `11` all stay silent.

**Opt-out:** `SDD_SKIP_HEADLESS_ENVELOPE=1`, set inside a specific runner that legitimately needs the wider envelope (e.g. a future runner whose job *is* committing). Never set it globally.

**Output:** `=== UNATTENDED RUN — STRICTER ENVELOPE APPLIES ===` block on stdout, which SessionStart folds into context. Exits 0 in both modes; drains stdin so a large payload can never make it hang.

**Location:** ships in `hooks/claude/`, copied to each project's `.claude/hooks/`; wired via `SessionStart` in `templates/settings.json.template`, `templates/settings.harness.json.template`, and this repo's `.claude/settings.json`. Tests: `bash hooks/claude/headless-envelope-hook.test.sh` (18 cases covering gate, opt-out, rule presence, exit code, stdin drain).

---

### `agent-trace-hook.sh`
**Event:** `PostToolUse` — **Matcher:** `Agent`

**Purpose:** `trace.log` had zero reliable producers — population was a manual prose instruction buried in two rarely-run commands (`harness-validate.md`, `validate-adversarial.md`), so the file never actually got created despite `session-judge`, `stop-hook.sh`, and `evolve.md` all reading it. This hook makes every subagent spawn write its own trace entry deterministically. Buffers the PostToolUse event JSON from stdin, extracts `subagent_type`/`model`, derives a duration hint from the model tier (`haiku`→fast, `sonnet`→medium, `opus`→slow), and derives outcome: `dispatched` for background spawns (no `tool_response` yet — real result arrives later, out of band), else `pass`/`error` via keyword scan of the response.

**Format:** `YYYY-MM-DD HH:MM | agent | tier | outcome | duration-hint` (see `kiro/settings/rules/agent-tracing.md` for the full field spec, incl. optional `alignment`/`structural` fields this hook doesn't populate).

**Known limitation:** for background spawns (the default), the PostToolUse event fires at dispatch, not completion — `tool_response` is the spawn ack, not the agent's real result, so outcome is recorded as `dispatched` rather than pass/fail. Real per-agent success/failure tracking for background agents would need a hook on the completion notification, which Claude Code does not currently expose. Foreground agents (`run_in_background: false`) get a real `pass`/`error` outcome.

**Why it's needed:** `session-judge`, `stop-hook.sh`'s agent-failure-pattern check, and the `evolve` agent all read `trace.log` for evidence-based improvement — none of that works if the file is empty.

**Output / side effect:** Appends one line to `.claude/memory/trace.log`. Self-archives past 200 lines to `.claude/memory/glacier/trace-<date>.log`. Never blocks or errors the tool flow (always exits 0).

---

### `agent-behavior-guard.sh`
**Event:** `PreToolUse` — **Matcher:** `Read|Bash|WebFetch|WebSearch` — _(monitor-only by default, never blocks unless enforced)_

**Purpose:** Ported from perplexityai/numbat's rule-engine design (network indicators, persistence, chained/sequence findings) — scoped down for a single local harness: no rule files, no versioning, no signed bundles. Covers three detections none of the existing per-event hooks reach:

1. **`network_indicator`** — a Bash command or WebFetch/WebSearch target references a cloud-metadata SSRF endpoint (`169.254.169.254`, `metadata.google.internal`, `metadata.azure.com`, etc.)
2. **`persistence`** — a Bash command writes to crontab, a shell rc file, `~/.ssh/authorized_keys`, or enables a systemd/launchd unit
3. **`chained_secret_egress`** — a secret-bearing path (`.env`, `.pem`/`.key`/credentials files, `.aws/credentials`, `.ssh/`) is accessed via Read or Bash, then *later in the same session* an egress call happens (Bash `curl`/`wget`/`ssh`/`/dev/tcp/`/etc., or any WebFetch/WebSearch) — correlated across calls via a per-session ledger

**Why it's needed:** `protected-path-hook.sh` only fires on `Write|Edit` and is stateless per call — it never sees `Read`, Bash-based egress, or a pattern that spans multiple tool calls. This hook fills that specific gap: read-then-exfiltrate and metadata-SSRF/persistence patterns that a single-event, single-tool guard cannot catch.

**Default mode:** MONITOR ONLY — logs the finding and warns to stderr, always exits 0. Set `SDD_AGENT_GUARD_ENFORCE` to a comma-separated list of rule names (`network_indicator`, `persistence`, `chained_secret_egress`) or `all` to make matching rules hard-block (exit 2), mirroring numbat's monitor→enforce promotion without its rule-file machinery.

**Enforce mode fixed 2026-10-01:** until then the script ended in a bare `exit 0` that discarded the Python's exit code, so enforce mode logged `"mode": "enforce"` findings but **never blocked anything**. The Python's exit code is now the verdict. With any rule enforced, the guard also fails closed on its own failures (malformed event, `python3` missing, crash → exit 2); in monitor mode those stay silent.

**Tests:** `hooks/claude/agent-behavior-guard.test.sh` — 13 cases: each rule blocking under enforce, chained egress scoped to its session, enforce scoped to the named rule, monitor never blocking, and fail-closed vs. monitor on a malformed event and with `python3` off PATH. Runs the hook from a throwaway cwd so it never touches the repo's findings ledger.

**Output / side effect:** Appends one JSON line per finding (monitor or enforce) to `.claude/memory/agent-security-findings.jsonl`. Secret-access events for the chained-egress rule are recorded in `.claude/memory/.agent-behavior-guard-secret-access.jsonl`, keyed by `session_id`.

---

### `git-destructive-guard-hook.sh`
**Event:** `PreToolUse` — **Matcher:** `Bash` — _(hard block)_

**Purpose:** Blocks destructive git/gh operations independent of `settings.json`'s declarative allow/deny list (observed to not reliably block `git push --force` in some sessions even with an explicit deny entry present). Inspects the literal Bash command and exits 2 on any match. Blocks: any force-push variant, remote branch deletion via push (`--delete`, empty-refspec `:branch`), mirror push, local force branch delete (`git branch -D`), `gh repo delete`, and `git rebase` (rewrites shared history the same way a force-push does).

**Why it's needed:** Soft nudges (`protected-path-hook.sh`) only warn — this is the one place in the harness that actually refuses to run a destructive git/gh command.

**Matching detail (rewritten 2026-08-25 — was a bypassable string match):** the command is tokenized with `shlex` (a real shell lexer), split on shell operators into individual commands, and compared **token-by-token against exact flag names**. It recurses into `bash -c '...'` wrappers, skips `git` global options so `git -C dir push --force` and `git -c k=v push --force` still resolve to the `push` verb, strips leading `VAR=value` assignments, and rejects `git -c alias.*` outright (an alias hides the real verb).

The previous implementation regex-stripped quoted segments and then `grep -E`'d the remaining raw text. That is a string match over a rendered value, and every one of these defeated it while remaining a real force-push: `F=--force; git push $F`, `bash -c 'git push --force'`, `git push --fo""rce`, `cd sub && git push --force`. Guard rules must normalize before comparing — the same class of bug as blocking the literal string `169.254.169.254` while `curl http://2852039166/` reaches the same address. See `skills/agent-permissions-design/SKILL.md` § "Verdict Computation and Context-Dependence".

**Fail-closed behavior:** if the command cannot be parsed (unbalanced quotes) or a destructive-capable verb carries an unresolved expansion (`$VAR`, `$(...)`, backtick) that could expand to a flag, the hook blocks and asks for the literal value. This deliberately over-blocks on a narrow set of high-stakes verbs — `git push`, `git branch`, `git rebase`, `gh repo` — and nowhere else.

The guard also fails closed on **its own** failures (2026-10-01). If `python3` is missing, the event is malformed, or the analyzer crashes, the hook blocks any event that mentions `git` or `gh ` and allows the rest. Before this, both parse steps swallowed errors (`|| echo ""`, `|| REASON=""`) and fell through to `exit 0`, so a broken interpreter silently disabled the only hard-block on destructive git.

**No `ask` verdict, by design:** the hook only ever allows or hard-blocks. The harness's routine runners (`scripts/orchestration/daily-orchestrator.sh`, `scripts/routines/*`) run headless, where a prompt-the-human verdict is unanswerable and silently degrades to a hang or an implicit allow.

**Quoted text still safe:** `git commit -m "document git push --force risks"` is not blocked — `shlex` keeps the message as a single token and the verb resolves to `commit`, which is not checked.

(Original checks ported from claude-codex-settings' `ultralytics-dev` plugin, github.com/fcakyon/claude-codex-settings, which also added the `git rebase` check. Normalization rewrite prompted by Google ADK's `long-horizon-harness/horizon/guardrails/exfil_guard.py`, 2026-08.)

**Output / side effect:** `BLOCKED: ...` to stderr with reason and the offending command, exit 2. Silent (exit 0) on anything else.

**Tests:** `hooks/claude/git-destructive-guard-hook.test.sh` — 52 cases covering baseline blocks, the four regex-era bypasses above, six fail-closed cases (malformed event, non-string command, `python3` off PATH), and a false-positive guard block (normal pushes, commit messages that mention `--force`, `git status`, `gh repo view`). Run `bash hooks/claude/git-destructive-guard-hook.test.sh`.

---

### `agent-commit-attribution-hook.sh`
**Event:** `PreToolUse` — **Matcher:** `Bash` — _(soft, never blocks)_

**Purpose:** Warns before a `git commit` whose inline message carries no `Co-Authored-By` trailer.

1. Extracts the commit message from `-m`, `--message`, `--message=`, `-mmsg`, and short bundles such as `-am`
2. Checks the assembled message for a `Co-Authored-By:` trailer (case-insensitive)
3. Prints the trailer to append, then exits 0 — the commit is never blocked

**Why it's needed:** this is a measurement repair, not a style nag. `skills/keep-rate/SKILL.md` selects agent-authored commits with `git log --all --grep="Co-Authored-By: Claude"`, and the keep-rate widget in `scripts/utils/dashboard.py` blames against the same set. An agent commit that ships without the trailer drops silently out of the denominator, so keep-rate reads **high** — the failure is invisible and biased in the flattering direction. As of 2026-08 only 10 of the last 30 commits in this repo carried the trailer.

**Why `PreToolUse` and not `.git/hooks/commit-msg`:** a git hook sees a commit, not an author, so it can only nag on every commit or none. A PreToolUse Bash hook can tell the difference — if Claude issued the command, it is an agent commit by definition. Enforce identity at the chokepoint that knows who acted. (Framing from onecli's gateway, which rewrites commit payloads in-flight because GitHub App tokens have "no natural author identity" — github.com/onecli/onecli.)

**Why soft:** matches `address-check-hook.sh`, the harness's existing precedent for "did a CLAUDE.md instruction survive compaction" — a passive signal for a human to act on, not a gate.

**Exclusions (silent, no warning):** `--amend --no-edit`, `--squash`, `--fixup`, `-C`/`--reuse-message`, `-c`/`--reedit-message`, and any `git commit` with no inline message (an editor session, whose content the hook cannot see). These are commits whose message is generated or inherited rather than authored — the same reason onecli's gateway skips merge endpoints.

**Output / side effect:** `[attribution] ...` block to stdout naming the trailer to append, exit 0. Silent on trailered commits, excluded forms, and non-commit commands.

**Tests:** `hooks/claude/agent-commit-attribution-hook.test.sh` — 22 cases. Run `bash hooks/claude/agent-commit-attribution-hook.test.sh`.

---

### `ai-writing-guard-hook.sh`
**Event:** `PreToolUse` — **Matcher:** `Write|Edit|MultiEdit|Bash` — _(hard block)_

**Purpose:** Blocks AI-sounding word/phrase patterns (a small swap-list of overused words like `leverage`/`delve`, a few cliche openers like `it is important to note`, and hedge words such as `crucial`/`significant` repeated 3+ times) before they land in a file write/edit or a `git commit` / `gh` command's message text. Scoped so it never touches real code: markdown files are checked whole (minus fenced/inline code, so words inside backticks are exempt), code files only in `#`/`//`/`/* */` comments and docstrings, Bash only in the `-m`/`--message`/`-b`/`--body`/`-t`/`--title` value or heredoc body of a git/gh command. Em-dash is deliberately NOT flagged — this harness's own docs and hook banners already use it as house style.

**Why it's needed:** None of the harness's other hooks check how text reads — they cover security/git-safety/behavior, not style drift. Long sessions drift toward AI-sounding writing that a prompt reminder won't reliably self-police; this is a hard-block domain like the destructive-git guard above, not a nudge domain. Ported from claude-codex-settings' `humanize` plugin (github.com/fcakyon/claude-codex-settings).

**Output / side effect:** On a match, emits `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", ...}}` naming the offending word/phrase and a plain swap. Silent (exit 0) on clean text or unrecognized file types.

---

### `reject-feedback-hook.sh`
**Event:** `UserPromptSubmit` — **Matcher:** _(all)_ — _(soft, never blocks)_

**Purpose:** When the user rejects or interrupts a tool call, their explanation arrives as their next prompt. This hook walks the transcript backward to detect that pattern, classifies the explanation into a reject reason (`wrong_target`, `tool_steering`, `scope_drift`, `verify_first`, `rule_setting`, `factual_challenge` — other categories like profanity or a bare "no" are treated as noise and dropped), and appends a `[friction]` line to `observations.md` for actionable categories only.

**Why it's needed:** The harness's auto-memory system asks Claude to manually notice corrections and save `feedback` memories — inconsistent in practice. This makes the signal systematic instead of relying on Claude catching every pushback.

**Explicitly NOT a duplicate of the tool-failure-memory loop:** `tool-failure-capture.sh`/`tool-failure-recall.sh` record when a Bash/MCP call ran and errored (a command-execution signal, with its own ledger and promotion routine). This hook fires on the user declining or redirecting a proposed action — a different signal — and deliberately reuses the existing `observations.md` append convention (same file `revert-detect-hook.sh` and `action-capture.sh` write to) instead of inventing a parallel file or pipeline.

**Output / side effect:** Appends `- YYYY-MM-DD [friction]: tool rejected (<category>) — "<excerpt>"` to `observations.md`. De-duplicates by excerpt. Ported from claude-codex-settings' `claude-telemetry-hooks` plugin (github.com/fcakyon/claude-codex-settings), with its OTel export dropped — this harness has no OTel backend configured.

---

### `subagent-context-hook.sh`
**Event:** `SubagentStart` — **Matcher:** none (every agent type)

**Purpose:** Injects the harness's load-bearing conventions directly into each spawned subagent's context, so a child agent starts knowing them rather than re-deriving or violating them.

**Why it's needed:** `CLAUDE.md`, `.claude/rules/`, and `SessionStart` hook output are parent-thread only. A subagent begins without any of it. Until `SubagentStart` existed the only lever was to nudge the *parent* at `PreToolUse:Agent` and hope it briefed the child (`gbrain-agent-spawn.sh`) — a request, not a guarantee. This is the guarantee.

**Rules injected** (kept deliberately short — this is paid per spawn, so it competes with the actual task for attention):
1. **Tools** — one line: prefer `ctx_*` over native equivalents, native Grep/Glob are policy-denied, native Read is for the edit gate. The full native→`ctx_*` mapping table is **deliberately not repeated here** — the lean-ctx MCP server states it in its own `instructions` block, which reaches the subagent anyway. (Trimmed 2026-09-01; it was previously stated in four always-loaded surfaces at once.)
2. **Blast radius** — *one* ordered check, not three: Serena `find_referencing_symbols` for a Python symbol → GitNexus `impact` otherwise → `ctx_callgraph(action="callers")` when the index is broken or the edit is not symbol-shaped. A tool that errors gives **no answer**; it does not report "no callers". Also: Serena diagnostics after any `.py` edit. (Consolidated 2026-09-01 — these were three competing MUSTs across `CLAUDE.md`, `rules/lean-ctx.md`, and this hook, with no precedence, one of them naming a currently-broken index.)
3. **Parsing** — no regex over free text to extract structured facts; emit structured data at the source.
4. **Evidence** — no completion claim without verification evidence from this run; hedged future tense is a tell; a non-zero probe exit is an answer, not a failure; report skipped or blocked steps.
5. **Scope** — change size ≤1 module (worded "change size", not "blast radius", so it does not collide with rule 2); Rule of Three before extraction; never commit installed harness output (`.claude/`, `specs/`, `CLAUDE.md`, `AGENTS.md`, `ERRORS.md`).
6. **Reporting** — address the user as "Husband"; end with Files changed / What changed / Not touched.
7. **Handoff pointer** — appended only when `.claude/memory/handoff/latest.md` exists and is <24h old. A stale pointer is worse than none, so freshness is checked with `find -mtime -1` rather than assumed.

**Two implementation constraints, both load-bearing:**
- **Must emit JSON, not plain stdout.** Claude Code adds plain-text stdout as context only for `UserPromptSubmit`, `UserPromptExpansion`, `SessionStart`, and `PostModelSwitch`. Every other hook in this directory uses `cat << 'RULES'`; that pattern is silently discarded here. This hook writes `hookSpecificOutput.additionalContext` via `jq`.
- **Must never block on stdin.** A hook that waits on a read which never completes stalls the subagent spawn itself. The read is bounded (`read -t 2`) and every failure path still injects — `agent_type` only tailors the text, so losing it degrades the message, not the mechanism.

**Output / side effect:** One JSON object on stdout. Silent (exit 0, no output) when `SDD_SKIP_SUBAGENT_CONTEXT=1`. Tests: `hooks/claude/subagent-context-hook.test.sh` (14 cases, including a FIFO stall case that fails if the hook ever blocks).

---

### `claudemd-edit-notice.sh`
**Event:** `PostToolUse` — **Matcher:** `Write|Edit|MultiEdit` — _(soft gate, never blocks)_

**Purpose:** Says that a just-written `CLAUDE.md`, `CLAUDE.local.md`, or `AGENTS.md` is **not active in the running session**. Fires on the basename only, wherever the file lives — project root, a package subdirectory, or `~/.claude/`.

**Why it's needed:** Project-root and user-level instruction files are read once at session start and held in memory. Editing one mid-session changes the file on disk and changes nothing about the session that is running. That is not a hypothetical here — `harness-fix-agent`, `skill-augment-agent`, `claudemd-review` and `/kiro:evolve` all write `CLAUDE.md` mid-session and then continue as though the rule they just wrote is in force. It is not, and the agent cannot observe its own stale context, so no prompt or skill can catch this. Only something outside the model's context can say so.

**Strength:** Soft. The write already happened and this does not undo it. The remedy it names is `/compact`, `/clear`, or a restart.

**Exit code is 2, deliberately.** For `PostToolUse`, stdout goes to the debug log and stderr on exit 0 is never shown to Claude. Exit 2 is the documented way to surface stderr from this event — the tool already ran, so it warns without blocking. An `echo` on exit 0 here would be a hook that appears to work and does nothing.

**Parsing:** reads `.tool_input.file_path` (falling back to `.tool_input.path`) with `jq`, takes the basename, and compares against a literal three-name list. Structured fields and exact tokens only — a substring match would fire on `templates/CLAUDE.md.template`, which the tests assert it does not.

**Output / side effect:** `[claudemd-edit-notice] <file> was edited — the change is NOT active in this session.` plus the remedy, on stderr, exit 2. Silent (exit 0) on every other file, on malformed or empty input, when `jq` is missing, and when `SDD_SKIP_CLAUDEMD_NOTICE=1`. Tests: `hooks/claude/claudemd-edit-notice.test.sh` (18 cases, including the template and `claude.md` near-misses and a directory literally named `CLAUDE.md`).

**Source:** extracted 2026-09-06 — see `docs/sources/articles/README.md`, the "19 Claude Code mistakes" entry.

---

### `todo-focus-hook.sh`
**Event:** `PostToolUse` — **Matcher:** `TodoWrite`

**Purpose:** Enforces one in-progress todo at a time. Counts `in_progress` entries in the written list and, when there is more than one, names the competing items and asks for one to be picked.

**Why it's needed:** `TodoWrite` accepts any number of concurrent `in_progress` entries and enforces nothing. The failure is not cosmetic — an agent that marks four items in progress starts all four, splits attention, and finishes none cleanly. The single-active constraint is what makes a todo list a work queue instead of a wish list.

**Strength:** Soft. The write already happened and this hook does not undo it.

**Exit code is 2, deliberately.** For `PostToolUse`, stdout goes to the debug log and stderr on exit 0 is never shown to Claude. Exit 2 is the documented way to surface stderr from this event — the tool already ran, so it warns without blocking. An `echo` on exit 0 here would be a hook that appears to work and does nothing.

**Parsing:** reads `.tool_input.todos[].status` with `jq`. Structured fields only — no prose pattern-matching.

**Output / side effect:** `[todo-focus] N todos are in_progress at once:` plus the competing item names, on stderr, exit 2. Silent (exit 0) at 0 or 1 active items, on any non-`TodoWrite` tool, on malformed input, or when `SDD_SKIP_TODO_FOCUS=1`. Tests: `hooks/claude/todo-focus-hook.test.sh` (15 cases).

---

## Hook Wiring Reference

Verified directly against `.claude/settings.json` on 2026-09-06 (not just this doc's prior claims):

All 44 registrations below are live — counted with the `jq` command in this section, not carried over from the previous sync. Regenerate this block from the real config with:

```bash
jq -r '.hooks | to_entries[] | .key as $e | .value[] | .matcher as $m | .hooks[]
       | [$e, ($m//""|if .=="" then "(all)" else . end),
          (.command|sub(".*/hooks/";"")|sub("\"$";""))] | @tsv' .claude/settings.json | sort
```

```
SessionStart     (all)                                → session-start-hook.sh
SessionStart     (all)                                → headless-envelope-hook.sh
SessionStart     (all)                                → caveman-activate.js  [global, ~/.claude/hooks/]
SessionStart     .*                                   → lean-ctx hook observe  [global]
Stop             (all)                                → stop-hook.sh
Stop             (all)                                → address-check-hook.sh   [HARNESS-ONLY]
Stop             (all)                                → caveman-savings-hook.sh
Stop             (all)                                → verification-retry-hook.sh
Stop             .*                                   → lean-ctx hook observe  [global]
UserPromptSubmit (all)                                → prompt-hook.sh
UserPromptSubmit (all)                                → doc-parse-nudge.sh
UserPromptSubmit (all, keyword-gated)                 → frontend-security-nudge.sh
UserPromptSubmit (all, keyword-gated)                 → pr-mention-nudge.sh
UserPromptSubmit (all)                                → reject-feedback-hook.sh
UserPromptSubmit (all)                                → caveman-mode-tracker.js  [global]
UserPromptSubmit .*                                   → lean-ctx hook observe  [global]
PreToolUse       Bash                                 → git-destructive-guard-hook.sh
PreToolUse       Bash                                 → agent-commit-attribution-hook.sh
PreToolUse       Bash                                 → pr-evidence-hook.sh
PreToolUse       Bash                                 → cheap-model-delegation-hook.sh
PreToolUse       Bash|mcp__.*                         → tool-failure-recall.sh
PreToolUse       Bash                                 → bash …  [global, lean-ctx shell allowlist]
PreToolUse       Grep|Glob                            → …       [global, lean-ctx — denies native Grep/Glob]
PreToolUse       Read                                 → …       [global, lean-ctx read gate]
PreToolUse       Read                                 → lean-ctx-nudge-hook.sh  [project-local hard gate, distinct from the global lean-ctx read gate above]
PreToolUse       Write|Edit                           → memory-discipline-hook.sh
PreToolUse       Write|Edit                           → protected-path-hook.sh
PreToolUse       Write|Edit                           → skill-validate-hook.sh
PreToolUse       Write|Edit|MultiEdit                 → risk-zone-edit-gate-hook.sh
PreToolUse       Write|Edit|MultiEdit|Bash            → ai-writing-guard-hook.sh
PreToolUse       Read|Edit|MultiEdit                  → pre-tool-use-gitnexus.sh
PreToolUse       Agent                                → gbrain-agent-spawn.sh
PreToolUse       Agent                                → prompt-quality-check.sh  [see docs/prompt-quality/README.md]
PreToolUse       mcp__…claude-mem…save_observation    → gbrain-memory-write.sh
PreToolUse       mcp__raindrop__                      → raindrop-best-practices.sh
PreToolUse       WebFetch|WebSearch                   → gbrain-external-search.sh
PreToolUse       Read|Bash|WebFetch|WebSearch         → agent-behavior-guard.sh
SubagentStart    (all)                                → subagent-context-hook.sh
PostToolUse      Write|Edit                           → impeccable-detect-hook.sh
PostToolUse      Write|Edit                           → hook-added-notify.sh
PostToolUse      Write|Edit                           → lean-ctx-nudge-hook.sh
PostToolUse      Write|Edit  (*/skills/*/SKILL.md)    → skill-permissions-gate.sh
PostToolUse      Write|Edit|MultiEdit (test/CI cfg)   → test-integrity-guard.sh
PostToolUse      Write|Edit|MultiEdit (.py only)      → ruff-quality-gate-hook.sh
PostToolUse      Write|Edit|MultiEdit (.ts/.js only)  → js-quality-gate-hook.sh
PostToolUse      Write|Edit|MultiEdit (CLAUDE/AGENTS) → claudemd-edit-notice.sh
PostToolUse      Write|Edit|MultiEdit                 → sloppiness-warn-hook.sh
PostToolUse      Bash                                 → revert-detect-hook.sh
PostToolUse      Bash                                 → setup-buffer-hook.sh
PostToolUse      Bash                                 → action-capture.sh
PostToolUse      Bash                                 → pr-auto-create-hook.sh
PostToolUse      Bash                                 → pr-risk-tier-hook.sh
PostToolUse      Bash                                 → gitnexus-hook.cjs  [global]
PostToolUse      Agent                                → agent-trace-hook.sh
PostToolUse      Skill                                → skill-usage-tracker.sh
PostToolUse      TodoWrite                            → todo-focus-hook.sh
PostToolUse      Read                                 → lean-ctx hook read-dedup  [global]
PostToolUse      .*                                   → lean-ctx hook observe  [global]
PostToolUseFailure Bash|mcp__.*                       → tool-failure-capture.sh
PreCompact       (all)                                → compaction-discipline-hook.sh
PreCompact       .*                                   → lean-ctx hook observe  [global]
SessionEnd       .*                                   → lean-ctx hook observe  [global]
```

**Nothing is unwired.** Until 2026-08-30 this section carried a second list of 14
hooks that were registered in `templates/settings.json.template` (shipped to every
project) but missing from `templates/settings.harness.json.template` (which
generates this repo's own config). The direction of that drift was the harmful
one: those hooks fired in every installed repo while *not* firing in the repo
where they are written and tested — including the entire tool-failure-memory
loop (`tool-failure-recall.sh` + `tool-failure-capture.sh`).

The two templates are now reconciled and the relationship is enforced:

```
hooks(harness template) == hooks(project template) + HARNESS_ONLY
```

`scripts/setup/reconcile-settings-templates.py` owns that rule. `--check` fails on
drift and runs inside `/kiro:harness-validate`; `--sync` regenerates the harness
template and runs inside both `install.sh` and `update.sh` before either copies a
template anywhere.

**Add shared hooks to `templates/settings.json.template` and run `--sync`. Never
edit the harness template directly** — that is precisely the drift the check exists
to catch, and it will fail the next validate.

`HARNESS_ONLY` currently holds exactly one entry, `address-check-hook.sh`: it
enforces the "Husband" address convention, which lives in the harness repo's own
CLAUDE.md and is deliberately absent from `templates/CLAUDE.md.template`, so in any
other project it would log violations of a rule that repo never adopted. Every
addition to that list needs a written reason for the same standard.

Permissions are deliberately **not** reconciled and are excluded from the check.
The harness repo grants itself write access to its own source tree (`hooks/`,
`scripts/`, `templates/`, `agents/`, `kiro/`) that no target project may have, and
denies `git push*` outright where projects only deny force-push.

---

## Adding a New Hook

1. Write the script to the harness source at `hooks/claude/<name>.sh` — **not** to an installed `.claude/hooks/` copy, which `update.sh` overwrites. `install.sh`/`update.sh` propagate every `hooks/claude/*.sh` to every project automatically and `chmod +x` it; no per-hook edit to the installers is needed.
2. Add the wiring entry to `templates/settings.json.template` so the hook ships everywhere, and to the local `.claude/settings.json` so it fires this session.
3. Document it in this file (the `hook-added-notify.sh` hook will remind you if you forget).
4. Update the Wiring Reference table above.
5. For any hook that blocks, or whose matching logic is non-trivial, add `hooks/claude/<name>.test.sh` with both block *and* allow cases. Files matching `*.test.sh` are skipped by the installers' copy loop, so they stay in the harness repo and never ship as runtime hooks. Run them with `bash hooks/claude/<name>.test.sh`.

**Matching rule for any guard hook:** parse the command into its structure (argv via `shlex`, URLs via a URL parser) and compare tokens exactly. Do not substring- or regex-match the rendered command text — that is defeated by re-rendering the same value, and `git-destructive-guard-hook.sh` shipped with exactly that bug until 2026-08-25.

**No regex, continued (2026-09-30).** `prompt-quality-check.sh` was rewritten off `re` onto the same literal-token pattern: the prompt is tokenized to lowercase words (apostrophes stay inside a word, every other non-alphanumeric character separates, so `double-check` is the two tokens `double check`) and phrases are matched on token boundaries. The rewrite was verified score-identical to the regex version on 94 real Agent prompts, and it added two anti-patterns — `think-instruction` and `show-reasoning-request`. `scripts/routines/startup-payload-audit.sh` lost its `@import` regex the same day in favour of a line-prefix parser. Both are off `scripts/utils/no-regex-debt.txt`; `action-capture.sh` and `stop-hook.sh` are the remaining hooks that regex-parse free text.

_Last synced: 2026-10-01_

