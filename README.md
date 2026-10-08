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

**Usage settings:** the optional `usage:` section of `config/outreach.yaml`. Every key
is optional; the defaults are in `UsageConfig` (`src/leadforge/outreach/config.py`):

| Key | Default |
|---|---|
| `budget.searches` / `budget.fetches` / `budget.llm_calls` | 200 / 300 / 300 paid calls per users search |
| `max_evidence_age_days` | 730 |
| `strictness` | `verified_plus_likely` (or `verified_only`) |
| `include_ecosystem` | `false` |
| `fetch.max_bytes` / `fetch.passage_chars` | 1000000 / 600 |
| `classifier.effort` / `classifier.prompt` | `low` / `usage_v1` |
| `class_strengths` | per evidence class `strong`, `medium` or `weak`; defaults: `vendor_customer_ref`, `job_posting`, `code_dependency` strong, `third_party_content` weak, the rest medium |
| `cues` | polarity phrase lists for the offline classifier (`vendor_or_partner`, `used_past`, `evaluating`, `uses_now`, `injection`) |

The LLM classifier uses the same provider and key as the compilers
(`LEADFORGE_LLM_PROVIDER`, `LEADFORGE_LLM_MODEL`, `<PROVIDER>_API_KEY`). The synthetic
flow uses the offline classifier: no network, no model. A live flow with no key raises
`UsageClassifierUnavailableError` before any provider call and never falls back to the
offline classifier.

**Targeting:** the product catalog is the only source: `config/catalog/<vendor>.yaml`
per vendor plus `config/catalog/roles.yaml` (role families and seniority), loaded by
`lead_ingestion/catalog.py`. `LEADFORGE_CATALOG_DIR` points it at another directory.
There is no `target_profile.yaml`; a profile is built from the catalog
(`Catalog.to_profile`). A vendor file holds:

- `vendor`: `key`, `name`, `domains`, `partner_domains`;
- `products`: each with `key`, `name`, `aliases` (`text` plus `co_terms`),
  `technology_uids` (Apollo's technology IDs, e.g. `mongodb_atlas`), `packages` and
  `customer_paths`;
- `ecosystem`: the same shape, searched only with `include_ecosystem`.

Unknown keys are errors. A drafted entry for an unknown vendor is written to
`config/catalog/drafts/<key>.yaml` and no search may use it until approved
(`UnapprovedDraftError`):

```bash
uv run leadforge outreach catalog approve <key>
# or POST /api/catalog/drafts/<key>/approve on the web server
```

Before changing `technology_uids`, check them against Apollo's live list:

```bash
APOLLO_API_KEY=... uv run python scripts/check_apollo_technologies.py
APOLLO_API_KEY=... uv run python scripts/check_apollo_technologies.py --catalog DIR
uv run python scripts/check_apollo_technologies.py --help
```

## Running

```bash
uv run leadforge ingest
```

One run: discovery, then enrichment in a fixed order (HubSpot → Hunter → Apollo →
Google, then Hunter again for the people Apollo filled in, then a second free HubSpot
pass), then the merge and the save. It prints a
report per source (mode, calls, credits, failures). Exit code 0 if at least one source
succeeded, 1 if none did.

- Only one run at a time: a second concurrent run stops before spending anything.
- A rerun with no new data changes nothing.
- No personal data is written to logs or the report.

To search the catalog's products, name the vendor and the source columns the catalog
fills (`--uid-source` takes technology UIDs, `--alias-source` alias phrases; or set
`LEADFORGE_UID_SOURCE` / `LEADFORGE_ALIAS_SOURCE`). Every other vendor becomes a
competitor:

```bash
uv run leadforge ingest --vendor <key> --product <key> --uid-source <src> --alias-source <src>
```

### Finding users of a product

```bash
uv run leadforge outreach search users --product <key> [--product <key> ...]
uv run leadforge outreach search users --vendor <key> --include-ecosystem --usage-budget 50
uv run leadforge outreach report --search <search-id>   # md (default) or --format json
uv run leadforge outreach usage-eval [--cases PATH] [--classifier offline|llm]
```

`usage-eval` scores a classifier against human-labelled cases (default seed set:
`src/leadforge/outreach/usage/eval_cases.yaml`); `offline` is the default and needs no
key. The web page (`leadforge web`) fills its vendor and product dropdowns from
`GET /api/catalog`. The report shows each person's verdict, Company Usage, Person Fit
and the quoted evidence, each linked to its URL.

**How a Lead is judged.** Evidence from several families of pages (vendor customer
pages, job postings, code dependencies, the company's own site, third-party content,
public LinkedIn search snippets, technographics, the person's own words) is classified
per quote (uses now, used in the past, evaluating, vendor or partner). Independent
evidence classes grade the company's usage: `verified`, `likely`, `unverified` or
`negative`. The person's role grades their fit: `core`, `adjacent` or `irrelevant`.
The two give the verdict: `selected`, `manual_review` or `rejected`. Exclusions
(`vendor_staff`, `vendor_partner`, `left_company`) reject first. In users mode the
verdict gates qualification and the score only ranks. LinkedIn is reached only through
public search snippets: its pages are never fetched and no LinkedIn credentials are
accepted.

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

### Web UI

```bash
uv run leadforge web              # http://127.0.0.1:8710, opens a browser
uv run leadforge web --port 9000 --no-open
```

One page over both stores (your store and the demo store): run an ingestion or the
demo (`fresh`, `faults`), regenerate the demo tables, browse and filter leads (field
provenance, web evidence, signals, opt-outs, flagged domain ties), see every run's
per-source figures, and read the demo scorecard down to each person's checks. Before a
run on your store it lists which sources go live and may spend credits. Contacts are
masked until you tick "Reveal contacts". It binds to localhost only and has no login.

### Demo dataset

A larger synthetic dataset (40 companies, 250 people) shaped like each provider's
documented responses, with an answer key of what the pipeline should do per person:

```bash
uv run leadforge demo run --fresh   # every source synthetic, into .leadforge/demo.db
uv run leadforge demo score         # stored leads vs the answer key, per scenario
uv run leadforge demo generate      # rewrite the tables from the fixed seed
DATABASE_URL=sqlite:///.leadforge/demo.db uv run leadforge leads list
```

Scenarios cover verified, unverified and missing emails, Apollo no-match and
low-confidence answers, HubSpot opt-outs, open deals, customers and duplicate
contacts, role and shared inboxes, duplicate Apollo records, same-name colleagues,
oversized or instruction-carrying titles, and Hunter verdicts (found, invalid,
accept-all, 451, SMTP failure). The first Apollo match is answered with a 429 that the
run retries. `--faults` also serves a failed SerpApi search. The tables live in `src/leadforge/lead_ingestion/demo/data/`; to change an
expectation, edit the generator's scenario table, never the key.

## Tests and checks

```bash
uv run pytest -q
uv run ruff check src scripts
uv run mypy
```

The strict demo spec, `src/leadforge/outreach/tests/demo/test_demo_users_search.py`,
runs a users search over the synthetic dataset and requires precision >= 0.98, recall
>= 0.85, zero adversarial selections and at least 2 evidence classes cited per
selection.

No test calls a real provider. The Postgres leg of the persistence tests is required:
it **fails rather than skips** without a server (set `LEADFORGE_TEST_POSTGRES_URL`, see below).

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

## LinkedIn data: what we use, and why we don't scrape it

LinkedIn holds the signals we care most about: a person's headline, their current
role, their job history, and what their company posts. LeadForge uses those
signals, but it **never logs in to LinkedIn, never automates a LinkedIn session,
and never fetches linkedin.com pages itself.** This section records why, so the
decision is not reopened without the reasoning in front of us.

### Why not scrape

1. **The terms forbid it, and LinkedIn enforces them.**
   - LinkedIn's User Agreement bans using bots, scripts, crawlers or browser
     add-ons to scrape the service or copy profiles.
   - *hiQ Labs v. LinkedIn* is often cited as "scraping public data is legal".
     It held only that scraping public pages is probably not computer hacking
     under US federal law. In 2022 the same court found hiQ had breached
     LinkedIn's User Agreement. hiQ settled, accepted a permanent injunction and
     agreed to delete the data.
   - In 2025 LinkedIn sued Proxycurl, a LinkedIn-data API, which then shut down.
   - The legal risk is contract liability and an injunction, not a criminal
     charge. It lands on whoever runs the scraper.
2. **It would put our outreach channel at risk.** LinkedIn is the first contact in
   every sequence (`specs/hunter-outreach/`). LinkedIn detects automated activity
   and restricts or bans the accounts behind it. A scraper tied to an operator's
   account risks the account we send invitations from.
3. **Privacy law still applies to public profiles.**
   - Under GDPR, collecting personal data about people in the EU needs a lawful
     basis (Art. 6), and people must be told when data about them is collected
     from somewhere other than themselves (Art. 14). Scraping at scale makes the
     second duty hard to meet.
   - Regulators have fined companies for building profiles from public sources
     without telling the people concerned. Poland's data protection authority
     fined Bisnode in 2019 for exactly this.
4. **It is fragile.** Login walls, rate limits and frequent markup changes mean a
   scraper needs constant repair. Silent breakage would quietly lower lead quality,
   the opposite of what users-mode recognition is for.

### What we use instead

| Route | What it gives | Where |
|---|---|---|
| Data providers under their own licence | Apollo: headline, title, seniority, departments, employment history, LinkedIn URL. Hunter: LinkedIn URL per email. | Apollo and Hunter adapters |
| Search-engine results for public LinkedIn pages | Title and snippet of `linkedin.com/in`, `/company` and `/posts` results. These often contain the headline, or a post naming a technology. The snippet is read; the page is never fetched. | Google Search (SerpApi) adapter, evidence class `linkedin_public` |
| Licensed LinkedIn-data provider (optional) | Full profile fields, under that provider's contract | A source adapter, when one is configured |
| Operator's own LinkedIn use | Reading a profile before sending an invite, by a person | Outside the app |

Signals from these routes count as evidence like any other: they are classified,
quoted and cited on the Lead (`specs/user-recognition/`, Requirement 9).

### Revisiting this decision

Only reopen this with one of these in hand:
- a licensed data source;
- LinkedIn partner API access;
- legal advice that covers the specific use.

Record the outcome here. This section is an engineering rationale, not legal advice.

## Project layout

```
src/leadforge/lead_ingestion/   the ingestion slice
  adapters/                     Apollo, HubSpot, Hunter, Google Search (+ SerpApi backend)
  fixtures/                     synthetic-mode sample data, checked against provider docs
  demo/                         demo dataset: generator, routed transport, scorecard
  store/                        SQLAlchemy models, migrations, lead reader
                                (migration 0013: usage_evidence, append-only;
                                usage_classification_cache; usage_company_grades)
  catalog.py                    the product catalog loader
  tests/                        the test suite
src/leadforge/outreach/usage/   user recognition: budget, classify, cues, demo_wiring,
                                drafts, eval, eval_cases.yaml, fetch, flow, grade,
                                person, queries, records, serp, stage, store, verdict
config/catalog/                 vendor YAML, roles.yaml, drafts/
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
