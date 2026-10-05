# Raindrop Workshop

> Reference doc for the Raindrop Workshop AI-agent tracing integration.
> Source of truth: `$SDD_HARNESS/docs/raindrop/README.md`

Raindrop Workshop is a local AI-agent debugger that captures every LLM call, tool invocation, and latency trace from your agents and displays them in a browser UI at `localhost:5899`. The harness auto-instruments all registered repos and exposes Workshop as a dedicated tab in the harness dashboard.

---

## What gets traced

| Repo | Entry point | `event=` label |
|---|---|---|
| `aiq-zora-ai-engine` | `AgentPipelineGraph.process()` | `aiq-zora-ai-engine` |
| `aiq-zora-agent-skills` | `DailyNewsHandler.handle()` | `aiq-zora-agent-skills` |
| `aiq-purina-salesorderintelligence-poc` | `event_generator()` in `query_portal.py` | `aiq-purina-salesorderintelligence-poc` |

Capturing traces is **free** — no tokens consumed. The self-healing eval loop costs ~5k-30k tokens per cycle and is always user-triggered.

---

## Setup (automatic via harness)

`install.sh` and `update.sh` both run `scripts/setup/raindrop-setup.sh` automatically. That script:

1. Adds `RAINDROP_LOCAL_DEBUGGER=http://localhost:5899` to `~/.claude/settings.json` env so all Claude-spawned subprocesses pick it up.
2. Adds the same export to `~/.bashrc` so all interactive shell sessions (where you run `uvicorn`, `python`, etc.) inherit it.
3. Detects each registered repo's virtualenv (`.venv/`, `venv/`, or `uv`-managed) and installs `raindrop-ai` inside it via `repo_pip_install` (`scripts/lib/repo-venv.sh`). That install carries the release-age cooldown from `scripts/lib/pip-cooldown.sh` on both its pip and `uv pip` paths: a release uploaded within `SDD_PIP_MIN_AGE` (ISO 8601 duration, default `P2D`) is not considered, since `raindrop-ai` is installed unpinned. Set `SDD_PIP_MIN_AGE=off` when you need a same-day release. A pip too old for `--uploaded-prior-to` installs without the cooldown and says so on stderr.

**One additional manual step** — install the Raindrop CLI itself (global binary, not Python):

```bash
curl -fsSL https://raindrop.sh/install | bash
```

Verify:

```bash
raindrop --version
```

---

## Using the Workshop tab

1. Open the harness dashboard (`python3 $SDD_HARNESS/scripts/utils/dashboard.py`)
2. Click **Workshop** in the sidebar
3. Select a repo from the repo selector
4. Click **Start raindrop workshop** to launch `raindrop workshop` on port 5899
5. The Workshop UI proxies through the dashboard at `/workshop/`
6. Filter by repo using the `event=` label in the Workshop UI sidebar
7. Click **Run Eval Loop** to trigger the self-healing eval cycle (costs tokens — always user-triggered)

---

## Self-Healing Eval Loop

Manually triggered from the dashboard. It:

1. Reads the 10 most recent traces for the selected repo via `GET http://localhost:5899/api/events?event=REPO_NAME&limit=10`
2. Writes `pytest` assertions covering observed behavior patterns
3. Runs the test suite
4. Auto-fixes failures (max 3 cycles)
5. Produces a report

Token cost estimate: ~5k tokens (small trace set) to ~30k tokens (complex multi-tool traces with 3 fix cycles).

Skill reference: `~/.claude/skill-library/raindrop-eval-loop/SKILL.md`

---

## Instrumentation details

All instrumentation is injected at the outermost agent entry point with a graceful fallback — if `raindrop-ai` is not installed, the entire path is a silent no-op:

```python
try:
    import raindrop.analytics as _raindrop
    _raindrop.init(api_key=None, tracing_enabled=True, bypass_otel_for_tools=True, auto_instrument=False)
    _RAINDROP_ENABLED = True
except (ImportError, Exception):
    _RAINDROP_ENABLED = False
    _raindrop = None
```

### aiq-zora-ai-engine

Uses a decorator to avoid re-indenting the 7-return-path `process()` body:

- File: `engine/core/pipeline.py` — `AgentPipelineGraph` class
- Pattern: `@_raindrop_trace("aiq-zora-ai-engine")` on `async def process(self, user_message, ...)`

### aiq-zora-agent-skills

Inline wrap of `DailyNewsHandler.handle()`:

- File: `zora_skills/product/cfo-insights/daily-news/scripts/handler.py`
- Pattern: `begin()` at function entry, `finish()` + `flush()` at each return path

### aiq-purina-salesorderintelligence-poc

Wraps the streaming `event_generator()` async generator inside `chat_llm()`:

- File: `backend/app/api/query_portal.py`
- Pattern: `begin()` at generator start, `finish()` + `flush()` after final response is accumulated

**Langfuse isolation**: instrumentation wraps at the outermost level, before any `@observe` / `create_root_trace_context` calls — both systems run in parallel without interference.

---

## Dashboard companion endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/workshop/` | Proxy to `http://127.0.0.1:5899` — Workshop UI |
| `POST` | `/api/workshop-start` | Spawns `raindrop workshop` subprocess |
| `POST` | `/api/workshop-eval?repo=PATH` | Spawns `claude --print "Use the raindrop-eval-loop skill..."` |

---

## Skills

| Skill | Path | Purpose |
|---|---|---|
| `raindrop-instrument-agent` | `~/.claude/skill-library/raindrop-instrument-agent/SKILL.md` | Add tracing to a new agent |
| `raindrop-agent-replay` | `~/.claude/skill-library/raindrop-agent-replay/SKILL.md` | Set up replay server + `.raindrop/agents.yaml` |
| `raindrop-eval-loop` | `~/.claude/skill-library/raindrop-eval-loop/SKILL.md` | Read traces, write tests, auto-fix (max 3 cycles) |

---

## Instrumenting a new repo

Run the skill:

```
/raindrop-instrument-agent
```

Or manually:

1. Add `"raindrop-ai"` to `pyproject.toml` dependencies or `requirements.txt`
2. Run `bash $SDD_HARNESS/scripts/raindrop-setup.sh` to install into the venv
3. Inject the try/except import block at the top of the agent entry file
4. Wrap the outermost agent call with `begin()` / `finish()` / `flush()`
5. Use `event="your-repo-name"` so the dashboard can filter per repo

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| Workshop tab shows "not installed" | `raindrop` CLI not in PATH | `curl -fsSL https://raindrop.sh/install \| bash` |
| Workshop tab shows "not instrumented" | `raindrop-ai` not in pyproject/requirements | `bash $SDD_HARNESS/scripts/raindrop-setup.sh` |
| Traces not appearing | `RAINDROP_LOCAL_DEBUGGER` not set in process env | Restart shell (`.bashrc` was updated by setup); or re-run `raindrop-setup.sh` |
| Traces appear under wrong repo | `event=` label mismatch | Verify the `event=` value matches the repo name in the dashboard |
| Eval loop fails immediately | Workshop not running | Click "Start raindrop workshop" in the dashboard first |
| Import error at startup | `raindrop-ai` not installed in this venv | `pip install raindrop-ai` or `uv pip install raindrop-ai` in the repo's venv |
