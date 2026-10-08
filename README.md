# LeadForge

A GTM pipeline that goes **find → qualify → personalize → sequence → track** and stops
at the outbox: every send is a dry run.

One command takes a product name, finds the people whose employer actually uses that
product, proves it with quoted evidence, scores and ranks them, drafts a LinkedIn invite
and a follow-up email for each, and schedules the sequence. Every threshold lives in
`config/outreach.yaml` or `config/catalog/`, never in code.

The ingestion layer pulls people and companies from four read-only sources, merges them
into **one lead per person**, and stores them in SQLite (default) or PostgreSQL.

| Source | Role | Key |
|---|---|---|
| Apollo | finds people (search) and enriches them (match) | `APOLLO_API_KEY` |
| HubSpot | your CRM: contacts, open deals, opt-outs | `HUBSPOT_ACCESS_TOKEN` |
| Hunter | finds and verifies email addresses | `HUNTER_API_KEY` |
| Google Search (SerpApi) | web evidence about each company | `SERPAPI_API_KEY` |

A source with no key runs in **synthetic mode** on built-in sample data, so the whole
pipeline works with no keys at all.

`docs/FLOW.md` walks one person through one run end to end, step by step, and names
every place a language model is called and what contains its output.

## Quick start

Needs Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/dansashalesser/LeadForge.git
cd LeadForge
uv sync                          # creates .venv and installs everything
uv run leadforge web             # the dashboard: http://127.0.0.1:8710
```

The dashboard opens on the **Search** tab against the bundled demo dataset, so a first
run needs no keys, spends nothing and sends nothing. Pick a vendor and a product, plan
the search, run it.

The same thing without a browser:

```bash
uv run leadforge outreach search users --product datastax --demo
uv run leadforge outreach report --search <search-id> --demo
```

Or just the ingestion layer, on your own store:

```bash
uv run leadforge ingest          # one run; no keys = every source synthetic
uv run leadforge leads list      # the leads it stored
```

Data goes to `.leadforge/leadforge.db` in the working directory, and the demo dataset to
`.leadforge/demo.db` (both git-ignored).

Already cloned? `git pull` then `uv sync`.

## Running

### Web UI

```bash
uv run leadforge web              # http://127.0.0.1:8710, opens a browser
uv run leadforge web --port 9000 --no-open
```

One page, five tabs, over either store — **Demo dataset** or **My store** — chosen in
the header:

| Tab | What it does |
|---|---|
| **Search** (first, the default) | Plan and run a lead search. Vendor and product dropdowns come from `GET /api/catalog`. Shows every gathered Lead with its verdict, Company Usage grade, Person Fit grade, score and quoted evidence linked to its URL; selected Leads first, then filterable by status and by free text. Opens a Lead to read its Messages. |
| **Overview** | Store counts and the last run. |
| **Leads** | Browse and filter stored leads: field provenance, web evidence, signals, opt-outs, flagged domain ties. Downloads as `.xlsx`. |
| **Runs** | Every run's per-source figures (mode, calls, credits, failures). |
| **Scorecard** (demo store only) | The demo answer key against what the pipeline decided, down to each person's checks. |

A search report downloads as Markdown or Excel from the page, and as JSON from
`GET /api/outreach/searches/<id>/report?format=json`. Before a run on **My store** the
page lists which sources go live and may spend credits; a demo run never can. Contacts
are masked until you tick "Reveal contacts". The server binds to localhost only and has
no login. Every write endpoint requires an `X-LeadForge: 1` header, which the page sends
and a stray browser request would not.

### An ingestion run

```bash
uv run leadforge ingest
```

One run: discovery, then enrichment in a fixed order (HubSpot → Hunter → Apollo →
Google, then Hunter again for the people Apollo filled in, then a second free HubSpot
pass), then the merge and the save. It prints a report per source (mode, calls, credits,
failures). Exit code 0 if at least one source succeeded, 1 if none did.

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

### Searching for leads

Three modes. `users` is the one with the evidence pipeline behind it:

```bash
uv run leadforge outreach search users --product <key> [--product <key> ...]
uv run leadforge outreach search users --vendor <key> --include-ecosystem --usage-budget 50
uv run leadforge outreach search workers "<company>" [--domain <domain>]
uv run leadforge outreach search free-text "data people at Cassandra shops"
```

Every search subcommand takes `--demo`, which runs the whole thing against the synthetic
demo dataset and its own store: no keys, no credits, dry-run sends.

One search command does the lot — it plans, ingests, gathers every stored Lead the plan
matches, grades company usage, grades person fit, decides a verdict, scores and ranks,
writes the invite and the email, and opens the sequence. Then read it back:

```bash
uv run leadforge outreach report --search <search-id>    # md (default) or --format json
uv run leadforge outreach messages --search <search-id>  # the Messages it wrote
uv run leadforge outreach scorecard --search <search-id> # demo only: vs the answer key
uv run leadforge outreach tick --search <search-id> --days 3   # advance the sequence
uv run leadforge outreach usage-eval [--cases PATH] [--classifier offline|llm]
```

`report` and `messages` take `--demo` too, and `report` takes `--reveal` to print emails,
URLs and names whole. `tick` runs against the store `DATABASE_URL` names. `.xlsx` reports
are a web-only format.

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
run retries. `--faults` also serves a failed SerpApi search. The tables live in
`src/leadforge/lead_ingestion/demo/data/`; to change an expectation, edit the
generator's scenario table, never the key.

## How a Lead is qualified

Six stages. The first five decide whether a person is worth contacting; the sixth
decides whether what we wrote to them is fit to send. Every stage records its reason, so
a verdict can be read backwards from the report.

Three ideas run through all of them:

1. **Validate before spending.** Catalog lookups, term checks and plan compilation all
   happen before the first provider call, so a typo costs nothing.
2. **Require evidence, or say so.** A stage that cannot prove something returns
   `unverified` / `manual_review` / `None` with a named reason. There is no silent
   fallback and no guessed value anywhere.
3. **Keep reruns deterministic.** The same inputs give the same decision, so thresholds
   are tested rather than argued about.

### Stage 1 — Ingest, cheapest-and-most-suppressive first

Enrichment order is derived from what each adapter declares about itself
(`enrichment_sort_key`), not from a hardcoded list:

| # | Source | What it gives | Why in this position |
|---|---|---|---|
| 1 | **HubSpot** (free) | lifecycle stage, open deals, opt-outs | free *and* can suppress — every opt-out is known before a credit is spent |
| 2 | **Hunter** (paid) | email address + verifier verdict, LinkedIn URL | paid, but a `451` verdict suppresses the person — prune them before Apollo is asked |
| 3 | **Apollo** (paid) | title, seniority, departments, employment history, LinkedIn URL, company domain, tech signals | suppresses nothing, so it only runs on survivors, and Hunter has already supplied the address that makes the match land |
| 4 | **Google/SerpApi** (paid) | company web evidence | needs the company domain Apollo produced; prunes nothing, so it goes last |

Then Hunter runs again (discovery hands over masked names, so the first call often has
nothing to ask) and HubSpot runs again — both only on identities they never saw, because
a late-discovered email address is exactly where an opt-out hides and the second HubSpot
pass is free.

Contributions are then clustered into one Lead per person. A cluster that looks like two
people fused is flagged for a human rather than silently merged.

### Stage 2 — Company Usage: does the employer actually use the product?

The expensive stage, so queries run cheapest-useful-first (`usage/queries.py`) per
company, stopping as soon as the grade reaches `verified` or the budget runs out:

| Order | Family | Query shape |
|---|---|---|
| 1 | `vendor_customer` | `site:<vendor-domain>` |
| 2 | `job_posting` | ATS boards (Greenhouse, Lever, Ashby, Workable) + `site:<company>/careers` |
| 3 | `code` | `site:github.com` |
| 4 | `own_site` | `site:<company-domain>` |
| 5 | `third_party` | open web |
| 6 | `linkedin_public` | `site:linkedin.com/in`, `/company`, `/posts` — snippets only |

A result is fetched only if its snippet names an alias. Fetching obeys `robots.txt`,
caps at 1 MB (`fetch.max_bytes`), keeps ±600 characters around each mention
(`fetch.passage_chars`), and **never fetches linkedin.com**.

Each passage is classified per quote into one relationship: `uses_now`, `used_past`,
`evaluating`, `vendor_or_partner`, `mentions_only`, `unrelated`. Each quote carries an
evidence class, and each class a strength (`class_strengths`):

| Evidence class | Default strength |
|---|---|
| `vendor_customer_ref`, `job_posting`, `code_dependency` | strong |
| `own_domain_content`, `linkedin_public`, `technographic`, `person_self_stated` | medium |
| `third_party_content` | weak |

Strength is then adjusted for age: an undated record is capped at medium, and one older
than `max_evidence_age_days` (730) drops one step.

The grade counts **independent** classes, not quotes (`usage/grade.py`):

| Finding | Grade | Reason code |
|---|---|---|
| the newest evidence says they *left* the product | `negative` | `newest_used_past` |
| ≥ 2 distinct classes, at least one a strong content class | **`verified`** | `independent_classes` |
| one content class | `likely` | `content_class` |
| a technographic fingerprint plus one other medium class | `likely` | `technographic_plus_medium` |
| only a technographic fingerprint | `unverified` | `technographic_only` |
| nothing | `unverified` | `no_usage_evidence` |

Ecosystem technologies (Apache Cassandra for DataStax, say) cap at `likely` unless
`--include-ecosystem` is passed (`ecosystem_cap`).

Budget per users search: **200 searches, 300 fetches, 300 LLM calls**, counted
separately. Exhaustion grades the company `unverified` with reason `budget_exhausted` —
an explicit "we ran out", never a fake negative.

### Stage 3 — Person Fit: is this person plausibly a user?

The title, departments, functions and seniority are matched whole-word against
`config/catalog/roles.yaml` (`usage/person.py`):

| Grade | Meaning |
|---|---|
| `core` | does the work the product is for ("data engineer") |
| `adjacent` | next to it ("engineering manager") |
| `irrelevant` | near the product but not a user ("sales engineer", "recruiter") |

`irrelevant` **beats** `core`, and an unknown title is `irrelevant`: a Sales Engineer at
a DataStax customer is not a DataStax user. `adjacent` lifts to `core` only when a
self-stated product mention backed by a confirmed record says so; an unconfirmed claim
never changes the grade.

Apollo match credits are spent only on people at `verified` or `likely` companies — we
never pay to enrich someone whose employer we already rejected.

### Stage 4 — The Verdict

Exclusions reject first, and the first one found is the reason: `vendor_staff`,
`vendor_partner`, `left_company`. Otherwise the matrix (`usage/verdict.py`):

| Company Usage | Person Fit | Verdict | Reason |
|---|---|---|---|
| `negative` | any | **rejected** | `usage_negative` |
| `unverified` (technographic only) | `core` | **manual_review** | `technographic_only` |
| `unverified` | anything else | **rejected** | `usage_unproven` |
| `likely`, under `strictness: verified_only` | any | **rejected** | `strictness_verified_only` |
| any | `irrelevant` | **rejected** | `person_irrelevant` |
| `verified` | `core` | **selected** | `verified_core` |
| `verified` | `adjacent`, boosted | **selected** | `verified_adjacent_boosted` |
| `verified` | `adjacent`, unboosted | **manual_review** | `person_unboosted` |
| `likely` | not `core` | **rejected** | `person_not_core` |
| `likely` | `core`, boosted | **selected** | `likely_core_boosted` |
| `likely` | `core`, unboosted | **manual_review** | `person_unboosted` |

Everything borderline goes to a human queue, never to the outbox.

### Stage 5 — Qualification score

Hard rules first (`qualify.py`). Every rule that applies is recorded, not just the first:

| Rule | Status |
|---|---|
| retired or superseded, opted out, suppressed, already a customer, has an open deal | `rejected`, score 0 |
| no LinkedIn URL | `needs_enrichment` — LinkedIn is always the first contact |

Then the score: a weighted mean of the terms that apply to this search, each 0..1
(`config/outreach.yaml`, `qualify:`):

| Term | Weight | Measured from |
|---|---|---|
| competitor evidence | 0.35 | strongest technology signal matching the plan's terms |
| ICP fit | 0.25 | share of fit checks met: current employer named, title present, and title matches an asked title/seniority |
| intent | 0.15 | strongest intent signal on the person or their employer |
| contactability | 0.15 | LinkedIn URL 0.5, plus verified email 0.5 / unverified or accept-all 0.25; a role address (`info@`) adds nothing |
| source agreement | 0.10 | share of fields two or more sources agree on |
| **threshold** | **0.5** | at or above → `selected`, below → `rejected` (`below_threshold`) |

Only applicable weights are counted and the omission is recorded as a reason, so a
`workers` search is not marked down for having no competitor evidence.

**In `users` mode the verdict gates and the score only ranks.** A `rejected` or
`manual_review` verdict becomes the status; a `selected` verdict passes without meeting
the threshold; a missing verdict is an error, never a pass. The competitor-evidence and
intent terms are dropped as rank-only, and ICP fit is taken straight from Person Fit
(`core` 1, `adjacent` 0.5, `irrelevant` 0) so the ranking reflects the grade that was
actually proved.

### Stage 6 — Does the message hold up?

Every Message, model-written or template-written, passes four deterministic checks
before it is kept (`message_checks.py`):

| Check | What it enforces |
|---|---|
| `schema` | the right shape for the kind (an invite has no subject, an email has one), a body with text, no unfilled template marker |
| `length` | invite ≤ 300 chars, subject ≤ 80, email ≤ 1200 |
| `banned_phrases` | none of "hope this email finds you well", "touch base", "circle back", "synergy", "game-changer" |
| `grounding` | at least one claim; every claim names a fact of the Lead, quotes words of the Message, and those words state the fact; and no capitalised name or number appears that the Lead record never gave |

A failure is regenerated up to 3 times, with the failed checks named back to the writer
so the next attempt fixes the specific problem. After that the Lead goes to
`manual_review` with no message. A hallucinated figure in a cold email is the one
failure that cannot be taken back, so an unchecked message never ships. The invite and
the email ship as a pair or not at all, and each stored Message records the model and
the prompt version.

With a model key, a judge then scores each message 1–5 on **personalization,
specificity, tone and clarity** (`judge.py`). It only measures: it never decides whether
a message is stored, a bad call raises a named error rather than inventing a score, and
with no key the summary reads `judge: off`.

[How the messages are personalized](#how-the-messages-are-personalized) covers what the
writer was allowed to know about the person in the first place, and [What would actually be
sent](#what-would-actually-be-sent) shows the messages themselves.

### Trigger logic: what fires, when, and why it is a fold

A Lead's place in its sequence is not stored anywhere. There is no `status` column to
update and no job that marks a Lead "emailed". `due` (`triggers.py`) is a pure function
over four inputs — the Lead's recorded events, the current time, the trigger config, and
three contact facts — and it returns the actions owed right now. The first matching rule
wins:

| # | Condition | Action | Why it sits there |
|---|---|---|---|
| 1 | a `halted` event exists | nothing, ever | a halt is final; no later event can un-halt a Lead |
| 2 | opted out, or suppressed | `halted` | consent outranks every pending step, and it is re-read on every tick rather than cached at selection time |
| 3 | an `email`, `fallback_email` or `stalled` event exists | nothing | each is terminal: one sequence, one ending, no second chances loop |
| 4 | no `invite` event yet | **LinkedIn invite** | LinkedIn is always first contact — the cheapest and most reversible touch, and the one the person can ignore at no cost |
| 5 | `accepted`, and `accept_delay_days` (2) have passed | **email** | an accepted invite is permission; the two-day wait is what keeps the email from reading as an automation that was watching |
| 6 | invite unanswered for `invite_timeout_days` (5) | **fallback email** if the Lead has a usable address, else `stalled` | silence is not a no, but it is not a yes either: exactly one fallback, then stop |
| 7 | anything else | nothing | waiting is a legitimate state and needs no row |

"Usable address" is narrow on purpose (`ContactFacts.usable_email`): an email that is
present, Hunter-**verified**, and not a role address. An accept-all or unverified address
never gets the fallback, and `info@` never gets it at all — a bounce or a shared inbox is a
worse outcome than no second touch.

Writing the sequence as a fold rather than a state machine with a status column buys four
things:

- **Idempotence.** `outreach tick` can run twice, or fifty times, at the same `now` and
  nothing new fires, because what is owed is derived from what is recorded. Cron
  overlapping itself is not an incident.
- **No drift.** A status column and an event log eventually disagree, and then neither can
  be trusted. Here the events are the only record, so there is nothing to reconcile.
- **Testable time.** Delays are arguments, not sleeps: pass a different `now` and assert
  the action. `--days 3` in the CLI is the same lever.
- **Auditability.** Every action has the events that caused it, so "why did this person get
  a fallback email on the 9th" is answerable from rows rather than from reasoning.

In synthetic and demo runs, invite acceptance comes from the `simulation:` block (seed 7,
`accept_rate` 0.6, within `max_accept_days` 4) via `SeededAcceptance`, so the same seed
replays the same acceptances and a timeline can be asserted in a test. A worked example of
rules 4, 5 and 3, with `--days` advancing the clock:

| Day | Events on the Lead | What `due` returns |
|---|---|---|
| 0 | — | `invite` |
| 2 | `invite` | nothing (accepted, but the delay has not passed) |
| 4 | `invite`, `accepted` (day 2) | `email` |
| 7 | `invite`, `accepted`, `email` | nothing — terminal |

Had the invite gone unanswered instead, day 4 would still return nothing and day 5 would
return `fallback_email` — or `stalled`, if the only address on file were unverified or a
shared inbox. Both endings are terminal, so day 7 returns nothing either way.

**Dispatch is dry-run only.** Firing a Message writes one `OutreachTriggerEvent` row, one
console line and one JSONL line in `outbox/dry_run.jsonl`, all with the same values. The
Message row's `state` is `dry_run` and can be nothing else, and `dispatch.py` imports no
transport, so nothing can leave the machine. [What would actually be
sent](#what-would-actually-be-sent) shows all three outputs for one invite.

### Where the model is used, and what contains it

Five call sites, each with the same guardrails. `docs/FLOW.md` has the long version.

| Call site | What it does | Could it be skipped? |
|---|---|---|
| `usage/classify.py` (`usage_v1`) | labels each fetched passage | yes — the offline classifier (alias and polarity cues) runs instead with no key |
| `llm_messages.py` (`invite_v2`, `email_v2`) | writes the invite and the email | yes — offline templates write them instead |
| `judge.py` (`judge_v1`) | scores message quality 1–5 | yes — `judge: off` |
| `compile_llm.py` (`compile_search_v1`) | turns a free-text query into a Search Plan | only `free-text` mode needs it |
| `usage/drafts.py` (`catalog_draft_v1`) | drafts a catalog entry for an unknown vendor | optional; a human must approve the draft |

In every one of them:

- untrusted text (page passages, Lead facts) goes inside one escaped block, never into
  the system prompt;
- the answer must fit a schema, with no extra fields allowed;
- anything the model claims is checked against the input — `classify.py` throws the
  answer away unless the subject is the target company, the product key matches, and
  every quote appears word for word in the passage;
- a failure becomes `None`, `manual_review` or a named error, never a silent fallback;
- every judgement is stamped with the model, prompt version and input hash, so a grade
  can be traced back to the exact prompt and passages.

The model reads and writes language. Code decides what happens next.

### The numbers we hold ourselves to

| Metric | Where | Gate |
|---|---|---|
| precision of users-mode selections | `tests/demo/test_demo_users_search.py` | ≥ **0.98** |
| recall of users-mode selections | same | ≥ **0.85** |
| adversarial selections (injection titles, vendor staff, look-alike companies) | same | **0** |
| evidence classes cited per selection | same | ≥ **2** |
| classifier accuracy on labelled passages | `leadforge outreach usage-eval` | reported, seed set in `usage/eval_cases.yaml` |
| decision agreement with the demo answer key | `leadforge outreach scorecard` / `demo score` | per-scenario confusion rows |
| message quality | the judge, on each stored Message | 1–5 per rubric item, compared across prompt versions |

## How the messages are personalized

A personalized message here is **one true, specific, cited thing about this person's
work** — not a template with the blanks filled in. The design question is therefore not
"how do we make it sound personal" but "what is this person's record actually entitled to
say", and every mechanism below exists to keep those two the same thing.

### The write loop: what the model decides, and what it may not

Personalization here is a model writing prose inside a loop that code controls. The
division of labour is the whole point, and it is worth stating in one table:

| Step | Who does it | Why there |
|---|---|---|
| Choose which facts exist at all | code (`facts.build_facts`) | a writer that can choose its own evidence can choose convenient evidence |
| Order them strongest-first | code (`facts._usage`) | the hook is a consequence of what was proved, not of what reads well |
| Turn facts into sentences | the model (`invite_v2`, `email_v2`) | language is the one part a rule cannot do well |
| Decide whether that message is allowed | code (`message_checks.py`) | the writer of a claim is the worst judge of it |
| Decide what happens next | code (`service.py`, `triggers.py`) | a model that can set status can talk itself into a send |
| Say how good it reads | the model (`judge.py`) | a measurement, with no authority over the outcome |

One model call writes one Message. The answer comes back as structured output —
`WrittenMessage`, with `extra="forbid"`, so a subject, a body and a list of claims and no
other field — and becomes a `Draft`. The Draft then faces the same four checks an offline
Draft faces. If any fails, the failed check names and details go back to the model inside
an escaped `<failed_checks>` block and it writes again, up to `max_regenerations` (3) more
attempts. If none passes, the Lead goes to `manual_review` with no Message at all.

Three properties of that loop are deliberate:

- **The feedback names the defect, not the verdict.** A model told "try again" rewords; a
  model told `grounding: not in the Lead record: ['40']` has one way to pass. See
  [What would actually be sent](#what-would-actually-be-sent) for that exact exchange.
- **The checks never soften.** The loop has no path that accepts a failing draft, so the
  only way out is a message that is more defensible than the last one. A retry budget with
  a relaxing standard is just a slower hallucination.
- **The pair is atomic.** The invite and the email are written in one `write()` call, and a
  failing email withholds a passing invite. A first touch with no follow-up is worse than
  no touch, so the sequence never starts half-built.

The model itself is configuration, not code: `llm:` in `config/outreach.yaml` sets the
provider, model, timeout and reasoning effort, and `LEADFORGE_LLM_PROVIDER` /
`LEADFORGE_LLM_MODEL` override them, so switching provider is an environment change.
Prompts are versioned files under `prompt_files/` — changed wording is a new file, so the
exact text behind any stored Message is still on disk months later. That is what makes the
judge's scores comparable at all: `invite_v1` against `invite_v2` is a real comparison
because both prompts still exist.

With a key set, the judge scores every stored Message 1–5 on **personalization,
specificity, tone and clarity**, and the scores land on the Message row. It is
instrumentation, not a gate — a judge that could block a send would be a second,
unauditable standard sitting beside the four checks, and a model grading a model is not
evidence. A failed judge call is a named error and a missing score, never an invented one.

### The fact set: the only thing a writer ever sees

Neither writer — model or template — is handed the Lead. Both are handed a **fact set**
built by `build_facts` (`facts.py`), which reads the Lead record, its current Employment,
its Signals, the web evidence attached to its company and the Evidence Records behind the
usage Verdict that selected it, **and nothing else**. There is no general knowledge of the
company in the prompt, nothing from the model's own training is permitted into the output,
and no field is read that is not in the table below:

| Fact kind | Value | `field` recorded | Where it was gathered |
|---|---|---|---|
| `name` | full name | `full_name` | Apollo match, HubSpot contact, Hunter |
| `title` | current job title | `employments[i].title` | Apollo (title, headline, seniority), HubSpot |
| `company` | current employer's name | `employments[i].company.name` | Apollo, HubSpot |
| `tech` | technology signal label | `employments[i].company.tech_signals[n]`, `tech_signals[n]` | Apollo technographics, on the employer or the person |
| `intent` | intent signal label | `employments[i].company.intent_signals[n]`, `intent_signals[n]` | Apollo, web evidence |
| `product` | the catalog **name** of a product in use | `usage_evidence[i].product_key` | the usage stage; the name comes from `config/catalog/` |
| `usage` | the published sentence behind that product | `usage_evidence[i].quote` | a page we fetched: a job ad, a vendor case study, an engineering post, a public LinkedIn snippet |
| `evidence` | title or snippet of a web result | `web_evidence[i].title` / `.snippet` | Google Search (SerpApi) |

Every fact carries four things: an **id** the writer cites, the **field** it was read from,
a **context** note saying what the value proves (shown beside the value, never part of it,
and the prompt forbids stating it), and a **variant** saying which wording the value asks
for — a product in use reads differently from one a company has left behind.

That `field` is the point. Because every fact names its origin, every claim in a finished
message maps back to a specific provider field on a specific Lead, which is what makes a
message auditable rather than merely plausible. Caps keep it small and current: at most
`max_hook_facts` (3) facts per kind, strongest signal first, and a usage quote trimmed to
whole words within `max_usage_quote_chars` (160).

**What is deliberately not in the fact set:**

- **No contact details.** The email address, LinkedIn URL and phone number are for
  dispatch, never for message content. A message cannot mention how we found them.
- **No CRM internals.** Lifecycle stage, deals and opt-out flags decide *whether* to
  write; they are never material to write *about*.
- **No scraped profile.** LeadForge never fetches a linkedin.com page. Where a person's
  own public words appear, they arrived as a search-engine snippet
  (`linkedin_public`) or as a classified `person_self_stated` record, with a URL.
- **No inferred attributes.** Nothing about seniority, budget, team size or pain is
  guessed. If it is not a stored field, it does not exist.
- **No identifiers.** A product whose key the catalog cannot name is dropped rather than
  printed: `astra` is an identifier, not something to put in front of a person.
- **No relationship we cannot stand behind.** Only `uses_now`, `evaluating` and
  `used_past` records are citable. A passing mention, a vendor's own staff page and an
  unrelated hit say nothing about use, so they never become a fact.

### The hierarchy: the most specific true thing leads

Facts are ordered so the strongest hook is the first one offered — citable relationship
first (`uses_now`, then `evaluating`, then `used_past`), then by the classifier's
confidence. A product in use plus the sentence that proves it is the most specific true
thing available, so it leads; a technology signal, an intent signal and a web-result
snippet follow in that order.

When a product hook is used, the offline writer **drops the role line entirely**. Reading
someone's own job title and employer back to them is exactly what makes a message sound
assembled, and the model prompt bans it for the same reason: they know where they work;
what we noticed about the work is the interesting part.

### Retelling, not quoting

The strongest fact is also the most awkward one: a job ad that says *"we run our ledger on
it"* was written by somebody else, about the company, not by this person. Pasting it in
quotation marks reads like surveillance, and attributing it to them is false.

So the prompt asks for a **retelling in the second person** — "your ledger runs on it" —
and `_within` (`message_checks.py`) enforces what a retelling may do:

- every word the claim asserts must be a word of the quote, plural-tolerant;
- grammar words are free — articles, prepositions, conjunctions, the verbs that only
  carry tense, and the pronouns, so the voice can turn around from "we" to "your";
- **polarity must match**: a retelling may not negate what the quote affirms, nor drop a
  negation the quote holds;
- it must assert at least one word of the quote, so an empty paraphrase is not a claim.

Never in quotation marks, and never attributed to the person or their company as something
they said.

### The safeguards, and what each one stops

| Risk | What stops it | Where |
|---|---|---|
| Prompt injection from a fetched page, a web snippet or a crafted job title | Facts reach the model only inside **one escaped `<lead_facts>` block of the user turn**. `delimit()` escapes `&`, `<` and `>`, so no value can close the block or open another, and the prompt's third line tells the model the block is data and that an instruction inside it is never followed. | `prompts.delimit`, `facts.render_facts`, `invite_v2` / `email_v2` |
| Injection reaching the system prompt | The system prompt is a versioned file filled only with **trusted integers from config** (`max_chars`, `subject_max_chars`). `Prompt.fill` raises if a marker is missing, so a prompt can never be sent half-filled, and no Lead text is ever interpolated into it. | `prompts.py`, `llm_messages.py` |
| An invented figure, name, funding round or result | The **grounding** check extracts every capitalised token and every number from the subject and body and requires each to appear in some fact's value. The only exemptions are a capital that merely begins a sentence, the word "LinkedIn", and the first-person pronoun. | `message_checks._ungrounded` |
| A claim that cites a fact it does not actually state | Every claim must name a real fact id, quote words that appear **verbatim in the message it was written for**, and those words must state that fact. All three, or the message fails. | `message_checks._grounding` |
| A paraphrase that drifts into something the source never said | The word-containment and polarity rules above. | `message_checks._within` |
| Someone else's sentence attributed to the person | A prompt rule, plus the `(evidence class, relationship, date)` context on every `usage` fact, which the prompt explicitly forbids stating. | `invite_v2` / `email_v2`, `facts._quotes` |
| Flattery, hype, pitching, corporate filler | Prompt rules ban pitching, links, flattery, "I noticed / came across", and filler about similar teams or helping them scale — plus the configurable `banned_phrases` check. | prompts, `message_checks._banned` |
| A leaked template marker | The **schema** check refuses any `{...}` left in a subject or body, and quote values have braces stripped before a writer is ever shown them. | `_schema`, `facts._quote` |
| Half a pair going out | The invite and the email are written in one `write()` call; a failing email withholds the passing invite. | `LlmWriter.write` |
| An unbounded retry loop, or a model talked into passing | On failure the **failed checks are named back** inside an escaped `<failed_checks>` block, up to `max_regenerations` (3) more attempts. After that the Lead goes to `manual_review` with no message at all. The checks themselves are never relaxed. | `llm_messages._one`, `_feedback` |
| A silent downgrade to a weaker writer | With a key set, a failed model call raises `MessageGenerationError` naming **only the exception type** — never the provider payload — and does not fall back to templates. | `llm_messages._one` |
| A generic message for a thin Lead | A Lead with too little to say produces drafts the checks refuse, rather than a message that says nothing. | `offline_messages`, `message_checks` |
| An unauditable send | Every Draft records its generator (`llm` or `offline`), the model name and the prompt version. Prompts and templates are versioned files: changed wording is a new file, so the exact text behind any stored Message is still on disk. | `Draft`, `prompts.py` |

### With no model key: the offline writer

The template writer (`offline_messages.py`) gets the **same fact set** and passes the
**same four checks**, with no network and no model. It picks fragments from
`template_files/lines_offline_v2.txt`, fills them from the Lead's own facts, and records a
`Claim` for every fact it states — so the identical grounding check judges it.

It is deliberately never given the `usage` quote. A template cannot read a sentence, so
quoting one and appending a remark produces a message that means nothing; only the model
writer, which can read it, is offered it. The template states the product itself instead.
Drafts are stamped `generator: offline`, `model: offline`.

### How we know this holds

`src/leadforge/outreach/tests/test_messages.py` is 55 tests named one per rule, including:

- `test_facts_come_only_from_the_lead_record_with_the_field_each_was_read_from`
- `test_lead_facts_reach_the_model_only_in_one_escaped_block`
- `test_the_prompt_numbers_come_from_config`
- `test_a_name_or_figure_the_lead_record_never_gave_is_ungrounded`
- `test_a_capital_that_only_starts_a_sentence_and_the_platform_name_are_fine`
- `test_a_claim_may_retell_a_quote_in_the_messages_own_voice`
- `test_a_retelling_may_not_bring_in_a_word_the_quote_does_not_hold`
- `test_a_product_the_catalog_cannot_name_is_passed_over_not_keyed`
- `test_a_failing_message_is_regenerated_with_the_failed_checks_named`
- `test_after_the_bound_the_lead_goes_to_manual_review_with_no_message`
- `test_a_failing_email_withholds_the_invite_too`
- `test_a_failing_model_call_is_a_named_error_naming_only_the_type`
- `test_a_lead_with_nothing_to_say_yields_messages_the_checks_refuse`
- `test_the_offline_writer_uses_only_the_leads_own_facts`
- `test_a_message_that_failed_its_checks_is_refused_for_storage`

On top of that, the demo spec requires at least 2 evidence classes cited per selection, so
a selected Lead always has more than one independent thing it can truthfully say.

## What would actually be sent

Every send is a dry run, so "what would go out" is something you can read rather than
guess at. This section is one Lead's worth of real output: the facts the writer saw, the
pair it wrote, the same pair with no model key, what happens when a draft fails, and the
three places a fired Message lands.

The Lead is subject `p261` of the demo dataset, scenario `verified_user`: a Head of Data
Platform at a company whose Greenhouse ad and whose vendor case study both name the
product. `leadforge outreach search users --product datastax --demo` selects him, and
`leadforge outreach messages --search <id> --demo` prints the pair.

### What the writer was given

The entire prompt input, as `render_facts` builds it and `delimit` escapes it:

```
<lead_facts>
[name] Bastian Lindqvist
[title] Head Data Platform
[company] Kestrel Health
[tech:DataStax] DataStax
[tech:Amazon AWS] Amazon AWS
[tech:Kubernetes] Kubernetes
[product:datastax] (uses_now) DataStax
[usage:0] (vendor_customer_ref, uses_now, 2026-06-02) Kestrel Health customer platform powered by DataStax Astra DB.
[usage:1] (job_posting, uses_now, 2026-08-14) Kestrel Health hiring Staff Database Engineer. You will run DataStax Astra DB in production.
</lead_facts>
```

That is all of it, and each line carries on its `Fact` row the field it was read from. No
CRM lifecycle stage, no email address, no LinkedIn URL, no company description, nothing
from the model's own training, and no fourth technology signal — `max_hook_facts` is 3 per
kind. The company's third piece of evidence, an Apollo technographic fingerprint, produced
the `product` fact but no quotable sentence: a machine-made string is not words anybody
wrote, so there is nothing in it to retell.

The notes in parentheses say what a value proves. The prompt forbids stating them, so
`job_posting` and the dates never reach the reader — they are there so the model knows that
somebody in recruiting wrote that sentence, not Bastian.

### The LinkedIn invite, as the model writes it

`generator: llm`, `model: claude-sonnet-5-5`, `prompt_version: invite_v2`, 171 of 300
characters:

> Hi Bastian — you run DataStax Astra DB in production. That is the thing I spend my days
> on, and I would rather hear how it is going from someone doing it than guess at it.

Stored beside it, the claims that make it checkable:

```json
{"claims": [{"fact_id": "name", "text": "Bastian"},
            {"fact_id": "usage:1", "text": "you run DataStax Astra DB in production"}]}
```

What it does *not* do is where the design shows:

- **It does not read his title and employer back to him.** He knows where he works, and
  "Head of Data Platform at Kestrel Health" is the tell of a mail merge. With a product
  hook present, both the prompt and the offline writer drop the role line.
- **It does not quote the job ad.** Somebody in recruiting wrote that sentence, about the
  company — quotation marks would read like surveillance, and attributing it to Bastian
  would be false. "You will run DataStax Astra DB in production" is retold as "you run
  DataStax Astra DB in production": the same claim, from his side, in the message's own
  voice, and every word of it is a word the quote holds.
- **It does not pitch, link, ask for a call, or announce that it noticed something.** The
  one specific thing carries the note; the connection request already says we want to
  connect.
- **It cannot say anything the record cannot answer for.** Every capitalised word in it —
  Bastian, DataStax, Astra, DB — appears in a fact value, which is what the grounding check
  demands. A sentence about his "40 million writes a day" would be refused before it was
  ever stored, however plausible it sounded.

### The follow-up email

Fires only after the invite is accepted, `accept_delay_days` later. `prompt_version:
email_v2`, 30 of 80 subject characters and 349 of 1200 body characters:

> **Subject:** Running Astra DB in production
>
> Hi Bastian,
>
> You run DataStax Astra DB in production, and that is the part of the stack I work on
> every day.
>
> I am writing because I would like to hear how that is holding up for you — what is
> smooth, and what you have had to work around. If twenty minutes is worth it to you, I am
> glad to find a time; if not, no hard feelings.
>
> Thanks for reading.

The subject is the thing itself rather than a sales line: no colon-and-buzzword
construction, no question mark fishing for a reply. The ask is one sentence, bounded in
minutes, and explicitly easy to decline — the cheapest way to keep a channel open is to
make saying no cost nothing.

### The same Lead with no model key

The offline writer gets the same fact set and passes the same four checks, with no network
and no model. `generator: offline`, `model: offline`, `prompt_version: invite_offline_v2`:

> Hi Bastian — Kestrel Health runs DataStax, and that is what I work on day to day. Happy
> to connect.

> **Subject:** Quick note, Bastian
>
> Hi Bastian,
>
> Kestrel Health runs DataStax, and that is what I work on day to day.
>
> If you are open to it, I would welcome twenty minutes to compare notes. If not, no hard
> feelings.
>
> Thanks for reading.

Plainer, and deliberately so. A template cannot read the job ad, so it is never handed the
quote — it names the product the evidence proves and stops. It also states the product by
its catalog **name**, "DataStax", never by the key `datastax`: a key is an identifier, not
something to put in front of a person. The honest floor of a no-model run is a short, true,
specific sentence, not a fluent one that implies reading we did not do.

A Lead with a thinner record degrades visibly rather than silently. A `workers` search runs
no usage stage, so there is no product fact at all, the role line comes back and the hook
falls to a technology signal:

> Hi Pavel — Staff Database Engineer at Ingleby Payments is close to the corner I work in.
> Running DataStax well is harder than it looks from outside. Happy to connect.

That is the signal to look at the evidence, not at the copy. And a Lead with nothing at all
to say produces drafts the checks refuse, which is the intended outcome: no message is a
better result than a generic one, because a generic message spends the one first
impression we get on nothing.

### When a draft fails

Say the model reaches for a figure nobody gave it:

> Hi Bastian — you run DataStax Astra DB in production across 40 million writes a day.

The grounding check pulls every capitalised token and every number out of the draft and
finds `40` in no fact value, so the draft never becomes a Message. The failed check is
named back to the model in its own escaped block:

```
Your previous attempt failed these checks, so write it again:
<failed_checks>
grounding: not in the Lead record: ['40']
</failed_checks>
```

Naming the defect matters more than retrying. "Try again" invites a reword; "`40` is not
in the record" has exactly one fix. The checks are never relaxed to let an attempt
through, so the loop can only end by the message getting more honest, and it is bounded:
`max_regenerations` (3) more attempts, then the Lead goes to `manual_review` with no
Message at all. One hallucinated figure in a cold email cannot be taken back, so an
unchecked message never ships.

### What a fired Message looks like

`leadforge outreach tick --search <id> --days 3` writes the same values to three places
and sends nothing. A console line:

```
[dry-run] 2026-09-02T09:00:00+00:00 invite via linkedin decision=4b1f0c2e-7a65-4f1d-9c3e-2a7d5e8b1140 (171 chars, not sent)
```

A line appended to `outbox/dry_run.jsonl`, which is the file to diff when a prompt version
changes:

```json
{"at": "2026-09-02T09:00:00+00:00", "decision_id": "4b1f0c2e-7a65-4f1d-9c3e-2a7d5e8b1140", "message_id": "c9a7f0d1-3b62-4e08-8a55-6f2c1d94ab77", "kind": "invite", "channel": "linkedin", "subject": null, "body": "Hi Bastian — you run DataStax Astra DB in production. That is the thing I spend my days on, and I would rather hear how it is going from someone doing it than guess at it.", "state": "dry_run"}
```

And an `OutreachTriggerEvent` row, which is what the next `tick` reads to decide what is
owed. Reading the pair back:

```
$ leadforge outreach messages --search 7d1a9f44-02c5-4b7e-9f31-55a0c1e6d2b8 --demo
4b1f0c2e-7a65-4f1d-9c3e-2a7d5e8b1140 linkedin [dry_run] llm/claude-sonnet-5-5/invite_v2
  Hi Bastian — you run DataStax Astra DB in production. That is the thing I spend my days on, and I would rather hear how it is going from someone doing it than guess at it.
4b1f0c2e-7a65-4f1d-9c3e-2a7d5e8b1140 email [dry_run] llm/claude-sonnet-5-5/email_v2
  subject: Running Astra DB in production
  Hi Bastian,
  ...
2 message(s)
```

This Lead never accepts the invite in the demo's seeded simulation, so what actually fires
on day 5 is the fallback email rather than the accepted-path email — the sequence is
`fallback_email`, which the scorecard checks against the answer key.

Every Message row keeps its `state` at `dry_run` and can hold nothing else, and
`dispatch.py` imports no transport at all. Nothing can leave the machine because there is
nothing in the process that could carry it.

That is a deliberate stopping point rather than an unfinished feature. A generated message
is a draft until a human has read a few of them, and the cost of being wrong is
asymmetric: a bad send burns the only cold channel the person has given us, while a bad
draft in a JSONL file costs a diff. Adding a real transport means writing one adapter and
flipping `state` — we have left that to the point where someone owns the sending account.

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
| `LEADFORGE_LLM_PROVIDER`, `LEADFORGE_LLM_MODEL`, `<PROVIDER>_API_KEY` | The model for every LLM call. Defaults come from the `llm:` block of `config/outreach.yaml` (`anthropic`, `claude-sonnet-5-5`, 60 s timeout, `low` reasoning effort). LangChain builds the client, so switching provider is an environment variable, not a code change. With no key every optional model call is skipped and the pipeline runs offline. |
| `LEADFORGE_CATALOG_DIR` | Points the catalog loader at another directory. |
| `LEADFORGE_UID_SOURCE`, `LEADFORGE_ALIAS_SOURCE` | Defaults for `ingest --uid-source` / `--alias-source`. |

HubSpot needs a private-app token with the `crm.objects.contacts.read` and
`crm.objects.deals.read` scopes. Each variable's docs link is in `.env.example`.

Bad values (an unknown plan, a short secret, a non-number limit) stop the run before
anything is recorded or spent, and the error never echoes the value.

**Thresholds:** `config/outreach.yaml`. `qualify:` holds the weights and the threshold,
`messages:` the length caps, regeneration budget and banned phrases, `triggers:` the
sequence delays, `llm:` the provider and model, `simulation:` the demo's acceptance
behaviour, `outbox_path` the dry-run JSONL. All of them are documented in the
qualification section above.

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

The LLM classifier uses the same provider and key as the compilers. The synthetic flow
uses the offline classifier: no network, no model. A live flow with no key raises
`UsageClassifierUnavailableError` before any provider call and never falls back to the
offline classifier.

**Targeting:** the product catalog is the only source: `config/catalog/<vendor>.yaml`
per vendor plus `config/catalog/roles.yaml` (role families and seniority), loaded by
`lead_ingestion/catalog.py`. There is no `target_profile.yaml`; a profile is built from
the catalog (`Catalog.to_profile`). A vendor file holds:

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

Prompts and offline templates are versioned files, never inline strings:
`src/leadforge/outreach/prompt_files/<name>_v<N>.txt` and `template_files/`. Changed
wording means a new file, because every stored plan and Message names the exact text it
came from — older versions stay for that audit trail.

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
   every sequence. LinkedIn detects automated activity
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
quoted and cited on the Lead.

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
  web/                          FastAPI dashboard: api.py, jobs.py, export.py, static/
  catalog.py                    the product catalog loader
  tests/                        the test suite
src/leadforge/outreach/         search, qualify, write, sequence, report
  usage/                        user recognition: budget, classify, cues, demo_wiring,
                                drafts, eval, eval_cases.yaml, fetch, flow, grade,
                                person, queries, records, serp, stage, store, verdict
  qualify.py                    hard rules and the weighted score
  message_checks.py             the four pre-send checks
  triggers.py                   the sequence fold
  judge.py                      the message-quality rubric
  prompt_files/                 versioned LLM prompts
  template_files/               versioned offline message templates
  static/                       the dashboard's Search tab
config/outreach.yaml            every threshold: qualify, messages, triggers, llm, usage
config/catalog/                 vendor YAML, roles.yaml, drafts/
docs/FLOW.md                    one run, end to end, with every LLM call named
scripts/                        dev tools (Apollo technology check)
```

## Known limits

- Not confirmed against a live account yet: HubSpot's `hs_is_closed` deal filter and
  the exact shape of Apollo's "no match" reply. Watch the first live run.
- Shared-inbox detection (`info@`, `sales@`, …) uses an English word list.
- Primary-domain ties with no stored answer stay flagged for a person to decide; there
  is no model-backed resolver yet.
- `.xlsx` is a web-only report format; the CLI writes Markdown and JSON.
- Sends are dry-run only. There is no transport adapter, by design.
