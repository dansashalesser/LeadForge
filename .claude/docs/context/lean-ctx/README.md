# lean-ctx — Token-Efficient File Reading & Code Analysis

> MCP server that compresses file-read and code-analysis token costs. Works alongside RTK (which handles shell output). Together they cover the full context surface: RTK owns Bash outputs, lean-ctx owns file reads and AST analysis.

## What It Is

[lean-ctx](https://github.com/yvgude/lean-ctx) is a single Rust binary that runs as a persistent MCP server, exposing 83 `ctx_*` tools to Claude Code (17 advertised in the schema under the `standard` profile; the rest reachable via `ctx_call`). Its core value is **smart file reading** — a `ctx_read(path, "signatures")` call returns only exported symbols and types instead of the full file, at a fraction of the token cost.

## Tool Profile

Profile `standard` advertises 17 of 83 tools. Everything else is reachable via
`ctx_call {"name":"<tool>","arguments":{...}}`. List names with
`ctx_call {"name":"ctx_discover_tools","arguments":{"query":""}}`.
Switch profiles with `lean-ctx tools <minimal|lean|standard|power>`.

```
Before:                                After lean-ctx:

Claude → Read(auth.ts)                 Claude → ctx_read("auth.ts", "signatures")
^                        |             ^                                           |
|    12,000 tokens       |             |    ~400 tokens (exports + types only)    |
+────────────────────────+             +───────────────────────────────────────────+
```

Re-reads of files already in the cache cost ~13 tokens regardless of file size.

## Relationship to RTK

| Layer | Tool | Approach | Automation |
|---|---|---|---|
| Bash output | **RTK** | PreToolUse hook rewrites shell commands | Fully automatic |
| File reads | **lean-ctx** | MCP tools replace Read/Grep | Claude chooses intentionally |
| Code analysis | **lean-ctx** | ctx_graph / ctx_search(action="symbol") / ctx_callgraph | Claude chooses intentionally |

They are complementary, not a choice between them: RTK's hook compresses raw shell output automatically for any command, and `ctx_shell` adds pattern-specific compression on top. Use `ctx_shell` per the tool-mapping table in `rules/lean-ctx.md` — RTK running underneath is not a reason to skip it.

## Core Tools

### Read Surface

| Tool | Description |
|---|---|
| `ctx_read(path, mode)` | Smart read with 10 modes (see below) |
| `ctx_read(paths=[...], mode)` | Batch read multiple files with one mode |
| `ctx_read(path)` | Auto-selects mode based on task context (mode omitted) |
| `ctx_tree(path, depth)` | Compact directory map |
| `ctx_search(pattern, path)` | Token-efficient grep |
| `ctx_call {"name":"ctx_outline","arguments":{"path":"..."}}` | File structure overview |

### Code Analysis

| Tool | Description |
|---|---|
| `ctx_graph(action, path)` | Dependency graph, impact analysis, diagrams |
| `ctx_search(action="symbol", name=...)` | Definition + all call sites for a symbol |
| `ctx_callgraph(symbol, direction)` | Callers or callees of a function |
| `ctx_delta(path)` | Diff since last read (for re-checks after edits) |
| `ctx_call {"name":"ctx_routes","arguments":{"path":"..."}}` | API route discovery |

## Read Modes

| Mode | Use when | Pruning analogy |
|---|---|---|
| `auto` | Unsure — system picks optimal | — |
| `full` | About to edit this file | No pruning |
| `map` | Need exports + deps, not implementation | Chunk-level (logical unit boundaries) |
| `signatures` | API surface only (functions, types, interfaces) | Chunk-level — best for code files |
| `diff` | Re-reading after an edit | Delta pruning |
| `aggressive` | Large file, context only, need max compression | Token-level — highest compression ratio |
| `entropy` | Highlight high-signal / high-entropy fragments | Attention-mimicking — scores by information density |
| `task` | Active task set defined — filters to task-relevant content | Query-aware — best precision for accuracy-critical work |
| `reference` | Quote-friendly minimal excerpts | Sentence-level — best for prose and documentation |
| `lines:N-M` | Specific line range (e.g. `lines:40-80`) | Manual range selection |

**Decision guide** (from [Redis context pruning research](https://redis.io/blog/context-pruning-llm-tokens/)):
- **Code files** → `signatures` or `map`. Chunk-level beats token-level for code — broken syntax is worse than verbosity.
- **Prose / docs** → `reference`. Sentence-level: keeps topic sentences, drops elaboration.
- **Accuracy-critical tasks** → `task`. Query-aware filtering preserves only what's relevant to the current question.
- **Unknown or mixed content** → `aggressive` or `entropy`. Entropy mode mimics attention-head scoring without requiring model internals access.
- **Re-reading after edit** → `diff` or `ctx_delta`. Pays ~13 tokens regardless of file size.

## Installation

```bash
# Recommended
curl -fsSL https://leanctx.com/install.sh | sh

# Or via cargo
cargo install lean-ctx

# Or via npm
npm install -g lean-ctx-bin
```

### MCP Registration

lean-ctx is registered via the Claude Code CLI:

```bash
claude mcp add lean-ctx lean-ctx -- --mcp
```

Verify with `claude mcp list`.

### Shell Hook (optional — skip if RTK is installed)

```bash
lean-ctx setup --shell
```

This installs a `~/.zshenv` interceptor for `ctx_shell`. **Not needed** — RTK's PreToolUse hook already provides superior shell compression automatically.

## ctx_graph vs gitnexus

| | ctx_graph | gitnexus |
|---|---|---|
| Scope | File-level, symbol-level, within-session | Repo-level, cross-repo, cross-session |
| What it answers | "What calls X?", "What imports Y?", "What breaks if Z changes?" | "How are these repos related?", "What's the overall architecture?" |
| Interface | MCP tool call | Browser-based dashboard (served at localhost:4747) |
| Best for | In-session code analysis while coding | High-level exploration and repo mapping |

Use `ctx_graph` for impact analysis during a coding task. Use gitnexus for architectural overview and repository visualization.

## Upgrading

```bash
# Re-run the installer (replaces the binary in place)
curl -fsSL https://leanctx.com/install.sh | sh

# Or via cargo
cargo install lean-ctx --force

# Verify
lean-ctx --version
```

The MCP server registration in `settings.json` does not need to change between versions.

## Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| `ctx_*` tools not available in Claude | lean-ctx not registered as MCP server | Add to `mcpServers` in `~/.claude/settings.json` |
| `lean-ctx: command not found` | Binary not in PATH | Ensure install dir is in `$PATH` |
| MCP server fails to start | lean-ctx not installed | Run the installer first |
| `ctx_shell` and RTK both active | Double-processing | Disable lean-ctx's shell hook; rely on RTK |
