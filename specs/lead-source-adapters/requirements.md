# Requirements Document

## Project Description (Input)

lead-source-adapters: Multi-source lead ingestion layer for LeadForge. BaseLeadSource abstract class + one adapter per provider (Apollo.io, HubSpot, Google Search, Leadfeeder, Hunter.io, UpLead, ZoomInfo, Clay), REST or MCP transport, live vs synthetic mode (fixtures mirroring each provider's documented API response schema), normalization into a CanonicalLead with per-field provenance, cross-source dedupe/merge, persistence to SQLite/Postgres via SQLAlchemy, registry so new sources are plug-and-play, .env.example listing required keys. Adapters must follow current official provider API docs.

## Introduction

This feature delivers the **Lead Ingestion Layer** — the first stage of the LeadForge pipeline (`sources → normalize → dedupe/merge → enrich → qualify → personalize → guardrails → trigger → sender (dry-run) → report`). It is responsible for everything up to and including a persisted, deduplicated `CanonicalLead` record with per-field provenance. It is **not** responsible for qualification, scoring, personalization, or delivery.

The layer is composed of five collaborating components, named here so EARS statements have concrete subjects:

| Component | Responsibility |
|---|---|
| **Source Registry** | Discovers adapters, reads enablement config, hands the orchestrator a list of active sources |
| **`<Provider>` Source adapter** | Auth, transport, fetch, provider-specific raw schema, `normalize()` to `CanonicalLead` |
| **Ingestion Orchestrator** | Runs enabled sources, isolates failures, enforces rate limits, emits the run record |
| **Merge Engine** | Cross-source identity matching, field-level conflict resolution, provenance preservation |
| **Lead Store** | SQLAlchemy persistence of canonical leads, provenance rows, and run records |
| **Target Profile** | Configuration naming the technologies, competitors, and keywords being targeted, plus each provider's vocabulary for them |

**Binding project constraints** (from `prefs.md`, treated as requirements, not preferences):
adding a source is one new class and nothing else; the full demo runs with zero API keys present; no real outbound message is ever sent; every provider normalizes into one canonical structure; Python + SQLAlchemy with SQLite default and Postgres capability; guardrails and evaluations are core scope; **everything is agnostic and plug-and-play**, which Requirement 23 makes falsifiable.

**`CONTEXT.md` is binding on this document.** A **Lead** is always one human; company-level data is a **Company Signal**; the two are related by an **Employment**. Where this document said "lead" and meant a company, it has been corrected.

**Provider scope (ADR-0005):** four adapters are built — Apollo (12), HubSpot (13), Google Search (14), Hunter (16). Requirements 15, 17, 18 and 19 are **deferred, not withdrawn**: their research is verified and they remain the specification for later work. The plug-and-play claim is proved by the runtime-registration test in 3.2, not by the adapter count, so a fifth adapter would demonstrate nothing the fourth does not.

**Provider API facts cited below were cross-checked against `research.md`, which is authoritative on provider endpoints, auth schemes, and limits.** Every gap raised during generation has since been resolved — see **Resolved Decisions** and the **Verification Note** at the end of this document for what was corrected and what remains to confirm during design.

---

## Requirements

### Requirement 1: Canonical Lead Model with Per-Field Provenance

**Objective:** As a pipeline stage downstream of ingestion, I want every lead in one identical shape with the origin of each field recorded, so that scoring, personalization, and compliance never need provider-specific knowledge.

#### Acceptance Criteria

1. The Lead Ingestion Layer shall expose exactly one canonical lead type, `CanonicalLead`, as the only lead type visible to any downstream stage.
   - Verify: No module outside `sources/` imports a provider-specific raw schema.
2. When an adapter returns a normalized lead, the Normalizer shall attach to every populated field a provenance record containing source name, data mode, fetch timestamp, and the provider's raw field path.
   - Verify: Every populated canonical field has a non-null provenance record attached.
3. If a provider supplies no value for a canonical field, then the Lead Ingestion Layer shall leave that field null and record no provenance entry for it.
   - Verify: Absent provider values produce null fields and zero provenance rows.
4. The `CanonicalLead` type shall be a Pydantic v2 model that rejects unknown fields at construction time.
   - Verify: Constructing CanonicalLead with an undeclared field raises a validation error.
5. The `CanonicalLead` type shall carry identity fields (`email`, `linkedin_url`, `full_name`), zero or more `Employment` relations, technographic evidence, intent evidence, and compliance flags (`opt_out`, `suppressed`).
   - Verify: All three identity fields, the employment relation, and both compliance flags exist on the model.
6. When provider content is stored on a canonical field, the Normalizer shall mark that field as untrusted external text.
   - Verify: Provider-sourced text fields carry an untrusted-content marker in provenance.
7. The `CanonicalLead` type SHALL NOT carry a `company_domain` field; a lead's employer SHALL be reachable only through an `Employment` relation naming a `CompanySignal`.
   - Verify: No canonical field holds an employer domain directly, and employer lookups traverse `Employment`.
8. Each `FieldProvenance` record SHALL carry a `confidence_origin` of `provider_stated`, `heuristic`, or `none`, together with the provider's raw certainty value and the name of the scale it was expressed on.
   - Verify: A provider-stated confidence retains its raw value and scale name; a field with no provider certainty records `none`, never a default number.
9. Where a source was capable of answering for a canonical path, was asked, and reported no match, the Lead Ingestion Layer SHALL record a negative-evidence entry distinct from the absence recorded under 1.3.
   - Verify: "Asked, no match" and "never able to answer" are distinguishable downstream; a source with no such field in its API never produces negative evidence.

---

### Requirement 2: `BaseLeadSource` Contract

**Objective:** As a developer adding a provider, I want one abstract class that fully specifies what an adapter must do, so that writing a new adapter is a mechanical exercise with no downstream edits.

#### Acceptance Criteria

1. The Lead Ingestion Layer shall define an abstract `BaseLeadSource` class declaring `name`, `capabilities`, `rate_limit`, `data_mode`, `fetch_raw()`, and `normalize(raw) -> list[Contribution]`, where a `Contribution` is a lead, company-signal, or employment contribution.
   - Verify: BaseLeadSource declares all six named members as abstract or concrete, and `normalize()` returns contributions rather than canonical leads.
2. If a subclass of `BaseLeadSource` does not implement `fetch_raw()` and `normalize()`, then instantiation shall raise an error at import or construction time.
   - Verify: Instantiating an incomplete subclass raises TypeError before any network call.
3. The `BaseLeadSource` class shall declare capability flags for `search` and `enrich` independently, so a provider may support either, both, or neither.
   - Verify: Search-only and enrich-only adapters both construct without error.
4. When the Ingestion Orchestrator invokes an adapter, it shall interact only through members declared on `BaseLeadSource`.
   - Verify: Orchestrator code contains zero references to concrete adapter class names.
5. The `BaseLeadSource` class shall read its credentials from environment variables only, never from config files or code.
   - Verify: No adapter reads a secret from `/config/` or a source literal.
6. When `normalize()` receives a raw payload that fails the adapter's declared raw schema, the adapter shall raise a named normalization error identifying the provider and the offending field.
   - Verify: Malformed raw payload raises a named error naming provider and field.
7. The `BaseLeadSource` class SHALL declare `cost_class` (`free` | `paid`), `charge_unit` (`per_lead` | `per_company` | `per_call`), and `yields_suppression`, from which the Ingestion Orchestrator SHALL derive enrichment order rather than reading a hand-maintained list.
   - Verify: Enrichment order is computed from declared attributes; adding a source places it in the correct tier with no edit outside its own module.
8. The `BaseLeadSource` class SHALL declare, for each Target Profile term it can express, the provider vocabulary it uses; a source declaring none SHALL be recorded as not-applicable for that term rather than as reporting no match.
   - Verify: A provider with no targeting surface never contributes negative evidence for a Target Profile term.
   - Convention: a Target Profile term's canonical path is `target_profile.<term>`. A vocabulary is empty when it is absent, a blank string, or an empty collection; any other value, including `0` and `false`, is a real provider identifier. A source answers a term (and may therefore report Negative Evidence for it) if and only if its vocabulary for that term is non-empty.

---

### Requirement 3: Source Registry and Plug-and-Play Extensibility

**Objective:** As a reviewer of this take-home, I want "add a source = add one class" to be a falsifiable property, so that the plug-and-play claim can be tested rather than asserted.

#### Acceptance Criteria

1. When a new module defining a `BaseLeadSource` subclass is placed in `src/leadforge/sources/`, the Source Registry shall discover and register it without any edit to the registry, the orchestrator, the models, or the database layer.
   - Verify: Adding a source module changes exactly one file plus fixtures.
2. The repository shall contain an automated test that adds a throwaway `BaseLeadSource` subclass at runtime and asserts it appears in the registry and completes a synthetic ingestion run.
   - Verify: A test registers a dummy source and runs it end-to-end.
3. When two registered sources declare the same `name`, the Source Registry shall raise a duplicate-source error at startup.
   - Verify: Two sources sharing a name fail startup with a named error.
4. While a source is disabled in `config/`, the Source Registry shall exclude it from the active source list and the Ingestion Orchestrator shall not instantiate it.
   - Verify: Disabled sources produce no adapter instance and no network call.
5. The Source Registry shall report, for each registered source, its name, capability flags, resolved data mode, and whether credentials were found.
   - Verify: Registry listing shows name, capabilities, mode, and credential status.
6. The Source Registry SHALL record for each source a `live_access` classification of `available`, `gated`, or `unavailable`, and the run report SHALL state which sources could run live versus which are synthetic-only by necessity.
   - Verify: The registry listing and the run report both show every source's live-access classification.

---

### Requirement 4: Live and Synthetic Data Modes

**Objective:** As a demo operator with no API keys, I want the whole pipeline to run on synthetic data automatically, so that the demo is reproducible on any machine with zero credentials.

#### Acceptance Criteria

1. When no credential is present in the environment for a provider, that provider's adapter shall resolve its data mode to `synthetic` and shall make no outbound HTTP or MCP call.
   - Verify: With an empty environment, zero outbound network calls are made.
2. When a credential is present for a provider and no explicit override is set, that provider's adapter shall resolve its data mode to `live`.
   - Verify: Present credential plus no override resolves the mode to live.
3. Where an explicit per-source mode override is configured, the adapter shall honor the override even when a credential is present.
   - Verify: Override forces synthetic mode despite a valid credential being set.
4. When an adapter resolves its data mode, the Ingestion Orchestrator shall log the source name, the resolved mode, and the reason for that resolution.
   - Verify: Each source logs one line naming mode and resolution reason.
5. While every source is in synthetic mode, the Lead Ingestion Layer shall complete a full ingestion run producing persisted canonical leads.
   - Verify: A zero-key run persists at least one canonical lead successfully.
6. The resolved data mode shall be recorded in the provenance of every field the adapter produces.
   - Verify: Provenance records distinguish synthetic-sourced fields from live-sourced fields.

---

### Requirement 5: Synthetic Fixture Schema Fidelity

**Objective:** As a reviewer judging whether the adapters are real, I want synthetic fixtures to be indistinguishable in shape from real provider responses, so that synthetic mode exercises the same normalization code path as live mode.

#### Acceptance Criteria

1. The Lead Ingestion Layer shall store synthetic fixtures as JSON files under `fixtures/<provider>/`, one file per provider endpoint exercised.
   - Verify: Each exercised provider endpoint has a corresponding fixture JSON file.
2. When an adapter runs in synthetic mode, it shall pass the fixture through the identical raw-schema validation and `normalize()` path used in live mode.
   - Verify: Synthetic and live modes invoke the same normalize function body.
3. The repository shall contain, for every provider, a test asserting that its fixtures validate against the adapter's declared raw Pydantic schema.
   - Verify: Every provider fixture validates against its declared raw schema.
4. If a fixture fails its provider's raw-schema validation, then the test suite shall fail and name the provider and the failing field.
   - Verify: An invalid fixture fails the suite naming provider and field.
5. Each provider's fixtures SHALL exercise both a positive and a negative outcome for whatever that provider actually contributes — a Target Profile match and no match where the provider supports technographic targeting, a suppression and a non-suppression where it reports CRM state, a verified and an unverifiable address where it verifies email.
   - Verify: Every adapter has a positive and a negative fixture for its own contribution, and no fixture carries a field its provider's documented schema does not define.
6. Each fixture file shall record, in a sibling metadata entry, the provider documentation URL and the date the schema was verified.
   - Verify: Every fixture has a doc URL and verification date recorded.

---

### Requirement 6: Ingestion Run Orchestration and Partial-Failure Isolation

**Objective:** As a demo operator, I want one provider being down, throttled, or unauthorized to degrade the run rather than abort it, so that a single bad credential never costs me the whole demo.

#### Acceptance Criteria

1. If a source raises any error during `fetch_raw()` or `normalize()`, then the Ingestion Orchestrator shall record the failure against that source and continue with the remaining enabled sources.
   - Verify: One failing source still yields results from all other sources.
2. If a source returns HTTP 401 or 403, then the Ingestion Orchestrator shall mark that source `unauthorized`, skip its remaining calls in the run, and not retry.
   - Verify: An unauthorized source is skipped without retries and clearly marked.
3. If a source returns HTTP 429, then the Ingestion Orchestrator shall mark that source `rate_limited` and apply the backoff policy in Requirement 7 before any further call to it.
   - Verify: A 429 response marks the source rate-limited and triggers backoff.
4. If every enabled source fails, then the Ingestion Orchestrator shall exit with a non-zero status and a summary naming each source and its failure class.
   - Verify: All-sources-failed exits non-zero listing each source and failure class.
5. When a run completes with at least one successful source, the Ingestion Orchestrator shall exit zero and report per-source counts of attempted, succeeded, and failed.
   - Verify: Partial success exits zero with per-source attempt and failure counts.
6. The Ingestion Orchestrator shall enforce a configured per-run wall-clock timeout, after which in-flight sources are cancelled and recorded as `timed_out`.
   - Verify: Exceeding the run timeout cancels sources and marks them timed_out.
7. The Ingestion Orchestrator SHALL execute registered sources concurrently through a bounded worker pool whose size is read from configuration and defaults to 4.
   - Verify: A run with eight registered sources never has more than four in flight, and the bound comes from config rather than a literal.
8. The Ingestion Orchestrator's concurrency bound SHALL be independent of each adapter's own rate limiter, so that cross-source parallelism never relaxes a provider's documented per-provider throttle.
   - Verify: Running sources in parallel leaves every single provider's observed request rate at or below its declared limit.
9. The Ingestion Orchestrator SHALL execute a run in two phases: Discovery, which runs every `search`-capable source, followed by Enrichment, which runs every `enrich`-capable source over the leads Discovery produced. The Enrichment work list SHALL be mechanical — every lead Discovery produced — and SHALL involve no scoring or qualification judgment.
   - Verify: A zero-key run exercises every registered adapter, including enrich-only ones; the work list is derived without reference to any score.
10. Within the Enrichment phase the Ingestion Orchestrator SHALL order sources by their declared `cost_class`, `charge_unit`, and `yields_suppression`, running free suppression-bearing sources before any credit-bearing source, and SHALL remove from the remaining work list any lead marked `opt_out` or `suppressed`.
    - Verify: A lead a free source reports as opted out reaches no credit-bearing source; reordering is achieved by changing declared attributes, never by editing a list.
11. Where a source declares `charge_unit: per_company`, the Ingestion Orchestrator SHALL invoke it once per deduplicated `CompanySignal` rather than once per lead.
    - Verify: Two leads at one company cause exactly one per-company call.

---

### Requirement 7: Rate Limiting, Retry, and Backoff

**Objective:** As an integrator of eight third-party APIs, I want each provider's documented limits respected client-side, so that the system is a well-behaved API consumer and does not burn paid credits on avoidable 429s.

#### Acceptance Criteria

1. Each adapter shall declare its rate limit in requests per time window, sourced from the provider's published documentation, and the Ingestion Orchestrator shall throttle calls to that limit client-side before dispatch.
   - Verify: Each adapter declares a documented rate limit the orchestrator enforces.
2. When a provider returns a `Retry-After` header, the Ingestion Orchestrator shall wait at least the indicated interval before the next call to that provider.
   - Verify: A Retry-After header delays the next call by that interval.
3. If a provider returns a 5xx response or a connection error, then the Ingestion Orchestrator shall retry with exponential backoff and jitter up to a configured maximum attempt count.
   - Verify: Transient 5xx errors retry with jittered exponential backoff, bounded.
4. If a provider returns 4xx other than 408 or 429, then the Ingestion Orchestrator shall not retry.
   - Verify: Non-retryable 4xx responses produce exactly one attempt each.
5. While a source is in synthetic mode, the Ingestion Orchestrator shall bypass throttling and retry logic entirely.
   - Verify: Synthetic-mode runs incur zero throttle delay and zero retries.
6. The Ingestion Orchestrator shall record, per source per run, the count of throttle waits, retries, and 429 responses.
   - Verify: Run record shows throttle waits, retries, and 429s per source.

---

### Requirement 8: Cross-Source Deduplication and Merge

**Objective:** As a qualification stage, I want one record per real human with every contributing source's view preserved, so that data-confidence scoring can count agreeing sources and I never message the same person twice.

#### Acceptance Criteria

1. The Merge Engine SHALL treat normalized `linkedin_url` as the strongest match key, where normalization lowercases the host and path and strips query string, fragment, and trailing slash. LinkedIn ranks above email because it survives a change of employer, whereas a work address does not.
   - Verify: LinkedIn URLs differing only by query string or case merge; a lead whose employer changed still resolves to one identity.
2. When `linkedin_url` is absent on either lead, the Merge Engine shall match on equal normalized **verified** `email` values, where normalization lowercases and trims the address.
   - Verify: Case-differing identical verified emails merge into exactly one lead record.
3. When both `linkedin_url` and a verified `email` are absent on either lead, the Merge Engine SHALL match on normalized `full_name` paired with **any** domain of **any** `Employment` of either lead, current or historical, and SHALL require at least one further agreeing attribute — shared title, shared employer, or overlapping employment dates. The Merge Engine SHALL NOT match on name alone.
   - Verify: One person recorded at a former employer by one source and a current employer by another resolves to a single lead; two different people sharing a name and a past employer, with no further agreement, remain separate.
4. When two sources supply different values for the same canonical field, the Merge Engine shall select the winner by this ordered rule: higher configured source trust rank first; on a tie, provider-stated confidence ahead of heuristic confidence; on a further tie, higher field-level confidence; on a further tie, more recent fetch timestamp.
   - Verify: Field conflicts resolve by trust rank, then confidence origin, then confidence, then recency; a heuristic confidence never outranks a provider-stated one.
5. When a field conflict is resolved, the Merge Engine shall retain every losing value with its own provenance record, marked as superseded.
   - Verify: Losing conflict values persist as superseded provenance, not discarded.
6. The Merge Engine shall preserve per-field provenance across the merge such that each field on the merged lead resolves to the source that supplied the winning value.
   - Verify: Every merged field resolves to its originating source name.
7. The merged lead shall carry the set of all contributing source names and a per-field count of agreeing sources.
   - Verify: Merged lead lists contributing sources and per-field agreement counts.
8. The Merge Engine shall produce identical output regardless of the order in which sources are ingested.
   - Verify: Shuffling source order yields byte-identical merged lead records.
9. If a later ingestion run produces a lead that matches an already-persisted lead, then the Merge Engine shall merge into the existing record rather than creating a duplicate.
   - Verify: Re-running ingestion creates zero additional lead records.
10. Source trust rank shall be read from `config/`, not hardcoded in Python.
    - Verify: Changing trust rank in config alters conflict outcomes without code edits.
11. The Lead Merge stage SHALL NOT use an unverified email address as a dedupe match key; an unverified address MAY act only as corroborating evidence raising the confidence of a match already established by a stronger key.
    - Verify: Two leads sharing only an unverified email never merge; the same pair merges once that address is verified.
12. The Lead Merge stage SHALL be non-destructive: each source's contribution SHALL persist as its own immutable provenance record, and the `CanonicalLead` SHALL be a derived projection recomputed from those records. No unmerge or split operation SHALL exist.
    - Verify: Changing the match rule and recomputing yields a different canonical set with zero mutation or deletion of contribution records.
13. The Merge Engine SHALL read a configured set of Identity Exclusions — specific values barred from acting as a match key — from `config/`, and SHALL bump `projection_version` when that set changes. Because a match-rule change is global and cannot undo one wrongly merged cluster, this exclusion set is the only supported repair for an over-merge.
    - Verify: Adding a value to the exclusion set and recomputing separates a previously over-merged cluster, with no contribution record mutated or deleted.
14. The Merge Engine SHALL disqualify as a match key any email address that any source reports against two or more distinct normalized person names, independently of the configured exclusion set.
    - Verify: A role address such as `info@` that two sources attach to different people never merges them, with no entry added to the exclusion set.
15. When a cluster carries two or more distinct non-null `full_name` values that do not match under normalization, the Ingestion Orchestrator SHALL flag it as a suspected over-merge on the run report, without blocking the run.
    - Verify: A deliberately over-merged cluster is named on the run report; a correctly merged cluster is not.
16. A `CompanySignal`'s identity SHALL be the set of its normalized registrable domains rather than a single domain, clustered by the same order-independent mechanism used for leads. Registrable domains SHALL be derived using a Public Suffix List pinned to a dated snapshot, so that merge output stays reproducible under 8.8.
    - Verify: Two records for one company under different domains resolve to one `CompanySignal`; a PSL update cannot silently change an existing projection.
17. The `CompanySignal`'s chosen primary domain SHALL be decided by a vote across contributing sources weighted by source trust rank, SHALL be used only for display, and SHALL NOT appear in any match rule.
    - Verify: Changing the primary domain alters no clustering outcome.
18. Where a primary-domain vote ties exactly, the Merge Engine MAY resolve it through a language model constrained to choose among the candidate domains already in the set. The resolution SHALL be persisted as a record carrying the chosen domain, candidate set, model, prompt version, and timestamp, and the projection SHALL read that stored record rather than calling the model. In synthetic mode the model SHALL NOT be called; the tie SHALL resolve to the lowest-sorted candidate and be flagged on the run report.
    - Verify: Projection remains a pure function and recompute is byte-identical; a synthetic run makes no model call; the model can never return a domain outside the candidate set.

---

### Requirement 9: Persistence via SQLAlchemy

**Objective:** As a pipeline whose stages communicate through the database, I want canonical leads, provenance, and run records persisted engine-agnostically, so that the demo runs on zero-setup SQLite and the same code runs on Postgres.

#### Acceptance Criteria

1. The Lead Store shall persist canonical leads, per-field provenance records, and ingestion run records using SQLAlchemy 2.x ORM models.
   - Verify: All three entity types persist through SQLAlchemy ORM models.
2. When `DATABASE_URL` is unset, the Lead Store shall default to a local SQLite database requiring no external service.
   - Verify: Unset DATABASE_URL starts successfully against a local SQLite file.
3. When `DATABASE_URL` names a Postgres instance, the Lead Store shall operate against it with no change to adapter, orchestrator, or merge code.
   - Verify: Switching to Postgres changes only the DATABASE_URL environment value.
4. The Lead Store shall contain no engine-specific SQL, dialect-conditional branches, or raw SQL strings outside Alembic migrations.
   - Verify: Grep finds no dialect branching outside the migrations directory.
5. The repository shall contain an automated test that exercises the ingestion-to-persistence path against both SQLite and Postgres.
   - Verify: The same persistence test passes on SQLite and Postgres.
6. When an ingestion run persists leads, the Lead Store shall write them in a transaction that rolls back entirely on failure.
   - Verify: A mid-write failure leaves zero partially-written leads behind.
7. Schema changes shall be delivered as Alembic migrations, with no runtime `create_all()` in application code paths.
   - Verify: Application startup performs no implicit schema creation call.
8. The Lead Store SHALL retain raw provider payloads in a `raw_responses` table held separate from the canonical schema and excluded from default queries, under a configurable retention period defaulting to indefinite in synthetic mode and 30 days in live mode.
   - Verify: Raw payloads land only in `raw_responses`, and live-mode records older than the retention window are purged.

---

### Requirement 10: Credential Configuration and `.env.example`

**Objective:** As a reviewer cloning this repository, I want every required key documented in one file with no real values, so that I know exactly what live mode needs and that nothing secret was committed.

#### Acceptance Criteria

1. The repository shall contain a committed `.env.example` listing one entry per provider credential, plus `DATABASE_URL`, `LLM_PROVIDER`, and `LLM_MODEL`.
   - Verify: `.env.example` lists every provider credential plus DB and LLM settings.
2. Each `.env.example` entry shall follow the `<PROVIDER>_API_KEY` convention, carry an empty or clearly-placeholder value, and carry a comment naming the provider doc URL.
   - Verify: Every entry has a placeholder value and a documentation URL.
3. Where a provider needs more than a single key, `.env.example` shall list each required variable separately.
   - Verify: Multi-credential providers list each required variable on its own line.
4. The repository shall contain an automated test asserting that every credential variable read by a registered adapter appears in `.env.example`.
   - Verify: A test fails if an adapter reads an undocumented environment variable.
5. The Lead Ingestion Layer shall never log, persist, or include in a report the value of any credential.
   - Verify: No log line or database row contains a credential value.
6. If a `.env` file is present, then the Lead Ingestion Layer shall load it without overriding variables already set in the process environment.
   - Verify: Process environment variables take precedence over `.env` file values.

---

### Requirement 11: Outbound-Send Prohibition at the Ingestion Boundary

**Objective:** As the owner of this PoC's reputation, I want the ingestion layer structurally incapable of contacting a real person, so that no provider integration can become an accidental send path.

#### Acceptance Criteria

1. The Lead Ingestion Layer shall issue only read-oriented provider operations and shall never invoke a provider operation that sends an email, message, connection request, or sequence enrollment.
   - Verify: No adapter calls a provider send, sequence, or messaging endpoint.
2. The Lead Ingestion Layer shall never create, update, or delete a record in a provider's system of record.
   - Verify: Every adapter issues read-only operations against provider systems.
3. The repository shall contain an automated test asserting that no adapter references a provider send or sequence endpoint path.
   - Verify: A test fails if any adapter references a send-capable endpoint.
4. If a provider's suppression, opt-out, or do-not-contact signal is present on a record, then the adapter shall carry that signal onto the canonical lead's compliance flags.
   - Verify: Provider opt-out signals surface as canonical compliance flags.
5. While running in synthetic mode, the Lead Ingestion Layer shall make no outbound network connection of any kind.
   - Verify: Synthetic runs open zero outbound sockets under network assertion.

---

### Requirement 12: Apollo.io Source Adapter

**Objective:** As a discovery stage, I want Apollo.io people and organization search to supply firmographics, titles, and technographic evidence, so that companies matching the configured Target Profile surface with role and company context.

#### Acceptance Criteria

1. The Apollo Source adapter shall authenticate by sending the API key in the `x-api-key` HTTP header, and shall not send it as a bearer token.
   - Verify: Apollo requests carry x-api-key; bearer-token auth is never used.
2. The Apollo Source adapter shall contribute person identity (name, title, LinkedIn URL), organization firmographics, and technology-stack evidence to the canonical lead.
   - Verify: Apollo-sourced leads populate identity, firmographic, and technographic fields.
3. The Apollo Source adapter shall declare both `search` and `enrich` capabilities, and shall invoke enrichment only for leads the pipeline has already marked for enrichment.
   - Verify: Apollo enrich calls occur only for pipeline-flagged leads.
4. When Apollo returns a 429, the adapter shall read the `retry-after` response header and back off by at least that many seconds.
   - Verify: Apollo 429 handling honors the retry-after header value exactly.
5. The Apollo Source adapter shall record the per-window quota remaining from the `x-minute-requests-left`, `x-hourly-requests-left`, and `x-24-hour-requests-left` response headers on the run record, tolerating their absence.
   - Verify: Apollo run record captures remaining quota or notes header absence.
6. If Apollo returns a 403 indicating a scoped key lacks endpoint access, then the adapter shall mark itself `unauthorized` with a message naming the endpoint and the scope cause.
   - Verify: Apollo scope 403 produces an unauthorized mark naming the endpoint.
7. The Apollo Source adapter shall branch error handling on Apollo's stable error `code` identifier rather than on the human-readable message text.
   - Verify: Apollo error handling switches on code, never on message text.
8. The Apollo Source adapter SHALL treat discovery and enrichment as two distinct calls: `POST https://api.apollo.io/api/v1/mixed_people/api_search` for search, which costs 0 credits and returns no emails or phone numbers and obfuscated last names, and `POST https://api.apollo.io/api/v1/people/match` for enrichment, which costs 1 credit per match.
   - Verify: Search calls consume zero credits and yield no email or phone fields; only `people/match` calls are credit-bearing.
9. WHERE Apollo returns `match_confidence` of `none`, the Apollo Source adapter SHALL record a no-match outcome and SHALL NOT count a credit against the run.
   - Verify: A `match_confidence=none` response contributes no canonical lead and no credit to the run record.
10. The Apollo Source adapter SHALL paginate search with `page` and `per_page` (≤100) and SHALL NOT request beyond Apollo's 500-page ceiling.
   - Verify: Apollo paging caps `per_page` at 100 and stops at page 500.
11. The Apollo Source adapter SHALL branch error handling on the nested `error_details.code` value rather than top-level error fields, which Apollo removes on 2027-02-16.
   - Verify: No code path reads a top-level Apollo error field; rate-limit handling matches on `USAGE.RATE_LIMIT.API_RATE_LIMIT_EXCEEDED`.
12. The Apollo Source adapter SHALL source technographic evidence from the `currently_using_any_of_technology_uids[]` family of search parameters, using snake_case UIDs.
   - Verify: Technographic filters are sent as snake_case technology UIDs on the search call.
13. The Apollo Source adapter SHALL read its technology UIDs from the Target Profile's Apollo vocabulary (Requirement 23) rather than hard-coding them, and SHALL emit a warning naming any configured UID that returns zero matches across an entire run.
    - Verify: Correcting a UID is a config edit, not a code change; a deliberately bogus UID produces a named warning on the run record.
14. The Apollo Source adapter SHALL NOT implement the asynchronous phone-waterfall flow, which is out of scope for this feature.
   - Verify: No webhook or polling path exists for Apollo phone enrichment.

---

### Requirement 13: HubSpot Source Adapter

**Objective:** As the qualification stage, I want to know whether a lead is already a HubSpot contact, customer, or open deal, so that existing relationships and suppressed contacts are excluded before any outreach is drafted.

#### Acceptance Criteria

1. The HubSpot Source adapter shall authenticate with a private app access token sent as `Authorization: Bearer <token>`, and shall not use the retired HubSpot API key scheme.
   - Verify: HubSpot requests use a bearer private app token only.
2. The HubSpot Source adapter shall contribute CRM-state signals — contact existence, lifecycle stage, owner, last activity date, open deal presence, and marketing opt-out — to the canonical lead.
   - Verify: HubSpot-sourced leads populate CRM lifecycle, ownership, and opt-out fields.
3. The HubSpot Source adapter SHALL target the date-based API version path `/crm/objects/2026-09/contacts/...`, SHALL NOT use the `/crm/v3/objects/...` legacy path, and SHALL hold the version string in configuration rather than as a code literal.
   - Verify: Changing HubSpot API version requires a config edit, not code.
4. The HubSpot Source adapter shall throttle CRM Search calls to the documented search-specific limit of 5 requests per second per token, independent of the account burst limit.
   - Verify: HubSpot search calls never exceed five requests in one second.
5. The HubSpot Source adapter shall maintain a client-side token bucket for search calls rather than relying on rate-limit response headers, which HubSpot does not return on search responses.
   - Verify: HubSpot throttling works with zero rate-limit response headers present.
6. If HubSpot returns a 429, then the adapter shall read `policyName` from the error body to distinguish a secondly from a daily limit and back off accordingly.
   - Verify: HubSpot 429 backoff differs for secondly versus daily policy names.
7. If a HubSpot lookup finds a contact flagged as opted out or suppressed, then the adapter shall set the canonical lead's compliance flags so downstream stages exclude it.
   - Verify: HubSpot opt-out contacts arrive with compliance flags set true.
8. The HubSpot Source adapter shall use REST transport, because the HubSpot remote MCP server requires interactive OAuth 2.1 PKCE unsuitable for a headless pipeline.
   - Verify: HubSpot adapter performs no MCP connection or OAuth browser flow.

---

### Requirement 14: Google Search Source Adapter

**Objective:** As an intent-signal stage, I want public web evidence of Target Profile usage — job posts, conference talks, GitHub mentions — so that qualification has evidence no contact database carries.

#### Acceptance Criteria

1. The Google Search Source adapter shall obtain results through a pluggable search backend interface, with SerpApi as the default backend.
   - Verify: Swapping the search backend requires no change to adapter logic.
2. The Google Search Source adapter shall not depend on the Google Custom Search JSON API, which is closed to new customers and shuts down on 2027-01-01.
   - Verify: No code path calls the deprecated Custom Search JSON API.
3. The Google Search Source adapter shall authenticate to SerpApi by sending the API key as the `api_key` request parameter and shall keep the key server-side.
   - Verify: SerpApi requests carry api_key; the key never reaches a client.
4. The Google Search Source adapter shall read results from the `organic_results` array and shall parse optional blocks such as answer box and knowledge graph defensively, tolerating their absence.
   - Verify: Responses lacking optional blocks normalize without raising an error.
5. The Google Search Source adapter shall contribute intent and technographic evidence as evidence records carrying the matched query, result URL, snippet text, and retrieval date.
   - Verify: Each search evidence record carries query, URL, snippet, and date.
6. The Google Search Source adapter shall mark every snippet and title it contributes as untrusted external text under the prompt-injection guardrail.
   - Verify: Search snippets carry the untrusted-content marker in their provenance.
7. The Google Search Source adapter shall not assume more than ten results per request, because Google discontinued the `num` parameter.
   - Verify: Adapter paginates rather than requesting one hundred results inline.
8. If SerpApi returns a 429 for exceeding hourly throughput or an exhausted search balance, then the adapter shall mark itself `rate_limited` and surface which of the two causes applied.
   - Verify: SerpApi 429 handling distinguishes throughput exhaustion from balance exhaustion.

---

### Requirement 15: Leadfeeder Source Adapter — DEFERRED

> **Deferred, not withdrawn** (ADR-0005). Not built in this feature. The research below is verified and stands as the specification for when it is.

**Objective:** As an intent-signal stage, I want to know which companies visited the website and what they looked at, so that qualification can weight demonstrated interest over cold firmographic fit.

#### Acceptance Criteria

1. The Leadfeeder Source adapter SHALL authenticate against the current public API by sending an `X-Api-Key: <key>` header (or an OAuth token carrying the `web_visits:read` scope), SHALL NOT use the legacy `Authorization: Token token=<key>` scheme, and SHALL send a descriptive `User-Agent` header identifying LeadForge.
   - Verify: Leadfeeder requests carry the `X-Api-Key` header and a LeadForge `User-Agent`; no request uses the legacy Token scheme.
2. The Leadfeeder Source adapter shall resolve an account identifier before requesting account-scoped resources, and shall surface a named error when no account is accessible.
   - Verify: Missing accessible account raises a named error, not a null dereference.
3. The Leadfeeder Source adapter shall contribute company-level visit intent — visiting company, industry, employee count, location, and visit recency — to the canonical lead.
   - Verify: Leadfeeder-sourced records populate visiting company and visit recency.
4. The Leadfeeder Source adapter shall throttle to the documented limit of 100 requests per minute, applied per token for unscoped calls and per account for account-scoped calls.
   - Verify: Leadfeeder calls never exceed one hundred requests per minute.
5. The Leadfeeder Source adapter shall request only the fields it maps, using sparse fieldsets, rather than retrieving full records.
   - Verify: Leadfeeder requests specify sparse fieldsets limited to mapped fields.
6. Where Leadfeeder supplies company-level rather than person-level records, the adapter shall produce a company-scoped canonical lead with null person identity fields rather than fabricating a contact.
   - Verify: Company-only Leadfeeder records yield null person identity fields.
7. The Leadfeeder Source adapter SHALL call `GET https://api.leadfeeder.com/v1/web-visits/companies` with `account_id`, `start_date`, `end_date`, and `page[num]`/`page[size]` (≤100), and SHALL parse the JSON:API envelope `{data[{id, type:"company_location", relationships{company, location}}], meta{pagination{page_count, page_num, total_count}, request_id}}`.
   - Verify: Leadfeeder calls hit the `/v1/web-visits/companies` endpoint and normalize from the JSON:API `data`/`meta` envelope.

---

### Requirement 16: Hunter.io Source Adapter

**Objective:** As a contactability stage, I want discovered and verified email addresses with a deliverability judgment, so that outreach is only drafted for leads that can actually be reached.

#### Acceptance Criteria

1. The Hunter Source adapter shall authenticate against base URL `https://api.hunter.io/v2/` using the API key in the `X-API-KEY` header.
   - Verify: Hunter requests target the v2 base URL with X-API-KEY auth.
2. The Hunter Source adapter shall contribute discovered email addresses, a deliverability verdict, a confidence score, and supporting sources to the canonical lead.
   - Verify: Hunter-sourced leads carry email, verdict, confidence, and sources.
3. The Hunter Source adapter shall use Email Finder when a domain and person name are known, and Email Verifier when an address is already known.
   - Verify: Known addresses route to verifier; unknown addresses route to finder.
4. When Email Verifier returns HTTP 202, the adapter shall poll until a verdict is returned or a configured poll budget is exhausted.
   - Verify: A 202 verification response is polled to verdict or budget exhaustion.
5. The Hunter Source adapter shall throttle Email Verifier to 10 requests per second and 300 per minute, and Domain Search and Email Finder to 15 per second and 500 per minute.
   - Verify: Hunter throttles match the documented per-endpoint rate limits.
6. If Hunter returns HTTP 451 indicating restricted personal-data processing, then the adapter shall record a compliance restriction on the lead and contribute no contact data for it.
   - Verify: A 451 response sets a compliance restriction and contributes no email.
7. The Hunter Source adapter shall distinguish 403 (rate limit reached) from 429 (usage limit reached) and shall not treat 403 as an authorization failure.
   - Verify: Hunter 403 is handled as throttling, never as unauthorized.
8. Where the sandbox key `test-api-key` is configured, the adapter shall operate against Hunter's dummy responses without consuming credits.
   - Verify: Sandbox key runs consume zero Hunter credits.

---

### Requirement 17: UpLead Source Adapter — DEFERRED

> **Deferred, not withdrawn** (ADR-0005). Not built in this feature. The research below is verified and stands as the specification for when it is.

**Objective:** As a discovery and contact stage, I want person and company lookups with verified contact detail, so that the pipeline has a second discovery provider whose coverage differs from Apollo's.

#### Acceptance Criteria

1. The UpLead Source adapter shall authenticate against base URL `https://api.uplead.com/v2/` by sending the API key as a bare `Authorization` header value with no `Bearer` prefix.
   - Verify: UpLead requests send a bare key without any Bearer prefix.
2. The UpLead Source adapter shall look up a person by email when an address is known, and by the combination of first name, last name, and domain otherwise.
   - Verify: UpLead lookups use email when present, name plus domain otherwise.
3. The UpLead Source adapter shall contribute person identity, location, email, phone, social links, and nested company data to the canonical lead.
   - Verify: UpLead-sourced leads populate identity, contact, and company fields.
4. The UpLead Source adapter shall use the combined person-and-company lookup in preference to separate calls when both are needed, because one person result costs one credit.
   - Verify: Combined lookups replace paired person and company calls.
5. The UpLead Source adapter shall record credits consumed per run on the run record.
   - Verify: UpLead run record reports the number of credits consumed.
6. If UpLead returns no match for a query, then the adapter shall record a no-match outcome and contribute no canonical lead rather than emitting an empty one.
   - Verify: UpLead no-match outcomes create zero canonical lead records.
7. The UpLead Source adapter SHALL throttle to the documented limit of 500 requests per minute, and SHALL read the `X-RateLimit-*` and `Retry-After` response headers to adapt its own pacing.
   - Verify: UpLead calls never exceed 500 requests per minute and back off per `Retry-After` when present.

---

### Requirement 18: ZoomInfo Source Adapter — DEFERRED

> **Deferred, not withdrawn** (ADR-0005). Not built in this feature. ZoomInfo was the only OAuth2 client-credentials provider, so the token cache and proactive refresh path of 18.3 and 18.7 is retained in the design as a documented seam — otherwise the auth abstraction collapses to static-header auth with nothing to prove it general.

**Objective:** As a discovery stage, I want ZoomInfo's contact and company data plus its intent and WebSights signals, so that the highest-coverage enterprise dataset is represented in the architecture.

#### Acceptance Criteria

1. The ZoomInfo Source adapter SHALL authenticate against the GTM API using OAuth2 client credentials — `POST https://api.zoominfo.com/gtm/oauth/v1/token` with HTTP Basic auth (`client_id:client_secret`) and a form body of `grant_type=client_credentials` — and SHALL NOT use the legacy Enterprise JWT, PKI, or username-and-password flows.
   - Verify: ZoomInfo auth calls the GTM OAuth2 token endpoint with Basic auth; no legacy JWT/PKI/password code path exists.
2. The ZoomInfo Source adapter SHALL send `Authorization: Bearer <access_token>` and `Content-Type: application/vnd.api+json` on every GTM data request.
   - Verify: Every ZoomInfo data request carries the bearer access token and the JSON:API content type.
3. WHILE a run outlives the access token's `expires_in` window, the ZoomInfo Source adapter SHALL re-authenticate proactively, and SHALL cache the token for the remainder of that window.
   - Verify: Runs outlasting `expires_in` refresh the token without a 401 occurring.
4. The ZoomInfo Source adapter shall contribute contact identity, title, seniority, company firmographics, and, where entitled, intent and WebSights signals to the canonical lead.
   - Verify: ZoomInfo-sourced leads populate identity, firmographic, and intent fields.
5. When ZoomInfo flags a record as opted out through its compliance surface, the adapter shall set the canonical lead's compliance flags.
   - Verify: ZoomInfo opt-out records arrive with compliance flags set true.
6. If ZoomInfo authentication fails, then the adapter shall mark itself `unauthorized` for the run and shall not attempt data calls.
   - Verify: Failed ZoomInfo auth produces zero subsequent data requests.
7. The ZoomInfo Source adapter SHALL keep the authenticate step separate from the search step so one access token is reused across calls within its validity window, and SHALL page `contacts/search` using `page[number]` and `page[size]` (≤100).
   - Verify: One ZoomInfo token serves multiple paged searches within its `expires_in` window.
8. The ZoomInfo Source adapter SHALL call `POST /gtm/data/v1/contacts/search` for discovery, `POST /gtm/data/v1/contacts/enrich` for contact enrichment with at most 25 IDs per call, and `POST /gtm/data/v1/companies/technologies/enrich` for Target Profile technology evidence.
   - Verify: Discovery, contact enrichment, and technology enrichment each call their documented GTM endpoint, with enrich batches capped at 25.
9. The ZoomInfo Source adapter SHALL be registered with `live_access: unavailable` and SHALL default to synthetic mode, because GTM API access requires an enterprise contract with no self-serve path; its live code path SHALL nonetheless be implemented and unit-tested against fixtures.
   - Verify: A default run uses ZoomInfo fixtures, and the live path carries unit-test coverage that needs no credentials.

---

### Requirement 19: Clay Source Adapter — DEFERRED

> **Deferred, not withdrawn** (ADR-0005). Not built in this feature. Its enrichment schema was never verified (Risk R3), which made it the weakest candidate to build against.

**Objective:** As a discovery and enrichment stage, I want Clay's waterfall enrichment reachable from the pipeline, so that the architecture demonstrates an MCP-capable source alongside the REST ones.

#### Acceptance Criteria

1. The Clay Source adapter SHALL authenticate against the public API base `https://api.clay.com/public/v0` by sending the `clay-api-key` header, and SHALL NOT use the hosted MCP server, a browser session cookie, or `clay login` CLI state.
   - Verify: Clay requests carry `clay-api-key` against `api.clay.com/public/v0`; no MCP, cookie, or CLI-state path is exercised.
2. The Clay Source adapter shall contribute enriched person and company fields, recording Clay as the provenance source without claiming the underlying provider Clay used.
   - Verify: Clay-sourced fields record Clay as source, not downstream providers.
3. Where Clay's MCP transport is selected instead of REST, the adapter shall satisfy the same `BaseLeadSource` contract and produce identical canonical output.
   - Verify: REST and MCP Clay transports produce identical canonical leads.
4. The Clay Source adapter shall not use a session cookie, browser session, or `clay login` CLI state for authentication, because neither is viable in a headless pipeline.
   - Verify: Clay adapter never reads a session cookie or CLI login state.
5. If a Clay run is asynchronous and returns a pending status, then the adapter shall poll for completion within a configured budget rather than blocking indefinitely.
   - Verify: Pending Clay runs poll to completion or bounded budget exhaustion.
6. The Clay Source adapter SHALL execute search as the documented two-step flow — `POST /search/query-mode` to create the search, then `POST /search/query-mode/{search_id}/run` with `limit` in 1..500 (default 20) — and SHALL paginate by repeating the run call against the server-held cursor, reading `has_more`, `source_type`, and `exhaustion_reason`.
   - Verify: Clay paging issues repeated run calls against the server cursor and stops on `has_more=false` or a recorded `exhaustion_reason`.
7. IF Clay returns HTTP 429, THEN the Clay Source adapter SHALL honour the `Retry-After` header; IF Clay returns HTTP 402, THEN the adapter SHALL record a quota-exhausted outcome on the run record and SHALL NOT retry within the same run. The adapter SHALL NOT attempt Clay table writes, which are read-only and Enterprise-only.
   - Verify: A 429 honours `Retry-After`, a 402 halts Clay for that run without retry, and zero table-write calls are issued.

---

### Requirement 20: Transport Abstraction

**Objective:** As the pipeline, I want to be unable to tell whether a source speaks REST or MCP, so that transport choice stays a private decision of each adapter.

#### Acceptance Criteria

1. The Lead Ingestion Layer shall expose no transport-specific type, parameter, or branch above the `BaseLeadSource` interface.
   - Verify: No orchestrator or merge code references REST or MCP types.
2. Where a provider offers an MCP server and it is viable headlessly, the adapter may use MCP transport while satisfying the identical `BaseLeadSource` contract.
   - Verify: MCP-backed adapters pass the same contract tests as REST ones.
3. When an adapter changes transport, no file outside that adapter's module and its fixtures shall require modification.
   - Verify: Transport change touches only the adapter module and fixtures.
4. The Lead Ingestion Layer shall use `httpx` for REST transport with configured timeouts on every request.
   - Verify: Every REST request carries an explicit connect and read timeout.
5. If an MCP transport requires an interactive browser-based authorization flow, then the adapter shall fall back to REST transport or to synthetic mode and log the reason.
   - Verify: Interactive-auth MCP paths fall back and log the fallback reason.

---

### Requirement 21: Ingestion Run Observability and Reporting

**Objective:** As a reviewer running the demo once, I want the run to explain itself, so that I can see which sources ran live, which ran synthetic, what each contributed, and what failed.

#### Acceptance Criteria

1. When an ingestion run starts, the Ingestion Orchestrator shall create a persisted run record carrying run identifier, start time, and the resolved mode of every enabled source.
   - Verify: Each run persists one record listing every source and mode.
2. When an ingestion run finishes, the Ingestion Orchestrator shall persist per-source counts of records fetched, leads normalized, leads merged into existing records, and failures by class.
   - Verify: Run record reports fetched, normalized, merged, and failed counts.
3. The Ingestion Orchestrator shall emit structured logs that never contain credential values or full raw provider payloads.
   - Verify: Logs contain no credential values and no full raw payloads.
4. When a lead is merged, the Merge Engine shall log the matching key used and the fields whose conflicts were resolved.
   - Verify: Each merge logs its match key and resolved conflicting fields.
5. The ingestion run record shall be queryable from the database so the pipeline report is a query rather than in-memory state.
   - Verify: The run report is produced entirely from database queries.

---

### Requirement 22: Untrusted Provider Content Handling

**Objective:** As the personalization stage that will feed this text to an LLM, I want provider-supplied free text quarantined as data, so that a crafted job posting or bio cannot steer the model.

#### Acceptance Criteria

1. The Lead Ingestion Layer shall classify all provider-supplied free text — search snippets, bios, job descriptions, company descriptions — as untrusted external content.
   - Verify: Every free-text provider field is classified as untrusted content.
2. The Lead Ingestion Layer shall store untrusted content verbatim without executing, interpolating, or interpreting any instruction-like content within it.
   - Verify: Untrusted text is stored verbatim with zero template interpolation.
3. When untrusted content is persisted, the Lead Store shall preserve the untrusted classification on the stored field.
   - Verify: Untrusted classification survives the write and read round trip.
4. The Lead Ingestion Layer shall enforce a configured maximum length on each untrusted text field, truncating and flagging rather than storing unbounded provider text.
   - Verify: Oversized provider text is truncated and flagged, never stored whole.

---

### Requirement 23: Target Profile

**Objective:** As an operator targeting my own market, I want the definition of who we are looking for to be configuration rather than code, so that the system finds leads against any set of technologies and competitors and is not built around one vendor.

#### Acceptance Criteria

1. The Lead Ingestion Layer SHALL read its targeting definition — technologies, competitors, and keyword templates — from a Target Profile in `config/`, and no requirement, module, or test SHALL name a specific vendor as a built-in assumption.
   - Verify: Changing the Target Profile retargets the whole pipeline with no code edit; a search of the source tree for any vendor name returns only configuration and example fixtures.
2. The Target Profile SHALL store, for each canonical term, the per-provider vocabulary that expresses it, because providers use mutually incompatible identifiers for the same technology.
   - Verify: One term carries an Apollo UID, a web-search phrase list, and any other provider vocabulary in a single entry.
3. Adding a term to the Target Profile SHALL require editing exactly one configuration file, and adding a source SHALL require one new class plus that source's column in the same file.
   - Verify: Adding a term touches one file; adding a source touches one module plus one config column.
4. Where a provider's targeting identifiers are issued by that provider, the adapter SHALL validate configured identifiers against the provider's own lookup surface at startup in live mode and SHALL warn, naming any identifier the provider does not recognise.
   - Verify: A stale identifier produces a named startup warning rather than silently returning zero results.
5. The repository SHALL ship one worked example Target Profile.
   - Verify: A clean clone runs the demo against the shipped profile with no edits.

---

### Requirement 24: Company Signals and Employment

**Objective:** As a qualification stage, I want company-level data kept separate from people, so that records with no human in them are never mistaken for contactable leads and per-company costs are paid once.

#### Acceptance Criteria

1. The Lead Ingestion Layer SHALL expose a `CompanySignal` type for company-level data, distinct from `CanonicalLead`, and SHALL NOT represent a company as a lead with null person-identity fields.
   - Verify: No canonical lead exists without at least one person-identity field; company-only provider records produce `CompanySignal` records.
2. The Lead Ingestion Layer SHALL relate a `CanonicalLead` to a `CompanySignal` through an `Employment` carrying the employer, and, where the provider supplies them, the title and whether the employment is current.
   - Verify: A lead's employer is reachable only through `Employment`, and historical employments persist alongside the current one.
3. A `CompanySignal` SHALL be shared by every `CanonicalLead` employed there rather than duplicated per lead.
   - Verify: Two leads at one company reference one `CompanySignal` record.
4. The Lead Ingestion Layer SHALL carry `Signal` records on both `CanonicalLead` and `CompanySignal`, each with its own strength, recorded during ingestion and never used to resolve a merge conflict.
   - Verify: Changing a signal strength alters no merge outcome.

---

## Resolved Decisions

Every `[NEEDS CLARIFICATION]` marker raised during generation has been answered. Five were closed against `research.md` during the orchestrator cross-check; the remaining six were decided by the project owner on 2026-10-04. Nothing below blocks approval.

### Decided by the project owner, 2026-10-04

| # | Requirement | Decision |
|---|---|---|
| 1 | 6.7-6.8 | Sources run **concurrently** through a bounded pool, default 4, configurable. The pool bound is independent of each adapter's own throttle. |
| 2 | 8.11 | An unverified email **may not** act as a dedupe match key — only as corroborating evidence behind a stronger key. Catch-all domains make pattern-guessed addresses collide across distinct people. |
| 3 | 8.12 | **No unmerge path.** Merge is non-destructive: contributions persist immutably and `CanonicalLead` is a recomputed projection, so a rule change plus recompute replaces surgical splitting. |
| 4 | 9.8 | Raw payloads **retained** in a separate `raw_responses` table, excluded from default queries, retention indefinite in synthetic mode and 30 days in live mode. |
| 5 | 18.9 | Live ZoomInfo access assumed **unobtainable**. ZoomInfo is `live_access: unavailable` and synthetic-only by default; the live path is still written and unit-tested against fixtures. |
| 6 | 12.13 | Apollo technology UIDs come from `config/technology_uids.yaml` with `datastax` / `apache_cassandra` as defaults, plus a zero-match warning. Never hard-coded. — **superseded 2026-10-05 by decision 16:** UIDs now come from the Target Profile's Apollo vocabulary (Requirement 23), and no vendor is a default. |

### Decided during the domain grill, 2026-10-05

| # | Requirement | Decision |
|---|---|---|
| 7 | 1.5, 1.7, 24 | A **Lead** is one human. Company-level data is a **Company Signal** joined by an **Employment**. `lead_scope` and `company_domain` are removed. ADR-0001. |
| 8 | 6.9–6.11 | The run is **two-phase** — Discovery then Enrichment — with a mechanical work list, so enrich-only adapters are exercised in the keyless demo and leads can reach a verified email. ADR-0002. |
| 9 | 2.7, 6.10 | Enrichment order is **derived** from `cost_class`, `charge_unit`, and `yields_suppression`. Free suppression-bearing sources run first and suppressed leads leave the work list before any credit is spent. ADR-0002. |
| 10 | 16, 6.11 | Hunter is **batched by domain**; `per_company` sources are called once per deduplicated company, not once per lead. ADR-0002. |
| 11 | 1.8, 8.4 | Three distinct concepts: **Source Trust Rank**, **Field Confidence**, **Signal Strength**. Confidence is provider-stated, retains its raw value and scale, and is never fabricated. A heuristic fallback is permitted but labelled and always outranked by a provider-stated value. |
| 12 | 8.1–8.3 | Match keys are ordered by **durability**: LinkedIn, then verified email, then name plus any employment domain with corroboration. A work email and a company domain both change with employer; LinkedIn does not. ADR-0003. |
| 13 | 8.13–8.15 | **Identity Exclusions** are the only repair for an over-merge and are specified rather than left as a mitigation. An address reported against two distinct person names is structurally disqualified. An over-merge detector reports suspect clusters without blocking. ADR-0003. |
| 14 | 8.16–8.18 | A **Company Signal**'s identity is a **domain set** under a pinned Public Suffix List. The primary domain is a trust-weighted vote, is display-only, and never appears in a match rule. An exact tie may escalate to a language model constrained to the candidate set, persisted so projection stays pure — never called in synthetic mode. |
| 15 | 1.9, 2.8 | **Negative Evidence** ("asked, no match") is distinct from **Not Applicable** ("never able to answer"). Collapsing them would penalise leads for their provider's schema rather than their own reality. |
| 16 | 5.5, 12.13, 23 | DataStax is a **Target Profile**, not a domain concept. No requirement names a vendor. 5.5 is capability-scoped, because three providers carry no technographic field and a literal 5.5 forced fixtures to contradict 5.3. ADR-0004. |
| 17 | scope | **Four providers at full depth** rather than eight at surface depth; the sophisticated merge is kept. ADR-0005. |

### Closed against `research.md` during orchestrator cross-check

The Leadfeeder API choice (current public API, not the legacy token API), UpLead's rate limit (500/min) and plan-tier gate, ZoomInfo's endpoints and auth (GTM API, OAuth2 client credentials), the Clay surface (public `v0` REST API), and Clay's rate-limit behaviour (429 + `Retry-After`, 402 on quota).

## Verification Note

The generating agent could not read `research.md` (the lean-ctx size gate blocked native `Read` and no `ctx_read` tool was in its toolset), so every provider fact in its draft was re-derived from official documentation. The orchestrator has since read `research.md` and cross-checked Requirements 12-19 against it.

**Corrections applied against `research.md`:** HubSpot moved from the legacy `/crm/v3/...` path to the date-based `/crm/objects/2026-09/...` path; Leadfeeder moved from the legacy `Token token=` scheme to the current public API with `X-Api-Key`; ZoomInfo moved from the legacy Enterprise JWT/PKI flow to the GTM API with OAuth2 client credentials; Clay's surface fixed to the `public/v0` REST API with its two-step query-mode flow; UpLead's throttle set to the documented 500/min; Apollo's free-search / paid-enrich credit split made explicit.

**Removed:** a criterion asserting Hunter returns HTTP 222 for an indeterminate verification. `research.md` documents only 202 (verification still running — poll), 403, 429, and 451 for Hunter; the 222 claim was introduced during generation, is unsupported, and duplicated the 202 handling already covered by Requirement 16.4.

**Still unverified — confirm during design:** items marked ⚠ in `research.md`, notably the Apollo technology UIDs for DataStax and Cassandra (now config-driven per Requirement 12.13, so a wrong guess is a config fix rather than a code change) and the Clay routine/function schemas for enrichment.
