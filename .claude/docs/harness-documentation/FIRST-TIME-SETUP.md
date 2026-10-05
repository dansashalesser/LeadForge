# First-Time Setup

> Use this when setting up the SDD harness on a new machine or for the first time.  
> Each section tells you what to check first — only run the install steps if the tool isn't already there.

---

## Platform Support

| Platform | Support level | Notes |
|---|---|---|
| **Linux** | Full | All tools supported natively |
| **macOS** | Full | RTK via Homebrew; rest identical to Linux |
| **Windows (WSL2)** | Full | Recommended for Windows — run all commands inside WSL2; identical to Linux |
| **Windows (native)** | Partial | npm/Node tools work natively; bash hooks require WSL2 or Git Bash |

**Windows recommendation**: Use WSL2. Install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) (`wsl --install` in PowerShell as Administrator), then follow the Linux instructions throughout this guide from inside a WSL2 terminal.

---

## Step 0: Verify Prerequisites

These must already exist. Check them first:

**Linux / macOS / WSL2:**
```bash
claude --version    # Claude Code CLI
node --version      # Node.js (needed for npx and npm installs)
git --version       # git
```

**Windows (PowerShell — if not using WSL2):**
```powershell
claude --version
node --version
git --version
```

If any are missing, install them before continuing.

> On Windows without WSL2: install [Node.js](https://nodejs.org) and [Git for Windows](https://git-scm.com/download/win). Claude Code CLI installation is the same (`npm install -g @anthropic-ai/claude-code` or via the installer).

---

## Step 0b: Power Tools (ripgrep, fd, jq)

These optional CLI tools dramatically improve Claude's file search speed and JSON parsing. When present, Claude uses them automatically instead of slower `find`/`grep`/`cat`.

| Tool | Install check | What it does |
|---|---|---|
| `rg` (ripgrep) | `which rg` | Fast recursive search; replaces grep |
| `fd` | `which fd` | Fast file finder; replaces find |
| `jq` | `which jq` | JSON parsing and filtering |

**macOS (Homebrew):**
```bash
brew install ripgrep fd jq
```

**Linux / WSL2 (apt):**
```bash
sudo apt-get install -y ripgrep fd-find jq
# fd-find installs as 'fdfind'; create an alias:
mkdir -p ~/.local/bin
ln -sf "$(which fdfind)" ~/.local/bin/fd
```

Skip with: `bootstrap.sh --skip-power-tools`

---

## Step 1: RTK (Token Compression)

RTK is the most impactful global tool — it compresses all Bash output by 60–90% before it reaches Claude.

### Check if already installed

```bash
which rtk && rtk --version
```

**If found** — just wire the global hook (one command):
```bash
rtk init -g --auto-patch
```

**If not found** — install via Homebrew (macOS, Linux, WSL2):

```bash
brew install rtk
rtk init -g --auto-patch
```

**Linux without Homebrew:**
```bash
curl -fsSL https://rtk-ai.app/install.sh | sh
rtk init -g --auto-patch
```

**Windows (native — no WSL2)**: RTK has no native Windows binary. Skip this step; token compression will not be active. To get it, install WSL2 and run the Linux instructions above inside it.

`rtk init -g` writes a `PreToolUse` hook to `~/.claude/settings.json`. All projects inherit it automatically.

**Verify:**
```bash
rtk gain    # shows cumulative savings (starts at 0 on fresh install)
```

---

## Step 2: Raindrop Workshop (Agent Tracing)

Raindrop Workshop is the harness's built-in AI-agent debugger. It captures every LLM call, tool invocation, and latency trace and streams them to `localhost:5899`. The harness auto-instruments all registered repos and exposes Workshop as a tab in the dashboard — **no per-repo `.env` changes needed**.

### Check if already installed

```bash
which raindrop && raindrop --version
```

**If found** — nothing else needed. Run `raindrop workshop` to confirm it starts.

**If not found** — install the CLI (global binary):

```bash
curl -fsSL https://raindrop.sh/install | bash
```

### Python SDK

The harness wires this automatically via `raindrop-setup.sh` during `install.sh` / `update.sh`. It:

- Installs `raindrop-ai` in each registered repo's virtualenv (`.venv/`, `venv/`, or `uv`-managed), with the `SDD_PIP_MIN_AGE` release-age cooldown (default `P2D`) from `scripts/lib/pip-cooldown.sh`
- Adds `RAINDROP_LOCAL_DEBUGGER=http://localhost:5899` to `~/.claude/settings.json` (Claude env)
- Adds the same export to `~/.bashrc` (shell env for user-run servers)

To run it manually (idempotent):

```bash
bash $SDD_HARNESS/scripts/raindrop-setup.sh
source ~/.bashrc
```

**Verify:**
```bash
# Workshop starts on port 5899
raindrop workshop &
# Open harness dashboard → Workshop tab
python3 $SDD_HARNESS/scripts/utils/dashboard.py
```

See `docs/raindrop/README.md` for full details on tracing, the eval loop, and troubleshooting.

---

## Step 3: GitNexus (Code Intelligence)

GitNexus is optional but recommended for large repos. It enriches file reads/edits with dependency and blast-radius context via a PreToolUse hook.

### Check if already installed

```bash
which gitnexus && gitnexus --version
```
or
```bash
npx gitnexus --version
```

**If found** — skip to per-project setup below.

**If not found:**
```bash
npm install -g gitnexus
```

### Per-project activation (run once per repo)

GitNexus requires indexing each repo separately. Either:

**Option A:** During `install.sh` (recommended for new projects):
```bash
$SDD_HARNESS/install.sh /path/to/project --with-gitnexus
```

**Option B:** After `install.sh`, inside Claude Code:
```
/kiro:gitnexus-setup
```

This indexes the repo (`.gitnexus/`), adds the MCP server to `.claude/settings.json`, updates `.gitignore`, and registers editor integration.

Option A now does the MCP wiring itself: `install.sh --with-gitnexus` calls `scripts/setup/gitnexus-reconcile.sh <project> --wire` instead of printing a note asking you to paste the `mcpServers` JSON by hand, and it only runs `gitnexus setup` once a `--check` confirms both the index and the MCP server exist. If that check fails it says so and sends you to Option B — that gate exists because `gitnexus setup` writes a MUST/NEVER block into `CLAUDE.md` (or `AGENTS.md`, if that's where the project keeps its conventions), and an unwired install left the agent ordered to call `gitnexus_*` tools that did not exist.

After `gitnexus setup` succeeds, the reconciler runs once more and **compacts** that block: the upstream version costs ~900 tokens on every API call (each rule written twice, in MCP and CLI form, plus tables the skill listing already carries), so it is replaced by the harness's compact rule set, which keeps every MUST/NEVER rule. Set `SDD_GITNEXUS_FULL_BLOCK=1` if you want the upstream block left verbatim.

---

## Step 4: impeccable (Frontend Design QA)

Automatically flags design anti-patterns when Claude writes frontend files (`.tsx`, `.jsx`, `.css`, etc.). The hook silently skips if the binary isn't installed — no errors, just no scans.

### Check if already installed

```bash
which impeccable && impeccable --version
```

**If found** — nothing else needed. The `impeccable-detect-hook.sh` is already in place after `install.sh` runs.

**If not found:**
```bash
npm install -g impeccable@3.6.0   # pinned — `install.sh` installs this same version via IMPECCABLE_VERSION
```

That's it — the hook in `.claude/hooks/impeccable-detect-hook.sh` picks it up automatically on the next frontend file write.

---

## Step 4b: Proof SDK (Spec Review Gate)

The Proof SDK powers the collaborative review sessions in `spec-requirements`, `spec-design`, and `spec-tasks`. The skill auto-installs it on first use — no manual setup required. This step is just a verify/preflight check.

### Check if already installed

```bash
ls ~/.claude/tools/proof-sdk/node_modules 2>/dev/null && echo "installed" || echo "not yet — will auto-install on first /kiro:spec-requirements"
```

**If not installed** — nothing to do. The `proof-collaborative-review` skill installs it automatically when you run any spec phase command for the first time:

```bash
cd ~/.claude/tools
git clone https://github.com/EveryInc/proof-sdk
cd proof-sdk
npm install
```

**Prerequisites:** Node.js (already verified in Step 0). No additional global tools needed.

### Remote server (optional)

If your team runs a shared Proof server instead of localhost, set:

```bash
export PROOF_SERVER_URL=http://your-server:4000
# Add to ~/.bashrc to persist
```

---

## Step 5: uv (Python Package Manager)

Required for autoresearch and any project using `uv`-managed Python environments.

### Check if already installed

**Linux / macOS / WSL2:**
```bash
which uv && uv --version
```

**Windows (PowerShell):**
```powershell
Get-Command uv -ErrorAction SilentlyContinue; uv --version
```

**If found** — nothing else needed.

**If not found:**

**Linux / macOS / WSL2:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows (PowerShell):**
```powershell
powershell -ExecutionPolicy BypassPolicy -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Or via winget:
```powershell
winget install --id=astral-sh.uv -e
```

---

## Step 6: Privacy Filter / opf (PII Scanning)

Optional. Blocks commits containing secrets, emails, account numbers, etc.

### Check if already installed

```bash
which opf
```

**If found** — nothing else needed (downloads model weights on first run, ~2.8GB to `~/.opf/`).

**If not found** (OPF is not on PyPI — install from OpenAI's GitHub source):
```bash
uv tool install --python 3.13 git+https://github.com/openai/privacy-filter.git
# or: pipx install --python python3.13 git+https://github.com/openai/privacy-filter.git
```

To wire as a pre-commit hook for a project:

**Linux / macOS / WSL2:**
```bash
# In your project root:
cat >> .git/hooks/pre-commit << 'EOF'
#!/bin/bash
bash "$(git rev-parse --show-toplevel)/.claude/hooks/scan-pii.sh" --staged
EOF
chmod +x .git/hooks/pre-commit
```

**Windows (Git Bash or PowerShell):**
```powershell
# In your project root (PowerShell):
$hookPath = ".git\hooks\pre-commit"
Add-Content $hookPath "#!/bin/bash"
Add-Content $hookPath 'bash "$(git rev-parse --show-toplevel)/.claude/hooks/scan-pii.sh" --staged'
# Git Bash handles the bash shebang; no chmod needed on Windows
```

---

## Step 7: Per-Project Install

Once global tools are in place, install the harness into each project:

> **`$SDD_HARNESS` on the very first run.** Every command below is written against `$SDD_HARNESS`, which `install.sh` and `update.sh` export and write into `~/.zshrc` / `~/.bashrc` (whichever exist) — appended on first run, rewritten in place afterwards, so it follows the clone if you move it. On the *first* invocation the variable does not exist yet, so bootstrap with a direct path to the clone (`bash /path/to/sdd-harness/install.sh /path/to/project`); open a new shell after that and `$SDD_HARNESS` resolves everywhere.

**Linux / macOS / WSL2:**
```bash
$SDD_HARNESS/install.sh /path/to/project
# or with GitNexus in one shot:
$SDD_HARNESS/install.sh /path/to/project --with-gitnexus
```

**Windows (native, no WSL2):** Run from Git Bash:
```bash
$SDD_HARNESS/install.sh /c/dev/my-project
```
Or use the WSL2 path if Claude Code is running inside WSL2. From PowerShell, invoke Git Bash with the call operator from inside the repo: `& "C:\Program Files\Git\bin\bash.exe" install.sh /c/dev/my-project` (running the `.sh` directly fails with a `#!`-shebang parser error, and `~/...install.sh` fails as "not recognized as a cmdlet").

**Install into all registered projects at once:** use `--all` to walk `projects.txt`, skipping any project already installed (`.claude/kiro/` present):
```bash
$SDD_HARNESS/install.sh --all                  # skip already-installed
$SDD_HARNESS/install.sh --all --force          # re-sync every project (push updates)
$SDD_HARNESS/install.sh --all --with-gitnexus  # batch install + GitNexus
```

`install.sh` propagates **every** hook in the harness's `hooks/` directory into the project's `.claude/hooks/` (and `chmod +x`'s them) — except `*.test.sh` files, which are harness-repo test suites for the hooks rather than runtime hooks and are skipped by the copy loop — syncs `docs/` into `.claude/docs/`, and generates a project stack summary. The harness is the source of truth — which hooks actually fire is governed by the project's `.claude/settings.json` wiring, not by which files are present. Re-run `update.sh` to re-sync after the harness changes.

`install.sh` validates `templates/settings.json.template` with `scripts/setup/check-settings-json.sh` before copying it; if the template is not strict JSON the copy is skipped and an error is printed rather than shipping a settings file Claude Code cannot parse. It also drops `.claude/settings.notes.md` (from `templates/settings.notes.md.template`) — the place for notes that cannot live inside `settings.json`, since JSON allows no comments and no content after the closing brace. Both `install.sh` and `update.sh` then run `scripts/setup/repair-settings-json.py` over the project, which peels a trailing `//` comment block out of an existing `settings.json` into that sidecar. It is idempotent, leaves valid files untouched, and reports (rather than guesses) when the breakage is something other than a trailing comment block.

Two things the copied `templates/settings.json.template` now brings with it. It sets **`cleanupPeriodDays: 365`** — Claude Code's default is 30 days, after which session transcripts under `~/.claude/projects/<slug>/` are deleted, and the harness's measurement stack (`token-forensics.py`, `rtk-net-effect.py`, `dashboard.py`, `herder.py`, `session-judge`, `detect_reexplanation.py`) reads those raw transcripts, so the default silently truncated its own evidence window with no error. `0` does **not** mean unlimited; it wipes immediately. It also registers `claudemd-edit-notice.sh` on `PostToolUse Write|Edit|MultiEdit` (in both templates), which warns that a `CLAUDE.md`/`AGENTS.md` edited mid-session is not loaded until `/compact`, `/clear`, or a restart. Opt out with `SDD_SKIP_CLAUDEMD_NOTICE=1`.

Before generating the harness repo's own `.claude/settings.json`, `install.sh` and `update.sh` both run `scripts/setup/reconcile-settings-templates.py --sync`. `templates/settings.json.template` is the source of truth for shared hooks and `templates/settings.harness.json.template` is derived from it plus an explicit harness-only allowlist, so the two cannot drift apart in the direction that matters — a hook firing in every installed project while not firing in the repo where it is written and tested. A failure there prints a warning and the install continues. Add a shared hook to the project template and run `--sync`; never edit the harness template directly.

Then, inside Claude Code in the project directory, run these once:

```
/kiro:steering         # scans codebase, generates steering/product.md, tech.md, structure.md
/codebase-legibility   # sets up CLAUDE.md hierarchy, .claudeignore, and codebase map
```

Daily maintenance runs automatically via the local OS scheduler (registered by `install.sh` / `update.sh`). No per-project setup is required. Registration is followed by a **preflight** that runs the orchestrator once under the scheduler's own environment — on macOS and Linux the install fails (exit 1) if that comes back non-zero, so a job that registers but cannot execute is reported at setup instead of doing nothing nightly. On macOS the most common cause is a harness under a TCC-protected folder (`~/Documents`, `~/Desktop`, `~/Downloads`), which launchd is refused access to; the preflight names it and gives both fixes.

Update `.gitignore` to exclude harness files. `install.sh` automatically adds the harness-local entries (`.claude/`, `specs/`, `CLAUDE.md`, `AGENTS.md`, `ERRORS.md`) under a `# SDD harness` header — skip those below if already present. The list is defined once as `SDD_GITIGNORE_ENTRIES` in `scripts/lib/project-gitignore.sh`, and `update.sh` calls the same `ensure_gitignore` on every sync (git repos only), so a project installed before an entry existed picks it up on its next update instead of needing a re-install. For the full recommended exclusion set:
```gitignore
CLAUDE.md
# Generated per machine, same class as CLAUDE.md: AGENTS.md by `lean-ctx setup`,
# ERRORS.md by the 2+-attempts logging rule
AGENTS.md
ERRORS.md
specs/
.claude/settings.json
.claude/.last-harness-check
.claude/hooks/
.claude/steering/
.claude/commands/
.claude/agents/
.claude/kiro/
# Per-user memory — stays local, never shared
.claude/memory/**
!.claude/memory/daily/
!.claude/memory/glacier/
!.claude/memory/meta/
!.claude/memory/.gitkeep
!.claude/memory/**/.gitkeep
.claude/docs/

# Serena symbol index — regenerable per machine via `serena project index`
.serena/

# Keep this one committed:
!.claude/settings.local.json
```

---

## Quick Checklist

Run through this on a fresh machine:

| Tool | Check (Linux/macOS/WSL2) | Install if missing | Activate |
|---|---|---|---|
| `rg`, `fd`, `jq` | `which rg && which fd && which jq` | macOS: `brew install ripgrep fd jq`; Linux/WSL2: `apt-get install ripgrep fd-find jq` (see Step 0b) | automatic |
| `rtk` | `which rtk` | macOS/Linux/WSL2: `brew install rtk`; Linux no-brew: `curl -fsSL https://rtk-ai.app/install.sh \| sh`; Windows native: requires WSL2 | `rtk init -g --auto-patch` |
| `raindrop` | `which raindrop` | `curl -fsSL https://raindrop.sh/install \| bash` (all platforms) | automatic via `install.sh`; see Step 2 |
| `gitnexus` | `which gitnexus` | `npm install -g gitnexus` (all platforms) | `/kiro:gitnexus-setup` per-project |
| `impeccable` | `which impeccable` | `npm install -g impeccable@3.6.0` (all platforms; version `install.sh` pins) | automatic via hook |
| `proof-sdk` | `ls ~/.claude/tools/proof-sdk/node_modules` | auto-installed on first spec phase run (requires Node.js) | automatic via skill |
| `uv` | `which uv` | Linux/macOS/WSL2: `curl -LsSf https://astral.sh/uv/install.sh \| sh`; Windows: see Step 5 | nothing extra |
| `opf` | `which opf` | `uv tool install --python 3.13 git+https://github.com/openai/privacy-filter.git` | wire pre-commit hook |
| harness | `ls $SDD_HARNESS/install.sh` | clone/copy harness | `install.sh /path/to/project` |
| caveman hooks | `ls ~/.claude/hooks/caveman-activate.js` | auto-installed from `hooks/global/` by `install.sh` (defaults to lite) | automatic via `install.sh` |
| lean-ctx | `which lean-ctx` | auto-wired into `~/.claude/settings.json` by `install.sh` if CLI detected | install CLI first, then run `install.sh` |

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `rtk: command not found` in hook | RTK not installed or not in PATH | `brew install rtk` |
| Workshop tab shows "not installed" | `raindrop` CLI missing | `curl -fsSL https://raindrop.sh/install \| bash` |
| No traces appearing in Workshop | `RAINDROP_LOCAL_DEBUGGER` not in env | Run `source ~/.bashrc`; or re-run `raindrop-setup.sh` |
| `raindrop-ai` import error at agent startup | SDK not installed in venv | `bash $SDD_HARNESS/scripts/raindrop-setup.sh` |
| Permission dialog on every Bash call | Stale legacy hook from a prior token-compression tool still present in `~/.claude/settings.json` | Remove the old hook entry and run `rtk init -g` to install the current `rtk hook claude` entry |
| Hook not firing at all | `rtk init -g` not run | Run `rtk init -g --auto-patch` |
| GitNexus context missing in Claude | MCP not in `settings.json` or repo not indexed | Run `/kiro:gitnexus-setup` |
| impeccable scans not appearing | Binary not in PATH | `npm install -g impeccable@3.6.0` |
| Local daily maintenance not running (macOS) | LaunchAgent not loaded | `launchctl list com.sdd.daily-orchestrator` to check; re-run `install.sh` or `update.sh` to re-register |
| Local daily maintenance not running (macOS) **while the LaunchAgent is loaded** | launchd holds no Full Disk Access, so it is refused at exec time (`Operation not permitted`, exit 126) when the harness lives under a TCC-protected folder — `~/Documents`, `~/Desktop`, `~/Downloads`. `launchctl list` shows the job as present the whole time | Run `bash $SDD_HARNESS/scripts/orchestration/setup-mac-orchestrator.sh --force`; its preflight reproduces the failure and names the cause. Fix by moving the harness somewhere unprotected (e.g. `~/GitHub/`) and re-running `install.sh`, or by granting Full Disk Access to `/bin/bash` in System Settings → Privacy & Security. The grant is per-machine and never travels with a clone |
| Harness cross-repo hooks stopped firing everywhere | `~/.sdd-harness-root` points at a directory that no longer exists — the harness was moved or renamed | Session start and session end now print `[HARNESS-POINTER-STALE]` naming the dead path. Re-run `bash <harness>/update.sh` from the new location to rewrite the pointer |
| A repo gets no scheduled routines and appears on no dashboard | It carries a harness install but was never added to `projects.txt`, and the dashboard only renders repos it is told about | `bash $SDD_HARNESS/scripts/utils/check-fleet-registration.sh` lists every such repo; add it to `projects.txt` or uninstall the harness there |
| Local daily maintenance not running (Linux) | Cron entry missing | `crontab -l \| grep sdd-daily` to check; re-run `install.sh` or `update.sh` to re-register |
| Local daily maintenance not running (WSL) | Task Scheduler entry missing | `schtasks.exe /Query /TN "SDD Daily Orchestrator"` to check; re-run `install.sh` to re-register |
| **Windows:** hooks fail with `bash: /bin/bash: No such file` | Claude Code running on native Windows; hook paths are Linux-style | Use WSL2 so Claude Code runs in Linux, or change hook commands from `/bin/bash` to the Git Bash path (`C:/Program Files/Git/bin/bash.exe`) |
| **Windows:** `uv` not found after install | PowerShell PATH not reloaded | Restart terminal or run `. $env:USERPROFILE\.cargo\env` (or reopen shell) |
| **Windows:** `install.sh` fails | Script requires bash | Run from Git Bash or WSL2, not PowerShell or CMD |
| `Settings file failed to parse: .claude/settings.json — Invalid or malformed JSON` (permission rules and hooks silently inactive) | Comments or notes after the closing brace — JSON allows neither. Installs before 2026-08-12 copied a template that carried a `//` block | `python3 $SDD_HARNESS/scripts/setup/repair-settings-json.py /path/to/project` moves the block to `.claude/settings.notes.md`; `update.sh` now does this automatically. Keep all notes in `settings.notes.md` |
| `headroom` proxy silently crash-loops (`Error: Deployment '<profile>' did not become ready after start`, launchd shows nonzero `last exit code` with no stdout/stderr) | A proxy dependency is missing from the running uv tool env (seen so far: `mcp`, `magika`) — the real error (`No module named 'X'`) is written only to `~/.headroom/deploy/<profile>/runner.log`, never to launchd's own logs | Check `~/.headroom/deploy/<profile>/runner.log` first, always — it has the real reason launchd's silence hides. Fix: `uv tool install "headroom-ai[proxy]" --python 3.12 --force`, then `headroom install apply --preset persistent-service --memory --profile <profile>`. headroom-setup.sh installs via the `[proxy]` extra (not a hand-maintained requirements list) precisely so this stops recurring release over release |
| `ANTHROPIC_BASE_URL` points at a dead headroom proxy and won't stay unset | headroom's own SessionStart hook (`headroom init hook ensure`) re-asserts routing every session, so editing `settings.json`/shell rc by hand doesn't stick | Nothing to do manually — `session-start-hook.sh` now probes the routed URL every session (`scripts/utils/headroom-unwire-if-dead.py`) and strips it automatically when the proxy is unreachable; routing resumes on its own once the proxy is healthy again. If a VS Code terminal specifically still shows the stale value after that, fully quit VS Code (Cmd+Q, not Reload Window) and relaunch — it caches resolved shell env once per app launch |

_Last synced: 2026-10-01_
