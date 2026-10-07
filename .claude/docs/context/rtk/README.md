# RTK — Automatic Token Compression

> Global automatic integration that intercepts shell command output before it enters the LLM context window, reducing token consumption by 60–90%+ with no manual steps.

## What It Is

[RTK](https://github.com/rtk-ai/rtk) (Rust Token Killer) is a 6.6MB single-binary CLI proxy written in Rust. It sits between Claude Code's Bash tool and the shell: every time Claude runs `git diff`, `pytest`, `tsc`, or any other supported command, RTK intercepts the invocation, runs the command, filters the output through a multi-stage compression pipeline, and returns the compressed version.

**This happens automatically.** No commands to run, no skill to invoke, no per-project setup.

```
Before:                              After RTK:

Claude → git diff → shell            Claude → git diff → RTK → shell
^                       |            ^                   |         |
|    92,000 tokens      |            |    18,000 tokens  | filter  |
+───────────────────────+            +───────────────────+---------+
```

## How It Works

A `PreToolUse` hook in `~/.claude/settings.json` intercepts every Bash tool call:

```json
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [{ "type": "command", "command": "rtk hook claude" }]
      }
    ]
  }
}
```

When Claude runs `git diff HEAD~3`, the hook calls `rtk hook claude` with the JSON payload. If RTK has a filter for the command, it outputs a rewrite directive (with `permissionDecision: "allow"`) telling Claude Code to run `rtk git diff HEAD~3` instead. The proxy runs the original command, captures its output, applies the filter, and returns the compressed version. Claude sees only the compressed output.

Commands RTK doesn't recognize pass through untouched (exit 0, no output).

## Filter Coverage

RTK covers 100+ commands across all major development workflows:

| Category | Commands |
|---|---|
| **Git** | `git status`, `git diff`, `git log`, `git add`, `git commit`, `git push`, `git pull` |
| **Test runners** | `pytest`, `cargo test`, `jest`, `vitest`, `playwright`, `go test`, `rspec`, `rake` |
| **File ops** | `ls`, `find`, `grep`, `diff`, `tree`, `wc`, `read` |
| **Build tools** | `cargo build/check`, `go build`, `tsc` (via npx), `next build`, `dotnet build` |
| **Linters** | `eslint` (via npx), `ruff`, `mypy`, `cargo clippy`, `golangci-lint`, `rubocop`, `prettier` |
| **Containers** | `docker`, `kubectl` |
| **Cloud** | `aws` |
| **Package mgmt** | `npm`, `pnpm`, `pip`, `bundle`, `prisma` |
| **Data / APIs** | `gh`, `glab`, `curl`, `psql`, `jq` |

### What gets compressed

- **Git diffs** — metadata and excess context lines stripped, just the hunks
- **Test runners** — passing tests removed, only failures + summary kept
- **Directory listings** — permissions and inode noise removed, structure kept
- **Build/lint errors** — grouped by file/rule, verbose compiler output stripped
- **Log output** — duplicate lines deduplicated with counts

### What never gets touched

- Error messages (exit codes always preserved)
- Outputs under the noise threshold (not worth compressing)
- Commands without a registered filter (pass through unchanged)

## Viewing Savings

```bash
rtk gain              # cumulative token savings summary
rtk gain --history    # per-command breakdown
rtk gain --graph      # ASCII graph of daily savings
rtk gain -p           # filter to current project directory
rtk discover          # scan Claude Code history for missed opportunities
```

## Installation

**All platforms** — use Homebrew (macOS and Linux):

```bash
brew install rtk
rtk init -g
```

`rtk init -g` writes the `PreToolUse` hook to `~/.claude/settings.json`. All projects and sessions inherit it automatically.

**Linux without Homebrew:**

```bash
curl -fsSL https://rtk-ai.app/install.sh | sh
rtk init -g
```

**Verify:**

```bash
rtk --version           # should show rtk 0.42.0
rtk gain                # starts at 0 on fresh install
```

## Configuration

Config file location:
- **macOS**: `~/Library/Application Support/rtk/config.toml`
- **Linux**: `~/.config/rtk/config.toml`

Create or view: `rtk config`

```toml
[hooks]
# Commands RTK will NOT rewrite (exact prefix match)
exclude_commands = ["curl", "playwright"]

[tee]
# Save raw outputs alongside filtered versions
enabled = true
mode = "failures"  # "failures" | "always" | "never"
```

## Bypassing Compression

When exact raw output is needed:

```bash
rtk proxy <cmd>        # run command unfiltered (still tracked in rtk gain)
rtk hook check <cmd>   # preview how a command would be rewritten (dry-run)
```

Use `rtk proxy` for: piping output to other tools, debugging scripts that parse exact stdout, or comparing output before/after a change verbatim.

## Ultra-Compact Mode

Append `--ultra-compact` for extra compression (ASCII icons, inline format):

```bash
rtk --ultra-compact git status
rtk --ultra-compact pytest tests/
```

## Upgrading

```bash
brew upgrade rtk
rtk --version    # confirm new version
```

The global hook in `~/.claude/settings.json` does not need updating between versions.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `rtk: command not found` in hooks | Not installed or not in `$PATH` | `brew install rtk` |
| Permission dialogs on every Bash call | Stale legacy hook from a prior token-compression tool still present in `~/.claude/settings.json` | Remove the old hook entry and run `rtk init -g` |
| Hook not firing | `rtk hook claude` entry missing from `~/.claude/settings.json` | Run `rtk init -g` |
| Hook firing but no compression | Command not in RTK's filter list | Expected — passes through unchanged |
| Compressed output loses needed detail | Filter too aggressive for this command | Add to `exclude_commands` in config, or use `rtk proxy` |

Check hook behavior:

```bash
echo '{"tool_name":"Bash","tool_input":{"command":"git status"}}' | rtk hook claude
# Should output: {"hookSpecificOutput":{"permissionDecision":"allow","updatedInput":{"command":"rtk git status"},...}}
```
