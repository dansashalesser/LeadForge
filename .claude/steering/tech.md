<!-- L0: LeadForge stack — Python, LangChain-agnostic LLM, SQLite/Postgres, Docker -->
# Technology Stack

_updated_at: 2026-10-04 — stack decided (Python, agnostic LLM layer, SQLite→Postgres, Docker); guardrails and evals added_

## Architecture

A staged pipeline with swappable components at every boundary:

```
sources (adapters) → normalize → dedupe/merge → enrich → qualify → personalize (LLM) → guardrails → trigger → sender (dry-run) → report
```

- **Plug and play everywhere**: lead sources, LLM provider, sender, and DB each sit behind an interface and are chosen through config or env, never hardcoded.
- Each stage reads from and writes to the DB, so a run can be resumed or inspected and the report is a query, not in-memory state.

## Core Technologies

- **Language**: Python 3.12+
- **LLM layer**: LangChain (`init_chat_model`), which keeps the provider swappable through config. The default provider is Anthropic Claude (e.g. `claude-sonnet-5-5`), set by env.
- **Database**: SQLAlchemy 2.x + Alembic. SQLite is the default (zero setup); Postgres is selected by `DATABASE_URL`.
- **Data models**: Pydantic v2 for provider raw schemas, the canonical lead, and structured LLM output
- **HTTP**: `httpx` with retry and backoff (e.g. `tenacity`) and per-provider rate limits
- **Container**: Docker + docker-compose (app service, plus an optional `postgres` profile)

## Source Integration Pattern

- **`BaseLeadSource`** (abstract) holds what every provider shares: name, auth from env, rate limit, `search()` / `enrich()` capability flags, `fetch_raw()`, and `normalize(raw) -> CanonicalLead`.
- One subclass per provider (Apollo, HubSpot, Google Search, Leadfeeder, Hunter, UpLead, ZoomInfo, Clay). Each subclass declares its raw Pydantic schema and its field mapping.
- **Transport is a detail of the adapter**: REST API by default, or an MCP client where the provider offers an MCP server (e.g. HubSpot, Clay). The pipeline can't tell which is used.
- **Data mode per source**: `live` (real API with key) or `synthetic` (fixtures shaped exactly like the provider's documented response). Missing key means synthetic, and the log says so.
- Sources are found through a registry, so a new provider means a new class, fixtures, and env vars only.

## Guardrails

_Very important. These are first-class components, not afterthoughts._

- **Send safety**: dry-run is the default. The live sender is not implemented in the PoC, and any future version is gated behind an explicit flag plus config. Respect the suppression list, a per-lead contact cooldown, and daily caps.
- **LLM output validation**: structured output (Pydantic). Hard length limits (LinkedIn invite note ≤ 300 chars). Claims must be grounded: the message may reference only facts present in the lead record. No disparaging the competitor and no fabricated metrics. Tone and PII checks. On failure: regenerate a bounded number of times, then drop to manual review.
- **Prompt-injection hygiene**: provider and web data (especially Google Search snippets) is untrusted input, kept apart as data in prompts, never as instructions.
- **Compliance**: store only what outreach needs, keep consent and opt-out flags on the lead, and record provenance for every field.

## Evaluations

- **Deterministic checks** (pytest): schema validity, length limits, banned phrases, grounding (every claim maps to a lead field)
- **LLM-as-judge** rubric: personalization specificity, relevance to the DataStax pain point, tone, call to action. Scores are stored per message.
- **Golden set**: fixed synthetic leads with expected qualification outcomes, so scoring changes show up as regressions
- Eval runs are reproducible with the same fixtures and a pinned model config, and results are written to the DB and the report

## Development Standards

- Type hints everywhere and mypy (strict on the core). Ruff for lint and format.
- pytest. Each adapter is tested against its synthetic fixtures (raw → canonical mapping).
- Line endings normalized to LF via `.gitattributes`.
- Secrets live only in env. `.env.example` lists every provider key plus the LLM and DB settings, with no real values.

## Development Environment

### Common Commands

```bash
# Dev:   TBD (set when scaffolded — expected: uv-managed venv)
# Run:   TBD (CLI entrypoint, dry-run pipeline over synthetic data)
# Test:  TBD (pytest)
# Docker: docker compose up  (planned)
```

## Key Technical Decisions

- **2026-10-04 — Python + LangChain-agnostic LLM layer**: switching LLM providers must not touch pipeline code.
- **2026-10-04 — SQLite default, Postgres-ready via SQLAlchemy**: zero-setup demo with a production-shaped path.
- **2026-10-04 — Adapter-per-provider over a shared base, normalized to one canonical lead**: adding sources is cheap, and downstream stages stay provider-agnostic.
- **2026-10-04 — Synthetic fixtures mirroring real provider schemas**: the demo runs without keys while the adapters stay real.
- **2026-10-04 — Guardrails and evals are core scope**, not polish.
- **2026-10-04 — REST is the default transport; MCP is an optional seam**. The HubSpot remote MCP server needs interactive OAuth 2.1 PKCE, which doesn't fit a headless pipeline.
- **2026-10-04 — Search wide, enrich narrow**. Provider search calls are free and return no contact data, while enrich calls cost credits. Enrich only leads that pass qualification on preview data.
- **2026-10-04 — Google Search goes through a pluggable backend (SerpApi default)**. The Custom Search JSON API is closed to new customers and shuts down on 2027-01-01.
- **2026-10-04 — HubSpot uses date-versioned paths** (`/crm/objects/2026-09/...`), with the version held in config.

---
_Document standards and patterns, not every dependency_
