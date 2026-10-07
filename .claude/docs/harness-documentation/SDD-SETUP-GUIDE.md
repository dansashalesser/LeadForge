# SDD Setup Guide

> This file is managed by the SDD harness (`sdd-harness/docs/`).
> It is the single source of truth — do not edit copies in individual projects.
> _Last synced: 2026-10-01_

---

## Recent Changes (2026-10-01 — Harness Build 27)

One theme: the per-call cost of the skill listing. ~570 skills, each one listed in every prompt, cost **~13.3k tokens on every API call** whether a skill was used or not. Two moves took that to **~5.0k**: 445 narrow skills were consolidated into 28 broader ones (14 of them domain routers), and the install split into two tiers so only the routers plus a small pinned set stay listed. The single reference for where a skill goes and how to register one is new: `docs/skills/SKILL-HIERARCHY.md`.

### Skills (`skills/`) — two tiers + 14 domain routers
- **Two install tiers, one repo source.** Source for both tiers stays at `skills/<name>/`. The split happens at install: `scripts/setup/sync-skills.sh` routes each skill to **Listed** (`~/.claude/skills/<name>/`, in every prompt's skill listing) or **Library** (`~/.claude/skill-library/<name>/`, not listed, costing nothing until something reads it). Current split: 30 Listed, 559 Library.
- **The 14 masters (domain routers)** — `backend-dev · frontend-dev · languages · data-and-db · ai-ml-agents · devops-infra · observability-incident · security · code-quality · cloud-sdks · product-growth · integrations · docs-knowledge · harness-meta`. A master holds no technique of its own: its body is a table of its domain's sub-skills (`name | one-line | path`) plus an instruction to read the ones that fit, as many as the task needs. An agent sees the 14 master descriptions, opens the relevant master(s), then **reads the sub-skill's `SKILL.md` at its path with the Read tool**. A Library skill is never reached through the Skill tool — that tool resolves Listed skills only, so `Skill("<library-name>")` fails. Point callers at the path instead.
- **What stays Listed (pinned).** Only skills that something loads *by name* through the Skill tool: the hard-invoked `auditing-spec-choices`, `cheap-model-delegation`, `document-parsing`, `issue-triage-routing`, `secure-agent-design`, `skill-eval-gate`, `skill-extraction`, `tool-failure-memory`; the routine-invoked `session-quality`, `keep-rate`, `skill-curator`; and `lean-ctx` + `gitnexus` as always-on guidance. Everything else is Library. Keep this set small — each addition is paid on every API call.
- **445 skill directories were deleted and folded into 28 new ones** (942 files removed, 53 rewritten): the 14 masters above plus topic umbrellas `azure-sdk`, `m365-agents`, `dbos`, `fal`, `hig`, `wiki`, `conductor`, `c4-architecture`, `composio-automation`, `documentation`, `secure-coding`, `penetration-testing`, `seo`, `cro`. Any harness file that named a deleted skill is now a dangling pointer — the `action-capture.sh` renames below are two that were caught. Grep the tree before citing a skill name.

### Scripts
- `scripts/setup/sync-skills.sh` **(new)** — the single namer of the tier split, called by both `install.sh` and `update.sh`; never copy skills into `~/.claude` by hand. Membership is an exact whole-line `grep -qxF` against the manifest (no associative arrays — macOS ships bash 3.2), and routing a skill to the library **evicts any stale Listed copy**, so a skill that changes tier stops costing per-prompt context. **A missing manifest installs everything Listed and exits 0** — the pre-split behaviour; the split can never block an install or update. New `sync-skills.test.sh` (11 cases: master Listed, library routing, stale-Listed eviction, per-tier counts, no-manifest fallback).
- `scripts/setup/skill-library.txt` **(new)** — the manifest: 559 skill names, one per line, `#`/blank ignored. Manifest and skill tree are currently consistent (no entry names a missing directory).
- `scripts/routines/skill-write.sh` / `scripts/routines/skill-delete.sh` — both now read the manifest to pick the installed path, because writing to the wrong tier left a stray Listed copy *and* missed the copy Claude actually reads. `skill-delete.sh` also drops the skill's manifest line so a later sync cannot route a skill that no longer exists; its master-router row must still be removed by hand (a dead row is a dangling path, not a crash).
- `install.sh` — `install_globals()` delegates skill install to `sync-skills.sh` instead of looping `sync_dir` into `~/.claude/skills/`.
- `scripts/setup/gitnexus-reconcile.sh` — new `compact_block()` runs on every live reconcile and replaces the upstream GitNexus block body (~900 tokens, loaded on every API call) with the harness's compact rule set: every MUST/NEVER rule kept, the CLI fallback stated once instead of each rule written twice (MCP + CLI form), and the resources and skill-path tables dropped since the skill listing already carries them. The indexed repo name is carried over from the existing block — it is GitNexus's registry name, not always the directory name — falling back to the project basename. Idempotent (`cmp` before writing) and **skipped entirely when the block has no `<!-- gitnexus:end -->`**, since splicing an unterminated block would swallow the rest of the file. `SDD_GITNEXUS_FULL_BLOCK=1` keeps the upstream block untouched. `gitnexus-reconcile.test.sh` now 41 cases — new ones assert each MUST rule survives compaction, the repo name is carried, the old tables are gone, the head and tail of `CLAUDE.md` around the block are preserved, and an unterminated block is left alone with its tail intact.
- `scripts/utils/herder.py` + `scripts/utils/dashboard.py` — the Herder chat box gained `/skill` and `@path` completion (`list_invocables()`, `list_repo_paths()`, `complete()` in `herder.py`; keydown/accept handling in the dashboard's JS). Both candidate lists are read off disk in the target repo, never hardcoded, and are briefly cached so a burst of keystrokes is one disk walk. Details in the Herder row under Automated Hooks; `herder.test.sh` is 34 offline cases, 6 of them completion.
- `scripts/lib/pip-cooldown.sh` **(new, + `pip-cooldown.test.sh`)** — release-age cooldown for every harness-driven Python install. `pip_cooldown_install <py> <pip args…>` adds `--uploaded-prior-to "$SDD_PIP_MIN_AGE"` (ISO 8601 duration, default `P2D`) when that pip supports the flag; a pip too old installs **without** the cooldown and says so on stderr rather than failing the install outright. `uv_cooldown_flags` echoes the uv equivalent (`--exclude-newer <age>`), and emits nothing when `SDD_PIP_MIN_AGE=off` — the escape hatch for a same-day security fix. Rationale: harness specs are unpinned, so an install resolves to whatever was published today, and a malicious release is usually yanked within hours; the cooldown closes that window with no pins to bump. Adapted from pi's `.npmrc` `min-release-age=2`. `pip-cooldown.test.sh` is 9 offline cases driven by fake `python`/`uv` binaries that log their argv — no network (`bash scripts/lib/pip-cooldown.test.sh`).
- `scripts/lib/venv-tools.sh`, `scripts/lib/repo-venv.sh` — both now source `pip-cooldown.sh`, so their callers get the cooldown without each opting in: `setup/check-harness-deps.sh` (the self-healing `.venv-tools` install) and `setup/liteparse-setup.sh` via `venv-tools.sh`; `repo_pip_install`'s pip path **and** its `uv pip install --python <venv>` fallback via `repo-venv.sh` (so `raindrop-setup.sh` inherits it too). Not covered, deliberately: `headroom-setup.sh`'s `pip install --user` and `.venv-tools`' own pip self-upgrade.

### Hooks
- `hooks/claude/prompt-hook.sh` — **hot-memory is no longer injected on every prompt.** It is ~2k tokens and each injection stays in the transcript, so injecting every time re-sent one more copy on every later API call. It now fires on the 1st prompt of a session, then every `SDD_HOT_MEMORY_EVERY` prompts (default 10), plus the first prompt after a compaction (the summary may have dropped it — compaction count comes from counting `"compact_boundary"` in the transcript, and a change resets the cadence). The counter lives in `.claude/memory/.prompt-hook/<session_id>.json`; counters untouched for a week are pruned on a session's first prompt. Every failure path **injects** (unreadable event, unusable `session_id`, non-integer `SDD_HOT_MEMORY_EVERY`, unwritable counter) because a missed injection is invisible while an extra one merely costs 2k tokens — except a corrupt counter file, which resets the cadence rather than injecting forever. New `prompt-hook.test.sh` (13 cases: cadence, per-session isolation, post-compaction reset, custom `SDD_HOT_MEMORY_EVERY`, corrupt-counter reset).
- `hooks/claude/action-capture.sh` — `[seed-target:]` skill domains repointed at skills that still exist: `deployment-engineer` → `deployment-pipeline-design`, `nodejs-best-practices` → `nodejs-backend-patterns`. A seed target naming a deleted skill is a suggestion nothing can act on.

### Commands and agents
- `commands/kiro/adapt-to-repo.md`, `commands/kiro/daily-briefing.md` — both stopped calling `Skill(...)` and now **read their skill by path** (`~/.claude/skill-library/adapt-to-repo/SKILL.md`, `~/.claude/skill-library/synthesizing-daily-briefings/SKILL.md`), since both skills moved to the Library tier where the Skill tool cannot resolve them. Any command or hook that invokes a Library skill needs the same shape.
- `agents/kiro/skill-extract-agent.md` — extraction now targets the two-tier hierarchy: write source to `skills/<name>/SKILL.md`, **default every extracted skill to Library** (manifest line + one row in the owning master's table, master picked from the 14 domains), and leave it off the manifest only when a hook or command will `Skill("<name>")` it. The completion summary reports the installed path as `~/.claude/skill-library/<name>/SKILL.md` and names the master it was filed under.
- `agents/kiro/save-session-agent.md` — suggested-skill example updated from the deleted `debugging-strategies` to `systematic-debugging`.

### Kiro Settings — Rules (`kiro/settings/`)

- `kiro/settings/rules/tasks-generation.md` — new rule **"Refactor first, then build — two steps, never one"**: a feature task may not carry restructuring. When `design.md` picked *refactor* or *hybrid* for an area, `/kiro:spec-tasks` emits a behavior-preserving refactor task ahead of the feature task that depends on it, as its own commit. Full wording in the Kiro Settings section below.

### Project Constitution (`CLAUDE.md`)
- The repo's own GitNexus managed block is now the compacted form written by `gitnexus-reconcile.sh` (see Scripts above): one line naming the indexed repo and the CLI fallback, then seven rules. The `## Always Do` / `## Never Do` / `## Resources` / `## CLI` tables are gone; no rule was dropped. Downstream projects get the same shape on the next `update.sh`, and `SDD_GITNEXUS_FULL_BLOCK=1` opts out.

---

## Recent Changes (2026-10-01 — Harness Build 26)

One theme: every blocking hook in the harness was failing **open** on its own failures. A missing `python3`, a malformed event, or a crashed analyzer collapsed into an empty string and `exit 0` — so a broken interpreter silently disabled the block, with no error anywhere. Two hooks were fixed; a third turned out never to have blocked at all. The rule and its worked examples are now written into `skills/hook-design`, and every blocking hook has a malformed-event and a `python3`-off-PATH test case.

### Hooks
- `agent-behavior-guard.sh` — **enforce mode never blocked anything until now.** The script ended in a bare `exit 0` that discarded the heredoc'd Python's exit code, so `SDD_AGENT_GUARD_ENFORCE` logged `"mode": "enforce"` findings and then allowed the call. The Python's exit code is now the verdict (`RC=$?`, `exit 2` propagated). With any rule enforced the guard also fails closed on its own failures (malformed event, `python3` missing, crash → exit 2); monitor mode stays silent and still never blocks. New `agent-behavior-guard.test.sh` (13 cases: each rule blocking under enforce, chained egress scoped to its session, enforce scoped to the named rule, monitor never blocking, plus fail-closed vs. monitor on a malformed event and with `python3` off PATH). Runs from a throwaway cwd so tests never touch the real findings ledger. This hook is also now listed in Automated Hooks below — it was missing from the table
- `git-destructive-guard-hook.sh` — fails closed on its own failures. Both parse steps (event JSON, command analyzer) swallowed errors into `""` and fell through to `exit 0`, which turned a missing `python3` into a silent allow of `git push --force` — the harness's only hard block on destructive git. A `fail_closed()` helper now blocks (exit 2) with the reason on stderr, scoped by a **literal glob** on the raw event to commands that mention `git` or `gh ` so a broken dependency does not take down every Bash call. A non-string `tool_input.command` is a parse failure, not an empty command. `git-destructive-guard-hook.test.sh` 46 → 52 cases (six fail-closed: malformed event with and without `git`, non-string command field, and `python3` missing on a force push, a benign git command, and a non-git command)
- `ledger-append-only.sh` — same fix, same shape. `PROTECTED_LEDGERS` moved above the parse so `fail_closed()` can match against it: a failed parse blocks when the raw event names a protected ledger (literal substring) and allows everything else, so one broken dependency does not block every Write. `ledger-append-only.test.sh` 12 → 17 cases (five fail-closed, including `tool_input` arriving as a string instead of an object)

### Skills and agents
- `skills/hook-design` — new **Fail Closed, Narrow Only** section, carrying two rules for any hook that can exit 2 (neither applies to advisory always-exit-0 hooks). (1) *Fail closed*: a blocking hook whose own machinery fails must not allow the call; the fail-open shape to look for is `|| echo ""` / `except: print('')` followed by `[ -z "$X" ] && exit 0`. Scope the failure block with a literal glob on the raw event so a broken dependency does not block every tool call, and **propagate the verdict** — a heredoc'd `sys.exit(2)` under a trailing bare `exit 0` is discarded, which is exactly how `agent-behavior-guard.sh`'s enforce mode was inert. Every blocking hook's `*.test.sh` needs a malformed-event case and a `python3`-off-PATH case (a `PATH` holding only `cat`), or a fail-open regression passes the suite. (2) *Signals only narrow*: a hook verdict may block, warn, pause or revoke — it never grants. No hook returns `permissionDecision: "allow"` or writes allow-rules; a detection that can widen access can be steered by whoever controls its input. The Harness Hook Conventions example was corrected to let a parse failure exit non-zero instead of swallowing it, since an empty field and a failed parse mean different things and only the first may fall through to `exit 0`. Source: Perplexity, "How we engineer safer agents", 2026-09-29

---

## Recent Changes (2026-09-30 — Harness Build 25)

Two things landed together in this build. A skill-extraction pass over 18 sources (9 integrations, all edits to existing artifacts; see `docs/skills/skill-extraction/extraction-history.md`), and a round of repairs found by running the harness: Serena's MCP registration is now pinned and reconciled rather than hand-run from git, the Keep Rate and observations-archive recipes are pinned as scripts instead of re-derived per run, the Proof review flow was corrected against the current SDK, and several doc pointers were fixed after `docs/` was reorganized.

### Hooks
- `prompt-quality-check.sh` — two new anti-patterns, `think-instruction` and `show-reasoning-request` (from Anthropic's Opus 5.5 prompting guidance). Rewritten from regex to literal token matching and removed from the no-regex debt ledger; checked score-identical to the old version on 94 real Agent prompts. New `prompt-quality-check.test.sh` (11 cases)

### Scripts
- `scripts/utils/token-forensics.py` — per-hook injection tables (`hook_additional_context` and `hook_success`, never summed), ranked by amplified cost. Test cases 14 → 19
- `scripts/routines/startup-payload-audit.sh` — ceiling ratchet (only goes down, `--rebaseline` for intended growth); regex-free `@import` parsing, removed from the no-regex debt ledger. New `startup-payload-audit.test.sh` (17 cases)
- `scripts/utils/dashboard.py` — Startup Payload card shows the ceiling and its delta, plus an "above ceiling" badge
- `scripts/routines/daily-briefing-prompt.md` — runs the new stalled-work sweep
- `scripts/setup/serena-reconcile.sh` **(new, + `serena-reconcile.test.sh`)** — keeps Serena usable for the Risk Gate's Python row and the post-`.py`-edit diagnostics call. `--global` makes sure the user-scope `serena` MCP server launches a **pinned PyPI release** with `--open-web-dashboard False`, registering it if absent and migrating the old `git+https://github.com/oraios/serena` launch (which re-resolved the git source on every start and blew the MCP connect timeout on a cold cache). Writes go through `claude mcp`, never by editing `~/.claude.json`. With a project dir it makes sure `.serena/project.yml` lists every language the repo tracks (`.py` → `python`, `.ts/.tsx/.js/.jsx/.mjs/.cjs` → `typescript`), creating the file via the pinned `serena project create` when missing and otherwise appending missing languages without removing any. Run by `install.sh` and `update.sh`, always non-fatal; `--check` is a quiet exit-code probe. The pinned version lives only in this script (`SDD_SERENA_VERSION` overrides). See the Serena entry under Prerequisites
- `scripts/session/_keep_rate_calc.py` **(new)** — the Keep Rate recipe from `skills/keep-rate/SKILL.md` Step 2, pinned as a script instead of re-derived per run. Records its pinned choices in the docstring (working branch only + `--no-merges`; one blame map over every text path in HEAD keyed by commit so lines that moved to a renamed file still count; `-M -C` on blame and numstat; `--line-porcelain`; binariness tested on the **working-tree** file, not the diff blob; empty-tree base for root commits; denominator spans every text path a commit touched, including ones later deleted or renamed; per-commit blame credit clamped to lines added). Changing any of them makes the figure incomparable with previously recorded ones. `[--days N] [--repo PATH] [--json]`
- `scripts/session/_archive_obs.py` **(new)** — archives whole dates out of `observations.md` into `glacier/`. Selection is **by date, never by count**, and that is the entire point: the keep-most-recent-N rewrite reads, decides, and rewrites the file whole, so an entry appended between the read and the write is destroyed — which happened on 2026-09-01. Archiving a fixed set of *past* dates commutes with a concurrent append, since a new entry is dated today and today is refused. `--dates ... [--repo PATH] [--dry-run]`
- `scripts/utils/no-regex-debt.txt` — two entries paid down (`prompt-quality-check.sh`, `startup-payload-audit.sh`), leaving `stop-hook.sh` and `action-capture.sh` as the remaining free-text parsers. Paydowns are recorded in the file's header with the date and what replaced the regex
- `scripts/session/trust_score.py`, `commands/kiro/daily-maintenance.md` — doc pointer corrected to `docs/harness-documentation/SDD-USAGE.md` (the file moved; the old `docs/SDD-USAGE.md` path no longer resolves)

### Commands
- `commands/kiro/gitnexus-setup.md`, `skills/gitnexus` — documentation pointer corrected to `docs/integrations/gitnexus/README.md` after the README relocation

### Settings & constitution templates
- `templates/CLAUDE.md.template` — the Serena bullet no longer prints a hand-run `claude mcp add … git+https://…` command. It now states that the server is registered user-scope and **pinned to a PyPI release** by `install.sh`/`update.sh` via `scripts/setup/serena-reconcile.sh`, and that hand-registering from git is what to avoid — that launch times out on a cold cache

### Skills and agents
- `hook-design` — "Replay Before Ship" section plus `resources/hook-replay.py` (+ test): replays recorded tool calls through the old and new version of a PreToolUse hook and reports the verdict flips; counts only by default
- `skill-eval-gate` — an infrastructure failure is a missing run, not a FAIL; judge validation adds a score-twice consistency check
- `auditing-token-spend` — per-hook signal, plus diagnosis rows for a dominant hook, cache-breakers (with the correct 1h subscription / 5m API cache lifetime) and large output on small changes
- `context-optimization` — notes the 5-minute TTL is the API default, not the subscription one
- `model-tiers` — removed the false "this harness runs `high`" effort claim; Opus 5.5 defaults to `medium`, raise effort only when it saves a retry
- `synthesizing-daily-briefings` — Phase 2b stalled-work sweep (stale PRs, drafts still carrying the auto-created Evidence placeholder)
- `proof-collaborative-review` — the setup and publish steps were wrong against the current Proof SDK and produced an unusable review page. Four corrections: build the editor bundle (`npm run build`) or the page renders "Editor not built"; symlink `dist/assets/*` into `public/assets/` because the server serves static files only from `public/` while `dist/index.html` asks for `./assets/editor.js`; start the server with `COLLAB_EMBEDDED_WS=1` or the session API tells the browser to dial `port+1` and the document never loads; and `POST /documents` takes `markdown`, not `content` (`MISSING_MARKDOWN` otherwise), with the share URL at `/d/<slug>`, not `/doc/<slug>`. Also: Proof re-serializes markdown on the way out (`-` bullets become `*`, table separators get padded, characters get backslash-escaped), so a raw `diff` against the original is mostly noise — diff the normalized forms, and when nothing but formatting changed, keep the original file
- `keep-rate` — anti-patterns from live runs: an empty 30-day cohort makes the rate **undefined, not 0%**; write generated scripts to `sdd-harness/scripts/` first, since `~/.claude/` is bulk-reinstalled and wiped; check the harness source before rebuilding a "missing" calculator (that is what `_keep_rate_calc.py` now pins); and calculator methodology changes break comparability the same way blame-flag changes do, so record the version alongside the value
- `session-quality` — the auth-outage discriminator is now a **tail-distance** rule rather than an absolute line count; completion markers stamped at routine start hide outages, because `claude --print` exits 0 on OAuth and 502 gateway failures, so gate on the artifact instead; a scheduler that never fired leaves the same zero artifacts as a genuinely idle day (check cadence before marking idle, and zero transcripts + observations + metrics across *both* repos on one day means never-fired, not idle); and `/private/tmp/claude-*/<slug>/` is subagent scratch, not a transcript channel — transcripts live in `~/.claude/projects/<slug>/`
- `skill-curator` — approval step now requires repo-qualified citations before applying an edit, the write-side fix for bare `(source: DATE [tag])` lines that only resolve in the repo that wrote them
- `verification-before-completion`, `error-handling-patterns`, `systematic-debugging` — new anti-patterns: memory entries are not evidence that an artifact persisted (check it is on disk and not gitignored); citations must carry the repo name (`(source: repo-name date [tag])`); and substring-matching injected content in serialized output (hot-memory, interpolated variables) false-positives — validate real status fields instead
- `agents/kiro/autoresearch-agent.md` — REPAIR outcome: a crash caused by the change's own bug is fixed and re-run under the same hypothesis, and ERRORED runs no longer count toward the pivot rule

---

## Recent Changes (2026-09-24 — Harness Build 24)

### Project Constitution (`CLAUDE.md`)
- **The harness repo's own `CLAUDE.md` is now committed** — it was already tracked despite the `.gitignore` entry, so that state is now intentional: contributors get the project conventions on clone. `CLAUDE.md` is dropped from this repo's `.gitignore` and `CLAUDE.local.md` is ignored in its place. **Installed projects are unchanged** — `SDD_GITIGNORE_ENTRIES` in `scripts/lib/project-gitignore.sh` still lists `CLAUDE.md`, so every downstream project keeps its own local. See Step 1 for the full gitignore consequence, including the `^CLAUDE\.md$` post-commit trigger that can now actually fire here.
- **Personal preferences moved to `CLAUDE.local.md`.** The `## Address` section (the "always call the user by name" rule and its passive `address-check-hook.sh` Stop-hook signal) left `CLAUDE.md` for the gitignored `CLAUDE.local.md`. Rule of thumb now that `CLAUDE.md` is shared: anything that only applies to one operator or one machine goes in `CLAUDE.local.md`; project conventions stay in `CLAUDE.md`.
- **`## Blast Radius` collapsed to a pointer.** The three-step ordered list was a second copy of the Risk Gate table in `.claude/rules/lean-ctx.md`; `CLAUDE.md` now names that table as the one check and keeps only the precedence claim — that it outranks the auto-generated GitNexus block below it, which states its own rule without knowing about Serena or lean-ctx. Nothing was dropped from the check itself; see Step 5 and the `rules/lean-ctx.md` row in Context Engineering Rules.
- **Serena section trimmed to the per-edit rules.** What stays in `CLAUDE.md` is the mandatory `mcp__serena__get_diagnostics_for_file(path)` call after any `.py` edit, plus "skip `initial_instructions` — CLAUDE.md is the manual here." The install command and the `--open-web-dashboard False` rationale now live in exactly one place: the **Serena** entry under Prerequisites in this guide.
- **Quality Gates pytest line trimmed** to "No pytest suite; verify shell via `*.test.sh` + throwaway-tree runs" — a statement of fact about this repo, replacing a conditional `pytest -x --ignore=tests/integration` instruction that never applied here. Projects with a real suite should keep the `pytest` form from the Step 5 template.
- **Stale index note removed.** The `## Blast Radius` block used to assert that the GitNexus FTS index was stale right now (`version 42 vs 40`) and that step 2 was therefore unavailable — a point-in-time fact frozen into a standing rule. The index was reindexed 2026-09-24 (49128 symbols, 53926 relationships, 314 execution flows; schema 4, gitnexus 1.6.12) and the auto-generated `<!-- gitnexus:start -->` block was regenerated with it: `analyze --index-only` for refresh, `bunx`/`pnpm dlx` bootstrap alternatives, CLI fallbacks alongside every MCP call, the `risk: UNKNOWN` rule (an empty caller set is not evidence of no callers — confirm by text search, never read it as an all-clear), the `partial`/`truncated` rule (a zero means unseen, not unaffected), and flattened `.claude/skills/gitnexus-*/SKILL.md` paths.

### Prerequisites
- The **Serena** entry is now the single source for Serena setup — `CLAUDE.md` points here instead of restating the install command and dashboard flags.

---

## Recent Changes (2026-09-23 — Harness Build 23)

### Scripts
- `scripts/utils/hook-config-audit.py` **(new)** — stdlib-only, no `re`. Three checks against a repo's own `.claude/hooks/*.sh` and `settings.json`/`settings.local.json`/`.mcp.json`: secrets (shells out to `scan-pii.sh`'s OPF engine, per-file to avoid timeout), network-exfil (`curl`/`wget` calls in hook scripts to a host outside a small hardcoded allowlist), and over-broad permission grants (a `permissions.allow`/`deny` entry with no scoping argument or an argument that is just `*`). `<repo_dir> [--json]`; exits 1 on any finding, 0 clean
- `scripts/routines/hook-config-audit-runner.sh` **(new)** — deterministic weekly wrapper around the script above (`HOOK_CONFIG_AUDIT_GAP_DAYS`, default 7). Writes `.claude/reports/security/hook-config-audit.json`. Wired into `scripts/orchestration/daily-orchestrator.sh` `run_one()`. Opt out with `SDD_SKIP_HOOK_CONFIG_AUDIT=1`
- `scripts/routines/skill-write.sh` **(new)** — single source of truth for writing any file inside a skill's directory (`SKILL.md`, `resources/examples/*.md`, `eval-verdict.json`, …). Writes both `skills/<name>/` (harness source) and the installed copy together and keeps a timestamped pre-write backup under `.claude/memory/skill-repair-backups/<name>/`. The installed path depends on the skill's tier — `~/.claude/skill-library/<name>/` when the skill is listed in `scripts/setup/skill-library.txt`, `~/.claude/skills/<name>/` otherwise; writing to the wrong tier leaves a stray listed copy and misses the copy Claude actually reads. Exists because `update.sh`'s `sync_dir` does `rm -rf` + `cp -r` from harness source over the installed copy every 4h regardless of session activity — a write landed on the installed copy alone survives only until the next tick, then is silently reverted. This was confirmed as the root cause of a 2026-09-18 regression where the dashboard's Apply-Approved flow fixed 19 skills' frontmatter on the installed copy only, and the fix was lost on the next sync. `skill-augment-agent`, `skill-curator`'s Phase 5, and the harness-health-runner's skill-repair step now all route through this script instead of Write/Edit
- `scripts/routines/skill-delete.sh` **(new)** — pairs with `skill-write.sh`: removes both `skills/<name>/` and the installed copy (a delete of the installed copy alone is re-created by the next sync tick), backing up both to `.claude/memory/skill-repair-backups/<name>/` first. It also drops the skill's line from `scripts/setup/skill-library.txt` so a later sync does not route a skill that no longer exists; a master-router row pointing at it must still be removed by hand
- `scripts/skill-quality-scan.py` **(new)** — deterministic (no LLM) scorer for the skill-curator's Phase 1 Quality Audit, run via `python3 $SDD_HARNESS/scripts/skill-quality-scan.py --json` against every `~/.claude/skills/*/SKILL.md`. Scores the four SkillOS dimensions (task relevance, operational validity, content quality, compression ratio; 0–3 each) from frontmatter + body-length heuristics, and reports `yaml_defect_count` for skills whose `description:` is the literal string `">"` instead of a bare `>` fold indicator (silently empties the description at parse time — 19 skills had this defect, fixed in this same pass). Replaces a per-file LLM read of ~1000 SKILL.md files, which was exhausting the skill-curator session's turn/time budget before Phase 4 (writing the report) ever ran — the cause of the report body freezing at 2026-08-06 for six weeks while later runs kept exiting 0 with nothing written. Duplicate-pair detection is NOT covered (semantic overlap needs judgment) and stays a targeted manual read of the script's low-quality candidates
- `scripts/utils/skill-dependency-scan.sh` — rewritten from an O(skills × files) per-skill `grep` loop (confirmed still running past 120s wall-clock on ~1000 skills) to a single-pass `awk` scan: every candidate file is read once and each line tokenized once against the full skill-name set. Same output contract (file:line only, capped per skill, alphabetical)
- `scripts/routines/skill-curator-runner.sh` — fixed a BSD-`date` parsing bug in the cadence guard: `date -j -f "%Y-%m-%dT%H:%M:%S%z"` requires `+0300`, not ISO8601's `+03:00`, so on macOS `LAST_EPOCH` silently fell to 0 and the weekly cadence gate never fired — confirmed every run had re-swept since ~2026-08-06 instead of pacing weekly. Fixed by stripping the colon from the offset before the macOS parse attempt
- `scripts/setup/headroom-setup.sh` — switched from `uv tool install headroom-ai --with-requirements headroom-extras.txt` to `uv tool install "headroom-ai[proxy]"` (same fallback chain: `uv tool install` → `pipx` → `pip install --user`). The hand-maintained `headroom-extras.txt` requirements list (deleted) had drifted behind the proxy's actual dependency tree (missing `mcp`, then `magika`), each gap producing a silent launchd crash-loop — exit 1, zero stdout/stderr. `[proxy]` is declared by the package itself, so it tracks whatever the proxy needs release over release
- `hooks/claude/verification-retry-hook.sh` — rewrote its embedded Python from `re`-based regexes to plain string/tokenizer logic (word-splitting + literal-sequence matching), fixing a pre-existing regex-ban violation (the repo bans `re` outside `.py` files reachable by ruff; this hook's Python lives in a shell heredoc, which `ruff`/TID251 cannot see) that had been committed before the no-regex pre-commit guard existed to catch it. Behavior is unchanged: same test-command detection, same success-claim phrases, now case-sensitive only where the original regex was
- `hooks/claude/session-start-hook.sh` — gained the headroom routing self-heal call (`scripts/utils/headroom-unwire-if-dead.py`) described in the Headroom section below
- `agents/kiro/skill-augment-agent.md` — Step 3.5 (Dreaming) and Step 4 (Apply and Log) now route every skill write through `skill-write.sh` instead of Write/Edit, matching the fix above
- `templates/settings.harness.json.template` — `cheap-model-delegation-hook.sh` is now also registered on `PreToolUse Bash` here (previously per-project template only); corrects the 2026-09-17 note below and the Automated Hooks table, which both said it was harness-template-excluded

---

## Recent Changes (2026-09-17 — Harness Build 22)

### Hooks
- `cheap-model-delegation-hook.sh` **(new)** — PreToolUse(Bash), advisory only (never blocks). Fires when a single `cat`/`head`/`tail` Bash command reads 6+ path-looking args in one call; suggests delegating bulk mechanical extraction (one fact/summary per file, no deep reasoning) to a haiku-tier subagent via the `cheap-model-delegation` skill instead of reading it all at the primary model's rate. Deliberately soft — a `PreToolUse(Read)` hard gate already exists for single large files (`lean-ctx-nudge-hook.sh`); bulk multi-file Bash reads can't be reliably judged as "extraction work" from the command line alone. Registered on `PreToolUse Bash` in `templates/settings.json.template`; as of 2026-09-23 also wired into the harness's own `settings.harness.json.template` (was excluded there at introduction — see Build 23 above)
- `pr-risk-tier-hook.sh` **(new)** — PostToolUse(Bash), best-effort (never fails the caller). Fires after a plain (non-force) `git push` when the current branch already has an open PR (same trigger shape as `pr-auto-create-hook.sh`); reads `.claude/steering/risk-zones.md` and labels the PR `risk:green`/`risk:yellow`/`risk:red` via `gh pr edit --add-label` so reviewers see blast radius before opening the diff. Gated behind `git config --worktree hooks.autopilot.enabled true` (requires `git config extensions.worktreeConfig true` once in the main worktree) — the same flag `hooks/git/post-commit` and `scripts/pr/detect_base_and_create.sh` now share, so ad hoc review/PR worktrees stay disabled by default and can't push/label things outside the session. Registered on `PostToolUse Bash` in both settings templates
- `risk-zone-edit-gate-hook.sh` **(new)** — PreToolUse(Write|Edit), soft gate (never a hard deny — `PreToolUse` can only allow/deny/ask, and a wrong/stale zone call must never block real work, same tradeoff as `protected-path-hook.sh`). Reads `.claude/steering/risk-zones.md` and warns before an edit lands in a red/yellow zone file (silent for green/unlisted files), suggesting the user check for characterization tests and run `gitnexus impact` on red-zone files. Registered in both settings templates
- `sloppiness-warn-hook.sh` **(new)** — PostToolUse(Write|Edit|MultiEdit), soft, never blocks. Scores the just-written file against `scripts/quality/sloppiness-score.sh`'s Verbosity/Erosion metrics (non-LLM-judge sloppiness measurement) and warns only when the file crosses the published AI-agent baseline (verbosity ≥0.33 / erosion ≥0.68) as "high-slop" — a single elevated file isn't worth interrupting on. Also records the metric via `scripts/session/record_metric.py` regardless of verdict. Registered in both settings templates
- `verification-retry-hook.sh` **(new)** — Stop hook. Catches claimed-but-unverified success: fires only when the final assistant turn uses explicit success language ("tests pass", "all passing", "verified working", …) and either no test-runner command ran this turn or the last one that did exited non-zero. Defaults to advisory (`VERIFICATION_RETRY_WARN_ONLY=1`) — logs but never blocks, because this repo already tried an always-blocking Stop hook once (`address-check-hook.sh` used to exit 2 on every turn missing "Husband") and demoted it to passive logging since blocking cost a full extra turn every time it fired. Setting `VERIFICATION_RETRY_WARN_ONLY=0` forces a retry via the Stop-hook JSON block contract, capped at `VERIFICATION_RETRY_MAX` (default 2) rounds per user request (counter resets on each new real user message; state in `.claude/memory/.verification-retry-state`). Registered in both settings templates
- `lean-ctx-nudge-hook.sh` — became **dual-mode**: the existing `PostToolUse(Write|Edit)` soft suggestion after editing a large file is unchanged, and it is now also registered as a `PreToolUse(Read)` **hard gate** that denies a native Read on a file ≥16 KB (~4,000 tokens) before it spends context, naming the `ctx_read` mode to use instead. Escape hatch: `LEAN_CTX_NUDGE_WARN_ONLY=1` downgrades the block to a warning (matches the existing `LEAN_CTX_ALLOWLIST_WARN_ONLY` convention). New `PreToolUse Read` entry added to both settings templates
- `js-quality-gate-hook.sh` — added `tailwind_design_token_check()`, running independently of oxlint/eslint and gated on a `tailwind.config.{js,ts,mjs,cjs}` file existing in the repo root (silent no-op on non-Tailwind projects). Regex-only checks covering the shadcn-ui/lint baseline: `no-raw-colors` (raw hex/rgb in a `className`), `no-arbitrary-values` (Tailwind arbitrary-value syntax like `w-[13px]`), `require-static-classes` (a `className` built from a template literal with interpolation, which breaks Tailwind's JIT static-class scanning). Advisory only; no settings-template change needed since the hook is already registered
- `prompt-quality-check.sh` — added `detect_anti_patterns()`, separate from the existing 1-5 dimension scores (context_provision, request_specificity, scope_management, information_timing, correction_quality). Flags named prompt anti-patterns as presence/absence findings rather than a spectrum score: `stale-few-shot` (2+ example blocks — verify they still match the codebase), `mandatory-scratchpad` (forced "think step by step in a scratchpad" ritual), `maximally-thorough-phrasing` (vague intensifiers like "leave no stone unturned" instead of naming the actual completeness criterion), `verification-ritual` (3+ repeated verify/check phrasings instead of stating the one concrete check that matters). Printed under a "🚩 Anti-patterns:" line and logged into the `anti_patterns` field of the `pq-log.jsonl` entry
- `hooks/git/post-commit` — `REPO_ROOT` is now derived via `git rev-parse --show-toplevel` instead of the hook script's own `BASH_SOURCE` location. Bug fix: a hook file lives under the main repo's `.git/hooks/`, but git invokes it with cwd set to whichever worktree triggered the commit — deriving `REPO_ROOT` from `BASH_SOURCE` always resolved to the main repo, so hook 3 (doc-sync) would `cd` there and commit/push docs against whatever branch happened to be checked out in the main repo, not the branch that actually just got the commit. Also gated behind the same `hooks.autopilot.enabled` worktree-scoped flag as `pr-risk-tier-hook.sh`, so ad hoc review/PR worktrees created via `git worktree add` can never auto-commit or push docs to the wrong place. See Step 8 for the full mechanism

### Scripts
- `scripts/quality/sloppiness-score.sh` **(new)** — computes the Verbosity/Erosion metrics `sloppiness-warn-hook.sh` scores against
- `scripts/quality/prompt-drift-check.sh` **(new)** — related prompt-quality tooling
- `scripts/routines/risk-zone-reseed-runner.sh` **(new)** — seeds `.claude/steering/risk-zones.md` (churn + test-file presence + `gitnexus impact`) that `pr-risk-tier-hook.sh` and `risk-zone-edit-gate-hook.sh` both read
- `scripts/routines/daily-briefing-runner.sh` **(new)** — daily-briefing routine runner, wired into `scripts/orchestration/daily-orchestrator.sh`
- `scripts/pr/detect_base_and_create.sh` — gained the same `hooks.autopilot.enabled` worktree gate as `hooks/git/post-commit` and `pr-risk-tier-hook.sh`
- `scripts/session/record_metric.py`, `scripts/utils/dashboard.py`, `scripts/pr/log_review.sh`, `scripts/pr/validate_review_json.py` — modified in support of the sloppiness metric and PR tooling above

### Settings Templates
- `templates/CLAUDE.md.template` — new **"## AI-Legible Code"** section: Blast radius (prefer changes touching ≤1 folder/module), Rule of Three (no shared extraction until 3 real call sites exist), Vertical slices (feature = one folder, no `shared/`/`utils/`/`common/`), Fail fast (validate at every public boundary, named exceptions, no bare `except`, no silent fallbacks), Context rot (AI coherence degrades past ~300k tokens — keep functions and PRs small), Reviewer model mismatch (use a separate session/model to review AI-generated code), "3rd patch, same function" (regenerate from spec instead of layering a third patch — kiro.dev "Frontier Engineering"), and "Boundary tests outlive the code they check" (write e2e/property/load tests before regenerating the code under them, not after — same source)
- `templates/settings.json.template` and `templates/settings.harness.json.template` — wired the 5 new hooks above plus the `lean-ctx-nudge-hook.sh` dual-mode change; see each hook's entry above for the exact event/matcher and which template(s)

---

## Recent Changes (2026-09-03 — Harness Build 21)

### Commands
- `/kiro:daily-maintenance` — **Step 1 now spawns Judge 3×** concurrently; Step 5 surfaces unresolved memory-gaps; new Step 6b mines behavior specs

### Agents
- `session-judge` — **Sample mode** documented: when caller suppresses append, runs 3 times independently and median is reconciled by caller
- `guardrails-agent` — Audits **four independent dimensions**: complexity, type-evidence (JS/TS), assertion strength (all ecosystems), structure (duplication, dead code, import direction)

### Hooks
- `skill-validate-hook.sh` — Validates frontmatter AND **provenance** (warns on remote-install verbs, URLs without pinned commits; new PreToolUse on Write SKILL.md)
- `subagent-context-hook.sh` — Injects harness conventions directly into spawned subagents (SubagentStart, no matcher)
- `test-integrity-guard.sh` — **No regex since 2026-09-03** (literal-token matching, not pattern matching). Soft gate on gradient-descent-to-green test weakening
- `ledger-append-only.sh` **(new)** — Hard-blocks (exit 2) Write/Edit/MultiEdit against the harness's own self-scored measurement ledgers (`trust-score.jsonl`, `metrics.jsonl`, `caveman-savings.jsonl`, `learnings.jsonl`, `observations.md`); real producers keep appending via Bash `>>`, unaffected. Escape hatch: `SDD_LEDGER_ROTATE=1` for housekeeping's pruning passes
- `claudemd-edit-notice.sh` **(new, 2026-09-06)** — Warns (exit 2, soft) that a just-written `CLAUDE.md`/`AGENTS.md`/`CLAUDE.local.md` is **not loaded in the running session**; instruction files are read once at session start. PostToolUse on `Write|Edit|MultiEdit`. Opt out: `SDD_SKIP_CLAUDEMD_NOTICE=1`
- `skill-validate-hook.sh` — also warns when a `SKILL.md` write no longer matches the `skill_md_sha256` in its sibling `eval-verdict.json`, or when an existing skill has no verdict file at all (2026-09-06)

### Skills
- `skill-eval-gate` — new **Phase 1c (compression-regression scenario)**: runs one soft-instruction scenario twice (verbatim vs. compressed `SKILL.md`) against the same check; a compressed-only failure is its own FAIL verdict, distinct from a capability gap
- `skill-eval-gate` — new **Phase 6 (record the verdict)**: on PASS, writes `eval-verdict.json` beside the `SKILL.md` carrying the sha256 of the exact instructions that were measured (2026-09-06)
- `better-call` — new **Step 2b (order-reversed second pass)**: scores the pair again with the incumbent presented first; a verdict that flips between orders is `INCONCLUSIVE`, not a win (2026-09-06)

### Scripts
- `scripts/skill-eval-staleness.py` **(new, 2026-09-09)** — Scans every `eval-verdict.json` against the model now running and reports the verdicts that no longer describe what they claim to. `skill-validate-hook.sh` catches the *other* invalidation (an edited `SKILL.md`) because a write is something a hook can fire on; a model change invalidates every verdict at once with no write anywhere, so it needs a scan. Three findings, worst first: `hash-mismatch`, `stale-model`, `unknown-model` — one per skill, since a changed `SKILL.md` has no valid verdict at all. **`--current-model` is required and has no default** (`SDD_CURRENT_MODEL` accepted); a wrong guess would mark every stale verdict as current, which is the failure the script exists to prevent. Skills with no verdict file are counted, never flagged. `--strict` exits 1 on any finding; `--json` for machine consumption; `--dir` for a non-default skill root. Run by `/kiro:daily-maintenance` Step 7, which **reports only** — re-measuring one skill costs 12 agent spawns. Tests: `scripts/skill-eval-staleness.test.sh` (27 offline cases)

### Context Rules
- `rules/lean-ctx.md` — **Native→`ctx_*` mapping deleted** (MCP server states it); kept: native Grep/Glob policy denial, `ctx_read` mode table, advertised tools list (17 in standard profile), three-row risk gate (one check per edit type)

### Settings Templates  
- `env` block sets `BASH_MAX_OUTPUT_LENGTH=15000`
- **`cleanupPeriodDays: 365`** (2026-09-06) — Claude Code's default is 30 days, after which session transcripts under `~/.claude/projects/<slug>/` are deleted. The harness's measurement stack reads those raw transcripts (`token-forensics.py`, `rtk-net-effect.py`, `dashboard.py`, `herder.py`, `session-judge`, `detect_reexplanation.py`, `/insights`), so the default silently truncated its own evidence window with no error. Set in `templates/settings.json.template` and in `~/.claude/settings.json`. `0` does **not** mean unlimited — it wipes immediately. Auto-memory files are exempt from the sweep; transcripts are not
- **Removed inert `Write(path)` rules** — `Edit(path)` covers Write/Edit/MultiEdit; script `fix-inert-write-rules.py` repairs projects
- **Templates reconciled** — `reconcile-settings-templates.py --check` runs in `/kiro:harness-validate`; drift there means a hook fires everywhere except where tested

---

A complete, self-contained guide to setting up the Spec-Driven Development (SDD)
harness used in this project. Follow these steps to replicate the setup in any
new Python/uv project.

---

## Prerequisites

- **Claude Code CLI** — installed and authenticated (`claude --version`)
- **Node.js** — for npx (`node --version`)
- **uv** — Python package manager (`uv --version`)
  - Every harness-driven Python install carries a **release-age cooldown** (added 2026-10-01): nothing published within `SDD_PIP_MIN_AGE` (ISO 8601 duration, default `P2D`) is considered, for both pip (`--uploaded-prior-to`) and uv (`--exclude-newer`). Harness specs are unpinned, so an install otherwise resolves to whatever was published today. Set `SDD_PIP_MIN_AGE=off` when you need a same-day security fix; see `scripts/lib/pip-cooldown.sh` in the Scripts notes.
- **git** — initialized repo (`git status`)
- **impeccable** _(optional, for auto frontend scan)_ — `npm install -g impeccable@3.6.0` (`install.sh` pins `IMPECCABLE_VERSION`; npm 10 has no release-age cooldown, so the pin is the guard)
- **Serena** _(optional, for Python code intelligence)_ — registered automatically by `install.sh` and kept pinned by every `update.sh` run via `scripts/setup/serena-reconcile.sh --global`, which issues `claude mcp add serena --scope user -- uvx --from serena-agent==<pinned> serena start-mcp-server --context claude-code --open-web-dashboard False`. The pinned version lives in that script only. **Never launch Serena from `git+https://github.com/oraios/serena`** (changed 2026-09-30): uvx re-resolves a git source on every start, and a cold-cache rebuild blew Claude Code's 30 s MCP connect timeout, so Serena silently failed to connect. The same script also runs per project and makes sure `.serena/project.yml` lists every language the repo tracks: Serena's auto-detection had registered caresync-vercel as TypeScript-only, which breaks every Python lookup, and `.serena/` is gitignored, so the fix can't be committed. This entry is the canonical reference for Serena setup: `CLAUDE.md` points here rather than restating it (changed 2026-09-24). The `--open-web-dashboard False` flag is load-bearing at user scope: every agent spawn starts its own Serena process, and without it each one opens a browser tab. The dashboard still runs at `http://localhost:24282/dashboard/` (port climbs per extra instance). The machine-local equivalent is `web_dashboard_open_on_launch: false` in `~/.serena/serena_config.yml`, which does not travel between machines. The `claude-code` context excludes `initial_instructions`, so Serena loads silently with zero session overhead — there is no `ide-assistant` context any more.
- **GitNexus** _(optional, for code intelligence)_ — installed by `install.sh` and kept pinned by every `update.sh` run via `scripts/setup/gitnexus-reconcile.sh --global`, which runs `npm install -g gitnexus@<pinned>` (pinned version lives in that script only; `SDD_GITNEXUS_VERSION` overrides). On macOS it first runs `brew install openssl@3` when missing (added 2026-10-01): 1.6.12's LadybugDB binary dlopens `/opt/homebrew/opt/openssl@3/lib/libssl.3.dylib`, and without it every `gitnexus` command fails at startup. The pin matters beyond the crash: the `<!-- gitnexus:start -->` block in `CLAUDE.md` is version-dependent output, and an older CLI (1.6.3) rewrites it with a template that drops the `risk: UNKNOWN` and partial/truncated rules — never commit a block produced by a CLI older than the pin.

**Windows users:** The recommended setup is **WSL2** — run everything from a WSL2 terminal and Claude Code will use the Linux environment. If running Claude Code natively on Windows, see the notes in Steps 6 and 8 regarding bash hooks and path differences. See [FIRST-TIME-SETUP.md](FIRST-TIME-SETUP.md) for the platform support summary.

---

## Step 1: Gitignore Configuration

Add to `.gitignore`:

```gitignore
# ── Installed harness output — NEVER commit ──────────────────────────────────
# .claude/ is regenerable output, not source. install.sh / update.sh rebuild it
# on every machine from the harness source tree. Per-machine settings.json and
# memory content also live here and stay local.
.claude/
specs/
scripts/setup-git-hooks.sh
scripts/remap-ccsdd-paths.sh

# SDD harness — local-only, never committed
CLAUDE.md
# Generated per machine: AGENTS.md by `lean-ctx setup`, ERRORS.md by the
# 2+-attempts logging rule. Same class as CLAUDE.md — regenerated, not source.
AGENTS.md
ERRORS.md

# Serena symbol index — regenerable per machine via `serena project index`
.serena/
```

One `.claude/` line replaces the previous per-subdirectory list (`.claude/hooks/`, `.claude/commands/`, `.claude/agents/`, `.claude/kiro/`, `.claude/steering/`, `.claude/settings.json`, `.claude/memory/**` + its `!` re-includes). Already-tracked `.claude/memory/**/.gitkeep` files persist as clone scaffolding — the broad ignore does not untrack them.

`CLAUDE.md` is ignored in every **installed project**, matching the entries the harness writes into their `.gitignore` (`.claude/`, `specs/`, `CLAUDE.md`, `AGENTS.md`, `ERRORS.md`). The **harness repo itself is the exception** (changed 2026-09-24): its own `CLAUDE.md` is committed as a shared reference so contributors get the project conventions, and `CLAUDE.local.md` is ignored in its place for personal preferences. The harness repo's `.gitignore` therefore carries `CLAUDE.local.md` under the `# SDD harness — local-only, never committed` header instead of `CLAUDE.md`; downstream projects are unaffected, since `SDD_GITIGNORE_ENTRIES` still lists `CLAUDE.md`. One consequence: the git post-commit hook's `^CLAUDE\.md$` harness-updater trigger now actually fires in this repo — previously it could never match, because the file was ignored.

That list lives in one place, `scripts/lib/project-gitignore.sh` (`SDD_GITIGNORE_ENTRIES`), sourced by both `install.sh` and `update.sh`. `install.sh` runs once per project, so an entry added later would never reach an already-installed project; `update.sh` calls the same `ensure_gitignore` on every sync (git repos only), so new entries backfill automatically. Appending is idempotent — each entry is added only if an exact matching line is absent, and the `# SDD harness — local-only, never committed` header is written at most once.

`AGENTS.md` and `ERRORS.md` joined the list because both are generated per machine rather than authored: `AGENTS.md` is written by `lean-ctx setup`, `ERRORS.md` by the 2+-attempts logging rule. They are the same class of file as `CLAUDE.md` — regenerated output, not source.

This library has its own test, `scripts/lib/project-gitignore.test.sh` — run it with `bash scripts/lib/project-gitignore.test.sh` (no framework, exits non-zero on failure). It builds throwaway project trees under `mktemp -d` and asserts three cases: a project with an existing `.gitignore` keeps its content and gains each entry exactly once; a project with no `.gitignore` gets one created whose first line is the header; a partial (legacy) `.gitignore` already listing `.claude/`, `specs/`, `CLAUDE.md` gains only the missing `AGENTS.md` and `ERRORS.md`, with no duplicates. The re-run case is the one that matters operationally: `update.sh` runs under `set -e` and calls `ensure_gitignore` on every sync, so `ensure_gitignore` always returns 0 — a non-zero "nothing to add" would abort every update of an already-configured project.

`.serena/` is ignored for the same reason as `.claude/`: it is Serena's symbol index, regenerable per machine with `serena project index`, so it is machine-local state rather than source.

The harness repo's own `.gitignore` additionally carries `reports/` (under a `# Generated reports — local only, never push` header) and the scheduler state files `.last-harness-sync` / `.last-drift-review`. `reports/` is where generated routine output lands — the drift-review sweep writes `reports/drift-review-report.md` there rather than into `docs/`, so a machine-local generated report cannot be committed or picked up by doc-sync as if it were documentation.

Commit: `git add .gitignore && git commit -m "chore: add SDD harness gitignore entries"`

---

## Step 2: Add ruff

```bash
uv add --dev ruff
git add pyproject.toml uv.lock && git commit -m "chore: add ruff linter to dev dependencies"
```

> This is the **only committed change** in the entire SDD setup.

---

## Step 3: Install cc-sdd

```bash
npx cc-sdd@latest --claude-agent --lang en
```

Generates 12 slash commands in `.claude/commands/kiro/`, 9 subagents in `.claude/agents/kiro/`,
and `.kiro/settings/` with templates and rules.

---

## Step 4: Remap Paths

The remap script moves cc-sdd's default `.kiro/` paths to our preferred locations:
- `.kiro/specs/` → `specs/` (at repo root)
- `.kiro/steering/` → `.claude/steering/`
- `.kiro/settings/` → `.claude/kiro/settings/`

It also patches all path references in command and agent files.

```bash
chmod +x scripts/remap-ccsdd-paths.sh
./scripts/remap-ccsdd-paths.sh
```

The script is idempotent — safe to run multiple times.

---

## Step 5: Create CLAUDE.md

Create `CLAUDE.md` at the repo root. Adapt for your project:

```markdown
# [Project Name]

## Context Resources (read on demand, not upfront)
- `.claude/steering/` — read when you need project architecture, stack, or code structure context
- `.claude/memory/hot-memory.md` — read at session start (current state, priorities)
- `.claude/memory/meta/patterns.md` — read at session start (workflow patterns)
- `.claude/memory/` — read when you need cross-session context or past decisions
- `specs/` — read when working on or near a feature that has a spec
- `.claude/behaviors/` — read when reviewing agent conduct or grading a trace, not upfront; kept blind from the agent whose trajectory it grades
- `.claude/docs/` — read when the user asks how to replicate or explain the SDD setup
- Context Hub MCP tools (`chub_search`, `chub_get`) — available automatically for third-party API docs

## SDD Workflow
1. `/kiro:steering`         — bootstrap/refresh project memory
2. `/kiro:steering-custom`  — add domain-specific steering (auth, DB, API, etc.)
3. `/kiro:idea-refine`      — refine vague ideas into spec-ready briefs (optional)
4. `/kiro:spec-init`        — start a new feature
5. `/kiro:spec-quick`       — fast path (requirements→design→grill→tasks in one command)
6. `/kiro:spec-impl`        — implement from approved spec
7. `/kiro:debug`            — systematic 6-step bug triage
8. `/kiro:simplify`         — behavior-preserving code simplification
9. `/kiro:verify`           — 7-stage verification pipeline (stage 7 = runtime, conditional)
10. `/kiro:ship`            — launch readiness check with rollout planning
11. `/kiro:reflect`         — review session, extract patterns, update memory
12. `/kiro:evolve`          — audit harness rules, propose improvements

## Rules
- Keep context under 40% before moving from planning to implementation
- Plan before multi-file or design-affecting changes — use Plan mode for back-and-forth. Sketch 2-3 approaches when the design is genuinely open; otherwise pick one and say why
- Features with clear correctness criteria need an approved spec in `specs/` before implementation — prefer an executable spec (failing test suite) or reference implementation over plain markdown. Markdown stays the default for open-ended/UX work. Bugfixes, perf work, and tooling do not need a spec
- Atomic commits per task (one task = one commit, code only)
- Never skip the human review gate between spec phases
- Never commit SDD files — harness is local only; keep operator-specific or machine-specific preferences in `CLAUDE.local.md` (gitignored), not in this file
- Read `.claude/memory/hot-memory.md` and `meta/patterns.md` at session start
- Observations are append-only; never edit past entries
- Hot memory stays under 50 lines; patterns under 70 lines
- Each fact lives in ONE file; reference via paths, don't duplicate

## AI-Legible Code
- **Blast radius**: prefer changes that touch ≤1 folder/module; if >1, scope down first
- **Rule of Three**: no shared extraction until 3 real call sites exist — two similar = coincidence
- **Vertical slices**: feature = one folder (routes/logic/data/types/tests); no cross-feature imports; no `shared/`, `utils/`, `common/`
- **Fail fast**: validate at every public boundary; named exceptions; no bare `except`; no silent fallbacks
- **Context rot**: AI coherence degrades past ~300k tokens — keep functions and PRs small
- **Reviewer model mismatch**: use a separate session/model to review AI-generated code
- **3rd patch, same function**: on a third patch to the same function, regenerate it from spec instead of layering another fix — patches stacking past two is how blast radius quietly outgrows the original ≤1-folder scope (kiro.dev — Frontier Engineering)
- **Boundary tests outlive the code they check**: e2e, property, and load tests are the stable spec — when code under them gets regenerated (see the 3rd-patch rule above), the tests are what proves the regenerated version still behaves the same. Write boundary tests before regenerating, not after (kiro.dev — Frontier Engineering)

## Blast Radius
The Risk Gate table in `.claude/rules/lean-ctx.md` is the one check, in order. It takes
precedence over the auto-generated GitNexus block below, which states its own rule without
knowing about Serena or lean-ctx.

## Quality Gates (automated)
- `ruff check`: on every `.py` file write
- `oxlint` (or `eslint`): on every `.ts`/`.tsx`/`.js`/`.jsx` file write, when one is installed. A finding naming an anti-slop rule (`no-unknown-parameters`, `no-unsafe-dictionary-type`, `no-chained-type-assertions`, …) means type evidence was thrown away — recover the real type, never silence the rule
- `pytest -x --ignore=tests/integration`: after each impl task
- doc sync: on every git commit
- harness guide sync: at end of any session where harness files changed
- memory reflect: after significant sessions (spec completion, major impl)
- memory housekeeping: when observations.md exceeds 50 entries
```

The `## Blast Radius` section is a **pointer, not a summary** (changed 2026-09-24 — it used to
restate the check as a three-step list, a second copy that could drift). The Risk Gate table it
points at lives in `.claude/rules/lean-ctx.md`, installed into every project, and names one check
per edit — run the first row that applies: a Python function/class → `mcp__serena__find_referencing_symbols(symbol)`
(LSP-accurate, authoritative); any other symbol → `mcp__gitnexus__impact({target, direction: "upstream"})`;
a broken/stale index or a non-symbol edit (auth, DB schema, 3+ files) → `ctx_callgraph(action="callers")`
plus `ctx_graph` for file-level deps. Report HIGH/CRITICAL risk instead of proceeding silently, and
treat a tool that errors or reports a version mismatch as **no answer** — it has not said there are
no callers. Keeping the precedence claim in `CLAUDE.md` is the load-bearing part: the auto-generated
GitNexus block further down states its own rule without knowing Serena or lean-ctx exist.

The `pytest -x --ignore=tests/integration` gate above is conditional on the project having a suite.
The harness repo's own `CLAUDE.md` replaced it with "No pytest suite; verify shell via `*.test.sh`
+ throwaway-tree runs" — state what your project actually does rather than carrying an instruction
that never fires.

> After creating this file, run `/codebase-legibility` inside Claude Code to complete the setup: subdirectory CLAUDE.md files for services and modules, `.claudeignore` for noise exclusion, and a codebase map if the repo has many top-level directories.

> **`CLAUDE.md` vs `CLAUDE.local.md`.** `CLAUDE.md` is gitignored in installed projects (Step 1) but committed in the harness repo itself, so treat it as shared: project conventions only. Anything tied to one operator or one machine — forms of address, local paths, personal workflow nudges — belongs in `CLAUDE.local.md`, which is gitignored in both cases. Claude Code loads both.

---

## Step 6: Create .claude/settings.json

**Linux / macOS / WSL2:**
```bash
mkdir -p .claude/hooks
```

**Windows (PowerShell — native, no WSL2):**
```powershell
New-Item -ItemType Directory -Force -Path .claude\hooks
```

> **Windows hook paths**: The `command` values in the hooks below use `/bin/bash /path/to/...`. On native Windows, replace `/bin/bash` with the Git Bash path: `"C:/Program Files/Git/bin/bash.exe" /path/to/...`. On WSL2, the Linux paths work as-is.

Create `.claude/settings.json`.

> **Strict JSON — no comments, nothing after the closing brace.** Claude Code parses this file as strict JSON and fails *quiet*: a malformed file is dropped whole, so every permission rule and hook in it silently stops working while the session looks normal. Keep explanatory notes in a `.claude/settings.notes.md` sidecar instead (`install.sh` and `update.sh` create one from `templates/settings.notes.md.template`). The annotations below the JSON block are documentation only — do not paste them into the file. Validate any settings file or template with:
>
> ```bash
> scripts/setup/check-settings-json.sh .claude/settings.json
> ```

```json
{
  "env": {
    "CLAUDE_CODE_TASK_LIST_ID": "your-project-name"
  },
  "mcpServers": {
    "context-hub": {
      "command": "npx",
      "args": ["-y", "@aisuite/chub-mcp"]
    }
  },
  "permissions": {
    "allow": [
      "Bash(ruff check:*)",
      "Bash(python -m ruff check <file>)",
      "Edit(docs/**)",
      "Write(docs/**)",
      "Edit(specs/**)",
      "Write(specs/**)",
      "Edit(.claude/docs/**)",
      "Write(.claude/docs/**)",
      "Edit(.claude/steering/**)",
      "Write(.claude/steering/**)",
      "Edit(.claude/memory/**)",
      "Write(.claude/memory/**)",
      "Edit(**/*.md)",
      "Write(**/*.md)",
      "WebFetch(domain:raw.githubusercontent.com)",
      "Skill(kiro:your-skill-name)",
      "Skill(kiro:your-skill-name:*)"
    ],
    "additionalDirectories": [
      "/path/to/.claude/kiro/settings/templates/memory",
      "/path/to/.claude/memory",
      "/path/to/.claude/docs",
      "/path/to/.claude"
    ]
  },
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "echo \"$CLAUDE_TOOL_INPUT_path\" | grep -q '\\\\.py$' && uv run ruff check --fix \"$CLAUDE_TOOL_INPUT_path\" 2>/dev/null || true"
          }
        ]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/impeccable-detect-hook.sh"
          },
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/hook-added-notify.sh"
          },
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/skill-permissions-gate.sh"
          }
        ]
      },
      {
        "matcher": "Read",
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/lean-ctx-nudge-hook.sh"
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/revert-detect-hook.sh"
          },
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/setup-buffer-hook.sh"
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "hooks": [{ "type": "command", "command": "/bin/bash /path/to/.claude/hooks/session-start-hook.sh" }]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/stop-hook.sh"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/memory-discipline-hook.sh"
          },
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/protected-path-hook.sh"
          },
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/skill-validate-hook.sh"
          }
        ]
      },
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/git-destructive-guard-hook.sh"
          },
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/agent-commit-attribution-hook.sh"
          }
        ]
      },
      {
        "matcher": "Agent",
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/gbrain-agent-spawn.sh"
          }
        ]
      },
      {
        "matcher": "mcp__plugin_claude-mem_mcp-search__save_observation",
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/gbrain-memory-write.sh"
          }
        ]
      },
      {
        "matcher": "WebFetch|WebSearch",
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/gbrain-external-search.sh"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "/bin/bash /path/to/.claude/hooks/compaction-discipline-hook.sh"
          }
        ]
      }
    ]
  }
}
```

What the entries above are for (keep this in `settings.notes.md`, not in the JSON):

- `Bash(ruff check:*)`, `Bash(python -m ruff check <file>)` — pre-approved ruff lint commands.
- `Edit(...)` / `Write(...)` pairs on `docs/**`, `specs/**`, `.claude/docs/**`, `.claude/steering/**`, `.claude/memory/**` — permissions for the doc-sync and harness-updater subagents spawned by the post-commit hook. Pair a `Write(...)` rule with every `Edit(...)` rule so the subagents can create new files, not just edit existing ones.
- `Edit(**/*.md)` / `Write(**/*.md)` — lets the doc-update hooks proceed without an approval prompt.
- `WebFetch(domain:...)` — domains Claude is allowed to fetch.
- `Skill(kiro:...)` — skill permissions, added as needed.
- `additionalDirectories` — directories outside the repo root that Claude should have read access to.

> `/path/to/` above is a placeholder for readability only — **do not** hand-write an absolute path there. What the installers actually render is `bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/<hook>.sh"`, which needs no editing at all.

> **Canonical source**: this JSON is an illustrative excerpt. The authoritative hook wiring lives in the harness source tree at `templates/settings.json.template` (per-project) and `templates/settings.harness.json.template` (the harness's own repo). Both now emit `bash "${CLAUDE_PROJECT_DIR:-.}/.claude/hooks/..."` — absolute when Claude Code exports `CLAUDE_PROJECT_DIR`, CWD-relative otherwise, machine-specific never. The harness template previously carried `{{HARNESS_DIR}}`-prefixed commands that `install.sh` substituted with the installing machine's absolute path; that substitution is what baked 23 machine-specific hook paths into `.claude/settings.json` and silently disabled every hook the moment the harness directory moved. Every command string is also quoted, so a harness or project path containing spaces no longer word-splits. `install.sh` / `update.sh` render these templates — edit the templates, not a generated `settings.json`. Both templates register `skill-permissions-gate.sh` on `PostToolUse Write|Edit` and `setup-buffer-hook.sh` on `PostToolUse Bash`. `caveman-savings-hook.sh` is wired on `Stop` in `templates/settings.harness.json.template` only (the harness's own dogfood repo) — not in the generic per-project `settings.json.template`. `templates/settings.json.template` also registers `reject-feedback-hook.sh` on `UserPromptSubmit` and `ai-writing-guard-hook.sh` on `PreToolUse Write|Edit|MultiEdit|Bash` (both new; see Automated Hooks below). The per-project template additionally registers `headless-envelope-hook.sh` on `SessionStart`, `js-quality-gate-hook.sh` on `PostToolUse Write|Edit|MultiEdit`, `todo-focus-hook.sh` on `PostToolUse TodoWrite`, and `subagent-context-hook.sh` on a new `SubagentStart` block with an empty matcher, plus `doc-parse-nudge.sh`, `caveman-savings-hook.sh` and `action-capture.sh`. Both templates register `pr-evidence-hook.sh` on `PreToolUse Bash`, alongside `agent-commit-attribution-hook.sh`.

> **Two template changes that are not hook wiring** (2026-09-03). Both templates now open with an `env` block setting `BASH_MAX_OUTPUT_LENGTH` to `15000`, capping how much of a shell command's output is admitted into context. And every `Write(path)` permission rule has been removed from the `allow` lists: Claude Code consults `Edit(path)` only for file-permission checks, and `Edit(path)` already covers Write, Edit and MultiEdit, so each `Write(...)` entry granted nothing while Claude Code announced it as inert once per rule at startup. Every removed entry had an `Edit(...)` twin already present, so this is dedup with no capability change. `scripts/setup/fix-inert-write-rules.py` performs the same repair across the registered fleet — dropping an allow `Write(X)` that has an `Edit(X)` twin, *renaming* one that does not, and always renaming (never dropping) a deny `Write(X)`, since an inert deny is a protection the user believes they have and deleting it would confirm the gap instead of closing it.

> **The two templates are reconciled, not maintained separately.** `templates/settings.json.template` is the source of truth for shared hooks; `templates/settings.harness.json.template` is derived from it by `scripts/setup/reconcile-settings-templates.py`, which enforces `hooks(harness) == hooks(project) + HARNESS_ONLY`. `HARNESS_ONLY` is an explicit allowlist and every entry carries a written reason (currently one: `address-check-hook.sh`, enforcing a convention that exists only in the harness's own `CLAUDE.md`). The two had silently diverged — as of 2026-08-30 the harness template was missing 14 hook registrations the project template shipped and carried 4 the project template did not, so those 14 hooks fired in every installed repo and *not* in the repo where they are written and tested. `install.sh` and `update.sh` both run `--sync` **before** copying the harness template into `.claude/settings.json`, so regeneration can never propagate drift; a failure there warns and continues rather than aborting. `/kiro:harness-validate` runs `--check` as a blocking step. **Permissions are deliberately excluded** — the two files *should* differ there, since the harness grants itself write access to its own source tree (`hooks/`, `scripts/`, `templates/`, `agents/`, `kiro/`) that no target project may have, and denies `git push*` outright where projects only deny force-push. The workflow is: add the hook to `templates/settings.json.template`, then run `--sync`. Editing the harness template directly is exactly the drift the check exists to catch.

> **Five new hooks wired, plus one dual-mode change (2026-09-17).** `templates/settings.json.template` and `templates/settings.harness.json.template` both gained: `risk-zone-edit-gate-hook.sh` on `PreToolUse Write|Edit` (soft warn on red/yellow zone files from `.claude/steering/risk-zones.md`), `sloppiness-warn-hook.sh` on `PostToolUse Write|Edit|MultiEdit` (Verbosity/Erosion scoring, warns only above the published high-slop baseline), `verification-retry-hook.sh` on `Stop` (advisory by default — `VERIFICATION_RETRY_WARN_ONLY=1` — capped retry loop when opted in via `VERIFICATION_RETRY_MAX`), and `pr-risk-tier-hook.sh` on `PostToolUse Bash` alongside `pr-auto-create-hook.sh` (labels the open PR `risk:red`/`yellow`/`green`). `lean-ctx-nudge-hook.sh` picked up a second registration, `PreToolUse Read` (hard-denies a native Read on a file ≥16 KB, naming the `ctx_read` mode to use instead; `LEAN_CTX_NUDGE_WARN_ONLY=1` downgrades to a warning) — its original `PostToolUse Write|Edit` entry is untouched. `cheap-model-delegation-hook.sh` (advisory nudge toward the `cheap-model-delegation` skill for bulk Bash `cat`/`head`/`tail` reads) is registered on `PreToolUse Bash` in `templates/settings.json.template`; it was initially excluded from the harness's own `settings.harness.json.template` and not part of `HARNESS_ONLY`, but was added there too on 2026-09-23 to close that drift. `pr-risk-tier-hook.sh`, `risk-zone-edit-gate-hook.sh` and the newly-gated `scripts/pr/detect_base_and_create.sh` all read the same opt-in git config flag, `hooks.autopilot.enabled` (worktree-scoped: `git config --worktree hooks.autopilot.enabled true`, after a one-time `git config extensions.worktreeConfig true` in the main worktree) — see Step 8 for the full mechanism and rationale.

> **Templates are validated on the way out.** `install.sh` runs `scripts/setup/check-settings-json.sh templates/settings.json.template` before copying and refuses to create `.claude/settings.json` if the template is not strict JSON; `update.sh` runs the same check against the regenerated harness `settings.json` and the per-project template. Until 2026-08-12 the per-project template carried a trailing `//` comment block documenting the optional ktx MCP server, which made every settings file installed from it unparseable — that block now lives in `templates/settings.notes.md.template`, copied to `.claude/settings.notes.md` on install and update. `scripts/setup/repair-settings-json.py <project>` backfills projects installed before the fix (idempotent; `--dry-run` to preview). `/kiro:harness-validate` runs the checker as a blocking step.

---

## Step 7: Create .claude/hooks/stop-hook.sh

Create `.claude/hooks/stop-hook.sh` and make it executable:

```bash
chmod +x .claude/hooks/stop-hook.sh
```

**No path editing required.** The hook reads the harness root from `~/.sdd-harness-root` — **the** single stored pointer to the harness, written by `install.sh` / `update.sh` via `scripts/lib/harness-pointer.sh`. It is the only file on the machine that records where the harness lives, and it stays a plain file rather than a symlink because Windows and Git Bash need elevated privileges to create symlinks (`~/.claude/sdd-harness` is a convenience symlink *derived* from it, never read as an independent source). This makes the hook fully portable: rename the harness directory, move to a new machine, or change install depth — it resolves correctly without modification.

The two failure states are distinguished. An **absent** pointer means the harness was never installed globally, and the hook exits silently — correct. A pointer naming a directory that **no longer exists** means the harness moved, which deactivates every cross-repo hook on the machine; the hook (and `session-start-hook.sh`) prints `[HARNESS-POINTER-STALE]` with the dead path and the fix (`bash <harness>/update.sh`) before exiting 0. Both used to be an indistinguishable silent `exit 0`, so a move could disable the harness fleet-wide with no symptom for weeks.

**`$SDD_HARNESS` (human-facing counterpart).** `~/.sdd-harness-root` is what *scripts* read; `$SDD_HARNESS` is what *you* type. Both `install.sh` and `update.sh` export `SDD_HARNESS="$HARNESS_DIR"` and write it into `~/.zshrc` and `~/.bashrc` (whichever exist) at the end of the globals stage — appending an `export SDD_HARNESS="..."` line under a comment on first run, and rewriting the existing line in place (via `sed`) on later runs so the value follows the repo if it moves. No manual setup is needed, and every documented command can then be written as `$SDD_HARNESS/install.sh …` regardless of where the harness was cloned. On the very first run the variable isn't set yet in the current shell, so bootstrap with a direct path to the clone; open a new shell afterwards and it's available everywhere.

The stop hook runs lightweight checks at the end of every Claude session:

1. **Harness update check** — if the harness has new commits since last install, prints a nudge to run `update.sh`.
2. **Memory health check** — if `.claude/memory/observations.md` has >50 entries, prints a nudge to run `/kiro:housekeeping`.
3. **Agent failure pattern detection** — if `.claude/memory/trace.log` shows 3+ consecutive failures for the same agent, prints a nudge to run `/kiro:evolve` to investigate friction patterns.
4. **Session signal detection** — runs `scripts/session/detect_reexplanation.py` against the session transcript (Haiku via `claude --print`, i.e. the user's subscription — the `anthropic` SDK path was removed on 2026-08-20). Drain signals append a `[memory-gap]` observation; charge signals (unambiguous approval) append a `[session-charge]` observation. Both at most once per calendar day. Skipped entirely when `SDD_HEADLESS=1`, which the detector sets on its own nested session (that is what stops the recursion) and which the routine runners set so unattended runs are not measured. If the detector cannot run it writes a `[detector-down]` observation and exits 4 rather than reporting zero, and it records the drain count — zero included — to `.claude/memory/metrics.jsonl` via `scripts/session/record_metric.py`.
5. **learnings.jsonl promoter** — once per day, ranks today's `observations.md` entries by tag priority and appends the top-ranked one to `.claude/memory/learnings.jsonl`.
6. **Stale action-item escalator** — parses `.claude/memory/action-items.md` for overdue `- [ ] <desc> | due:YYYY-MM-DD` items and appends a `[stale-action-item]` observation for the most-overdue one, once per day.
7. **Cache-cost dominance nudge** — if cache tokens are ≥70% of the session's token spend and the session compacted at least once, appends a `[cache-cost]` observation and invokes `scripts/session/write_handoff.py --trigger cache-cost` to write a resumable snapshot to `.claude/memory/handoff/latest.md`.

See `docs/hooks/README.md` for the full nine-check reference (this list omits two lower-signal checks — session depth tracking, setup sequence capture — kept out of this walkthrough for brevity).

Doc sync and harness updates are **not** triggered here — they fire from the git post-commit hook (Step 8) instead.

**Design principle**: Doc sync belongs in the git lifecycle, not the Claude session lifecycle. Running `claude --print` background agents on every session stop blocks Claude Code and spawns subprocesses on every message. The post-commit hook fires exactly once per commit, with a clear scope (the changed files in that commit).

See the full script in `.claude/hooks/stop-hook.sh`.

---

## Step 8: Install Git Hooks

**Linux / macOS / WSL2:**
```bash
chmod +x scripts/setup-git-hooks.sh
./scripts/setup-git-hooks.sh
```

**Windows (Git Bash):**
```bash
# Run from Git Bash (not PowerShell or CMD):
./scripts/setup-git-hooks.sh
```
The `chmod +x` is not needed on Windows; Git Bash handles executable bit for shell scripts automatically when called via bash.

This script:
1. Installs `.git/hooks/post-commit` — triggers **both doc sync and harness updater** on every commit
2. Patches `.claude/settings.json` with the repo's absolute path (replacing `/path/to/`)

The post-commit hook applies a self-commit guard first, then runs three stages:
- **Guard — self-commit bail** — if the subject of the commit just made starts with `docs: auto-sync`, the hook exits immediately, before every stage (GitNexus reindex included). Hook 3's auto-sync commits touch `.md` files under watched source dirs such as `skills/` and `hooks/`, which matched the harness-updater guard and re-fired the agents in a loop.
- **Hook 1 — Doc sync** — detects whether any non-`.md`, non-`.claude/` files changed and builds the doc-sync prompt. The prompt's `.md` scan excludes `.venv/`, `.git/`, `__pycache__/`, and `.claude/` — the installed `.claude/` mirror is regenerated output, so doc sync no longer edits it (and no longer updates `.claude/steering/`; use `/kiro:sync-docs` for steering). It no longer spawns `claude` itself — Hook 3 runs the prompt.
- **Hook 2 — Harness updater** — runs if the commit touched the harness **source tree**, matched by `^(agents|commands|hooks|kiro|scripts|rules|templates|skills)/` or `^CLAUDE\.md$`. (Previously this matched `.claude/`; `.claude/` is now generated output, rebuilt by `install.sh` / `update.sh`, so it is gitignored and never the trigger.) Builds the guide-sync prompt for `docs/harness-documentation/SDD-SETUP-GUIDE.md`; Hook 3 runs it. The prompt routes each changed path to its section: `commands/` → slash commands, `agents/` → subagents, `CLAUDE.md` → project constitution, `skills/` → skills, `hooks/` → automated hooks, `kiro/` → rules/templates, `rules/` → context engineering, `templates/settings*.template` → hooks/configuration.
- **Hook 3 — Detached runner (sync, then auto-commit + push `.md`)** — if either guard above fired, everything runs as **one fully-detached background job**: the doc-sync agent, then the harness-updater agent, then `git add -- '*.md'` → `git commit -m "docs: auto-sync (<date>)"` → `git push`, scoped to `*.md` only. Non-`.md` changes sitting in the working tree are never staged or pushed. If the push fails (no remote/auth), the commit still lands and the runner logs a warning instead of failing.
- **Why detached** — `git commit` returns immediately and the terminal is freed (no hang, no Ctrl-C). The job runs with stdin from `/dev/null`, is `disown`ed, and appends all output to `.git/post-commit-docsync.log`, so nothing reaches the tty. Each `claude` invocation is wrapped in `timeout 900` (or `gtimeout 900` where only coreutils provides it) when available, so a stuck agent cannot block; if neither binary exists the agents run unbounded.
- **Concurrency lock** — the detached job serializes itself on `.git/post-commit-docsync.lock`, created with `mkdir` (atomic). If a previous run still holds the lock, the new run logs `=== skipped <date>: another doc-sync run is active ===` and exits without running the agents. This keeps rapid successive commits from spawning parallel agents that race on the git index and drop each other's commits. A lock left behind by a killed run is stolen once it is older than 30 minutes (`find -mmin +30` then `rmdir`); on normal exit an `EXIT` trap removes it. Clear one by hand with `rmdir .git/post-commit-docsync.lock`.
- **Re-entrancy** — the `.md`-only commit made by Hook 3 re-fires the post-commit hook, but the self-commit guard matches its `docs: auto-sync` subject and exits immediately. No loop. (The older "both guards fail" reasoning was not enough: auto-sync commits touching `.md` under `skills/` or `hooks/` still matched the harness-updater guard.)
- **Debugging** — tail `.git/post-commit-docsync.log`; each run is delimited by `=== doc-sync run <date> ===` and `=== done <date> ===`. A `=== skipped <date>: another doc-sync run is active ===` line means the lock was held — that commit's doc sync is covered by the run already in flight, or by the next commit.
- The GitNexus incremental reindex (when `.gitnexus/` exists) is likewise backgrounded with stdin from `/dev/null` and stdout/stderr discarded, so it cannot print into or block the terminal.
- The `--dangerously-skip-permissions` flag is required: the background agents run non-interactively (no terminal to answer permission prompts), so without it the doc sync would stall on the first `Edit`/`Write` and never apply changes.
- **Executable bit** — the source file `hooks/git/post-commit` is *not* required to be executable in the harness tree (it is currently mode `100644`). `install.sh` and `update.sh` copy it to `.git/hooks/post-commit` and then run `chmod +x` on the copy, so the installed hook is always executable regardless of the source mode. If you install the hook by hand, `chmod +x .git/hooks/post-commit` yourself.
- **`REPO_ROOT` comes from `git rev-parse --show-toplevel`, not the hook's own location (2026-09-17).** The hook previously derived `REPO_ROOT` as `$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)` — the directory the *installed hook file* lives under. A hook file always lives under the main repo's `.git/hooks/`, but git invokes it with `cwd` set to whichever **worktree** triggered the commit (`git worktree add`). `BASH_SOURCE`-derived `REPO_ROOT` therefore always resolved to the main repo, so Hook 3 (doc-sync) would `cd` there and commit/push docs against whatever branch happened to be checked out in the main repo — not the branch that actually just got the commit. `git rev-parse --show-toplevel` (run with no forced `cwd`) always resolves to the worktree that fired the hook; the hook now exits 0 immediately if that call fails rather than falling back to a guess.
- **Opt-in gate: `hooks.autopilot.enabled` (2026-09-17).** The check sits right after `REPO_ROOT` is resolved and *before* `CHANGED` is computed — so when `git config --bool hooks.autopilot.enabled` doesn't read `true` for the current worktree, the entire `post-commit` script exits 0 immediately: not just Hook 3 (doc-sync), but also Hook 0 (the GitNexus incremental reindex) and every other guard below it never run at all. Enable it per worktree with `git config --worktree hooks.autopilot.enabled true` (requires a one-time `git config extensions.worktreeConfig true` in the main worktree so worktree-scoped config sections exist at all). The gate exists because ad hoc review/PR worktrees created via `git worktree add` should never auto-commit or push docs, PRs, or labels to somewhere outside the session that created them — a review checkout is disabled by default until a human explicitly opts it in. The same flag also gates `hooks/claude/pr-risk-tier-hook.sh` and `scripts/pr/detect_base_and_create.sh`, so one switch controls every "this worktree may push/create/label something visible outside this session" behavior.

**`.git/hooks/pre-commit` (harness repo only).** `hooks/git/pre-commit` runs **two** guards, both about the harness lying to itself, and blocks the commit if either fails. It is installed by `install.sh` / `update.sh` (`install_harness_pre_commit()` in `scripts/lib/harness-pointer.sh`) into the harness repo only — never propagated to downstream projects, since a user's own project may legitimately reference absolute paths. Before this, the hook existed in the source tree but nothing ever copied it: both installers handled only `post-commit`, and the hook's own header documented a manual `cp` from a `git-hooks/` directory that does not exist — so the guard had never run automatically while two files advertised it as wired up.

1. **`scripts/utils/check-no-hardcoded-paths.sh`** — a machine/user-specific absolute path must never be committed into harness source. Every path is self-located (`scripts/lib/resolve-harness-dir.sh`) or computed from `$HARNESS_DIR`.
2. **`scripts/utils/check-no-regex.py`** (added 2026-09-03) — `ruff.toml` bans `re` via TID251, but ruff only reads `.py` files, so the ban never reached the Python living in shell heredocs. This guard covers that half, running against a shrinking debt ledger (`scripts/utils/no-regex-debt.txt`) so the 15 pre-existing violations do not block commits while new ones do. It is skipped silently if `python3` is unavailable.

Both run and **both verdicts print** rather than short-circuiting on the first failure — fixing one and rediscovering the other on the next attempt wastes a cycle. Each guard is also skipped if its script is absent, so a downstream repo that somehow inherits the hook does nothing rather than erroring.

The install is non-destructive: an existing `.git/hooks/pre-commit` that is *not* this guard is left alone with a note telling you the line to add. A pre-commit that already contains `check-no-hardcoded-paths` is overwritten on every install/update, so extra lines appended to it (e.g. the `scan-pii.sh --staged` call the README documents) belong in `hooks/git/pre-commit` in the source tree instead. Bypass for one commit with `git commit --no-verify`.

**What the guard scans.** `*.sh`, `*.py`, `*.json` and `*.template` under the harness root — config counts as code. It originally scanned only `*.sh`/`*.py` and excluded `.claude/**` wholesale, which made `.claude/settings.json` invisible on both counts while it held 23 absolute hook paths; the guard reported `✓ No hardcoded paths` the whole time. Exclusions are now narrow and each is deliberate: `.claude/**` (synced copies of this source — they would double-report), `docs/**` (prose and frozen historical records), `skills/**` (vendored third-party skills whose recorded benchmark output contains other people's home paths — 13 of them drowned the real findings), `scripts/lib/resolve-harness-dir.sh` (the one allowed depth marker), `scripts/lib/harness-pointer.sh` (the one allowed namer of `~/.sdd-harness-root` and its convenience symlink), and the guard script itself (it names the patterns it bans). Gitignored files that are generated per machine and must still be path-free are re-admitted explicitly via a `GENERATED_SCANNED` list — currently just `.claude/settings.json`, since `settings.local.json` is the user's own file and may legitimately hold absolute paths (e.g. `additionalDirectories`). `git ls-files` cannot see generated files, hence the explicit list.

The script is idempotent — safe to run multiple times.

---

## Step 9: Add Custom Subagents

Create two custom subagent definitions in `.claude/agents/kiro/`:

### doc-sync.md
Documentation synchronization agent. Reviews `git diff HEAD~1` and updates relevant `.md` files in `specs/` and `.claude/steering/` to prevent documentation drift.

Triggered by: `/kiro:sync-docs` command or the post-commit hook.

### harness-updater.md
Harness documentation maintenance agent. Updates this file (`SDD-SETUP-GUIDE.md`) whenever Claude Code harness files change.

Triggered by: the git post-commit hook when harness **source** files are in the commit — `agents/`, `commands/`, `hooks/`, `kiro/`, `scripts/`, `rules/`, `templates/`, `skills/`, or `CLAUDE.md`. Target file is `docs/harness-documentation/SDD-SETUP-GUIDE.md`.

See the full agent definitions in `.claude/agents/kiro/doc-sync.md` and `.claude/agents/kiro/harness-updater.md`.

---

## Step 10: Bootstrap Steering

In a fresh Claude Code session:

```
/kiro:steering
```

Generates `.claude/steering/product.md`, `tech.md`, `structure.md` from the codebase.

Optionally run `/kiro:steering-custom` for domain-specific steering:
```
/kiro:steering-custom auth
/kiro:steering-custom database
/kiro:steering-custom api-standards
```

---

## Step 11: Generate Usage Guide

The usage guide (`.claude/docs/harness-documentation/SDD-USAGE.md`) is a quick-reference for all SDD commands with examples. It should be generated as part of setup so that users (and AI agents replicating the harness) know how to use the workflow immediately.

Copy the usage guide from this project, or generate one by asking Claude:

```
Create .claude/docs/harness-documentation/SDD-USAGE.md — a quick-reference listing every /kiro: command,
what it does, and an example invocation. Include a "Typical Workflow" section at the end.
```

This file is read-on-demand (not loaded at session start) and lives under `.claude/docs/harness-documentation/` alongside this guide.

---

## Step 12: Bootstrap Memory

The memory system provides persistent cross-session context using a cog-inspired architecture
(temperature-based tiers, progressive condensation, structured observations).

### Directory Structure

```
.claude/memory/
├── hot-memory.md              # <50 lines, read at session start — priorities, active specs, decisions
├── observations.md            # Append-only session log (max 50 entries before archival)
├── action-items.md            # Cross-session TODOs with due dates and priority
├── entities.md                # Project entity registry (services, APIs, databases)
├── meta/
│   ├── self-observations.md   # SDD workflow learnings (what worked, what didn't)
│   └── patterns.md            # Distilled workflow rules (<70 lines, read at session start)
└── glacier/                   # Archive for old observations (YAML frontmatter)
    └── index.md               # Auto-generated catalog
```

### Bootstrap

Run `/kiro:reflect` in any Claude session — it auto-creates `.claude/memory/` from templates
in `.claude/kiro/settings/templates/memory/` if the directory doesn't exist.

Or manually copy templates:

**Linux / macOS / WSL2:**
```bash
mkdir -p .claude/memory/meta .claude/memory/glacier
cp .claude/kiro/settings/templates/memory/*.md .claude/memory/
cp .claude/kiro/settings/templates/memory/meta/*.md .claude/memory/meta/
```

**Windows (PowerShell):**
```powershell
New-Item -ItemType Directory -Force -Path .claude\memory\meta, .claude\memory\glacier
Copy-Item .claude\kiro\settings\templates\memory\*.md .claude\memory\
Copy-Item .claude\kiro\settings\templates\memory\meta\*.md .claude\memory\meta\
```

Then seed `hot-memory.md` and `entities.md` with your project's current state.

### Memory Conventions

Conventions are defined in `.claude/kiro/settings/rules/memory-conventions.md`:

- **Observations**: `- YYYY-MM-DD [tags]: text` (append-only, max 5 per reflect)
- **Tags**: `spec`, `impl`, `design`, `debug`, `decision`, `friction`, `insight`, `pattern`, `enforceable`, `escaped`, `skill-update`
- **Action items**: `- [ ] task | due:YYYY-MM-DD | pri:high/medium/low | added:YYYY-MM-DD`
- **Entities**: 3-line max per entry
- **L0 headers**: Every memory file starts with `<!-- L0: summary (max 80 chars) -->`
- **SSOT**: Each fact lives in ONE file; others reference via paths, never copy
- **Caps**: hot-memory <50 lines, patterns <70 lines, observations <50 entries

### Memory Commands

| Command | When to run | What it does |
|---|---|---|
| `/kiro:reflect` | After significant sessions | Mines git log for observations, promotes patterns, updates hot-memory |
| `/kiro:housekeeping` | When observations >50 or periodically | Archives old observations to glacier, prunes caps, validates formats |
| `/kiro:evolve` | On demand | Audits harness rules against friction patterns, proposes improvements |

---

## Slash Commands

| Command | Purpose |
|---|---|
| `/kiro:steering` | Bootstrap/refresh project memory from codebase |
| `/kiro:steering-custom` | Add domain-specific steering (auth, database, API, etc.) |
| `/kiro:idea-refine "rough idea"` | Refine vague ideas into clear, spec-ready briefs. First runs a **Scale Check**: if `specs/_maps/<slug>.md` already exists, or the idea is program-scale per `issue-triage-routing` axis 4 (spans multiple decisions, each deserving its own spec), it charts/updates that map (destination + ordered fog list) via `idea-refine-agent`, then decomposes and re-triages the first fog item as a normal feature-scale brief — one pass never dead-ends at just the map. Feature-scale ideas with no map skip straight to the single-brief flow |
| `/kiro:spec-init "description"` | Initialize new feature workspace in `specs/`. When run standalone (not via `spec-quick`), first runs `/kiro:pref-elicit {$ARGUMENTS}` unless `prefs.md` already exists — surfaces declarative vs. imperative preferences before the spec assumes defaults |
| `/kiro:spec-requirements {feature}` | Generate EARS-format requirements (subagent) |
| `/kiro:spec-design {feature}` | Generate research notes + technical design (subagent) |
| `/kiro:spec-grill {feature}` | Domain grilling session — align terminology, crystallise decisions, update docs and ADRs inline (interactive) |
| `/kiro:spec-tasks {feature} [--sequential]` | Generate P-wave parallel task list (subagent); `--sequential` disables parallel `(P)` markers. On tasks approval, also greps `specs/_maps/*.md` for a fog line matching this feature and, if found, moves it from "Not yet specified" to "Decisions so far" (linking `specs/{feature}/`) — keeps a charted program map in sync as its slices get spec'd |
| `/kiro:spec-impl {feature}` | Implement from approved spec via TDD (subagent). Phase -1 adds a **Decision-Budget Gate** alongside the over-engineering and integration-ordering gates: every task must leave the implementer *inheriting* decisions rather than making them, and every deliberately-open freedom must be named in the task as delegated ("implementer's choice: X"). The other gates catch *too much*; this one catches under-specification, which does not fail loudly — it produces working, tested code embedding an architecture the user never chose. Phase +1 then runs `/kiro:audit-choices {feature}` **per pass** (not once at the end — waiting until close means auditing a session trace that has already been compacted away). The result feeds back into the gates: entries clustering on one slice means reslice it; a pass heavy with `needs-user` means the Decision-Budget Gate should have failed |
| `/kiro:audit-choices <feature> [--close]` | Reconstructs the decisions an implementation made where the spec said nothing, verdicts each `sound` / `unsound` / `needs-user`, and appends them to `specs/<feature>/choices.md`. Runs automatically as the last step of `/kiro:spec-impl`; invoke directly to re-audit a pass, audit work done outside the spec pipeline, or (with `--close`) resolve open calls and dedupe the ledger at spec close. Three constraints: **changes no code** (findings route to the next pass), **never blocks** (every `needs-user` entry carries a reversible provisional call so an unattended run completes — an irreversible provisional is reported as such), and **least-confident first** (ordered so the entries most needing attention are read before attention runs out). Drives `skills/auditing-spec-choices/` |
| `/kiro:spec-quick "description"` | Fast path: requirements→design→grill→tasks in one command (grill skipped with `--auto`). Pre-check applies `issue-triage-routing` — routes to ONE-SHOT (implement directly, no spec) / DEFER / CLARIFY before spec'ing; proceeds to full spec generation only on SPEC. In Interactive Mode, Step 1.5 now runs 1.5a Preference Elicitation (`/kiro:pref-elicit {description}`, mandatory, writes `prefs.md` used by phases 2–5) before 1.5b Idea Refinement |
| `/kiro:debug "bug description"` | Systematic 6-step bug triage (reproduce→fix→guard) |
| `/kiro:simplify <file-or-feature>` | Behavior-preserving code simplification |
| `/kiro:ship [feature]` | Launch readiness: verification + rollout planning |
| `/kiro:validate-gap {feature}` | Gap analysis: requirements vs. existing code |
| `/kiro:validate-design {feature}` | Design quality review (with remediation plan on NO-GO) |
| `/kiro:validate-impl {feature}` | Implementation vs. spec validation (with remediation) |
| `/kiro:validate-adversarial {feature}` | Three-pass adversarial review with +1/-2 scoring |
| `/kiro:converge [feature]` | Spec ↔ code reconciliation — detects bidirectional drift (spec-ahead / code-ahead / contradiction); reuses `validate-impl-agent` |
| `/kiro:spec-status {feature}` | Show current phase, approvals, and open tasks |
| `/kiro:sync-docs` | Sync docs with ALL code changes (uncommitted + staged + committed). Filters out `.md` files and `.claude/` paths — `.claude/` is regenerated output (rebuilt by `install.sh` / `update.sh`) and gitignored; changes to the harness **source** tree (`agents/`, `commands/`, `hooks/`, `kiro/`, `scripts/`, `rules/`, `templates/`, `skills/`, `CLAUDE.md`) are the harness updater's job. This is the *manual* trigger — the stop-hook runs it at session end, and the post-commit git hook is the safety net: it runs doc sync, then the harness updater, then auto-commits and pushes **only** the `.md` files they touched, all inside one detached background job (each agent bounded by `timeout 900`), so `git commit` returns immediately and output lands in `.git/post-commit-docsync.log` |
| `/kiro:reflect` | Review session, extract observations, update memory (subagent) |
| `/kiro:learn-eval [scope]` | Quality-gate session/sprint/feature patterns before saving to memory — scores specificity/actionability/evidence (1-3 each, pass ≥6); verdicts Save/Absorb/Route/Drop (subagent). Use for deeper periodic evaluation vs. `/kiro:reflect`'s quick frequent capture |
| `/kiro:housekeeping` | Prune memory, archive old observations to glacier (subagent) |
| `/kiro:daily-maintenance` | Nightly orchestrator — wires Judge → Reflect → Housekeeping → Trust Score → Skill Augment → Behavior Spec Mining into one pipeline; idempotent per calendar day (guards on today's `[judge]` observation), surfaces unresolved `[memory-gap]`s as `[routine-alert]`. Never edits code/specs — memory and skills only. **Step 1 spawns the Judge three times** (2026-09-03), all in one message so they run concurrently: the judge is an LLM at temperature > 0 and its `score_delta` lands in a cumulative score nothing ever revisits, so until this ran k=3, "the score fell 4 points" and "the judge sampled differently" were the same observation. The three prompts are identical and no run is told about the others — correlating them defeats the purpose as thoroughly as running one. Only the **caller** appends the single `[judge]` observation covering all three; three subagents each appending their own would triple-count every tag into `trust_score.py auto-score`, which counts tag occurrences. A failed run is **missing, not a vote for zero**: 1–2 missing/malformed verdicts log `routine-error: judge-verdict-invalid (N of 3)` and pass only the valid deltas to Step 4; all 3 bad passes a single `--delta 0`. Step 4 then calls `scripts/session/trust_score.py apply` with one `--delta` per run: the helper takes the **median** so one outlier draw cannot move the score, records the day `inconclusive` and applies `0.0` when the samples spread by more than `JUDGE_SPREAD_LIMIT` (2.0 on the ±4.5 scale), and persists the raw `samples`, `spread` and `inconclusive` flag into `trust-score.jsonl` so an inconclusive day is distinguishable from a day nothing ran. A single `--delta` is still accepted and skips the spread gate, reporting `"spread": null` rather than `0.0` — one sample has no spread, and claiming otherwise reads as perfect agreement. An `inconclusive` status is not a problem to fix; it is the helper declining to commit a number it cannot stand behind. Step 5 surfaces unresolved memory-gaps from the last 24h as a `[routine-alert]` observation if Step 2's output shows no new memory entries on the same topics. Step 6 augments skills (max 3 skills, append-only, ≤150 chars/addition, evidence-gated on observations and judge drains). Step 6b mines behavior specs (max 3, each cited, validated, `.claude/behaviors/` only). Tests: `scripts/session/trust_score.test.sh` |
| `/kiro:evolve` | Audit harness rules effectiveness, propose improvements (subagent) |
| `/kiro:harness-fix` | Encode a behavioral prevention rule from a specific mistake |
| `/kiro:harness-validate` | Check structural integrity of harness installation — broken references, missing files, memory caps, plus a blocking strict-JSON check of the settings templates and the live `.claude/settings.json` via `scripts/setup/check-settings-json.sh`. Also runs `scripts/utils/check-no-hardcoded-paths.sh` (same script the `.git/hooks/pre-commit` hook runs — that hook is now installed into the harness repo by `install.sh` / `update.sh`, where before it sat in the source tree with nothing ever copying it; invoke it by hand as `bash scripts/utils/check-no-hardcoded-paths.sh` — it lives under `scripts/utils/`, not `scripts/`) and `scripts/utils/check-fleet-registration.sh`, which reports harness-installed repos missing from `projects.txt`. Step 2b runs `python3 scripts/setup/reconcile-settings-templates.py --check` (harness repo only), asserting `hooks(harness template) == hooks(project template) + HARNESS_ONLY` — non-zero exit is a blocker, because drift there means a hook fires in every installed repo but not in the one where it is written and tested, or the reverse. Permissions are excluded on purpose: the two templates *should* differ there. Fix by adding shared hooks to `templates/settings.json.template` and running `--sync`, never by editing the harness template directly |
| `/kiro:harness-test` | Haiku smoke-test to expose vague prompts |
| `/kiro:tool-failure-review [min-count]` | Review the tool-failure ledger — diagnose recurring Bash/MCP failures and promote durable lessons into memory (default `min-count` 3). Review step of the tool-failure-memory loop (capture → recall → review) |
| `/kiro:verify [mode]` | Multi-stage verification pipeline (subagent). Modes: `quick` (build + test), `full` (default — all static/unit stages plus the conditional system stage), `pre-commit` (build + types + lint + test; skips audit, git status, and system), `pre-pr` (all stages + stricter thresholds, plus the conditional system stage). **Stage 7 (System/runtime)** runs only in `full`/`pre-pr`, and only when the diff touches ≥2 components or crosses a process/entrypoint boundary — it starts the app, drives the critical path, and asserts side effects, the only stage that proves the software runs rather than inspecting artifacts. `quick`/`pre-commit` never run it by design (seconds-to-minutes of startup is the wrong trade in a tight loop). Verdicts are `READY` / `NOT READY` / **`UNPROVEN`** — UNPROVEN means static checks and unit tests pass but the system stage was triggered and found no run command; fix by adding the run/start command to `.claude/steering/tech.md` and re-running `/kiro:verify full`. Never treat UNPROVEN as READY. On a system-stage failure the defect is at a boundary unit tests do not cross — fix, then re-run the whole stage from startup rather than resuming mid-sequence |
| `/kiro:guardrails` | Audit/scaffold rules for deterministic enforcement across **four independent dimensions**, each reported as its own coverage line and never summed — a project can be fully compliant on one and score zero on the rest. (1) **Complexity** caps how tangled one function is. (2) **Type evidence** (JS/TS) caps how much type information it threw away, which is where agent-written TypeScript fails most often. (3) **Assertion strength** caps whether the tests prove anything, via a mutation-testing gate — recommended only, never installed, since it turns a 30-second suite into minutes. (4) **Structure** covers duplication, dead code and import direction — the checks that look *across* functions, which no linter does: nothing has a rule for "this block also exists in another file", and a helper with no callers left lints clean because no call site disagrees with it. Structural checks must be scaffolded **delta-gated** (fail on what a change adds, not on whole-repo state, or the first run's hundreds of findings get the check disabled) and **agent-callable** (the benefit comes from the agent running it in the session it wrote the code, while it still knows why two copies exist) |
| `/kiro:ci-scaffold` | Generate CI configuration mirroring the verify pipeline |
| `/kiro:autoresearch-init` | Interactive ML project setup — generates program.md, train.py, prepare.py |
| `/kiro:autoresearch [N]` | Run autonomous ML experiment loop (N iterations or continuous). If `recipe.md` exists in the project root, it's passed to the agent alongside `program.md` as a versioned record of signal-filtering policy and staged-autonomy level (see AutoResearch section below) |
| `/kiro:macro-eval-sweep [days-back] [name-filter]` | Population-scale failure pattern sweep over Raindrop Workshop traces; clusters recurring failures, ranks by impact, writes dated report, posts Workshop annotations |
| `/kiro:daily-briefing` | Prioritized daily status briefing synthesized from `.claude/memory/manager/` plus whatever live sources are connected (git, GitHub, Jira/Confluence, Slack). Reads `~/.claude/skill-library/synthesizing-daily-briefings/SKILL.md` **by path** — the skill is Library tier, so the Skill tool cannot resolve it |
| `/kiro:adapt-to-repo <url, pasted content, or idea>` | Analyze an external page/post/idea and plan which parts are worth implementing here, with no redundancies against what the repo already does. Reads `~/.claude/skill-library/adapt-to-repo/SKILL.md` **by path** (Library tier) and treats `$ARGUMENTS` as its input |

### Global commands (`commands/global/`)

These live in the harness at `commands/global/` and are installed **machine-global** into `~/.claude/commands/` by `install_globals()` — they are not `kiro:`-namespaced and work in any repo, harness-installed or not.

| Command | Purpose |
|---|---|
| `/claudemd-review [--apply]` | Audits the **current repository's** always-loaded instruction files (`CLAUDE.md`, `@imports`, `.claude/rules/*`) against a lean-context rubric, writes a findings report, and stamps `.claude/memory/.last-claudemd-review` so the bi-weekly `session-start-hook.sh` reminder (`[CLAUDEMD-REVIEW-DUE]`, >14 days stale) resets. Defaults to propose-only; `--apply` writes the edits. It is the **per-repo** counterpart to the harness-health-runner routine, which audits *all* registered repos into `reports/claudemd-review-report.md` — the two write to different files and must not be confused |
| `/notify <message>` | Sends a message to the chat channels configured in `~/.env.channels`. Resolves the notifier as repo-local first (`.claude/scripts/integrations/channels/notify.py`), falling back to the harness source at `$SDD_HARNESS/scripts/integrations/channels/notify.py` — the fallback is written as `$SDD_HARNESS`, not a hardcoded `~/.claude/sdd-harness`, so it resolves wherever the harness was cloned |

---

## Subagents

| Agent | Trigger | Purpose |
|---|---|---|
| `@agents-spec-requirements` | `/kiro:spec-requirements` | EARS requirements generation; approval via Proof collaborative review session |
| `@agents-spec-design` | `/kiro:spec-design` | Research + technical design; approval via Proof collaborative review session |
| `@agents-spec-tasks` | `/kiro:spec-tasks` | P-wave task breakdown; approval via Proof collaborative review session |
| `@agents-spec-impl` | `/kiro:spec-impl` | TDD implementation per task |
| `@agents-spec-refactor` | After each impl task (auto, spawned by spec-impl agent) | Post-task self-review: reuse, quality, efficiency, 3-tier security checks + test re-run |
| `@agents-idea-refine` | `/kiro:idea-refine` | Structured ideation: problem framing → divergent/convergent thinking → spec-ready brief. For program-scale ideas, breadth-first charts the distinct decisions into a map instead of going deep on one. Renders approaches as a Mermaid diagram instead of bullets when the idea is visual/interface-shaped or has many plausible shapes — tangible comparisons surface reactions abstract text doesn't |
| `@agents-debug` | `/kiro:debug` or via jira-solve BUG routing | 6-step systematic debugging: reproduce → localize → reduce → fix → guard → verify |
| `@agents-jira-solve` (`jira-solve-agent`) | `/kiro:jira-solve` Step 3 | Analyzes a Jira issue JSON into a structured solve report: problem statement, acceptance criteria, and relevant codebase files found via Glob/Grep |
| `@agents-simplify` | `/kiro:simplify` or via spec-refactor complexity findings | Behavior-preserving simplification with Chesterton's Fence principle |
| `@agents-ship` | `/kiro:ship` | Staged rollout planning with decision thresholds and rollback procedures |
| `@agents-validate-gap` | `/kiro:validate-gap` | Requirements vs. code gap analysis |
| `@agents-validate-design` | `/kiro:validate-design` | Design quality review (with remediation) |
| `@agents-validate-impl` | `/kiro:validate-impl` | Implementation validation (with backlink checks) |
| `@agents-validate-adversarial` | `/kiro:validate-adversarial` | Three-pass adversarial review. Step 0 reads in a **fixed order that is itself the mechanism** — `spec.json` + `requirements.md` first and alone, writing down the acceptance criteria to judge against *before* seeing how anyone chose to satisfy them; then steering; then `design.md`/`tasks.md`; then source. Batching those reads defeats it. The **plan-blindness rule** follows: the builder's plan is evidence about what was *attempted*, never about what counts as *correct*, so any criterion that does not trace back to `requirements.md` is reported as **criterion drift** (a Concern in Step 1) rather than adopted. Distinct from `validate-impl`'s spec-integrity check, which catches a spec weakened in git after approval; this catches the reviewer silently inheriting the builder's definition of done from a spec nobody edited at all |
| `@agents-validate-production` | After all impl tasks complete (auto, spawned by spec-impl agent) | Production readiness scan: env config, deployment, resilience, observability, data safety, security posture, staging/CI + human attestation checklist |
| `@agents-steering` | `/kiro:steering` | Project memory bootstrap |
| `@agents-steering-custom` | `/kiro:steering-custom` | Domain-specific steering |
| `@agents-doc-sync` | git post-commit hook or `/kiro:sync-docs` | Code→doc drift prevention for committed changes |
| `@agents-harness-updater` | git post-commit hook (when harness **source** files are committed — `agents/`, `commands/`, `hooks/`, `kiro/`, `scripts/`, `rules/`, `templates/`, `skills/`, `CLAUDE.md`) | Harness→guide sync |
| `@agents-reflect` | `/kiro:reflect` | Session mining → observations, patterns, hot-memory |
| `@agents-learn-eval` | `/kiro:learn-eval` | Scores candidate patterns on specificity/actionability/evidence (1-3 each, threshold ≥6 to pass), then deduplicates against `meta/patterns.md`. Verdicts: **Save** (score≥6, no duplicate — new entry in patterns.md), **Absorb** (score≥6, similar entry exists — merge evidence in), **Route** (score≥6 but tied to exactly one existing skill — skip memory, hand to `skill-augment-agent` instead), **Drop** (score<6 or exact duplicate) |
| `@agents-housekeeping` | `/kiro:housekeeping` | Memory archival (observations >50 entries → glacier, keep 25 recent; completed action items >10 → keep 5), pruning (hot-memory <50 lines, patterns <70 lines), format validation (L0 headers, entry formats), stale-item flagging (action items >14d, entities >30d), glacier index rebuild. Reviews `[auto-learn, YYYY-MM-DD]`-tagged hot-memory entries left by the micro-reflect stop hook: keep if <7d old, promote to `meta/patterns.md` if 7d+ with reinforcing evidence, else remove (no archive — ephemeral by design). Also flags patterns/hot-memory entries tied to exactly one skill and recommends routing them into that skill via `skill-augment-agent` — never auto-moves, archive-first discipline |
| `@agents-evolve` | `/kiro:evolve` | Rule audit, friction analysis, trace log analysis, improvement proposals, linter graduation |
| `@agents-session-judge` (`session-judge`) | `/kiro:daily-maintenance` Step 1 (nightly, spawned **3×**) | Independent adversarial scorer of the harness's own session behavior — emits a JSON verdict only and is hard-constrained from proposing fixes. **Sample mode** (2026-09-03): when the caller says not to append a `[judge]` observation (daily-maintenance reconciles k=3 runs itself), the judge scores the window for real and the **duplicate-run rule does not apply**. That last rule exists to stop a rerun of the whole routine from double-scoring a day, but applied to a sample it would mean run 1 scores the day and runs 2 and 3 see run 1's entry and return `score_delta: 0`, turning three independent draws into `[-2, 0, 0]` whose median is 0 — not variance removed but the first sample discarded and replaced with two fabricated zeros. The prompt tells the judge which mode it is in; in sample mode, skip the duplicate check and score the window every time |
| `@agents-verify` (`verify-agent`) | `/kiro:verify` | Runs the verification pipeline and reports a structured PASS/FAIL/SKIP table. The stages are **three layers**, not a flat list: **L1 static** (1 Build, 2 Types, 3 Lint, 3b), **L2 unit** (4 Test, 5 Debug Audit), **L3 system** (7 System/runtime). Evidence from a lower layer never substitutes for a higher one, and **layer gating** applies — a layer whose predecessor FAILed is SKIPped with reason `gated by L<n> failure`, since a runtime run against a build that does not compile produces noise, not evidence. L3 exists because stages 1–6 only inspect artifacts (files, exit codes, output text) and none of them observes the software running; "unit tests pass" is not "the task is complete". **Step 7 procedure**: discover the run command (steering `tech.md` first, then `package.json` scripts, `Procfile`, `docker-compose.yml`, Makefile `run`), then start and wait for a real ready signal (listening port / readiness log / health 200 — not "the process didn't exit"), drive the critical path through the real interface, assert the side effect landed (a 200 with no side effect is a failure), check the error channel (new warnings count), and clean up (leaked resources → WARN). Any of those failing → FAIL; no run command found → SKIP with reason, never PASS. Step 7 gets its own 180s budget for startup + critical path (a run that never becomes ready is a FAIL, not a SKIP), separate from the 60s per-command limit on other stages. Reports only — never modifies code. **Completion priority**: refactoring/performance/style findings are reported only after functional verification passes, as deferred notes, never as reasons to hold a passing verdict |
| `@agents-guardrails` | `/kiro:guardrails` | Linter complexity rule auditing and scaffolding for deterministic code quality enforcement. The audit action now scores three independent dimensions: **Complexity** (cyclomatic complexity, nesting depth, function size, parameter count, statement count, cognitive complexity), **Type Evidence** for JS/TS as a separate coverage line (never folded into complexity: stacked casts, `unknown` in contracts, untyped bags, lost inference, casts without invariants, `typeof` checks, module mocking), and **Assertion Strength** across all ecosystems (a suite at 100% coverage can prove nothing if assertions are trivial; audit for mutation-testing gates per ecosystem — Python `mutmut`/`cosmic-ray`, JS/TS Stryker, Go `go-mutesting`/`gremlins`, Rust `cargo-mutants`, Java PIT). Each dimension is reported separately; a project can score zero on type evidence while passing complexity, and zero on assertion strength while passing both. The audit also grades **Message Quality** for project-authored rules: every rule whose message is missing or doesn't state the fix carries a WARN-level gap (never a hard failure). The scaffold action fills those messages in and recommends (never vendors) type-evidence rules from `dmmulroy/anti-slop`. Mutation testing recommendations name the diff-scoped entry point, never the full sweep, and must not wire into the lint script/CI/pre-commit — a gate that takes minutes is worse than absent |
| `@agents-ci-scaffold` | `/kiro:ci-scaffold` | CI configuration generation (GitHub Actions, GitLab CI, Azure Pipelines) |
| `@agents-harness-validate` | `/kiro:harness-validate` | Structural integrity check, component index generation. Step 3 also runs `scripts/setup/check-settings-json.sh` over `templates/settings.json.template`, `templates/settings.harness.json.template`, and `.claude/settings.json` — non-zero exit is a blocker, since Claude Code drops every permission rule and hook in a malformed settings file without warning and a broken template propagates that to every project installed from it. Notes belong in `settings.notes.md`, not in the JSON |
| `@agents-autoresearch-init` | `/kiro:autoresearch-init` | Interactive interview → file generation |
| `@agents-autoresearch` | `/kiro:autoresearch` | Autonomous ML experiment loop. Step 5's outcome table distinguishes **three** crash cases (2026-09-30), because the old single "crashed → revert" row threw away untested hypotheses: a crash caused by the change's **own bug** (typo, import error, shape mismatch) is a **REPAIR** — fix it and re-run under the *same* hypothesis, logging the crash as ERRORED, since the hypothesis has not been tested yet; a crash the hypothesis *predicts* (the idea itself diverges or OOMs) is a plain REVERT, because the crash is the answer; and the same hypothesis erroring twice after a repair is reverted and dropped. ERRORED runs no longer count toward the "reverted 2+ times in a row → pivot direction" rule — a crash that answered nothing is not evidence against the direction. The standing rule is **a crash is not a result; a disappointing result is**: repair crashes, but don't keep re-tweaking a hypothesis whose run completed and lost |
| `@agents-skill-augment` | `/kiro:daily-maintenance` (nightly) | Encodes session learnings into SKILL.md files; max 3 skills/run, append-only, ≤150 chars/addition. Evidence-gated on observations, judge drains, or `type: feedback` memories — human-feedback auto-qualifies and is drafted before machine signals. Dreaming step writes synthetic worked examples to `resources/examples/`. **As of 2026-09-23, never Write/Edit `~/.claude/skills/<name>/SKILL.md` (or its `resources/`) directly** — `update.sh`'s 4h launchd sync overwrites the installed copy from this repo's `skills/<name>/` regardless of session activity, silently reverting a direct edit. Both the SKILL.md body edit (Step 4) and the dreaming examples file (Step 3.5) are drafted to a temp file, then applied via `.claude/scripts/routines/skill-write.sh <skill-name> <temp-file> [relative-path]`, which updates the harness-source and installed copies together and keeps a timestamped backup. |
| `@agents-behavior-spec` (`behavior-spec-agent`) | `/kiro:daily-maintenance` Step 6b (nightly) | Reviews today's Judge verdict, `type: feedback` memories, and `[revert]`/`[drain]` observations for recurring **conduct** patterns (not skill-content gaps — that's `skill-augment-agent`'s job) and drafts/revises `.claude/behaviors/<name>/BEHAVIOR.md` — answer-key material for grading trajectories, deliberately never shown to the agent being graded. Max 3 specs/run; ≥2 occurrences required except `type: feedback` memories which auto-qualify at 1; every spec validated with `.claude/scripts/validate-behavior-spec.py` before being left in place; writes only under `.claude/behaviors/`, never `~/.claude/skills/` or `CLAUDE.md` |
| `@agents-save-session` (`save-session-agent`) | `/kiro:save-session` | Captures current session state (files touched, decisions, exact next step) into a resumable snapshot for `/kiro:resume-session`. Snapshot format includes a **Suggested Skills** section (which skill a resuming agent should call, e.g. `spec-tdd-impl` if mid-implementation) and follows two output-discipline rules: **no duplication** — reference specs/ADRs/commits/diffs by path rather than restating their content — and **redact secrets** — strip API keys/tokens/passwords from evidence before writing to the session file |
| `@agents-skill-extract` (`skill-extract-agent`) | `/kiro:skill-extract-scan` and `/kiro:skill-extract` | Analyzes a repository or extraction plan and generates harness artifacts (skills, hooks, scripts, commands, routines). Generated skills go into the **two-tier hierarchy** (`docs/skills/SKILL-HIERARCHY.md`): source to `skills/<name>/SKILL.md`, defaulting to the **Library** tier — name added to `scripts/setup/skill-library.txt` plus one row in the owning master's table, master chosen from the 14 domains — and left off the manifest (Listed) only when a hook or command will `Skill("<name>")` it. Its completion summary reports the installed path as `~/.claude/skill-library/<name>/SKILL.md` and names the master the skill was filed under |

---

## Automated Hooks

> **How hooks ship.** `install.sh` and `update.sh` copy every `hooks/claude/*.sh` into each project's `.claude/hooks/` and `chmod +x` them — no per-hook edit to the installers is needed. Files matching `*.test.sh` are skipped by that copy loop: they are harness-repo test suites (`hooks/claude/<name>.test.sh`), not runtime hooks, and shipping them would install non-hook scripts into every project's hook directory.

| Hook | Trigger | Action |
|---|---|---|
| PostToolUse (lint) | Every `.py` write in Claude | `uv run ruff check --fix {file}` |
| PostToolUse (impeccable) | Every frontend file Write/Edit (`.tsx/.jsx/.css/.vue/.svelte/.html`) | Runs `impeccable detect {file}` and surfaces anti-pattern violations. No-ops silently if CLI not installed. Requires one-time `npm install -g impeccable@3.6.0` (the version `install.sh` pins). |
| PostToolUse (test-integrity-guard) | Every Write/Edit/MultiEdit to a test file or CI/coverage config (`test_*`, `*_test.*`, `*.spec/.test.*`, `tests/`, `pytest.ini`, `pyproject.toml`, `.coveragerc`, jest/vitest config, CI YAML) | Soft gate (never blocks): flags "gradient descent to green" test weakening — added skip/xfail/`@Disabled` markers, tautological assertions (`assert True`), touched coverage thresholds (`--cov-fail-under`, `coverageThreshold`), or removed assertions. Asks Claude to confirm a deliberate spec change vs. a shortcut to pass. **No regex since 2026-09-03** — every check was a Python `re` pattern, which put the hook in violation of the repo-wide ban in `ruff.toml`, a ban TID251 could not enforce here because ruff only reads `.py` files and this Python lives in a shell heredoc. Detection is now literal-token membership plus `pathlib`. The tradeoff is deliberate: literal matching cannot express a word boundary, so `describe.skipBecause(` trips the `.skip(` probe — for a soft advisory that always exits 0, a rare extra line of output beats a pattern that silently matches the wrong span. Script: `.claude/hooks/test-integrity-guard.sh`; tests `hooks/claude/test-integrity-guard.test.sh`. |
| PostToolUse (ruff-quality-gate) | Every Write/Edit/MultiEdit to a `.py` file (soft gate, never blocks) | Runs `ruff check` on any Python file touched by Write/Edit/MultiEdit and surfaces findings back into context — closes the gap between `CLAUDE.md`'s Quality Gates claim ("`ruff check`: on every `.py` file write") and what was actually enforced: the pre-existing `Bash(ruff check *)` permission entry only ran if Claude remembered to invoke it. Silently no-ops if `ruff` isn't installed or the touched file isn't Python; output is a `⚠ ruff-quality-gate — <filename>` banner with raw findings, silent when clean. Wired into both `templates/settings.json.template` and `templates/settings.harness.json.template`. Script: `hooks/claude/ruff-quality-gate-hook.sh` → `.claude/hooks/ruff-quality-gate-hook.sh`. |
| PostToolUse (js-quality-gate) | Every Write/Edit/MultiEdit to a `.ts/.tsx/.mts/.cts/.js/.jsx/.mjs/.cjs` file (soft gate, never blocks) | The sibling of `ruff-quality-gate-hook.sh` for the other half of the languages the harness installs into: `ruff check` fired on every `.py` write while nothing at all fired on a TypeScript write, so agent-written TS reached the human unlinted in every project. Prefers `oxlint` (millisecond-scale, and the runner dmmulroy/anti-slop's low-evidence rule set uses), falls back to `eslint`, exits silently when neither is installed — safe on machines with no JS toolchain. Skips `.d.ts` and anything under `node_modules/`, `dist/`, `build/`, `.next/`, `coverage/`, `vendor/`; wall-clock guarded by `timeout`/`gtimeout` 20s when available. A clean file prints nothing (both linters emit a "0 problems" summary line, which is filtered). When a finding names an anti-slop rule (`no-chained-type-assertions`, `no-unknown-parameters`/`-returns`/`-type-aliases`, `no-unsafe-dictionary-type`, `no-known-value-widening`, `no-widen-then-assert`, `require-safety-comment-for-type-assertion`, `no-runtime-typeof`, `no-module-mocking`) it adds a callout: type evidence was thrown away, recover the real type rather than silence the rule. The rules themselves are vendored per repo (`npx skills add dmmulroy/anti-slop --skill install-anti-slop`); this hook is only the enforcement point. Registered on `PostToolUse Write\|Edit\|MultiEdit` in both templates. **`tailwind_design_token_check()` (2026-09-17)** runs independently of oxlint/eslint and is gated on a `tailwind.config.{js,ts,mjs,cjs}` file existing in the repo root — silent no-op on non-Tailwind projects, so it never fires as noise elsewhere. Regex-only (no lint plugin needed), covering the shadcn-ui/lint baseline: `no-raw-colors` (raw hex/rgb in a `className`), `no-arbitrary-values` (Tailwind arbitrary-value syntax like `w-[13px]`), `require-static-classes` (a `className` built from a template literal with interpolation, which breaks Tailwind's JIT static-class scanning). Advisory only; no settings-template change was needed since the hook was already registered. Script: `hooks/claude/js-quality-gate-hook.sh`; tests `hooks/claude/js-quality-gate-hook.test.sh` (21 cases, stubs the linter so the suite runs with no JS toolchain). |
| PreToolUse (cheap-model-delegation) | Every Bash `cat`/`head`/`tail` command that reads 6+ path-looking args in one call | Advisory only, never blocks. Suggests delegating bulk mechanical extraction (one fact/summary per file, no deep reasoning) to a haiku-tier subagent via the `cheap-model-delegation` skill instead of reading it all at the primary model's rate. Deliberately soft rather than a hard gate: bulk-read intent can't be reliably judged from the command line alone, unlike the existing `PreToolUse(Read)` hard gate for a single large file (`lean-ctx-nudge-hook.sh`). Registered on `PreToolUse Bash` in `templates/settings.json.template`; also wired into `templates/settings.harness.json.template` as of 2026-09-23 (initially excluded there). Script: `hooks/claude/cheap-model-delegation-hook.sh`. |
| PostToolUse (todo-focus) | Every `TodoWrite` call | Soft: enforces one `in_progress` todo at a time. `TodoWrite` accepts any number of concurrent `in_progress` entries and enforces nothing, and an agent that marks four items active starts four, splits attention, and finishes none cleanly — the single-active constraint is the load-bearing part of a todo tool. The hook names the competing items and asks for one to be picked; it does not undo the write, which has already happened. **Exits 2 deliberately**: `PostToolUse` stdout is never injected into context, and on exit 0 stderr goes only to the debug log, so an `echo` on exit 0 would be a hook that appears to work and does nothing. Exit 2 is the documented way to surface stderr to Claude from `PostToolUse` — the tool already ran, so it warns without blocking. Reads `.tool_input.todos[].status` with `jq` as structured data, never by matching free text. Re-checks `tool_name` in-script so a mis-scoped registration is inert rather than noisy. No-ops without `jq`. Opt out: `SDD_SKIP_TODO_FOCUS=1`. Script: `hooks/claude/todo-focus-hook.sh`; tests `hooks/claude/todo-focus-hook.test.sh` (15 cases). |
| SessionStart (headless-envelope) | Every session start, but **only** emits when `SDD_HEADLESS=1` | Injects a stricter operating envelope into unattended `claude --print` routine runs. The eight unattended entry points (seven `scripts/routines/*-runner.sh` plus `daily-orchestrator.sh`'s drift review) all invoke `SDD_HEADLESS=1 claude --print --permission-mode bypassPermissions`, so the least-supervised sessions in the harness ran with the *widest* permissions; until this hook, `SDD_HEADLESS` was only ever read to **suppress** interactive behaviour (`stop-hook.sh`, `caveman-savings-hook.sh`, `scripts/utils/dashboard.py`) and nothing anywhere read it to **tighten** behaviour. Six rules, stated to override anything looser in `CLAUDE.md`: one unit of work; no history-rewriting or publishing git (no push/reset --hard/rebase/--force/branch-or-tag deletion, commit only when the routine prompt says to); writes stay in the routine's own lane (reports and `.claude/memory/` always fair game, harness artifacts off-limits unless the prompt names that artifact class as its output); a two-strike loop guard that writes `ESCALATION:` into the report instead of a third attempt; honest reporting where partial completion is acceptable and fabrication is not; no new dependencies. Advisory by construction — `SessionStart` output becomes context and cannot block. Truly zero bytes in every interactive session. Opt out per-runner (never globally) with `SDD_SKIP_HEADLESS_ENVELOPE=1`. Script: `hooks/claude/headless-envelope-hook.sh`; tests `hooks/claude/headless-envelope-hook.test.sh` (18 cases covering gate, opt-out, rule presence, exit code, stdin drain). |
| SubagentStart (subagent-context) | Every subagent spawn, no matcher — fires **inside the child** | Injects the harness's load-bearing conventions directly into each spawned subagent via JSON `hookSpecificOutput.additionalContext`: ctx_* tool preference, Serena diagnostics after `.py` edits, a **BLAST RADIUS** block, the no-regex-on-free-text parsing rule, verification-evidence and faithful-reporting rules, change-size and Rule-of-Three scope limits, the do-not-commit-installed-output rule, and the reporting format. The blast-radius block names **one** check to run, in order — `mcp__serena__find_referencing_symbols` for a Python symbol (LSP-accurate, authoritative), `mcp__gitnexus__impact` for anything else, `ctx_callgraph(action="callers")` when the index errors or reports a version mismatch — and states that a broken index is an unknown answer, never "no callers". It previously listed the Serena and GitNexus calls as two independent bullets under TOOLS with no ordering, which invited running both or neither. The TOOLS block now says native Grep/Glob are policy-denied rather than restating the whole native→`ctx_*` mapping, and the scope bullet that used to be a second "blast radius" is now **change size**, so one phrase no longer means two things in the same injected text. `CLAUDE.md`, `.claude/rules/` and `SessionStart` output are all parent-thread only, so a subagent starts without them and re-derives or violates conventions the main thread already knows; before `SubagentStart` existed the only lever was to nudge the parent at `PreToolUse:Agent` and hope it briefed the child (`gbrain-agent-spawn.sh`) — a request, not a guarantee. Two constraints are load-bearing: plain stdout is **not** injected for this event (the `cat << 'RULES'` pattern every other hook here uses would be silently discarded), and the stdin read is bounded (`read -t 2`) with every failure path still injecting, because a hook that blocks on stdin stalls the spawn itself. Also appends a pointer to `.claude/memory/handoff/latest.md`, but only when that snapshot is <24h old — a stale pointer is worse than none. Kept short on purpose: the text is prepended to every spawn, so its cost is multiplied by spawn count. No-ops without `jq`. Opt out: `SDD_SKIP_SUBAGENT_CONTEXT=1`. Script: `hooks/claude/subagent-context-hook.sh`; tests `hooks/claude/subagent-context-hook.test.sh` (14 cases, including a FIFO stall case that fails if the hook ever blocks). |
| PreToolUse (gbrain-agent-spawn) | Every `Agent` tool call — addresses the **parent**, not the child | Writes a deterministic handoff snapshot of the main session (`scripts/session/write_handoff.py --trigger agent-spawn`) and prints the spawn-time decision rules: model tiers (`haiku` = classification/validation/expansion/dedup, `sonnet` = `claude-sonnet-5`, the default for generation/synthesis/tool loops, `opus` = `claude-opus-5`, deep reasoning and high-stakes judgment only — subagents use sonnet, since latency compounds in loops and opus buys little there), run mode, and what context to hand down. **Division of labour with `subagent-context-hook.sh`**: this hook can only *request* that the caller brief the child; `SubagentStart` is what guarantees the always-true conventions actually land inside it. The file previously asserted hooks *cannot* inject into a subagent's context — true of `PreToolUse:Agent`, which was all that existed when it was written, and made false by `SubagentStart` (verified 2026-08-30 against Claude Code 2.1.221: a probe subagent read the injected block back verbatim, `agent_type` included). Script: `hooks/claude/gbrain-agent-spawn.sh`. |
| spec-refactor (internal) | After each impl task's SELF-REVIEW step (Step 5) | Spawned by spec-tdd-impl-agent; reviews touched files, fixes issues, re-runs tests |
| PostToolUse (Jira comment) | Every `git push` Bash command | Posts Jira comment with branch/commits/docs summary if a `jira-solve` session is active |
| UserPromptSubmit (Jira capture) | Every user prompt | Captures ticket ID from `/kiro:jira-solve TICKET-ID` prompts, writes to `~/.claude/state/active_jira_ticket` |
| UserPromptSubmit (context priming) | 1st prompt of a session, then every `SDD_HOT_MEMORY_EVERY` (default 10), plus the first prompt after a compaction | Injects `.claude/memory/hot-memory.md` contents (wrapped in `--- Active Context ---` markers) so the agent primes on current state before responding. **Not every prompt** — hot-memory is ~2k tokens and each injection stays in the transcript, so injecting every time re-sends one more copy on every later API call; the post-compaction injection exists because the summary may have dropped it. Compactions are counted by scanning `transcript_path` for `"compact_boundary"`, and a change in that count resets the cadence. The per-session counter is `.claude/memory/.prompt-hook/<session_id>.json` (counters untouched for 7 days are pruned on each new session). Fails **open** — an unreadable event, a non-integer/`<1` `SDD_HOT_MEMORY_EVERY`, or a counter it cannot read or write all inject, with the reason on stderr, since a missed injection is invisible while an extra one only costs tokens. Fast (<1s); no-ops if hot-memory is missing or empty. Implemented in `.claude/hooks/prompt-hook.sh`; tests in `hooks/claude/prompt-hook.test.sh`. |
| SessionStart (settings.json self-heal) | Every Claude session start, before every other check in the hook | Runs `scripts/setup/repair-settings-json.py` (resolved from `$HOME/.sdd-harness-root`) against the current project whenever `.claude/settings.json` exists and `python3` is available. Claude Code parses that file as strict JSON and silently drops a malformed one whole — every permission rule and hook in it stops working with no in-session error — so repairing here caps the damage at one session instead of lasting until someone happens to run `update.sh`. Idempotent and cheap: valid files are read and left alone, and the `OK ` no-op line is filtered out so healthy repos print nothing. When a repair happens, prints `[SETTINGS-REPAIRED] <detail>` plus a note that Claude Code already read the old file, so the rules and hooks are inactive for **this** session and return at the next session start. Runs ahead of the memory-bootstrap gate because a broken settings file needs fixing whether or not the repo has memory yet. |
| SessionStart (maintenance check) | Every Claude session start | **macOS:** clears `com.apple.macl` xattrs from `.claude/hooks/` (Write/Edit tools set this attribute, blocking subprocess execution of edited hook files). Then two modes: (1) if no local `daily-runner.sh` is installed — checks if today's `[judge]` sentinel is absent from `observations.md` and asks Claude to run `/kiro:daily-maintenance`; (2) if `daily-runner.sh` is installed and stale (>24h or never ran) — fires it in the background via `nohup` silently, without consuming session context. Also checks if the per-repo CLAUDE.md review is >2 weeks stale (`.claude/memory/.last-claudemd-review`) and asks Claude to run `/claudemd-review` if so. Also checks for a `.claude/memory/.steering-bootstrap-pending` sentinel (dropped by `install.sh` for fresh installs with no steering files): if present and no steering `.md` files exist, injects `[STEERING-BOOTSTRAP-DUE]` prompting `/kiro:steering`. Claude removes the sentinel after steering completes. Finally, checks `.claude/memory/handoff/latest.md` — if present and <24h old (written by `scripts/session/write_handoff.py` from a prior compaction or subagent spawn), injects `[SESSION-HANDOFF-AVAILABLE]` prompting Claude to silently read it before responding to the user's first message. |
| Stop (memory health) | Every Claude session end | Nudges `/kiro:housekeeping` if observations >50; nudges `/kiro:evolve` if agent failure patterns detected |
| Stop (session signal detector) | Every Claude session end | Runs `scripts/session/detect_reexplanation.py` (Haiku LLM); appends `[memory-gap]` observation for drain signals (re-explanation) and `[session-charge]` for charge signals (approval). Each written at most once per day. |
| PostToolUse (action-capture) | Every Bash git-commit, test run, deploy, or failed command | Prompts memory capture after high-signal Bash actions (git-commit, test failures, deploys, struggle); auto-writes `[seed-target:]` observation on non-zero exit. Script: `.claude/hooks/action-capture.sh`. |
| UserPromptSubmit (doc-parse-nudge) | Every user prompt (keyword-gated) | Fires when prompt mentions document parsing or RAG pipeline building (PDF, DOCX, OCR, embed, ingest, vector store). Injects a reminder to invoke the `document-parsing` skill. When the nudge fires, also appends a `[doc-parse-nudge]` observation to `observations.md`. Exits <5ms on non-matching prompts. Script: `hooks/claude/doc-parse-nudge.sh`. |
| PostToolUse (revert detector) | Every git revert/reset/restore Bash call | Immediately appends `[revert]` drain observation to `observations.md` — gives trust-battery Judge concrete evidence. Script: `.claude/hooks/revert-detect-hook.sh`. |
| PostToolUseFailure (tool-failure capture) | Every failing Bash/MCP tool call | Records the failure into a per-repo ledger `.claude/memory/tool-failures.jsonl`, keyed by a normalized command signature so the same failure shape clusters and its `count` climbs. Capture half of the tool-failure-memory loop. Script: `.claude/hooks/tool-failure-capture.sh`; see the `tool-failure-memory` skill. |
| PreToolUse (tool-failure recall) | Every Bash/MCP tool call | Soft advisory (never blocks): if this command shape has failed ≥2× and is still open, injects the failure count, last error, and any recorded remedy so Claude reconsiders before repeating it. Once-per-session-per-signature dedupe + 45-day recency gate. Script: `.claude/hooks/tool-failure-recall.sh`. |
| daily-orchestrator (fleet harness sync) | Once per calendar day, harness-level, **before** the per-repo runners (gated by `$SDD_HARNESS/.last-harness-sync` using a portable day-string compare, not GNU-only `date -d`) | Runs `$HARNESS_DIR/update.sh` so every registered project picks up harness changes with no human step. Nothing else ever ran `update.sh` — `stop-hook.sh` only prints a `Run: update.sh` nudge and then waits for a human, so a harness fix could sit unapplied in an installed project indefinitely (this is how a `settings.json` broken by an old template survived for months in an installed repo). `bash -n update.sh` must pass first, so a half-written `update.sh` is never run across the whole fleet; a parse failure is logged to `logs/orchestrator.log` and `logs/orchestrator-errors.log` and the sync is skipped. `--repo <path>` is forwarded to `update.sh`; `--dry-run` prints a `[would-sync]` line only. The state file is written only on exit 0, so a failed sync retries the next run instead of consuming the day. Opt out with `SDD_SKIP_HARNESS_SYNC=1`. |
| daily-orchestrator (tool-failure review) | Once per day per repo via the daily orchestrator (self-paces to ~2×/week via `MIN_GAP_DAYS=3`; no-ops unless a promotable ledger entry exists) | Runs `.claude/scripts/tool-failure-review-runner.sh`, which invokes `/kiro:tool-failure-review` headlessly: diagnoses recurring failures (`count ≥ 3`, open, unpromoted) and promotes the understood, reusable ones into memory files + `ERRORS.md`, then marks them resolved on the ledger. Review (promotion) half of the tool-failure-memory loop. Opt out with `SDD_SKIP_TOOL_FAILURE_REVIEW=1`. |
| daily-orchestrator (code-review learning) | Once per day per repo via the daily orchestrator (self-paces to weekly via `CODE_REVIEW_LEARNING_GAP_DAYS=7`; no-ops unless a merged PR has a pr-babysit review log not yet processed) | Runs `.claude/scripts/routines/code-review-learning-runner.sh`: diffs pr-babysit's logged review (`.claude/memory/pr-reviews/pr-<n>.md`) against real human review activity (`gh api` comments/reviews) on merged PRs. Low-risk findings (conventions, dismissed-flag patterns) are promoted straight into memory; higher-risk methodology changes are only reported to `docs/code-review-learning-report.md` for human approval. Opt out with `SDD_SKIP_CODE_REVIEW_LEARNING=1`. |
| daily-orchestrator (security report) | Once per day per repo via the daily orchestrator (self-paces to daily via `MIN_GAP_DAYS=1`; applies to every repo) | Runs `.claude/scripts/routines/security-report-runner.sh`: static security scan of recent git changes using the `ai-security-workflow` skill — checks for OWASP patterns, secrets, injection sinks. Writes `.claude/reports/security/<date>-security-report.md`. Visible in the dashboard Scheduled Tasks section. Retries automatically on failure — the state file is only written after a successful run (exit 0), so a failed scan doesn't consume the gap-days window; stdout is also tee'd to `.claude/memory/.last-security-report-output.log` since the orchestrator wrapper redirects stdout to `/dev/null` and only captures stderr. Opt out with `SDD_SKIP_SECURITY_REPORT=1`. |
| daily-orchestrator (startup payload audit) | Once per day per repo via the daily orchestrator (deterministic, no LLM; self-paces to daily via its own state-file guard) | Runs `.claude/scripts/routines/startup-payload-audit.sh`: audits the fixed per-session startup token tax (`CLAUDE.md` + `@imports` + `.claude/rules/*` + auto-loaded `MEMORY.md`). Writes `.claude/reports/context/startup-payload.json`, read by the dashboard's Context Health tab. Keeps a per-repo ceiling that only goes down; growth above it is flagged `over_ceiling` (accept with `--rebaseline`). Wired into `daily-orchestrator.sh` `run_one()`. Opt out with `SDD_SKIP_STARTUP_AUDIT=1`. |
| daily-orchestrator (RTK net effect) | Once per day per repo via the daily orchestrator (deterministic, no LLM; self-paces to daily via its own state-file guard) | Runs `.claude/scripts/routines/rtk-net-effect-runner.sh`, a wrapper around `.claude/scripts/utils/rtk-net-effect.py`: measures RTK's **global** effect, not just local savings — exact-match Bash rerun rate and Read reread rate within the same session, from `~/.claude/projects/**/*.jsonl`. Writes `.claude/memory/rtk-net-effect.json`; on no data in the lookback window (`SDD_RTK_NET_EFFECT_DAYS`, default 30) it leaves the prior snapshot in place rather than overwriting it empty. Read by the dashboard's RTK layer note (Headroom tab), which shows both rates alongside its savings figure instead of savings alone — not a Scheduled Tasks tab card. Wired into `daily-orchestrator.sh` `run_one()`. Opt out with `SDD_SKIP_RTK_NET_EFFECT=1`. |
| daily-orchestrator (hook/MCP-config audit) | Once per day per repo via the daily orchestrator (deterministic, no LLM; self-paces to weekly via `HOOK_CONFIG_AUDIT_GAP_DAYS`, default 7) | Runs `.claude/scripts/routines/hook-config-audit-runner.sh`, a wrapper around `.claude/scripts/utils/hook-config-audit.py`: sweeps the repo's own `.claude/hooks/` and `settings.json`/`settings.local.json`/`.mcp.json` — the harness's own hooks and MCP config, not user content — for secrets (shells out to `scan-pii.sh`'s OPF engine per file), network-exfil patterns (`curl`/`wget` calls in hook scripts to a host outside a small allowlist), and over-broad permission grants (a `permissions.allow`/`deny` entry with no scoping argument, or an argument that is just `*`). Neither `scan-pii.sh` (content-only) nor `skill-permissions-gate.sh` (fires only on new `SKILL.md` writes) re-sweeps existing hooks/config as the hook count grows. Writes `.claude/reports/security/hook-config-audit.json`. Wired into `daily-orchestrator.sh` `run_one()`. Opt out with `SDD_SKIP_HOOK_CONFIG_AUDIT=1`. |
| daily-orchestrator (fleet registration check) | Every fleet run, after the per-repo loop (skipped under `--dry-run`) | Runs `scripts/utils/check-fleet-registration.sh --quiet`: finds repos that have the harness installed (`.claude/` present) but are missing from `projects.txt`. Such a repo gets zero routines and appears nowhere, because the dashboard only renders repos it is told about — absence is invisible unless something looks for it. Never fatal: it is a report, not a gate. A finding writes `orchestrator: unregistered harness repo(s) found` to `logs/orchestrator.log` and the detail to `logs/orchestrator-errors.log`. The same script is a step in `/kiro:harness-validate`. |
| OS Scheduler + SessionStart (daily maintenance) | 18:00 local, repeating every 4h (6x/day) on WSL/Windows (`setup-global-orchestrator.sh`); once daily on macOS/Linux; SessionStart catch-up if >24h stale | Runs per-repo `daily-runner.sh` → `/kiro:daily-maintenance` — Judge → Reflect → Housekeeping → Session Quality → Keep Rate → Trust Score → Augment Skills → Adversarial Check. The orchestrator skips `daily-runner.sh` if it already ran today (dedup via state-file date check), so the SessionStart catch-up never double-fires. Auto-registered by `install.sh` / `update.sh`: Windows Task Scheduler on WSL (`setup-global-orchestrator.sh`), cron on Linux (`setup-linux-orchestrator.sh`), launchd on macOS (`setup-mac-orchestrator.sh`). Each setup script **preflights** that the orchestrator can actually execute under its scheduler rather than trusting that registration succeeded — `launchctl load` returning 0 only means the job was registered, and with the harness under `~/Documents` launchd (holding no Full Disk Access) was refused at exec time with `Operation not permitted`, exiting 126 daily for four days behind a green install. macOS registers a throwaway probe LaunchAgent that runs `--dry-run` in the same launchd context; Linux runs `--dry-run` under an approximated cron environment (`env -i`, minimal PATH); both **exit 1** on failure, and the macOS path names TCC explicitly when `$HARNESS_DIR` is under `~/Documents`, `~/Desktop` or `~/Downloads`, offering both fixes (move the harness, or grant Full Disk Access to `/bin/bash`). The macOS LaunchAgent's `ProgramArguments` now wraps the orchestrator in **`caffeinate -i`** (`/bin/bash -lc "caffeinate -i <orchestrator>"`), so the unattended 18:00 fire is not cut short partway through by idle or display sleep — a laptop that dozes mid-run otherwise kills the routines still queued behind the one in flight, which reads afterwards as a partial run rather than a failure. WSL/Windows preflights through `wsl.exe -d <distro> -- bash -lc` but only **warns**, since the matching `schtasks /Run` + `LastTaskResult` check could not be tested. The preflight also runs on the "already registered / already loaded" path — that is exactly the state a silently-dead job reports. See `docs/scheduled-tasks/README.md` → "Preflight — registration is not execution". Opt out: `SDD_SKIP_ROUTINE=1` at install time; `schtasks.exe /Delete /TN "SDD Daily Orchestrator"` (Windows); `crontab -l | grep -vF sdd-daily-orchestrator | crontab -` (Linux); `launchctl unload ~/Library/LaunchAgents/com.sdd.daily-orchestrator.plist` (macOS); `rm .claude/scripts/orchestration/daily-runner.sh` per-repo. `daily-runner.sh` recovers from stale locks (removes a lock dir older than 2h, left by a SIGKILL'd run) and uses a single `EXIT` trap to release the lock (bash fires `EXIT` on `INT`/`TERM` too, so one trap covers all exit paths). Each routine's stderr is captured to a per-run buffer and appended to `logs/orchestrator-errors.log` only on non-zero exit (stdout still goes to `/dev/null`), so a failing routine leaves a diagnosable trace instead of a false-looking `exit=0` line in `logs/orchestrator.log`. `daily-orchestrator.sh` itself (the fleet-level dispatcher, one level above `daily-runner.sh`) now logs a start line to `logs/orchestrator.log` on every real invocation and, via an `EXIT` trap, a matching finish line with the exit code and repo count — plus an arg-validation error line to `logs/orchestrator-errors.log` for bad flags (`--repo` with no path, unknown args) — so a crash before the repo loop starts (bad env, `resolve-harness-dir.sh` failure, missing `projects.txt`) is structurally distinguishable from a zero-work success instead of leaving no trace at all. `daily-orchestrator.sh` sources `.claude/scripts/lib/env-detect.sh`, which detects host OS/WSL and classifies a registered repo path as `cross-fs` when it's under `/mnt/*` on WSL (the one confirmed real perf risk on that setup); cross-fs repos surface as a `WARNING` line in `logs/orchestrator.log` (no auto-reroute). The harness-level drift review it also runs is gated on elapsed days since the last **successful** run (`DRIFT_REVIEW_GAP_DAYS`, default 7) rather than a fixed day-of-week, so a machine asleep through Wednesday's trigger window no longer silently loses the week — see `docs/scheduled-tasks/README.md` → "Drift Review". That elapsed-days math now runs through `python3` (`datetime.date.fromisoformat`) instead of `date -d`, which is GNU-only: on macOS the epoch lookup always failed, the gate was skipped entirely, and the "weekly" review spawned a full `claude --print` session on every single orchestrator run. If `~/.env.channels` exists, it posts a 20-line tail summary of the run to chat channels via `.claude/scripts/integrations/channels/notify.py` (opt out with `SDD_SKIP_CHANNEL_NOTIFY=1`; no-ops silently when the env file or notifier is absent). See `SDD-USAGE.md` → "Daily Maintenance". |
| UserPromptSubmit (frontend-security-nudge) | Every user prompt (keyword-gated) | Fires when prompt contains build intent (`build a`, `create a`, `implement a`, `scaffold`, etc.) AND a frontend/UI keyword (React, Vue, Svelte, CSS, component, form, modal, etc.). Injects a reminder to invoke `secure-agent-design` before writing the first file. When the nudge fires, also appends a `[frontend-security-nudge]` observation to `observations.md`. Exits <5ms on non-matching prompts — zero overhead for non-frontend work. Script: `hooks/claude/frontend-security-nudge.sh`. |
| PreToolUse (prompt-quality-check) | Every `Agent` tool call | Scores the agent prompt against 6 PQ dimensions (context provision, request specificity, scope management, information timing, correction quality, overall) using fast Python heuristics — no LLM required. Outputs a scored report to Claude's context and appends a JSON entry to `~/.code-insights/pq-log.jsonl`. Scores < 3.5 surface improvement tips per dimension. Scores ≥ 4.0 confirm the prompt is ready. **`detect_anti_patterns()` (2026-09-17)** adds a second, separate layer — named prompt anti-patterns flagged as presence/absence findings rather than a 1-5 spectrum score: `stale-few-shot` (2+ example blocks — verify they still match the codebase), `mandatory-scratchpad` (forced "think step by step in a scratchpad" ritual), `maximally-thorough-phrasing` (vague intensifiers like "leave no stone unturned" instead of naming the actual completeness criterion), `verification-ritual` (3+ repeated verify/check phrasings instead of stating the one concrete check that matters), plus (2026-09-30) `think-instruction` ("think carefully / step by step" — the model always reasons) and `show-reasoning-request` ("show your reasoning" — can be declined). Printed under a "🚩 Anti-patterns:" line in the report and logged into the `anti_patterns` field of the `pq-log.jsonl` entry. Matching is literal token matching, no regex. Tests: `hooks/claude/prompt-quality-check.test.sh`. Script: `hooks/claude/prompt-quality-check.sh`. Dashboard: Session Quality → Prompt Quality (✨) sub-tab. |
| PostToolUse (skill-usage-tracker) | Every `Skill` tool invocation | Appends one `{"ts","skill"}` line to `logs/skill-usage.jsonl` — the evidence layer for skill deprecation (replaces guessing from file mtime). Consumed by the dashboard **Skill Changes → Skill Usage** panel (total/30d invocations, hot skills, cold-skill count where cold = no invocation in 30d) and the weekly skill-curator routine's Phase 1.5 Usage Evidence audit. No-ops if the log dir is unavailable. Script: `.claude/hooks/skill-usage-tracker.sh`. |
| Dashboard button (skill-curator controls) | Click **Analyze & Propose** / **Apply Approved** in the dashboard's **Skill Changes** panel (live/companion dashboard only — `python3 scripts/utils/dashboard.py`, not the `--static` export) | **Analyze & Propose** (`POST /api/skill-curator-propose`) spawns a detached headless `claude --print --permission-mode bypassPermissions` session that runs the `skill-curator` skill's Phases 1–4 against `reports/skill-curation-report.md` (rationale capped to 1–2 sentences per item), now including the report's `## Dependency Flags` section (Phase 1.6 — deletion/archive candidates cross-referenced by another skill, hook, agent, or command via `scripts/utils/skill-dependency-scan.sh`), and writes the numbered proposal to `.claude/memory/.skill-curator-proposal.md` instead of printing it to chat. The panel polls `GET /api/skill-curator-proposal` every 3s (up to ~40 tries, ~2 min) until the file appears, then renders it inline with an **Apply Approved** button, a **Re-analyze** button, and a free-text instruction box (default `apply all`). **Apply Approved** (`POST /api/skill-curator-apply?instruction=...`) first tars `~/.claude/skills/` to `.dashboard/skill-backups/skills-<timestamp>.tar.gz`, then spawns a second headless session that executes only the approved subset per Phases 5–6, appends the curation log entry to `reports/skill-curation-report.md`, and deletes the proposal file so it can't be re-applied. Both runs log for real — a timestamped header (prompt/instruction, backup path) plus the subprocess's full stdout/stderr go to `logs/skill-curator-propose.log` / `logs/skill-curator-apply.log`. Implemented in `scripts/utils/dashboard.py` (`_run_skill_curator_propose`, `_run_skill_curator_apply`, `_backup_skills_dir`, `read_skill_proposal`). |
| Dashboard (Herder tab — spawn and supervise sessions) | **Spawn** from the form, then talk to each agent in its card's **chat box** (reply field, Enter to send, Shift+Enter for a newline, 📎 to attach `@filename` chips) or **Stop** it (live/companion dashboard only). The chat re-renders on the `live tail (5s)` poll and on send. The old `window.prompt` popup and the `prompt`/`activity`/`refresh`/`raw pane` buttons are gone; a collapsed `raw pane` disclosure sits under the chat | Spawns and supervises real interactive Claude Code sessions via `scripts/utils/herder.py`, backed by [Herdr](https://herdr.dev) (`herdr server` is a headless daemon needing no TTY, and every `herdr workspace\|tab\|pane\|agent` subcommand answers with JSON on stdout, so nothing pattern-matches text). Chosen over the `subprocess.Popen(["claude","--print",…])` primitive the skill-curator endpoints use because `--print` is a one-shot pipe with nothing to attach to: a Herdr session outlives the dashboard process, reports pollable lifecycle state (`idle`/`working`/`blocked`/`done`), and can be joined from a terminal mid-run with `herdr agent attach <name>`. **Nothing about the agent is hardcoded** — permission modes are discovered by handing the agent's own CLI an invalid value so commander.js lists the valid set during argument parsing (nothing executes, no session starts; found 6 modes for `claude` where the previous hardcoded list had 4), and model ids come from `.dashboard/models-pricing-history.json` filtered by a kind→provider map, with family aliases derived from the ids present. Both carry `discovered: true\|false` and a `source`, and the UI says plainly when a list is a fallback. Each spawn writes a ledger entry to `.dashboard/herder/<name>.json` and tags the session `--name herder:<label>`, which lands in the transcript as `agentName`/`customTitle` and is read as a field — resolution is lazy, because at spawn time the session has usually not written its first line. Two earlier heuristics failed in production and are documented as such: "newest transcript modified after the spawn" credited a fresh agent with 94M tokens belonging to the session that spawned it, and "newest transcript absent before the spawn" made three agents started seconds apart each claim a sibling's transcript. A stored id is therefore **revalidated against the transcript's own tag on every read**. `agent_spend(name)` returns **None**, not 0, when the transcript cannot be located. A pane inherits `CLAUDE_CODE_CHILD_SESSION`, which turns transcript saving off and would make every herder-spawned session invisible to `token-forensics.py` and `session-judge` — scrubbed in the server env and per-workspace via `--env`. The HTTP endpoints (`/api/herder-status`, `-list`, `-options`, `-stream`, `-read`, `-spawn`, `-stop`, `-prompt`) are guarded by **two independent checks**, since either alone is bypassable: a per-process `X-Herder-Token` only a page served by this process has, plus an `Origin` allowlist so a page that somehow learned the token still cannot drive it from another site (an absent `Origin` is allowed, a present-but-foreign one is not). The chat box itself (`_hdBox`, `herderChatOpen`, `herderUpdateChat`, `herderSendReply`, `herderFileTag`/`herderRemoveFile`, `herderInputKeydown` in `scripts/utils/dashboard.py`) is a **rendering of `/api/herder-stream`, not a message store**: it keeps no local history, and it draws only the stream's `t === 'text'` events, so the pane shows the agent's side of the conversation — your own sent prompt is not echoed back into the feed. Sending clears the input and the file chips, then refreshes immediately and again after 1s, because the reply usually has not landed in the stream yet at `POST /api/herder-prompt` return. The `live tail (5s)` checkbox drives each visible card's chat refresh (previously the activity feed), and auto-scroll is conditional — the pane only jumps to the bottom if you were already within 40px of it, so reading back through history is not yanked away by the next poll. Attached files are a **prompt convention, not an upload**: the 📎 chips are appended to the outgoing text as a trailing `Files: @a @b` line for the agent to resolve itself. Two functions from the previous feed UI, `herderStream` and `herderRawPane`, survive in the file with no callers, and the `raw pane` disclosure's `<pre id="hdlog-…">` is consequently never populated — opening the disclosure only re-renders the chat. Known limitation: spawning into a repo Claude Code has never been opened in fails with `agent_not_ready` at the first-run trust prompt — open that repo by hand once. The reply field also **completes `/skill` and `@path` the way Claude Code's own input does**: `/` at the start of any word offers skills and commands, `@` offers repo paths, both ranked by a fuzzy subsequence match with shallow/short names first (so `@read` offers `README.md` before `some/deep/README.md`). The candidate lists are read off disk in the target repo — `list_invocables()` walks the repo's and the user's `skills/`+`commands/` plus claude.ai-synced plugin roots (`synced/<org>/<plugin>/{skills,commands}`) and pulls each one's `description:` out of frontmatter; `list_repo_paths()` walks tracked paths and honours `.gitignore`. Nothing is hardcoded in a list, and both are cached briefly so a burst of keystrokes is one disk walk. While the list is open, Enter accepts the highlighted entry instead of sending. Tests: `scripts/utils/herder.test.sh` (34 offline cases, 6 of them completion; `HERDER_LIVE=1` adds a real spawn/stop). |
| Dashboard (scheduler alarm banner) | Every render of the dashboard's **Scheduled Tasks** tab | A scheduler that is not installed, or whose last launch exited non-zero, now renders a full-width red banner **above** the routine cards (`_scheduler_banner()` in `scripts/utils/dashboard.py`), not a small yellow line inside the scheduler card. The banner states plainly that no routine below can run and that everything under it is stale regardless of its badge — the failure this exists for is a dead scheduler presenting as a page full of calm "PENDING" badges, which is how a fleet that had executed nothing for four days looked merely idle. launchd reports exit codes shifted left 8 bits (`32256` = exit 126), so the banner un-shifts before displaying, and on exit 126 it names the likely cause outright: the OS refused to execute the orchestrator, normally Full Disk Access when the harness sits under `~/Documents`, `~/Desktop` or `~/Downloads`. Footer points at `logs/orchestrator.stderr.log`. |
| PreToolUse (raindrop-best-practices) | Every `mcp__raindrop__` tool call | Injects five active-observability patterns before any Raindrop Workshop MCP call: batch facets (multiple dimensions → one LLM call), facet-first summarization before clustering, 128K token cap on input, no-LLM nearest-summary classification, and long-tail sampling with HDBSCAN. Reduces naïve trace analysis cost by ~80–90%. Script: `hooks/claude/raindrop-best-practices.sh`. |
| PreToolUse (rtk) | Every Bash tool call by any agent | `rtk hook claude` rewrites matching commands to `rtk <cmd>`, compressing output before it reaches the LLM (60–90%+ token reduction). Emits `permissionDecision: "allow"` so rewrites are silent. Passes through commands without filters unchanged. Global — fires in all sessions and projects. |
| PreToolUse (GitNexus) | Every file Read/Edit by any agent | Enriches file operations with 360° symbol graph context (callers, dependencies, process participation); no-ops gracefully when GitNexus is not installed |
| PreToolUse (memory-discipline) | Every Write/Edit to `*/memory/*.md` or `MEMORY.md` | Gates memory writes with discipline rules — valid content: workflow patterns, user preferences, reusable lessons. Invalid: case-specific facts, citations, investigation outcomes. Claude sees the rules before executing the write and can revise content. Implemented in `.claude/hooks/memory-discipline-hook.sh`. |
| PreToolUse (protected path) | Every Write/Edit to a sensitive path (`.env`, crypto keys, credentials, `.aws/`, `.ssh/`) | Injects a confirmation banner; Claude must pause and ask the user before proceeding. Prevents accidental overwrites of secrets files. Implemented in `.claude/hooks/protected-path-hook.sh`. |
| PreToolUse (ledger-append-only) | Every Write/Edit/MultiEdit against a self-scored measurement ledger (`.claude/memory/trust-score.jsonl`, `metrics.jsonl`, `caveman-savings.jsonl`, `learnings.jsonl`, `observations.md`) | Hard block (exit 2) — a self-scored metric an agent can also edit isn't a measurement. Modeled on exo (an autonomous-agent harness)'s one safety invariant: the agent cannot alter its own canonical event log. Does not touch real producers, which all append via `>>`/`echo` from a Bash-run hook script — a different tool this hook's `Write\|Edit\|MultiEdit` matcher never sees; what it blocks is Claude's own Write/Edit/MultiEdit tool calls against these exact files. Literal suffix match only, per the repo-wide no-regex ban. Escape hatch: `SDD_LEDGER_ROTATE=1`, set by the housekeeping runner (never an ordinary agent turn), disables the block for `housekeeping-agent`/`/kiro:housekeeping`'s legitimate pruning/archival passes — an env gate on purpose, not a soft warn, since a warn can be talked past in the same turn that is trying to rewrite the ledger. **Fail-closed on its own failures (2026-10-01):** a missing `python3` or a malformed event used to collapse to an empty path and `exit 0`, so a broken interpreter silently disabled the block. The parse now exits non-zero and the hook blocks whenever the raw event names a protected ledger (literal substring), allowing everything else so one broken dependency does not block every Write. Implemented in `hooks/claude/ledger-append-only.sh` → `.claude/hooks/ledger-append-only.sh`; tests in `hooks/claude/ledger-append-only.test.sh` (17 cases, five of them fail-closed). |
| PreToolUse (risk-zone edit gate) | Every Write/Edit/MultiEdit to a file listed in `.claude/steering/risk-zones.md` | Soft gate (never blocks): looks up the file's zone (`red`/`yellow`/`green`, seeded weekly from git churn + test-file presence + `gitnexus impact`) and, for `red`/`yellow`, banners the signal and asks Claude to check for tests / run `gitnexus impact` first. Silent for `green` or unlisted files. Implemented in `.claude/hooks/risk-zone-edit-gate-hook.sh`. See the `risk-zone-engine` skill. |
| PreToolUse (git-destructive-guard) | Every Bash tool call | Hard block (exit 2, refuses the tool call — not a soft nudge). Blocks force-push variants (`--force`, `--force-with-lease`, `--force-if-includes`, `-f`, short bundles like `-fu`), remote branch deletion (`--delete`/`-d`, `--mirror`, empty-refspec `git push origin :branch`), local force branch delete (`git branch -D`), `gh repo delete`, and `git rebase` (rewrites shared history the same as a force-push; add a follow-up commit or a fresh branch instead). **Matching (rewritten 2026-08-25):** the command is tokenized with `shlex`, split on shell operators, and compared token-by-token against exact flag names — it recurses into `bash -c '...'`, resolves `git -C`/`git -c` prefixes to the real verb, strips `VAR=value` prefixes, and rejects `git -c alias.*`. The prior implementation regex-stripped quotes then grepped raw text, which `F=--force; git push $F`, `bash -c 'git push --force'`, `git push --fo""rce`, and `cd sub && git push --force` all defeated. Fails closed on unparseable input or an unresolved `$VAR`/`$(...)` in a destructive verb. Quoted commit messages mentioning "force" still pass (the verb resolves to `commit`). Built because the declarative allow/deny list alone was observed to not reliably block `git push --force` in some sessions. **Fail-closed on its own failures (2026-10-01):** both parse steps swallowed their errors into an empty string and fell through to `exit 0`, so a missing `python3`, a malformed event, or an analyzer crash silently disabled the harness's only hard block on destructive git. Those failures now block any event that mentions `git` or `gh ` and allow the rest. Implemented in `.claude/hooks/git-destructive-guard-hook.sh`; tests in `hooks/claude/git-destructive-guard-hook.test.sh` (52 cases, six of them fail-closed). |
| PreToolUse (agent-behavior-guard) | Every `Read`, `Bash`, `WebFetch`, `WebSearch` call (matcher `Read\|Bash\|WebFetch\|WebSearch`) | **Monitor-only by default — never blocks unless enforced.** Three detections no per-event hook reaches, ported down from perplexityai/numbat's rule engine (no rule files, no versioning, no signed bundles): `network_indicator` (a command or fetch target naming a cloud-metadata SSRF endpoint — `169.254.169.254`, `metadata.google.internal`, `metadata.azure.com`), `persistence` (a Bash write to crontab, a shell rc file, `~/.ssh/authorized_keys`, or a systemd/launchd unit), and `chained_secret_egress` (a secret-bearing path — `.env`, `.pem`/`.key`, `.aws/credentials`, `.ssh/` — read via `Read` or Bash, then an egress call *later in the same session*, correlated through a per-session ledger). `protected-path-hook.sh` cannot cover these: it fires only on `Write\|Edit` and is stateless per call, so it never sees `Read`, Bash egress, or a pattern spanning tool calls. Default mode logs the finding and warns on stderr at exit 0; set `SDD_AGENT_GUARD_ENFORCE` to a comma-separated rule list or `all` to make matching rules hard-block (exit 2) — numbat's monitor→enforce promotion without its machinery. **Enforce mode fixed 2026-10-01:** the script ended in a bare `exit 0` that discarded the Python's exit code, so enforce mode wrote `"mode": "enforce"` findings and then allowed the call; the Python's exit code is now the verdict, and with a rule enforced the guard fails closed on its own failures (malformed event, missing `python3`, crash) while monitor mode stays silent. Findings append to `.claude/memory/agent-security-findings.jsonl`; secret-access events live in `.claude/memory/.agent-behavior-guard-secret-access.jsonl`, keyed by `session_id`. Registered in both `templates/settings.json.template` and `templates/settings.harness.json.template`. Implemented in `hooks/claude/agent-behavior-guard.sh` → `.claude/hooks/agent-behavior-guard.sh`; tests in `hooks/claude/agent-behavior-guard.test.sh` (13 cases, four of them fail-closed/monitor, run from a throwaway cwd). |
| PreToolUse (agent-commit-attribution) | Every Bash tool call | Soft advisory (prints to stdout, exit 0, never blocks): warns when a `git commit` supplies an inline message (`-m`, `--message=`, `-mmsg`, `-am`) that carries no `Co-Authored-By` trailer, and names the trailer to append. Not a style nag — `skills/keep-rate` and the keep-rate dashboard widget both select agent commits via `git log --grep="Co-Authored-By: Claude"`, so an untrailered agent commit silently leaves the denominator and inflates the reported keep rate. A `.git/hooks/commit-msg` hook cannot do this: it sees a commit, not an author, whereas a PreToolUse hook knows Claude issued the command. Silent on `--amend --no-edit`, `--squash`, `--fixup`, `-C`/`-c` reuse forms, and editor-driven commits (message not visible). Implemented in `.claude/hooks/agent-commit-attribution-hook.sh`; tests in `hooks/claude/agent-commit-attribution-hook.test.sh` (22 cases). |
| PreToolUse (pr-evidence) | Every Bash tool call; acts only on `gh pr create` | Soft advisory (prints to stdout, exit 0, never blocks): nudges when the PR body carries no literal `## Evidence` section. `verification-before-completion` already demands evidence for claims made in conversation, but that evidence evaporates at the PR boundary — the reviewer receives a description and nothing to check it against. Deliberately soft: not every PR has a visible surface, and a hard block would force fabricated evidence on docs-only PRs, which is worse than none. The command is tokenized with `shlex` and `gh pr create` matched token-by-token, per the lesson in `git-destructive-guard-hook.sh` — a guard that matches the rendered string can be defeated by re-rendering it. The `## Evidence` marker itself is a plain substring test inside the body value, which is correct here: it is a fixed structural token with no adversary, and the failure mode is a missed nudge rather than a bypassed block. Does **not** cover PRs opened by `scripts/pr/detect_base_and_create.sh` — that `gh pr create` runs inside a script, not as a Bash tool call, so no `PreToolUse` event fires; that path writes its own placeholder section instead. Implemented in `.claude/hooks/pr-evidence-hook.sh`; tests `hooks/claude/pr-evidence-hook.test.sh` (36 cases, asserting on emitted text rather than exit codes, since a soft gate's exit code is constant). |
| PreToolUse (skill-validate) | Every Write to a `SKILL.md`; the frontmatter rules apply under `~/.claude/skills/<name>/` and the provenance scan applies anywhere | Validates skill frontmatter before writing: `name:` must be kebab-case and match the file path slug; `description:` must exist and be ≥25 chars; warns on vague description starters. Exit 2 hard-blocks on errors. **Provenance scan (2026-09-03):** a skill that tells an agent to load its instructions from a URL is a supply-chain vector — the remote file can change after review, and no frontmatter check can see that. A line carrying an `http(s)` URL ending in `.md`/`.txt`/`.json`/`.yaml`/`.yml` next to an adopt verb (`read`, `fetch`, `load`, `follow`, `install`, `set up`) warns to vendor the content or pin a commit/tag; a line carrying a remote-install verb (`curl`, `wget`, `\| sh`, `\| bash`, `npx`) warns that the skill records neither publisher nor reviewed version. These are **warnings, not errors**, and they are emitted even for a `SKILL.md` written outside the skills directory, where the frontmatter rules do not apply. Substring tests only — a hand-rolled URL matcher is exactly the almost-right parser the repo-wide regex ban exists to prevent. Implemented in `.claude/hooks/skill-validate-hook.sh`; tests `hooks/claude/skill-validate-hook.test.sh`. |
| PreCompact (compaction-discipline) | Every context compaction | Injects boundary-timing principle and state-preservation checklist: compact at workflow phase boundaries (not arbitrary turn counts), preserve artifact paths, cited facts, open questions, and decisions. Use anchored iterative summarization. Adds concrete, checkable fidelity requirements: mark every user question answered/partial/unanswered (with a "Pending Questions" subheading listing the rest verbatim); keep confirmed root causes (with file:line) separate from ruled-out hypotheses; group files into critical/referenced/mentioned tiers rather than a flat list; treat subagent/Task tool results as primary evidence to preserve in full, not compressible chatter; keep both sides plus the decision criteria of any A-vs-B comparison the user weighed. Also fires `scripts/session/write_handoff.py --trigger precompact`, writing a deterministic (non-LLM) snapshot of the session to `.claude/memory/handoff/latest.md` so working state survives independent of the in-context summary. Implemented in `.claude/hooks/compaction-discipline-hook.sh`. |
| PostToolUse (pr-auto-create) | Every successful non-force `git push` Bash command | Calls `scripts/pr/detect_base_and_create.sh`. If `.git/gh-stack` exists (a `stacking-pull-requests` stack is already active for this branch, initiated by `smart_commit.sh`), runs `gh stack submit --auto` to submit/update every layer's PR instead of bundling everything into one — covers a manual `git push` that bypassed `smart_commit.sh`'s own submit call. Otherwise, auto-detects the branch's true fork-point base (via `git merge-base` across all local/remote refs) and opens a single draft PR (`gh pr create --fill --draft`) if one isn't already open. Bails silently on force pushes, push failures, or if `gh` is missing. Implemented in `.claude/hooks/pr-auto-create-hook.sh`. Two changes in the shared script (2026-09-03): the PR number is now read back with `gh pr list --head <branch> --json number --jq '.[0].number'` instead of being grepped off the end of the printed URL — the URL is free text, the number is a field, same principle as the repo-wide regex ban — and the script **writes its own `## Evidence` placeholder** into the new PR body via `gh api -X PATCH`, stating that evidence was not captured because the PR was opened automatically on push before any before/after probe was run. It is deliberately debt-shaped rather than an exemption: the PR is created as a draft and the placeholder is what has to be replaced before it leaves draft. This path is invisible to `pr-evidence-hook.sh` (the `gh pr create` runs inside a script, not as a Bash tool call, so no `PreToolUse` event fires), and writing the marker also keeps that hook quiet when an agent later fills it in. Tests: `scripts/pr/detect_base_and_create.test.sh`. |
| UserPromptSubmit (pr-mention-nudge) | Every user prompt (keyword-gated: `pr`, `pull request`, `open/create a pr`, `merge this`) | Calls the same shared `scripts/pr/detect_base_and_create.sh` as `pr-auto-create-hook.sh` — catches the case where a PR is requested before (or independent of) a push. Implemented in `.claude/hooks/pr-mention-nudge.sh`. |
| PostToolUse (pr-risk-tier) | Every successful non-force `git push` where the branch already has an open PR | Diffs the PR's files against its base branch, looks each up in `.claude/steering/risk-zones.md`, takes the worst zone found, and labels the PR `risk:red`/`risk:yellow`/`risk:green` via `gh pr edit --add-label` (creating the label if missing). Silent if `gh` is missing, no PR is open, or the risk-zone map doesn't exist. Implemented in `.claude/hooks/pr-risk-tier-hook.sh`. See the `risk-zone-engine` skill. |
| PostToolUse (sloppiness-warn) | Every Write/Edit/MultiEdit to a code file | Scores the file with `scripts/quality/sloppiness-score.sh`'s Verbosity/Erosion metrics (non-LLM-judge, dependency-free bash/awk proxy for clone detection and cyclomatic mass). Records the verbosity value to `.claude/memory/metrics.jsonl` (`sloppiness` metric, surfaced on the dashboard's Session Quality card) on every scoreable write; warns only when the file's verdict is `high-slop` (at/above published AI-agent baselines verbosity 0.33 / erosion 0.68). Never blocks. Implemented in `.claude/hooks/sloppiness-warn-hook.sh`. See the `clean-code` skill. |
| PostToolUse (hook-added-notify) | Every Write/Edit that creates a new `.claude/hooks/claude/*.sh` | Injects a reminder to document the new hook in `docs/hooks/README.md` (and the Wiring Reference table) before the session ends. Stays silent if the hook is already documented. Implemented in `.claude/hooks/hook-added-notify.sh`. |
| PreToolUse (lean-ctx nudge) | Every native Read of a file ≥16 KB (~4,000 tokens) | Hard-denies the Read, naming the `ctx_read` mode to use instead (`signatures` for code, `reference` for text, `aggressive` for unknown). Also registered on `PostToolUse(Write\|Edit)` as a soft suggestion after editing a large file. Silent for small files and data formats (`.json/.yaml/.toml/.lock`). `LEAN_CTX_NUDGE_WARN_ONLY=1` downgrades the Read block to a warning. Implemented in `.claude/hooks/lean-ctx-nudge-hook.sh`. |
| post-commit (doc sync) | Every `git commit` with non-`.md` source changes | Doc-sync: updates all `.md` files referencing changed code via `claude --dangerously-skip-permissions --print`. The prompt is built here; the `claude` call itself is executed by the detached runner (stage 3). |
| post-commit (harness updater) | Every `git commit` touching harness source — `agents/`, `commands/`, `hooks/`, `kiro/`, `scripts/`, `rules/`, `templates/`, `skills/`, or `CLAUDE.md` | Updates `docs/harness-documentation/SDD-SETUP-GUIDE.md` via `claude --dangerously-skip-permissions --print`, executed by the detached runner (stage 3). Path-to-section routing is spelled out in the hook prompt (`rules/` → context engineering, `templates/settings*.template` → hooks/configuration, etc.) |

**Gotcha — `$TODAY_` vs `${TODAY}_`:** both the doc-sync and harness-updater prompts embed the literal instruction `Replace any existing '_Last synced' line with: _Last synced: ${TODAY}_`. Bash parses `$TODAY_` as a reference to a nonexistent variable named `TODAY_` (empty), not `$TODAY` followed by a literal underscore — this silently blanked the date stamp in every `.md` file the agents touched. Always brace variable references immediately followed by an underscore or other identifier character (`${VAR}_suffix`, not `$VAR_suffix`). Fixed in `hooks/git/post-commit`.
| post-commit (detached doc-sync runner + auto-sync `.md`) | Every `git commit` where the doc-sync or harness-updater guard fired — except commits whose subject starts with `docs: auto-sync` (the hook's own), which bail at the top, and commits arriving while a previous run still holds the lock | One fully-detached background job (`{ …; } </dev/null >>.git/post-commit-docsync.log 2>&1 &` + `disown`): takes an atomic `mkdir` lock on `.git/post-commit-docsync.lock`, then runs the doc-sync agent, then the harness-updater agent, then `git add -- '*.md'`, commits `docs: auto-sync (<date>)` scoped to `*.md`, and pushes. `git commit` returns immediately — nothing blocks the terminal. Only one run executes at a time: a concurrent run logs `=== skipped <date>: another doc-sync run is active ===` and exits, so rapid commits cannot spawn parallel agents that race on the git index and drop commits. The lock is released by an `EXIT` trap, and a stale one (>30 min) is stolen by the next run. Each agent is bounded by `timeout 900` / `gtimeout 900` when available. Never stages or pushes non-`.md` files; a failed push leaves the commit in place and logs a warning. The `.md`-only commit re-fires the hook, but the self-commit guard matches its `docs: auto-sync` subject and exits — no loop. Progress and errors go only to `.git/post-commit-docsync.log`. Runs at **every** `SDD_PROFILE` level, `minimal` included — git hooks are infrastructure, not enforcement (see Kiro Settings → Hook profiles). Script: `hooks/git/post-commit`. |
| Stop (address-check) | Every Claude session end | Checks the last assistant turn for the "Husband" address rule from `CLAUDE.md`. If missing, prints `[address-check] husband not found — compact needed` to stdout and exits 0 — a mechanical log line only, not fed back to Claude and not blocking the stop (no forced extra turn, no token cost). It used to exit 2 and inject a `/compact`-and-re-respond correction prompt; that self-correcting loop cost a full turn every time it fired, so the hook was demoted to a passive log. No-ops silently if transcript is unreadable. Script: `hooks/claude/address-check-hook.sh`. |
| Stop (verification-retry) | Every Claude session end | Verification processor (Spotify Backstage AiKA pattern, see `behavioral-modes` skill): scans the turn for explicit success language ("tests pass", "verified working", ...) and cross-checks it against any test-runner command that ran this turn. If the claim is unbacked (no test ran, or the last one failed), logs `[VERIFICATION-RETRY] (warn-only) ...` by default (`VERIFICATION_RETRY_WARN_ONLY=1`); set to `0` to opt into the real auto-retry, which emits `{"decision": "block", "reason": "..."}` to force a retry, capped at `VERIFICATION_RETRY_MAX` (default 2) rounds per user request before giving up. Defaults to advisory because `address-check-hook.sh`'s always-blocking predecessor was demoted for the same turn-cost reason. **No regex since 2026-09-23**: the embedded Python (in a bash heredoc, invisible to ruff's TID251 `re`-ban since ruff only reads `.py` files) is now literal tokenization — `tokenize_words(text)` plus `is_test_command()` (single-token set `pytest`/`jest`/`vitest`/`rspec`/`py.test` + multi-token sequences like `("npm","run","test")`) and `claims_success()` (positional pass/test-word matching), and the transcript-path regex substitution is now a plain `str.replace`. Script: `hooks/claude/verification-retry-hook.sh`. |
| Stop (caveman-savings) | Every Claude session end, once per calendar day, only when Caveman mode is active | Takes the last real assistant response and asks a cheap Haiku call to re-expand it into normal prose, then diffs response lengths (word-count heuristic, ~1.3 tokens/word) to produce a real sample of Caveman's token savings. Guarded against self-recursion via `SDD_CAVEMAN_MEASURING=1`. Appends a JSON line to `.claude/memory/caveman-savings.jsonl`, read by the dashboard's Budget & Efficiency → Compression Pipeline view and folded into the combined-savings total. Script: `hooks/claude/caveman-savings-hook.sh`. |
| PostToolUse (setup-buffer) | Every Bash command (atomic, ≤3 lines) | Detects setup-pattern commands (package installs, `docker compose`/`build`, DB create/migrate, `.env` sourcing/export, `git clone`, `make install`/`setup`/`init`) and appends them to `.claude/memory/.setup-session-buffer.log`; the Stop hook later folds the buffer into `.claude/memory/setup-knowledge.md`. Source: `hooks/claude/setup-buffer-hook.sh` → installed to `.claude/hooks/setup-buffer-hook.sh`; registered on `PostToolUse Bash` in both settings templates. |
| PostToolUse (skill-permissions-gate) | Every Write/Edit to `*/skills/*/SKILL.md` | Soft gate (never blocks): reminds Claude to run `agent-permissions-design` before marking a new/edited skill complete — checks tool access, irreversible-action gates, scope boundaries, and external-service access. Source: `hooks/claude/skill-permissions-gate.sh` → installed to `.claude/hooks/skill-permissions-gate.sh`; registered on `PostToolUse Write|Edit` in both settings templates. Fires only for paths matching `*/skills/*/SKILL.md`, so other `.md` writes are untouched. Its in-file `# REGISTRATION` comment now names `$SDD_HARNESS/.claude/settings.json` rather than a hardcoded `~/.claude/sdd-harness/...` path. |
| PreToolUse (ai-writing-guard) | Every Write/Edit/MultiEdit, and every Bash call that is a `git commit` or `gh` invocation | Hard deny (blocks the tool call): scans for AI-sounding buzzwords/clichés before they land in a file or a commit/PR body — scoped narrowly so it never flags real code. Markdown files (`.md/.markdown/.mdx/.txt`): whole text, minus fenced/inline code. Hash-comment languages (`.py/.sh/.yaml/...`): only `#` comments and docstrings. C-style languages (`.js/.ts/.java/...`): only `/* */` and `//` comments. Bash: only the `-m/--message/-b/--body/-t/--title` text or heredoc body of a `git commit`/`gh` command — never the rest of the command line. Flags buzzword swaps (`leverage`→`use`, `utilize`→`use`, `delve`→`look at`, etc.), AI-cliché phrases (`a testament to`, `it is important to note`, `in conclusion`, …), words like `crucial`/`significant`/`moreover` used ≥3× in one write, and stray `§` marks. Em-dash is deliberately exempt — it's this harness's own doc/hook house style. Ported from claude-codex-settings' "humanize" plugin. Implemented in `.claude/hooks/ai-writing-guard-hook.sh`. |
| UserPromptSubmit (reject-feedback) | Every user prompt | Soft (never blocks): walks the transcript backward for a just-rejected/interrupted tool call, and if the user's next prompt reads as an explanation for that reject, classifies it into a reason (`wrong_target`, `tool_steering`, `scope_drift`, `verify_first`, `rule_setting`, `factual_challenge`) and appends a `[friction]` observation to the same `observations.md` that `revert-detect-hook.sh` and `action-capture.sh` already write to — no new file or ledger. Noise (profanity, bare "no", "try again") is deliberately not logged. Distinct from the tool-failure-memory hooks, which fire on a command that *ran and errored*, not one the user declined before it ran. Ported from claude-codex-settings' claude-telemetry-hooks plugin (OTel export dropped — no backend configured). Implemented in `.claude/hooks/reject-feedback-hook.sh`. |

---

## Global Hooks (bundled — auto-installed by `install.sh`)

These hooks live in `~/.claude/hooks/` (not per-project `.claude/hooks/`) and fire from `~/.claude/settings.json`. They are stored in the harness at `hooks/global/` and installed by `install_globals()` during `install.sh`. No manual copy needed.

| Hook | Script | Purpose | Setup |
|---|---|---|---|
| **Caveman mode (activate)** | `~/.claude/hooks/caveman-activate.js` | Injects terse-response mode at session start. Reads `~/.claude/.caveman-active` for mode level (defaults to `lite`). Requires `node` in PATH. | Auto-installed by `install.sh`; default level `lite` created if not present |
| **Caveman mode (tracker)** | `~/.claude/hooks/caveman-mode-tracker.js` | Fires on `UserPromptSubmit` to sustain caveman mode across turns | Auto-installed by `install.sh` |
| **lean-ctx bash rewrite** | `~/.claude/hooks/lean-ctx-rewrite.sh` | Rewrites common shell commands through `lean-ctx` for compressed output | Auto-wired in `~/.claude/settings.json` by `install.sh` if `lean-ctx` CLI is detected |
| **lean-ctx read redirect** | `~/.claude/hooks/lean-ctx-redirect.sh` | No-op placeholder that allows native Read so Edit works | Auto-wired alongside lean-ctx rewrite hook |
| **Caveman statusline (badge + context meter)** | `~/.claude/hooks/caveman-statusline.sh` | Renders the `[CAVEMAN]` badge and savings suffix (gated on caveman mode being active), plus a **live context-usage meter** (`NN%ctx`, color-coded green/yellow/red at 70%/90%) parsed from `context_window.used_percentage` in the JSON Claude Code pipes to the statusline script on stdin — this part renders regardless of caveman mode. Best-effort persists the last-seen percentage per repo (keyed by `sha256(repo_path)[:16]`, since `CLAUDE_CONFIG_DIR` is machine-global) to `~/.claude/dashboard-context/<key>.json`, which `scripts/utils/dashboard.py`'s `_live_context_card()` reads (state older than 15 min is treated as a closed session and not shown). Opt out of the meter with `CAVEMAN_STATUSLINE_CONTEXT=0`; opt out of the savings suffix with `CAVEMAN_STATUSLINE_SAVINGS=0`. Requires `python3` for the JSON parse; no-ops silently without it. | Registered via `"statusLine"` in `~/.claude/settings.json`; auto-installed by `install.sh` |

> **Note:** `install.sh` copies `hooks/global/*` → `~/.claude/hooks/` and patches `~/.claude/settings.json` automatically. Caveman defaults to `lite`; override by writing `~/.claude/.caveman-active` with `full` or `ultra`. lean-ctx hooks only wire if the `lean-ctx` CLI is installed.

---

## Context Engineering Rules (`rules/`)

Context rules live in the harness source tree at `rules/` and are synced into every project at `.claude/rules/` by `install.sh` and `update.sh` (`sync_dir "$HARNESS_DIR/rules" "$PROJECT_DIR/.claude"`). They are loaded per session, so they count toward the startup token tax audited by `startup-payload-audit.sh` — keep them short.

| Rule file | Installed to | Purpose |
|---|---|---|
| `rules/lean-ctx.md` | `.claude/rules/lean-ctx.md` | Context Engineering layer. The native→`ctx_*` mapping table was **deleted** (2026-09-03): the lean-ctx MCP server states the full mapping in its own `instructions` block, which loads wherever the server does, so a second copy cost context on every session and created a second thing to keep in sync. What remains is the one fact the server cannot state — **native Grep and Glob are denied by policy in this harness**, so for those two the mapping is not a preference, while native `Read` stays available for the read-before-write edit gate and for `~/.claude/projects/<slug>/memory/`. Editing is `ctx_patch` after `ctx_read(mode="anchored")` — the old `ctx_edit` tool is gone. Also the `ctx_read` mode-selection table (`full`, `signatures`, `diff`, `map`, `lines:N-M`, `auto`), a **Profile: `standard`** section documenting the 17 tools actually advertised in the schema (`ctx_callgraph`, `ctx_compose`, `ctx_delta`, `ctx_execute`, `ctx_expand`, `ctx_explore`, `ctx_glob`, `ctx_graph`, `ctx_knowledge`, `ctx_overview`, `ctx_patch`, `ctx_read`, `ctx_search`, `ctx_session`, `ctx_shell`, `ctx_tree`, `ctx_url_read`) — the other ~66 tools are trimmed from the schema but still callable via `ctx_call {"name":"<tool>","arguments":{...}}`, discoverable with `ctx_call {"name":"ctx_discover_tools",...}`; deprecated names to avoid: `ctx_semantic_search`, `ctx_symbol`, `ctx_multi_read`, `ctx_smart_read` (semantic search is now `ctx_search(action="semantic")`); the bare `shell` alias was removed in favor of `ctx_shell`. The 6-step orient → locate → read → edit → verify → record workflow, proactive calls (`ctx_overview`, `ctx_knowledge(wakeup)`, `ctx_compress` via `ctx_call`), the compression-bypass escalation ladder, the pre-edit **risk gate — now a three-row table naming one check, not three** (Python function/class → `mcp__serena__find_referencing_symbols`, LSP-accurate and authoritative; any other symbol → `mcp__gitnexus__impact({target, direction:"upstream"})`; broken/stale index or a non-symbol edit such as auth, DB schema, 3+ files → `ctx_callgraph(action="callers")` plus `ctx_graph` for file-level deps), with the rule that a tool which errors or reports a version mismatch has given **no answer** and the blast radius must be reported as unknown rather than treated as silence-means-safe (mirrors the `## Blast Radius` block in `CLAUDE.md`), and session start/end conventions. The closing rule now reads "prefer ctx_* over native Read/Grep/Shell/Glob" with two explicit exceptions — the edit gate (read-before-write) and `~/.claude/projects/<slug>/memory/` files — replacing the old unconditional "NEVER use native Read/Grep/Shell." Versioned by the `<!-- lean-ctx-rules-v11 -->` marker so `update.sh` can detect drift. |

Editing rule: change `rules/*.md` in the harness source tree, then run `update.sh`. Never edit `.claude/rules/*.md` in a project — it is regenerated output and will be overwritten.

> **Where lean-ctx keeps its savings data.** The dashboard's token-savings figures come from `$XDG_DATA_HOME/lean-ctx/stats.json` and `.../savings/ledger.jsonl`, defaulting to `~/.local/share/lean-ctx/` — **on macOS too**. lean-ctx does not follow the macOS "Application Support" convention that `dashboard.py`'s generic `_platform_data_dir()` assumes for other apps, and `~/.config/lean-ctx/` holds only `config.toml`. Resolving through the generic helper pointed the dashboard at a directory that never contains the ledger, so savings read as zero on macOS.

---

## Kiro Settings — Rules & Templates (`kiro/settings/`)

Harness-internal rules and document templates live at `kiro/settings/` in the source tree and are synced to `.claude/kiro/settings/` by `install.sh` / `update.sh` (see Step 4 path remap). Unlike `rules/`, these are **not** loaded every session — commands and agents reference them on demand, so they cost no startup tokens.

| Path | Purpose |
|---|---|
| `kiro/settings/rules/*.md` | Behavioral rules referenced by commands/agents on demand — spec phases, task generation, agent output format, alignment scoring, steering principles, test backlinks, frontend anti-patterns, memory conventions, hook profiles, deterministic enforcement |
| `kiro/settings/templates/steering/` | Steering document templates used by `/kiro:steering` |
| `kiro/settings/templates/steering-custom/` | Templates for `/kiro:steering-custom` domain docs |
| `kiro/settings/templates/memory/` | Memory bootstrap templates copied into `.claude/memory/` (Step 12) |
| `kiro/settings/templates/skill-extraction-plan.md` | Plan scaffold used by `/kiro:skill-extract` |

### Task generation (`kiro/settings/rules/tasks-generation.md`)

Read by `/kiro:spec-tasks` and `spec-tasks-agent` when turning requirements + design into `tasks.md`. Alongside the existing requirement-coverage, vertical-slicing and integration-task rules:

- **Refactor first, then build — two steps, never one** (added 2026-10-01): when `design.md` chose *refactor* or *hybrid* for an area (design-discovery's "extend vs refactor vs wrap"), the feature task touching that area must be preceded by its **own behavior-preserving refactor task**. That task changes no behavior — existing tests stay green — and its `_Requirements:_` line lists the IDs of the feature task it enables. It lands as its own commit (one task = one commit), so a failed feature can be reverted without losing the restructuring, and the feature task then builds on the refactored shape rather than alongside it. Mixing restructuring into a feature task makes regressions unattributable: a red test could be either change.

### Deterministic enforcement (`kiro/settings/rules/deterministic-enforcement.md`)

Principle: conventions that can be mechanically checked belong in a linter rule, not just steering/markdown — the markdown is the *why*, the linter is the *what*. Referenced by `/kiro:guardrails` and `guardrails-agent`.

- **Complexity baselines** (per-function): `max-lines-per-function`, `complexity`, `max-depth`, `max-params`, `max-statements` (ESLint) / `max-complexity`, `max-args`, `C901` (ruff) / `cognitive_complexity`, `too_many_arguments` (clippy) / `gocyclo`, `funlen` (golangci-lint) — all run with zero-warning tolerance.
- **Structural baselines** (cross-function, the checks no per-function linter can see): duplicated code (`pyscn`, `jscpd`), dead code (`pyscn`, `vulture`, `knip`, `ts-prune`), dependency direction (`import-linter`, `eslint-plugin-boundaries`, `go-arch-lint`). Two load-bearing constraints on wiring these in: **gate on the delta, not the whole repo** (a whole-repo first run reports hundreds of findings and gets disabled the same day), and **make them agent-callable, not CI-only** (the benefit is the agent running the checker in the session it wrote the code, while it still knows why two copies exist). Track the average, not the summary grade — a grade can hold steady while the underlying metric drifts. This is the "Structure" dimension in `guardrails-agent`'s four-dimension audit (complexity / type evidence / assertion strength / structure — see `/kiro:guardrails` in the Slash Commands table).
- **Frontend design-token baselines** (Tailwind, gated on `tailwind.config.*` detection): `no-raw-colors`, `no-arbitrary-values`, `require-static-classes` — see `js-quality-gate-hook.sh`'s `tailwind_design_token_check()` in the Automated Hooks section for the enforcement path.
- **Graduation path**: observation → recurring pattern (3+) → documented in steering/markdown → graduated into a linter rule → markdown becomes rationale only. `/kiro:evolve` identifies graduation candidates; `/kiro:guardrails` applies them.

### Hook profiles (`kiro/settings/rules/hook-profiles.md`)

Graduated automation levels selected with the `SDD_PROFILE` environment variable:

| Profile | Session hooks (`stop-hook.sh`) | Git hooks | Use for |
|---|---|---|---|
| `minimal` | Skipped entirely | Run normally | Rapid prototyping, exploratory work, small fixes |
| `standard` (default when unset/empty) | All checks run | Run normally | Normal development |
| `strict` | All checks run | Run normally | Production-bound code; also run `/kiro:verify quick` manually before committing |

```bash
export SDD_PROFILE=minimal          # persistent
SDD_PROFILE=strict git commit -m "release prep"   # per-invocation
```

Hook scripts guard at the top with `SDD_PROFILE="${SDD_PROFILE:-standard}"` and `exit 0` when it is `minimal`.

**Git hooks ignore the profile** — they are infrastructure, not enforcement. The one unconditional exit is the self-commit guard: a commit whose subject starts with `docs: auto-sync` (the hook's own) skips every stage at every profile level. Otherwise the post-commit third stage runs everywhere: the detached runner executes the doc-sync and harness-updater agents, then stages, commits, and **pushes** only `*.md` files at *every* profile level, `minimal` included. So a `minimal` session still touches the network and the remote on commit — asynchronously, after `git commit` has already returned, with the outcome recorded in `.git/post-commit-docsync.log` rather than the terminal.

That stage is also **serialized at every profile level** on `.git/post-commit-docsync.lock` (atomic `mkdir`). If a previous run still holds the lock, the new run logs `=== skipped <date>: another doc-sync run is active ===` and exits without running the agents, so rapid successive commits never spawn parallel agents that race on the git index. A lock older than 30 minutes is stolen; a normal exit removes it via an `EXIT` trap. Consequence for `minimal`: a fast commit burst may leave later commits' doc sync to the run already in flight or the next commit — not to a per-commit run.

---

## Skills (`skills/`)

Skills live in the harness source tree at `skills/<name>/SKILL.md` (589 skill directories currently: 30 Listed, 559 Library). `install_globals()` in `install.sh` delegates to `scripts/setup/sync-skills.sh`, which syncs each one into **one of two tiers** — `~/.claude/skills/<name>/` for skills listed in every prompt (the 14 domain-router masters plus the small harness-called set), or `~/.claude/skill-library/<name>/` for skills reached on demand through a master and therefore absent from the per-prompt listing. Membership is driven by `scripts/setup/skill-library.txt`; see `docs/skills/SKILL-HIERARCHY.md`. Either way skills are **machine-global**, not per-project — one install serves every repo.

- Adding a skill: create `skills/<name>/SKILL.md` with kebab-case `name:` matching the directory and a `description:` of ≥25 chars (enforced by the `skill-validate` PreToolUse hook), then run `install.sh` / `update.sh`. **Default the new skill to the Library tier** — add its name on its own line to `scripts/setup/skill-library.txt` and add one row to its owning master's table in `skills/<master>/SKILL.md` (`` | `<name>` | <one-line what+when> | `~/.claude/skill-library/<name>/SKILL.md` | ``). Leave it off the manifest only when a hook or command will call `Skill("<name>")` by name, since that tool resolves Listed skills only. Full rules: `docs/skills/SKILL-HIERARCHY.md`.
- Every write to a `*/skills/*/SKILL.md` also trips the **skill-permissions-gate** PostToolUse hook — a soft reminder to run `agent-permissions-design` (tool access, irreversible-action gates, scope boundary, external access) before calling the skill done.
- Usage is logged to `logs/skill-usage.jsonl` by the `skill-usage-tracker` hook and reviewed by the weekly skill-curator routine.
- **Never hardcode the harness path inside a skill.** Skill bodies that need to point at the harness source write `$SDD_HARNESS/...`, not `~/.claude/sdd-harness/...` (and certainly not a literal `/home/<user>/...`, which `repo-drift-review` carried until it was caught). `$SDD_HARNESS` is exported by `install.sh` / `update.sh` into `~/.zshrc` / `~/.bashrc`, so it resolves on every machine regardless of where the harness was cloned; the `~/.claude/sdd-harness` symlink is a convenience derived from `~/.sdd-harness-root`, not a stable address to write into docs. Converted in this pass: `gitnexus`, `hook-design`, `privacy-filter`, `repo-drift-review`, `skill-curator`, `skill-extraction`, `verification-skill-authoring`. `scripts/utils/check-no-hardcoded-paths.sh` excludes `skills/**` (vendored third-party skills record other people's home paths in benchmark output), so this one is convention, not an enforced gate — check it by eye when authoring.
- **Generated reports go to `reports/`, not `docs/`.** Every skill and routine that writes a recurring machine-local report now targets the gitignored `reports/` directory: `skill-curator` and `skill-eval-gate` → `reports/skill-curation-report.md`, `repo-drift-review` → `reports/drift-review-report.md`, `sonar-hotspot-review` → `reports/sonar-hotspot-review.md`, and the harness-health routine → `reports/claudemd-review-report.md` (Phase 1) plus `reports/skill-curation-report.md` (Phase 2) plus token-spend attribution (Phase 3, `scripts/routines/harness-health-runner.sh`, cadence `MIN_GAP_DAYS=13`, force with `HARNESS_HEALTH_FORCE=1`). The runner executes `scripts/utils/token-forensics.py --days 14` **itself** and substitutes the output into the prompt's `FORENSICS_PLACEHOLDER`, rather than asking the headless session to shell out: a headless run that is merely *told* to run a script can skip it silently and then report on nothing while looking like it ran. A missing script or non-zero exit is substituted as a visible marker, so the phase can report "no data" but can never invent figures. Substitution uses `sed` for the date and `awk` with an env-var read for the report body, so the captured output's own characters are never interpreted as replacement syntax. `docs/` is committed documentation and is swept by doc-sync; a routine report landing there gets committed and treated as prose it isn't. See Step 1 for the matching `.gitignore` entry.
- `skills/writing-behavior-specs` — authors and revises `.claude/behaviors/<name>/BEHAVIOR.md` conduct specs: answer-key material for grading a completed trajectory, deliberately kept blind from the agent being graded (unlike `CLAUDE.md` rules or a `<domain>-verify` skill, both read by the agent mid-work). Invoked automatically by `behavior-spec-agent` during nightly maintenance, not meant to be run on demand. Covers deciding whether a candidate behavior belongs (`references/deciding-what-to-save.md` — needs a recognizable situation, a meaningful choice, and provable trajectory evidence; rejects generic virtues, tool syntax, one-off procedures, and disguised outcome rubrics), writing the spec (Intent/Evidence/Decision/Execution/Recovery/Failure-modes dimensions, used only where they add clarity), and calibrating it against positive/negative/outside-scope/lucky-correct-negative trajectories (`references/calibrating-with-trajectories.md`) before validating structurally with `scripts/validate-behavior-spec.py`. Source: [braintrustdata/agentbehavior](https://github.com/braintrustdata/agentbehavior).
- `skills/stacking-pull-requests` — reference skill for the harness's automated stacked-PR flow (GitHub's native stacked PRs via the `gh-stack` CLI extension, public preview 2026-07-30), mapping one SDD task commit to one stack layer instead of bundling a whole spec into a single PR. The automation itself lives in two scripts, not in this skill: `skills/git-pushing/scripts/smart_commit.sh` detects eligibility and inits/adds layers, `scripts/pr/detect_base_and_create.sh` runs `gh stack submit --auto` instead of `gh pr create` once a stack is active. Load this skill only when a stack needs manual intervention (sync conflict, reordering, abandoning), when tuning the auto-trigger threshold (`SDD_STACK_MIN_TASKS`, default 2; `SDD_SKIP_STACK` to disable), or when explaining why a branch did/didn't stack — not as a `gh stack` CLI tutorial.
- `skills/git-pushing` — `smart_commit.sh` now auto-detects a stacked-PR-eligible branch on its first task commit (per `stacking-pull-requests`) and routes every subsequent commit through `gh stack add` + `gh stack submit --auto` instead of a plain `git commit`, falling back to a plain commit if `gh stack add` ever fails mid-stream.
- `skills/issue-triage-routing` — added a fourth triage axis, **Scale** (program-scale: spans multiple decisions that each deserve their own spec, vs. feature-scale: maps to one spec), checked *before* ambiguity/complexity since a program-scale idea can read as simple in one sentence. New **PROGRAM** route (precedence: defer beats program beats clarify beats spec beats one-shot) sends program-scale, on-roadmap ideas to `/kiro:idea-refine`'s map-charting instead of straight into `spec-quick`.
- `skills/secure-agent-design` — added **Pattern 7: Provider-State Portability & Audit Trail**, covering stateful/hosted-provider LLM architectures (OpenAI Responses API, Anthropic extended thinking, Gemini Interactions API) where opaque server-bound state (encrypted reasoning, hosted-tool results, compaction summaries, encrypted subagent messages, server-keyed conversation IDs) silently breaks inspection, export, replay, audit, or deletion. Adds a five-criteria pre-ship checklist and the rule that every such agent keeps its own independently-held, human-readable transcript rather than relying solely on a provider-side ID.
- `skills/evaluation/micro` — added an **Error-Analysis Bootstrap** (extracted from Hamel Husain's `error-analysis` skill) to run *before* rubric design when there's no failure taxonomy yet: collect ~100 traces, read and note the first root-cause per failure, cluster into 5–10 categories once 30–50 are read, label every trace, compute failure rates, then decide per category (direct fix first; only build an evaluator — code-based for objective failures, LLM-judge for subjective ones — if the failure persists). Output goes to `.claude/memory/failure-taxonomy-<YYYY-MM-DD>.md`.
- `skills/csv-data-summarizer` — analyzes a CSV end to end without asking questions: pandas stats, missing-data audit, and only the charts the data supports (time-series only with a date column, correlation heatmap only with ≥2 numeric columns, frequency counts for categoricals), closing with 2–4 dataset-grounded insights. Ships a bundled `analyze.py` (`summarize_csv(file_path)`) run as `python ~/.claude/skill-library/csv-data-summarizer/analyze.py <file.csv>` — with no argument it falls back to `resources/sample.csv`. It writes fixed-name PNGs (`correlation_heatmap.png`, `time_series_analysis.png`, `distributions.png`, `categorical_distributions.png`) into the **current working directory**, and the skill's `.gitignore` ignores `*.png` so generated charts stay untracked. Fixtures: `resources/sample.csv` (21 rows of sales data) as the test fixture, `examples/showcase_financial_pl_data.csv` (45 rows = 15 months × 3 product lines, 25 financial metrics) as a larger demo input; if pandas/matplotlib/seaborn are missing, the skill writes the equivalent inline. Requires `python>=3.8`, `pandas`, `matplotlib`, `seaborn`. Upstream origin: [coffeefuelbump/csv-data-summarizer-claude-skill](https://github.com/coffeefuelbump/csv-data-summarizer-claude-skill). Tracked as **ordinary files in this repo** — edit under `skills/csv-data-summarizer/` and commit in the parent repo like any other skill, then run `install.sh` / `update.sh` to sync it to `~/.claude/skills/`. The skill's `.gitignore` un-ignores `csv-data-summarizer.zip` so the packaged bundle is committed alongside the source.
  - **Vendoring gotcha (fixed 2026-07-28)** — the skill was originally dropped in with its upstream `.git/` directory intact. Git treated `skills/csv-data-summarizer/` as an **embedded nested repo**, so only the files git happened to see (`SKILL.md`, `README.md`, `resources/README.md`) were tracked in the parent; the payload — `analyze.py`, `requirements.txt`, `.gitignore`, `csv-data-summarizer.zip`, `resources/sample.csv`, `examples/showcase_financial_pl_data.csv` — never traveled on clone. A fresh clone plus `install.sh` therefore installed a skill whose bundled script did not exist. The fix was to delete the nested `.git/` and commit the files as ordinary tracked files (`49882a5`). There is no `.gitmodules` entry and this skill is **not** a submodule. **When vendoring any external skill: remove its `.git/` first, then verify with `git ls-files skills/<name>/` that every payload file is actually tracked.**
  - **Reference shape for skill authoring**: `SKILL.md` was cut from ~149 lines to ~36 by deleting the shouty-prohibition block (`⚠️ CRITICAL BEHAVIOR REQUIREMENT`, the DO/NEVER-SAY/FORBIDDEN lists, worked example output, per-industry adaptation table) and stating the rule once positively — "run the full analysis immediately and present complete results in one response; do not ask what they want, list options, or offer choices" — followed by a 4-step **Procedure**, a **Bundled script** section, and two hard **Constraints** (report missing values rather than silently dropping; include every numeric column in the summary). Behavior is unchanged; the token cost is not. Prefer this shape for new skills.
  - The `name`/`description` frontmatter carries the trigger conditions (`Use when the user shares or references a CSV wanting a summary, analysis, or insights`) so routing no longer depends on a prose "When to Use This Skill" section. The `metadata.version` key was dropped — git is the version record — and `metadata.dependencies` is now unpinned (`python>=3.8, pandas, matplotlib, seaborn`); the pinned minimums (`pandas>=2.0.0`, `matplotlib>=3.7.0`, `seaborn>=0.12.0`) live in the skill's `requirements.txt`, which is the single source of truth for versions.
  - **Bundle layout** — a skill is not limited to `SKILL.md`. `install_globals()` calls `sync_dir` on the whole `skills/<name>/` directory, so every payload file lands in `~/.claude/skills/<name>/` with the same relative paths the `SKILL.md` references. `csv-data-summarizer` is the worked example:
    ```
    skills/csv-data-summarizer/
    ├── SKILL.md                              # skill definition (frontmatter + procedure)
    ├── README.md                             # human-facing docs (upstream origin, features, example output)
    ├── analyze.py                            # bundled script — summarize_csv(file_path), 150 dpi PNGs
    ├── requirements.txt                      # pandas>=2.0.0, matplotlib>=3.7.0, seaborn>=0.12.0 — source of truth for versions
    ├── .gitignore                            # Python/IDE/OS artifacts + *.png; un-ignores csv-data-summarizer.zip
    ├── csv-data-summarizer.zip               # packaged bundle for the Claude.ai Settings → Capabilities → Skills uploader
    ├── examples/showcase_financial_pl_data.csv   # larger demo input
    └── resources/
        ├── sample.csv                        # 21-row test fixture
        └── README.md                         # fixture columns + local testing steps
    ```
    Because the paths survive the sync, a bundled script must be invoked at its installed location (`python ~/.claude/skills/<name>/analyze.py …`), and any fixture the script defaults to must sit under the skill directory. The `.zip` is only for the Claude.ai web uploader — Claude Code loads the skill from `~/.claude/skills/` directly and never reads it.
- `skills/local-llm-eval` — the backing script `.claude/scripts/ollama_model_test.py` (OMT, sourced from `scripts/utils/ollama_model_test.py`) is no longer Ollama-only. Two additions:
  - **Custom runner (`--runner PATH`)** — points OMT at any CLI-wrapped model or agent instead of a local Ollama model. Requires `--model` (there's no model-discovery step for custom runners, unlike the Ollama path which lists installed models via `list_ollama_models()`). The runner executable is invoked once per run with the prompt on stdin and `OMT_MODEL`/`OMT_PROMPT`/`OMT_RUN_DIR` set in its environment; its stdout becomes the recorded response (`run_via_custom_runner()`). Example: `python3 .claude/scripts/ollama_model_test.py --model my-agent-v2 --runner ./scripts/my_agent_runner.sh --prompt-file prompt.txt --runs 3`.
  - **Automated grading (`--checker PATH`, Phase 6)** — replaces eyeballing output with a pass/fail verdict. The checker executable runs once after all generations for a model complete, with `OMT_RUN_DIR`/`OMT_MODEL` set in its environment, and must print JSON (`{"pass": bool, "score": number, "notes": str}`). `run_checker()` validates the JSON and the required `pass` key; `write_grades_file()` appends the verdict to `grades.json` in the run directory (parallel to `metadata.json`) and prints a `Checker verdict: PASS/FAIL` line to the console. Works with both the Ollama path and `--runner`. Omitting `--checker` preserves the original human-eyeballing workflow with no behavior change.
- `skills/skill-curator` — the weekly automated sweep (`scripts/routines/skill-curator-runner.sh` + `scripts/routines/skill-curator-prompt.md`) gained a **Phase 1.6 — Dependency Cross-Reference**, run *before* Phase 1's low-quality audit even fires. A new deterministic script, `scripts/utils/skill-dependency-scan.sh`, greps every skill name (word-boundary, `grep -rn -w`) across other skills' `SKILL.md` bodies plus `hooks/`, `agents/`, `commands/`, `kiro/settings/rules/`, `scripts/routines/`, and `CLAUDE.md`, emitting `path:line` locations only (never the matched line text, to avoid a common-word skill name burying the report in noise) and capping each skill to 8 referrers (`+N more` beyond that). The runner splices this map into the prompt's `DEPENDENCY_MAP_PLACEHOLDER` via a temp-file `sed` `r`/`d` insert rather than a variable substitution, since referrer paths can contain `&`/`\` that both `sed` and `awk` treat specially in replacement text. Phase 4's report now has a **mandatory** `## Dependency Flags` section (must appear even when empty, with an explicit "no dependency flags" fallback line) listing any skill that is BOTH a low-quality/cold candidate (Phase 1 / Phase 1.5) AND cross-referenced per the Phase 1.6 map. The interactive `skill-curator` skill treats this section as ground truth (never re-derives it) and gained a new **Delete + migrate references** action type distinct from a plain **Delete** — referrers must be updated or explicitly waived by the user before the flagged skill's directory is removed, never a bare delete.
- `skills/cma-advisor` **(new)** — lets a Claude Managed Agents (CMA) working agent consult a stronger model mid-turn on a single high-stakes/irreversible decision via a reserved `advisor` roster entry (`multiagent.agents`). Covers roster setup (at most one advisor per roster, reserved `anthropic.advisor` name, no per-input tool — consultation policy lives in the system prompt), bounding total spend via a session `budget` (no per-call cap exists), monitoring consultations as thread lifecycle events on the session stream, and retrieving per-consultation cost. Sibling to `cma-outcomes` (post-hoc grade-and-revise, a different mechanism) — don't conflate the two.
- `skills/prototype` **(new)** — build throwaway code to answer a design question the conversation can't settle by talking in circles: `LOGIC.md` (a single shareable HTML file exercising a state machine through hard-to-reason-about cases) for "does this logic feel right?", `UI.md` (several radically different UI variations on one route, switchable via URL param) for "what should this look like?". Rules for both: throwaway from day one and clearly marked, trivial to run, no persistence by default, no polish, surface full state after every action, and capture the validated decision back into real code (with the prototype itself committed to a scratch branch as a primary source) when done. Adapted from mattpocock/skills (MIT).
- `skills/wizard` **(new)** — generates an interactive bash wizard (`template.sh`) that walks a human step-by-step through a manual procedure an agent can't do itself: provisioning infrastructure, setting up credentials/CI secrets, clicking through an unfamiliar third-party dashboard, or a one-off migration/cutover. The template supplies the UX (stage progress, confirmation gates, cross-platform URL opening, hidden secret entry, idempotent `.env` upserts, `gh secret`/`gh variable` writes) — authoring a wizard means only scoping the procedure's stages and writing each stage's `open_url`/`ask`/`write_env`/`set_secret` calls, never hand-editing the library above the `STAGES` marker. Generic form of the pattern `scripts/setup/headroom-setup.sh` and `gitnexus-setup-agent` already hand-build. Adapted from mattpocock/skills (MIT).
- `skills/agent-manager-skill` — retargeted from a tmux-only tool to **Herdr-first**: Path 1 controls Herdr-managed panes (`herdr agent start/prompt/wait/get/read/send-keys`, gated on `HERDR_ENV=1` — never control a Herdr session from outside Herdr, never install/launch Herdr silently), falling back to the original tmux+python3 wrapper (Path 2) only when Herdr isn't available. Scope is explicitly *other terminal panes*, not in-process `Agent`/`Task` orchestration (`dispatching-parallel-agents`/`multi-agent-patterns` territory).
- `skills/git-advanced-workflows` — added a **Resolving Merge Conflicts** pointer to a new `references/git-conflict-resolution.md`: reconstruct why each side's change exists before resolving, preserve both intents where compatible, run project checks before finishing, and never `--abort` as an escape hatch (it just defers the same conflict to whoever merges next with less context).
- `skills/keep-rate` — added **AI Adoption %** (`claude_commits / total_commits` over a 30-day window), a distinct volume metric from Keep Rate's durability metric — a high adoption % with a low keep rate is a real, different signal from the reverse. Recorded alongside Keep Rate in Step 5/6 as a separate `[ai-adoption]` observation line. Surfaced in the dashboard's Session Quality panel as a 4th stat card + glossary entry (`scripts/utils/dashboard.py`).
- `skills/model-tiers` — added **Cascade Escalation**, the per-call counterpart to the existing session-level tier judgment: try the cheap tier first on every call, escalate only calls that fail a confidence/quality check (arXiv 2305.05176 reports up to 98% cost savings vs. always using the top tier). Scoped explicitly to this harness's own Claude-tier routing (haiku/sonnet/opus/fable) — not the cross-provider trained-classifier machinery of RouteLLM-style routers. Worth building only for a high-volume task class with a cheap, reliable confidence signal.
- `skills/prompt-caching` — Response Caching section now spells out the 3-step store/match/serve-or-call mechanism (one production case: 61.6–68.8% hit rate, 92.5–97.3% positive-hit accuracy per arXiv 2411.05276) and a **fit caveat for this harness**: the technique's premise (~30% semantically-similar traffic, arXiv 2508.07675) is a multi-user high-QPS assumption that doesn't obviously hold for a single-developer harness with no embedding/vector-store infra — treat the section as reference for products this harness helps build, not a recommendation to add caching to the harness's own operation.
- `skills/rtk-token-reduction` — added **TALE-EP** (estimate-then-constrain) for sizing a subagent token cap instead of guessing a fixed number: ask the model zero-shot for its minimum-needed tokens, then feed that back as the actual budget (~67% avg output reduction, <3% accuracy drop across 7 benchmarks per arXiv 2412.18547). Caveat: compression isn't uniformly safe — arithmetic/multi-step-math subtasks lose ~4 accuracy points at an 80% token cut where commonsense/symbolic tasks lose nothing, so don't apply a tight budget uniformly. Newer Claude models (Opus 4.7+) don't take a raw `budget_tokens` parameter — use `effort` instead.
- `skills/iterative-repair-loop` — added a **held-out set** guard: reserve ~20% of validation cases untouched during iteration (Phase 2 never sees them, Phase 3 doesn't score against them until after convergence), then re-run them once the loop reports `passed: true`. A visible-case pass that fails held-out cases means the loop gamed the rubric, not solved it — treat as FAIL, not a partial win; skip for artifacts with <5 total cases. Also added a **flaky-result rule**: if a case's pass/fail flips across two identical re-runs, don't average it away — stop the loop, report it as a finding, and fix the source of non-determinism before resuming. New terminal outcomes `Held-out FAIL` and `Flaky result` and a `Held-out check:` line added to the completion report template.
- `skills/agent-memory-systems` — added **File-Based vs Structured Memory: When Files Lose**, benchmarking this harness's own default (markdown + grep under `.claude/memory/`) against embedded atomic-fact stores on LongMemEval-S: 44.9% vs 73.6% accuracy, 665k vs 27k tokens per correct answer (~25x), with the gap widening a further 15pts at 500-session scale — files win only on abstention accuracy (88.9% vs 77.8%). The practical read is deliberately narrow: file-based memory is fine for what the harness actually uses it for (session-scoped recall, small fact counts, human-readable audit trail) and degrades specifically on cross-session joins and temporal aggregation over long history. Adding a structured layer is warranted when a real need to query "what changed across N sessions" appears — not before.
- `skills/memory-systems` — the "❌ Knowledge graphs for agent memory" anti-pattern gained a nuance line so it isn't read as "structure loses": the measured loss belongs to *LLM-distilled* graphs (Zep 74.6%, Graphiti 53.4% on LongMemEval), while raw dated-fact stores with no distillation step score ~78% — beating both files and distilled KGs at 6x less context and 400x less ingest cost. The failure mode is the distillation step's information loss, not structure itself.
- `skills/multi-agent-patterns` — added **"Scale scrutiny to blast radius, not nesting depth"**: the risk signal for a post-condition gate is an agent-generated artifact's graph position (fan-out to downstream consumers), not how deep it sits in the subagent delegation chain. A leaf node feeding one consumer tolerates a light post-condition; a node whose output fans out broadly (a plan several executors follow, a routing classification) needs a proportionally stronger gate — dedicated verifier, schema-constrained output, evidence traveling with the conclusion, N-way independent production, or human approval before further fan-out.
- `skills/gitnexus-debugging`, `skills/gitnexus-exploring`, `skills/gitnexus-impact-analysis`, `skills/gitnexus-pr-review`, `skills/gitnexus-refactoring` — every worked example and tool-call reference was rewritten from bare `gitnexus_query`/`gitnexus_context`/`gitnexus_impact`/`gitnexus_detect_changes`/`gitnexus_rename`/`gitnexus_cypher` to the `mcp__gitnexus__*`-prefixed form the MCP server actually exposes. The bare names never resolved as callable tools; `scripts/setup/gitnexus-reconcile.sh`'s new `fix_tool_names()` (see GitNexus section below) performs the equivalent rewrite on the managed block it writes into `CLAUDE.md`/`AGENTS.md`, so the skill bodies and the generated project block are now consistent.
- `skills/lean-ctx` — tool count bumped 69 → 83; the "Core Tools (10 always visible)" table was replaced with **"Advertised Tools (profile `standard` — 17)"**, listing all 17 schema-visible tools (adds `ctx_callgraph`, `ctx_compose`, `ctx_delta`, `ctx_execute`, `ctx_expand`, `ctx_explore`, `ctx_glob`, `ctx_graph`, `ctx_patch`, `ctx_url_read`; drops `ctx_edit` and `ctx_call` from the always-visible list) with a note that the other ~66 tools are reachable via `ctx_call {"name":"<tool>","arguments":{...}}`. File Editing now reads "Use native Edit/StrReplace. If unavailable, use `ctx_patch` after `ctx_read(mode="anchored")`" (was `ctx_edit`). The "More Tools" section spells out the `ctx_call`/`ctx_discover_tools` invocation syntax and notes `ctx_callgraph`/`ctx_graph` are already advertised (only `ctx_impact`, `ctx_architecture`, `ctx_routes`, `ctx_smells` need `ctx_call`); symbol lookups route through `ctx_search(action="symbol")`.
- `skills/agent-harness-design` — added **The Distribution Test** (own the layer, or keep the model-native default?). The layer routing decides *which* layer to fix; this decides *whether to own it at all*. Rule: the closer the work sits to what frontier models were trained on, the better an off-the-shelf harness performs; start general and add custom structure only as you narrow onto a use case you need to be excellent at. The non-obvious part is that **distribution is per-subtask, not per-mission** — a mission can be far out of distribution while its subtasks (edit a file, run a shell command, search a repo) sit squarely inside it, so the usual correct shape is a custom harness around the mission that still delegates in-distribution subtasks to the model-native tool. Models are RL'd on their *own* tool formats (LangChain Deep Agents swaps the edit-file implementation per model rather than imposing one house format). Includes a 5-step decision procedure and an explicit self-audit: **this harness's own wholesale `ctx_*` substitution for native Read/Grep/Glob is exactly the case the test flags** — defensible on token-compression grounds, but it trades accuracy the model already had for context budget and should be re-justified, not assumed. Source: Harrison Chase (LangChain), "When to Build Your Own Agent Harness", 2026-08.
- `skills/agent-permissions-design` — added **Verdict Computation and Context-Dependence**, splitting a permission decision into *how the verdict is computed* and *what it means where it fires*. (1) **Normalize before comparing** — never match a guard rule against the rendered string form of a structured value; parse to structure (argv via a real lexer, URLs via a URL parser, addresses into numeric form) and compare tokens exactly. Substring/regex matching over command text is not a security control. Canonical illustration: blocking the literal `169.254.169.254` does nothing about `curl http://2852039166/` — the same bug class that lived in this harness's own `git-destructive-guard-hook.sh` until 2026-08. (2) **Fail closed** on unresolvable expansions (`$VAR`, `$(...)`) for high-stakes verbs. (3) **Tier grant matching** — match credential-tier grants on exact command shape (design guidance only here: Claude Code exposes no persistent grant store a harness can control). (4) **An `ask` verdict is context-dependent** — `ask` only exists where a human can answer, so it must degrade to `deny` under headless execution (anything reachable from `scripts/orchestration/daily-orchestrator.sh` or `scripts/routines/*`); evaluate guards cheapest-deterministic-first and leave any human prompt last. Two new anti-pattern rows cover both halves.
- `skills/agentic-rl-tito` — scope widened (description + `also_sourced: arxiv.org/abs/2608.17528`) to cover **harness-mediated RL**, where the trainer does *not* own the token buffer because training runs through a deployment harness (Claude Code, OpenHands, mini-SWE-agent) that owns the tools and control loop. Measured: only 36% of rollouts stay a single sample (mean 2.41), i.e. token-prefix continuity breaks in ~⅔ of rollouts, from chat-template non-compositionality, decode–retokenize drift, and output reserialization. Prescribes **best-effort merging** (merge only on exact token-prefix match, else start a new sample) and bans buffered token replacement (AReaL, verl Uni-Agent) as a silent off-policy discrepancy. The two loss-side fixes **must ship as a pair** — rollout-level advantage alone regresses below baseline (33.1% vs 35.0%); with rollout-level token-mean normalization it reaches 38.2% — and every sample from one rollout must land in a single optimizer update. Explicitly not automatable from a Claude Code harness: no hook or routine can observe it.
- `skills/goal-mode` — a good `/goal` condition now has **five** parts, not three: the existing measurable end state, evidence source, and turn cap, plus (4) **an invariant** — what the run must not do even to satisfy part 1 (`do not change the public API of any exported hook`, `do not edit or delete any existing test to make it pass`, `do not add a dependency`), because a metric plus a turn cap constrains only the destination and the evaluator checks whether the stated condition was met, not whether the result is good; and (5) **a progress requirement** — an abort clause for spinning in place (`abort if two consecutive turns show no improvement`, `abort if the same command produces the same failure twice`), since a turn cap bounds damage but does not detect futility. Part 5 is the authoring-time form of `loop-patterns`' circuit breakers — keep the thresholds aligned (loop-patterns breaks at 2 no-progress passes). Also: name the tool that produces the evidence, since the evaluator reads the **transcript**, not your files.
- `skills/keep-rate` — Step 1 queries corrected: drop `--all` (it enumerates every branch — 233 commits vs 111 on dev — inflating the denominator; enumerate on the working branch) and add `--no-merges` (3 merges contributed 8,505 phantom lines). Two more anti-patterns added for Step 2: skip `--numstat` rows with `-` (binary files inflated one file by 1141%) and use `git blame --line-porcelain` headers instead of `git blame | grep <hash>`, which misses continuation lines.
- `skills/error-handling-patterns` — new anti-pattern: **text scanning for error status classification**. Setting error status by checking whether report text contains "error"/"failed" keywords falsely fails diagnostic agents; use structured status fields only.
- `skills/skill-extraction` — Phase 1 gained a **recourse ladder for degraded retrieval**. WebFetch failing is the normal case (login walls, Cloudflare, SPA shells, cross-host redirects, paywalls, deleted pages all return *something*), so "I fetched it" can be false: **never summarize a page you did not actually read**. Seven rungs, stop at the first that returns real content — direct WebFetch → the redirect URL WebFetch handed back → provider public JSON (Reddit `<url>.json`, HN firebaseio, Bluesky XRPC, Mastodon `/api/v1/statuses/<id>`, `gh api .../contents/<path>`) → YouTube transcript via `uvx --from youtube-transcript-api` (`yt-dlp` is usually absent, don't install it; `timedtext`/InnerTube are PO-token gated) → third-party archive mirrors → Wayback `web.archive.org/web/2/<url>` → WebSearch for title + `transcript`/`summary`, which is **secondhand** and must be labelled as such. Private/deleted content is a dead end — say so. The extraction output must report `Fetch status: ok | partial | failed` plus which rung succeeded, and on failure which rungs were tried and why each failed, and must distinguish **missing** from **zero/absent** in the `docs/sources/` entry. If no rung returns real content, propose **nothing** from that source.
- `skills/auditing-spec-choices` **(new)** — the workflow behind `/kiro:audit-choices` and `/kiro:spec-impl` Phase +1. Reconstructs the decisions an implementation made where the spec was silent and records each in `specs/<feature>/choices.md` as `sound` (any reasonable implementer would agree), `unsound` (needs rework), or `needs-user` (a preference the agent does not own). Phases 1–6 run per implementation pass; Phase 7 (`--close`) resolves provisionals, drops reverted entries, dedupes, and promotes what survived. Every `needs-user` entry carries a **reversible provisional call** so an unattended run never stalls waiting for a human — an irreversible provisional is reported as irreversible rather than dressed up as reversible. Entries are ordered least-confident first, so the ones most needing attention are read before attention runs out. Phase 6 stays quiet unless a signal actually fired: entries clustering on one slice (reslice it), many `needs-user` in one pass (the Decision-Budget Gate should have caught it), repeated `unsound` on one theme (propose the missing convention, not per-instance fixes). An audit that always reports a problem stops being read.
- `skills/auditing-token-spend` **(new)** — measures where tokens actually went, from `~/.claude/projects/**/*.jsonl`, via `scripts/utils/token-forensics.py`. Every other token skill in the harness (`context-optimization`, `context-window-management`, `rtk-token-reduction`, `cost-optimization`) is prescriptive advice about *reducing* context; none measured actual spend, so none could say which advice would pay. Five analyses: deduplicated totals (same `requestId` collapse the dashboard uses — naive summing overstates ~80%); **amplified per-tool cost** = chars returned × requests that followed in the same session, ranking what a tool *caused* rather than what it returned; peak rolling 5h window (what a usage limit actually measures); session shape as an automation fingerprint; and an automation split that reports its own `method` — `isSidechain` would give an exact subagent split but measured 2026-08-30 is present on every assistant line and true on none (0 of 6,423 across 150 transcripts), so the script falls back to a clearly-labelled short-session proxy and switches automatically if the field is ever populated. It never prints an unpopulated field as `0%`: unknown and zero are different findings. Also drives Phase 3 of the bi-weekly harness-health routine. Two additions (2026-09-03): spend is now **cost-weighted rather than counted**, since a raw token count ranks the wrong sessions — a cache read costs 0.1× an input token, a cache write up to 2×, and output ≈5× — so a million cache reads cost a tenth of a million fresh input tokens; and **cache-bust detection**, since the prompt cache is keyed on model, so a model switch mid-session invalidates the prefix and the next turn re-prefills, showing up as an unusually large `cache_creation` on the turn right after the switch. Placeholder model names in angle brackets (`<synthetic>`, marking injected/error turns the model did not generate) are excluded by `is_real_model()` — counting them produced 4 spurious busts out of 5 on the first run, every one with 0 re-prefill cost. A third addition (2026-09-03): **Phase 5 (RTK Net Effect)** measures RTK's local savings against a global cost `token-forensics.py` can't see — RTK reports raw shell bytes removed on one command, not whether the agent, missing detail it needed, reran that command or re-read the same file later in the session, the recovery cost that would make "saved" net-negative globally. The new sibling script `scripts/utils/rtk-net-effect.py` measures that recovery-path signal directly from transcripts (exact-match Bash rerun rate, Read reread rate, same-session only, `--days`/`--project`/`--limit`/`--json`); it is the same instrument a new daily routine, `scripts/routines/rtk-net-effect-runner.sh`, writes to `.claude/memory/rtk-net-effect.json` and that the dashboard's RTK layer note (Headroom tab) reads to show both rates alongside its savings figure instead of savings alone. Read a high rerun/reread rate as a caveat, not a verdict — evidence, not proof, that compression cost detail the agent had to go get back, since a genuinely new task can also re-issue an old command or re-read an old file; a rate that is high *and rising* is the signal worth acting on, a flat baseline is not itself a finding.
- `skills/skill-curator` — the description-budget audit gained an **aggregate ceiling**, run before the per-skill table: `python3 $SDD_HARNESS/scripts/skill-listing-budget.py --top 20`. Per-skill thresholds (>150 ⚠️, >200 🔴) cannot tell you whether the listing is affordable — every skill can sit under 150 chars while the total is an order of magnitude over. Names and descriptions are paid on **every session unconditionally** (bodies load only on invocation), so the working budget is **1% of the context window**; first run on this machine was 995 skills / ~42.2k tokens = 21.1× over on a 200k window, 4.22× on 1M. Read the ratio, not the absolute: above ~2× the driver is skill *count*, not description length, so the finding routes to Phase 3's deprecate-cold-30d / archive-cold-90d passes rather than 200 description rewrites. The script also flags skills with **no description at all** — strictly worse than a long one, since they consume a listing slot while giving the router nothing to match on, so they effectively never fire.
- `skills/claudemd-review` — two new audit checks. **Unenforced MUST rules**: collect every hard rule (MUST/NEVER/ALWAYS/"on every X"), find what mechanically enforces it (hook matcher, `permissions.deny` entry, lint rule, test), and flag the ones nothing enforces with the concrete artifact to add — which event, which matcher, which deny rule. A hard rule backed only by written instruction is a hope: it holds while context is short and attention is on it, and stops holding under long sessions, compaction, and subagents, which is exactly when it mattered. Not every MUST gets a hook reflexively — some genuinely need semantic judgement, and saying so explicitly is a valid outcome. **Three-axis leakage** (the review historically checked only the first): lint leakage (rules a linter already enforces — empirically the most common), README/manifest leakage (setup steps and dependency lists copied from `README.md`/`package.json`/`pyproject.toml`), and skill leakage (guidance duplicated from a `SKILL.md` body, paid every session to say what the skill says anyway when it fires).
- `skills/skill-eval-gate` — two phases added, both of which can force **INCONCLUSIVE**. **Phase 1b (calibrate scenario difficulty)** replaces "vary in difficulty," which is unverifiable by inspection, with a two-point probe: baseline on a strong model, treatment on a weak one. Strong baseline passes → scenario too easy, replace it. Weak treatment fails everywhere → too hard. Weak treatment passes where strong baseline fails → well calibrated. Strong scoring *worse* than weak on the same scenario is not a difficulty signal at all — stop, because it means a reward hack or a broken checker. **Phase 3b (validate the judge)** applies whenever an LLM rubric scores anything: rank a deliberately bad and a clearly good reference answer at temperature 0, and if the judge does not put the good one strictly above the bad one, discard the whole run — unvalidated judge scores are not weak evidence, they are no evidence. Pair every quality judgement with a **completeness** judgement, since any "is this lean/clean/focused" rubric can be won by producing less; require the judge to cite the specific construct it penalized or say `none`; and persist raw runs so a rubric tweak can be re-scored instead of re-generated (which changes two variables at once).
- `skills/tool-design` — added the **six-section skeleton** for a tool description (opening line → WHEN TO USE → WHEN NOT TO USE → DO NOT USE FOR → USAGE → EXAMPLES, including one near-miss the tool should decline). Sections 3 and 4 overlap **on purpose**: a soft handoff tells the model where to go instead, a hard prohibition holds when the request is ambiguous and the soft version bends. The repetition is tier-sensitive — small models drop the soft boundary under ambiguity, mid-tier models measurably gain from the restatement, and the largest are unaffected and not harmed — so it costs nothing where unnecessary and rescues routing where it is not. Names **bash gravity**: as a collection grows, routing collapses toward the most general tool, because it is never *wrong*, only worse, which is why section 4 prohibits *this* tool rather than recommending another. Verify by **per-section ablation** against three fixed probe prompts, not by re-reading. Also added **output caps: cap, announce, paginate** — ~500 lines per file read, ~50 search matches, ~5000 chars of command output; announce the cut and its size, because silent truncation is worse than none (the model acts on a false premise and concludes "there are no other call sites" from a list cut at 50); offer the continuation; keep the **tail** for anything that can fail (failures put their signal last) and the head for structured listings; and report the true total, not the shown count.
- `skills/dispatching-parallel-agents` — added a **fan-out-for-planning variant**, explicitly distinct from the partitioned work the rest of the skill assumes. Planning does not partition: every drafter looks at the same problem, so three agreeing drafts feel like corroboration while being one draft sampled three times. Divergence has to be engineered — spawn at least three drafters with named orthogonal biases (fewest slices / risk-first ordering / seam quality), keep them blind to each other in fresh contexts and separate worktrees, and diversify by **vendor family, not model tier** (a weaker model of the same family buys a worse version of the same opinion, and its disagreements are noise). Synthesize rather than picking a winner: independent agreement is firm ground, disagreement is the map of the load-bearing decisions and the actual product of the exercise. Then re-inspect the highest-risk slice as its own feature, and sweep the conversation for decisions that live only in chat plus plans that are now superseded.
- `skills/goal-mode` — added **non-blocking checkpoints** (rule 7) for decisions that genuinely want a human, where "don't stop to ask" would otherwise mean stopping anyway or pretending the decision was obvious. Open the evidence where a watching human can see it, wait a bounded ~5 minutes, and on silence decide on the evidence — silence is not a blocker, since the premise of goal mode is that nobody may be watching. Record the call, what else was viable, and specifically **how to reverse it**; reversibility is the load-bearing part, and an irreversible decision with no answer is a genuine blocker under rule 6. A checkpoint is a course-correction opportunity, never a gate — one that can halt the run will halt it at 3am. Spec-backed runs log these to `specs/<feature>/choices.md`, which `/kiro:audit-choices` is built to read.
- `skills/keep-rate` — Step 2 hardened further: detect root commits (`git rev-parse --verify -q "<commit>^"`) and diff against the empty-tree hash `4b825dc642cb6eb9a060e54bf8d69288fbee4904` instead of failing; count added lines with `--numstat -M -C` (handles renames, skips binary `-` rows); build the blame map **once over all text files in HEAD keyed by commit** rather than blaming each commit's own paths, which loses files renamed after the commit. Baseline note added: record which blame flags produced a baseline, since pinning `-M -C` shifts the figure ~0.2pp — drift, not decay.
- `skills/create-pr` — added **Step 3b: Attach Runtime Evidence**, and the description was retargeted from "follows Sentry conventions" to what the skill now guarantees ("creates pull requests with captured before/after evidence in the body"). Everything else in a PR description is the author's account of their own work, which the reviewer cannot check; the `## Evidence` section is the part they can. Form follows the change — before/after screenshot or video for a visible surface, before/after numbers or the same command failing then passing for logic/perf/data, one line saying so when there is genuinely nothing to show. **Use the same probe both times** (a "before" from one command and an "after" from another is two unrelated facts side by side), and **capture the before-state while reproducing the problem, before fixing it** — that is the load-bearing rule and the easiest to skip, because after the fix the capture costs a revert, so what gets written instead is the old behavior recalled from memory and presented as an observation. If the before-state is gone, write `Before: not captured` and say why; never reconstruct one. Explicitly not a test plan — the existing ban on test plans covers stated intent to check, not the captured result of having checked.
- `skills/iterate-pr` — retitled "Iterate on PR Until **the Reviewer** Passes It". Success now requires three conditions, and the third is not self-assessed: green CI, no unaddressed human feedback, and **the reviewer re-ran after the latest push and came back clean**. That third condition is what terminates the loop — "I addressed the comments" is graded by the same agent that wrote the code, and an agent that believes it is done is exactly the state the loop exists to escape. The verdict is read from whatever reviewer the repo actually runs (a `gh api graphql` query counting unresolved, non-outdated review threads, plus `gh pr view --json reviews,reviewDecision,commits`) rather than assuming a vendor; scoring bots' numeric verdicts must be at maximum with zero unresolved threads. Two failure modes are refused outright: a reviewer verdict predating the last push (compare the review timestamp against the head commit — a stale pass is not a pass), and resolving one's own threads to reach zero. New escalation: **5 review rounds without a clean verdict** stops and reports what the reviewer still objects to, as does an objection that is wrong or a judgment call the agent should not make alone.
- `skills/skill-eval-gate` — the treatment arm is now **k=3, `pass^3` not `pass@3`**: each scenario runs 3 independent agents in one message and scores PASS only if all 3 pass. The asymmetry against the single baseline run is deliberate — Phase 1b already disqualifies any scenario a strong baseline passes, so extra baseline runs buy nothing, while the treatment arm is the one making the claim and the failure guarded against is a skill certified on one lucky draw. Cost is 12 spawns for a 3-scenario gate; if that is too expensive, **cut scenarios, never the k=3 runs** — 5 scenarios at k=1 is a worse gate than 3 at k=3, buying breadth with the ability to tell signal from noise. Phase 4's table shows the three runs individually, and a split (2/3 or 1/3) is neither a pass nor a clean fail but direct evidence that the skill's effect is smaller than the run-to-run noise; a majority of split scenarios is a new **INCONCLUSIVE** verdict row, and splits are never rounded up to "basically passing" or averaged into a percentage that makes a coin-flip look like 67% quality.
- `skills/skill-eval-gate` — added **Phase 1c (compression-regression scenario)**: one of the ≥3 Phase 1 scenarios must target a **soft** instruction (a "usually"/"prefer"/implied exception, not a hard rule) and run twice against the same deterministic check — verbatim `SKILL.md` vs. a compressed version (a fresh subagent asked to summarize it to ~half length, or the project's real compaction path — a `PreCompact` hook, `context-compression` — preferred over a hand-written summary when one exists). Compression tends to flatten qualifiers into absolutes once the reasoning behind them is gone; modeled on a documented case where a compressed instruction hardened into an absolute scheduling rule and broke parallel task execution, fixed by one clarifying sentence, not a rewrite. Reuses one of the existing Phase 1 scenarios rather than adding a 4th; skip only when the skill has no soft/conditional instructions at all, stating that explicitly. A compressed-only failure is its own **FAIL** verdict row in Phase 5 — a compression regression, distinct from an ordinary capability gap — fixed by pinning the flattened qualifier with one explicit sentence, not by expanding the document generally.
- `skills/claudemd-review` — **hook-injected context is now in scope** for the conflicting-instructions check. Any hook emitting `hookSpecificOutput.additionalContext` (`SubagentStart`, `SessionStart`, `UserPromptSubmit`) is a first-class instruction surface — always resident, and for a subagent it is the *only* one, since `CLAUDE.md` and `.claude/rules/` never reach a spawned agent — so the review reads those hook bodies in `hooks/claude/` alongside the CLAUDE.md files. Two failure modes live only here: a **duplicate statement** across CLAUDE.md and an injected hook (paid on every spawn, two copies to keep in sync — ask which surface reaches the audience that needs it and delete the other), and an **unsatisfiable MUST** naming a tool that currently errors, which trains the model to discount every other MUST. Hooks are read as inputs, never edited by a Phase 4 ablation experiment.
- `skills/context-compression`, `skills/context-window-management` — both gained the same controlled measurement (SKILL.state, arXiv:2608.26263 Table 5), which pins every strategy to an identical ~1,800-token budget on a 100-step task: sliding-window truncation 0.18, ReAct + LLMLingua 0.22, summary-capped history 0.52, structured state object **0.94** — against 0.84 for *unbounded* full history. Holding the budget constant isolates the variable: the gain came from **structure, not from spending fewer tokens**, and more context was not better. LLMLingua is the instructive failure — entropy-based pruning deletes "seemingly redundant slot identifiers that are semantically vital", and identifiers, keys and field names are individually low-information and collectively load-bearing because they are what later steps join on. Practical rule: before compressing a span, ask whether it is **relationally dense** (does anything later have to join on an ID, path, key or name in it?); if yes, restructure into a compact explicit form rather than summarizing or truncating. `context-window-management` also now states that cost is not proportional to token count — cache reads 0.1× input, cache writes up to 2×, output ≈5× — so ranking by raw tokens ranks the wrong things; weight before ranking, per `scripts/utils/token-forensics.py`.
- `skills/keep-rate` — four more Step-2 anti-patterns: test binariness against the **working-tree file, not the diff blob** (git-LFS pointers read as text in a diff and are binary in the worktree); do not filter `--line-porcelain` headers on field count alone (headers carry 3–4 fields, so `len(parts) >= 4` counts the first line only — check fields 2–3 for digits); do not change blame flags mid-measurement (per-commit reattribution under `-M -C` reaches **4.7pt**, e.g. `8df5ed3` 93.6→98.3, `0a9946c` 99.9→92.4, so blame-based keep rates are incomparable across flag settings); and the denominator must count **all touched paths**, since a HEAD-only count silently drops deleted/renamed files and yields an impossible keep rate above 100%.
- `skills/privacy-filter` — doc pointer moved to `docs/security/privacy-filter/README.md` (the README relocated under `docs/security/`).
- `skills/session-quality` — two anti-patterns added: do not mark `--idle` on zero commits alone (a prior run's output lands post-judge in that window), and do not assume routine idleness from zero observations — check the routine transcripts for the hard-failure signature (~15 lines means it died at auth), because an outage is otherwise indistinguishable from an idle day. Two refinements since: on zero commits, **check sibling repos** — activity is invisible when `.claude/` is gitignored or the work landed in a parallel directory — and the transcript-length signature is a prompt to look, not a verdict: a ~15–19-line transcript can be a legitimate short session, so read it for the auth error rather than inferring one from the line count.
- `skills/session-quality` (2026-09-30) — the transcript-length heuristic above is now retired in favour of a **tail-distance** rule, since absolute line counts overlapped between dead and live runs. Three further anti-patterns: a completion marker stamped at routine *start* hides outages, because `claude --print` exits 0 on OAuth and 502 gateway failures — gate on the artifact; a scheduler that never fired leaves exactly the artifacts of an idle day, so check cadence, and zero transcripts + observations + metrics across **both** repos on one date means never-fired, not idle; and `/private/tmp/claude-*/<slug>/` is subagent scratch space, not a transcript channel — an empty `tasks/` there is normal, and transcripts live in `~/.claude/projects/<slug>/`.
- `skills/hook-design` — added **Replay Before Ship** plus a bundled `resources/hook-replay.py` (and its `.test.sh`). Hand-written cases only cover the calls you thought of; before changing what a PreToolUse hook blocks, replay the calls the agent actually made (from `~/.claude/projects/*/*.jsonl`) through both the old and new version and justify every `newly_blocked` / `newly_allowed` / `new_errors` flip — a false block on a routine command is the most common way a hook change goes wrong. The old hook is the baseline. Counts only by default (transcripts hold secrets; `--samples N` prints truncated calls), `--fail-on-change` wires it into the hook's own test once the intended flips are accepted, each run gets a throwaway `HOME` (writes outside `~` are **not** isolated), and a run with zero recorded calls exits 2 rather than passing — nothing tested is not a pass. Coverage limit: a command nobody has run yet is invisible to it, so keep the hand-written edge cases too. Source: Dream-RSI (arXiv 2609.14858) — score a candidate policy by replaying recorded history against the incumbent.
- `skills/hook-design` (2026-10-01) — added **Fail Closed, Narrow Only**, two rules for any hook that can exit 2 (advisory always-exit-0 hooks are out of scope). *Fail closed*: a blocking hook whose own machinery fails must not allow the call — `python3` off PATH, malformed event JSON, analyzer crash. The fail-open shape is `|| echo ""` or `except: print('')` followed by `[ -z "$X" ] && exit 0`, which turns a broken interpreter into a silent allow; the section carries the `fail_closed()` helper to copy, scoped by a literal glob on the raw event so a broken dependency does not block every tool call. Propagate the verdict too — a heredoc'd `sys.exit(2)` is discarded if the script then ends in a bare `exit 0`, which is why `agent-behavior-guard.sh`'s enforce mode blocked nothing until 2026-10-01. Worked examples: `git-destructive-guard-hook.sh`, `ledger-append-only.sh`, `agent-behavior-guard.sh`. Every blocking hook's `*.test.sh` now needs a malformed-event case and a `python3`-off-PATH case (a `PATH` holding only `cat`), without which a fail-open regression passes the suite. *Signals only narrow*: a verdict may block, warn, pause or revoke, never grant — no hook returns `permissionDecision: "allow"` or writes allow-rules, since a detection that can widen access can be steered by whoever controls its input, and widening access is the human's call. Source: Perplexity, "How we engineer safer agents", 2026-09-29.
- `skills/keep-rate` (2026-09-30) — the Step 2 recipe is now pinned as a script, `scripts/session/_keep_rate_calc.py`, rather than re-derived per run; if it appears missing from `.claude/scripts/session/`, check the harness source first (`.claude/` is gitignored and bulk reinstalls wipe it) and rebuild from the SKILL.md Step only as a last resort. Four anti-patterns: an emptied 30-day cohort makes the rate **undefined, not 0%**; generated scripts go to the harness `scripts/` tree first; and a calculator methodology change (clamp logic, reattribution rules) invalidates comparability exactly as a blame-flag change does — record the methodology version alongside the value.
- `skills/skill-curator` — Phase 4's approval step now requires **repo-qualified citations** (`(source: repo-name date [tag])`) before an edit is applied. `skills/` is the fleet's shared source of truth, so any repo's curator can write here; a bare `(source: DATE [tag])` resolves only in the repo that wrote it and reads as dangling everywhere else. The same rule was added as an explicit anti-pattern to `skills/keep-rate` and `skills/verification-before-completion`.
- `skills/proof-collaborative-review` — corrected against the current Proof SDK: build the editor bundle, symlink `dist/assets/*` into `public/assets/`, serve with `COLLAB_EMBEDDED_WS=1`, post `markdown` (not `content`), and share `/d/<slug>` (not `/doc/<slug>`). Step 6 now compares **content**, normalizing Proof's re-serialization first — formatting-only differences are not edits. Details in the Proof Collaborative Review section below.
- `skills/skill-eval-gate` — an **infrastructure failure is a missing run, not a FAIL**: a scenario that couldn't reach its API or tool is re-run, and only three consecutive errors make it report no verdict for that scenario rather than scoring it. Phase 3b's judge validation gained a fourth step — score one real treatment output **twice** with the same rubric and model; a verdict that flips between the two means the judge is reading noise, so discard the run and tighten the rubric. Ranking a good output above a bad one proves the judge can see the difference; agreeing with itself proves the scores are stable enough to count (Lance Martin, *Automating eval design and hillclimbing with Claude*).
- `skills/auditing-token-spend` — hooks are now measured alongside tools. Hook output is injected on every prompt, session start and edit, so the same amplification applies; the script reports them as `event script` in two tables that are **never summed** (`hook_additional_context` is confirmed to reach the model, `hook_success` stdout only for some events, so read it as an upper bound). Three new diagnosis rows: one hook dominating the table means trim or gate its output; a low cache-read share means something broke the prompt cache (the Claude Code cache-breakers are a long pause past the cache lifetime — **1h on a subscription, 5m on an API key or usage credits** — plus model switches, MCP servers connecting or disconnecting, effort changes through a gateway, first use of fast mode, and compaction; only the model switch is visible in transcripts); and large output on a small change means checking for retries before touching effort.
- `skills/context-optimization` — the documented 5-minute cache TTL is the **API** default; Claude Code sessions on a subscription get 1 hour. Points at `auditing-token-spend` Phase 3 for what breaks the cache inside Claude Code.
- `skills/model-tiers` — dropped the claim that this harness runs `high` effort: nothing sets `/effort` or `CLAUDE_CODE_EFFORT_LEVEL`, so every session runs the active model's default, and Opus 5.5 defaults to `medium` and thinks more at a given level than Opus 5 did — don't carry over a level tuned on an older model. Raise effort only when it saves a retry: ~20K extra thinking tokens cost about what a ten-turn retry loop does (Osmani, "What a task costs on Opus 5.5", 2026-09-25).
- `skills/synthesizing-daily-briefings` — added **Phase 2b, a stalled-work sweep**. Phase 2's sources report what moved; nothing reports what stopped moving, which is invisible by construction. The sweep uses `gh` to find the author's open PRs untouched for `STALL_DAYS` (default 3) and drafts still carrying the auto-created Evidence placeholder from `scripts/pr/detect_base_and_create.sh` (a fixed literal substring test, not a pattern). Results land in their own `## Stalled` section after P0/P1; each item is evidenced by its PR URL, so it passes the evidence rule as-is, and a sweep that could not run says `Stalled sweep skipped: <reason>` rather than silently omitting the section.
- `skills/gitnexus` — documentation pointer updated to `$SDD_HARNESS/docs/integrations/gitnexus/README.md`.
- `skills/claude-win11-speckit-update-skill` — stub replaced with real content: updates an existing GitHub SpecKit installation on Windows 11 without clobbering local customizations, via a hash-diff + 3-way merge against the upstream release (`/speckit-updater` with `--target`, `--dry-run`, `--force`, `--backup`, `--conflicts-only`) instead of a blind overwrite. Not for a fresh install. Caveat baked into the skill itself: upstream `github/spec-kit` was archived 2026-02-01, so behavior is verified only against the last pre-archival release — re-verify the flag set before trusting it if SpecKit resumes development elsewhere. Source: [NotMyself/claude-win11-speckit-update-skill](https://github.com/NotMyself/claude-win11-speckit-update-skill).
- `skills/fal-image-edit` — stub replaced with real content: editing an existing image via fal.ai models — style transfer, object removal/inpainting, instruction-based edits ("make the sky sunset-colored"). Not for text-to-image generation from scratch (different model family). Documents the `fal_client` Python SDK directly (`pip install fal-client`, `FAL_KEY` env var, `fal_client.upload_file()` before editing) rather than mirroring the upstream repo's original path, which no longer resolves after `fal-ai-community/skills` was restructured around a `genmedia` CLI.
- `skills/fal-platform` — stub replaced with real content: managing fal.ai **async job execution** directly — submit/status/result/cancel and webhook patterns for long-running requests (video generation, batch image jobs) where blocking on `fal_client.run()` isn't acceptable. Not for a specific model's request shape — see `fal-generate`/`fal-audio`/`fal-image-edit`/`fal-upscale` for those. Same `fal_client` SDK rewrite rationale as `fal-image-edit` (upstream path no longer resolves post-`genmedia` restructure).
- `skills/fal-upscale` — stub replaced with real content: increasing image/video resolution or enhancing quality via fal.ai models — upscaling, denoising, restoring low-res input. Not for generating new content from a prompt (`fal-generate`) or instruction-based edits to already-correct-resolution content (`fal-image-edit`). Same `fal_client` SDK direct-documentation rewrite as the other `fal-*` skills.
- `skills/infinite-gratitude` — stub replaced with real content: installs and runs the external `/infinite-gratitude` multi-agent research command, dispatching 5–10 parallel agents per wave for a research question wide enough to split into independent angles (competitor benchmarks, tool/library comparisons, literature reviews, due-diligence). Not for a single linear investigation path. Install is a third-party curl-to-shell script (`curl -fsSL .../install.sh | bash`) — verify the URL against the repo's README before running. Flags: `--agents N` (1–10, default 5), `--depth quick|normal|deep`, `--waves N` (default 3). Source: [sstklen/infinite-gratitude](https://github.com/sstklen/infinite-gratitude).
- `skills/makepad-skills` — stub replaced with real content: indexes 14 upstream `makepad-2.0-*` skills for cross-platform Rust UI work with Makepad 2.0 (DSL/layout syntax, widgets, events, shaders, animation, theming, 1.x→2.0 migration) so the right sub-skill loads instead of guessing. Not for Makepad 1.x (archived on upstream's `v1/makepad-1.0` branch, different APIs). Three install options (working-directory reference via `additionalWorkingDirectories`, symlink each sub-skill, or plain copy); `makepad-2.0-design-judgment` is the stated entry point to load first. Source: [ZhangHanDong/makepad-skills](https://github.com/ZhangHanDong/makepad-skills).
- `skills/nanobanana-ppt-skills` — stub replaced with real content: turns a document or outline into a styled PPT **image** deck (PNG/JPEG per slide, not editable `.pptx` text) via Google's Gemini image model ("Nano Banana") plus an optional Kling AI transition-video pipeline. Requires Python 3.8+, `GEMINI_API_KEY` (required), `KLING_ACCESS_KEY`/`KLING_SECRET_KEY` (optional, video only). Install clones into `~/.claude/skills/ppt-generator`, sets up a venv, and invokes via `/ppt-generator-pro`. Source: [op7418/NanoBanana-PPT-Skills](https://github.com/op7418/NanoBanana-PPT-Skills).
- `skills/skill-seekers` — stub replaced with real content: wraps the `skill-seekers` CLI (18 source types, 22 export targets) to turn a docs site, GitHub repo, PDF, video, or local codebase into a reusable AI skill/knowledge asset (for Claude, RAG, or another coding assistant) instead of re-reading the source every session. Not for a one-off lookup — setup cost exceeds just reading the source once. Core workflow: `skill-seekers create <url>` then `skill-seekers package output/<name> --target claude` → a zip ready for `~/.claude/skills/`. Source: [yusufkaraaslan/Skill_Seekers](https://github.com/yusufkaraaslan/Skill_Seekers).
- `skills/x-article-publisher-skill` — stub replaced with real content: converts a long-form article/blog post into an X/Twitter thread, or a single promotional post linking to the full article. Covers per-tweet character counting (280 chars before the `N/` prefix), thread numbering, hook-first opening, cliffhanger transitions, and a pre-return quality checklist (no orphaned endings, link appears exactly once in the final tweet). Source: [wshuyi/x-article-publisher-skill](https://github.com/wshuyi/x-article-publisher-skill).

---

## Jira Integration (Optional)

The harness includes an optional hook pair that automatically posts a Jira comment describing what was done every time you push code after a `jira-solve` session.

### How it works

1. **Ticket capture** (`UserPromptSubmit` hook — in `~/.claude/settings.json` globally):
   When you type `/kiro:jira-solve TICKET-ID`, the hook extracts the ticket ID and writes it to `~/.claude/state/active_jira_ticket`. This is a fire-and-forget async hook that never blocks Claude.

2. **Post-push comment** (`PostToolUse Bash` hook — in `.claude/settings.json`):
   After any `git push` command, the hook checks if a Jira session is active. If so, it calls `.claude/scripts/integrations/jira/jira_push_comment.py`, which:
   - Reads `origin/main..HEAD` git log and diff stats
   - Finds the most recently modified `.md` in `docs/` mentioning the ticket
   - Assembles a Jira wiki-markup comment (branch, commits, approach, files changed)
   - Posts it via `jira_client.py` and deletes the state file (single-fire)

### Issue routing

Before doing any work, `/kiro:jira-solve` applies a pre-gate: `Skill("issue-triage-routing")` runs against the fetched issue first. If it routes to **DEFER** (off-roadmap) or **CLARIFY** (ambiguity blocks a spec), that verdict is honored immediately — type-based routing below only runs once triage yields **SPEC** or **ONE-SHOT**.

Once past the pre-gate, routing is by issue type:
- Bug / Defect → systematic debugging workflow (`@agents-debug`)
- Story / Feature / Epic → `/kiro:spec-quick` seeded from Jira context
- Task / Sub-task / Improvement / Chore → direct implementation plan

Issue analysis itself is delegated to the `jira-solve-agent` subagent (see Subagents section), which converts the issue JSON into a structured problem statement and searches the repo for relevant files.

### Scripts

| Script | Location | Purpose |
|---|---|---|
| `jira_client.py` | `.claude/scripts/integrations/jira/jira_client.py` | Stdlib-only Jira REST API client (fetch/comment/search) |
| `jira_capture_ticket.py` | `.claude/scripts/integrations/jira/jira_capture_ticket.py` | Reads stdin JSON, extracts ticket ID from prompt, writes state file |
| `jira_push_comment.py` | `.claude/scripts/integrations/jira/jira_push_comment.py` | Builds and posts Jira comment from git context + docs |

### Credentials

Create `~/.env.jira` with PAT authentication (Jira Data Center):

```
JIRA_URL=https://your-jira-instance.example.com
JIRA_PAT=your-personal-access-token
```

Or Basic auth (Jira Cloud):

```
JIRA_URL=https://your-jira-instance.example.com
JIRA_USERNAME=your.email@example.com
JIRA_API_TOKEN=your-api-token
```

### Settings.json additions

**`~/.claude/settings.json`** (global — captures ticket on prompt submit):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python3 /path/to/repo/.claude/scripts/integrations/jira/jira_capture_ticket.py 2>/dev/null || true",
            "async": true
          }
        ]
      }
    ]
  }
}
```

**`.claude/settings.json`** (project — posts comment on git push):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "jq -r '.tool_input.command' | grep -q '^git push' && python3 /path/to/repo/.claude/scripts/integrations/jira/jira_push_comment.py /path/to/repo 2>/dev/null || true"
          }
        ]
      }
    ]
  }
}
```

> Replace `/path/to/repo` with the repo's absolute path. Merge with existing `PostToolUse` entries — do not replace the ruff lint hook. The merged file must stay strict JSON (no comments, nothing after the closing brace); confirm with `scripts/setup/check-settings-json.sh .claude/settings.json` and keep any explanatory notes in `.claude/settings.notes.md`.

### Usage

```
/kiro:jira-solve ZORAAI-1234     <- Claude captures the ticket ID
... work on the fix ...
git push                          <- hook fires, posts comment to ZORAAI-1234 automatically
```

The state file is deleted after the comment posts, so subsequent pushes won't double-post.

---

## AutoResearch (Optional — ML Experiment Automation)

The harness includes an optional autoresearch subsystem for autonomous ML experimentation, adapted from [karpathy/autoresearch](https://github.com/karpathy/autoresearch).

### What it does

An AI agent iterates on a training script (`train.py`) guided by a research brief (`program.md`). Each iteration: propose hypothesis → edit code → run 5-min experiment → evaluate metric → keep or revert. Overnight, it runs dozens of experiments autonomously.

### Components

| File | Location | Purpose |
|---|---|---|
| `autoresearch-init.md` | `commands/kiro/` | Interactive setup command — asks leading questions, generates project files |
| `autoresearch.md` | `commands/kiro/` | Experiment loop command |
| `autoresearch-init-agent.md` | `agents/kiro/` | Interview agent — 8 questions across 3 phases, then file generation |
| `autoresearch-agent.md` | `agents/kiro/` | Experiment loop agent — modifies `train.py`, runs experiments, evaluates |

### Prerequisites

- `uv` — Python package manager
  - Linux / macOS / WSL2: `curl -LsSf https://astral.sh/uv/install.sh | sh`
  - Windows: `powershell -ExecutionPolicy BypassPolicy -c "irm https://astral.sh/uv/install.ps1 | iex"`
- Python 3.10+
- GPU recommended (CPU works but slower)
- git initialized in the project (for experiment revert via `git checkout`)

### Setup & Usage

```bash
# 1. Interactive setup (generates program.md, train.py, prepare.py)
/kiro:autoresearch-init

# 2. Prepare data (one-time)
uv run prepare.py

# 3. Run the experiment loop
/kiro:autoresearch          # continuous
/kiro:autoresearch 10       # 10 iterations
```

No settings.json changes needed — these are manual-only commands with no hooks.

### Agent Recipe (Optional)

If `recipe.md` exists in the project root, `/kiro:autoresearch` passes it to the agent as additional context alongside `program.md`. It's a versioned artifact tracking the evolution of the research loop itself — not just current state but *why* decisions were made:

- **What we've tried** — dated log of experiments, results, and whether the change was kept or reverted
- **Signal filtering policy** — which result signals to act on vs. treat as noise, preventing "slop generation" (chasing metrics that don't represent real improvement)
- **Staged autonomy level** — 1 = human approves every change, 2 = human reviews batches, 3 = agent fully autonomous with daily review, plus the criteria for advancing a stage
- **What we've learned** — non-obvious findings from prior iterations

On first run, create a skeleton `recipe.md` with the initial signal-filtering policy and autonomy stage; the agent updates it after each batch of experiments. The inner loop (`/kiro:autoresearch` itself) optimizes `train.py`; the outer loop (`recipe.md` evolution) optimizes how the inner loop operates. (Source: Gavrilescu (2025) via Latent Space — "Autoresearch: The Feedback Loop Behind Self-Improving Agents".)

### CLAUDE.md additions

Add to your project's `CLAUDE.md` if using autoresearch:

```markdown
## AutoResearch
- `/kiro:autoresearch-init`  — interactive setup (generates program.md, train.py, prepare.py)
- `/kiro:autoresearch [N]`   — run experiment loop (N iterations or continuous)
- `program.md` is read-only during experiments
- `prepare.py` is read-only during experiments
- `train.py` is the only file the agent modifies
```

See `docs/research/autoresearch/README.md` for full details.

---

## GitNexus (Optional — Code Intelligence + Visual Explorer)

The harness includes an optional integration with [GitNexus](https://github.com/abhigyanpatwari/GitNexus) — a zero-server code intelligence engine that builds a knowledge graph from your codebase and exposes it via MCP tools. When present, harness agents gain graph-backed context; when absent, everything works as before.

### What it does

GitNexus indexes your codebase into a knowledge graph (symbols, dependencies, call chains, execution flows) using Tree-sitter AST parsing. It then exposes MCP tools for querying the graph and a Web UI for visual exploration.

Once set up, **everything is automatic** — no extra commands needed in your daily workflow:

1. **PreToolUse context enrichment** — Every file read/edit by any agent is enriched with 360-degree symbol context (callers, dependencies, process participation)
2. **Auto-reindex on commit** — Post-commit hook keeps the knowledge graph fresh after every commit
3. **Impact detection in verify pipeline** — Stage 0 maps git diffs to affected processes with risk scores
4. **Blast radius in spec-impl** — Before TDD, scans all files to be modified for downstream dependents
5. **Call chain tracing in debug** — Localize step queries GitNexus instead of manual grep
6. **Community-seeded skill extraction** — Leiden-detected functional clusters as extraction candidates
7. **Visual exploration** — `/kiro:gitnexus-explore` launches browser-based WebGL graph (the only manual command)

### Components

| File | Location | Purpose |
|---|---|---|
| `gitnexus-setup.md` | `commands/kiro/` | Install, index, configure MCP and editor integration. Does **not** hand-edit config — delegates MCP wiring and the `gitnexus setup` gate to `gitnexus-reconcile.sh` |
| `gitnexus-explore.md` | `commands/kiro/` | Launch Web UI to browse code connections |
| `gitnexus-impact.md` | `commands/kiro/` | Query blast radius for current changes |
| `gitnexus-setup-agent.md` | `agents/kiro/` | Setup agent — same six steps as the command, same delegation to `gitnexus-reconcile.sh` |
| `gitnexus-reconcile.sh` | `scripts/setup/` | The one writer of GitNexus config. `--wire` writes `.mcp.json` + enables the server in `.claude/settings.json` (idempotent, refuses an unparseable settings file); `--check` exits 0 only when index **and** MCP server both exist; no flag reconciles the managed block — in `CLAUDE.md`, or `AGENTS.md` for projects that relocated their conventions there — **compacting** it, repairing skill paths, and rewriting bare `gitnexus_*` tool names to the `mcp__gitnexus__*` form the MCP server exposes. Compaction swaps the ~900-token upstream body (every rule written twice, MCP and CLI, plus a resources table and a skill-path table the skill listing already carries) for the harness's compact rule set, keeping every MUST/NEVER rule and stating the CLI fallback once; the indexed repo name is carried over from the existing block (GitNexus's registry name is not always the directory name) and an unterminated block is left alone. Idempotent; `SDD_GITNEXUS_FULL_BLOCK=1` keeps the upstream block verbatim. Installed to projects as `.claude/scripts/setup/gitnexus-reconcile.sh` |

### Prerequisites

- Node.js 18+ (for `npx gitnexus`)
- npm (for global installation)
- Git initialized in the project

### Setup & Usage

```bash
# Option 1: Via Claude Code command (recommended)
/kiro:gitnexus-setup                    # Install, index, configure everything

# Option 2: During harness installation
$SDD_HARNESS/install.sh /path/to/project --with-gitnexus

# Option 3: Manual
npm install -g gitnexus
gitnexus analyze                                                   # index the repo
bash .claude/scripts/setup/gitnexus-reconcile.sh . --wire          # register the MCP server
bash .claude/scripts/setup/gitnexus-reconcile.sh . --check \
  && gitnexus setup \
  && bash .claude/scripts/setup/gitnexus-reconcile.sh .            # editor integration, gated
```

**What `/kiro:gitnexus-setup` (and `gitnexus-setup-agent`) does at Steps 3 and 5:** it no longer reads `.claude/settings.json` and merges an `mcpServers.gitnexus` block by hand. Step 3 runs `bash .claude/scripts/setup/gitnexus-reconcile.sh . --wire`, which writes the server to `.mcp.json`, enables it in `.claude/settings.json`, no-ops when the server is already configured in any scope, and leaves an unparseable settings file untouched. Step 5 gates `npx gitnexus setup` behind `gitnexus-reconcile.sh . --check` and re-runs the reconciler afterwards, because `gitnexus setup` writes a managed MUST/NEVER block into `CLAUDE.md` (or `AGENTS.md`, if that's where the project keeps its conventions) calling `gitnexus_*` tools — writing it before index and MCP server both exist leaves the agent under rules for tools it cannot call. The reconciler's live-block pass also repairs skill paths and rewrites any bare `gitnexus_*` tool names to the `mcp__gitnexus__*` form the MCP server actually exposes. If `--check` fails, the command skips Step 5 and reports which half is missing. The Step 6 report line therefore reads `MCP: [configured|already configured] in .mcp.json`, not `in .claude/settings.json`.

**What `install.sh --with-gitnexus` actually does now:** it wires the MCP server for real by calling `scripts/setup/gitnexus-reconcile.sh <project> --wire` (which writes the server into `.mcp.json` and adds it to `enabledMcpjsonServers` in `.claude/settings.json`), instead of the old behavior of printing a `NOTE:` telling the user to paste an `mcpServers` block into `.claude/settings.json` by hand. It then runs `gitnexus setup` **only** when `gitnexus-reconcile.sh <project> --check` confirms that both the index and the MCP server exist; otherwise `gitnexus setup` would write its managed MUST/NEVER `CLAUDE.md` block ordering the agent to call `gitnexus_*` tools that were never registered — which is exactly what happened on every install where nobody pasted the JSON. If the check fails, install prints `Skipped 'gitnexus setup' — index or MCP server missing.` and directs you to `/kiro:gitnexus-setup`. After a successful `gitnexus setup`, the reconciler runs once more to repair the managed block.

**`update.sh` reconciles the managed block on every sync.** The block is committed — to `CLAUDE.md`, or to `AGENTS.md` for projects that relocated their conventions there (`CLAUDE.md` wins on a tie) — but `.gitnexus/` is gitignored and the MCP server lives in local config, so a fresh clone inherits rules for tools it cannot call. `update.sh` runs `scripts/setup/gitnexus-reconcile.sh <project>` (non-fatal, `|| true`) to strip the block when it's dead and, when it's live, compact it, repair its skill paths and rewrite any bare `gitnexus_*` tool names to `mcp__gitnexus__*`; it no-ops for projects that never ran `gitnexus setup`.

### Using the Web UI

```bash
/kiro:gitnexus-explore                  # starts server + opens browser
# Or manually:
gitnexus serve                          # http://localhost:4567
```

The harness dashboard's **🕸 GitNexus** tab reaches the same UI differently: `gitnexus serve` answers only `/api/*` and 404s at `/`, so it is the API backend, not a web server for the UI. The tab therefore iframes the hosted app (`https://gitnexus.vercel.app/?repo=<name>`, which talks to `http://localhost:4747` by default) and probes `http://localhost:4747/api/repos` in real CORS mode to decide whether the backend is up — a `no-cors` probe of `/` returns an opaque response, so a 404 from a live server read as success. The iframe carries `allow="local-network-access"` because the hosted (https) app has to reach a `localhost` backend. The dashboard no longer proxies GitNexus through its own `/gn/` endpoint — the `_proxy_gitnexus` handler, its `/gn/` route, and the auto-repo-select script it injected are gone; the ports and URLs now live in one place at the top of `scripts/utils/dashboard.py` (`GN_PORT`, `GN_WEB_UI`, `GN_PROBE_URL`) and are substituted into the page JS as `__GN_WEB_UI__` / `__GN_PROBE_URL__`. Probe failures share one `gnOffline(message)` path, so an HTTP error surfaces as `GitNexus API returned HTTP <status>` instead of the generic "not running" text.

The Web UI lets you:
- Browse symbols (functions, classes, methods) in an interactive graph
- Trace call chains from entry points through dependencies
- Inspect process flows and which symbols participate
- View Leiden-detected community clusters (color-coded)
- Explore incoming/outgoing relationships with confidence scores

### Using impact analysis

```bash
/kiro:gitnexus-impact                   # analyze uncommitted changes
/kiro:gitnexus-impact --from HEAD~3     # analyze last 3 commits
```

### What's automatic after setup

Once GitNexus is set up, you don't need to run any extra commands. The following happen automatically:

| What | When | How |
|---|---|---|
| **Context enrichment** | Every file read/edit | PreToolUse hook injects callers, dependencies, processes |
| **Reindex** | Every git commit | Post-commit hook runs `gitnexus analyze --skip-embeddings` |
| **Impact detection** | Every `/kiro:verify` | Stage 0 maps diff to affected processes |
| **Blast radius scan** | Every `/kiro:spec-impl` | Scans files-to-modify for downstream dependents |
| **Call chain tracing** | Every `/kiro:debug` | Localize step queries GitNexus for call chains |
| **Community seeding** | Every `/kiro:skill-extract-scan` | Leiden clusters as extraction candidates |

All enhancements degrade gracefully — if GitNexus is removed, agents fall back to grep/glob.

### CLAUDE.md additions

Add to your project's `CLAUDE.md` if using GitNexus:

```markdown
## GitNexus
- `/kiro:gitnexus-setup`     — one-time setup (install, index, configure MCP)
- `/kiro:gitnexus-explore`   — launch Web UI to browse code connections
- `/kiro:gitnexus-impact`    — query blast radius for current changes
- `.gitnexus/` is gitignored and regenerable via `gitnexus analyze`
```

GitNexus also **writes its own block into `CLAUDE.md`**, fenced by `<!-- gitnexus:start -->` / `<!-- gitnexus:end -->` and regenerated on reindex — do not hand-edit inside the markers. The 2026-09-24 regeneration in this repo refreshed the symbol/relationship/flow counts, switched the staleness remedy to `node .gitnexus/run.cjs analyze --index-only`, added `bunx`/`pnpm dlx` bootstrap alternatives, paired every MCP call with its `node .gitnexus/run.cjs …` CLI fallback, flattened the skill paths to `.claude/skills/gitnexus-*/SKILL.md`, and added two rules worth knowing: `risk: UNKNOWN` is unresolved rather than low (an empty caller set can mean unresolvable callers — dynamic dispatch, plain-object property access, cross-language calls — so confirm by text search before treating a symbol as unused), and `partial: true` / `truncated: true` on `detect_changes` is not a clean check. The hand-written `## Blast Radius` pointer above it takes precedence, because this block states its own rule without knowing Serena or lean-ctx exist.

See `docs/integrations/gitnexus/README.md` for full details.

---

## Raindrop Workshop (Automatic — AI-Agent Tracing)

The harness integrates [Raindrop Workshop](https://raindrop.sh) — a local AI-agent debugger that captures every LLM call, tool invocation, and latency trace from your agents. All registered repos are auto-instrumented. Traces are free; the self-healing eval loop is user-triggered and costs tokens.

### What it does

Three integration layers:

1. **Workshop tab in the harness dashboard** — per-repo trace viewer, start/stop Workshop, trigger the eval loop
2. **Auto-instrumentation** — entry points in all registered repos emit traces with `event=repo-name` for per-repo filtering
3. **Self-healing eval loop** — reads traces → writes pytest assertions → runs → auto-fixes (max 3 cycles)

### Setup (automatic after CLI install)

`install.sh` and `update.sh` call `scripts/setup/raindrop-setup.sh` automatically. That script:
- Adds `RAINDROP_LOCAL_DEBUGGER=http://localhost:5899` to `~/.claude/settings.json` and `~/.bashrc` — **no repo `.env` files are touched**
- Installs `raindrop-ai` in each registered repo's detected virtualenv (`.venv/`, `venv/`, or `uv`-managed) through `repo_pip_install`, so the install carries the release-age cooldown from `scripts/lib/pip-cooldown.sh`: nothing uploaded within `SDD_PIP_MIN_AGE` (default `P2D`) is considered. `SDD_PIP_MIN_AGE=off` disables it for a same-day security fix.

The only manual step is installing the CLI binary (once, globally):

```bash
curl -fsSL https://raindrop.sh/install | bash
source ~/.bashrc
```

### Using the Workshop tab

```bash
# Start the harness dashboard
python3 $SDD_HARNESS/scripts/utils/dashboard.py

# In the browser: click Workshop → select repo → Start raindrop workshop
# Traces appear in real-time as agents run
# Click "Run Eval Loop" to trigger the self-healing cycle
```

### Instrumented repos

| Repo | Entry point | `event=` label |
|---|---|---|
| `aiq-zora-ai-engine` | `AgentPipelineGraph.process()` | `aiq-zora-ai-engine` |
| `aiq-zora-agent-skills` | `DailyNewsHandler.handle()` | `aiq-zora-agent-skills` |
| `aiq-purina-salesorderintelligence-poc` | `event_generator()` in `query_portal.py` | `aiq-purina-salesorderintelligence-poc` |

All instrumentation uses graceful fallback — if `raindrop-ai` is not installed, the code is a silent no-op.

### Adding a new repo

```bash
# Install harness into the new repo (wires raindrop automatically)
$SDD_HARNESS/install.sh /path/to/new-repo

# Or instrument an existing registered repo
/raindrop-instrument-agent
```

### CLAUDE.md additions

Add to your project's `CLAUDE.md` if using Raindrop Workshop:

```markdown
## Raindrop Workshop
- Traces fire automatically when agents run (free)
- View in harness dashboard → Workshop tab
- `event=repo-name` for per-repo filtering
- Run eval loop from dashboard (costs tokens — always manual)
```

See `docs/raindrop/README.md` for full details, instrumentation patterns, and troubleshooting.

---

## Impeccable (Optional — Frontend Design Quality)

The harness integrates [pbakaus/impeccable](https://github.com/pbakaus/impeccable) (25.6k ⭐) — a design quality system that catches the visual and functional flaws AI coding assistants routinely produce. Provides 27 deterministic anti-pattern rules + 12 LLM critique rules across 7 domains: typography, color, spatial design, motion, interaction, responsive, and UX writing.

### What it does

Three integration layers, each independent:

1. **`impeccable-audit` skill** (`~/.claude/skill-library/impeccable-audit/SKILL.md`) — On-demand 7-domain visual audit with PASS / NEEDS WORK / BLOCK verdict. Invoke as `/impeccable-audit` or with a focus area (`/impeccable-audit focus: motion`)
2. **`frontend-anti-patterns.md` rule** (`kiro/settings/rules/frontend-anti-patterns.md`) — Deterministic enforcement rules (AP-01 through UW-03) referenced by `/kiro:validate-design` and the adversarial agent when reviewing UI components
3. **`impeccable-detect-hook.sh`** (`.claude/hooks/impeccable-detect-hook.sh`) — PostToolUse hook that auto-scans frontend files on every Write/Edit and surfaces violations inline

### Prerequisites

```bash
npm install -g impeccable@3.6.0   # one-time, pinned; hook exits silently if not installed
```

Verify: `impeccable --version`

### Setup

The hook is already wired into `.claude/settings.json` via the PostToolUse entry above (Step 6). The skill and rules file are included in the harness and copied to every project by `update.sh`.

No `install.sh` or `setup-git-hooks.sh` changes are needed.

### Key anti-patterns flagged

| Code pattern | Rule ID | Problem |
|---|---|---|
| `background-clip: text` | AP-01 | Gradient text — AI fingerprint |
| `backdrop-filter: blur()` | AP-02 | Glassmorphism — dated, accessibility issues |
| `border-left: Npx solid accent` | AP-03 | Colored left border — AI fingerprint |
| `linear-gradient()` on hero/card bg | AP-04 | Gradient background — AI fingerprint |
| Card nested inside card | AP-05 | Spatial hierarchy collapse |
| 3-col identical card grid | AP-06 | Zero design intention |
| `background: #ffffff` | AP-07 | Pure white — use warm off-white |
| `ease-in` / `ease-out` | MO-01 | Stale easing — use `cubic-bezier` |
| No `:focus-visible` | IA-01 | Accessibility failure — BLOCK |
| Contrast < 4.5:1 | CO-01 | WCAG AA failure — BLOCK |

### CLAUDE.md additions

Add to your project's `CLAUDE.md` Quality Gates section:

```markdown
- impeccable detect: auto-runs on every frontend file write (requires `npm install -g impeccable@3.6.0`)
- `/impeccable-audit`: on-demand visual quality audit with PASS/NEEDS WORK/BLOCK verdict
```

### Transferring to a new repo

`install.sh` and `update.sh` propagate **every** hook in the harness's `hooks/claude/` directory to each project's `.claude/hooks/` unconditionally — including `impeccable-detect-hook.sh` — and sync `kiro/settings/rules/frontend-anti-patterns.md` and `docs/` automatically. The skill lives at `~/.claude/skill-library/impeccable-audit/` (global, not per-project). No manual copy step is needed:

```bash
$SDD_HARNESS/update.sh        # re-syncs hooks + rules + docs to every registered repo
# or, for a brand-new repo:
$SDD_HARNESS/install.sh /path/to/project
```

The PostToolUse wiring ships in `templates/settings.json.template` and is installed automatically (see Step 6 above for the equivalent manual entry).

See `docs/design/impeccable/impeccable.md` for the full rule set and workflow placement.

---

## Proof Collaborative Review (Built-in — Spec Phase Gates)

The harness uses [Proof](https://github.com/EveryInc/proof-sdk) (by Every Inc.) for human review gates at each SDD spec phase (`spec-requirements`, `spec-design`, `spec-tasks`). When a phase completes, the skill publishes the artifact to a live Proof document, presents a browser URL, and waits for your review before writing the approved version back.

### What it does

- Publishes the generated markdown artifact (requirements, design, task list) to a self-hosted Proof server
- Presents a URL — open in any browser to annotate, comment, or rewrite inline
- Waits for your "done" signal, then retrieves the final human-edited version
- Tears down the server only if the skill started it (PID-file guard)

### Where the skill lives

The skill ships with the harness — no separate installation needed:

```
$SDD_HARNESS/skills/proof-collaborative-review/SKILL.md   ← harness repo (replicated to all machines)
~/.claude/skill-library/proof-collaborative-review/SKILL.md               ← global (symlinked by install.sh)
```

`install.sh` propagates the skill to every project's skill lookup path automatically.

### Proof SDK (Node.js — auto-installed on first use)

The skill clones and installs the Proof SDK on first use into `~/.claude/tools/proof-sdk/`:

```bash
mkdir -p ~/.claude/tools
cd ~/.claude/tools
git clone https://github.com/EveryInc/proof-sdk
cd proof-sdk
npm install

# Editor bundle — without it the review page renders "Editor not built".
[ -f dist/assets/editor.js ] || npm run build

# The server serves static files only from public/, but dist/index.html
# asks for ./assets/editor.js — symlink the built assets across.
for f in dist/assets/*; do
  b=$(basename "$f")
  [ -e "public/assets/$b" ] || ln -s "../../dist/assets/$b" "public/assets/$b"
done
```

The skill detects `~/.claude/tools/proof-sdk/node_modules/` — if present, it skips the install. Never runs `npm install` twice.

The build and symlink steps were added 2026-09-30: a clone plus `npm install` alone yields a server that starts, accepts the document, and then serves a page that cannot edit it.

**Starting the server:** always `COLLAB_EMBEDDED_WS=1 npm run serve`. Without that flag collaboration runs on a second port and the session API tells the browser to dial `ws://localhost:4001`, so the document never loads on the URL the skill just printed.

**Publishing an artifact:** `POST /documents` takes a **`markdown`** field (a `content` field returns `MISSING_MARKDOWN`), and the share URL is `$PROOF_URL/d/<slug>` — not `/doc/<slug>`.

**Reading the result back:** Proof re-serializes markdown on the way out (`-` bullets become `*`, table separators get padded, some characters get backslash-escaped). Diff the normalized forms rather than the raw text, and when nothing but formatting changed, keep the original file — formatting-only differences are not edits.

**Prerequisites:** Node.js (already required by the harness, see Prerequisites above).

### Remote Proof server (optional)

By default the skill starts a local server at `http://localhost:4000`. To use a shared remote instance instead:

```bash
export PROOF_SERVER_URL=http://your-server:4000
```

Add to `~/.bashrc` or `~/.claude/settings.json` → `env` block to persist across sessions.

### CLAUDE.md additions

No CLAUDE.md changes needed — the skill is invoked automatically by the kiro spec commands at each phase gate.

---

## RTK (Automatic — Token Compression)

The harness includes a global integration with [RTK](https://github.com/rtk-ai/rtk) (Rust Token Killer) — a 6.6MB single-binary CLI proxy that intercepts Bash command output and compresses it before it enters the LLM context window. Typical reduction: 60–90% on development commands.

### What it does

A `PreToolUse` hook in `~/.claude/settings.json` runs `rtk hook claude` on every Bash tool call. When RTK has a filter for the command, the hook emits a rewrite directive (`permissionDecision: "allow"`) telling Claude Code to run `rtk <original>` instead. The proxy executes the command, applies the filter, and returns the compressed version. Everything is automatic — no commands to invoke, no per-project setup.

**Filters cover 100+ commands:** git (diff/status/log/add/commit/push/pull), pytest/cargo test/go test/jest/vitest/playwright/rspec/rake, ls/find/grep/diff/tree/wc, cargo build/check/clippy, tsc, eslint/ruff/mypy/golangci-lint/rubocop/prettier, docker, kubectl, aws, curl, gh, glab, psql, jq, npm/pnpm/pip/bundle/prisma.

### Installation

**All platforms** — use Homebrew (macOS and Linux):

```bash
brew install rtk
rtk init -g
```

`rtk init -g` writes the `PreToolUse` hook to `~/.claude/settings.json` once. All projects and sessions inherit it automatically.

**Linux without Homebrew:**

```bash
curl -fsSL https://rtk-ai.app/install.sh | sh
rtk init -g
```

**Windows (native):** RTK does not ship a native Windows binary. Use WSL2 — install WSL2 and run the Linux instructions above from inside it.

### Verifying it works

```bash
rtk --version    # should show rtk 0.42.0
rtk gain         # cumulative savings (starts at 0 on fresh install)
```

Preview how a command would be rewritten without running it:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | rtk hook claude
# Should print: {"hookSpecificOutput":{"permissionDecision":"allow","updatedInput":{"command":"rtk git status"},...}}
```

### Bypassing compression

When exact raw output is needed (debugging, piping to other tools):

```bash
rtk proxy <cmd>        # run command unfiltered (still tracked in rtk gain)
rtk hook check <cmd>   # dry-run preview of the rewrite decision
```

### CLAUDE.md additions

No CLAUDE.md changes needed — RTK is fully automatic and global.

See `docs/context/rtk/README.md` for full filter coverage, configuration, ultra-compact mode, and troubleshooting.

---

## Headroom (Automatic — Prompt/Context Compression Proxy)

The harness includes a global integration with [headroom-ai](https://github.com/headroom-ai/headroom) — a process-level proxy that compresses prompts/messages before they reach the Claude API (60–95% savings). It is complementary to RTK, not a replacement: RTK compresses Bash *command output* on the way into context; Headroom compresses the *prompt/message stream itself* at the proxy layer. Both can run at once.

### What it does

`scripts/setup/headroom-setup.sh` is idempotent and safe to re-run; it is called automatically by `install.sh` and `update.sh`. On each run it:

1. Installs `headroom-ai` globally — tries `uv tool install` (Python 3.12 first, for prebuilt wheels of the Rust/maturin extension), then `pipx`, then `pip install --user`, first one that works.
2. Installs `headroom-ai` into each registered repo's detected virtualenv (uv + `pyproject.toml`, or a discovered `.venv`/`venv`), for Python API use.
3. Installs Headroom as a persistent background service — a launchd LaunchAgent on macOS, a systemd user service on Linux — via `headroom install apply --preset persistent-service --memory`. This keeps the proxy warm across sessions (cold start ~10s → ~1s) and auto-starts on login. Skipped on unsupported OSes or if a service already reports healthy.
4. Wires Claude Code to route through the proxy durably (`headroom init --global --memory claude`, all shells + GUI) — but **only after** confirming the proxy is healthy via `curl http://127.0.0.1:${HEADROOM_PORT:-8787}/readyz`, so `ANTHROPIC_BASE_URL` is never pointed at a dead proxy.
5. Removes any legacy `alias claude='headroom wrap claude'` line from `~/.bashrc` / `~/.zshrc` left by older installs — that approach was bash-only (never loaded under macOS's default zsh) and conflicted with the persistent service over the same port.

Routing wired by step 4 does not self-repair if the proxy later dies (crash, machine restart racing launchd, a bad headroom upgrade) — headroom's own SessionStart hook re-asserts the same routing every session, so a hand edit doesn't stick. `hooks/claude/session-start-hook.sh` covers that gap on every session start via `scripts/utils/headroom-unwire-if-dead.py`: a bounded ~1.5s connect probe against the routed host, stripping `ANTHROPIC_BASE_URL` from `~/.claude/settings.json` when it refuses the connection (idempotent no-op on a healthy or unrouted proxy).

A companion script, `scripts/utils/sync-memories-to-headroom.py`, bidirectionally syncs harness markdown memories with Headroom's SQLite DB at session start when Headroom is installed.

### Verifying it works

```bash
headroom verify                                          # end-to-end proxy health check
curl -fsS http://127.0.0.1:8787/readyz                    # proxy readiness probe
headroom install status                                  # persistent service status
```

Savings and install status are visible in the harness dashboard's Headroom panel (`python3 scripts/utils/dashboard.py`), which reads `~/.headroom/proxy_savings.json`.

### CLAUDE.md additions

No CLAUDE.md changes needed — Headroom is fully automatic and global, wired the same way as RTK.

See `.claude/scripts/README.md` (Utilities section) for the canonical script description.

---

## Context Hub (Automatic API Documentation)

The harness includes [Context Hub](https://github.com/andrewyng/context-hub) as an MCP server. It provides a curated registry of LLM-optimized documentation for third-party libraries and APIs (OpenAI, Stripe, Anthropic, etc.) so agents use accurate, up-to-date API signatures instead of hallucinating from training data.

### How it works

Context Hub runs as an MCP server (`chub-mcp`) configured in `.claude/settings.json`. It exposes tools that Claude Code can call automatically when needed:

| MCP Tool | Purpose |
|---|---|
| `chub_search` | Search docs/skills by keyword |
| `chub_get` | Fetch doc content by ID (with language/version selection) |
| `chub_list` | List all available documentation entries |
| `chub_annotate` | Read/write persistent annotations on docs |
| `chub_feedback` | Rate doc quality to inform maintainers |

**No manual invocation needed.** Claude Code sees these tools automatically and calls them when it encounters unfamiliar APIs or needs accurate documentation for code generation.

### Prerequisites

- **Node.js** — for npx (already required by the harness)

The MCP server is configured in Step 6 (`settings.json`). No additional installation steps required — `npx -y @aisuite/chub-mcp` downloads and runs the server on demand.

### CLAUDE.md additions

Add to your project's `CLAUDE.md` Context Resources section:

```markdown
- Context Hub MCP tools (`chub_search`, `chub_get`) — available automatically for third-party API docs
```

---

## Detailed Documentation

Each harness subsystem has a detailed reference doc:

| Subsystem | Location | Contents |
|---|---|---|
| Kiro (SDD engine) | `docs/workflow/kiro/README.md` | All commands, agents, rules, templates, workflows |
| Cog Memory | `docs/memory/README.md` | Tier architecture, file formats, conventions, data flow |
| Jira Integration | `docs/integrations/jira/README.md` | Hook architecture, scripts, credentials, troubleshooting |
| AutoResearch | `docs/research/autoresearch/README.md` | Interview protocol, loop mechanics, agent behavior |
| Trust Battery | `docs/trust-battery/README.md` | Nightly Judge/Reflector loop, rubric, scoreboard, `auto-score` session success ratio (uncorrected sessions earn passive positive credit, read from `.claude/memory/.session-history`), opt-out, non-goals |
| RTK | `docs/context/rtk/README.md` | Token compression proxy — filter coverage, install, configuration, upgrading |
| Context Hub | [github.com/andrewyng/context-hub](https://github.com/andrewyng/context-hub) | MCP server for third-party API docs (external) |
| Design Quality | `docs/design/README.md` | Visual design quality integrations index |
| Impeccable | `docs/design/impeccable/impeccable.md` | 27 anti-pattern rules, skill usage, hook setup, transfer instructions |
| Proof Collaborative Review | `$SDD_HARNESS/skills/proof-collaborative-review/SKILL.md` | Spec phase-gate review sessions — Proof SDK setup, server lifecycle, API reference |
| Raindrop Workshop | `docs/raindrop/README.md` | AI-agent tracing — instrumented repos, eval loop, dashboard tab, troubleshooting |
| Headroom | `.claude/scripts/README.md` (Utilities) | Prompt/context compression proxy — install (global + per-repo venv), persistent service (launchd/systemd), durable Claude Code routing, `sync-memories-to-headroom.py` |
| Scheduled Tasks | `docs/scheduled-tasks/README.md` | All scheduled routines (daily maintenance, macro-eval, skill-curator, harness health, drift review); OS scheduler setup; dashboard **Scheduled Tasks** tab. The weekly skill-curator runs a Usage Evidence audit (Phase 1.5) over `logs/skill-usage.jsonl`, reporting deprecate candidates (no use in 30d) and archive candidates (90d); `pinned: true` skills are never flagged. A new Phase 1.6 — Dependency Cross-Reference — runs `scripts/utils/skill-dependency-scan.sh` *before* the prompt fires (deterministic `grep -rn -w` over other skills' SKILL.md bodies, hooks, agents, commands, CLAUDE.md, kiro rules, and routine scripts, capped at 8 referrers per skill), and `skill-curator-runner.sh` splices its output into the prompt's `DEPENDENCY_MAP_PLACEHOLDER` via a temp-file `sed` `r`/`d` insert (not a variable substitution, since referrer paths can contain `&`/`\`). Any skill that is BOTH a low-quality/cold candidate AND cross-referenced is surfaced in the report's mandatory `## Dependency Flags` section instead of being folded into a plain deletion candidate — the human-invoked `/skill-curator` skill treats that section as ground truth and requires **Delete + migrate references** (referrers updated first) rather than a bare delete. |
| Hooks Reference | `docs/hooks/README.md` | Complete hook documentation — event types, purpose, wiring reference for all active hooks |
| Local LLM Eval | `docs/evaluation/local-llm-eval/README.md` | Offline prompt evaluation with Ollama via OMT — multi-model comparison, variance testing, custom CLI runners (`--runner`) for non-Ollama models/agents, automated grading (`--checker`) |
| Structured Web Dataset | `docs/research/structured-web-dataset/README.md` | Building tabular datasets from NL descriptions — web research mode and synthetic mode |

---

## Troubleshooting: Doc-Sync Hooks That Block Claude Code

**Symptom**: Claude Code UI freezes or shows a new session spawned after every message. Session list floods with background agents.

**Root causes**:
1. A Stop hook calls `claude --print` — Stop fires on *every message completion*, not just true session end
2. The Stop hook uses `git diff HEAD~1` as a condition — this always returns output (even on a clean tree), so the condition always passes
3. The Stop hook watches `.claude/memory/` for changes — memory files are written every session

**Fix**: move all `claude --print` agents out of the Stop hook and into `.git/hooks/post-commit`:

```bash
# WRONG — Stop hook (fires every message)
# claude --print "..." &   ← never do this here

# RIGHT — post-commit hook (fires once per git commit), fully detached
# .git/hooks/post-commit
{
  timeout 900 claude --dangerously-skip-permissions --print "..." || true
} </dev/null >>"$REPO_ROOT/.git/post-commit-docsync.log" 2>&1 &
disown 2>/dev/null || true
```

**Rules to remember**:
- Never call `claude --print` from a Stop hook
- Never use `git diff HEAD~1` as a condition in Stop (always has output)
- Never watch `.claude/memory/` for mtime changes in Stop (written every session)
- Background doc agents belong in git hooks (commit-scoped) or manual slash commands
- Detach the git-hook job (`</dev/null`, output appended to a log, `disown`) so `git commit` returns immediately instead of waiting on the agent, and bound each `claude` call with `timeout 900` / `gtimeout 900` so a stuck agent cannot hold the terminal
- If the hook commits `.md` files itself, keep the guards narrow enough that the resulting `.md`-only commit fails them — otherwise the hook re-fires forever

The Stop hook should only contain **passive checks** (e.g., nudging housekeeping when observations exceed a threshold). See `.claude/hooks/stop-hook.sh` for the reference implementation.

_Last synced: 2026-10-01_

