# Implementation Plan

Feature: `lead-source-adapters` — the Lead Ingestion Layer.

Domain language is binding: `CONTEXT.md` terms (Lead, Company Signal, Signal, Signal
Strength, Source Trust Rank, Field Confidence, Confidence Origin, Employment, Match Key,
Identity Exclusion, Over-merge, Under-merge, Target Profile, Discovery, Enrichment,
Suppression, Credit, Verified Email, Negative Evidence, Not Applicable) are used
throughout. Their listed synonyms are not.

Scope is four providers at full depth — Apollo, HubSpot, Google Search, Hunter (ADR-0005).
Requirements 15, 17, 18 and 19 are deferred, not withdrawn; see **Deferred Requirements**
at the end.

**Reading the `(P)` marker.** A `(P)` task shares no file with any other `(P)` task that is
ready at the same time, so two agents can take them concurrently. Within one major task,
sub-tasks still run in the order listed — the marker buys cross-major parallelism, not
intra-major. Blocking dependencies are stated in the detail bullets wherever two tasks look
interchangeable but are not.

All work lands inside the single vertical slice `src/leadforge/lead_ingestion/`
(Decision D1, Option B), so every task's blast radius is one folder by construction. One
task = one commit.

---

- [x] 1. Scaffold the ingestion slice
- [x] 1.1 Create the vertical slice package with pinned dependencies and tooling
  - Set up one folder owning adapters, canonical types, normalization, merge, store, migrations, fixtures, and tests — no shared `models/`, `db/`, `utils/`, or `common/` package
  - Pin the dependency set: Pydantic v2, SQLAlchemy 2.x, Alembic, `httpx`, `tenacity`, `structlog`, Typer, PyYAML, `python-dotenv`, and a registrable-domain library with a dated Public Suffix List snapshot
  - Wire lint, type-check, and the test runner, including async test support and an HTTP-boundary mocking library
  - Add the ingest command entrypoint as a stub that resolves the slice and returns a placeholder outcome
  - Add the static guard that no module outside this slice may import a provider raw schema
  - _Requirements: 1.1_

- [x] 1.2 Define the named error taxonomy for every failure class
  - One exception hierarchy rooted at a source error carrying the source name, with named subclasses for unauthorized, rate-limited, Credit-exhausted, transient, timed-out, compliance-restricted, and normalization failures
  - Separate named errors for fixture schema failure, duplicate source name, undeclared endpoint, and no accessible provider account
  - Every error names the provider and, where applicable, the offending raw field path and canonical path
  - No bare exception handlers and no silent fallback anywhere in the slice
  - _Requirements: 2.6, 6.2, 6.3_

---

- [x] 2. Canonical Lead model with per-field provenance
- [x] 2.1 Define the Lead, Company Signal, and Employment entities
  - Expose exactly one canonical Lead type as the only lead type a downstream stage may consume, rejecting undeclared fields at construction and frozen against mutation
  - Carry identity attributes (email with its verification status, LinkedIn URL, full name), zero or more Employments, technographic evidence, intent evidence, and compliance flags for Suppression
  - Expose Company Signal as a separate entity for organization-level information; a record with no person identity is never a Lead
  - Reach a Lead's employer only through an Employment naming a Company Signal, carrying title and whether the Employment is current — no employer domain attribute on the Lead itself
  - Share one Company Signal across every Lead employed there rather than copying it per Lead
  - _Requirements: 1.1, 1.4, 1.5, 1.7, 24.1, 24.2, 24.3_

- [x] 2.2 Define the provenance record with Field Confidence and Confidence Origin
  - Attach to every populated field a provenance record carrying source name, data mode, fetch timestamp, and the provider's raw field path
  - Record Field Confidence as the provider's own stated certainty, retaining its verbatim raw value and the name of the scale it was expressed on
  - Record a Confidence Origin of provider-stated, heuristic, or none; a field with no provider certainty records none rather than a fabricated default number
  - Keep a superseded flag on the record so a losing contribution can be retained rather than dropped
  - _Requirements: 1.2, 1.8_

- [x] 2.3 Add the untrusted provider-text type
  - Wrap all provider-supplied free text — snippets, bios, job descriptions, company descriptions — in a distinct type with no implicit string conversion, so concatenating it into an instruction is a type error rather than a convention breach
  - Carry a truncation flag and the original length on the wrapper
  - Mark the corresponding provenance record as untrusted external text
  - _Requirements: 1.6, 22.1_

- [x] 2.4 Distinguish Negative Evidence from Not Applicable
  - Record Negative Evidence when a source could answer for a canonical path, was asked, and reported no match
  - Record Not Applicable when a source's API carries no such field, so it was never able to answer
  - Keep both distinguishable downstream and both distinct from the plain absence of a value
  - Confirm a source with no surface for a question never contributes Negative Evidence for it
  - Done as `SourceAbsence` with `AbsenceKind` in `models.py` (choices.md, 2.4). The model enforces only the shape (Negative Evidence names a raw field path, Not Applicable has none); checking against the adapter's declared surface is task 3.1, emitting it is 3.3 and 5.1, and consuming it is 16.3
  - _Requirements: 1.9_

- [x] 2.5 Carry Signals with Signal Strength on both entities
  - Attach zero or more Signals to a Lead and to a Company Signal, each with exactly one Signal Strength
  - Record Signal Strength during ingestion only; it never participates in resolving a field conflict
  - Prove by test that changing a Signal Strength alters no merge outcome
  - _Requirements: 24.4_

---

- [x] 3. Adapter contract
- [x] 3.1 Declare the adapter contract and capability flags
  - Declare name, capabilities, rate limit, data mode, raw fetch, and normalization as the complete adapter surface, with normalization returning contributions rather than a canonical Lead
  - Make the fetch and normalize members abstract so an incomplete subclass fails at construction, before any network call is possible
  - Declare Discovery and Enrichment capability flags independently so a provider may support either, both, or neither
  - Declare on each adapter the set of canonical paths its API can answer for, so "could answer" versus "never able to answer" is data on the adapter and not an inference
  - Validate every `SourceAbsence` (task 2.4) against that declaration at the adapter boundary: Negative Evidence is accepted only for a canonical path the source declares answerable and only when its `raw_field_path` is a surface the source declares; Not Applicable only for a path it does not declare. Reject any other combination with a named error
  - Prove by test that an adapter with no surface for a canonical path cannot emit Negative Evidence for it, and that the model-level check in 2.4 (which cannot see the source's API) is backed by this one
  - _Requirements: 1.9, 2.1, 2.2, 2.3_

- [x] 3.2 Declare cost class, charge unit, and Suppression yield
  - Declare whether a source is free or Credit-bearing, whether it charges per Lead, per company, or per call, and whether it yields Suppression
  - Make Enrichment order derivable from these declarations so adding a source places it in the correct tier with no edit outside its own module
  - _Requirements: 2.7_

- [x] 3.3 Declare the per-source Target Profile vocabulary
  - Declare, for each Target Profile term a source can express, the provider vocabulary that expresses it
  - Treat an empty declaration as Not Applicable for that term rather than as a report of no match, represented as a `SourceAbsence` of kind Not Applicable (task 2.4) so a source with no targeting surface never produces Negative Evidence for a Target Profile term
  - _Requirements: 1.9, 2.8, 23.2_

- [x] 3.4 Declare read-only endpoints and environment-only credentials
  - Declare every provider path an adapter may reach, with read-only expressed in the type system so a write endpoint cannot be constructed
  - Read credentials only through declared environment variable names resolved from the process environment, never from configuration files or source literals
  - Keep the orchestration layer free of any reference to a concrete adapter class, interacting only through the declared contract
  - _Requirements: 2.4, 2.5, 10.5, 11.1, 11.2_

---

- [x] 4. Transport port
- [x] 4.1 Define the transport port with the REST implementation
  - One send interface over which every provider call travels, carrying an endpoint, parameters, body, and headers, with no transport-specific type or branch visible above the adapter contract
  - REST implementation with an explicit connect and read timeout on every request, never relying on library defaults
  - Reject at the transport any path not present in the calling adapter's declared endpoint map
  - Confine a transport change to the owning adapter module and its fixtures
  - _Requirements: 20.1, 20.3, 20.4_

- [x] 4.2 (P) Add the fixture transport for synthetic mode
  - Load the provider's stored JSON response for the requested endpoint and return it through the same port the REST implementation satisfies
  - Hold no socket, so a synthetic run makes zero outbound HTTP or MCP calls as a structural property rather than a policed behaviour
  - Traverse the identical raw-schema validation and normalization body that live mode traverses, with no mode conditional inside any adapter
  - Blocked on 4.1 for the port definition; parallel with 4.3 and 4.4, which touch separate modules
  - _Requirements: 4.1, 5.1, 5.2, 11.5_

- [x] 4.3 (P) Add the MCP transport seam with interactive-auth fallback
  - Satisfy the identical adapter contract over MCP so an MCP-backed source passes the same contract assertions as a REST-backed one
  - Fall back to REST transport or to synthetic mode when a server demands an interactive browser authorization flow, logging the reason for the fallback
  - Blocked on 4.1; parallel with 4.2 and 4.4
  - _Requirements: 20.2, 20.5_

- [x] 4.4 (P) Define the authentication strategy port with a token cache
  - Strategies for the schemes the four built adapters need: custom key header, bearer token, and request-parameter key
  - Keep an OAuth2 client-credentials strategy backed by a token cache that refreshes before the stated validity window expires and reuses one token across many calls, so the authentication abstraction is not shaped around static-header schemes alone
  - Resolve every credential from the declared environment variable names only
  - Blocked on 4.1; parallel with 4.2 and 4.3
  - _Requirements: 2.5, 18.3, 18.7_

---

- [x] 5. Normalization and provenance emission
- [x] 5.1 Emit per-field provenance from declarative field rules
  - Declare each provider's field mapping as data — canonical path, raw field path, untrusted marker, optional transform — and emit provenance mechanically from that declaration rather than from hand-written assignments
  - Emit exactly one provenance record for each rule whose raw path resolves to a value, and zero for each that resolves to nothing, leaving the canonical field empty
  - For each rule that resolves to nothing, also emit a `SourceAbsence` (task 2.4) from the same declaration: Negative Evidence when the adapter declares the canonical path answerable and the source was asked and returned no match, Not Applicable when the adapter declares no surface for it; emit neither when the source was never queried for that field. A `SourceAbsence` is never a provenance record, so the zero-provenance rule above still holds
  - Cross-check that every `UntrustedText` value is paired with provenance marked untrusted (follow-up from the 2.3 ledger entry)
  - Carry the resolved data mode onto every provenance record so synthetic-sourced fields are distinguishable from live-sourced ones
  - Fail the suite when a field present in a fixture is neither mapped nor explicitly listed as intentionally ignored
  - _Requirements: 1.2, 1.3, 1.9, 4.6_

- [x] 5.2 Store untrusted provider text verbatim under a length bound
  - Store untrusted text exactly as supplied, with no interpolation, interpretation, or execution of instruction-like content inside it
  - Hold each untrusted field to a configured maximum length, truncating and flagging with the original length preserved rather than storing unbounded provider text
  - _Requirements: 22.2, 22.4_

- [x] 5.3 Raise a named normalization error identifying provider and field
  - Fail fast when a raw payload violates the adapter's declared raw schema, naming the provider, the offending raw field path, and the canonical path it was mapping to
  - Never coerce or substitute a value at this boundary
  - _Requirements: 2.6_

---

- [x] 6. Lead Store persistence
- [x] 6.1 Define the append-only contribution log and projection schema
  - Typed ORM models for ingestion runs, per-source runs, raw responses, source contributions, contribution fields, Lead identities, Match Keys, the canonical Lead projection, and canonical field provenance
  - Make the Match Key table uniquely constrained on key type and value, so identity resolution is enforced by the database rather than by application logic
  - Restrict column types to the engine-portable set, with no dialect-specific types, no dialect-conditional branches, and no raw SQL strings outside migrations
  - Treat contributions and contribution fields as append-only: no code path updates or deletes them
  - _Requirements: 9.1, 9.4_

- [x] 6.2 Ship the schema through migrations only
  - Express every schema change as a migration revision, with the test suite upgrading to head
  - Perform no implicit schema creation on application startup
  - _Requirements: 9.7_

- [x] 6.3 Resolve the database engine from configuration with a local default
  - Default to a local zero-setup SQLite file when the database URL is unset
  - Operate against Postgres when the database URL names one, with no change to adapter, orchestration, or merge code
  - _Requirements: 9.2, 9.3_

- [x] 6.4 Scope write transactions to the per-source contribution batch
  - Commit the run record at run start so it survives any source failure
  - Wrap each source's contribution batch in one transaction that rolls back entirely on failure, leaving the run to continue with the remaining sources
  - Shield the transaction scope from run-timeout cancellation so a cancelled source is either fully written or fully rolled back, never torn
  - _Requirements: 9.6_

- [x] 6.5 (P) Retain raw provider payloads under a retention policy
  - Land raw payloads only in the dedicated raw-response table, never on the canonical tables, reachable only through an explicit repository call so default queries exclude them
  - Set retention to indefinite in synthetic mode and to a configurable window defaulting to thirty days in live mode, with a purge routine that deletes expired rows
  - Blocked on 6.1 and 6.3; parallel with 6.6, which touches the contribution-field mapping instead
  - _Requirements: 9.8_

- [x] 6.6 (P) Preserve the untrusted classification across the round trip
  - Persist the untrusted marker, truncation flag, and original length alongside the stored value
  - Prove by test that the classification survives a write and read round trip unchanged
  - Blocked on 6.1 and 6.3; parallel with 6.5
  - _Requirements: 22.3_

- [x] 6.7 Exercise the ingestion-to-persistence path on both engines
  - One parameterized test covering the same path against SQLite and against Postgres
  - Make the Postgres leg a required gate rather than a silent skip, so the criterion is actually met rather than half-proved
  - _Requirements: 9.5_

---

- [x] 7. Source Registry and plug-and-play discovery
- [x] 7.1 Discover adapters by package scan with duplicate-name rejection
  - Discover every adapter subclass placed in the slice's adapter package without any edit to the registry, the orchestration layer, the canonical model, or the database layer
  - Raise a named duplicate-source error at startup when two sources declare the same name
  - Default a source absent from configuration to enabled at lowest Source Trust Rank, so adding a source stays one module rather than a module plus a mandatory configuration entry
  - _Requirements: 3.1, 3.3_

- [x] 7.2 Exclude disabled sources without instantiating them
  - Omit a source disabled in configuration from the active source list and never construct its adapter object, so no provider call is reachable for it
  - _Requirements: 3.4_

- [x] 7.3 Publish source descriptors including live-access classification
  - Report for each registered source its name, capability flags, resolved data mode, whether credentials were found, which declared variables are missing, and whether its declared rate limit is published by the provider or self-imposed
  - Classify each source's live access as available, gated, or unavailable, declared on the adapter with an optional configuration override
  - _Requirements: 3.5, 3.6_

- [x] 7.4 Prove plug-and-play with a runtime-registered throwaway source
  - Register a throwaway adapter subclass at runtime, assert it appears in the registry listing, and assert it completes a synthetic ingestion run end to end
  - Treat this as the load-bearing falsifiable form of the plug-and-play claim; a passing adapter count proves nothing this test does not
  - _Requirements: 3.2_

---

- [x] 8. Data mode resolution and credential manifest
- [x] 8.1 (P) Resolve live versus synthetic mode with a stated reason
  - Resolve in precedence order: per-source configuration override first, then a global mode override, then a live-access classification of unavailable, then all declared credentials present, then synthetic with the missing variable names as the reason
  - Honour a per-source override even when a credential is present
  - Emit one structured log line per source naming the resolved mode and the reason for it
  - Treat an empty credential value as absent
  - Parallel with 8.2, 8.3, and 8.4 — four separate modules, no shared writes
  - _Requirements: 4.2, 4.3, 4.4_

- [x] 8.2 (P) Load the environment file without overriding the process environment
  - Load a present environment file at startup while leaving any variable already set in the process environment untouched
  - Parallel with 8.1, 8.3, and 8.4
  - _Requirements: 10.6_

- [x] 8.3 (P) Generate the credential example file from the registry manifest
  - Generate the committed example file from the union of every registered adapter's declared variables plus the database URL and the LLM provider and model settings
  - Give every entry a placeholder or empty value and a comment naming the provider documentation URL, listing each variable separately where a provider needs more than one
  - Assert as a lockfile-style check that the generated output equals the committed file, so an adapter reading an undocumented variable fails the suite
  - Omit the retired Google Custom Search variables, since no code path may call that API
  - Blocked on 7.3 for the registry listing; parallel with 8.1, 8.2, and 8.4
  - _Requirements: 10.1, 10.2, 10.3, 10.4_

- [x] 8.4 (P) Redact credential values from every log and report
  - Seed a log redaction processor with the values of every variable in the credential manifest at startup, so an accidental interpolation is scrubbed before emission
  - Emit structured logs that carry no credential value and no full raw provider payload
  - Assert no log line and no database row outside the raw-response table contains a credential value
  - Parallel with 8.1, 8.2, and 8.3
  - _Requirements: 10.5, 21.3_

---

- [x] 9. Target Profile configuration
- [x] 9.1 Read targeting terms and provider vocabularies from configuration
  - Read the targeting definition — technologies, competitors, and keyword templates — from a Target Profile in configuration, with no module, test, or requirement naming a specific vendor as a built-in assumption
  - Store for each canonical term the per-provider vocabulary that expresses it, since providers use mutually incompatible identifiers for the same technology
  - Keep adding a term to one configuration file, and adding a source to one module plus that source's column in the same file
  - _Requirements: 23.1, 23.2, 23.3_

- [x] 9.2 (P) Ship one worked example Target Profile
  - Ship a complete example profile so a clean clone runs the demo with no edits
  - Keep every vendor name confined to configuration and example fixtures
  - Blocked on 9.1 for the profile schema; parallel with 9.3
  - _Requirements: 23.5_

- [x] 9.3 (P) Validate provider-issued targeting identifiers at startup
  - Check configured provider-issued identifiers against the provider's own lookup surface at startup in live mode, warning and naming any identifier the provider does not recognise
  - Make a stale identifier produce a named startup warning rather than silently returning no matches
  - Blocked on 9.1; parallel with 9.2
  - _Requirements: 23.4_

---

- [x] 10. Rate limiting, retry, and backoff
- [x] 10.1 (P) Build the composite multi-window token bucket
  - One named bucket per source and endpoint class, since several providers throttle per endpoint class rather than per account
  - AND every window of a multi-window bucket, so both a per-second and a per-minute limit must permit before dispatch
  - Take each declared limit from the provider's published documentation where one exists, and mark a self-imposed default as undocumented so the registry listing can surface the distinction
  - Let a provider-supplied retry interval always win over the computed backoff
  - Record per source per run the count of throttle waits, retries, and throttling responses
  - Parallel with 10.2 — separate modules, no shared writes
  - _Requirements: 7.1, 7.2, 7.6_

- [x] 10.2 (P) Apply bounded jittered retry over the error taxonomy
  - Retry on transient and rate-limited errors only, with exponential backoff and full jitter to a configured maximum attempt count
  - Dispatch retry decisions on the error type, never on an HTTP status code, so a provider with inverted status conventions needs no special case above its adapter
  - Make every other failure class exactly one attempt
  - Parallel with 10.1
  - _Requirements: 7.3, 7.4_

- [x] 10.3 Bypass throttling and retry entirely in synthetic mode
  - Construct neither the bucket nor the retry policy for a source resolved to synthetic, so a synthetic run incurs zero throttle delay and zero retries by absence rather than by a conditional
  - Blocked on 10.1 and 10.2, which it wires together
  - _Requirements: 7.5_

---

- [x] 11. Ingestion Orchestrator
- [x] 11.1 Run enabled sources through a bounded worker pool
  - Execute registered sources concurrently behind a cross-source bound read from configuration and defaulting to four
  - Keep the cross-source bound and each adapter's own pacing as separate mechanisms, so neither can relax the other and cross-source parallelism never lifts a provider's declared throttle
  - Assert both that peak in-flight count never exceeds the bound with eight registered sources, and that the bound came from configuration rather than a literal
  - Resolve each source's data mode before any pool slot is acquired, so a synthetic source never reserves throttle capacity
  - _Requirements: 6.7, 6.8_

- [x] 11.2 Isolate per-source failures by failure class
  - Record any fetch or normalization error against its own source and continue with the remaining enabled sources
  - Mark an unauthorized source, skip its remaining calls for the run, and never retry it
  - Mark a throttled source rate-limited and apply the backoff policy before any further call to it
  - _Requirements: 6.1, 6.2, 6.3_

- [x] 11.3 Map the run outcome to a process exit code
  - Exit non-zero with a summary naming each source and its failure class when every enabled source failed
  - Exit zero when at least one source succeeded, reporting per-source counts of attempted, succeeded, and failed
  - _Requirements: 6.4, 6.5_

- [x] 11.4 Hold the run to a wall-clock timeout
  - Wrap the whole run in a configured wall-clock bound, cancelling in-flight sources on expiry and recording them as timed out
  - _Requirements: 6.6_

- [x] 11.5 Sequence Discovery before Enrichment over a mechanical work list
  - Run every Discovery-capable source first, then every Enrichment-capable source over the Leads Discovery produced
  - Derive the Enrichment work list mechanically from every Lead Discovery produced, with no scoring or qualification judgment and no reference to any score
  - Treat an Enrichment phase with an empty supplied work list as a no-op rather than an error
  - Confirm a zero-credential run exercises every registered adapter, including Enrichment-only ones
  - _Requirements: 6.9_

- [x] 11.6 Derive the Enrichment order from declared cost attributes
  - Order Enrichment sources by declared cost class, charge unit, and Suppression yield, running free Suppression-bearing sources before any Credit-bearing source
  - Remove from the remaining work list any Lead a source has reported as suppressed, so a suppressed Lead reaches no Credit-bearing source
  - Do any reordering by changing declared attributes, never by editing a maintained list
  - _Requirements: 6.10_

- [x] 11.7 Call per-company sources once per distinct Company Signal
  - Invoke a source declaring per-company charging once per clustered Company Signal rather than once per Lead
  - Assert that two Leads at one company cause exactly one per-company call
  - _Requirements: 6.11_

---

- [x] 12. Apollo Source adapter
- [x] 12.1 (P) Implement Apollo Discovery search with technographic targeting
  - Authenticate by custom key header only; never send the key as a bearer token
  - Call the credit-free mixed-people search endpoint, which returns no email or phone attributes and obfuscated last names, and contribute person identity, organization firmographics, and technology-stack evidence
  - Send technographic filters as snake_case technology identifiers drawn from the Target Profile's Apollo vocabulary, never hard-coded, and check each against a dated snapshot of Apollo's published supported-technology list at startup
  - Warn, naming the identifier, when a configured identifier returns no matches across an entire run
  - Cap page size at one hundred client-side and stop paging at the five-hundred-page ceiling
  - Bind the transport to the adapter: `BaseLeadSource.__init__` takes the transport (design: `__init__(transport, mode, config)`), the adapter never chooses its own, and the transport's endpoint map is the adapter's own `endpoints` rather than a separately passed copy. Resolves the task 4.1 `needs-user` entry on transport construction; 13.x, 14.x, and 15.x inherit the result
  - Parallel with 13.x, 14.x, and 15.x — each adapter owns its own module and fixture directory. Within this major, run 12.1 before 12.2 and 12.3
  - _Requirements: 12.1, 12.2, 12.8, 12.10, 12.12, 12.13_

- [x] 12.2 (P) Implement Apollo Credit-bearing Enrichment match
  - Declare both Discovery and Enrichment capabilities, invoking Enrichment only for Leads the pipeline has already placed on the work list
  - Call the people-match endpoint as a distinct call costing one Credit per match, keeping it the only Credit-bearing path
  - Record a no-match outcome contributing neither a Lead nor a Credit when Apollo reports a match confidence of none
  - Implement no webhook or polling path for the asynchronous phone waterfall, which is out of scope
  - Blocked on 12.1; parallel with 13.x, 14.x, and 15.x
  - _Requirements: 12.3, 12.8, 12.9, 12.14_

- [x] 12.3 (P) Classify Apollo errors and record per-window allowances
  - Branch error handling on Apollo's nested stable error code identifier, never on a top-level error field and never on human-readable message text
  - Match the documented rate-limit code and, on a throttling response, back off by at least the provider-supplied retry interval
  - Mark a scope-related forbidden response unauthorized with a message naming the endpoint and the scope cause
  - Record the per-window remaining request allowance from Apollo's response headers on the run record, tolerating their absence
  - Blocked on 12.1; parallel with 13.x, 14.x, and 15.x
  - _Requirements: 12.4, 12.5, 12.6, 12.7, 12.11_

---

- [x] 13. HubSpot Source adapter
- [x] 13.1 (P) Implement the HubSpot CRM-state lookup on the date-versioned path
  - Authenticate with a private app access token as a bearer token; never use the retired key scheme
  - Target the date-based API version path with the version string held in configuration rather than as a code literal, and never the legacy versioned path
  - Contribute CRM-state Signals — record existence, lifecycle stage, owner, last activity date, open deal presence, and marketing Suppression
  - Set the canonical compliance flags when a looked-up record is flagged suppressed, so downstream stages exclude it
  - Use REST transport only, with no MCP connection and no interactive browser authorization flow
  - Parallel with 12.x, 14.x, and 15.x. Within this major, run 13.1 before 13.2
  - _Requirements: 13.1, 13.2, 13.3, 13.7, 13.8_

- [x] 13.2 (P) Throttle HubSpot search client-side with policy-aware backoff
  - Throttle search calls to the documented search-specific limit of five requests per second per token, independent of the account burst limit
  - Keep the bucket purely client-side, since HubSpot returns no rate-limit headers on search responses
  - Read the policy name from a throttling error body to tell a per-second limit from a daily one and back off accordingly
  - Blocked on 13.1; parallel with 12.x, 14.x, and 15.x
  - _Requirements: 13.4, 13.5, 13.6_

---

- [ ] 14. Google Search Source adapter
- [x] 14.1 (P) Implement the pluggable search backend
  - Obtain results through a pluggable search backend interface with SerpApi as the default and only in-scope implementation, so swapping backends needs no change to adapter logic
  - Implement no path to the deprecated Custom Search JSON API, which is closed to new customers and shuts down on 2027-01-01
  - Send the SerpApi key as a request parameter kept server-side
  - Never assume more than ten results per request, paginating instead of requesting a hundred inline
  - Parallel with 12.x, 13.x, and 15.x. Within this major, run 14.1 before 14.2 and 14.3
  - _Requirements: 14.1, 14.2, 14.3, 14.7_

- [ ] 14.2 (P) Contribute untrusted web evidence as Company Signals
  - Read results from the organic-results array and parse optional answer-box and knowledge-graph blocks defensively, tolerating their absence without raising
  - Contribute intent and technographic evidence as Signals carrying the matched query, result URL, snippet text, and retrieval date
  - Wrap every snippet and title in the untrusted provider-text type so the marker reaches provenance
  - Produce Company Signals rather than Leads, since public web evidence carries no person identity
  - Blocked on 14.1; parallel with 12.x, 13.x, and 15.x
  - _Requirements: 14.4, 14.5, 14.6_

- [x] 14.3 (P) Tell SerpApi throughput exhaustion from balance exhaustion
  - Mark the source rate-limited on a throttling response and surface which of the two causes applied — hourly throughput exceeded or search balance exhausted
  - Blocked on 14.1; parallel with 12.x, 13.x, and 15.x
  - _Requirements: 14.8_

---

- [x] 15. Hunter Source adapter
- [x] 15.1 (P) Implement Hunter email discovery batched by domain
  - Authenticate against the v2 base URL with the custom key header
  - Contribute discovered addresses, a deliverability verdict, a Field Confidence, and the supporting sources behind each address
  - Route a known address to the verifier and an unknown name-plus-domain pair to the finder
  - Throttle the finder and verifier through separate named buckets at their documented per-endpoint limits, ANDing the per-second and per-minute windows of each
  - Treat the published sandbox key as live mode with a sandbox credential rather than a third data mode, consuming zero Credits
  - Declare per-company charging so the Orchestrator batches calls by domain rather than per Lead
  - Parallel with 12.x, 13.x, and 14.x. Within this major, run 15.1 before 15.2 and 15.3
  - _Requirements: 16.1, 16.2, 16.3, 16.5, 16.8_

- [x] 15.2 (P) Implement Hunter verification with bounded polling
  - Poll to a verdict when the verifier reports verification still running, stopping at a configured poll budget and recording budget exhaustion rather than blocking indefinitely
  - Map the resulting verdict onto the canonical email verification status, so only a Verified Email becomes eligible as a Match Key
  - Blocked on 15.1; parallel with 12.x, 13.x, and 14.x
  - _Requirements: 16.4_

- [x] 15.3 (P) Invert Hunter's status-code conventions in error classification
  - Override error classification so Hunter's forbidden response maps to rate-limited and its too-many-requests response maps to Credit-exhausted, keeping the inversion invisible above the adapter
  - Never treat Hunter's forbidden response as an authorization failure
  - Map the restricted-personal-data response to a compliance restriction, recording the restriction on the Lead and contributing no contact data for it
  - Blocked on 15.1; parallel with 12.x, 13.x, and 14.x
  - _Requirements: 16.6, 16.7_

---

- [ ] 16. Merge Engine
- [x] 16.1 (P) Extract Match Keys ordered by durability
  - Treat the normalized LinkedIn URL as the strongest Match Key — lowercased host and path, query string, fragment, and trailing slash stripped — because it survives a change of employer where a work address does not
  - Fall to an equal normalized Verified Email when a LinkedIn URL is absent on either side, lowercased and trimmed
  - Fall further to a normalized full name paired with any domain of any Employment, current or historical, requiring at least one corroborating attribute — shared title, shared employer, or overlapping Employment dates — and never matching on name alone
  - Bar an unverified address from acting as a Match Key, allowing it only to raise the Field Confidence of a match a stronger key already established
  - Parallel with 16.3, which touches the conflict-ordering module instead, and with all of 12.x through 15.x
  - _Requirements: 8.1, 8.2, 8.3, 8.11_

- [ ] 16.2 Cluster identities order-independently
  - Cluster contributions over the Match Key graph by union-find, so the result is independent of the order contributions arrive in
  - Prove order-independence by shuffling contributions across seeded permutations and comparing a canonical serialization byte for byte
  - Merge a later run's matching contribution into the existing identity rather than creating a second Lead, so re-running ingestion adds no Lead records
  - Blocked on 16.1 for key extraction
  - _Requirements: 8.8, 8.9_

- [ ] 16.3 (P) Resolve field conflicts under a total order
  - Select the winning value by Source Trust Rank first, then provider-stated Confidence Origin ahead of heuristic, then higher Field Confidence, then more recent fetch timestamp
  - Extend that partial order into a total one with deterministic final tiebreaks, since equal ranks, equal confidences, and identical fixture timestamps are ordinary in synthetic mode and byte-identical output is required regardless
  - Never let a heuristic Field Confidence outrank a provider-stated one, and keep any heuristic a pure function of the contribution
  - Read Source Trust Rank from configuration, never from a Python literal, so changing a rank alters conflict outcomes with no code edit
  - Keep `SourceAbsence` records (task 2.4) out of conflict resolution: neither Negative Evidence nor Not Applicable is a competing value, and Not Applicable never lowers a Lead's standing because of its provider's gaps; expose both kinds on the merged Lead so downstream stages can tell them apart and tell them from plain absence
  - Parallel with 16.1; blocked only on 2.2 for the provenance shape
  - _Requirements: 1.9, 8.4, 8.10_

- [ ] 16.4 Retain losing values as superseded provenance
  - Persist every losing value with its own provenance record marked superseded rather than dropping it
  - Resolve each field on the merged Lead back to the source that supplied the winning value
  - Carry on the merged Lead the set of all contributing source names and a per-field count of agreeing sources
  - Blocked on 16.3
  - _Requirements: 8.5, 8.6, 8.7_

- [ ] 16.5 Recompute the Lead projection non-destructively
  - Keep each source's contribution as its own immutable record and derive the canonical Lead as a projection recomputed from those records, with no unmerge or split operation existing anywhere
  - Make the projection a pure function of its cluster and the trust ranking, so running it twice yields identical bytes
  - Prove that changing the match rule and recomputing yields a different canonical set with zero mutation or deletion of contribution records
  - Recompute inside one transaction after all sources settle, leaving the prior projection valid on rollback
  - Blocked on 16.2 and 16.4
  - _Requirements: 8.12_

- [ ] 16.6 Apply Identity Exclusions as the only Over-merge repair
  - Read from configuration a set of Identity Exclusions — specific values barred from acting as a Match Key — and skip them during key extraction
  - Bump the projection version whenever that set changes, making the rule change observable rather than silent
  - Prove that adding a value to the exclusion set and recomputing separates a previously Over-merged cluster with no contribution record mutated or deleted
  - Blocked on 16.1 — shares the key-extraction module, so not parallel with it
  - _Requirements: 8.13_

- [ ] 16.7 Disqualify addresses reported against two distinct person names
  - Structurally bar from acting as a Match Key any address that any source reports against two or more distinct normalized person names, independently of the configured Identity Exclusions
  - Prove a role address that two sources attach to different people never merges them, with no entry added to the exclusion set
  - Blocked on 16.1 — shares the key-extraction module
  - _Requirements: 8.14_

- [ ] 16.8 Flag suspected Over-merges without blocking the run
  - Flag on the run report any cluster carrying two or more distinct non-null full names that do not match under normalization, without halting the run
  - Prove a deliberately Over-merged cluster is named on the report and a correctly merged cluster is not
  - Blocked on 16.2; writes to the run-report warning surface, so not concurrent with 18.2
  - _Requirements: 8.15_

- [ ] 16.9 Cluster Company Signals on a registrable-domain set
  - Make a Company Signal's identity the set of its normalized registrable domains rather than a single domain, clustered by the same order-independent mechanism used for Leads
  - Derive registrable domains from a Public Suffix List pinned to a dated snapshot, so an upstream list refresh cannot silently change an existing projection
  - Prove two records for one company under different domains resolve to one Company Signal
  - Blocked on 16.2
  - _Requirements: 8.16_

- [ ] 16.10 Elect a display-only primary domain by trust-weighted vote
  - Decide the chosen primary domain by a vote across contributing sources weighted by Source Trust Rank
  - Use it only for display and keep it absent from every match rule, so changing it alters no clustering outcome
  - Blocked on 16.9
  - _Requirements: 8.17_

- [ ] 16.11 Persist the constrained primary-domain tie resolution
  - On an exact tie, allow escalation to a language model constrained to choose among the candidate domains already in the set, so it can never return a domain outside that set
  - Persist the resolution as a record carrying the chosen domain, the candidate set, the model, the prompt version, and a timestamp, and have the projection read that stored record rather than calling the model, so recompute stays a pure function and byte-identical
  - Never call the model in synthetic mode; resolve the tie to the lowest-sorted candidate and flag it on the run report
  - Blocked on 16.10
  - _Requirements: 8.18_

- [ ] 16.12 Log the Match Key and resolved conflicts per merge
  - Carry the Match Key used and the fields whose conflicts were resolved on the projection result, so the log line is derived rather than hand-assembled
  - Blocked on 16.5
  - _Requirements: 21.4_

---

- [ ] 17. Fixture fidelity contract suite
- [ ] 17.1 Record fixture provenance metadata per provider endpoint
  - Store synthetic fixtures as JSON under a per-provider directory, one file per exercised provider endpoint
  - Record in a sibling metadata entry the provider documentation URL and the date the schema was verified, for every fixture file
  - Touches every adapter's fixture directory, so not concurrent with 12.x through 15.x
  - _Requirements: 5.1, 5.6_

- [ ] 17.2 (P) Validate every fixture against its declared raw schema
  - One test per provider asserting its fixtures validate against the adapter's declared raw schema
  - Fail the suite naming the provider and the failing field when a fixture violates its schema
  - Blocked on 17.1; parallel with 17.3 — separate test modules
  - _Requirements: 5.3, 5.4_

- [ ] 17.3 (P) Cover positive and negative outcomes per provider contribution
  - Give every adapter a positive and a negative fixture for whatever that provider actually contributes — a Target Profile match and no match where it supports technographic targeting, a Suppression and a non-Suppression where it reports CRM state, a Verified Email and an unverifiable address where it verifies addresses
  - Scope the criterion to each provider's own capability, so no fixture carries a field its provider's documented schema does not define
  - Blocked on 17.1; parallel with 17.2
  - _Requirements: 5.5_

---

- [ ] 18. Run observability and reporting
- [ ] 18.1 Persist the run record with every source's resolved mode
  - Create one persisted run record at run start carrying the run identifier, start time, the pool bound, a configuration snapshot that makes the run reproducible, and the resolved mode of every enabled source
  - _Requirements: 21.1_

- [ ] 18.2 Persist per-source counts and failure classes
  - On run completion, persist per source the counts of records fetched, Leads normalized, Leads merged into existing records, and failures by class, alongside throttle waits, retries, throttling responses, Credits consumed, remaining per-window allowances, and warnings
  - Record each source's live-access classification so the report states which sources could run live and which are synthetic-only by necessity
  - Blocked on 18.1; shares the run-report warning surface with 16.8
  - _Requirements: 21.2_

- [ ] 18.3 Produce the run report entirely from database queries
  - Make the run record queryable so the report is a query rather than in-memory state
  - Blocked on 18.1 and 18.2
  - _Requirements: 21.5_

---

- [ ] 19. Structural guardrails
- [ ] 19.1 (P) Assert no adapter reaches a send-capable endpoint
  - Issue only read-oriented provider operations, never one that sends an email, message, connection request, or sequence enrollment, and never one that creates, updates, or deletes a record in a provider's system of record
  - Add a static test asserting no adapter references a send, sequence, or messaging endpoint path
  - Parallel with 19.2 and 19.4 — separate static test modules
  - _Requirements: 11.1, 11.2, 11.3_

- [ ] 19.2 (P) Assert a synthetic run opens zero sockets
  - Run the full synthetic path with socket construction patched to raise, proving no outbound network connection of any kind is opened
  - Parallel with 19.1 and 19.4
  - _Requirements: 4.1, 11.5_

- [ ] 19.3 Propagate provider Suppression onto compliance flags
  - Carry any provider Suppression, do-not-contact, or restriction signal present on a record onto the canonical compliance flags
  - Combine compliance flags with OR semantics, so a Suppression from any source survives the projection and no merge can clear it
  - Blocked on 16.5 — touches the projection
  - _Requirements: 11.4_

- [ ] 19.4 (P) Assert the canonical-boundary and persistence structural rules
  - Assert no module outside the ingestion slice imports a provider raw schema
  - Assert the orchestration layer contains no reference to a concrete adapter class name
  - Assert no dialect branching and no raw SQL outside the migrations directory
  - Assert application startup performs no implicit schema creation
  - Parallel with 19.1 and 19.2
  - _Requirements: 1.1, 2.4, 9.4, 9.7_

---

- [ ] 20. Run the zero-credential ingestion end to end
  - With an empty environment, complete a full run that exercises every registered adapter across Discovery and Enrichment and persists at least one canonical Lead
  - Confirm the run exits zero and reports per-source attempt, success, and failure counts
  - Confirm a single failing source still yields results from every other source, and that all sources failing exits non-zero naming each failure class
  - Blocked on 11.x, 12.x through 15.x, 16.x, 17.x, and 18.x — this is the integration gate for the whole slice
  - _Requirements: 4.5, 6.1, 6.4, 6.5_

---

## Requirements Coverage

| Requirement | Tasks |
|---|---|
| 1 Canonical Lead with provenance | 2.1, 2.2, 2.3, 2.4, 5.1, 19.4 |
| 2 Adapter contract | 1.2, 3.1, 3.2, 3.3, 3.4, 5.3, 19.4 |
| 3 Registry and plug-and-play | 7.1, 7.2, 7.3, 7.4 |
| 4 Live and synthetic modes | 4.2, 5.1, 8.1, 19.2, 20 |
| 5 Fixture schema fidelity | 4.2, 17.1, 17.2, 17.3 |
| 6 Run orchestration | 1.2, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 20 |
| 7 Rate limiting and retry | 10.1, 10.2, 10.3 |
| 8 Match Keys and merge | 16.1, 16.2, 16.3, 16.4, 16.5, 16.6, 16.7, 16.8, 16.9, 16.10, 16.11 |
| 9 Persistence | 6.1, 6.2, 6.3, 6.4, 6.5, 6.7, 19.4 |
| 10 Credentials | 3.4, 8.2, 8.3, 8.4 |
| 11 Send prohibition | 3.4, 4.2, 19.1, 19.2, 19.3 |
| 12 Apollo adapter | 12.1, 12.2, 12.3 |
| 13 HubSpot adapter | 13.1, 13.2 |
| 14 Google Search adapter | 14.1, 14.2, 14.3 |
| 16 Hunter adapter | 15.1, 15.2, 15.3 |
| 18 token-cache seam only (18.3, 18.7) | 4.4 |
| 20 Transport abstraction | 4.1, 4.3 |
| 21 Observability | 8.4, 16.12, 18.1, 18.2, 18.3 |
| 22 Untrusted content | 2.3, 5.2, 6.6 |
| 23 Target Profile | 3.3, 9.1, 9.2, 9.3 |
| 24 Company Signals and Employment | 2.1, 2.5 |

## Deferred Requirements

Deferred under ADR-0005 — four providers at full depth rather than eight at surface depth.
Their research is verified and each remains the specification for later work. The
plug-and-play claim is proved by the runtime-registration test in 7.4, not by the adapter
count, so a fifth adapter would demonstrate nothing the fourth does not.

| Requirement | Deferred IDs | Rationale |
|---|---|---|
| 15 Leadfeeder | 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 15.7 | Company-only Signals repeat the architectural lesson Google Search already carries. Note that 15.4 cites a "documented limit of 100 requests per minute" the provider does not publish — reword it to a self-imposed configurable default before building (Correction N1) |
| 17 UpLead | 17.1, 17.2, 17.3, 17.4, 17.5, 17.6, 17.7 | A second Discovery provider adds coverage, not a new lesson beyond Apollo's Credit split |
| 18 ZoomInfo | 18.1, 18.2, 18.4, 18.5, 18.6, 18.8, 18.9 | Live access needs an enterprise contract with no self-serve path. 18.3 and 18.7 are **not** deferred — the token cache with proactive refresh is built in 4.4 so the authentication abstraction does not collapse to static-header schemes |
| 19 Clay | 19.1, 19.2, 19.3, 19.4, 19.5, 19.6, 19.7 | Enrichment routine and function schemas were never verified (Risk R3), making it the weakest candidate to build against |
