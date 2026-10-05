# lean-ctx — Context Engineering Layer
<!-- lean-ctx-rules-v11 -->

## Tool Mapping
The lean-ctx MCP server states the full native→`ctx_*` mapping in its own `instructions`
block, which loads wherever the server does. It is not repeated here — a second copy costs
context on every session and creates a second thing to keep in sync.

The one fact the server cannot tell you: **native Grep and Glob are denied by policy in
this harness**, so the mapping is not a preference for those two. Native `Read` stays
available for the read-before-write edit gate and for `~/.claude/projects/<slug>/memory/`.

## Profile: `standard` (17 advertised tools)
Advertised: ctx_callgraph, ctx_compose, ctx_delta, ctx_execute, ctx_expand, ctx_explore,
ctx_glob, ctx_graph, ctx_knowledge, ctx_overview, ctx_patch, ctx_read, ctx_search,
ctx_session, ctx_shell, ctx_tree, ctx_url_read.
The other ~66 tools are trimmed from the schema but still exist. Reach them via `ctx_call`:
`ctx_call {"name":"ctx_impact","arguments":{"action":"analyze"}}`
Find names with `ctx_call {"name":"ctx_discover_tools","arguments":{"query":"keyword"}}`.
Deprecated — do not use: `ctx_semantic_search`, `ctx_symbol`, `ctx_multi_read`, `ctx_smart_read`.
The bare `shell` alias was removed — use `ctx_shell`.

## ctx_read Mode Selection
| Goal | Mode | When |
|------|------|------|
| Edit this file | `full` | Before any edit |
| Understand API | `signatures` | Context-only, won't edit |
| Re-read after edit | `diff` | Post-edit verification |
| Large file overview | `map` | >500 lines, won't edit |
| Specific region | `lines:N-M` | Know exact location |
| Unsure | `auto` | System selects optimal mode |

## Workflow (follow this order)
1. **Orient:** `ctx_overview(task)` or `ctx_compose(task, path)` for unfamiliar tasks
2. **Locate:** `ctx_search(pattern, path)` for exact text; `ctx_search(action="semantic", query=...)` for concepts
3. **Read:** `ctx_read(path, mode)` with appropriate mode from table above
4. **Edit:** `ctx_patch` after `ctx_read(mode="anchored")`, or native Edit if available
5. **Verify:** `ctx_read(path, "diff")` + `ctx_shell("test command")`
   - `.py` files: also run `mcp__serena__get_diagnostics_for_file(path)` — catches type errors and lint issues the shell won't
6. **Record:** `ctx_knowledge(action="remember", content="...")` for non-obvious findings

## Proactive (use without being asked)
- `ctx_overview(task)` — at session start for orientation
- `ctx_knowledge(action="wakeup")` — at session start to surface prior findings
- `ctx_call {"name":"ctx_compress","arguments":{}}` — when context grows large (at phase boundaries)

## Compression Bypass (only when compressed output hides needed detail)
`ctx_read(path, "lines:N-M")` → `ctx_read(path, "full")` → `ctx_shell(cmd, raw=true)`
Return to compressed defaults after one expanded retrieval.

## Risk Gate (before high-impact edits)
One check, chosen by what you are editing — not three. Run the first row that applies:

| Editing | Run | If it fails |
|---|---|---|
| A Python function/class | `mcp__serena__find_referencing_symbols(symbol)` — LSP-accurate, authoritative | fall to the row below |
| Any other symbol | `mcp__gitnexus__impact({target, direction:"upstream"})` | fall to the row below |
| Index broken/stale, or a non-symbol edit (auth, DB schema, 3+ files) | `ctx_callgraph(action="callers")`, plus `ctx_graph` for file-level deps | say the blast radius is unknown |

Report HIGH/CRITICAL risk instead of proceeding silently. A tool that errors or reports a
version mismatch has given you **no answer** — it has not told you there are no callers.
Say so rather than treating silence as safety.

## Session
- **Start:** `ctx_session(action="status")` + `ctx_knowledge(action="wakeup")`
- **End:** `ctx_session(action="decision", content="what was done + next steps")`
- **On [CHECKPOINT]:** `ctx_session(action="task", value="current status")`

Prefer ctx_* over native Read/Grep/Shell/Glob. Exceptions: native Read for the edit gate
(read-before-write) and for `~/.claude/projects/<slug>/memory/` files.
<!-- /lean-ctx -->