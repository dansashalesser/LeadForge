# Daily Security Report — 2026-10-04

## Summary
- Files scanned: 34 Python files (pattern sweep) + deep review of `dashboard.py`, `herder.py`
- Findings: 1 critical, 3 high, 2 medium
- Status: ISSUES FOUND

## Scope Note
`git log --since="25 hours ago"` returned **no commits**, and `HEAD~1` does not exist
(single-commit repo: `5b41741 Initial commit`). Tracked files are only `README.md` and
`.gitattributes` — no tracked source files. Per the "no git history" fallback, scope
became all source files in the working tree: 34 `.py` files under `.claude/`
(gitignored local harness scripts). No `.ts`/`.js`/`.go`/`.java`/`.rb`/`.rs` files exist.

Clean on all critical grep patterns across all 34 files: `shell=True` (0), `os.system` (0),
`os.popen` (0), `eval(`/`exec(` (0), `pickle.load` (0), `yaml.load(` (0), `verify=False` (0),
`DEBUG=True` (0), no hardcoded vendor credentials (`sk-`, `ghp_`, `AKIA`, `xox[bpas]-`,
`AIza`, Slack/Discord webhook URLs — 0 matches). `HERDER_TOKEN` is generated per-process via
`secrets.token_urlsafe(32)`; `runner_log_token` values are log labels, not credentials.

All findings are in one file: the local dashboard HTTP server.

## Findings

### [CRITICAL] Unauthenticated POST endpoint spawns a `bypassPermissions` agent with attacker-controlled prompt text
**File:** `.claude/scripts/utils/dashboard.py:6501` (handler) → `:6220` (`_run_skill_curator_apply`) → `:6246` (spawn)
**Pattern:** `do_POST` routes `/api/skill-curator-apply` without calling `_herder_guarded()`.
The `instruction` query parameter is read straight off the URL (`:6503`), interpolated into a
prompt (`:6233` — `## User Approval Instruction\n{instruction}`), and passed to:
`subprocess.Popen(["claude", "--print", "--permission-mode", "bypassPermissions", prompt], cwd=HARNESS_DIR, env={**os.environ, ...})`.
**Risk:** Drive-by remote code execution. The server listens on `127.0.0.1:4569` (`:6562`, `:70`).
A cross-origin `POST` with no custom headers and no body is a CORS *simple request*, so any
website the developer visits while the dashboard is running can fire
`fetch('http://127.0.0.1:4569/api/skill-curator-apply?instruction=<payload>', {method:'POST', mode:'no-cors'})`
with no preflight and no user interaction. The attacker controls a prompt segment handed to an
agent running with **permission checks disabled** and the full inherited environment — i.e.
arbitrary file write and command execution on the developer's machine. The attacker cannot read
the response (no CORS header), but does not need to; the side effect is the payload.
This is clearly an oversight rather than a design choice: the sibling herder endpoints implement
exactly this defense, and `_herder_authorized` (`:6259`) documents the cross-origin threat it blocks.
**Fix:** Add `if self._herder_guarded() is None: return` (or an equivalent token + Origin check) to
`/api/skill-curator-apply` before `_run_skill_curator_apply` is reached.

### [HIGH] PowerShell command injection via the `repo` query parameter
**File:** `.claude/scripts/utils/dashboard.py:6120` (`_start_gitnexus_serve`), reached from `:6471`
**Pattern:** `ps_cmd = f"Set-Location '{win_path}'; gitnexus serve"` is passed to
`subprocess.Popen(["powershell.exe", ..., "-Command", ps_cmd])`. `win_path` derives from the
unauthenticated `repo` query parameter via `_wsl_to_windows(repo_path)`. The only validation is
`Path(repo_path).is_dir()` (`:6474`) — a directory-existence check, not a content check.
**Risk:** A single quote in the path escapes the quoted `Set-Location` argument and appends
arbitrary PowerShell to the command string, executed outside the sandbox PowerShell applies to
argv-style spawns. Preconditions: the path must exist as a directory and resolve under
`/mnt/<drive>/`, so the attacker needs to influence a directory name (e.g. via a shared mount,
archive extraction, or a downloads folder) rather than inject a path freely. Reachable
unauthenticated through the same CSRF vector as the finding above.
**Fix:** Pass the directory via `cwd=repo_path` to an argv-list `Popen` instead of interpolating
into `-Command`, or reject paths containing `'`, `;`, `` ` `` and `$`.

### [HIGH] Unauthenticated POST endpoint spawns an agent with attacker-controlled cwd and prompt
**File:** `.claude/scripts/utils/dashboard.py:6486` (handler) → `:6155` (`_run_workshop_eval`)
**Pattern:** `/api/workshop-eval` has no auth guard. `repo` flows into both the prompt
(`:6160`) and `cwd=repo_path` (`:6167`) of `subprocess.Popen(["claude", "--print", prompt], ...)`.
**Risk:** Any visited website can start a Claude agent session in any existing directory on the
machine and inject text into its prompt through the path string. Lower impact than the critical
finding only because this spawn does not set `bypassPermissions`.
**Fix:** Apply the same `_herder_guarded()` check.

### [HIGH] Unauthenticated process- and agent-spawn endpoints (missing-auth class)
**File:** `.claude/scripts/utils/dashboard.py:6471`, `:6480`, `:6495`
**Pattern:** `/api/gitnexus-serve`, `/api/workshop-start`, and `/api/skill-curator-propose` are all
routed with no token or Origin check. `/api/skill-curator-propose` → `_run_skill_curator_propose`
(`:6176`) spawns `claude --print --permission-mode bypassPermissions` (`:6197`); its prompt is
static, so there is no injection, but the trigger is still unauthenticated.
`/api/gitnexus-serve` also runs `pkill -f "gitnexus serve"` (`:6114`).
**Risk:** Cross-origin pages can repeatedly spawn `bypassPermissions` agents and long-running
servers, kill local processes, and cause the curator agent to rewrite `~/.claude/skills/`
unprompted — unauthorized state change plus resource exhaustion.
**Fix:** Guard every `do_POST`/`do_GET` branch by default; make the guard the first statement in
both dispatchers rather than opt-in per route.

### [MEDIUM] Raw exception strings returned to the HTTP client
**File:** `.claude/scripts/utils/dashboard.py:6375`, `:6383`, `:6392`, `:6411`, `:6420`, `:6433`, `:6525`, `:6536`, `:6546`
**Pattern:** `self._send_json({"error": str(exc)}, 500)` across the herder routes.
**Risk:** Leaks absolute filesystem paths, repo layout, and internal failure detail. Contained by
the token guard on these specific routes, so disclosure requires the token first.
**Fix:** Return a generic message; log the detail server-side.

### [MEDIUM] Unauthenticated proxy to the local Workshop service
**File:** `.claude/scripts/utils/dashboard.py:6434` → `:6439` (`_proxy_workshop`)
**Pattern:** `/workshop/*` proxies to `http://localhost:5899` with `ws_path = parsed.path[9:]`,
no guard.
**Risk:** Not SSRF — host and port are hardcoded, so the request cannot be redirected to an
arbitrary target. But any page can reach arbitrary paths on a local service that is otherwise
only bound to loopback, and `parsed.path` is not normalized before being appended. Responses
carry no CORS header, so a cross-origin caller triggers without reading.
**Fix:** Guard the route and normalize/allowlist the proxied path prefix.

## Dependency Changes
None — no `requirements.txt`, `package.json`, `go.mod`, or lockfile was in scope, and no
dependency file changed (no commits in the window).

## Clean Files
No issues found in the other 33 files: `validate-behavior-spec.py`, `skill-quality-scan.py`,
`skill-listing-budget.py`, `skill-eval-staleness.py`, `setup/fix-inert-write-rules.py`,
`setup/repair-settings-json.py`, `setup/declare-repo-deps.py`, `setup/gitnexus-mcp.py`,
`setup/reconcile-settings-templates.py`, `utils/sync-memories-to-headroom.py`,
`utils/check-no-regex.py`, `utils/ollama_model_test.py`, `utils/hook-config-audit.py`,
`utils/token-forensics.py`, `utils/rtk-net-effect.py`, `utils/headroom-unwire-if-dead.py`,
`utils/remap-claude-json-paths.py`, `utils/herder.py`, `pr/validate_review_json.py`,
`integrations/jira/jira_push_comment.py`, `integrations/jira/jira_client.py`,
`integrations/jira/jira_capture_ticket.py`, `integrations/blackhole/blackhole-cursor.py`,
`integrations/channels/notify.py`, `session/_keep_rate_delta.py`, `session/record_metric.py`,
`session/trust_score.py`, `session/detect_reexplanation.py`, `session/_archive_obs.py`,
`session/write_handoff.py`, `session/_keep_rate_classified.py`, `session/_keep_rate_calc.py`,
`session/micro_reflect.py`.

`herder.py` was reviewed specifically for path traversal in `read_agent`/`agent_stream`/
`prompt_agent` (`:949`, `:1075`, `:752`) since they take a user-supplied `name`: all three pass it
as an argv element to `herdr` with no shell and no `open()` on a constructed path. No finding.

## Notes
- Severity reflects a **developer-workstation** threat model: the dashboard binds loopback only, so
  nothing here is internet-exposed. The realistic attacker is any webpage open in the developer's
  browser while `dashboard.py` is running. CSRF against loopback services is the whole attack
  surface, and the `bypassPermissions` spawn is what turns it from nuisance into RCE.
- All six findings are the same root cause: the auth guard is opt-in per route, and six routes
  opted out. A default-deny dispatcher would close all of them at once.
- These files are gitignored harness scripts, not shipped product code. That lowers blast radius
  to this machine but does not reduce the severity of the critical finding, which executes here.
- Scanned per the routine's read-only scope; no code was modified. Fixes above are recommendations
  only.
