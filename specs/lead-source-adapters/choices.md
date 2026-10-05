# Choices Ledger — lead-source-adapters

Decisions made where the spec was silent. Appended per implementation pass.
Verdicts: `sound` (any reasonable implementer would agree) / `unsound` (needs rework) /
`needs-user` (a preference the agent does not own).

## Pass 1 — 2026-10-05 (task 1.1)

Not logged: uv/hatchling, `>=` floors + `uv.lock`, and offline `tldextract` were settled by
the user at the Phase -1 gate. Test location, pytest/ruff/mypy config, and placeholder
wording were named implementer's choices.

### Root `leadforge` CLI is owned by the ingestion slice
- **Verdict:** needs-user
- **Spec said:** `leadforge ingest` entrypoint (design stack table); nothing on who owns the root `leadforge` app once other stages (qualification, personalization) need subcommands
- **Chose:** `[project.scripts] leadforge = "leadforge.lead_ingestion.cli:app"` — the slice's Typer app *is* the product CLI
- **Alternatives:** a thin root app at `src/leadforge/cli.py` mounting each slice's sub-app (`app.add_typer`) — but that is a cross-slice module, which D1/CLAUDE.md discourage until a second stage exists
- **Provisional:** reversible — change one line in `pyproject.toml` and move `ingest` under a root app when stage two lands; no command name changes for users
- **Evidence:** `pyproject.toml` `[project.scripts]`; subagent report "CLI layout"

### Slice tests and the static guard ship inside the wheel
- **Verdict:** unsound (low severity)
- **Spec said:** tests live in the slice (D1); nothing on packaging them
- **Chose:** `[tool.hatch.build.targets.wheel] packages = ["src/leadforge"]` — includes `lead_ingestion/tests/`; `structure_guard.py` is a runtime module whose only consumer is a test
- **Alternatives:** exclude `**/tests/**` from the wheel; keep the guard under `tests/`
- **Fix next pass:** add a hatch wheel `exclude` for `**/tests`; consider moving the guard into `tests/`
- **Evidence:** `pyproject.toml:36-37`, `src/leadforge/lead_ingestion/structure_guard.py`

### Raw schemas are private by package: everything under `lead_ingestion.adapters`
- **Verdict:** sound
- **Spec said:** guard "no module outside this slice may import a provider raw schema" (tasks.md:33); no location for raw schemas (design names components, not paths)
- **Chose:** `RAW_SCHEMA_PACKAGE = "leadforge.lead_ingestion.adapters"`; whole subpackage (adapter classes included) off-limits outside the slice. Binds tasks 12–15 to put raw models there
- **Alternatives:** a `raw_schemas` module per adapter, or marker base class detected by name — both weaker for an AST import check
- **Evidence:** `structure_guard.py:13`; subagent report "Raw schema location"

### Guard scans only `src/`
- **Verdict:** sound
- **Spec said:** "outside this slice"; silent on non-`src` code (scripts, `tests/integration`)
- **Chose:** `src_root.rglob("*.py")` minus the slice
- **Alternatives:** scan repo root — would flag integration tests that legitimately touch raw fixtures
- **Evidence:** `structure_guard.py:27-34`

### Dependency floors not given by the design
- **Verdict:** sound
- **Spec said:** floors for httpx/tenacity/alembic/typer only
- **Chose:** pydantic>=2, sqlalchemy>=2, structlog>=24, pyyaml>=6, python-dotenv>=1, tldextract>=5; dev mypy>=1.13, pytest>=8.3, pytest-asyncio>=0.25, respx>=0.22, ruff>=0.8; exact versions in `uv.lock`
- **Evidence:** `pyproject.toml`

### Dev interpreter pinned to 3.12 via `.python-version`
- **Verdict:** sound
- **Spec said:** Python 3.12+
- **Chose:** pin 3.12 so uv stops defaulting to 3.13; `requires-python` stays `>=3.12`
- **Evidence:** `.python-version`; subagent report

### Files touched outside the slice
- **Verdict:** sound
- **Spec said:** single-folder blast radius (D1)
- **Chose:** empty `src/leadforge/__init__.py` (packaging needs it) and cache/venv entries appended to `.gitignore`
- **Evidence:** subagent file list

Evidence: partial — the refactor and production-readiness agents were not spawned (the
subagent has no Agent tool). Serena diagnostics run by the parent: only `reportMissingImports`
for third-party packages (Pyright not pointed at `.venv`); mypy strict inside the venv is clean.
