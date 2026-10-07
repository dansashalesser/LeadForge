# LeadForge

Lead ingestion, enrichment and qualification pipeline. The ingestion layer pulls
people and companies from four read-only sources, merges them into **one lead per
person**, and stores them in SQLite (default) or PostgreSQL.

| Source | Role | Key |
|---|---|---|
| Apollo | finds people (search) and enriches them (match) | `APOLLO_API_KEY` |
| HubSpot | your CRM: contacts, open deals, opt-outs | `HUBSPOT_ACCESS_TOKEN` |
| Hunter | finds and verifies email addresses | `HUNTER_API_KEY` |
| Google Search (SerpApi) | web evidence about each company | `SERPAPI_API_KEY` |

A source with no key runs in **synthetic mode** on built-in sample data, so the whole
pipeline works with no keys at all.

## Quick start

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/dansashalesser/LeadForge.git
cd LeadForge
uv sync                          # creates .venv and installs everything
uv run leadforge ingest          # one run; no keys = every source synthetic
uv run leadforge leads list      # the leads it stored
```

Data goes to `.leadforge/leadforge.db` in the working directory (git-ignored).

Already cloned? `git pull` then `uv sync`.

## Configuration

```bash
cp .env.example .env             # .env is git-ignored; never commit it
```

`leadforge` reads `.env` from the working directory (or the file named by
`LEADFORGE_ENV_FILE`). Variables already set in your shell win over the file.

| Variable | What it does |
|---|---|
| `APOLLO_API_KEY`, `HUBSPOT_ACCESS_TOKEN` + `HUBSPOT_API_VERSION`, `HUNTER_API_KEY`, `SERPAPI_API_KEY` | A source goes **live** once all of its variables are set; otherwise it stays synthetic. The run report says which and why. |
| `APOLLO_PLAN` | `free` (default), `basic`, `professional` or `organization`. Sets Apollo's rate limits. |
| `HUNTER_PLAN` | `data` (default), `all-in-one` or `free`. Sets the verifier credit cost (1 or 0.5) and the free plan's 10-address cap. |
| `SERPAPI_HOURLY_LIMIT` | Searches per hour on your SerpApi plan (default 50, the free plan). Raise it to your plan's limit. |
| `LEADFORGE_MATCH_KEY_SECRET` | At least 32 bytes, e.g. `openssl rand -hex 32`. Keys the hashes used in logs and the lead index. Set it once and keep it; without it each run uses a random key. |
| `LEADFORGE_MODE` | `live` or `synthetic` for every source, overriding the keys. |
| `DATABASE_URL` | SQLAlchemy URL. Empty means the local SQLite file. |
| `LLM_PROVIDER`, `LLM_MODEL` | Reserved for the model-backed tie resolver; not used yet. |

HubSpot needs a private-app token with the `crm.objects.contacts.read` and
`crm.objects.deals.read` scopes. Each variable's docs link is in `.env.example`.

Bad values (an unknown plan, a short secret, a non-number limit) stop the run before
anything is recorded or spent, and the error never echoes the value.

**Targeting:** `config/target_profile.yaml` lists the competitor technologies to
search for. Apollo's names are its own technology IDs (e.g. `mongodb_atlas`). Before
changing them, check them against Apollo's live list:

```bash
APOLLO_API_KEY=... uv run python scripts/check_apollo_technologies.py
uv run python scripts/check_apollo_technologies.py --help
```

## Running

```bash
uv run leadforge ingest
```

One run: discovery, then enrichment in a fixed order (HubSpot → Hunter → Apollo →
Google, then a second free HubSpot pass), then the merge and the save. It prints a
report per source (mode, calls, credits, failures). Exit code 0 if at least one source
succeeded, 1 if none did.

- Only one run at a time: a second concurrent run stops before spending anything.
- A rerun with no new data changes nothing.
- No personal data is written to logs or the report.

### Reading leads

```bash
uv run leadforge leads list                      # active leads, 50 per page
uv run leadforge leads list --after <last-id>    # next page
uv run leadforge leads list --company-id <id> --include-retired
uv run leadforge leads show <lead-id>
uv run leadforge leads show <lead-id> --reveal   # unmask emails and URLs
uv run leadforge leads show <lead-id> --json
```

Emails and LinkedIn URLs are masked (`a***@example.com`) unless you pass `--reveal`.
A lead that was merged into another shows as retired, with a pointer to its successor.

In Python: `load_lead`, `list_leads` and `find_lead` (by email or LinkedIn URL) in
`leadforge.lead_ingestion.store.lead_reader`.

## Tests and checks

```bash
uv run pytest -q
uv run ruff check src scripts
uv run mypy
```

No test calls a real provider. The Postgres leg of the persistence tests is required:
it **fails rather than skips** without a server.

## Postgres

SQLite is the zero-setup default. For PostgreSQL 16:

```bash
docker compose up -d postgres
export LEADFORGE_TEST_POSTGRES_URL=postgresql://leadforge:leadforge@localhost:5432/leadforge
uv run pytest                                     # tests against it
export DATABASE_URL=$LEADFORGE_TEST_POSTGRES_URL  # optional: run the app on it
```

With `LEADFORGE_TEST_POSTGRES_URL` unset, the tests start a throwaway cluster from
locally installed PostgreSQL server binaries instead. The compose credentials are for
local development only.

## Project layout

```
src/leadforge/lead_ingestion/   the ingestion slice
  adapters/                     Apollo, HubSpot, Hunter, Google Search (+ SerpApi backend)
  fixtures/                     synthetic-mode sample data, checked against provider docs
  store/                        SQLAlchemy models, migrations, lead reader
  tests/                        the test suite
config/target_profile.yaml      what to search for
scripts/                        dev tools (Apollo technology check)
specs/lead-source-adapters/     requirements, design, ADRs, tasks, and every decision
                                made where the spec was silent (choices.md)
```

## Known limits

- Not confirmed against a live account yet: HubSpot's `hs_is_closed` deal filter and
  the exact shape of Apollo's "no match" reply. Watch the first live run.
- Shared-inbox detection (`info@`, `sales@`, …) uses an English word list.
- Primary-domain ties with no stored answer stay flagged for a person to decide; there
  is no model-backed resolver yet.
