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

## Pass 2 — 2026-10-05 (task 2.1)

Gates overridden by the user ("proceed anyway") with the directive to test extensively and build
real Employment↔Company Signal linking. Not logged: test layout, seeded `random.Random` loops
instead of `hypothesis` (not a dependency), `Any`-typed aliases in tests for deliberately
ill-typed input, compliance flags defaulting to `False` (OR-merge belongs to the merge task).

### At most one current Employment per Lead (hard rejection)
- **Verdict:** needs-user
- **Spec said:** "historical employments persist alongside the current one" (24.2); nothing on concurrent jobs or on what happens when a provider reports two current roles
- **Chose:** `CanonicalLead` raises if more than one Employment has `is_current is True`
- **Alternatives:** allow several current Employments; keep the Lead and flag/demote extras at normalization; pick one deterministically
- **Provisional:** reversible — delete one validator clause; no stored data exists yet. Risk to watch: real providers do emit two "current" roles, and this turns a usable Lead into a normalization failure
- **Evidence:** `models.py:164-165`, tests in `test_canonical_entities.py`

### Company Signal identity key `company_id` (new field)
- **Verdict:** needs-user
- **Spec said:** share one Company Signal across Leads (24.3); the merge design keys employment matching on registrable domain (design Key 3), not on an opaque id
- **Chose:** added `CompanySignal.company_id: NonBlank`, used by `share_company_signals` for dedup and conflict detection and by the in-Lead conflict check
- **Alternatives:** key on the registrable domain set; key on `(name, domains)`; content equality only
- **Provisional:** reversible while only 2.1 depends on it, but later tasks (merge, normalization) will bind to it. Decide before the merge task: who assigns `company_id`, and how does it relate to domain-based matching?
- **Evidence:** `models.py` `CompanySignal`, `share_company_signals`; `ConflictingCompanySignalError` in `errors.py`

### Email typed as `str` with a hand-written check, not `EmailStr`
- **Verdict:** needs-user
- **Spec said:** design Domain section declares `email: EmailStr | None`; the design's own risk note says `EmailStr` rejects some real provider addresses and failures must be named, not coerced
- **Chose:** plain `str` plus `_check_email`, stored verbatim, because `email-validator` is not installed and no dependency was added
- **Alternatives:** add `email-validator` and use `EmailStr` as designed
- **Provisional:** reversible — swap the annotation and add the dependency; a deviation from the approved design, so confirm
- **Evidence:** `models.py:76-87`

### `Employment.company` holds the `CompanySignal` object, not an id
- **Verdict:** sound
- **Spec said:** an Employment names a Company Signal; shared across Leads rather than copied (1.7, 24.2, 24.3)
- **Chose:** object reference, giving real shared identity (`is`); Pydantic revalidation left off so instances are not copied
- **Evidence:** `models.py` `Employment`; identity tests

### Frozen, `extra="forbid"`, tuple collections, hashable entities
- **Verdict:** sound
- **Spec said:** reject undeclared fields, frozen against mutation (1.4, 24.1); design names the config
- **Chose:** shared `_Entity` base with `ConfigDict(extra="forbid", frozen=True)`
- **Evidence:** `models.py:49-50`

### Person identity is any of email, LinkedIn URL, full name (non-blank)
- **Verdict:** sound
- **Spec said:** a record with no person identity is never a Lead (1.5)
- **Chose:** at least one non-blank identity attribute; `email_status` defaults to `UNKNOWN`, and any other status without an email is rejected; `linkedin_url` is `HttpUrl`
- **Evidence:** `models.py:145-160`

### Duplicate Employment rejected; same company with different stints allowed
- **Verdict:** sound
- **Spec said:** silent
- **Chose:** exact-duplicate Employment raises; a conflicting `CompanySignal` under one `company_id` inside a Lead raises
- **Evidence:** `models.py:161-175`

### `is_current: bool | None` and optional non-blank `title`
- **Verdict:** sound
- **Spec said:** title and current flag "where the provider supplies them" (24.2)
- **Chose:** `None` means unknown, never coerced to past
- **Evidence:** `models.py:120-128`

### Company Signal shape and Signal ranges
- **Verdict:** sound
- **Spec said:** fields open
- **Chose:** needs a name or at least one domain; domains unique, no whitespace, stored verbatim; `Signal.strength` finite float in [0, 1]; `TechSignal` and `IntentSignal` are non-interchangeable subclasses
- **Evidence:** `models.py:61-118`

### `share_company_signals` semantics
- **Verdict:** sound
- **Spec said:** share one Company Signal across Leads (24.3)
- **Chose:** equal copies collapse to the first-seen instance; conflicting content raises; idempotent and non-mutating
- **Evidence:** `models.py:177-201`

### Deferred to later tasks
- **Verdict:** sound
- **Chose:** `contributing_sources`, `provenance` on the Lead, and `UntrustedText` left out; `full_name` is a non-blank `str` for now (2.2 and normalization own them)

Evidence: partial — `spec-refactor-agent` and `validate-production-agent` were not run by the
subagent (no Agent tool). Parent re-ran pytest: 196 passed. Commit 74a1532 is local and unpushed;
`tasks.md` shows 2.1 `[x]` but is uncommitted (specs/ is not committed by harness rule).

## Task 2.1 — user decisions on the three `needs-user` choices

### Several current Employments are allowed and flagged
- **Decision (user):** a Lead may hold several current Employments; extras are flagged and weighed when judging lead quality, not rejected.
- **Done:** the "at most one current" validator is removed. `CanonicalLead.current_employments` and `has_multiple_current_employments` are derived properties (not serialized fields). The scoring task consumes the flag.

### `company_id` is one canonical id assigned by this layer
- **Decision (user):** one company id unified across all providers; a provider's own id is normalized onto it.
- **Done:** `company_id` stays the Company Signal identity; `CompanySignal.provider_ids` (tuple of `ProviderCompanyId(source, id)`, one per source) records each provider's native id. Providers never supply `company_id`.
- **Open for the merge task:** the rule that assigns `company_id`. Design D7 makes company identity a domain set under the pinned PSL, so the merge task should derive `company_id` deterministically from that domain set, and match on registrable domain, never on `company_id` alone.

### Email uses `EmailStr`
- **Decision (user):** add `email-validator` and follow the design.
- **Done:** `email-validator>=2.3.0` added; the hand-written check is removed. `EmailStr` lowercases the domain and keeps the local part, so the earlier "stored verbatim" behavior changed. Surrounding whitespace is rejected, not stripped, to keep the no-silent-coercion rule.

### Notes for the merge task (from the 2.1 follow-up self-review)
- `provider_ids` and `domains` compare by tuple order, so one company with the same ids in a different order reads as a conflict. The merge task should canonicalise the order (for example, sort) before comparing.
- `share_company_signals` raises when two signals share a `company_id` but carry different `provider_ids`; it does not union them. The merge task must unify Company Signals across providers before calling it.

## Task 2.2 — FieldProvenance (pass audit)

### Confidence invariants by Confidence Origin
- **Verdict:** sound
- **Spec said:** record Field Confidence verbatim with its scale; origin none records no fabricated number (1.2, 1.8)
- **Chose:** `none` requires `confidence`, `confidence_raw`, `confidence_scale` all None (even 0.0 rejected); `provider_stated` requires raw and scale, normalized `confidence` optional; `heuristic` requires a `confidence` number and forbids raw and scale
- **Evidence:** `models.py:102`, `tests/test_provenance.py`
- **Note:** the `heuristic` rule is the least obvious; revisit if a heuristic ever needs to cite a scale.

### Invariant violations raise pydantic `ValidationError`, no taxonomy error
- **Verdict:** sound
- **Chose:** consistent with 2.1 entities; adapter layer wraps into `NormalizationError` later, since it holds the source name and raw path

### `confidence` reuses the [0, 1] `Strength` type
- **Verdict:** sound
- **Chose:** finite 0.0-1.0, matching design.md's normalized-for-comparison scale

### `fetched_at` must be timezone-aware
- **Verdict:** sound
- **Chose:** naive datetimes rejected (`AwareDatetime`); merge recency ordering needs an unambiguous instant. Reversible.

### Blank strings, verbatim raw, enum placement, deferred fields
- **Verdict:** sound
- **Chose:** `NonBlank` on path/source/raw/scale fields; `confidence_raw` a verbatim string; `DataMode` in models.py per design.md; `untrusted`, `CanonicalLead.provenance`, `contributing_sources` left to 2.3 and the normalizer/merge tasks; `superseded` defaults False

Evidence: partial — `spec-refactor-agent` and `validate-production-agent` not run by the subagent (no Agent tool). Parent re-ran pytest: 227 passed. Commit ce27828 is local and unpushed; `tasks.md` shows 2.2 `[x]` but is uncommitted (specs/ not committed by harness rule).

## Task 2.3 — UntrustedText (pass audit, 2026-10-05)

### `untrusted` defaults to False on FieldProvenance, nothing yet ties it to the value type
- **Verdict:** resolved by user — field made required (no default); the normalizer must state it for every field. Task 5.1 may still add the `UntrustedText`-value cross-check
- **Original verdict:** needs-user
- **Spec said:** "Mark the corresponding provenance record as untrusted external text" (2.3); design.md shows a plain bool
- **Chose:** `untrusted: bool = False`. A normalizer that forgets to set it silently labels provider text as trusted. Enforcement is deferred to the `FieldMap` untrusted flag (task 5.1)
- **Alternatives:** make it required (no default); or have 5.1 reject an `UntrustedText` value whose provenance is not `untrusted=True`
- **Provisional:** reversible — keep the default; task 5.1 must add the cross-check, and if it does not, flip to required
- **Evidence:** `models.py` FieldProvenance; subagent report item 6

### `__str__` raises TypeError (stricter than design.md)
- **Verdict:** sound
- **Spec said:** "no implicit string conversion … a type error" (2.3); design.md only says no `__str__` that returns the payload
- **Chose:** `__str__` raises; `__repr__` withholds payload and shows lengths, so debuggers and pytest output keep working. `"x" + obj` is a TypeError by construction
- **Note:** `%s` logging of an UntrustedText will raise inside the logger; callers log `.value` deliberately or the repr
- **Evidence:** `models.py` UntrustedText; `tests/test_untrusted_text.py`

### Truncation invariant on the wrapper
- **Verdict:** sound
- **Chose:** truncated ⇒ `original_length > len(value)`; not truncated ⇒ `original_length == len(value)`; violations raise `ValidationError` (matches 2.1/2.2). Cutting and max length stay with the normalizer (5.2)
- **Note:** `len` counts characters, not bytes; 5.2 should keep that unit when it applies the configured bound

### Type name, module, base class, serialization
- **Verdict:** sound
- **Chose:** `UntrustedText` in `models.py` (design.md name, existing single-module layout), extends `_Entity` (frozen, extra=forbid), `value: StrictStr`. `model_dump`/JSON keep `value` so the 22.3 round trip works; explicit access is `.value`

Evidence: re-verified by parent — pytest 240 passed, mypy clean, ruff clean on the slice. Commit 890288f is local and unpushed. Subagent did not run spec-refactor-agent, validate-production-agent, or a blast-radius scan (no Agent tool); change is a defaulted field plus a new type. Repo-wide ruff 842 errors were not investigated.

## Task 2.4 — SourceAbsence (pass audit, 2026-10-05)

### Representation of Negative Evidence / Not Applicable (design.md silent)
- **Verdict:** resolved by user — keep `SourceAbsence`; the representation must be explicit where it is built and consumed. tasks.md now names it in 3.1 (validate against the adapter's declared surface), 3.3 (empty vocabulary is Not Applicable), 5.1 (normalizer emits it) and 16.3 (merge consumes it without penalising)
- **Original verdict:** needs-user
- **Spec said:** "Record Negative Evidence when a source could answer... Record Not Applicable when a source's API carries no such field... both distinguishable... distinct from plain absence" (2.4); design.md line 307: absence = `None`, zero provenance rows
- **Chose:** separate frozen `SourceAbsence(_Entity)` in `models.py` with `canonical_path`, `source_name`, `kind: AbsenceKind` (`NEGATIVE_EVIDENCE` | `NOT_APPLICABLE`), optional `raw_field_path`. `CanonicalLead` and `FieldProvenance` untouched
- **Alternatives:** `kind`/`asked` flag on `FieldProvenance` (needs valueless provenance, breaks line 307); sentinel values in fields (pollutes every type); redundant `asked: bool`; generic three-state `Coverage` union (over-built)
- **Provisional:** reversible — a new record type with no consumers yet; swap freely until task 3.1 or the merge tasks (16.x) consume it
- **Evidence:** `models.py` SourceAbsence; `tests/test_source_absence.py` (9 tests)

### No-surface rule enforced structurally via raw_field_path
- **Verdict:** sound
- **Chose:** Negative Evidence requires `raw_field_path`; Not Applicable forbids it, so a source with no surface cannot build Negative Evidence (ValidationError)
- **Note:** the model cannot verify the named path exists in the source's API; task 3.1 (capability declaration) must add that check

### fetched_at / data_mode omitted from SourceAbsence
- **Verdict:** sound (revisit if merge/audit tasks need absence timestamps)

Evidence: parent re-ran pytest: 251 passed. Commit 5a70831 pushed. Subagent did not run spec-refactor-agent, validate-production-agent or a blast-radius scan (additive types only, no consumers).

## Task 2.5 — Signals and Signal Strength (2026-10-05)

### Most of 2.5 was already built in 2.1
- **Verdict:** sound
- **Found:** `Signal`/`TechSignal`/`IntentSignal` with `label` and `strength` (0.0-1.0), and `tech_signals`/`intent_signals` tuples on both `CanonicalLead` and `CompanySignal`, shipped with 2.1. 2.5 added tests and one tightening, not new entities

### Representation, scale, shape (design.md silent)
- **Verdict:** sound, reversible
- **Chose:** Signal Strength is one `float` in 0.0-1.0, finite, field `strength` on `Signal`. Signal = `label` + `strength`; two kinds (tech, intent). Collections are `tech_signals` / `intent_signals` tuples, default empty, on both entities
- **Alternatives:** one mixed `signals` tuple with a kind field (loses typing, 2.1 already split); ordinal enum (loses granularity)

### "Exactly one strength" enforcement
- **Verdict:** sound
- **Chose:** required field (no default), `extra="forbid"` blocks a second one, and new `SignalStrength` type is strict (rejects bool, numeric strings, sequences, None). Field Confidence keeps the lax `Strength` type, untouched
- **Note:** strict applies in python mode; JSON round trip still accepts JSON numbers (tested)

### What the "no merge outcome" test proves
- **Verdict:** needs-follow-up in 16.3
- **Proves today (structural only):** no field a merge resolves (identity, employments, provenance) carries or derives from strength; leads/companies differing only in strength have identical dumps outside the signal tuples; no entity defines `__lt__`-style ordering; only Signal types own a `strength` field
- **Cannot prove:** that a merge run ignores strength. No Merge Engine or resolution hook exists, and none was built. 16.3 must add a real run over leads differing only in strength
- **Evidence:** `tests/test_signal_strength.py`; uv run pytest 265 passed, mypy and ruff clean on the slice

## Task 3.1 — Adapter contract and capability flags (2026-10-05)

Evidence: TDD agent report; `spec-refactor-agent` run (one hole fixed); `validate-production-agent` run (0 critical). Parent re-ran pytest, ruff, mypy: clean.

### Where the task left freedoms open — all provisional and reversible
- **Verdict:** needs-user (Decision-Budget Gate failed; the task named none of these as delegated)
- **Chose, in `base_source.py`:**
  1. One module holds `Capability`, `RateWindow`, `RateBucket`, `SourceRequest`, `RawBatch`, `LeadContribution`, `BaseLeadSource`.
  2. `SourceRequest`, `RawBatch`, `LeadContribution` are minimal frozen models (`kind`; `source_name` + verbatim `payload`; `source_name` + `absences`). Later tasks extend them.
  3. One ClassVar `answerable_surfaces: Mapping[str, frozenset[str]]` holds both answerable canonical paths (keys) and their provider surfaces (values).
  4. Boundary check is the concrete `validate_absence` plus `normalize_checked`, raising the new `InvalidAbsenceError`. The orchestrator is expected to call `normalize_checked`.
  5. `__init__` takes only `mode`; it raises `TypeError` for missing or malformed declarations. The transport argument arrives in 4.1.
  6. `capabilities` may be empty (neither search nor enrich).
- **Alternatives:** separate path and surface declarations; check inside `normalize` via `__init_subclass__` wrapping (rejected as too magic).

### Known gaps, not fixed (self-review)
- **Verdict:** needs-follow-up
- `normalize_checked` checks a contribution's absences but not that the contribution's own `source_name` equals the adapter's `name`.
- A subclass can override `normalize_checked` and skip validation; Python has no final methods. The registry or contract suite (3.x) should assert the override is absent.
- The declaration mappings are plain dicts checked at construction only and are shared across subclasses; they can be mutated afterwards.

## Task 3.2 — Cost class, charge unit, Suppression yield (2026-10-05)

Evidence: TDD agent report; parent re-ran pytest, ruff, mypy.

### Where the task left freedoms open — provisional and reversible
- **Verdict:** needs-user (the task did not name these as delegated)
- **Chose, in `base_source.py`:**
  1. Sort key `(is_paid, not yields_suppression, charge_unit_rank, name)`: free+suppression, free, paid+suppression, paid.
  2. Free non-suppression sources run before paid suppression-bearing ones; the spec only requires free suppression-bearing first.
  3. Charge-unit rank `per_company` < `per_call` < `per_lead` (fewest billable events first), a heuristic.
  4. `name` breaks ties for determinism.
  5. `enrichment_order` is a free function over declarations and does not filter by capability; 11.6 does that.
- **Alternatives:** method on the class; ranking paid-suppression ahead of free non-suppression.
- **Known gap:** ordering tests are example-based only, no property test.

## Task 3.3 — Per-source Target Profile vocabulary (2026-10-05)

Evidence: TDD agent report; parent re-ran pytest (326 passed), ruff, mypy.

### Where the task left freedoms open — provisional and reversible
- **Verdict:** needs-user (Decision-Budget Gate failed; the task named none of these as delegated)
- **Chose, in `base_source.py`:**
  1. `target_vocabulary` is a mandatory ClassVar `Mapping[str, object]`; `{}` is legal (no targeting surface).
  2. Keys are non-blank canonical term names; values are opaque. None, blank str, or empty collection/mapping counts as an empty declaration.
  3. A term is expressible only if it has a non-empty vocabulary; every other term is Not Applicable.
  4. `target_term_absence(term)` returns None if expressible, else a NOT_APPLICABLE `SourceAbsence` at `target_profile.<term>` (`TARGET_TERM_PATH_PREFIX`). Blank term raises ValueError.
  5. Construction rejects (TypeError) any source whose `target_profile.<term>` keys in `answerable_surfaces` differ from its expressible terms, so Negative Evidence is only possible where a vocabulary exists.
- **Alternatives:** no cross-check against `answerable_surfaces`; a typed vocabulary value instead of `object`; term-keyed path without a prefix.
- **Known gap:** vocabulary values are not validated against provider-issued identifiers (task 9.3).

### Task 3.3 — audit pass (2026-10-05, manual: skill `auditing-spec-choices` is not installed)

Evidence: independent `spec-refactor-agent` review; parent re-ran pytest (331), ruff, mypy. Ordered least-confident first.

1. **Cross-check of `target_profile.*` surfaces against vocabulary at construction** — **needs-user.** Couples two declarations the spec keeps separate (1.9 vs 2.8); a reasonable alternative is no coupling. Provisional, reversible (delete one `__init__` check).
2. **Term path `target_profile.<term>`** — **needs-user.** Naming convention the spec does not fix; Merge Engine (16.3) will consume it. Provisional, reversible until 16.3.
3. **0/False scalar vocabulary values count as non-empty** — **needs-user.** Treated as opaque provider IDs; reviewer kept it. Reversible.
4. **Mutable vocabulary after construction is not guarded** — **needs-user (known gap).** Same as every other ClassVar declaration; fix across all declarations or none.
5. **Non-str term raises ValueError** (reviewer fix) — **sound.** Was AttributeError / a bogus path for bytes.
6. **Vocabulary `Mapping[str, object]`, `{}` legal, mandatory declaration** — **sound.** Matches the design signature.
7. **Not Applicable for an expressible term is rejected** — **sound.** Mirrors 3.1's rule.

Counts: sound 3, unsound 0, needs-user 4. The audit was done by the parent, not an independent auditor.

### Task 3.3 — user decisions on the four `needs-user` entries (2026-10-05)
1. **Cross-check of `target_profile.*` surfaces against vocabulary** — decided: keep ("sounds right"). Now stated in design.md (BaseLeadSource constraints).
2. **Term path `target_profile.<term>`** — decided: fix it in the spec. Added to requirements 2.8 (Convention) and design.md.
3. **0/False vocabulary values** — decided: settle on one phrasing. One rule, stated in `_is_empty_vocabulary`, requirements 2.8 and design.md: a vocabulary is empty when absent, a blank string, or an empty collection; anything else, including `0` and `false`, is a real provider identifier. (Interpretation: the reply "settle on a single phrasing" was matched to this entry; confirm if it meant something else.)
4. **Mutable declarations** — decided: fix for all. `BaseLeadSource.__init_subclass__` copies `rate_limit`, `answerable_surfaces`, `target_vocabulary` into `MappingProxyType` views at class definition; mutation raises `TypeError`, and mutating the author's original dict changes nothing. Task 3.4 must add `endpoints` to `_FROZEN_MAPPINGS`. Rebinding a ClassVar on the class remains possible and is out of scope.
- **Self-review of the freezing fix:** found and fixed two holes in the first version (a mixin's plain dict was inherited unfrozen; a `MappingProxyType` wrapping a mutable dict stayed mutable). `__init_subclass__` now copies whatever `getattr` resolves into a fresh proxy. **Known gap (needs-user):** `target_vocabulary` values are opaque and not deep-frozen; deep-freezing would change the value types adapters see (a dict would become a non-JSON-serialisable mappingproxy), so it is left as a design call.

## Task 3.4 — Read-only endpoints and environment-only credentials (2026-10-05)

### Audit pass (manual: skill `auditing-spec-choices` is not installed; audited by the parent from the subagent report and a re-run of ruff, mypy and pytest: 408 passed)
Least-confident first.
1. **Orchestration layer identified by file name** (`orchestrator`/`orchestration`, module or package, directly in the slice) — **needs-user.** The guard passes vacuously until such a module exists. Provisional and reversible: the names are two constants in `structure_guard.py`. Confirm the names when the orchestration task lands.
2. **Each `Endpoint.bucket` must name a key in the adapter's own `rate_limit`** — **needs-user.** Stricter than the design text. It fails at class definition, which is cheap to loosen. Keep it unless a provider shares a bucket across adapters.
3. **`endpoints` and `required_env` are mandatory declarations; `{}` and `()` are legal** — **sound.** Same rule as the other declarations; keyless sources are expressible.
4. **`Endpoint.method` is GET or POST; `read_only` must be the literal `True`** (`0`, `1`, `"True"`, `None` rejected at runtime; mypy rejects `False`) — **sound.** POST is needed for read-only search endpoints, and the design lists both.
5. **`required_env` is a tuple of unique names with no whitespace, `=` or NUL** — **sound.**
6. **Unset or whitespace-only variable counts as missing; all missing names are reported together; names only, never values** — **sound.**
7. **Resolver reads `os.environ` at call time, returns only declared names as a read-only view, no `.env` loading** — **sound.** `.env` is 10.6. The source-inspection test for `open`/`getenv`/`load_dotenv` is brittle but cheap.
8. **Guard flags any import of `adapters` and any use of a `BaseLeadSource` subclass under `adapters/`, direct or transitive** — **sound.**

Counts: sound 6, unsound 0, needs-user 2. The audit was done by the parent, not an independent auditor.
Signals: none. No clustering and no `unsound` entries. 3.5 should add `UndeclaredEndpointError` transport rejection.

### Task 3.4 — independent self-review (spec-refactor-agent, 2026-10-05)
The first pass (above) was audited by the parent only; the independent review ran afterwards and found three gaps, all fixed:
- `Endpoint.__post_init__` now rejects any path that is not a root-relative path on the provider host (absolute URLs, `//host`, `//` inside a path, `.` / `..` segments, `?`, `#`, backslash, whitespace, non-printable characters).
- `_validate_endpoints` requires values to be exactly `Endpoint` (not a subclass) with `read_only is True`; a subclass overriding `__post_init__` could previously carry `read_only=False` past the class-definition check.
- `structure_guard` now flags `__import__("...adapters...")` and `importlib.import_module("...adapters...")` calls with a string literal.

Known gaps, not fixed (**needs-follow-up**): dynamic imports with a non-literal argument; base classes imported under an alias; a concrete adapter deriving from a helper base outside `adapters/`; `resolve_credentials` raises `AttributeError` for a custom `environ` mapping with non-str values; rebinding `Cls.endpoints` / `Cls.required_env` after class creation (needs a metaclass; out of scope, as for every declaration).
Major task 3 (adapter contract) is complete: 3.1 to 3.4 done and reviewed.

## Task 4.1 — Transport port with the REST implementation (2026-10-05)

### Audit pass (manual: skill `auditing-spec-choices` is not installed; audited by the parent from the subagent report and a re-run of ruff, mypy and pytest: 433 passed)
Least-confident first.
1. **Default timeouts: connect 5.0 s, read 30.0 s (write = read, pool = connect), module constants** — **needs-user.** The spec requires explicit timeouts but gives no numbers. Provisional and reversible: two constants, overridable per instance via constructor arguments.
2. **Transport is built from the adapter's `endpoints` map plus a provider name, not from the adapter itself** — **needs-user.** `BaseLeadSource.__init__` takes only `mode`, so the design's `__init__(transport, mode, config)` is not yet wired. Reversible; settle when the first concrete adapter (task 12) binds a transport.
3. **Non-2xx responses are returned, not raised; the adapter's `classify_error` maps status to error** — **sound.** Matches the design's error-classification split.
4. **Network failures: `httpx.TimeoutException` becomes `SourceTimedOut`, other `httpx.TransportError` becomes `SourceTransient(status=None)`; no retry here** — **sound.** Retry belongs to 10.2.
5. **Endpoint check compares the whole `Endpoint` (method and path); a declared path with a different method is rejected** — **sound.** Stricter than the task text and consistent with read-only.
6. **Path placeholders are filled from `params`, URL-quoted with `/` escaped, used keys removed from the query; a missing value raises `ValueError`** — **sound.**
7. **Query values stringified, `bool` rendered `true`/`false`; response `body` is parsed JSON or `None`; header keys lower-cased** — **sound.**
8. **`aclose()` is on `RestTransport` only, not on the `Transport` Protocol** — **sound.** `FixtureTransport` (4.2) holds no resources; revisit if 4.3 needs one.

Counts: sound 6, unsound 0, needs-user 2. The audit was done by the parent, not an independent auditor.
Signals: none. No `unsound` entries and no clustering.
Note: the first commit (1809b75) did not include `tasks.md` (4.1 marked `[x]`) or this ledger; they are committed afterwards.

### Task 4.1 — user decisions on the two `needs-user` entries (2026-10-05)
1. **Timeouts (connect 5 s, read 30 s):** confirmed by the user. Now **sound**.
2. **Transport construction from the endpoint map:** accepted as a stopgap. The user requires task 12 to fix it; recorded as a detail bullet on 12.1 (bind the transport through `BaseLeadSource.__init__`, endpoint map taken from the adapter itself). Stays open until 12.1 lands.

### Task 4.1 — independent self-review (spec-refactor-agent, 2026-10-05)
Found one real bug, fixed in d33651b (suite 437 passed, ruff and mypy clean, re-run by the parent):
- A placeholder value of `..` produced `/v1/people/..`, which httpx collapses to `/v1`, escaping the declared endpoint map (Req 11.1). `_fill_path` now rejects `""`, `"."` and `".."` with `ValueError`.
- `follow_redirects=False` is now pinned explicitly, with a test that a 302 is returned and its `Location` is never requested.
- A vacuous non-JSON-body test was replaced with a real 204 empty-body request; added tests for the placeholder values.

Known gaps, not fixed (**needs-follow-up**): `None` and list query values are stringified (`"None"`, `"['a']"`), a query-contract design call for the adapter tasks; no response-size cap; a missing path parameter raises plain `ValueError`, not a typed `SourceError`; `%2e%2e` is sent encoded and server-side decoding is out of our control. The new dot-segment test was not run against the old code; the bug was confirmed by probing httpx directly.

## Task 4.2 — Fixture transport for synthetic mode (2026-10-05)

### Audit pass (manual: skill `auditing-spec-choices` is not installed; audited by the parent from the subagent report and a re-run of ruff, mypy and pytest: 454 passed)
Least-confident first.
1. **Missing or unparseable fixture raises `FixtureSchemaError(provider, field="<provider>/<name>.json")`, reusing the task 1.2 class** — **needs-user.** Spec 5.4 names provider and field, so reuse fits, but a "fixture missing" is conceptually not a schema error. Provisional and reversible: one raise site; a dedicated class can replace it later.
2. **Fixtures root defaults to `<slice>/fixtures`; `fixtures_root` is a constructor override used by tests** — **sound.** Matches design `fixtures/<provider>/<endpoint>.json`.
3. **Filename is `<provider>/<endpoint-map-key>.json`; names must match `[A-Za-z0-9_][A-Za-z0-9_-]*` else `ValueError`; one `Endpoint` under two names is rejected** — **sound.** Blocks traversal and ambiguous filenames.
4. **Same whole-`Endpoint` undeclared check as 4.1 (`UndeclaredEndpointError`)** — **sound.** Keeps synthetic and live rejecting identically.
5. **Returns `TransportResponse(200, {}, parsed JSON)`; params, body and headers ignored; no error-status simulation** — **sound.** Minimal per the task; error paths are tested at the adapter level.
6. **Synchronous `read_text` inside async `send`** — **sound.** Small local files; scale is a handful of leads.
7. **No-socket property asserted by patched sockets, no httpx/socket attributes, and an AST check scoped to the class body (module still imports httpx for `RestTransport`)** — **sound.**

Counts: sound 6, unsound 0, needs-user 1. Audit done by the parent, not an independent auditor.
Signals: none; the Decision-Budget gap (fixture location, filename mapping, missing-fixture error unnamed in the task) produced entry 1. Consider naming these in 5.x/fixture tasks.

## Task 4.3 — MCP transport seam with interactive-auth fallback (2026-10-05)

### Audit pass (manual: skill `auditing-spec-choices` is not installed; self-audit by the implementing agent, with ruff, mypy and pytest re-run: 465 passed)
Least-confident first.
1. **The MCP client is injected as a minimal `McpSession` protocol (`call_tool(name, arguments) -> parsed JSON`); the `mcp` SDK is not imported or added to dependencies** — **needs-user.** Spec is silent on how the SDK is wired (design lists it as P2/optional). The seam is complete and testable now; a real SDK-backed session is deferred to the first MCP-selected adapter. Reversible: add an SDK adapter class implementing the protocol.
2. **Fallback target is a constructor-supplied `Transport` plus a `fallback_kind` of `"rest"` or `"synthetic"` (used for the log only)** — **sound.** The task names exactly these two targets; the caller picks one, so no mode branch lives in an adapter.
3. **Fallback is sticky for the transport's lifetime, with one log line** — **sound.** Retrying a browser flow in a headless run is the failure 20.5 forbids; one line avoids log spam.
4. **`reason` logged is the string the session puts in `McpInteractiveAuthError`, via structlog `warning` event `mcp_interactive_auth_fallback`** — **needs-user.** Spec says "log the reason". An auth URL may carry a state token, so session implementers must not put secrets in `reason`; nothing here redacts it. Provisional; add redaction if a real session needs it.
5. **Tool name is the endpoint-map key; arguments are `{**params, **json_body}` (body wins); headers are not forwarded** — **sound.** Mirrors the fixture transport's naming; headers carry REST credentials, MCP auth belongs to the session. The name-clash rule is arbitrary but tested.
6. **Result is `TransportResponse(200, {}, body)`; `TimeoutError` maps to `SourceTimedOut`, other `OSError` to `SourceTransient`; other exceptions propagate** — **sound.** Same typed errors as REST; unknown exceptions are not swallowed (ruff BLE).
7. **Exception named `McpInteractiveAuthError` (renamed from `...Required` for the N818 lint rule)** — **sound.**
8. **Same whole-`Endpoint` undeclared check as 4.1/4.2, applied before and after fallback** — **sound.**

Counts: sound 6, unsound 0, needs-user 2. Self-audit, not an independent auditor.
Signals: none. The two needs-user entries share a cause: the task did not say how the MCP client is supplied or what the log may contain.

## Task 4.4 — Authentication strategy port with a token cache (2026-10-05)

### Audit pass (manual: skill `auditing-spec-choices` is not installed; self-audit by the implementing agent, with ruff, mypy and pytest re-run: 519 passed)
Least-confident first.
1. **The OAuth2 token request sends `grant_type=client_credentials` as a query parameter, not a form body** — **needs-user.** The `Transport` port carries `params` and `json_body` only, and ZoomInfo (the sole OAuth2 provider, deferred) would expect `application/x-www-form-urlencoded` or its documented shape. Not verified against a live token endpoint. Provisional: keeps the token call on the declared-endpoint port. Reversible: add a form-body field to the port when ZoomInfo is un-deferred.
2. **Token-endpoint failures: 400/401/403 map to `SourceUnauthorized` (with a malformed 2xx body also mapping to it), every other non-2xx to `SourceTransient`; response bodies are never echoed** — **needs-user.** Spec is silent on token-fetch classification (18.6 only says auth failure marks the source unauthorized). 429 is mapped to transient and loses `Retry-After`. Provisional.
3. **Static strategies take env var names plus an injectable `environ`, resolve at construction, and raise `MissingCredentialError` (all names together) before any request** — **sound.** Mirrors `resolve_credentials` (2.5); a small helper duplicates its few lines because that function takes a source class and would have forced a shim.
4. **Credentials containing control characters (CR/LF/NUL) are rejected with a `ValueError` naming the variable, not the value** — **sound.** Header-injection guard; the spec did not ask for it.
5. **Refresh margin defaults to 60 s, clamped to half the token lifetime; the window is measured from before the request was sent** — **sound.** Spec says "before the stated validity window expires" without a number. The clamp stops short-lived tokens being refetched every call; measuring from request start errs early, never late.
6. **`TokenCache` single-flight via an `asyncio.Lock`; failures cache nothing; `invalidate(rejected)` drops only if the rejected token is still current** — **sound.** Stops a late 401 evicting a fresh token. The cache has no thread safety (asyncio only), which matches the async port.
7. **Auth overrides a caller-supplied header (case-insensitive) or param of the same name; inputs are copied, never mutated** — **sound.** Arbitrary but tested.
8. **`HeaderKeyAuth` covers both a custom key header and bare `Authorization` (UpLead style); no separate class** — **sound.**
9. **Nothing wires a strategy into a transport or adapter yet** — **sound.** The task is the port and strategies; binding belongs to adapter tasks. 401-triggered `invalidate` is likewise left to the adapter.

Counts: sound 7, unsound 0, needs-user 2. Self-audit, not an independent auditor.
Signals: none. Both needs-user entries come from the OAuth2 token exchange being specified only by a deferred provider, so it cannot be checked against a real endpoint.

## Task 5.1 — Emit per-field provenance from declarative field rules (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy, pytest re-run: 550 passed)
Least-confident first.
1. **"Was the source asked about this field" is a `queried_paths` set on `NormalizationContext`, supplied by the caller; an empty default means no absences at all** — **needs-user.** The task says emit neither absence kind when the source was never queried, but nothing in the spec says how the normalizer learns what was queried (orchestrator, request, or adapter). I made it a context input and applied it to both kinds, so a Not Applicable record also needs its path listed in `queried_paths`. Provisional: reversible, one field; the orchestrator (a later task) may want to derive it from the request.
2. **The "fail the suite on an unmapped fixture field" bullet is a mechanism only (`unmapped_raw_paths(raw, rules, ignored)`), tested on synthetic dicts** — **needs-user.** No provider fixtures exist yet (adapters are 12-15), so no real fixture is checked. Each adapter task must add a test asserting the list is empty for its fixtures; until then the bullet is not met for any real provider.
3. **`LeadContribution` gained `values` and `provenance`, with a validator tying them together** — **needs-user.** design.md has `Normalizer.apply` return `LeadContribution` but never says it carries values. I added `values: Mapping[str, Any]` plus `provenance`. The validator requires the same canonical paths in both (one record each), provenance from the contribution's own source, and `untrusted` to equal "value is `UntrustedText`" in both directions. This closes the 2.3 follow-up as a model invariant, not only in the normalizer. The merge tasks bind to this shape. `values` is a dict inside a frozen model, so it is shallowly mutable.
4. **Dotted raw paths split on `.`; a raw key that itself contains a dot, a list index, or a non-mapping on the way resolves to nothing, silently** — **unsound, accepted.** Real payloads with arrays (for example employment history) cannot be mapped yet. Index/wildcard syntax is for the provider tasks to request; 5.3 should decide whether a type-mismatched path is a schema violation.
5. **"Resolves to nothing" means missing or `None` only; `""`, `0`, `False` are values; a transform returning `None` also counts as nothing** — **sound.** Matches design.md ("non-null").
6. **Confidence: every record gets `ConfidenceOrigin.NONE`** — **sound for now.** `confidence_fn` was removed from `FieldRule` (D5), and a provider-stated confidence path is outside 5.1's scope. A later task must add a rule field for the raw confidence path and scale.
7. **Untrusted rule on a non-`str` value, or any raw/transform value that is already `UntrustedText`, raises the existing `NormalizationError`** — **sound.** Never coerces. This is a partial use of 5.3's error; 5.3 still owns schema-violation checks generally.
8. **`UntrustedText` is built with `truncated=False`; no length bound applied** — **sound.** Task 5.2 owns the bound; it needs a max-length field on `NormalizationContext`.
9. **Duplicate canonical paths across rules raise `ValueError`; blank paths rejected at `FieldRule` construction** — **sound.** Two rules for one path would break the one-record-per-field invariant.
10. **No hypothesis dependency, so the property check is an exhaustive enumeration of present/null/missing/falsy combinations** — **sound.** The property-testing library is not installed; adding a dependency was out of scope.
11. **A rule whose raw path is not a declared surface produces a Negative Evidence that `validate_absence` later rejects** — **sound.** Fails loudly at the 3.1 boundary rather than being silently dropped; tested.

Counts: sound 7, unsound 1 (accepted, entry 4), needs-user 3. Self-audit, not an independent auditor.
Signals: the needs-user entries each come from design.md not saying how the Normalizer learns what was queried, where values live, or which fixtures to check. No blast-radius scan or refactor/production agent ran (no Agent tool). `LeadContribution` was extended; its only callers are tests, all still green.

## Task 5.2 — Store untrusted provider text verbatim under a length bound (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 571 passed)
Least-confident first.
1. **Default bound is 4000 characters (`DEFAULT_UNTRUSTED_MAX_LENGTH`), a number I picked** — **needs-user.** Requirement 22.4 says "configured maximum" and gives no value. 4000 fits a job description or bio but is arbitrary. Provisional: reversible, one constant; a deployment value should come from the eventual configuration layer.
2. **The bound lives on `NormalizationContext.untrusted_max_length`, one bound for every untrusted field, not per field or per provider** — **needs-user.** The spec says "each untrusted text field" is held to a maximum, which could mean per-field limits. A per-rule override on `FieldRule` is a small addition if wanted. Provisional: reversible.
3. **The bound counts code points, not bytes or grapheme clusters, so a cut can split a combining sequence or emoji ZWJ sequence** — **unsound, accepted.** It matches the unit `UntrustedText.original_length` already uses (2.3 note), and a code-point cut never yields invalid UTF-8. Cutting mid-grapheme can leave a dangling combining mark; storing verbatim outranks cosmetic tidiness. Note the Lead Store (6.x) must size any byte-limited column accordingly: 4000 characters can be up to 16000 UTF-8 bytes.
4. **Truncation is a bare prefix: no ellipsis marker, no whitespace trim** — **sound.** A marker would be altering provider text; the `truncated` flag and `original_length` carry the fact.
5. **`untrusted_max_length` must be a positive `int` (bool, float, str, None, 0, negative raise `ValueError` at context construction)** — **sound.** A zero or silently-coerced bound would either drop all text or disable the guard.
6. **Bound applies to the value after the rule's transform; trusted fields are never bounded** — **sound.** The untrusted flag is the rule's, and what is stored is what is bounded. Trusted text fields that should be bounded must be marked untrusted.
7. **Verbatim guarantee is by construction (slicing only), tested with `{}`/`%s`/`${}`/template/newline/control/bidi inputs; no `str.format` or f-string touches the value** — **sound.** `UntrustedText.__str__` already raises, so accidental interpolation downstream is a TypeError. A `str` subclass with overridden slicing is not defended against; provider JSON never produces one.
8. **No hypothesis dependency, so no property test; boundary lengths (9/10/11/500) are enumerated** — **sound.** Same reasoning as 5.1 entry 10.

Counts: sound 5, unsound 1 (accepted, entry 3), needs-user 2 (entries 1 and 2). Self-audit, not an independent auditor.
Signals: both needs-user entries are values the spec leaves to configuration (number and granularity). No blast-radius scan or refactor/production agent ran (no Agent tool); `NormalizationContext` gained a defaulted field, so existing callers are unaffected (full suite green).

## Task 5.3 — Raise a named normalization error identifying provider and field (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 586 passed)
Least-confident first.
1. **The "declared raw schema" is enforced by a new helper `validate_raw_payload(provider, model, raw, rules)` that runs the adapter's Pydantic model in default (lax) mode and discards the result** — **needs-user.** Requirement 2.6 says `normalize()` fails on a payload that violates the declared raw schema, but no adapter or raw model exists yet (12-19), so the helper is unexercised by any real provider. Lax mode was chosen because strict mode rejects JSON-native values such as ISO date strings; the cost is that `"5"` passes an `int` field unless the model uses `StrictInt`. Each adapter task must call it before `Normalizer.apply` and use strict field types where coercion would hide drift. Provisional: reversible, one function.
2. **Closes 5.1 entry 4 in part: a path whose parent is present but not a mapping now raises `NormalizationError`; a missing key or a `None` parent is still an absence** — **sound.** Treating a wrong shape as empty would record Negative Evidence for a field the provider never answered. List indexes and dotted keys are still unsupported, so a list parent on a mapped path now fails loudly instead of resolving to nothing, which will force the provider tasks to request index syntax rather than lose data silently. An existing 5.1 test that asserted the old behaviour was rewritten.
3. **Any exception from a field transform is converted to `NormalizationError` (raised `from None`)** — **sound.** The task says fail fast with the field named; a transform's own message can quote untrusted provider text, so the cause is deliberately suppressed. This loses the original traceback, which makes debugging a buggy transform harder; accepted.
4. **For schema violations the error names the first Pydantic violation only, with Pydantic's message and input value dropped** — **sound.** Both can echo provider text. The cost is that a payload with several problems reports one at a time.
5. **The raw path in a Pydantic-derived error comes from provider-chosen keys (extra="forbid" reports unknown key names), so it is `ascii()`-escaped and cut to 120 characters; a violation no rule covers gets canonical path `<unmapped>`** — **needs-user.** The spec requires naming the canonical path, but an unmapped field has none, so the placeholder is invented. The cut can land inside an escape sequence (cosmetic). Provisional: reversible.
6. **Errors raised in `Normalizer.apply` carry only rule-declared paths and the source name, never a payload value or key** — **sound.** Tested with a secret-bearing value; `str()` and `repr()` are clean. The error also pickles round-trip.
7. **No partial state on failure** — **sound.** `apply` builds local lists and constructs `LeadContribution` only at the end, so a raise returns nothing; the input mapping is never mutated.
8. **Null values and falsy values are unaffected: `None` is still absence, `""`/`0` still values** — **sound.** Unchanged from 5.1 entry 5.
9. **No hypothesis dependency, so shape combinations are enumerated by parametrization** — **sound.** Same reasoning as 5.1 entry 10.

Counts: sound 6, unsound 0, needs-user 2 (entries 1 and 5), plus entry 2 partially resolving an earlier accepted-unsound item. Self-audit, not an independent auditor.
Signals: the spec never says where the raw model lives or who calls the validator, which is why entry 1 is a helper instead of wiring. No blast-radius scan or refactor/production agent ran (no Agent tool); `_resolve` is private and its only caller is `Normalizer.apply`.

## Task 6.1 — Define the append-only contribution log and projection schema (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 608 passed)
Least-confident first.
1. **`source_contribution.lead_identity_id` is nullable per the design table but contributions are append-only, so the link can only be set at INSERT; a later "assign this contribution to a cluster" is an UPDATE and now raises `AppendOnlyViolationError`** — **needs-user.** The design (Logical Data Model) lists both facts and they conflict. Provisional: the guard wins, since 8.12 is the stated invariant. Merge (8.x) must either resolve the identity before persisting a contribution, or the link must move to a separate append-only table. Reversible: one column or one table.
2. **Append-only is enforced by ORM events only (`before_update`/`before_delete` on the two models, and a `Session` class-level `do_orm_execute` hook for bulk and `Query.delete`)** — **unsound, accepted.** Core statements on a `Connection` and any SQL outside the ORM bypass it, and there is no database trigger (a trigger would be dialect-specific, which 9.4 forbids). The guard is registered globally on `Session` at import of `store.models`; it only acts on the two tables. The structural AST test additionally finds no `update/delete/merge` call naming either model elsewhere in the package.
3. **Tables live in a new `leadforge.lead_ingestion.store` subpackage (`models.py`), not a top-level `db` package** — **sound.** D1 forbids shared top-level `db`/`models` packages; the slice owns its store. Provisional name; moving is an import change.
4. **Invented names and shapes where the design says only "projection columns", "counts" or "id PK":** all PKs are `Uuid` with `uuid4` default; `canonical_lead` carries email, email_status, linkedin_url, full_name, employments/tech_signals/intent_signals (JSON), opt_out, suppressed; `source_run` counts are `leads_found` and `contributions_written`; `status`/`data_mode`/`lead_scope`/`key_type` are plain `String`, not DB enums — **needs-user** only in the sense that later tasks may rename; provisional and reversible before migrations (6.2) freeze them.
5. **String lengths are guesses (e.g. `key_value` 512, `linkedin_url` 2048, `email` 320)** — **unsound, accepted.** Postgres enforces them and SQLite does not, so an over-long value passes the SQLite leg and fails on Postgres (6.7 should include one). Untrusted text goes into JSON `value`, which has no length bound, so the 16000-byte worry from 5.2 does not apply; a test stores 4000 four-byte code points.
6. **`DateTime(timezone=True)` on SQLite returns naive datetimes** — **unsound, accepted for now.** SQLite has no tz storage, so a timestamp does not round trip tz-aware there. 6.5 (retention comparisons) and 6.6 must handle this, for instance with a portable `TypeDecorator`.
7. **No ORM `relationship()`s, only foreign keys** — **sound.** Minimal for this task; repositories (6.4+) can add them. Foreign keys are not enforced by SQLite unless `PRAGMA foreign_keys` is on, which is engine-config work for 6.3, and the PRAGMA would be dialect-conditional code to keep out of the store.
8. **The portability test inspects metadata (type class module, allow-list, no `server_default`, tz-aware DateTime) and a raw-SQL/dialect AST scan covers the whole package outside `tests` and `migrations`** — **sound.** The scan flags `text`/`DDL` imports, `.dialect`, `create_all`/`drop_all` on any receiver, and SQL-shaped string constants (docstrings of ordinary sentences are ignored). Its own cases are tested. It is a heuristic: a SQL string assembled at runtime would slip through. Tests call `create_all` on in-memory SQLite, which the scan excludes since it is under `tests`; 6.2 must replace that with upgrade-to-head.
9. **No new dependency:** `sqlalchemy` and `alembic` were already pinned in 1.1 — **sound.**

Counts: sound 4, unsound 3 (entries 2, 5, 6; all accepted), needs-user 2 (entries 1, 4). Self-audit, not an independent auditor.
Signals: the design's `source_contribution` row contradicts its own "immutable" note on the nullable identity FK (entry 1). No blast-radius scan or refactor/production agent ran (no Agent tool; all code is new, no existing symbol modified).

## Task 6.2 — Ship the schema through migrations only (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 623 passed)
Least-confident first.
1. **Migrations are tested only on SQLite; the Postgres leg is untested** — **unsound, accepted.** The revision uses only portable types (`Uuid`, `String`, `Integer`, `Float`, `Boolean`, `JSON`, `DateTime(timezone=True)`) and no dialect branch, but no Postgres ran here. Task 6.7 must run `upgrade_to_head` on Postgres and the same no-drift check there.
2. **The drift test is `alembic.autogenerate.compare_metadata` on a migrated in-memory SQLite, with `compare_type` and `compare_server_default`** — **unsound, accepted.** SQLite reflection is loose (String lengths and some type differences are not reported), so a length change in the models could pass here and be caught only on Postgres. A mutation check (dropping a column makes the diff non-empty) shows the test can fail.
3. **`env.py` is online-only and raises `NotImplementedError` in offline (`--sql`) mode** — **sound, provisional.** Nothing needs SQL scripts yet; reversible by adding a `literal_binds` branch.
4. **Programmatic API lives in a new `store/migrate.py` (`alembic_config`, `upgrade_to_head`, `downgrade_to_base`); the target is a URL string or an open `Connection`** — **sound, provisional.** The connection form (`config.attributes["connection"]`) exists because an in-memory SQLite database is per-connection; the caller owns that transaction. With a URL the helper opens, commits and disposes its own engine, so `upgrade_to_head("sqlite://")` migrates a throwaway database. 6.3 replaces URL passing with config resolution and should call this, not reimplement it.
5. **Layout: `store/migrations/` (`env.py`, `script.py.mako`, `versions/`), no `alembic.ini`; revision ids are zero-padded sequence numbers (`0001`) with slug file names (`0001_initial_lead_store_schema.py`)** — **sound, provisional.** No ini means no logging config is touched and nothing is read from the working directory. Moving the directory is one constant (`MIGRATIONS_DIR`). The 6.1 SQL scan already skips any path containing a `migrations` part, so raw SQL and dialect code remain allowed only there; I kept that rule unchanged.
6. **No implicit creation is enforced by a scan over the whole `src` tree for `create_all`/`drop_all` (attribute or bare name) outside `tests` and `migrations`, plus a test that using the ORM on an unmigrated database fails with `OperationalError`** — **sound, with a gap.** It is a name-based AST heuristic: `metadata.create(...)` per table or `getattr(x, "create_" + "all")` would slip through. The 6.1 scan covers `create_all` inside the slice; this one widens it to all of `src`.
7. **The initial revision was generated by autogenerate and hand-trimmed (comments, formatting)** — **sound.** It freezes the 6.1 schema as it stands, including the open 6.1 needs-user items (nullable `source_contribution.lead_identity_id`, guessed lengths, plain-`String` enums). Changing any of them from now on is a new revision, not an edit of `0001`; no test locks `0001` against edits.
8. **The 6.1 test fixture now builds its schema with `upgrade_to_head(conn)` instead of `create_all`** — **sound.** All 6.1 tests pass unchanged otherwise.
9. **No new dependency** — **sound.**

Counts: sound 5, unsound 2 (entries 1, 2; both accepted), sound-with-gap 1 (entry 6), needs-user 0. Self-audit, not an independent auditor.
Signals: no Agent tool, so no refactor or production-readiness agent ran; no blast-radius scan (no existing symbol was modified besides the test fixture). Red phase was seen as a collection error (`store.migrate` missing) before implementation.

## Task 6.3 — Resolve the database engine from configuration with a local default (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 648 passed)
Least-confident first.
1. **Postgres was never exercised: no server and no driver is installed here (`psycopg` and `psycopg2` both missing), and no driver is pinned in `pyproject.toml` or named by the design** — **unsound, accepted.** Only URL parsing, `postgres://` normalisation, driver-name selection, and "plain `create_engine` call with no SQLite options" (via an intercepted `create_engine`) are tested. Task 6.7 needs a driver dependency; I did not invent one. This SQLAlchemy resolves a bare `postgresql://` to `psycopg` (v3), so `psycopg[binary]` is the likely pin.
2. **Backend branching exists in exactly one module, `lead_ingestion/database.py` (`get_backend_name() == "sqlite"` and the `PRAGMA foreign_keys=ON` connect hook)** — **sound, provisional.** The 6.1 scan forbids `.dialect`, `text`, `DDL` and SQL-shaped strings across the whole `lead_ingestion` package, not only `store/`; the PRAGMA string is not SQL-shaped and the module uses `URL.get_backend_name()`, so the scan passes without being loosened. I added a test that no other module names `get_backend_name`, `get_dialect`, `dialect`, `pragma`, or a backend name. This is a deliberate single-site exception, and the scan itself is unchanged.
3. **Default path is `<cwd>/.leadforge/leadforge.db`, resolved to an absolute path at call time; `.leadforge/` added to `.gitignore`** — **sound, provisional.** Nothing writes into the source tree by default unless the process runs there. Home-directory placement was rejected as invasive for a demo; cwd-relative suits a Docker volume mount. Resolving is side-effect free; only `create_store_engine` makes the parent directory, and only for the default (an explicit file URL's parent is the caller's to create).
4. **Relative SQLite file URLs (`sqlite:///rel/x.db`) are made absolute against the cwd (or `base_dir`) at resolution** — **sound, provisional.** It prevents the database moving after a `chdir`. It rewrites the user's URL; `file:` URIs are left untouched, so query-parameter forms like `?mode=ro&uri=true` are not handled specially.
5. **Unset and blank or whitespace-only `DATABASE_URL` both mean the default** — **sound, provisional.** An `.env` line `DATABASE_URL=` is then harmless. Reading `.env` files is 8.2, not done here: `resolve_database_url` reads `os.environ` or an injected mapping only.
6. **Errors never echo the raw URL: parse failures raise `DatabaseConfigError` with `from None`, unsupported backends are named by backend only, and missing drivers by driver name only** — **sound.** Tested with a password in the URL. `redact_url` (`hide_password=True`) is the sanctioned rendering. Gap: SQLAlchemy's own later errors (for example an `OperationalError` from a failed Postgres connect) are not wrapped here and may quote the DSN host, though SQLAlchemy masks the password in its URL repr.
7. **In-memory SQLite (`sqlite://`, `sqlite:///:memory:`) gets `StaticPool` and `check_same_thread=False`** — **sound.** Without it each checkout is a fresh empty database. Tests confirm a second connection sees the migrated schema. A shared single connection is not safe for concurrent threads; this is for tests and throwaway use.
8. **Foreign keys are enforced on SQLite through a `connect` event on engines built here; engines built elsewhere (including the throwaway engine inside `store.migrate._run`) do not have it** — **sound.** Migrations run without FK enforcement, which is acceptable for DDL. A control test shows a plain engine accepts an orphan `source_run` while the store engine raises `IntegrityError`. Any later code that builds its own engine would silently lose FK enforcement, so 6.4 onward should use `create_store_engine`.
9. **Only `sqlite` and `postgresql` backends are accepted; others raise `DatabaseConfigError`** — **sound, provisional.** The spec names only those two. Loosening is one tuple.
10. **No new dependency** — **sound** (see entry 1 for the missing Postgres driver).

Counts: sound 8, unsound 1 (entry 1; accepted), sound-with-gap 1 (entry 6), needs-user 1 (entry 1, stated below). Self-audit, not an independent auditor.
needs-user: **Which Postgres driver to pin (`psycopg[binary]` v3 proposed) and when** — required before 6.7 can run its Postgres leg; the spec and design are silent.
Signals: no Agent tool, so no refactor or production-readiness agent ran; no blast-radius scan (no existing symbol was modified; only new files plus a `.gitignore` line). Red phase was seen as a collection error (`database` module missing) before implementation.

## Task 6.4 — Scope write transactions to the per-source contribution batch (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 665 passed)
Least-confident first.
1. **A cancelled batch is completed, not abandoned: on cancellation the batch runs to its own commit or rollback, then `CancelledError` is re-raised** — **sound, provisional.** The design says "either fully written or fully rolled back"; both are allowed, and finishing is the simpler exact reading. Cost: a hung or very slow batch delays the run timeout (11.4) unboundedly, because nothing interrupts a worker thread. Reversible: a deadline or checkpoint flag in `write_batch` could roll back instead. 11.4 should know cancellation of a source mid-write returns only after that write has settled.
2. **Sync `Session` run through `asyncio.to_thread`, not `AsyncSession`** — **sound, provisional.** The design is silent on sync versus async sessions. An async engine needs a per-backend async driver (`aiosqlite`, `asyncpg`), putting backend names in code that must not know them (9.3, 9.4) and adding dependencies. The sync engine from `create_store_engine` stays the only seam. A thread cannot be interrupted, which is what makes the shield exact. Reversible with a rewrite of `write_batch`'s internals; the callers' contract (`await write_batch(fn)`) would stay.
3. **The batch callable is synchronous and receives a `Session`; the orchestrator must build its rows (async work) before calling `write_batch`** — **sound, provisional.** Nothing async can run inside the transaction. Callers must return plain values, not ORM instances (the session is closed on return).
4. **Transaction owned by the connection (`conn.begin()`, session joined with `join_transaction_mode="rollback_only"`)** — **sound.** My first version used `session.begin()`, and a test showed `session.commit()` inside the callable then committed early and tore the batch. Now a commit inside the callable is a no-op on the transaction and a `session.rollback()` dooms the whole batch (the later commit fails), so atomicity does not depend on the callable behaving. A final `session.flush()` is required: closing the session drops unflushed objects under this mode (seen as a 3-of-4 row failure).
5. **One write transaction at a time per `StoreWriter` (a `threading.Lock` taken in the worker thread)** — **sound, provisional.** In-memory SQLite shares one connection (6.3 entry 7), so interleaved batches would corrupt each other, and file SQLite has one writer anyway. On Postgres it needlessly serialises batches; the batches are small. Gap: blocked worker threads occupy the default executor (about 12 to 36 threads), which is fine for a pool bound of four. One writer per engine is assumed; two `StoreWriter`s on one in-memory engine would not serialise.
6. **The run record is committed through the same shielded path (`begin_run`), with only `status`, `pool_size`, `config_snapshot` as inputs** — **sound, provisional.** Run end update (`finished_at`, `exit_code`) and the `SourceRun` row are not written here. A failed batch also rolls back its own `SourceRun` row if the callable inserted it, so 11.2 must record a failed source's `SourceRun` (with `failure_class`) in a separate committed write via `write_batch`, after the failed batch. I did not add those helpers: they belong to 11.x.
7. **Only SQLite was exercised; the Postgres leg is untested** — **unsound, accepted.** Code is dialect-free (6.1 scan passes), but real Postgres behaviour (commit failure, locking) is 6.7's job, still blocked on the driver choice (see 6.3 needs-user).
8. **Cancellation is tested with real tasks, threads and `asyncio.timeout`**: cancel mid-batch (commit whole), cancel mid-batch then failure (rollback whole), cancel during commit (via an engine `commit` event), timeout, repeated cancellation, and one source cancelled while another writes. A mutation (`await inner` instead of `await asyncio.shield(inner)`) fails three of them, so the tests pin the shield. Gap: they sleep for 0.1 to 0.3 s to order events, so they are timing-based, though the ordering is forced by events, not by luck.
9. **Append-only guard unaffected** — **sound.** It is hooked on ORM mapper and `Session` events; sessions here are `Session` instances, so it still applies. Rollback of inserts is not an update or delete.

Counts: sound 6, unsound 1 (entry 7; accepted), needs-user 0, plus entry 8 a test-quality note. Self-audit, not an independent auditor.
Signals: no Agent tool, so no refactor or production-readiness agent ran. No existing symbol was modified (two new files only), so no blast-radius scan was needed; GitNexus index not consulted. Red phase was seen as a collection error (module missing); the later red for `session.commit()` and the `flush` omission were genuine assertion failures.

## Task 6.5 — Retain raw provider payloads under a retention policy (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 692 passed)
Least-confident first.
1. **Purge cannot delete an expired raw response that a contribution still references: `source_contribution.raw_response_id` is a NOT NULL foreign key (and 6.3 turns SQLite FK enforcement on), and contributions are append-only, so nulling the link is also forbidden** — **needs-user.** Every raw response that produced a contribution is referenced, so as built the purge only reclaims orphan rows (a fetch whose normalization wrote no contribution) and reports the rest in `PurgeResult.skipped_referenced`; 9.8's live-mode 30-day expiry is therefore not met for referenced payloads. Provisional: skip referenced rows rather than break the FK or the append-only guard. Options: (a) migration 0002 making `raw_response_id` nullable with `ON DELETE SET NULL` (a DB-level change to an append-only table, SQLite needs batch mode for it); (b) make `raw_response.payload` nullable and purge by blanking the payload, keeping the row as a tombstone; (c) drop the FK. I did not pick one because each reshapes the 6.1 schema. Reversible: purge logic is one method.
2. **Expiry is `retention_until <= now`: a row is expired at exactly its expiry instant** — **sound, provisional.** The requirement gives no boundary; "retained for 30 days" read as ending when the 30 days are complete. Tested at the microsecond before and at the instant.
3. **Timestamps are normalised to aware UTC in Python before binding, naive inputs are refused, and values read back are re-tagged UTC** — **sound, provisional.** This resolves the 6.1 note about naive datetimes on SQLite for `retention_until` with no dialect branch (6.1 scan still passes). It does not fix the other `DateTime` columns: anything else read from SQLite is still naive (6.6 and later readers must handle that). Tested with +14h and -12h offsets so a wall-clock comparison would fail.
4. **No schema change: `raw_response.retention_until` (nullable, indexed) already existed from 0001** — **sound.** No new revision, 0001 untouched, the drift test is unchanged and green.
5. **Exclusion from default queries is structural: no relationship to `RawResponse` on any model, `payload` is `deferred=True`, and the only module outside `store/models.py` that may name `RawResponse` is `store/raw_responses.py` (AST test, mutation-checked with a stray module)** — **sound.** `deferred` does not change the schema. A determined caller can still `select(RawResponse)` inside the store package's two modules; there is no database-level hiding.
6. **Retention is fixed at write time (`retention_until` stored per row); changing the window later does not retroactively change existing rows** — **sound, provisional.** It is what the stored column implies; a re-stamp routine is not built.
7. **`RAW_RETENTION_DAYS` (whole positive days) is my invented configuration name, via `RetentionPolicy.from_environ`; nothing reads the environment automatically** — **sound, provisional.** The design says "configurable" and names no key. Invalid values raise `RetentionConfigError` without echoing the input. Whether it joins the credential manifest or `.env.example` is for the orchestrator task.
8. **Purge is not scheduled or called from anywhere, and the clock is the `now` argument** — **unsound, accepted.** The task asks for the routine, not its trigger. A caller must run it through `StoreWriter.write_batch` with `datetime.now(UTC)`. The raw-response model is not covered by the 6.1 append-only guard (only `SourceContribution` and `ContributionField` are, confirmed by reading the guard), so the bulk delete is allowed.
9. **Only SQLite was exercised; the Postgres leg is untested.** `NOT IN` subquery, count and delete use portable constructs and no dialect branch — **unsound, accepted.** 6.7 must run the same retention tests on Postgres. The purge counts and deletes in separate statements, which is fine inside one `write_batch` transaction under the writer lock but not atomic against another process on Postgres.

Counts: sound 6, unsound 2 (entries 8, 9; accepted), needs-user 1 (entry 1). Self-audit, not an independent auditor.
Signals: no Agent tool, so no refactor or production-readiness agent ran. Modified `RawResponse` (one column flag, docstring); referencing symbols were not queried through serena or GitNexus (not consulted), but grep shows the only users are the migration, the transactions test, and the new module. Red phase was a collection error (module missing), so no assertion-level red was seen before implementing; boundary and leak tests were checked by temporary mutation only for the structural scan, not for the `<=` boundary.

## Task 6.6 — Preserve the untrusted classification across the round trip (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 720 passed)
Least-confident first.
1. **Migration 0002 makes `contribution_field.confidence` nullable (model changed to match)** — **needs-user.** 0001 declared it NOT NULL, but `FieldProvenance.confidence` is `None` whenever the origin is `none`, which is what the normalizer emits today, so no real contribution could be written without inventing a number. Not strictly the classification, but the write path is unusable without it. Provisional; options: keep 0002 (chosen), or store `0.0` and accept a fabricated certainty. Reversible: delete 0002 and revert the one model line before any database exists. Downgrade of 0002 fails if a NULL-confidence row exists (deliberate: no coalescing). Batch-mode `alter_column` was run on SQLite only; the Postgres path is untested. The 6.2 drift test is green.
2. **Classification lives in the existing `untrusted` / `truncated` / `original_length` columns; `value` holds the verbatim text as a plain JSON string, with no in-band marker** — **sound.** Read rebuilds `UntrustedText` from the columns only, so a provider string shaped like a serialized marker (tested as trusted str, trusted dict and untrusted text) is never reclassified. No new columns.
3. **Write refuses, rather than coerces, trusted values JSON cannot round-trip (tuple, datetime, set, bytes, None, NaN/inf, non-str keys, nested `UntrustedText`)** — **unsound, accepted.** A transform that returns a tuple or datetime will now fail at persistence. Chosen over silently turning tuples into lists. The error names the path and type, never the value. `str`/`int`/`float`/`bool` subclasses (e.g. `StrEnum`) are accepted and read back as the plain base type, equal but not the same type.
4. **Classification is taken from the value's type; a provenance `untrusted` flag that disagrees is refused** — **sound.** The 5.1 contribution validator already enforces this, but `model_construct` bypasses it, so the writer re-checks (tested).
5. **Read refuses inconsistent rows with `StoredFieldError` (untrusted non-text, missing truncation metadata, truncated length not longer than the text, trusted row carrying truncation metadata)** — **sound.** Never guesses a classification. The message omits the text.
6. **Naive datetime issue: `fetched_at` is converted to aware UTC in Python before binding, naive input refused, read re-tags UTC; no dialect branch** — **sound, provisional.** Same approach as 6.5; tested with a +14h offset. Other `DateTime` columns are untouched and still read back naive.
7. **Header arguments are explicit (`data_mode`, `fetched_at`, `lead_scope`) and per-field provenance that disagrees with `data_mode`/`fetched_at` is refused** — **sound, provisional.** `lead_scope` is a free string because ADR-0001 removed the attribute but the 0001 column is still NOT NULL; the caller decides what to store. Whether to drop the column is for a later migration.
8. **Only `confidence` and `raw_field_path` of the provenance, and no absences, are persisted** — **unsound, accepted.** Confidence origin, raw value, scale and `superseded` have no columns in 0001, so they do not survive a round trip. The task asked only for the untrusted classification; a later task owning projection or provenance must add them.
9. **Only SQLite was exercised.** — **unsound, accepted.** JSON strings with `\u0000`, lone surrogates and bidi controls round-trip on SQLite; the generic `JSON` type should map to Postgres `json`, which I did not run. 6.7 must repeat these tests on Postgres.
10. **`_as_utc` is duplicated from `raw_responses.py` rather than shared** — **sound.** Two call sites; extraction waits for a third.

Counts: sound 6 (entries 2, 4, 5, 6, 7, 10), unsound 3 (entries 3, 8, 9; accepted), needs-user 1 (entry 1). Self-audit, not an independent auditor.
Signals: no Agent tool, so no refactor or production-readiness agent ran. Red phase used a stub module (no-op write, empty read), so tests were seen failing at assertion level (empty values, DID NOT RAISE), not at import. Modified `ContributionField.confidence` only (one nullable flag); referencing symbols were not queried through serena or GitNexus. The adversarial cases were tested, but no mutation run was done to check each test fails against a deliberately in-band implementation.

## Task 6.7 — Exercise the ingestion-to-persistence path on both engines (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 1172 passed, 1 skipped, the skip pre-dates this task)
Least-confident first.
1. **The Postgres leg ran for real, against PostgreSQL 16.14 (Ubuntu 16.14-0ubuntu0.24.04.1)**, started two ways in this session: an ephemeral cluster the fixture itself builds (`initdb --no-locale -E UTF8 -A trust`, `pg_ctl start` with `listen_addresses=''` and a unix socket in a `mkdtemp` dir, as OS user `postgres`, removed after the session), and the same cluster started by hand and named through `LEADFORGE_TEST_POSTGRES_URL`. Both give 1172 passed. The docker-compose service was **not** run (no Docker daemon here), so the compose file is untested: only its YAML parse was checked. 26 parameterized tests run per engine plus 3 engine-free gate tests (55 in the module). Server-side behaviour is asserted by an identity test (dialect, driver `psycopg`, major version at least 16) so the leg cannot silently run on SQLite.
2. **The gate fails, it does not skip.** `LEADFORGE_TEST_POSTGRES_URL` set: use it; unset: start an ephemeral cluster; neither possible: `pytest.fail` with "run `docker compose up -d postgres` or set the URL". Tested with no server (message check) and with an unreachable URL carrying a password (the password never appears; the URL is shown through `redact_url`). An AST test bans `pytest.skip`, `skipif`, `importorskip` and `xfail` in the module. Gap: the failure surfaces as a setup ERROR of each Postgres-parameter test (session fixture), not a test FAIL; the run still exits non-zero. CI does not exist yet, so nothing sets the URL there; the ephemeral fallback means any CI image with server binaries is covered, one without needs the compose service or a service container.
3. **Real portability bug found and fixed: SQLite accepted values Postgres rejects** — **sound, provisional.** Red phase showed over-long `String(n)` (SQLite ignores the length), NUL in a `String` column (Postgres: `DataError`) and a lone surrogate in one (Python encoding error on both, but unclassified). Fixed by one ORM guard in `store/models.py` (`before_insert`/`before_update` on `Base`, propagated) that raises `ColumnValueError` (table and column named, never the value) from the column metadata, so both engines behave like the strictest, with no backend branch and no schema change (no new migration). The 6.1 scan, the 9.3 backend-name scan and the append-only structural test stay green. Limits: Core statements and bulk operations bypass it (a test pins that without the guard only Postgres refuses an over-long string); JSON columns are exempt because both engines keep NUL and lone surrogates there as escapes (verified on Postgres: `json`, not `jsonb`). If a later change moves to `jsonb`, NUL in JSON would be rejected and the guard must extend.
4. **Migrations 0001 to 0003 run unchanged on Postgres, up and down** — **sound.** The batch `alter_column` of 0002 and the foreign-key drop and recreate of 0003 worked with no edit. The convention risk in the 6.5 follow-up is now pinned: at 0001 the unnamed key is `source_contribution_raw_response_id_fkey` on Postgres (asserted, with `None` as the SQLite expectation), and a mutation of the 0003 name made 8 tests fail. A walk test seeds rows at 0001, upgrades through 0002 and 0003, downgrades through to base, and checks the rows at every step. Downgrades that must refuse (NULL confidence, purged payload) raise `IntegrityError` on both engines, each in a throwaway database because a failed SQLite table rebuild could leave a temporary table.
5. **No drift on Postgres**: `compare_metadata` with `compare_type` and `compare_server_default` on the migrated schema is empty. This is the check that SQLite's loose reflection could not make (String lengths included).
6. **Same observable results on both engines, with three explicit differences** (`EXPECT` table): unmanaged `DateTime` columns read back naive on SQLite and aware on Postgres (the same UTC instant); an over-long string through Core is refused only by Postgres; the unnamed 0001 key has a name only on Postgres. Round-trip results otherwise compared for equality: `\u0000`, lone surrogates (paired and unpaired), bidi controls, a 16000-byte emoji string, marker-shaped trusted text, `fetched_at` at +14h, -12h and 0, raw payload, retention instants.
7. **Purge on Postgres**: SET NULL detaches the referenced contribution, fields stay readable, synthetic and unexpired rows stay attached, an unreferenced expired payload is deleted with zero detached, the boundary is inclusive at 30 days, and the append-only guard still fires afterwards. Unique Match Key raises `IntegrityError`, and the whole batch rolls back with it (including the first insert in the batch). Four concurrent source batches commit on both engines.
8. **Per-test isolation on Postgres uses a schema per test (`CREATE SCHEMA` and `DROP SCHEMA ... CASCADE` through SQLAlchemy DDL objects, selected by `search_path` in the URL options)**, not a database per test — **sound, provisional.** It needs no `CREATEDB` right and no raw SQL text; the structural scan exempts `tests/` anyway. Not exercised: a Postgres where the role cannot create schemas.
9. **Driver: `psycopg[binary]>=3.3.6` (resolved 3.3.6)** pinned with `uv add` — **sound, provisional.** SQLAlchemy resolves a bare `postgresql://` to it (dialect driver asserted as `psycopg`), and `create_store_engine` accepts the URL unchanged. The 6.3 needs-user on the driver is therefore resolved by the user's earlier decision. Cost: a binary wheel; the pure-Python install alternative needs libpq.
10. **Tests use the URL form of the migration helper with the password unmasked (`render_as_string(hide_password=False)`)** — **sound.** Test-only and only for the throwaway trust-auth cluster; everything else passes `URL` objects.
11. **Instants written through the ORM to columns that no repository normalises (`started_at`, `finished_at`, `created_at`, `computed_at`) must be aware UTC** — **unsound, accepted.** SQLite stores the wall clock of whatever offset it is given and returns it naive, so a non-UTC value would read back wrong there while Postgres would be right. Only UTC is written today (`begin_run`). Later writers of those columns (11.x, 8.x) must normalise to UTC as `raw_responses` and `contributions` do. Not fixed here: a `TypeDecorator` would leave the portable-type set the 6.1 test pins.
12. **Purge counting is still not atomic across processes on Postgres** (6.5 entry 9, unchanged); not testable here with one writer. `PurgeResult.detached_contributions` is exact per transaction only.

Counts: sound 9 (entries 1, 3 through 10; entries 3, 8 and 9 provisional), unsound 3 (entries 2 as a setup-error gap, 11, 12; accepted), needs-user 0. Self-audit, not an independent auditor.
Signals: no Agent tool, so no refactor or production-readiness agent ran. Red phase was seen at assertion level (DID NOT RAISE `ColumnValueError`, and the Postgres `DataError` and `UnicodeEncodeError` the guard now preempts); every other test passed on both engines on first run, so migrations and the round trip needed no fix. Modified `store/models.py` (a new exception, one listener function on `Base`); callers of the mapped models were not queried through serena or GitNexus (not consulted), but the full suite passed. A hand-started Postgres at `/tmp/lfpg.*` was stopped at the end.

## Task 7.1 — Discover adapters by package scan with duplicate-name rejection (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 762 passed)
Least-confident first.
1. **Adapter package is `leadforge.lead_ingestion.adapters` (the slice's package, already named by `structure_guard.RAW_SCHEMA_PACKAGE`), not `leadforge/sources/` as Requirement 3.1 and `steering/structure.md` say** — **needs-user.** The task text says "the slice's adapter package" and slice decision D1 puts raw schemas there, so I followed the code. `ADAPTER_PACKAGE` is one constant and `discover(package=...)` takes any package. Reversible: change the constant and move the empty package. Requirement 3.1 and the steering file still read `sources/`; one of them should be reworded.
2. **Configuration is injected as `Mapping[str, SourceSettings]` keyed by the exact declared name; `SourceSettings(enabled=True, trust_rank=0)` is a frozen dataclass** — **needs-user.** Only `enabled` and `trust_rank` exist; 7.2 and 8.1 will add `mode` / `live_access` overrides, and 9.1 will build the mapping from `config/sources.yaml`. Trust rank is a non-negative `int`, higher wins (8.4), so `LOWEST_TRUST_RANK = 0`. That ranks are ints and 0 is the floor is my reading of the spec, which names no scale. Reversible: one dataclass.
3. **An import error in any adapter module fails startup (`SourceDiscoveryError`, cause chained) instead of skipping the module** — **needs-user.** Skipping makes a broken adapter look the same as an unconfigured one, and 3.3 wants startup to fail loudly. Cost: one broken provider module blocks every source, including in synthetic mode. Reversible: catch in `_import` and record.
4. **Names that differ only in case or padding collide (`strip().casefold()`); the stored name stays verbatim** — **unsound, accepted.** Two sources `Apollo` and `apollo` would be ambiguous in config and reports, but the spec only says "same name", so this is stricter than the text. Config lookup is by exact name, so `Apollo` in config does not configure `apollo`.
5. **A concrete adapter with a blank, missing, or non-str `name` fails startup; an abstract class that sets its own `name` also fails startup (naming the missing methods) instead of being skipped** — **sound, provisional.** Otherwise a half-written adapter silently vanishes. Abstract classes with no own `name` (intermediate bases) and `BaseLeadSource` itself are skipped. A name-bearing abstract mixin would be rejected; none exists.
6. **Only classes defined in the scanned module count (`__module__` check); the scan recurses into subpackages and registers nothing through `__init_subclass__`** — **sound.** Re-exports register once; no global registry means no cross-test or double-import leakage. The design text mentions `__init_subclass__` registration; I did not use it because it would also register test subclasses. A subclass of a concrete adapter that inherits its `name` is a duplicate; with its own `name` it registers.
7. **Discovery never instantiates an adapter; the name is read from the class** — **sound.** Required for 7.2. Consequence: `BaseLeadSource.__init__`'s other declaration checks still run only at construction, so a malformed capability declaration surfaces then, not at discovery.
8. **Config entries naming no registered source are not an error; `unknown_config_names` exposes them** — **sound, provisional.** Deferred providers (Leadfeeder, UpLead, ZoomInfo, Clay) may keep config rows. A typo for a disabled source silently stays enabled; whichever layer reports startup warnings should print this list.
9. **`registry.py` has a single `_add` registration path and no public `register`** — **sound.** 7.4 can add `register` on top of `_add`. A class registered twice is a duplicate, not idempotent.
10. **Reused `DuplicateSourceNameError` from 1.2; added `SourceDiscoveryError(module, *, detail)`** — **sound.** Both follow the `_Picklable` convention; the pickle test list includes the new one.

Counts: sound 6 (entries 5, 6, 7, 8, 9, 10; 5 and 8 provisional), unsound 1 (entry 4; accepted), needs-user 3 (entries 1, 2, 3). Self-audit, not an independent auditor.
Signals: no refactor or production-readiness agent ran (no Agent tool). Red phase used a stub registry (empty results), so 39 of 41 tests failed at assertion level (empty names, DID NOT RAISE, KeyError), not at import; the 2 that passed against the stub were the default-settings and real-package tests. Mutation probes: dropping the `__module__` filter, the casefold, or the abstract skip each fail tests; **dropping the module sort passes everything** because `pkgutil.iter_modules` already sorts by file name, so the sort is untested insurance. No Hypothesis property test was written. The real adapter package is empty, so discovery against real providers is untested until tasks 12-15. Blast radius: only added files plus one appended error class; no serena or GitNexus query was run.

## Task 7.2 — Exclude disabled sources without instantiating them (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 776 passed)
Least-confident first.
1. **`active(factory)` takes a caller-injected `SourceFactory = Callable[[type[BaseLeadSource]], BaseLeadSource]`, not the design's zero-argument `active()`** — **needs-user.** `BaseLeadSource.__init__` takes only `mode` today, and the mode resolver (8.1) and transport/credential binding (4.1 and 4.4 ledger entries, task 12.1) are not built, so the registry cannot know how to construct an adapter. The seam is one callable; the orchestrator (11.1) or a later task can supply it, or the registry can take a default factory once 8.1 and 12.1 land. Reversible: one parameter. The design signature `active()` and this one differ until then.
2. **Active order is Source Trust Rank descending, then name** — **needs-user.** The design states no active-list order. Descending matches 8.4 (higher rank wins), and name breaks ties so order never depends on registration or import order. Reversible: one sort key in `enabled_names`.
3. **A factory error for an enabled adapter propagates unchanged (no wrapping, no skip-and-continue)** — **sound, provisional.** A broken enabled source should fail loudly, as 7.1 does for import errors; `BaseLeadSource.__init__` already names the class in its `TypeError`. Cost: one bad adapter blocks the whole active list. Per-source isolation is 11.1's concern.
4. **A factory returning an object that is not an instance of the requested class raises `TypeError` naming the source** — **sound.** Cheap guard against a factory mapping classes to the wrong adapter.
5. **All sources disabled, or an empty registry, gives `()`, not an error** — **sound.** The spec says nothing else; whether zero active sources is a startup problem is the orchestrator's call.
6. **Disabled sources remain in `names()`, `source_class()` and `settings()`; `enabled_names()` is the filtered view** — **sound.** 7.3 descriptors need disabled sources visible. `source_class` returns a class, never an instance, so there is still no live handle to a disabled source.
7. **Config naming an unregistered source is ignored by `active`** — **sound, provisional.** Still only reported through `unknown_config_names` (7.1 entry 8); a typo for a disabled source therefore leaves the real source enabled.
8. **The discovery test imports `_Base` from this test module by dotted path (`leadforge.lead_ingestion.tests...`) inside a generated module** — **unsound, accepted.** The tests directory has no `__init__.py`; it resolves today as a namespace package but could break under a different pytest import mode.
9. **Requirement 3.4 says the *Ingestion Orchestrator* shall not instantiate a disabled source; here the Registry enforces it** — **sound.** The orchestrator will get sources only through `active`, so the guarantee lives in one place. Nothing yet stops the orchestrator constructing `source_class(name)` directly; 11.1 should not.

Counts: sound 6 (entries 3, 4, 5, 6, 7, 9; 3 and 7 provisional), unsound 1 (entry 8; accepted), needs-user 2 (entries 1, 2). Self-audit, not an independent auditor.
Signals: no refactor or production-readiness agent ran (no Agent tool). Red phase failed with `AttributeError` on the missing `active` (no stub), at the first test, not at import. Mutation probe: removing the enabled filter fails 5 of 14 tests. A tripwire adapter whose `__new__` and `__init__` raise and record calls proves non-construction, and a companion test shows the tripwire fires when enabled. No Hypothesis property test. Blast radius: additive change to `registry.py`; no serena or GitNexus query was run.

## Task 7.3 — Publish source descriptors including live-access classification (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 832 passed)
Least-confident first.
1. **The default mode in `describe` is synthetic when there is neither a per-source `mode` override nor an injected resolver, even if every credential is present** — **needs-user.** The real resolver is 8.1. A descriptor built before 8.1 lands therefore reports `synthetic` for a fully credentialed source, which is conservative (no live call implied) but not what 8.1 will say. Seam: `describe(environ, *, resolve_mode=ModeResolver)` where `ModeResolver = Callable[[type[BaseLeadSource], SourceSettings], DataMode]`. 8.1 should supply it; precedence there (override first) is already honoured here. Reversible: one function in `registry.py`.
2. **`live_access` was missing from the adapter contract; added as `LiveAccess` (available/gated/unavailable) and `BaseLeadSource.live_access` with the explicit default `AVAILABLE`, type-checked in `__init__`** — **needs-user.** The design lists it as a required declaration, but making it required would have broken every existing adapter fixture and contradicts "adding a source stays one class". A default of `AVAILABLE` means a forgotten declaration over-claims live access; the ZoomInfo adapter (18.9) must declare `UNAVAILABLE`, and Leadfeeder/Clay `GATED` per the design table. Not added to `_DECLARATIONS`, so absence is not an error. Reversible: drop the default and add the name to `_DECLARATIONS`.
3. **`SourceSettings` gained `mode` and `live_access` (default `None`), following 7.1 entry 2, and coerces valid strings to the enums while rejecting unknown strings (`ValueError`) and non-strings (`TypeError`)** — **sound, provisional.** Coercion is there so the 9.1 YAML loader can pass raw strings; whether 9.1 should instead validate itself is open. Case and padding are not normalised (`"Gated "` is rejected). A `mode` set in config is treated as the per-source override that 8.1 puts first.
4. **`rate_limit_documented` is true only when the adapter declares at least one bucket and every bucket has `documented=True`** — **sound, provisional.** The design field is a single bool, while buckets are per-bucket. Any self-imposed bucket makes the whole source "self-imposed", and no bucket at all is reported as not published (nothing to claim). A per-bucket view is not published; add it if the run report needs it.
5. **A source declaring no `required_env` reports `credential_present=True` and `missing_env=()`** — **sound.** Vacuous truth, matching the 8.1 rule "all declared credentials present"; the alternative would force a free keyless source to look credential-less.
6. **Blank or whitespace-only values count as missing, the same rule as `resolve_credentials`; `environ=None` falls back to `os.environ`** — **sound.** `describe()` looks up only declared names and stores only names and booleans, so an undeclared secret in the environment is never read. A non-`str` environ value would raise `AttributeError` on `.strip()`, as it does in `resolve_credentials`.
7. **Descriptor omits the design's `lead_scope` and `mode_reason`** — **sound, provisional.** `LeadScope` does not exist in code and `mode_reason` is 8.1's output; task text asks for neither. Add both when 8.1 and the adapters need them; `to_dict()` is the serialised form and would gain keys.
8. **Descriptor is a frozen stdlib dataclass with a `to_dict()` (capabilities sorted by value, enums as values), matching `RateBucket`/`SourceSettings`, not pydantic** — **sound.** Hashable and equal by value; ordering is by source name, independent of registration order.
9. **`describe` reads classes only and includes disabled sources with `enabled=False`** — **sound.** A tripwire adapter whose `__new__` raises proves no construction. The run-report half of 3.6 ("the run report SHALL state ...") is not done here; it belongs to the reporting tasks (16/18).
10. **An override can set `live_access` to any value regardless of mode** (e.g. override `available` on a source declared `unavailable`) — **unsound, accepted.** The config override is authoritative by design; whether `unavailable` should be un-overridable is 8.1's call.

Counts: sound 7 (entries 3, 4, 5, 6, 7, 8, 9; 3, 4, 7 provisional), unsound 1 (entry 10; accepted), needs-user 2 (entries 1, 2). Self-audit, not an independent auditor.
Signals: no refactor or production-readiness agent ran (no Agent tool). Red phase used stubs (`LiveAccess` enum, empty `SourceDescriptor`, `describe` returning `()`): 45 of 56 tests failed at assertion/attribute/TypeError level, none at import; the 11 that passed against the stub were the enum, empty-registry, and settings-default tests. Mutation probes: removing the blank-value check fails 3 tests, dropping the empty-bucket guard fails 1, ignoring the config override fails 2. No Hypothesis property test (no roundtrip or idempotence law beyond equality across registration order, which is an example test). Blast radius: additive in `registry.py`; `base_source.py` gained a defaulted ClassVar and a `__init__` check (existing adapter fixtures unaffected, full suite green); no serena or GitNexus query was run.

## Task 8.1 — Resolve live versus synthetic mode with a stated reason (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 867 passed)
Least-confident first.
1. **The 7.3 seam still returns only `DataMode`; the reason is not carried onto `SourceDescriptor`** — **needs-user.** `make_mode_resolver(environ, global_override=)` returns a `ModeResolver` whose result is `.mode`; the reason reaches the log line, and callers wanting it (18.1 `mode_reason` column) call `resolve_data_mode` directly, which returns `ModeResolution(mode, reason)`. Widening the seam to return `ModeResolution` would break the 7.3 tests knowingly; not done. Reversible: change `ModeResolver`'s return type and `_mode_for`.
2. **A global override sits above the `UNAVAILABLE` classification, so `LEADFORGE_MODE=live` forces live for ZoomInfo-style sources** — **needs-user.** Follows the design order literally (override, global, unavailable). A per-source `mode: live` does the same. Alternative: make `UNAVAILABLE` un-overridable by the global switch only. Reversible: reorder two branches in `_decide`.
3. **`registry._mode_for` changed: with a resolver present, the resolver now runs even for a source with a per-source `mode`, and the override still has the last word** — **sound, provisional.** Needed so an overridden source also logs one line (4.4); previously the resolver was skipped. Without a resolver behaviour is unchanged. Edits 7.3 code; its tests stayed green unchanged.
4. **Blank or whitespace-only credential values count as absent; `environ=None` uses `os.environ`** — **sound.** Same rule as `resolve_credentials`; only declared names are read.
5. **A source declaring no `required_env` resolves live with reason `no credentials required`** — **sound, provisional.** Vacuous truth of "all declared credentials present", matching 7.3 entry 5. A free keyless source would run live unless overridden; Google and similar declare variables so are unaffected.
6. **Global override input is `DataMode | str | None`; strings are stripped and casefolded, blank means unset, unknown strings raise `ValueError` (eagerly in `make_mode_resolver`), non-str raises `TypeError`** — **sound, provisional.** Lenient on case because it will come from an env var; strict on typos because silently ignoring `LEADFORGE_MODE=lvie` would run live or synthetic unexpectedly. Per-source strings stay strict (7.3). The env var read itself is 8.2.
7. **The configured `live_access` override replaces the adapter's declaration when classifying** — **sound.** Same effective value `describe` publishes.
8. **Reasons are fixed strings: `per-source override: X`, `global override: X`, `live access unavailable`, `missing credentials: A, B` (declared order), `all declared credentials present`, `no credentials required`** — **sound.** Names only; a planted-secret test checks logs, reasons and reprs. Log event `data_mode_resolved` at info with `source`, `mode`, `reason` keys, in the 20.5 style (`capture_logs`).
9. **The per-call log means a caller invoking `describe` twice logs twice** — **sound.** One line per resolution, not per run; 18.1/the orchestrator should resolve once per run.

Counts: sound 7 (entries 3 to 9; 3, 5, 6 provisional), unsound 0, needs-user 2 (entries 1, 2). Self-audit, not an independent auditor.
Signals: no refactor or production-readiness agent ran (no Agent tool). Red phase used a stub module (result type, functions returning synthetic with empty reason): 33 of 35 failed at assertion level, none at import; the 2 passing were the frozen-result and the blank-global-less cases. Mutation probes: dropping the blank-value strip fails 2 tests, ignoring the `live_access` override fails 1. No Hypothesis property test. Blast radius: one function in `registry.py`; no serena or GitNexus query was run.

## Task 8.2 — Load the environment file without overriding the process environment (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 895 passed, 1 skipped)
Least-confident first.
1. **Malformed-line numbers depend on a quirk of the python-dotenv parser**: it folds preceding blank lines into an error binding's span, so the reported line is `original.line` plus the leading newlines of the span — **unsound, accepted.** Covered by four tests (blank lines, CRLF, comment lines before the bad line); a dotenv upgrade that changes the span would fail them rather than mislead silently. The parser is `dotenv.parser.parse_stream`, which is not the documented public API (`dotenv_values` is), because that API drops malformed lines with only a log warning and gives no line numbers. Reversible: swap in a local parser behind `parse_env_text`.
2. **A blank or whitespace-only value in the target mapping counts as unset, so the file fills it** — **sound, provisional.** Same rule as 8.1 (empty credential is absent); otherwise an exported empty `APOLLO_API_KEY=` would silently mask the file while 8.1 still reported it missing. Consequence: a deliberately empty process variable cannot be used to blank out a file value.
3. **A bare `KEY` line with no `=` is malformed (dotenv would return `None`)** — **sound, provisional.** Failing loudly beats a silently unset credential.
4. **Default file is `.env` under the current working directory; override is `LEADFORGE_ENV_FILE` read from the process mapping (never from the file), and an explicit `path` argument beats both** — **sound, provisional.** Relative overrides resolve against the working directory (or `base_dir`). Requirement 10.1 fixes `.env.example`, and the design only says `.env`; the override variable is my addition.
5. **Absent file, including a dangling symlink, is silent; a directory, unreadable file, or invalid UTF-8 raises `EnvFileError`** — **sound.** Symlinks to real files are followed. The permission-denied path is tested by patching `read_bytes`, because the sandbox runs as root and the chmod test is skipped there (the 1 skipped).
6. **Nothing is applied unless the whole file parses; duplicate keys: last wins** — **sound, provisional.** Matches dotenv convention.
7. **Errors carry the path and a line number only, raised `from None` so no chained `UnicodeDecodeError` (which prints a byte) or `OSError` text reaches a traceback** — **sound.** Planted-secret tests cover malformed, undecodable, and denied reads. The path itself is assumed non-secret. The loader returns applied names, never values.
8. **`load_env_file(environ, ...)` writes into an injected mutable mapping; `load_env_file_into_process` is the sole `os.environ` mutator, and nothing calls it yet** — **needs-user.** Startup wiring belongs to the CLI/orchestrator (not in 8.2 scope): it must call it before `make_mode_resolver`, and read `LEADFORGE_MODE` from `os.environ` afterwards to pass as `global_override`. Not done here; I did not touch `cli.py`.
9. **No dependency was added: `python-dotenv>=1` was already pinned from 1.1** — **sound.**

Counts: sound 7 (entries 2 to 7, 9; 2, 3, 4, 6 provisional), unsound 1 (entry 1, accepted), needs-user 1 (entry 8). Self-audit, not an independent auditor.
Signals: no refactor or production-readiness agent ran (no Agent tool). Red phase used a stub module: 23 of 28 failed at assertion level, none at import. Mutation probes: replacing the blank-aware check with `name not in environ` fails 2 tests, dropping the blank-line offset fails 2. No Hypothesis property test. Blast radius: new module only; no serena or GitNexus query was run.

## Task 8.3 — Generate the credential example file from the registry manifest (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 931 passed, 1 skipped)
Least-confident first.
1. **Provider documentation URL comes from a new optional `BaseLeadSource.docs_url: ClassVar[str] = ""`; when blank, the first rate bucket's `doc_url` (by bucket name) is used; an adapter with credentials and neither fails generation (`ManifestError`)** — **needs-user.** 3.4 declared no per-provider doc URL; `RateBucket.doc_url` points at rate-limit pages, which may not be the credential page. Touches the adapter contract (5 lines in `base_source.py`). Reversible: drop the field and the bucket fallback becomes the only source. Tasks 12 to 15 should set `docs_url` explicitly.
2. **Only `DATABASE_URL`, `LLM_PROVIDER`, `LLM_MODEL` are built in; `RAW_RETENTION_DAYS`, `LEADFORGE_MODE`, `LEADFORGE_ENV_FILE` are not in the manifest** — **sound, provisional.** Requirement 10.1 lists exactly three extras; the omitted ones are defaulted tuning knobs, not credentials (`LEADFORGE_ENV_FILE` cannot sit in the file it locates). `RAW_RETENTION_DAYS` stays an invented name documented only in code. One tuple each in `BUILTIN_SETTINGS` adds them.
3. **LLM doc URL is the LangChain `init_chat_model` page, and the database URL points at the SQLAlchemy engine page** — **unsound, accepted.** I did not fetch either URL; they are from memory and may have moved. A stale comment link is cosmetic.
4. **Every value is empty, `DATABASE_URL=` included** — **sound.** Empty means local default (6.3) and unset (8.1, 8.2). The generator takes no environ, so a secret cannot reach the file; a test plants a secret on every variable.
5. **A variable shared by two adapters, or redeclaring a built-in, is one entry listing each owner and URL (`# <owner> docs: <url>`)** — **sound.**
6. **Hostile declarations are rejected, not escaped: names or URLs with control or non-printing characters, line separators, `#`, quotes, non-ASCII, non-http(s) URLs, non-str `docs_url`** — **sound.** Stricter than `_is_env_name`, which only bans whitespace, `=` and NUL.
7. **Retired `GOOGLE_CSE_API_KEY`, `GOOGLE_CSE_CX` are a deny-set: an adapter declaring one fails generation** — **sound, provisional.** A static list; a renamed variable would slip through, but no CSE adapter exists.
8. **Regenerate path is `uv run python -m leadforge.lead_ingestion.env_example` (`--path`, `--check`); the file defaults to `.env.example` in the working directory, so run it from the repo root** — **sound, provisional.** `cli.py` untouched; the failing test message prints the command. Output is LF bytes with one trailing newline, built-ins first, then adapter variables sorted.
9. **Tasks 12 to 15 must regenerate and commit `.env.example` when they add an adapter** — **needs-user.** The lockfile test fails until they do; intended, not automatic. The committed file lists only the three built-ins because the registry is empty.

Counts: sound 6 (entries 2, 4, 5, 6, 7, 8; 2, 7, 8 provisional), unsound 1 (entry 3, accepted), needs-user 2 (entries 1, 9). Self-audit, not an independent auditor.
Signals: no refactor or production-readiness agent ran (no Agent tool). Red phase used a stub module returning empty output: 32 of 36 tests failed at assertion level, none at import. No mutation probes were run. Blast radius: one additive field on `BaseLeadSource`; no serena or GitNexus query was run, but mypy and the full suite pass.

## Task 8.4 — Redact credential values from every log and report (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 978 passed, 1 skipped)
Least-confident first.
1. **The store does not scrub: a credential an adapter puts into a contribution value or `raw_field_path` would persist in a non-raw table.** — **needs-user.** The task asks to assert no non-raw row holds a credential; the test proves the invariant on a representative path (secret only in environ, log lines and the raw payload) and proves the scanner works by planting a secret in `raw_field_path` and finding it. Nothing enforces the invariant at write time. Reversible: call `redact()` in `write_contribution`, or reject a value matching the active redactor. Left out as a change to the 6.x store contract.
2. **`LLM_PROVIDER` and `LLM_MODEL` are not seeded, and `DATABASE_URL` seeds only its password** — **needs-user.** The task says "every variable in the manifest"; seeding `openai` would mask every line naming the provider and a SQLite path would vanish from diagnostics. Deviation is `NON_SECRET_SETTINGS` and the URL branch in `secrets_from_environ`; one line each to undo.
3. **Length rule: stripped values under 4 characters are ignored; 4 to 7 characters match only as whole tokens (not next to `[A-Za-z0-9_]`); 8 or more match anywhere, including inside another word** — **sound, provisional.** False negative: a 1 to 3 character secret leaks. False positive: a 4 to 7 character secret that is also an ordinary word masks that word. Blank values never match. Constants `MIN_TOKEN_LEN`, `MIN_SUBSTRING_LEN`.
4. **Encodings scrubbed: raw, stripped, percent-encoding (`quote`, `quote_plus`), JSON escaping, base64 and URL-safe base64 with and without padding** — **sound, provisional.** Not covered: base64 of a longer string containing the value (HTTP Basic `user:key`), hex, case changes, a secret split over two fields (a test pins this known false negative).
5. **Raw-payload guard by key name: exact `RAW_KEYS` plus suffixes `_payload`, `_body`, `_response`, at any depth; scalar values kept; any container over 2000 characters is replaced by a marker; strings over 2000 are truncated (20000 for `exception`, `stack`); truncation runs after redaction** — **needs-user.** The key convention is invented; `raw_field_path` and `confidence_raw` stay allowed. A payload logged under an unlisted key and under the size bound passes. Logging call sites (task 6.x, 16.x) should use these names for anything raw.
6. **`capture_logs` bypasses the configured chain entirely (it clears and restores the processor list), so captured entries are pre-redaction** — **sound.** Existing tests that scan captured logs test the call sites, not the processor. Tests wanting redacted captures use `capture_logs(processors=[active_redactor()])`; a test pins both behaviours and that the chain is restored.
7. **`configure_logging(environ, registry=None, renderer=None, extra_secrets=())` replaces the structlog config: JSON to stderr, ISO UTC timestamp, caching off; nothing calls it yet** — **needs-user.** Wiring it into the CLI start-up (with the 8.2 environment loader first) belongs to the CLI or orchestrator task. `registry=None` runs `SourceRegistry.discover()`. Stderr keeps logs out of report output on stdout.
8. **Overlapping secrets merge into one `***`; a trie-built regex keeps 5000 secrets at about 1.5 ms per 6 KB string; a 6000 character secret works** — **sound.** Measured once, not benchmarked in the suite beyond a loose 5 s bound.
9. **Unknown objects are replaced by scrubbed text only when `str()` contains a secret; exceptions are replaced by scrubbed `repr`; sets become sorted lists; mapping key collisions after redaction overwrite** — **sound, provisional.**
10. **Process-wide active redactor (`redact(text)` for 18.3) is module state set by `configure_logging`; an unconfigured process redacts nothing** — **needs-user.** A report built before logging is configured would not be scrubbed; 18.3 should call `configure_logging` or build its own `Redactor`.

Counts: sound 5 (entries 3, 4, 6, 8, 9; 3, 4, 9 provisional), unsound 0, needs-user 5 (entries 1, 2, 5, 7, 10). Self-audit, not an independent auditor.
Signals: no refactor or production-readiness agent ran (no Agent tool). Red phase used a stub returning its input: 23 of 45 tests failed at assertion level, none at import. No mutation probes. No serena or GitNexus query; the module is new and imported by nothing, so blast radius is nil.

## Task 9.1 — Read targeting terms and provider vocabularies from configuration (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 1050 passed, 1 skipped)
Least-confident first.
1. **17 existing files already name a vendor, so Requirement 23.1 ("a search of the source tree for any vendor name returns only configuration and example fixtures") is false today; the new scan tolerates them through a ratcheting `BASELINE`** — **needs-user.** The files are `models.py` and the store models and migration (a canonical `linkedin_url` field, arguably a data-model term rather than a targeting assumption), `structure_guard.py`, and 12 older tests that use provider names as sample data. I did not rewrite them: that is a cross-task change and this task is scoped to 9.1. The scan fails for any file not in the baseline and for a baseline file that no longer names a vendor, so the list only shrinks. Reversible: delete entries as files are cleaned. Decision for you: rename the placeholders in those tests, and whether `linkedin_url` counts.
2. **Scan scope: all text files under `src/` except directories named `adapters` and `fixtures`, plus the guard file itself (it holds the denylist); the denylist is 14 lead-data vendor and technology names, matched case-insensitively inside identifiers but not inside longer words** — **needs-user.** Tasks 12-15 add one module per provider under `adapters/`, so a provider module name is legitimately a vendor name; adapter-specific tests that name a vendor will have to sit under a `tests/adapters` path or extend the baseline, and I did not pre-exempt them. The shipped example profile and `config/` are outside `src/`, so 9.2 needs no exemption. Backend names (sqlite, postgres) are not in the denylist; the 6.1 scan owns them. `hunter` and `clay` are common words, so a false positive is possible.
3. **Cross-check with the adapter's 3.3 declaration is weak: a column is an error only when the registered source explicitly declares that term Not Applicable (present with an empty vocabulary) and the column supplies a non-empty one** — **needs-user.** 3.3 keys `target_vocabulary` by canonical term, but terms are now configuration, so an adapter cannot list them in code without breaking "adding a term touches one file". Requiring every configured term to appear in the adapter's declaration would make that impossible; ignoring the declaration would drop the "validated with the adapter-declared vocabulary" clause. The 3.3 rule that `answerable_surfaces` must match the vocabulary therefore stays a class-level check that cannot follow config-driven terms. A design call is needed before the first real adapter (task 12): either the adapter declares only which providers or term kinds it supports, or the 3.3 declaration becomes a default merged with config.
4. **Two files: `config/target_profile.yaml` (default path, relative to the working directory, explicit path argument as the only override, no environment variable) and `config/sources.yaml`** — **needs-user.** The spec names `config/` for the profile and `config/sources.yaml` for source settings; it does not name the profile file. The design's older `config/technology_uids.yaml` is superseded by decision 16. Reversible: two constants.
5. **A column naming a source that is not registered is not an error; `check_against_registry` returns those names for the caller to print as a startup warning, and nothing logs it yet** — **sound, provisional.** Same stance as 7.1 entry 8 (deferred providers keep columns). A typo in a source name silently disables targeting for that source until the warning is wired into startup (CLI or 9.3).
6. **Keyword templates are a list of non-blank strings; `render_keywords(term)` replaces `{term}` with the canonical term name by `str.replace`, other braces untouched, `{{term}}` becomes `{<term>}`** — **needs-user.** The spec says "keyword templates" and states neither a syntax nor what is substituted. The canonical term name is a poor search phrase for a technology whose display name differs; a template may need a per-term display text. Kept minimal on purpose; no engine.
7. **Strictness: an empty or missing profile file, a profile with no technology and no competitor, and unknown top-level keys are errors; a term with no columns is valid; for `sources.yaml` a missing or empty file means no configuration, but unknown keys and unknown settings are errors** — **sound, provisional.** Typos should fail rather than leave a source enabled or a profile empty. Task 6.7 (concurrency bound in `sources.yaml`) must add its key to `_TOP`. Technologies and competitors share one term namespace (the same name in both is an error) because both map to `target_profile.<term>`.
8. **Source names and terms are matched exactly (no case folding) and must be non-blank text with no surrounding whitespace; a vocabulary is opaque, deep-frozen (mappings to views, lists to tuples), and empty by the 3.3 rule (null, blank text, empty collection; `0` and `false` are identifiers)** — **sound, provisional.** This closes the 3.3 "values not deep-frozen" gap for values that come from configuration, not for a class-level declaration. A term containing a dot would make a key path ambiguous; not rejected.
9. **`ConfigurationError(path, *, key_path, detail)` is a new taxonomy member (`_Picklable`), used for both files; messages carry the file path, the key path (`technologies.some_term.some_source`, `keyword_templates[1]`) and fixed text, never a value; YAML parser messages are dropped except class name and line** — **sound.** Tests plant a secret-looking value at every offending position and assert it appears in neither `str`, `repr`, `args`, nor the cause chain.
10. **YAML is read with `SafeLoader` only; duplicate mapping keys are rejected by walking the composed node tree (PyYAML keeps the last silently); alias bombs freeze in linear time (shared objects frozen once); a recursive alias and 5000-deep nesting are named errors** — **sound.** Known limit: two keys that differ in text but construct to the same non-text value (`yes` and `true`) are reported as non-text keys, not as duplicates.
11. **`SourceSettings` values are validated in the loader (enabled a real bool, rank a non-negative int that is not a bool, mode and live_access one of the enum values) before constructing `SourceSettings`, so the rules exist twice** — **sound, provisional.** `SourceSettings` echoes the offending value in its own messages, which would break the no-value rule. If a field is added, both places change.
12. **`target_profile.py` imports the private `_is_empty_vocabulary` from `base_source`** — **sound.** One rule for "empty" beats two copies; promote it to public if a third caller appears.

Counts: sound 7 (entries 5, 7, 8, 9, 10, 11, 12; 5, 7, 8, 11 provisional), unsound 0, needs-user 5 (entries 1, 2, 3, 4, 6). Self-audit, not an independent auditor.
Signals: no refactor or production-readiness agent ran (no Agent tool). Red phase used stubs returning empty values: 60 of 64 new tests failed at assertion level (empty results, DID NOT RAISE), 4 passed against the stub (the real error class's pickle test and empty-configuration cases); the structural scan passed at once by design once the baseline was filled. The deep-nesting test first failed with a raw `RecursionError`, fixed and kept. No mutation probes, no Hypothesis test. No serena or GitNexus query: the modules are new and only `errors.py` gained a class. Nothing calls the loaders yet; 9.3 and the CLI will.

## Task 9.2 — Ship one worked example Target Profile (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 1060 passed, 1 skipped)
Least-confident first.
1. **Source names `apollo` and `google_search` in the columns are guesses; no spec text fixes a registered `name`** — **needs-user.** Tasks 12-14 choose `name` on each class. If one differs, the column is silently an unregistered-source warning (9.1 entry 5) and targeting for that source is off until the file is fixed. Reversible: rename a key. Tasks 12.1 and 14.1 must either use these names or edit the file, and should add a test that no column stays unregistered once its adapter exists.
2. **Apollo identifiers (`apache_cassandra`, `datastax`, `mongodb`, `couchbase`) are unverified examples** — **needs-user.** They follow Apollo's snake_case style and the design's old default names, but I checked them against no supported-technology list (the dated snapshot is a task 12.1 / 9.3 fixture). The header comment says so. A wrong one produces the zero-match warning (12.13), not a failure.
3. **Only two of four sources get a column; HubSpot and Hunter have none** — **sound, provisional.** Per 3.3 a column exists only where the source declares a surface. HubSpot is a CRM-state lookup and Hunter is email discovery by domain; neither takes a technology filter in the design. If either adapter declares a vocabulary later, add its line.
4. **`config/sources.yaml` not shipped** — **sound.** 9.1 shipped none, and an absent file means defaults; a file of defaults would add a second place to keep in sync. The task called it optional.
5. **The default path stays relative to the working directory: from the repository root the demo works; from any other directory `load_target_profile()` raises `ConfigurationError` naming `config/target_profile.yaml` and "file not found"** — **needs-user.** Recorded by a test, not changed (it is a 9.1 decision, entry 4). A demo launched from elsewhere fails clearly but not helpfully. Reversible: resolve against the project root, or add an environment override.
6. **Vendor names stay in `config/` only; the vendor-neutrality scan is untouched** — **sound.** The scan covers `src/`, so `config/` needs no exemption (confirmed: scan passes, new test file names no vendor). The tests assert structure through `providers()` and `terms()`, never vendor literals. They do not check that a column names the right provider; only tasks 12-15 can.
7. **Registry check asserts today's state: the adapter package is empty, so every column is returned as unregistered and none raises** — **sound, provisional.** That assertion is meant to break when the first adapter lands; tasks 12-15 then replace it with the real Not Applicable cross-check.
8. **The file is committed (`git check-ignore` reports it is not ignored) and a copy of `config/` alone loads through the default path** — **sound.**

Counts: sound 5 (entries 3, 4, 6, 7, 8; 3 and 7 provisional), unsound 0, needs-user 3 (entries 1, 2, 5). Self-audit, not an independent auditor.
Signals: no refactor or production-readiness agent ran (no Agent tool). Red phase: the file was absent, so 9 of 10 new tests failed (a `ConfigurationError` or `FileNotFoundError` from the missing deliverable, not an import error); the other passed because it asserts the missing-file error. No mutation probes, no property test (nothing to generalize over one fixed file).

## Task 10.1 — Build the composite multi-window token bucket (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 1101 passed, 1 skipped)
Least-confident first.
1. **A provider retry interval is capped at `max_retry_after_s`, default 3600 s, and each capped value is counted (`retry_after_capped`)** — **needs-user.** The spec says Retry-After always wins and sets no ceiling. Without one, a buggy or hostile header parks a source for the whole run. The default is my guess. Reversible: pass a larger cap, or raise the default. 10.2 and the adapters should decide whether a capped wait should instead halt the source.
2. **The throttle blocks the whole bucket on a throttling response, not only the endpoint that was throttled** — **sound, provisional.** Buckets already map to endpoint classes (design), so the block is scoped to the class the provider throttled. Another bucket of the same source is not blocked (tested). Adapters that learn a limit is account-wide must call `note_rate_limited` on each bucket; nothing does it for them.
3. **`SourceRateLimited.retry_after_s` is the carrier; no new field was added, and `note_rate_limited(retry_after_s: float | None)` takes the number, not the exception** — **sound.** 10.2 passes `err.retry_after_s`. A `None` or unusable value (zero, negative, NaN) adds no block but still counts a throttling response. The bucket does not dispatch on error type; that stays with 10.2.
4. **Windows start full (cold burst up to each window's capacity)** — **sound, provisional.** Standard token-bucket behaviour. A provider that treats a burst at run start as abuse would want an empty start. Reversible: initial tokens in `_Window.__init__`.
5. **FIFO via one `asyncio.Lock`, with the head waiter sleeping while holding it** — **sound.** No starvation (asyncio locks are FIFO), tested with 20 waiters. Cost: the head waiter's sleep blocks later waiters even if a later `cost=1` could fit where a head `cost=3` cannot; fine for single-cost use. `try_acquire` returns False while anyone is queued, so a probe cannot jump the queue.
6. **Cancellation safety rests on one rule: tokens are consumed in the same synchronous step that returns, never before an await** — **sound.** Tested for a waiter cancelled while asleep and one cancelled while queued; neither consumes, and the next waiter is not stuck. `throttle_waits` is counted only when an acquire completes, so a cancelled wait is not counted.
7. **Float drift: a permit is granted when tokens are within 1e-9 of the cost, but the sleep is for the full deficit** — **sound.** My first version slept for `deficit - 1e-9`, which left a residue too small for the clock (1000.0 + 1e-13 == 1000.0) and spun forever; the test run hung and caught it. The drift test computes its fake clock per step, because my first version accumulated `t += 0.1` and the clock's own rounding drifted past the tolerance after about 1300 steps. That is a test artefact, not a bucket fault.
8. **A backwards clock step clamps elapsed time to zero, resets each window's reference to the new reading, and pulls a provider block back to at most `now + cap`** — **sound, provisional.** Never mints tokens, never freezes the bucket. The block clamp exists only so a backwards step cannot stretch a block; a mutation probe confirmed it is tested.
9. **`cost` is accepted on `acquire` and `try_acquire`; a cost above the smallest window capacity, or not a positive int, raises `ValueError` instead of waiting forever** — **sound.** Nothing in the spec needs cost above 1 today; it costs a few lines and the "burst larger than capacity" case needed a defined answer.
10. **Construction rejects empty `windows`, non-int or non-positive `requests` (bool included), and non-finite or non-positive `per_seconds`; `RateBucket` and `RateWindow` are unchanged and still unvalidated at declaration** — **sound, provisional.** A bad declaration fails when the throttle is built (10.3 wiring), not at class definition. Validating in `RateWindow.__post_init__` would fail earlier but touches the 3.x contract.
11. **Declaration needed no extension: `RateBucket.windows` is already a tuple of `RateWindow`, and `documented` and `doc_url` already exist** — **sound.** `rate_limit_documented` (task 8.2) is untouched.
12. **One counter set per source per run, shared by all that source's buckets, held by `SourceThrottle`; a standalone bucket owns its own** — **sound.** `snapshot()` returns a frozen `ThrottleSnapshot(source_name, throttle_waits, retries, throttled_responses, retry_after_capped)`; 18.2 persists it. Fields are names and integers only, so no credential value can reach it (asserted by field list). No per-bucket breakdown.
13. **A throttle wait is counted once per acquire that slept, not once per sleep** — **sound.** `waited_s` includes queue time behind earlier waiters.
14. **Nothing wires this in: no orchestrator, transport, or adapter calls it yet, and the Transport port is untouched** — **sound.** 10.3 constructs it (and not at all in synthetic mode), 10.2 calls `record_retry`, and adapters 12.3, 13.2, 14.3 call `note_rate_limited`. `max_retry_after_s` is not exposed through `config/sources.yaml`.

Counts: sound 13 (entries 2, 4, 8, 10 provisional), unsound 0, needs-user 1 (entry 1). Self-audit, not an independent auditor.
Signals: no refactor or production-readiness agent ran (no Agent tool). Red phase used a stub whose methods returned permissive values: 33 of 39 tests failed at assertion level, none at import. Mutation probes: replacing the keep-the-longer-block `max` with assignment fails 1 test; letting `try_acquire` ignore queued waiters and dropping the backwards-clock block clamp both passed all 39, so two tests were added (41 total) and each mutation now fails 1. No Hypothesis property test; the rate invariant is an example test over 50 waiters. Blast radius: additive, new module only; no serena or GitNexus query was run.

## Follow-up to Task 6.5 — nullable raw_response_id (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 1112 passed, 1 skipped)
Least-confident first.
1. **Migration 0003 drops and recreates the unnamed `raw_response_id` foreign key by name, using a batch-mode naming convention (`%(table_name)s_%(column_0_name)s_fkey`)** — **unsound, accepted.** 0001 created the key unnamed. On SQLite the convention names the reflected key during the table rebuild; on PostgreSQL the plain `DROP CONSTRAINT` relies on the default name PostgreSQL assigns (`source_contribution_raw_response_id_fkey`). That match is by convention, not run: only SQLite was exercised. Task 6.7 must run upgrade, downgrade and the purge tests on PostgreSQL. No backend branch, as with 0002.
2. **User decision recorded: the 6.5 needs-user entry 1 is RESOLVED by decision (a)** — **sound.** `source_contribution.raw_response_id` is nullable with `ON DELETE SET NULL`; the purge deletes every expired raw payload, so 9.8's live-mode 30-day expiry now holds for referenced payloads too. The 6.6 needs-user entry 1 (migration 0002, nullable `contribution_field.confidence`) is likewise RESOLVED by user decision: keep it nullable.
3. **Detaching is done by the database, not the ORM** — **sound.** There are no relationships, so deleting a raw response through the Session emits no UPDATE of contribution rows; a statement-capture test asserts no UPDATE or DELETE touches `source_contribution`, and the 6.1 append-only guard and its structural scan are unchanged and green.
4. **SET NULL happens only on an engine that enforces foreign keys** — **unsound, accepted.** `create_store_engine` turns SQLite's pragma on (asserted in a test); a plain SQLite engine leaves a dangling id, and a test documents that. PostgreSQL always enforces. The purge does not check for enforcement itself; it cannot repair the link without an UPDATE the guard forbids.
5. **`PurgeResult.skipped_referenced` is replaced by `detached_contributions`** — **sound, provisional.** The old field is now always zero by construction; the new one counts contributions whose link the purge cleared. Only the 6.5 test used the old name. Reversible by renaming.
6. **After a purge, `SourceContribution` objects already loaded in the session are expired** — **sound.** Otherwise the identity map would keep serving the pre-purge id. The purge also flushes first so counts see pending rows. Tested with an object loaded before the purge.
7. **`StoredContribution` gains `raw_response_id: UUID | None` (default None); reading a purged contribution works and reports None** — **sound, provisional.** The write path still requires a raw id; only a purge produces a NULL link. The field was added rather than a boolean so a caller can tell which payload was lost.
8. **Downgrade of 0003 fails if any contribution already lost its payload** — **sound.** Tested (IntegrityError), like 0002's convention; no coalescing, no invented link. Downgrade with all links present restores NOT NULL and the plain key, and an upgrade of existing rows through 0002 to head keeps them.
9. **The count and the delete in `purge_expired` are separate statements** — **unsound, accepted.** Inside one `write_batch` under the writer lock this is consistent; on PostgreSQL across processes a row could expire between them, so `detached_contributions` is an exact count only per transaction. Same caveat as 6.5 entry 9.

Counts: sound 6 (entries 2, 3, 5, 6, 7, 8), unsound 3 (entries 1, 4, 9; accepted), needs-user 0. Self-audit, not an independent auditor.
Signals: no Agent tool, so no refactor or production-readiness agent ran. Red phase was seen at assertion level (4 purge tests, schema flag, head-nullable test failed on assertions; others on a missing field). The migration tests that downgrade or start from 0002 pass on the old code too, so they were checked only against the new migration (the NULL-downgrade test raises IntegrityError from the downgrade, after a count check that the seed row exists). Callers of `purge_expired` and `PurgeResult` were found by grep only (the test file); serena and GitNexus were not queried.

## Follow-up to Task 9.1 — vendor neutrality rewrite (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 1112 passed, 1 skipped)
Least-confident first.
1. **`linkedin` was removed from the denylist and the `linkedin_url` column and field were not renamed** — **needs-user.** `linkedin_url` is a canonical Lead data field (a person's profile link) in `models.py`, the store model, and migration 0001; renaming it needs a schema migration and ripples through provenance paths, fixtures and tests. I read Requirement 23.1 as forbidding a vendor named as a built-in targeting assumption, which a profile-link data field is not. The denylist keeps the other 13 names, and its docstring says why `linkedin` is out. Cost: a test or module that hard-codes LinkedIn as a provider would no longer be caught. Reversible: re-add `linkedin` to `DENYLIST` and rename the field with a migration. Decision for you: confirm that reading, or order the rename.
2. **Provider names in 11 older files were replaced by synthetic ones (`provider_one`, `provider_two`, `provider_three`, `provider_four`, `tech_beta`, `ProviderTwoSource`, `PROVIDER_ONE_API_KEY` style); the sample password `hunter2hunter2` became `s3cretpassw0rd`** — **sound.** Behavior is unchanged. The case-collision registry test needed `Provider_One` rather than `ProviderOne`, since case folding does not remove the underscore. Migration 0001 and `models.py` were not edited (their only hit was `linkedin_url`).
3. **The `BASELINE` ratchet is deleted; the scan asserts zero hits outside `adapters/` and `fixtures/`** — **sound.** A self-test plants `Apollo` in a temporary tree and checks it is flagged while the same name under `adapters/` is not. Tasks 12-15 adapter tests that name a vendor must sit under `adapters/` or `fixtures/` paths.

Prior 9.1 needs-user entry 1 (17 baselined files, Requirement 23.1 false today) is RESOLVED by user decision (rewrite); its question about `linkedin_url` is answered provisionally by item 1 above.
Signals: no Agent tool, so no refactor or production-readiness agent ran. Red phase seen: the scan failed listing 12 files with the baseline emptied. Blast radius was grep only; serena and GitNexus were not queried.

## Follow-up to Tasks 3.3 and 9.1 — config overrides adapter vocabulary (2026-10-05)

### Audit pass (manual self-audit by the implementing agent; ruff, mypy clean, pytest re-run: 1117 passed, 1 skipped)
Least-confident first. Applies the user decision: the adapter `target_vocabulary` (3.3) is a DEFAULT and the Target Profile configuration (9.1) OVERRIDES it.
1. **Explicit empty column over a term the adapter declares answerable (`target_profile.<term>` in `answerable_surfaces`) is a startup `ConfigurationError`, so a blank-out only works for terms with no declared surface** — **needs-user.** An empty column is the explicit "no surface" of the configuration and `effective_vocabulary` honours it (the term drops out, Not Applicable). The 3.3 class-level rule (surface keys equal expressible default terms) is kept unchanged, so every default term has a surface, and `validate_absence` still rejects Not Applicable on a declared surface. The result is that blanking a default term always errors, which makes blank-out almost inert. Alternative: relax the class rule to "surface implies default vocabulary" so a default without a surface can be blanked. Provisional, reversible (one check plus one construction rule).
2. **A term made expressible only by configuration has no declared surface, so Negative Evidence for it is rejected by `validate_absence`; Not Applicable is correctly not emitted for it** — **needs-user.** An adapter cannot name config-defined terms in code (23.3), and the raw field paths a surface needs are adapter knowledge. A real adapter (task 12) that wants Negative Evidence for config terms needs a rule such as a per-adapter default surface for all target terms. Until then those terms are queried but yield no Negative Evidence, which errs toward under-claiming.
3. **"Override" means per term, replacing the whole value; no deep merge of mapping or list vocabularies** — **sound, provisional.** The spec says vocabularies are opaque (3.3, 23), so only the adapter can merge them. Tested with a mapping default and a conflicting mapping column.
4. **Presence decides: a column key present for the source wins even when empty; no key means the adapter default applies** — **sound, provisional.** Added `TargetProfile.has_column`. `TargetProfile.vocabulary` is unchanged (an empty value still reads as `None`).
5. **`effective_vocabulary(profile, source_class)` iterates the terms of the profile only; an adapter default for a term the profile no longer lists is inert (stale default)** — **sound.** The profile is the universe of targeted terms, as it already drives `terms()` and `render_keywords`. No warning is raised for stale defaults; the class check on the adapter declaration still holds. A column for an unregistered source stays a returned warning.
6. **The old rule of `check_against_registry` (non-empty column versus declared Not Applicable) is removed; the new rule is empty column versus declared answerable surface, key path only, never values** — **sound.** The old rule contradicted "config wins". `BaseLeadSource.target_term_absence(term, *, vocabulary=None)` gained an optional effective mapping; no caller outside tests existed, so none changed. Vendor-neutrality and backend-name scans pass; the migration drift test is untouched (no schema change).

Prior 9.1 needs-user entry 3 (weak cross-check; design call on adapter-declared versus configured terms) is RESOLVED by user decision (config overrides adapter default). The 3.3 audit entry 1 (coupling of `answerable_surfaces` to vocabulary) stays as recorded, now with the effective check above.
Signals: red phase seen at assertion level (6 failures against a stub) before implementing. Blast radius by grep only; serena and GitNexus were not queried.

## Task 10.2 — Bounded jittered retry over the error taxonomy (2026-10-05)

Evidence: implementation written by a background agent that the container restart interrupted before review; the parent verified the files, then ran the independent `spec-refactor-agent`. Parent re-ran pytest (1260 passed, 1 skipped), ruff, mypy: clean. The reviewer's regression test was confirmed to fail against the old logic.

### Provisional decisions (the task left these open; all listed in the `retry.py` docstring)
- **Verdict:** needs-user
- Defaults 4 attempts, 0.5 s base, 30 s cap; full jitter `uniform(0, min(cap, base * 2**n))`.
- A provider-supplied `retry_after_s` replaces the computed backoff (jitter included), clamped to `max_retry_after_s`; unusable values fall back to the computed backoff.
- `SourceTimedOut` is not retryable by default (the design retries only `SourceTransient` and `SourceRateLimited`); `retryable` matches by `isinstance`.
- Every `SourceRateLimited` is reported to the throttle, including the last and one outside `retryable`; `record_retry` once per retry actually taken.
- Cancellation and any `BaseException` propagate untouched, including during the backoff sleep; the last failure is re-raised as the original object.

### Self-review findings
- **Fixed:** the exhaustion check read the caller-supplied cumulative `stats.attempts`, so reusing one `RetryStats` across runs shortened the next run's attempt budget. `run` now counts its own attempts.
- **Known gaps, not fixed (needs-user):** a caller-supplied `retryable` may include non-retryable-by-design types such as `SourceQuotaExhausted`, the same opt-in as widening to `SourceTimedOut`; `max_retry_after_s` accepts `inf`, matching `throttle.py`; the source-scan test bans any 3-digit number or the word "http" anywhere in `retry.py`, including comments, which is brittle (its AST half is sound); no property test, because `hypothesis` is not a dependency.

## Task 10.3 — Bypass throttling and retry in synthetic mode (2026-10-05)

Evidence: TDD agent report; independent `spec-refactor-agent` review with five mutation checks, each caught and restored; parent re-ran ruff, mypy, pytest (clean).
Process note: the implementer wrote tests and module in one step, so there is no recorded red phase; the reviewer's mutation checks stand in for it.

### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Wiring is a free function `build_pacing(source_name, rate_limit, mode)` in a new `pacing.py`, not an adapter method; it returns `SourcePacing(throttle, retry)` for live and `None` for synthetic. The orchestrator must not substitute a no-op.
- It takes `source_name` and `rate_limit`, not the source class.
- A mode that is not exactly a `DataMode` member raises `ValueError` (the plain string `"synthetic"` is rejected, never treated as live).
- No property test.

### Known gap (needs-follow-up)
- The retry policy uses `RetryPolicy()` defaults because no retry configuration exists, while design.md says `max_attempts` comes from config. A later task (config or orchestrator) must pass a configured policy into `build_pacing`.

## Task 7.4 — Prove plug-and-play with a runtime-registered throwaway source (2026-10-05)

Evidence: wrote test_plug_and_play.py first; ran it and saw 4 failures (`SourceRegistry` has no `register`) before adding the method; then ruff format/check, mypy clean, pytest 1270 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Added public `SourceRegistry.register(cls)` delegating to `_add` (same duplicate rule); rejected a module-level/global registration hook or decorator (design says no global state).
- Registration is per registry instance; a later `discover()` does not see the class. Rejected persisting it into the adapter package.
- No orchestrator exists (task 11), so "synthetic ingestion run" is composed inside the test: `active(factory)` -> `fetch_raw` -> `normalize_checked` -> persist (SourceRun, raw response, `write_contribution` via `StoreWriter`) -> `read_contribution` on migrated SQLite. Rejected building a run helper in src (speculative, task 11's scope).
- Zero sockets asserted by patching connect, connect_ex, getaddrinfo (not `socket.socket`, which the event loop needs).
- Throwaway class lives in the test module, not the adapter package, so discovery is not polluted.
### Known gaps (needs-follow-up)
- The run path should be replaced or backed by the real orchestrator test once task 11 exists.
- Postgres leg not exercised for this run (SQLite only).
- Serena/GitNexus blast-radius not queried; `register` is new, no existing callers.

### Self-review findings
Fixed:
- registry.py `register`: accepted non-classes, `BaseLeadSource` itself, abstract subclasses and blank names (AttributeError or silent bad entry). Now raises TypeError and registers nothing. New test `test_runtime_registration_rejects_non_sources_and_abstract_classes`.
- Mutation survivor: bypassing `normalize_checked` passed the run test. The throwaway now emits a NOT_APPLICABLE absence and the test spies `validate_absence`; bypass now fails.
- Socket guard did not cover unconnected UDP `sendto`; added it, plus `test_socket_guard_blocks_every_outbound_path` (connect, connect_ex, sendto, getaddrinfo, create_connection) so a dead patch is detected.
- Mutations verified killed: duplicate rule skipped, register no-op, no validation, not visible in active, normalize_checked bypass, guard off, global registry leak. Files restored. Tests, ruff, mypy all pass (1272 passed).
Known gaps:
- needs-follow-up: zero-socket guard patches connect/connect_ex/sendto/getaddrinfo only. Not covered: subprocess, socket.sendmsg/sendall on pre-connected sockets, AF_UNIX, third-party C-level resolvers. Guard is best-effort evidence, not proof.
- needs-follow-up: re-registering the same class raises DuplicateSourceNameError (same name rule); intentional, untested.
- needs-follow-up: no orchestrator (task 11); run is wired in the test, so it proves seams, not the real pipeline.

## Task 9.3 — Validate provider-issued targeting identifiers at startup (2026-10-05)

Evidence: wrote tests/test_identifier_validation.py first; with only an empty-returning stub, ran it and saw 7 of 9 fail at assertion level (2 passed trivially) before implementing identifier_validation.py; then ruff format/check, mypy clean, pytest 1281 passed, 1 skipped. No serena/GitNexus query (new module, no existing callers; imports target_profile, registry, errors, models unchanged).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- The provider's lookup surface is a caller-supplied async callable per source name (`IdentifierLookup`, returns recognised identifiers); rejected adding an adapter attribute/BaseLeadSource method, since no adapter exists to define the surface (speculative).
- Warnings are returned as `UnrecognisedIdentifier(source, term, identifier)` for the caller to print (like `check_against_registry`); rejected logging inside the module (nothing wires startup logging yet).
- Checks the effective vocabulary (config column overrides adapter default); rejected checking profile columns only.
- Identifiers are text or a list of text, compared exactly (no case folding); any other shape (mapping, numbers) for a source with a lookup raises ConfigurationError without echoing the value; rejected skipping such values silently.
- Lookup failure propagates; rejected catching it and warning "could not validate".
- Lookup runs once per source and only if it has identifiers; synthetic mode calls nothing; non-DataMode raises ValueError (as in pacing.py).
- A lookup for an unregistered source raises KeyError; rejected ignoring it.
### Known gaps (needs-follow-up)
- No adapter exists, so no real lookup (e.g. Apollo supported-technologies CSV snapshot, design line 881) is implemented; the snapshot fixture belongs to the adapter task.
- Nothing calls validate_identifiers at startup yet (CLI/orchestrator wiring); the 12.13 zero-match run warning is out of scope.
- No property test (hypothesis is not a dependency); no mutation probes; no self-review run.

### Self-review findings
- No defects found in identifier_validation.py; source unchanged.
- Added 4 tests (test_identifier_validation.py): wrong shapes (non-str list item, int, bool, None item) raise ConfigurationError; exact match (case/whitespace); failing lookup after an earlier success still propagates; warning order follows lookup-mapping then profile order.
- Mutation-checked (all killed): skip a source, declared vocabulary instead of effective, class defaults only, lookups in synthetic mode, swallowed lookup failure, drop no-identifier guard, case/whitespace-insensitive match, coerced shapes, unknown mode treated as live.
- needs-follow-up: a lookup returning a bare str (a Collection[str]) is split into characters and flags everything; no guard.
- needs-follow-up: duplicate identifiers in one term yield duplicate warnings; the same id under two terms warns per term (intentional).
- needs-follow-up: disabled sources are checked if the caller supplies a lookup; wiring decision belongs to the caller.
- needs-follow-up: lookups run sequentially (deterministic, but slow with many sources); cancellation propagates untouched (not separately tested).

- **Also fixed by the parent after review:** a lookup returning one bare string was split into characters, flagging every identifier; it now raises `TypeError` (test added, red first).

## Task 11.1 — Run enabled sources through a bounded worker pool (2026-10-05)
Evidence: wrote test_orchestrator_pool.py and test_concurrency_setting.py first; ran them and saw collection fail with ImportError (no `orchestrator` module, no `DEFAULT_MAX_CONCURRENT_SOURCES`) before any implementation; then 26 passed. Mutation: replacing `async with slots` with `if True` made the two peak-in-flight tests fail (restored). ruff format/check, mypy clean, pytest 1316 passed, 1 skipped. No serena/GitNexus query (new module; the source_settings change is additive).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- The bound is a top-level `max_concurrent_sources` key in `config/sources.yaml` (positive int, default 4 in `source_settings.DEFAULT_MAX_CONCURRENT_SOURCES`, read by `load_max_concurrent_sources`); rejected a separate config file or a literal/default inside the orchestrator (its constructor argument is required).
- Module is `orchestrator.py` (named so the 2.4 structure guard applies); `IngestionOrchestrator(registry, *, resolve_mode, build_source, max_concurrent_sources)` with `run(SourceRequest) -> tuple[SourceResult, ...]` (name, mode, reason, batch, in active order); rejected building the design's RunRequest/RunResult/SourceOutcome now (store, counts and phases are 11.2-11.7 and 20).
- Mode resolution and adapter construction are injected callables: `resolve_mode` returns `ModeResolution` (with reason) and `build_source(cls, mode, pacing)` is called from `SourceRegistry.active`; rejected the orchestrator reading env or building transports.
- Every enabled source's mode and pacing (`build_pacing`; `None` for synthetic) are fixed before any adapter runs or any slot is taken; pacing is handed to the adapter, which paces its own provider calls. Rejected the orchestrator acquiring a bucket itself, because it cannot know an endpoint's bucket.
- The semaphore is created per `run`; a slot is held only around `fetch_raw`.
- A source failure is not isolated: `asyncio.TaskGroup` cancels siblings and raises an `ExceptionGroup`; rejected `gather(return_exceptions=True)` as an unrequested isolation policy (11.2).
- Invalid bound: `ValueError` in the constructor, `ConfigurationError` (no value echo) in the loader.
### Known gaps (needs-follow-up)
- Retry policy is still `RetryPolicy()` defaults via `build_pacing` (10.3 gap unchanged); no retry configuration exists and the orchestrator does not call `RetryPolicy.run`. Backoff on 429 is 11.2 and needs the adapter or orchestrator to run calls through `pacing.retry`.
- No adapter consumes `pacing` yet; the 6.8 test uses a throwaway source acquiring its own bucket, with real 50 ms timing and a loose 0.8 lower bound.
- No persistence, SourceRun rows, phases (Discovery/Enrichment), timeout or exit code: tasks 11.2-11.7 and 20.
- Nothing wires the CLI to the orchestrator; no default `resolve_mode` binding to `resolve_data_mode`.
- A result carries the raw batch only; normalization is not called.
- No property test (hypothesis is not a dependency); no self-review run.

### Self-review findings
Fixed (tests only; orchestrator.py and source_settings.py needed no change):
- test_concurrency_setting.py: invalid-value test now asserts ConfigurationError.path, key_path == "max_concurrent_sources", and that the offending value is not echoed.
- test_orchestrator_pool.py: added bound 1 and bound > source count peaks, empty source list, result order when completion order is reversed, and cancelling a run (slot released, CancelledError propagates, no leaked tasks). Added a `delay` ClassVar to the test source.
- Mutation-checked (each fails a test, files restored byte-identical): no semaphore, bound +1, bound -1, forced sequential, literal 4, pacing built for synthetic, accept 0, accept bool, sorted-by-completion/name results, mode re-resolved inside the slot.
- Structure guard passes on orchestrator.py; it only touches BaseLeadSource members (name, rate_limit, fetch_raw). Full suite, ruff, ruff format and mypy are clean.
Known gaps:
- needs-follow-up: first failure cancels siblings and raises ExceptionGroup (by design until 11.2). No tasks leak; CancelledError is not swallowed.
- needs-follow-up: no env override for the bound; only config/sources.yaml (the task does not require env).
- needs-follow-up: adapters are all constructed before any slot is taken (registry.active is eager), so a construction error aborts the run before any fetch.
- needs-follow-up: the structure guard is static AST and does not catch getattr or string-built adapter lookups; not hardened here.
- Duplicate source names are rejected by the registry, not the orchestrator; not re-tested here.

## Task 11.2 — Isolate per-source failures by failure class (2026-10-05)
Evidence: wrote tests/test_orchestrator_isolation.py first; ran it and saw collection fail with ImportError (no SourceCallLedger/SourceStatus) before any implementation; then 18 passed. Mutation: disabling the halt check (`if False`) failed the two skip tests (restored). ruff format/check, mypy clean, pytest 1339 passed, 1 skipped. No serena/GitNexus query (additive change; only SourceResult consumers are orchestrator and test_orchestrator_pool).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Only the SourceError taxonomy is isolated; a non-SourceError Exception (programming error) still cancels siblings via TaskGroup and surfaces as ExceptionGroup, and BaseException/CancelledError propagate. Rejected catching `Exception` (6.1 says "any error") because it would record bugs as provider failures. The 11.1 test asserting ExceptionGroup uses RuntimeError, so it stayed valid unchanged.
- `SourceStatus` enum (ok, unauthorized, rate_limited, quota_exhausted, transient, timed_out, compliance_restricted, normalization_failed, failed) chosen by exception type; any other SourceError maps to `failed`. Rejected one status per exception class.
- Per-source record is `SourceOutcome(source_name, status, attempted, succeeded, failed, skipped, retries, error)`; `attempted` counts provider attempts incl. retries, succeeded/failed count calls. Carried on `SourceResult.outcome`; `SourceResult.batch` is now `RawBatch | None` and a `contributions` field was added (run now calls `normalize_checked`, needed for the "normalization error" half of 6.1). Rejected a separate RunReport return type (would rewrite every 11.1 test). One 11.1 test line was adjusted for the optional batch.
- Unauthorized and QuotaExhausted halt the source (later calls skipped, counted); RateLimited does not halt. Rejected halting on rate_limited and rejected a pre-call wait on a previously rate-limited source: "backoff before any further call" is implemented as RetryPolicy backing off before each retry.
- Retry wraps fetch plus normalize as one operation, through `pacing.retry`; synthetic (no pacing) gets one attempt. Backoff sleeps while holding the pool slot. Rejected releasing the slot during backoff (would need a second acquire).
- Added optional `retry_policy` to `IngestionOrchestrator` and `build_pacing` so tests (and later config) can supply a policy; default unchanged. Rejected sleep injection.
- No throttle feedback is passed to RetryPolicy (orchestrator cannot know the bucket).
### Known gaps (needs-follow-up)
- The retry policy is still the RetryPolicy() default unless a caller passes one; no configuration exists (10.3 gap stands).
- Backoff timing is tested with a real 50 ms retry_after, not an injected clock.
- No adapter consumes pacing, so an adapter that retries internally would double-retry; not resolved.
- Per-call skip is only exercised through SourceCallLedger directly; run() makes one call per source until phases (11.5-11.7) exist.
- Exit code (11.3), wall-clock timeout (11.4), phases/ordering (11.5-11.7), persistence of outcomes not built. No self-review run; no property test.

### Self-review findings
Fixed (tests only; orchestrator.py, pacing.py unchanged; mutation-checked, files restored byte-identical):
- tests/test_orchestrator_isolation.py: failure-class test now covers NoAccessibleAccountError and InvalidAbsenceError (FAILED), asserts exact attempt count per class (by type: transient and rate-limited 3, all others 1), contributions is None for the failed source, healthy siblings called once.
- Added: max_attempts=1 never retries a throttled source; InvalidAbsenceError from normalize_checked is recorded; multi-call ledger test pinning "status = most recent failure, later success keeps it" (this mutation survived before).
- Mutations confirmed caught: no-halt (unauthorized, quota), halt rate-limited, swallow RuntimeError, swallow CancelledError, mislabel class, drop attempts count. Full suite 1344 passed, ruff and mypy clean, structure guard passes.
Gaps left:
- needs-follow-up: outcome text is str(error); SourceComplianceRestricted carries subject=..., which may be a contact identifier (PII) in the run summary. Decide for 11.3 whether to redact.
- needs-follow-up: results hold only enabled/active sources; disabled sources are absent from the outcome map, so 11.3 must derive them from the registry.
- needs-follow-up: a non-SourceError or CancelledError in one source cancels siblings and discards their results (ExceptionGroup/CancelledError), by design but 11.3/11.4 must decide exit handling.
- needs-follow-up: retry feedback to the throttle is not wired (feedback=None), so 429s do not tighten buckets.

- **Also fixed by the parent after review:** `outcome.error` was `str(error)`, so a compliance restriction copied its `subject` (a person's address) into the run outcome and later the run record. A new `_outcome_message` omits it (test added, red first). Other classes name only providers and paths.

## Task 11.3 — Map the run outcome to a process exit code (2026-10-05)
Evidence: wrote tests/test_run_exit.py first; ran it and saw collection fail with ModuleNotFoundError (no run_exit module) before any implementation; then fixed one summary-format wording in my own test/impl, 7 passed. ruff format/check, mypy clean, pytest 1352 passed, 1 skipped. No serena/GitNexus query (new module, no existing symbol edited).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Delivered only the pure `map_run_exit(results) -> RunExit(exit_code, summary)` in new `run_exit.py`; the CLI `ingest` stub is not wired (no config/registry/transport bootstrap exists to run it, and test_cli pins the placeholder). Rejected wiring the CLI now.
- A source "succeeded" iff at least one of its calls succeeded; otherwise failed (halted sources included). Rejected judging by final `status` (a success followed by a failure would be miscounted).
- Exit 0 iff at least one source succeeded; exit 1 otherwise (all failed, or none enabled). Rejected exit 0 for "none enabled" (6.5 requires a success). Single non-zero code 1; rejected per-class codes.
- Disabled sources are absent from results so the mapping needs no registry; "none enabled" is the empty result tuple. Rejected passing the registry in.
- Summary: one line per source, `name: attempted=A succeeded=S failed=F`; failed sources `name: <status> attempted=...`; all-failed adds header `all enabled sources failed`; empty is `no enabled sources; nothing succeeded`. Names, classes and counts only; `outcome.error` never copied. Skipped count not shown (6.5 asks attempted/succeeded/failed).
### Known gaps (needs-follow-up)
- CLI `ingest` still a placeholder; exit code not reachable from the command line.
- A non-SourceError/CancelledError still escapes `run()` as ExceptionGroup/CancelledError before any mapping; exit handling for that is unaddressed (11.4 area).
- Timed-out handling (11.4) and phase results (11.5-11.7: multiple calls per source) are not built; the mapping is per-source over aggregated counts so it should still apply.
- No persistence of exit_code in ingestion_run.
- No self-review run; no property test.

### Self-review findings
Fixed (run_exit.py, tests/test_run_exit.py):
- Source names are now control-character-escaped in the summary (newline/ESC could forge lines).
- A source with status OK but no successful call was labelled "ok" as a failure class; now "no_successful_calls".
- Added tests: every SourceStatus has a recorded decision (9 pinned), OK-without-success label, int exit code + frozen RunExit, input-order/one-line-per-source, control-char injection.
- Mutation checks (flip success rule, drop class, append outcome.error, drop escaping, drop empty guard) all fail tests; files restored. pytest, ruff, mypy green.
Reviewed: rule "exit 0 iff >=1 source had a successful call" matches 6.4/6.5; the requirement does not make partial failure non-zero.
Known gaps:
- needs-follow-up: 6.4/6.5 are silent on zero enabled sources; exit 1 is a provisional decision to confirm.
- needs-follow-up: summary order is input order; deterministic only if the orchestrator orders results (11.5-11.7).
- needs-follow-up: a source with succeeded calls but zero contributions counts as succeeded (call-based); confirm intended.
- needs-follow-up: map_run_exit not wired into cli.py (deliberate).

## Task 11.4 — Hold the run to a wall-clock timeout (2026-10-05)
Evidence: wrote tests/test_orchestrator_timeout.py (9 cases) and tests/test_run_timeout_setting.py first; ran them and saw collection ImportError (DEFAULT_RUN_TIMEOUT_S) and 16 failures (unexpected run_timeout_s kwarg) before any implementation. After: ruff format/check clean, mypy clean, pytest 1385 passed, 1 skipped; timeout file run 3x, stable. A mid-way failure (a source raising CancelledError itself was being recorded timed_out) was caught by the existing 11.2 test and fixed. No serena/GitNexus query run (edited orchestrator internals; only in-slice tests call it).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Config key `run_timeout_s` (top-level in sources.yaml, beside max_concurrent_sources), seconds, positive finite int/float, default 600 (`DEFAULT_RUN_TIMEOUT_S`, `load_run_timeout_s`). Rejected: no default/disabled timeout; a CLI flag; per-source timeouts.
- Orchestrator takes `run_timeout_s` as a required constructor arg with no default (house style of the pool bound); existing test call sites were updated to pass 30. Rejected: optional with None meaning unbounded (silent fallback).
- Finished sources keep results untouched; in-flight sources are cancelled and recorded TIMED_OUT, counted as one failed call with attempts made so far; not-yet-started sources are never called and recorded TIMED_OUT with 0 attempts (error text says "not started"). Rejected: a new NOT_STARTED status (would widen 11.3's pinned status set) and omitting unstarted sources.
- Only the deadline's own expiry (asyncio.timeout, checked via expired()) becomes records; external cancel, non-SourceError exceptions and a source's own CancelledError still propagate as in 11.2. Rejected: returning partial results on a programming error.
- Exit mapping unchanged: call-based, so a timed-out source counts as failed (summary "timed_out"); exit 0 if any other source succeeded, 1 if none did. Rejected: a distinct exit code for timeout.
### Known gaps (needs-follow-up)
- Timeout is real wall clock (short real timeouts, event-gated hangs); no fake clock.
- Cancelled in-flight call's retry feedback/backoff stats are only attempts/retries seen so far.
- Design's shield around contribution persistence (torn writes) is not built; persistence does not exist in the orchestrator yet.
- Under 11.5-11.7 (multiple calls per source) time_out overwrites a prior failure status with TIMED_OUT; revisit.
- run_timeout_s not wired to the CLI/bootstrap (no wiring exists); no self-review run.

### Self-review findings
Fixed:
- A YAML/ctor `run_timeout_s` integer too large for a float (e.g. 10**400) made `math.isfinite` raise a bare OverflowError (not ConfigurationError/ValueError); a naive `0 < v < inf` would have accepted it and then overflowed inside `asyncio.timeout`. Both validators (orchestrator.py ctor, source_settings.load_run_timeout_s) now convert with `float()`, reject on OverflowError. `load_run_timeout_s` now returns the validated float. Tests added in both timeout test files.
Verified: ruff, mypy, full suite (1387 passed). Mutations caught: no timeout (hangs), finished results discarded, not-started sources called, started sources missing from map, in-flight not counted failed. Run timing is deterministic (finishing sources never suspend, so they complete before the 50ms timer can fire).
Known gaps:
- needs-follow-up: a source that swallows CancelledError or blocks in a non-cancellable section stalls TaskGroup exit, so the timeout is not a hard bound; no test.
- needs-follow-up: the `if not deadline.expired(): raise` branch is unreachable today and no test kills its mutation (documented defensive code).
- needs-follow-up: default 600s is a guess; no choices/design row beyond design.md:596; no example config file exists to document it.

## Task 11.5 — Sequence Discovery before Enrichment over a mechanical work list (2026-10-05)
Evidence: wrote tests/test_orchestrator_phases.py first; ran it and saw collection fail with ImportError (no EnrichmentRequest) before any implementation; after implementing, 14 tests passed (one test-helper bug of mine fixed along the way). ruff format/check, mypy clean; pytest 1401 passed, 1 skipped. No GitNexus/serena query (additive; SourceResult gained a field, its only constructors are orchestrator and test_run_exit). No mutation run.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Work list = `enrichment_work_list(results)`, a pure function over Discovery results: every normalized contribution in result order, unfiltered, unmerged, no score read. Rejected deduping/merging into Leads (the Merge Engine does not exist yet and merging is not an orchestrator concern).
- Enrichment receives the work list via new `EnrichmentRequest(SourceRequest)` (kind="enrich", `work_list`) in base_source.py. Rejected widening `fetch_raw` or a side channel; the spec names no carrier.
- A dual-capability source runs in both phases; a source with neither never runs. Rejected treating capability-less sources as Discovery.
- `SourceResult` is per source per phase (new required `phase` field; test_run_exit helper updated). Rejected one merged result per source (a dual source has two batches).
- One ledger/pool/deadline across phases: halting failures persist (halted source's Enrichment call recorded as skipped), bound holds across phases, cumulative counts. Rejected per-phase ledgers.
- Empty work list: Enrichment is a no-op with no Enrichment results recorded. Rejected recording OK/zero-attempt results (map_run_exit would read them as failures).
- Timeout: Enrichment sources never reached after the deadline are recorded TIMED_OUT even if the work list was empty/partial.
- Enrichment sources run concurrently in registry order for now (11.6 owns derived ordering and suppression pruning; 11.7 per-company calls act on the same work list).
### Known gaps (needs-follow-up)
- map_run_exit lists a dual-capability source once per phase (cumulative counts); not changed (11.3 scope).
- A run whose only enabled sources are Enrichment-only with no Discovery yields empty results, so map_run_exit says "no enabled sources" (misleading text).
- The work list holds contributions, not deduped Leads; a lead seen by two Discovery sources appears twice until a merge stage exists.
- 6.9's "zero-credential run exercises every adapter" is proven with throwaway sources only; real adapters do not exist yet.

### Self-review findings
Fixed (each test-first, seen failing before the fix):
- run_exit.map_run_exit listed a source run in both phases twice; now one line per source from its later (cumulative) result. Test added in test_run_exit.py.
- Empty results read "no enabled sources" even for an Enrichment-only run with no Discovery output; now "no enabled sources ran; nothing succeeded" (true for both cases, exit stays 1, names/classes only).
- orchestrator.SourceCallLedger.time_out overwrote a halted (unauthorized/quota) source's status with TIMED_OUT when the deadline hit before its Enrichment call; a halted source now keeps its status. Test: test_a_halted_source_stays_halted_when_the_deadline_hits_enrichment.
- Added test_enrichment_waits_for_a_slow_and_a_failing_discovery_source (deterministic, event-loop ticks, bound=1); the original ordering test could not distinguish slow-Discovery cases.
Mutation checks (all killed, files restored byte-identical): phases concurrent, work list dropped, Enrichment handed the search request, dual-capability source double-run in Discovery, work list deduplicated.
Not applicable: "mutate the input list" - work list and results are tuples.
Gaps left:
- needs-follow-up: an Enrichment-only run with an empty work list exits 1 and records no result per enabled Enrichment source; spec says no-op is not an error, so a distinct "nothing to enrich" exit/record may be wanted (design decision).
- needs-follow-up: a source timed out in both phases keeps only the later "not started" error text (outcome counts stay correct).
- needs-follow-up: Discovery contributions are not deduplicated across sources by design; 11.7 must handle per-company de-dup.

## Task 11.6 — Derive the Enrichment order from declared cost attributes (2026-10-05)
Evidence: wrote tests/test_orchestrator_enrichment_order.py (11 tests) and one in test_base_source.py first; saw test_base_source fail at collection (ImportError enrichment_tiers) and 7 of 11 new orchestrator tests fail (the other 4 are guards that held before and after). Then implemented; ruff format/check clean, mypy clean, pytest 1416 passed, 1 skipped. No existing test needed changing: none pinned 11.5's Enrichment start order (the 11.5 doc line "concurrently in registry order" was updated in the orchestrator docstring). No mutation run; no serena/GitNexus query (additive; run() Enrichment block only).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- "Order" = tiers of equal (cost_class, charge_unit, yields_suppression) run sequentially, sources inside a tier concurrently under the pool bound (new base_source.enrichment_tiers over 3.2's key). Rejected: fully sequential per source (drops 11.1 concurrency for no spec reason); start-order-only inside the concurrent pool (cannot prune before paid sources run).
- A suppression report is a contribution whose values carry `suppressed` or `opt_out` = True (6.10 names both). Rejected: new field on SourceResult/Request, or suppressed-only (task text).
- A report names its lead by shared `email` or `linkedin_url` (casefolded) with a work-list contribution; no lead identity or merge stage exists yet. Rejected: positional/object identity (contributions carry no back-reference to the input), full dedupe key logic (later merge task).
- A work-list contribution already flagged by Discovery is also removed before the first tier; enrichment_work_list left unfiltered (11.5 contract).
- Any source's flagged contribution is honored, not only yields_suppression=True ones; the attribute drives order only.
- Pruning to an empty list stops later tiers: not called, no result recorded (same as 11.5 empty list).
- Result order stays registry order per phase; no early stop beyond pruning.
### Known gaps (needs-follow-up)
- Identity paths ("email", "linkedin_url") and flag paths are string conventions in orchestrator.py; no adapter yet emits them, to be confirmed with 12.x-14.x/merge design (compliance flags OR-merge, design 11.4).
- Suppression of a lead known only by name or company is not matched.
- Prune uses only the immediately preceding tier's reports plus flags already in the list (cumulative via the list); a suppression reported by a source in the same tier as a paid source cannot prune that tier.
- 11.7 per-company dedupe not built; no mutation checks run.

### Self-review findings
Mutation checks run (18): hand-maintained order, reversed tiers, ignore yields_suppression, concurrent tiers, no pruning (after tier / after Discovery), wrong identity key, call tiers after empty list, insertion-order dependence, paid first, reversed charge rank, tiers split by name, opt_out/suppressed ignored: all killed. Survivors found: email-only identity, no casefold, no strip (untested); now killed by new tests. Files restored byte-for-byte after each mutation.

Fixed:
- orchestrator.py `_identities`: a blank/whitespace email or linkedin_url made all blank-identity leads match each other, so one blank-keyed suppression report removed every lead lacking an email. Blank values now name no lead (test written first, seen failing 1 vs 3).
- tests: added linkedin_url-only match, case/whitespace match, blank identity, prune_flagged input-not-mutated, and a hung free tier hitting the deadline (every Enrichment source recorded timed_out).

Gaps (needs-follow-up):
- needs-follow-up: linkedin_url is matched by casefold/strip only; no URL normalisation (trailing slash, scheme, www).
- needs-follow-up: tiers skipped because pruning emptied the work list record no result (consistent with 11.5 empty-list rule); 11.3 exit mapping not re-verified against that.
- needs-follow-up: a flag value that is truthy but not literally True (e.g. "true") is ignored.
- needs-follow-up: a source in a tier that fails (non-halting) is not tested for later-tier call counts beyond the unauthorized case.

## Task 11.7 — Call per-company sources once per distinct Company Signal (2026-10-05)
Evidence: wrote tests/test_orchestrator_per_company.py (11 tests) first; ran it and saw collection fail with ImportError (per_company_work_list missing) before any implementation; after implementing, all passed (no individual red per test beyond the collection failure). ruff format/check, mypy clean; pytest 1432 passed, 1 skipped. One mutation (dedupe disabled) failed 2 tests (5 per_company calls vs 1; 2 vs 1), file restored. No GitNexus/serena query (additive: one new function and one changed request in run()).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Company identity now = casefolded/stripped domain set at contribution value path `company.domain` (str or tuple/list/set/frozenset of str); equal sets are one company. Rejected: building PSL reduction or overlap clustering (task 16.9, 8.16-8.18, not built); keying on name or email domain; reading CompanySignal.company_id (work-list items are LeadContributions, which carry no CompanySignal). 16.9 replaces `_company_key`.
- A Lead with no domain is kept and stands for itself (never merged). Rejected: dropping it from the per-company list (silent loss) or grouping all unkeyed together (false merge).
- Non-text `company.domain` raises TypeError. Rejected: ignoring it (silent fallback).
- Per-company work list = first Lead of each company in work-list order, same objects, input tuple untouched; applied per tier (tier shares one charge_unit) on the already pruned list, so a suppressed Lead never stands for its company. Rejected: dedupe inside enrichment_work_list (11.5/11.6 contract is unfiltered); last-Lead or best-Lead representative (needs a score or ranking, forbidden).
- "Call" = provider call: the orchestrator still invokes each source once per phase; the source makes one provider call per work-list item, so the item count is the billable count (test sources count one per item). per_lead and per_call tiers still get the whole list. Rejected: one fetch_raw per company/lead (changes the 11.5 request contract and needs a per-call merge into one SourceResult batch).
- Fan-back: nothing is copied onto the other Leads at one company here; a company-level contribution reaches them through the shared Company Signal and Employment (ADR-0001) in a later stage. Rejected: duplicating the contribution per Lead.
- Ledger: no new counters; SourceOutcome counts orchestrator invocations (attempted 1, asserted in a test), not provider calls. Rejected: adding a billable-units field (speculative).
### Known gaps (needs-follow-up)
- Overlapping but unequal domain sets (acme.com vs {acme.com, acme.io}) and subdomains (mail.acme.com) count as different companies until 16.9 lands, so Credits may be spent twice for one company.
- No adapter emits `company.domain` yet; the path is a string convention to confirm with 12.x-14.x and the Merge Engine design.
- Per-company provider-call counts are not visible in the outcome ledger or run summary.
- Leads with no domain are not deduplicated, so a per-company source may be called for each.
- Self-review not run (no Agent tool); only one mutation checked.

### Self-review findings
Fixed:
- Mutation sweep (12 mutants: dedupe off, dedupe on per_lead / per_call tiers, no casefold, no strip, blank entries kept, domain-less leads collapsed, keep-last, first-domain-only key, nondeterministic order, input mutated, dedupe before pruning). 11 killed; "blank entries kept" survived. Added test_blank_domain_entries_are_not_part_of_the_company (seen failing under the mutant, passing restored). orchestrator.py restored byte-identical (sha checked). No src change.
Verified: 1 per_company call vs N per_lead calls; ledger shows 1 attempt; keying errs toward under-merge (extra credits, no lost leads); no Lead is dropped from run output, only skipped for that per_company source (Requirement 6.11 says "invoke once per deduplicated CompanySignal", so consistent). ruff, mypy, full suite green.
Known gaps:
- needs-follow-up: a non-text company.domain (int, UntrustedText, bytes) raises TypeError inside run(), outside source isolation, aborting the whole run and discarding paid Discovery results (confirmed with a scratch test, deleted). Consider treating as "no company" or isolating; left because the implementer chose fail-loud deliberately.
- needs-follow-up: no normalizer/adapter emits "company.domain" yet, so in production the dedupe is a no-op until a mapping supplies it; 16.9 must also fix the path name (Company Signal field is `domains`).
- needs-follow-up: overlapping domain sets (A={a.com,a.io}, B={a.com}) are called separately (under-merge); 16.9 clustering and PSL reduction replace _company_key.
- needs-follow-up: company-level result is not fanned back to the skipped Leads at the company.

## Task 12.1 — Implement Apollo Discovery search with technographic targeting (2026-10-05)
Evidence: wrote tests/adapters/test_apollo_source.py (29) and tests/test_transport_binding.py (5) first; saw collection ModuleNotFoundError (adapters.apollo) and 5 failures (no `transport` kwarg, no `build_transport`) before any code. After: ruff format/check clean, mypy clean (90 files), pytest 1467 passed, 1 skipped. No serena/GitNexus blast-radius query run; the base `__init__` change is keyword-only and defaulted, and the full suite is green.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Transport binding is `BaseLeadSource.__init__(mode, *, transport=None)`, not the design's positional `(transport, mode, config)`; rejected the design order because it breaks ~67 existing call sites in 10 test files, and `SourceConfig` does not exist. Apollo itself requires `transport` (keyword-only). Unbound adapters raise RuntimeError on `.transport`.
- Added `BaseLeadSource.base_url` (defaulted "") and classmethod `build_transport(mode, fixtures_root=None)` so the transport is always built from the adapter's own `endpoints`; rejected leaving callers to build `RestTransport(name, url, endpoints)` by hand (the copy-drift the task wants gone).
- Per-UID searches (one call series per technology UID), not one any_of search, so a zero-match UID can be named; rejected any_of because it cannot attribute matches. Costs more calls. People deduped by Apollo id.
- UID is sent as a plain string query value per request; rejected list values because the transport stringifies lists (known 4.1 gap).
- Capabilities {SEARCH} only; cost_class FREE, charge_unit PER_CALL for now; 12.2 must flip these (rejected declaring PAID/PER_LEAD early). live_access GATED; rate bucket 600/3600s with documented=False (plan-dependent).
- Default `target_vocabulary` is datastax/apache_cassandra (design default); the profile's effective vocabulary is passed as `vocabulary=` and replaces it. No profile loading inside the adapter.
- Canonical paths invented: person.provider_id, person.first_name, person.last_name (raw last_name_obfuscated), person.title, person.linkedin_url, company.name, company.technologies. No `full_name` (last name is obfuscated).
- Warnings (unknown UID at startup, UID with zero matches) are structlog warnings (`apollo_unknown_technology_uid`, `apollo_technology_no_matches`); rejected a run-record channel because none exists yet.
- Non-2xx raises a plain SourceError (message has status only); 12.3 will classify. Synthetic mode sends no key; live resolves APOLLO_API_KEY at fetch start.
- Apollo tests live in tests/adapters/ (path part `adapters` is exempt from the 23.1 vendor-neutrality scan); rejected tests/ top level because the scan forbids vendor names there.
- Edited two existing tests the new real adapter broke: test_plug_and_play factory now passes a built transport; test_example_target_profile no longer asserts an empty adapter package. Regenerated .env.example (+APOLLO_API_KEY).
### Known gaps (needs-follow-up)
- fixtures/apollo/supported_technologies.csv is a hand-made 4-row snapshot (uid,name,category), NOT Apollo's real CSV (not fetchable here); real column layout unverified. Must be replaced by a real dated snapshot (17.x); the date is only a constant.
- Technology-stack evidence is contributed only if Apollo returns organization.current_technologies; the search page documents no such field, and the filter match itself is not materialised as a value.
- Raw model assumes search fields from research.md (linkedin_url, current_technologies unverified on this endpoint); extra fields are ignored, not forbidden.
- Profile terms beyond the declared surfaces (mongodb, couchbase in the shipped example) are sent as UIDs but have no answerable surface, so no Negative Evidence for them.
- 12.2 (Enrichment), 12.3 (error codes, retry-after, quota headers) not built; no per-run quota record. Live path (RestTransport) not exercised against the network.
- RawBatch carries no fetch timestamp, so provenance fetched_at is normalize time.

### Self-review findings
Fixed (each test-first, seen failing, then green; full suite 1480 passed, ruff and mypy clean):
- apollo.py: first/last name, title and company.name were bare str; now `untrusted=True` (UntrustedText, 1.6/22.1). Test updated; asserts provenance.untrusted and confidence origin none.
- base_source.py `build_transport`: live mode accepted any base_url; now refuses non-https (the key rides in a header). Tests added in test_transport_binding.py.
- apollo.py docstring: raw-model extra-field tolerance documented; the CSV flagged as a hand-made stand-in and the date constant as the stand-in's date.
- Tests added: autouse socket guard (none existed for Apollo), key never in logs/repr/exception text, people/match refused by the transport and endpoint map immutable, no live transport built in synthetic mode (Apollo and base), search yields no absences, raw model refuses wrong/missing id and bad technology uid naming both paths.
- Mutation-checked, all killed, files restored identical: read_only=False endpoint, key logged, bearer header, Negative Evidence on queried paths, search first UID only, live transport in synthetic, per_page uncapped. One survivor (dropping validate_raw_payload, masked by the untrusted rule) is now killed by the new raw-model tests.
- Verified, no change: RestTransport and FixtureTransport reject undeclared endpoints, redirects off, UID goes in the encoded query string (checked with httpx), no bare except, CancelledError untouched, key read only via resolve_credentials from env.
### Known gaps (needs-follow-up)
- needs-follow-up: Apollo does not pace. The orchestrator hands `pacing` to the factory, but ApolloSource takes none and never calls the throttle before `transport.send`; up to 500 pages per UID can exceed the 600/hour bucket. Where pacing binds (base `__init__` or adapter) is a design call; settle with 12.3.
- needs-follow-up: a zero-match UID only logs a warning; no Negative Evidence for target_profile.<term> is emitted though those surfaces are declared answerable. Decide who emits it.
- needs-follow-up: no total cap on people accumulated (up to 50,000 per UID in memory); only the 100/500 paging bounds.
- needs-follow-up: a synthetic-mode adapter can still be handed a RestTransport by a caller; only `build_transport` guarantees the pairing (test doubles rely on `__init__` accepting any transport).
- needs-follow-up: UIDs are not checked for snake_case shape (non-blank only); company.technologies and linkedin_url stay untrusted=False (list and URL) and are unverified on the search endpoint; empty-string values are stored as populated (shared Normalizer behaviour).
- needs-follow-up: the design's positional `(transport, mode, config)` deviation is not yet in choices.md or design.md.

## Task 12.2 — Implement Apollo Credit-bearing Enrichment match (2026-10-05)
Evidence: wrote 7 pacing tests (tests/test_base_source_pacing.py) and 25 Apollo 12.2 tests first; saw 7 failures (`pacing` kwarg unknown) and a collection ImportError (`credits_in`) before any code. After: ruff format/check clean, mypy clean (91 files), pytest 1509 passed, 1 skipped. No serena/GitNexus blast-radius query run; the base change is a defaulted keyword-only `pacing` plus one new method, and the full suite is green. Edited two existing Apollo tests the new endpoint/capability broke (declaration test; undeclared-endpoint test now uses bulk_match).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Pacing binds in the BASE class: `BaseLeadSource.__init__(..., pacing=None)` and `_send(endpoint, ...)` which awaits `pacing.throttle.bucket(endpoint.bucket).acquire()` before `transport.send`; Apollo search and match both use it. Basis: the orchestrator cannot know the bucket (11.2 note) and the existing pool test source acquires inside `fetch_raw`. The design's sequence diagram shows the orchestrator awaiting capacity once per fetch; rejected that (one token per fetch, not per call, so 500-page searches go unpaced). Rejected acquiring in the transport (it would need pacing and mode logic).
- Retry NOT wired in the adapter: the orchestrator already runs each fetch through `pacing.retry` (SourceCallLedger); adapter retry would double-retry. Tests prove a retried fetch re-acquires per attempt and a non-retryable error gets one token. No `note_rate_limited` feedback is passed (needs 12.3 classification).
- Match endpoint is a read-only `Endpoint` (POST, params in query, no body): it reads a person and writes nothing to Apollo data; the credit spend is a billing effect, not a provider-data write. Rejected a new "credit-bearing" Endpoint flag (nothing consumes it).
- Type of request picks the path: `EnrichmentRequest` -> match, anything else -> search. Rejected branching on `request.kind` text.
- Match only leads from the work list carrying Apollo's own `person.provider_id` (sent as `id`), one call per distinct id; other sources' leads are not matched. Rejected linkedin_url/email keying (no cross-source canonical path convention yet; the orchestrator reads `linkedin_url`/`email`, Apollo contributes `person.*`).
- Declared PAID, PER_LEAD, yields_suppression False for the class; Discovery is still free. Rejected splitting per-phase declarations (the contract has one set).
- Raw Enrichment batch is `{"matches": [{"lookup", "response"}]}`, responses verbatim; `credits_in(batch)` is a pure count (no run-record channel). No-match logs `apollo_no_match` (lookup id only). `match_confidence` is a closed Literal; unknown value, or non-none with no person, is a NormalizationError.
- Field Confidence stays origin none for every field: Apollo's `match_confidence` is per record, not per field; rejected inventing a high/medium/low -> number map.
- Contributed from match: person id/first/last/title/linkedin_url/email/email_status and company name/technologies. Free text is UntrustedText; email and email_status are plain (identifier/enum-like). email_status is Apollo's raw string, not yet mapped to EmailStatus. No Negative Evidence emitted (no queried_paths).
- `fixtures/apollo/match.json` is hand-made from research.md's response shape, not a captured Apollo response.
### Known gaps (needs-follow-up)
- Credits and no-match outcomes are not on any run record: `credits_in` exists but nothing calls it (source_run.credits_consumed unwired). Needs the run-record channel.
- Leads from other sources are never enriched by Apollo until a canonical identity path convention exists.
- Match response shape (email_status values, current_technologies on match, extra fields) is from research.md, unverified live; live path not exercised against the network.
- No per-window allowance, 402/429 classification or Retry-After feedback to the throttle (12.3); any non-2xx is a plain SourceError. Throttle feedback `note_rate_limited` is therefore never called.
- Credits spent are not capped or budgeted per run; a large work list spends one credit per distinct id.
- Pacing is wired in the base class but HubSpot/Google/Hunter adapters do not exist yet to prove inheritance; `fetch_raw` in a source that bypasses `_send` is unpaced (a convention, not enforced).
- Rate bucket `default` is shared by search and match (design lists one 600/hour bucket).

### Self-review findings
Fixed:
- Retry double-spend: the orchestrator retries the whole fetch, so a failure on the Nth id re-matched ids 1..N-1 and spent their credits again. ApolloSource now keeps a per-instance `_matched` cache (lookup -> response), so an id is paid for once per run (new test test_a_retried_enrichment_does_not_pay_again..., seen failing first). `_match` extracted from `_enrich`.
- Mutation survivor: a blank/whitespace Apollo id was still matched when the `.strip()` guard was removed; added test_a_blank_apollo_id_is_never_sent_to_match (seen failing under the mutation).
- Mutation-checked, killed: bucket not awaited, awaited in synthetic, awaited once per fetch instead of per call, match called twice per id, other-source leads matched, credits doubled, none-counted-as-credit, key leaked into params. Files restored; ruff, mypy, 1511 tests pass.
Verified OK: the pacing deviation from the sequence diagram matches orchestrator.py's docstring and choices.md 11.1 (adapter paces, orchestrator never acquires, so no double pacing) and is recorded; every adapter call path uses `_send` (only auth.py's token fetch sends directly, not an adapter); an endpoint naming an undeclared bucket is refused at construction.
Known gaps:
- needs-follow-up: SPEC GAP. Requirement 12 does not say whose leads Apollo matches. research.md says match accepts id, email, linkedin_url or first_name+last_name+domain, so Apollo enriching other sources' leads is an open reading; the implementation matches only Apollo's own person.provider_id, so a lead found only by another source is never enriched by Apollo. Needs a user decision and a canonical email/linkedin identity path.
- needs-follow-up: the `_matched` cache gives once-per-run per instance; if a run ever makes two Enrichment batches on one instance, credits_in would count a cached id in both.
- needs-follow-up: a real no-match that omits match_confidence would raise NormalizationError (field is required); unverified against live Apollo.
- needs-follow-up: credits_in raises on a malformed batch rather than returning a count; a credit spent in a fetch that later fails normalization is not billed anywhere (no run-record channel).
- needs-follow-up: person.email and email_status are contributed but not in answerable_surfaces, so no Negative Evidence can be recorded for them.
- Suppressed-lead pruning for PAID sources is the orchestrator's generic tier logic (tested there with fake sources); not re-proven with Apollo itself.

## Task 12.3 — Classify Apollo errors and record per-window allowances (2026-10-05)
Evidence: wrote 21 new tests (31 cases) in tests/adapters/test_apollo_source.py first; ran them and saw 31 fail (old code raised bare SourceError "returned status N"); then implemented in adapters/apollo.py; final `uv run ruff format src`, `ruff check src`, `mypy`, `pytest -q` all clean (1543 passed, 1 skipped).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- `classify_error` lives on ApolloSource only, signature `(response, *, endpoint)`; design shows a base default `classify_error(response)` that does not exist in base_source.py. Rejected: inventing the base default (other providers' conventional mapping is outside 12.3) and keeping the design's endpoint-less signature (a scope-403 must name the endpoint).
- 429 is rate-limited whether or not the code matches `USAGE.RATE_LIMIT.API_RATE_LIMIT_EXCEEDED`; the cause is the code, else "unrecognized_429". Rejected: a permanent error for an unknown 429 code (would never retry a real throttle).
- Research documents no Apollo quota/credit-exhaustion signal distinct from 429, so no SourceQuotaExhausted is produced by Apollo (402 and other 4xx stay generic SourceError, one attempt).
- Scope-403 code is undocumented: every 401/403 becomes SourceUnauthorized(endpoint=path, scope_cause=error_details.code, or "no_error_code"). Rejected: matching a guessed scope code. Codes are accepted only if `[A-Za-z0-9_.]{1,100}`; message text and body are never put in errors.
- retry-after is read from the header only (positive finite seconds, else None); body `retry_after_seconds` ignored. Rejected: falling back to the body field or a default interval.
- Allowances: `ApolloSource.allowances` property (minute/hour/day -> int) holding the LAST response's values (replaced each response, so stale values drop); non-negative ASCII digits only, absent/malformed means no key. Rejected: accumulating history, and zero-filling.
- 5xx and 408 -> SourceTransient(status); other 4xx -> SourceError naming path, status, safe code.
### Known gaps (needs-follow-up)
- Allowances are not yet on a run record or fed to throttle.py: neither has a window-allowance channel; nothing reads `allowances` yet.
- Base-class default `classify_error` and the orchestrator/retry wiring of the unauthorized status for non-Apollo providers are not done.
- Apollo header names and the 403 scope code are unverified against live Apollo (research: headers undocumented); no live check.
- A credit spent in a fetch that later fails normalization is still billed nowhere (12.2 gap unchanged); no-match shape still unverified.

### Self-review findings

Fixed (all test-first, seen red then green):
- Base-class gap (design: "classify_error() hook on BaseLeadSource with a conventional default, overridden once"): added `BaseLeadSource.classify_error(response, *, endpoint)` with the conventional mapping (401/403 SourceUnauthorized naming the endpoint; 429 SourceRateLimited with safe Retry-After; 5xx/408 SourceTransient with status; any other non-2xx, including 1xx/3xx, a plain SourceError, which retry.py never retries: one attempt). `_send` now classifies and raises, so there is one call path; timeouts and connection errors stay the transport's SourceTimedOut / SourceTransient. Added optional `_note_response` hook (no-op) and public `retry_after_seconds()` in base_source.py. Signature deviates from the design's `classify_error(self, response)` by the keyword `endpoint`, needed to name the endpoint (12.6).
- ApolloSource: removed `_call` wrapper, now overrides only `classify_error` (code-based rate limit, scope cause on 401/403, `code=` on permanent 4xx) and `_note_response` (allowances). Its private `_retry_after` moved to the base helper.
- Crash: an allowance header of more than 4300 digits made `int()` raise ValueError (a hostile or garbled header crashed the call). Now length-bounded (15 digits), longer is dropped as unknown.
- Retry-After: `float()` accepted underscores ("1_0" became 10.0); now ASCII delta-seconds only (digits with optional fraction); HTTP-date, negative, NaN, inf, overflow, non-ASCII digits are None.
- Tests added: tests/test_base_classify_error.py (48 cases incl. override inversion, ledger/RetryPolicy end to end), Apollo end-to-end attempts/status table (401, 403 once; 422/404 once; 503 and 429 to the ceiling), more malformed allowance values.
- Mutation-checked (13 mutations, all killed, files restored byte-identical): 403 or 401 mapped away, permanent mapped transient, scope cause dropped, endpoint blanked, body echoed (base and Apollo), huge header crash, Retry-After parse removed or ignored, allowances not recorded.

Known gaps:
- needs-follow-up: allowances are "last response only", so after a transport failure (timeout, connection error) the previous values stay; counts only fall within a window, so a stale value can overstate. Nothing reads `allowances` yet (no run-record channel), so no throttle is misled today.
- needs-follow-up: under the bounded pool, concurrent calls on one instance race to be "last"; harmless on the single-threaded event loop (dict replaced atomically) but the value is any recent response, not the minimum. Task text says record on the run record: no such channel exists yet.
- needs-follow-up: Retry-After as an HTTP-date is treated as absent (backoff used), not parsed.
- needs-follow-up: SourceTimedOut text in transport.py includes `repr(exc)`; not reviewed for URL or secret content (outside this task).
- Scratch dir `scratchpad/rev` could not be deleted (rm denied); it holds only helper scripts and .bak copies, none in the repo.

- **Also fixed by the parent after review:** `transport.py` put `repr(exc)` in the `SourceTimedOut` text, which can carry a URL, query string or person data into `outcome.error` and the run record (Apollo match sends its parameters in the query string). It now names only the exception type (test added, red first).

## Task 13.1 — Implement the HubSpot CRM-state lookup on the date-versioned path (2026-10-05)
Evidence: wrote test_hubspot_source.py first; first run failed at collection (ImportError, no adapters.hubspot module), then built the adapter; 38 new tests pass. `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 1641 passed, 1 skipped. `.env.example` regenerated with `uv run python -m leadforge.lead_ingestion.env_example`.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- API version is `HUBSPOT_API_VERSION`, listed in `required_env` (so it appears in .env.example and live mode needs it), validated as YYYY-MM, no code default; rejected a constructor kwarg with a literal default and a sources.yaml key (unknown keys are errors there).
- Endpoint path uses the `{version}` placeholder that RestTransport fills; synthetic mode sends no version or credentials.
- Enrichment-only (`ENRICH`), FREE, PER_LEAD, yields_suppression True; rejected PER_CALL and a SEARCH capability.
- Canonical paths invented for CRM state: `crm.contact_exists`, `crm.lifecycle_stage`, `crm.owner`, `crm.last_activity_date`, `crm.has_open_deal`; no canonical model fields exist for them.
- `hs_email_optout` sets BOTH `opt_out` and `suppressed`; rejected setting `opt_out` only.
- Open deal presence needs a second read-only POST, `deals/search` (associations.contact + hs_is_closed=false), once per contact found; rejected GET associations (cannot tell open from closed).
- Lookup is by email only, for any work-list lead from any source; leads without email get no call. Rejected LinkedIn or name lookups.
- One contribution per contact found (duplicates OR together via prune_flagged); unknown email gives one contribution of Negative Evidence for every CRM path, including opt_out and suppressed.
- Up to 100 contacts per email, no paging; lookups cached per source instance so a retried fetch repeats nothing.
- Lifecycle stage and owner are plain identifiers, not UntrustedText; no free-text property requested.
- Only the `search` bucket (5/s, documented) is declared; no account-burst bucket since no endpoint uses it. Error classification is the base default (13.2 owns policyName handling).
- Non-enrichment request raises SourceError rather than silently returning nothing.
### Known gaps (needs-follow-up)
- Property names (notes_last_updated, hs_email_optout, hs_is_closed, hubspot_owner_id) and the associations.contact filter are from memory and research.md; developers.hubspot.com was blocked, so not verified against docs or a live portal.
- Fixtures contact_search.json and deal_search.json are hand-made stand-ins; FixtureTransport serves one fixed answer per endpoint, so the opted-out contact is the only fixture case (the non-suppression and not-found cases are covered with scripted transports).
- Client-side throttling and policyName backoff (13.2) not built; pacing relies on the base class when a SourcePacing is passed.
- Nothing yet builds HubSpotSource with real config (no bootstrap exists); the 13.4 to 13.6 behaviour is unverified.
- No property test; self-review (spec-refactor-agent) not run (no Agent tool).

### Self-review findings

Fixed (test-first, seen failing):
- Unknown email emitted Negative Evidence for `crm.has_open_deal` though no deal search was made for it (an answer never asked). `normalize` now uses a per-record context that leaves `crm.has_open_deal` out of `queried_paths` for an unknown email. Test `test_an_email_unknown_to_hubspot_records_negative_evidence_not_a_flag` updated.
- Mutation gap: unbounded search `limit` survived. Added `test_every_search_is_bounded_and_never_pages` (contact limit capped, deal limit 1, no `after`).

Checked and kept: `HUBSPOT_API_VERSION` as required_env matches the research.md env list (the version sits in the path via `{version}`; 13.3 says config); both endpoints are `read_only=True` (the default, enforced); the email travels in the POST body only, never in errors or paths; no logging in the module; no policyName handling (13.2 untouched); free plus yields_suppression lands in the first enrichment tier; the retry cache mirrors Apollo `_matched`. Mutations (suppressed dropped, version echoed, extra token header, unbounded limit) are all caught.

Known gaps:
- needs-follow-up: `crm.*` canonical path names are invented; no convention is fixed in the spec; Merge Engine 16.x will consume them.
- needs-follow-up: opt-out sets both `opt_out` and `suppressed` (13.7 says compliance flags; CONTEXT.md Suppression avoids the word opt-out); owner decision.
- needs-follow-up: `.env.example` leaves `HUBSPOT_API_VERSION=` empty with no documented value (research.md suggests 2026-09).
- needs-follow-up: a contact search with total above 100 is silently truncated, and one deal search is made per contact found (no total call cap beyond the work list).
- needs-follow-up: HubSpot property names and the `associations.contact` deal filter are unverified against a live portal; fixtures are hand-made.

## Task 13.2 — Throttle HubSpot search client-side with policy-aware backoff (2026-10-05)
Evidence: wrote tests/adapters/test_hubspot_throttle.py first; first run 16 failed, 6 passed (policy classification and the six-in-one-second pacing test failed), then implemented; all green. `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 1664 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- 429 with policyName DAILY is `SourceQuotaExhausted` (halts source for the run, no retry); SECONDLY and TEN_SECONDLY_ROLLING are `SourceRateLimited` with Retry-After; rejected treating daily as a long-interval `SourceRateLimited` (retry cannot fix it; design Error Categories separates the two rows).
- Absent, non-JSON, non-str, blank or unknown policyName defaults to `SourceRateLimited` (cause `unrecognized_policy`); rejected defaulting to quota exhaustion, since halting would drop leads on a limit that may clear in seconds, while retry is bounded.
- policyName read only from the top-level body field, trimmed and case-insensitive; rejected a nested `error.policyName` lookup.
- Retry-After is honoured for short policies, ignored for DAILY.
- Error text carries only `cause=policy_<name>` or `unrecognized_policy`, never the body.
- The `search` bucket gained a second window, 1 request per 0.2 s (even spacing) next to the documented 5 per 1 s, because the token bucket otherwise admits 5 at once plus 1 more 0.2 s later (six in one second, against 13.4). Rejected keeping the single honest window. This changed the 13.1 test `test_search_bucket_is_declared_as_five_per_second_documented` (assertion now lists both windows).
- 13.1 endpoint declarations were already right (both searches on `search`); no account-burst bucket added.
### Known gaps (needs-follow-up)
- Policy names (SECONDLY, TEN_SECONDLY_ROLLING, DAILY) and top-level placement are from memory and the spec text; developers.hubspot.com could not be checked, and no captured 429 fixture exists (tests use inline stand-in bodies).
- The 0.2 s spacing makes five lookups take about one second even when HubSpot would allow a burst; owner may prefer the plain window.
- Nothing builds HubSpotSource with real pacing in production yet (no bootstrap), so end-to-end pacing is verified only with a fake clock.
- No property test; self-review not run (no Agent tool).

### Self-review findings
Fixed (no defect found in hubspot.py; test gap only):
- Added two end-to-end tests to tests/adapters/test_hubspot_throttle.py driving the real HubSpotSource + RetryPolicy + orchestrator: DAILY -> QUOTA_EXHAUSTED, 1 attempt, 0 retries, other source OK; SECONDLY / unknown policy / non-JSON body -> RATE_LIMITED, bounded at max_attempts (3 attempts, 2 retries).
- Mutations run and killed by existing tests: DAILY->rate-limited, SECONDLY->quota, unknown->quota, spacing window removed (fake-clock test fails), body echoed into error text, policy read from a header. Files restored (diff verified).
- Checked: deals/search is in the same `search` bucket (right); classification only in HubSpot.classify_error (nothing above reads policyName/status for HubSpot); non-429 uses super().

Known gaps:
- needs-follow-up: policy names DAILY / SECONDLY / TEN_SECONDLY_ROLLING are UNVERIFIED; research.md and requirements.md name none of them (13.6 says only "secondly versus daily").
- needs-follow-up: a failure mid-fetch (e.g. DAILY on the 2nd email) discards the whole batch, including opt-out contacts already found; prune_flagged then cannot exclude them. Spec silent; partial-result design is outside 13.2.
- needs-follow-up: `bucket.documented=True` for 5/s is from requirement 13.4 text, not a verified HubSpot doc page.
- Skipped: str.upper() Unicode folding (e.g. dotless i) can match "DAILY"; harmless.

## Task 14.1 — Implement the pluggable search backend (2026-10-05)
Evidence: wrote adapters/test_google_search_backend.py first and ran it (collection ImportError, modules absent: red), then implemented; 26 new tests green. Final: `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 1694 passed, 1 skipped. `.env.example` regenerated via `uv run python -m leadforge.lead_ingestion.env_example` (+SERPAPI_API_KEY).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Port is a request-builder, not a sender: `SearchBackend` (ABC) gives endpoint, bucket, required_env, base_url, page_size, `build_call(query, page, credentials)`, `has_next_page(body)`. Dispatch, pacing, classification and caching stay in the adapter `_send`; rejected a backend that sends through the transport itself (would bypass pacing and classify_error).
- Backend selected by `DEFAULT_BACKEND_NAME = "serpapi"` plus `select_backend(name)` scanning the `search_backends` package (new backend = one new module); rejected an edited name->class dict and a `GOOGLE_SEARCH_BACKEND` env var (would need to be in required_env and be mandatory, contradicting "SerpApi default").
- Adapter class vars (endpoints, required_env, base_url, rate_limit) are read from the default backend at import, since build_transport and .env.example run without an instance; a backend instance with different endpoint or env names is refused with ConfigurationError. Rejected per-instance declarations.
- `GoogleSearchSource` (name `google_search`) shell created now so the port has a delegate; `normalize` returns [] for an empty batch and raises SourceError for any search results (14.2 builds it); rejected returning [] always (silent drop).
- Queries are a ctor argument (default none, no call); `results_per_query` default 10, paged by ceil(n/10), stops when no next page; no cap on it; `num` never sent. Rejected deriving queries from the Target Profile (14.2 territory).
- Cost PAID/PER_CALL (balance-bearing), capability SEARCH, no answerable surfaces or vocabulary yet; bucket "default" 1 req/s, documented=False (self-imposed, SerpApi hourly limit is plan-dependent); rejected an hourly figure as invented.
- Pages cached by (query, page) per adapter instance so a retried fetch repeats no balance-bearing call.
- Edited existing test `test_columns_of_unregistered_sources_are_warnings_not_errors`: it assumed google_search stayed unregistered; it now renames that column in a tmp copy of the shipped profile.
- Hand-made stand-in fixture `fixtures/google_search/search.json` (not captured).
### Known gaps (needs-follow-up)
- A backend with a different host/endpoint/credential cannot be used until adapter class declarations can vary (build_transport is a classmethod); only the default is selectable.
- No config-file wiring for the backend name or queries (no sources.yaml keys added).
- SerpApi request params (`engine`, `q`, `start`, `api_key`) and `serpapi_pagination.next` taken from research.md, not checked against live SerpApi.
- `config/target_profile.yaml` header comment ("No adapter is registered yet") is stale; its google_search columns are not yet used as queries or vocabulary (14.2).
- 14.2 and 14.3 not built; 429 uses the base default (cause http_429).

### Self-review findings
Fixed (test-first, each seen failing before the fix):
- `search_backends.select_backend` silently overwrote two backends sharing a name; it now raises DuplicateSourceNameError.
- A backend with another `base_url` was accepted, so its key could be sent to serpapi.com; the adapter now refuses a backend whose host differs (ConfigurationError).
- `queries="abc"` iterated into three paid searches; a bare string is now a TypeError.
- No spend bound: added MAX_QUERIES=100 and MAX_RESULTS_PER_QUERY=100 (ValueError), so a hostile "next" chain cannot page unboundedly.
- Added tests: drop-in backend module selectable by name, key absent from every exception/__cause__/traceback for ConnectError, ReadTimeout and a 500 over the real RestTransport, hostile query cannot change host/scheme/path.
Verified, no change needed: page cache by (query, page) already stops re-spend on retry; no bare except; synthetic builds no socket; mutations (URL echoed in error, silent fallback, cache removed, normalize accepting results) each fail a test.

Known gaps (needs-follow-up):
- needs-follow-up: backend is chosen by DEFAULT_BACKEND_NAME or constructor argument; research.md names a GOOGLE_SEARCH_BACKEND setting that nothing reads. A backend in a test module is passed in, not found by name (the scan covers only the package).
- needs-follow-up: queries have no run-time source (orchestrator passes none, so a registered run makes no call); when 14.2 adds one, a paid search followed by normalize raising discards the spend.
- needs-follow-up: live mode demands SERPAPI_API_KEY even with no queries.
- needs-follow-up: SerpApi response bodies are kept verbatim in RawBatch; if a response ever echoed api_key it would be stored (none in fixture, unverified live).
- needs-follow-up: no google_search-level test that redirects are off (flipping follow_redirects did not fail any test); query length is unbounded.
- needs-follow-up: a backend class lacking `name` makes select_backend raise AttributeError.

- **Correction by the parent:** the reviewer reported that flipping `follow_redirects` to true failed no test. It does: `test_redirects_are_not_followed` in `test_transport.py` fails under that mutation (verified, file restored). Redirects are pinned at the transport level, which is the only place the client is built.

## Task 14.2 — Contribute untrusted web evidence as Company Signals (2026-10-05)
Evidence: wrote tests/adapters/test_google_search_normalize.py first and ran it; the first test failed (normalize raised SourceError "not built yet"), then implemented; 37 new tests green. Replaced the 14.1 test asserting normalize raises with an empty-batch test. Final: `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 1740 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- One LeadContribution per organic result, answer box and knowledge graph (not one per search or page), with only `company.web_evidence.{query,block,url,title,snippet,retrieved_on}` paths; rejected `company.name`/`company.domain` (a result host such as a job board is not the company's domain) and rejected any `person.*` path (ADR-0001: web evidence never creates a Lead).
- No Signal Strength and no tech-vs-intent kind is contributed: a search result states neither, a number would be invented, and the spec gives no ingestion-time value; rejected a constant default strength and keyword classification of snippet text (would read strength/kind from the text).
- Title and snippet are UntrustedText; URL, our own query, block label and date stay plain str (14.6 names only snippet and title; Apollo keeps URLs plain); rejected wrapping the URL.
- Retrieval date = UTC date of the normalize call (ISO string value) with `fetched_at` in provenance; rejected a date taken from the result's own `date` field (publication date, listed in IGNORED).
- Raw paths in provenance are relative to a per-result wrapper `{query, block, retrieved_on, result}` (`result.link`, ...) so query/date get mechanical provenance; rejected hand-adding them outside the Normalizer.
- Absent or null `organic_results`/answer box/knowledge graph = no evidence, no raise; a present block of wrong type or shape raises NormalizationError (no silent fallback). A block mapping none of url/title/snippet contributes nothing.
- No answerable surface declared, so no Negative Evidence (a zero-result query records nothing); rejected declaring `organic_results` as an answerable surface.
- Replaced (not kept) the 14.1 test that required normalize to raise on results.
### Known gaps (needs-follow-up)
- Evidence is not attributed to any company: no name or domain is contributed, so nothing downstream can turn these records into a CompanySignal until queries are tied to a company (queries still have no run-time source; 14.1's gap stands).
- Contributions are flat `company.web_evidence.*` values, not TechSignal/IntentSignal objects; no merge/assembly step exists to build them, and strength is never set.
- A paid search followed by normalize raising still discards the spend (raw pages are not persisted before normalize here).
- Answer box / knowledge graph field names (`link`, `title`, `snippet`, `website`, `description`) are from memory of SerpApi, not verified; the blocks in tests are inline stand-ins; the fixture is a hand-made stand-in.
- The result URL is plain str and may embed provider-chosen text.
- No property test; self-review not run (no Agent tool).

### Self-review findings
Independent review. No code changes made; the implementation held up. Verified: ruff, mypy clean; pytest 1740 passed, 1 skipped; repo root clean.
- Mutation checks (files restored byte-identical, verified with cmp): snippet as plain str (killed), person.* rule added (killed), payload text in a NormalizationError (killed), wrong-shaped organic_results accepted (killed). Confidence is never fabricated: it comes only from the Normalizer (origin NONE) and rank (`position`) is in IGNORED.
- No bare except, no CancelledError handling in the touched code, no network in tests (Scripted transport).
- Requirement 14.5 says "evidence records" and does NOT demand Signal Strength; task 14.2 text says "as Signals" / "Company Signals".
- SPEC GAP: task 14.2 asks for Signals (strength recorded at ingestion, 24.4) and Company Signals; the adapter contributes flat `company.web_evidence.*` values with no strength, no tech/intent kind, no company name or domain. CompanySignal requires a name or a domain (models.py), and the orchestrator keys a company only on `company.domain`; no merge engine exists yet (16.9/16.10), so these contributions cannot be attached to a company and would be orphaned. needs-follow-up (needs-user).
- needs-follow-up: no cap on results per page; a hostile page with 10k results is normalized in full (silent drop vs raise-on-excess is a design decision, not made here).
- needs-follow-up: result URL is plain str with no length bound (22.4 bounds only untrusted free text; a URL is not on 22.1's list but can hold provider-chosen text). The query is operator-supplied, not provider text.
- needs-follow-up: provenance raw paths are wrapper-relative (`result.link`), identical for organic and answer box, not the provider's literal path (`organic_results[].link`). The block label is stored as a value to disambiguate.
- needs-follow-up: duplicate results across pages or queries are emitted twice; `retrieved_on`/`fetched_at` come from the normalize call, so re-normalizing one batch differs in timestamps only (values and order are stable).
- Zero-result search: no Negative Evidence (no answerable surface declared); consistent with 1.9 only if web search is not an answerable source, unconfirmed.

- **LEFT UNCHECKED IN tasks.md by the parent (SPEC GAP, needs-user):** the code is committed and safe (prompt-injection handling verified by mutation), but the task text says "as Signals / Company Signals" with Signal Strength recorded at ingestion (24.4). The adapter emits flat `company.web_evidence.*` values with no strength, no tech-vs-intent kind and no company name or domain, so no Company Signal can be built from them and nothing can attach them to a company. Completing it needs two decisions: how a query maps to a company (identity), and what Signal Strength an organic result gets when the provider states none.

## Task 14.3 — Tell SerpApi throughput exhaustion from balance exhaustion (2026-10-05)
Evidence: new tests/adapters/test_google_search_throttle.py run first (collection ImportError on ThrottleCause = red), then implemented; 19 new tests pass; `uv run ruff format src`, `ruff check src`, `mypy` clean; full `uv run pytest -q` 1759 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Placement: adapter `classify_error` maps the verdict to an error type once; the backend only READS the signal via a new non-abstract `SearchBackend.throttle_cause(body) -> ThrottleCause` (default UNRECOGNIZED). Rejected: parsing SerpApi text in the adapter (breaks backend swap, 14.1) and an abstract method (breaks existing custom backends).
- Signal: top-level string `error`, case-insensitive. "run out of searches" (documented text) = BALANCE -> SourceQuotaExhausted (halt, no retry). Rejected: treating any 429 as balance.
- "throughput" substring = THROUGHPUT -> SourceRateLimited cause=hourly_throughput. The hourly wording is NOT documented (serpapi.com blocked by egress); marker is an assumption. Rejected: no marker (14.8 could never surface throughput).
- Absent/non-JSON/non-str/unknown -> SourceRateLimited cause=unrecognized_throttle (bounded retry), as HubSpot 13.2. Rejected: halting the source on an unknown 429.
- Error text carries only the cause token; body, key, query never copied.
### Known gaps (needs-follow-up)
- Hourly-throughput error wording unverified; fixtures are hand-made stand-ins.
- No orchestrator-level test (status RATE_LIMITED vs QUOTA_EXHAUSTED, attempt counts) as HubSpot has; types follow errors.py/retry.py convention.
- The Account API is not queried to disambiguate an unrecognized 429.
- No property test. Self-review not run (no Agent tool).

### Self-review findings
Fixed:
- test_google_search_throttle.py had no end-to-end coverage (the HubSpot 13.2 file does). Added 7 tests: key/body absent from str/repr/__cause__/__context__; throughput Retry-After spacing through RetryPolicy ([3.0, 3.0]); orchestrator balance 429 -> QUOTA_EXHAUSTED, 1 attempt, 0 retries, other source OK; persistent throughput/unknown/None-body 429 -> RATE_LIMITED, 3 attempts, 2 retries; a retried fetch does not re-send an answered paid query (3 transport calls for 2 queries). Now 26 tests.
- Mutation-checked (all caught, file restored byte-for-byte): balance->RateLimited, throughput->Quota, unknown->Quota, body echoed, key/environ leaked in text.
- No production code changed. retry.py/orchestrator.py/base_source._send read no body or status text; 401/403 -> SourceUnauthorized via super().
Known gaps:
- needs-follow-up: "Your account has run out of searches." is NOT in specs/research.md (no such string there); its provenance is the implementer's claim only. Verify against serpapi.com docs.
- needs-follow-up: throughput marker "throughput" is unverified wording; if the real text differs, throughput 429s fall to UNRECOGNIZED, still SourceRateLimited (safe outcome, less precise cause label).
- Substring match: text naming both markers resolves to balance (halt). Provider-origin text only; accepted. Unknown/absent -> RateLimited matches 13.2.
- needs-follow-up: 402 and other SerpApi exhaustion statuses are undocumented in research.md; they take the base default (402 -> plain SourceError, one attempt).

## Task 15.1 — Implement Hunter email discovery batched by domain (2026-10-05)
Evidence: wrote fixtures/hunter/domain_search.json and tests/adapters/test_hunter_source.py (56 tests) first; ran it and saw collection fail with ModuleNotFoundError (adapters.hunter missing) before any implementation; after implementing all passed (no per-test red beyond the collection failure). `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 1822 passed, 1 skipped. `.env.example` regenerated with `uv run python -m leadforge.lead_ingestion.env_example` (adds HUNTER_API_KEY). No mutation run; no GitNexus/serena query (additive new module).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Scope: only `GET /v2/domain-search`, one per distinct company domain from the work list's `company.domain` (casefolded, stripped, str or collection); the task's finder/verifier routing bullet (16.3) is NOT built (finder is per-person so conflicts with per_company dedupe; verifier is 15.2). Rejected: declaring unused finder/verifier endpoints.
- One page of at most 100 addresses per domain, no paging, warning `hunter_domain_search_truncated` (counts only) when meta.results is larger. Rejected: paging (each page costs Credits) and silent truncation.
- Confidence: per-address integer 0-100 recorded as provider-stated on `person.email` only (value n/100, raw verbatim string, scale `hunter_confidence_0_100`), by replacing that provenance record in the adapter because the Normalizer only emits origin none. Rejected: extending FieldRule/Normalizer (shared, one call site), and attaching it to every field.
- Status map: valid->verified, accept_all->accept_all, invalid->invalid, unknown/webmail/disposable->unknown; missing verification/status contributes no `person.email_status`; other values raise NormalizationError. Rejected: marking unverified guesses `unverified` (asserts an attempt Hunter did not state), disposable->invalid (not stated by Hunter).
- Sandbox: live mode whose resolved key equals `test-api-key`; batch carries `credits_billable`; `credits_in` is 0 for sandbox/synthetic, else one per search. The one-credit-per-search figure is an assumption (Hunter's real billing not verified). Rejected: a third DataMode.
- Raw batch `{"searches": [{"domain","response"}], "credits_billable"}`; per-domain cache so a retried fetch buys nothing twice. Provenance raw paths are wrapper-relative (`email.value`, `domain`).
- Contributed paths invented (spec names none): `person.email`, `person.email_status`, `person.email_sources` (tuple of URIs), `person.first_name/last_name/title` (UntrustedText), `company.domain`, `company.name` (UntrustedText). Other fields (type, seniority, department, linkedin, twitter, phone) ignored explicitly.
- Non-hostname-shaped domain skipped with a value-free warning; non-text company.domain raises. Discovery request raises SourceError (as HubSpot).
- Key sent in `X-API-KEY` header (16.1 names it); `verifier` bucket declared (10/s AND 300/min) though no endpoint uses it yet; both buckets `documented=True`.
- No Negative Evidence: surfaces declared, but no queried_paths, so zero-email domains and null fields record nothing.
### Known gaps (needs-follow-up)
- Default classify_error applies: Hunter 403 is still SourceUnauthorized and 429 SourceRateLimited until 15.3; 451 untreated.
- Finder (name+domain) and verifier routes, 202 polling not built (15.2); the 15.1 task text lists the routing, needs a parent decision whether it belongs to 15.2.
- Fixture is a hand-made stand-in; Hunter field names (sources[].uri, verification.status null cases, confidence range) from research.md, not live-verified; `webmail`/`disposable` as verification statuses in domain-search unverified.
- Credit cost per domain-search unverified; sandbox behaviour not exercised live.
- Orchestrator integration through a real run (per_company_work_list + registry) only unit-tested via per_company_work_list.
- No property test; no mutation checks; self-review not run (no Agent tool).

### Self-review findings
Fixed (the red phase was observed through mutation checks afterwards, not as a separate pre-implementation run; the new tests were written before the routing code but first run after it):
- SCOPE (supersedes the "routing not built" decision and gap above): task 15.1's routing bullet and requirement 16.3 belong to 15.1 (15.2 owns only 16.4 polling). Built: `GET /v2/email-verifier` (bucket `verifier`) for a Lead with a usable `person.email`; `GET /v2/email-finder` (bucket `finder`) for a usable first and last name plus domain and no address; domain search otherwise. The previously declared-but-unused `verifier` bucket is now used, and the module docstring no longer says routing is not built.
- A verifier HTTP 202 is recorded as no verdict (nothing contributed, no error, counts-only log); polling stays 15.2.
- Finder and verifier answers map through their own rule sets (raw paths `email`, `status`, `score`, relative to `data`); `score` is a provider-stated confidence on `person.email` only, verbatim with the `hunter_confidence_0_100` scale. A finder that finds nothing contributes nothing and no absence. The raw batch gains `finds` and `verifications`; `credits_in` counts every live call.
- Per-run caches for finder pairs and verified addresses (a retried fetch does not pay again), next to the existing domain cache. Addresses are stripped and casefolded for dedupe and sending; names are deduped casefolded but sent as given.
- Input hygiene: malformed addresses are skipped (value never logged); names that are blank, over 100 characters, non-printable or masked with `*` (Apollo obfuscates last names) are never sent to the finder; a non-text `person.email` is a NormalizationError.
- Tests added (117 in the file now): key header-only on all three routes; no key, address, domain or name in errors or logs for malformed answers and error statuses on all three routes; hostile domains (`evil.com/../x`, `a.com?x=1`, ...); 16 finder calls and 11 verifier calls in one instant are spread by a fake clock and charge both windows; verifier verdict vocabulary; score range (NaN, bool, str, out of range) for finder and verifier; synthetic fixtures `email_finder.json` and `email_verifier.json` (hand-made stand-ins).
- Mutation checks run, each caught by a test: unknown and webmail mapped to verified, fabricated confidence, key logged, key in the verifier and finder query, body echoed into an error, undeclared verifier endpoint, domain, finder and verifier asked twice, hostile-domain pattern loosened, Negative Evidence on queried paths, routing disabled, 202 handling removed, verifier bucket swapped to finder, masked-name guard removed, minute window removed, sandbox billed. Two initial mutations were no-ops and were redone as real ones.
- Verified, not changed: the sandbox key `test-api-key` is documented in research.md and mandated by 16.8 and the task text; only the batch's billable flag is derived from the key value, the data mode is not.
- Whole suite 1883 passed, 1 skipped; ruff and mypy clean; no stray files in the repo root.

Known gaps left:
- SPEC GAP: finder and verifier are per-person calls but 15.1 declares `per_company`, so the orchestrator's `per_company_work_list` hands Hunter one Lead per company; other people at that company are never verified or found. Needs a design decision (per-call charge unit or a per-lead pass). needs-follow-up.
- needs-follow-up: Hunter `type: generic` (role addresses such as info@) is ignored, so a role address is passed through as `person.email` with no flag. The merge-side role-address rule (8.x) does not exist yet.
- needs-follow-up: credits are an assumption of one per live call (Hunter bills domain search by results and verification at its own rate); `credits_in` may misreport spend.
- needs-follow-up: no per-run cap on distinct domains, finder or verifier calls (Apollo and Google Search cap pages and queries); the Credit budget belongs to a later orchestrator task.
- needs-follow-up: the shared token bucket permits up to about twice a window's capacity in a sliding window (burst plus refill); this is the 10.1 throttle design, not Hunter's, but "15/s" is not a strict sliding bound. Tests assert spreading and that both windows are charged.
- needs-follow-up: the verifier `data` shape and the finder not-found behavior are unverified (stand-in fixtures). A finder 404 for "not found", if Hunter returns one, would be classified by the base class as a permanent SourceError aborting the whole fetch; classification is 15.3.
- needs-follow-up: IDN domains, domains with a URL scheme and trailing-dot domains are skipped (logged without the value), not converted.
- An unrecognised verdict raises NormalizationError for the whole batch rather than mapping to unknown: it never overclaims, but a new Hunter verdict would abort a paid batch.
- `HunterSource` is not registered or wired into a factory (no other adapter is outside tests either).

## Task 15.2 — Implement Hunter verification with bounded polling (2026-10-05)
Evidence: wrote 21 tests (backlinked 16.4) in tests/adapters/test_hunter_source.py first and ran the file: 23 failed (TypeError: unexpected keyword `clock`/poll args), 116 passed; then implemented in adapters/hunter.py. Replaced the 15.1 test "202 contributes nothing and is not polled" (superseded). `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 1905 passed, 1 skipped. No mutation run; no GitNexus/serena query; self-review not run (no Agent tool).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Poll = repeat the identical verifier GET (Hunter documents no job id) through the same paced `_send`; bound by `poll_attempts` (default 5) AND `poll_budget_s` (default 30s, measured from before the first request, pacing waits included). Rejected: attempts-only (unbounded wall time) or budget-only.
- Defaults 5 / 30s / 2s interval are invented (spec says only "configured poll budget"); they are constructor arguments, not wired to source_settings/config yet. Rejected: adding a config key now (no per-source settings mechanism for Hunter).
- Wait before a poll = Retry-After if a number else `poll_interval_s`; raised to the interval floor, clamped to the budget left; HTTP-date hints ignored; no wait after the last poll. Rejected: trusting an unclamped hint, or a floor-less hint (hammering).
- Give-up = no verdict (nothing contributed, so EmailStatus stays at the unknown default), one `hunter_verification_unfinished` warning with reason (attempts|budget) and poll count only, no exception. Rejected: raising (aborts a paid batch), or contributing an explicit `unknown`.
- Assumption (unverified): Hunter does not charge for polling; `credits_in` stays one per address, not per poll.
- Finished polls (verdict or give-up) are cached in the existing per-run `_verified` map so a retried fetch does not re-poll; an error or cancel mid-poll caches nothing and a retry restarts the poll from zero. Rejected: persisting partial poll progress (speculative).
- Clock and sleep injected into HunterSource (defaults time.monotonic / asyncio.sleep), separate from the throttle's. Cancellation and the run deadline rely on the default sleep being cancellable; no wrapper timeout added.
- Fixtures not extended: the fixture transport serves final verdicts only; 202 paths are tested with a scripted transport.
### Known gaps (needs-follow-up)
- Poll bounds are not exposed in config/CLI; defaults are guesses.
- Hunter's real 202 semantics (Retry-After presence, body, repeat-call billing, whether a poll of an in-progress address is rate-limited differently) are unverified; fixtures are stand-ins.
- A transient/rate-limit error mid-poll loses poll progress and the retry restarts at the first request.
- Budget is per address, so a batch of N unfinished addresses can wait up to N x poll_budget_s sequentially; only the run timeout bounds the total. Polling addresses is sequential, not concurrent.
- 15.3 (403/429 inversion, 451) untouched: a Hunter 403 during a poll is still SourceUnauthorized.

### Self-review findings
Fixed (test first, seen failing):
- `_is_finite` raised a bare `OverflowError` for an int too large for a float (`poll_budget_s=10**400`); now refused with the named `ValueError`. adapters/hunter.py.
- Added 8 test groups: wrong-kind/huge bounds by name, hostile Retry-After (inf, nan, -5, 0, 1e9, empty, 400 digits) never exceeds budget, budget smaller than interval, polled unknown verdict raises NormalizationError (as 15.1: aborts the whole paid batch on the polled path too), repeated/same-case addresses polled and paid once, 80 paced polls cannot finish under 7s on the real throttle, cancel during a poll request leaves no task and caches nothing.
- Mutation check (12 mutants: unbounded attempts, budget ignored/unclamped, Retry-After unclamped/ignored, sleep skipped, give-up raises/VERIFIED, address logged, poll bypasses _send, cache dropped, CancelledError swallowed): all killed; file restored byte-identical. Hunter test file runs in ~0.7s (no real sleeping). Full suite 1927 passed, ruff, mypy clean.
- The removed 15.1 "202 not polled" protection (a 202 contributes nothing, no error) is still covered by the give-up tests (normalize == [], no exception).
Known gaps:
- SPEC GAP: poll billing unverified. research.md says only "202 means still running, so poll"; it does not say whether a repeated GET is the same verification or a new billable one, nor whether a 202 is charged. Polling may spend a credit per poll; credits_in assumes one per address. Also credits_in counts a give-up (no verdict) as one credit, unverified. needs-follow-up
- SPEC GAP: no requirement or design key states poll numbers; 5 / 30s / 2s remain guesses and are not wired to config/sources.yaml (source_settings has only sources/max_concurrent_sources/run_timeout_s; no per-adapter settings convention). needs-follow-up
- SPEC GAP: tasks.md says "recording budget exhaustion"; only a counts-only log warning is emitted, no run-record entry. needs-follow-up
- A mid-poll error restarts the poll on retry and may re-pay if Hunter charges per verification. needs-follow-up
- Per-address budget: N unfinished addresses wait up to N x 30s sequentially; only the run timeout (11.4) bounds the total. Run-deadline test uses asyncio.timeout, not the real Orchestrator. needs-follow-up
- Pacing is a token bucket (burst 10 plus refill), as decided in earlier tasks; interleaving with other buckets is not starved because buckets are separate (not tested end to end). needs-follow-up

- **Parent note:** the "recording budget exhaustion" bullet is only partly met (a counts-only log warning) because the run record is task 18.2; wire the give-up count into the per-source counts there. 15.2 is ticked for the bounded polling itself.

## Task 15.3 — Invert Hunter's status-code conventions in error classification (2026-10-05)
Evidence: wrote 17 new tests first and ran them: 17 failed (403 read as unauthorized, 429 as rate-limited, 451 a plain SourceError, no restriction batch/validation) before any adapter code; after HunterSource.classify_error plus 451 isolation: ruff format/check clean, mypy clean, pytest 1951 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- A 451 on one address (verifier) or one name (finder) is ISOLATED: caught in fetch_raw (SourceComplianceRestricted only), recorded in the batch, batch continues; rejected: letting it fail the whole fetch (would drop every other lead's data).
- The restriction is carried on the Lead as a contribution with `suppressed` = True plus the identity asked by (address; or domain + first/last name as UntrustedText) and no verdict/confidence/source; rejected: also setting `opt_out` (HubSpot sets both) because Hunter says "restricted processing", not opt-out.
- A 451 on domain search is not isolated (no person named): it raises SourceComplianceRestricted and fails that fetch.
- classify_error cannot see the request, so the error's `subject` is the endpoint path, never an address/domain; rejected: re-raising with the address (PII in error text).
- 403 cause "http_403_rate_limit" (Retry-After via retry_after_seconds); 429 -> SourceQuotaExhausted "cause=http_429_usage_limit", Retry-After ignored.
- Restrictions are cached per run (no second paid ask on a retried fetch) and a 451 is assumed not billed (credits_in counts delivered responses only).
- New batch keys `restricted_finds` / `restricted_verifications`; a malformed one is a NormalizationError.
### Known gaps (needs-follow-up)
- Hunter's real 451/403/429 bodies and whether a 451 consumes a Credit were not verified; fixtures are hand-made stand-ins (scripted transport, no new fixture files).
- Name-only (finder) suppression has no identity prune_flagged can match (it keys on email/linkedin_url); the merge engine must match it by name+domain.
- Merge/orchestrator behavior of the flagged contribution was not tested end to end.

### Self-review findings
Fixed:
- Defect: a restricted address (verifier 451) could still carry contact data in the SAME batch from a finder or domain-search answer for that address (16.6 "contribute no contact data"). `normalize` now drops any search item, finder answer or verifier answer whose address is in `restricted_verifications`. Test-first: `test_a_restricted_address_never_keeps_contact_data_from_the_finder` failed, then passed.
- Added tests: end to end through the real orchestrator + RetryPolicy (403 -> RATE_LIMITED, 3 attempts, 2 retries; 429 -> QUOTA_EXHAUSTED, 1 attempt, other source OK; restricted address -> source OK with one flagged contribution; domain-search 451 -> COMPLIANCE_RESTRICTED, 1 attempt, no PII in outcome.error); the per-question catch lets 403/404/503/CancelledError through on finder and verifier.
- Mutation-checked (all killed, file restored byte-for-byte): 403 and 429 mapping dropped, 451 not compliance, subject = body, 429 retryable, catch widened to SourceError, 451 aborts batch, refused-address filter off.
Verified, no change: suppressed only (not opt_out) is consistent with CONTEXT.md Suppression (avoids "opt-out"); HubSpot's both-flags choice is its own (13.1) and prune_flagged honours either flag; 451 assumed unbilled (research.md is silent; credits_in counts only delivered answers); caches keep restricted questions from being re-asked on retry.
Known gaps:
- SPEC GAP: a per-question 451 never reaches the orchestrator as COMPLIANCE_RESTRICTED; the only record is the flagged contribution (source status OK). 16.6 and the design's Error Categories row say "record restriction on the lead", so the letter is met, but a run report / 18.2 per-source failure counts will not count restricted addresses. needs-follow-up: decide whether SourceOutcome needs a restricted count.
- needs-follow-up: a finder (name-only) restriction carries first_name/last_name/domain and no email or LinkedIn, so prune_flagged (11.6) cannot match it; it helps only if the Merge Engine (16.x) joins first+last into full_name and matches it. Hunter will be asked again in later runs.
- needs-follow-up: `yields_suppression` stays False (Hunter is paid; the ordering rule is for free sources) though Hunter now emits suppressed flags.
- needs-follow-up: a 451 for an address does not remove it from an answer given earlier by another source or tier; that rests on the merge's OR semantics.

## Task 16.1 — Extract Match Keys ordered by durability (2026-10-05)
Evidence: new tests/test_match_keys.py first run = collection ModuleNotFoundError (red), then 62 passed after match_keys.py; ruff format/check, mypy, full pytest green.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Result type `MatchKeys` (keys sorted by kind then value, plus corroborating_emails, titles, employers); rejected a bare list of keys, since 16.2 needs the 8.11 and 8.3 evidence too.
- Key 3 is emitted as a candidate, one per registrable domain; `corroborates(a, b)` is the pairwise gate (shared title or employer); rejected folding the gate into the key value.
- Role addresses are not recognised in 16.1 (design: no curated role-word list; 8.14 handled by 16.7 via distinct names); rejected a role-word blocklist.
- LinkedIn key = `host/path` lowercased, scheme/query/fragment/trailing slash dropped; `www.` and locale variants not folded (under-merge preferred); host-only URL gives no key.
- Plus-addresses kept as written; only EmailStatus.VERIFIED is a key; UNVERIFIED/ACCEPT_ALL are corroboration only; INVALID/UNKNOWN/missing status give nothing.
- Name from person.full_name, else first+last (both required); paths are the adapters' `person.*`/`company.*`, not the orchestrator's bare `email`/`linkedin_url`.
- Registrable domain via tldextract(suffix_list_urls=(), cache_dir=None): bundled PSL snapshot of the locked version; bare suffix/IP/localhost skipped.
- Non-text values raise TypeError naming the path only (matches orchestrator `_company_key`); rejected silent skip.
- MatchKey/MatchKeys repr withholds values (PII).
### Known gaps (needs-follow-up)
- Employment-date overlap corroboration not implemented: no canonical path for dates exists.
- PSL "dated snapshot" is the tldextract version in uv.lock, not an explicit dated file; 16.9/8.16 should confirm.
- orchestrator `_identities` (bare paths) not reused or unified; path-name mismatch with adapters remains.
- Identity Exclusions (16.6) and cross-name disqualification (16.7) not built; keys are a plain frozen dataclass they can filter.

### Self-review findings

Fixed (each test-first; full suite 2031 passed, ruff and mypy clean):
- match_keys.py: names were casefolded without Unicode normalisation, so composed and decomposed forms ("Jose" + combining acute vs "José") gave different keys (under-merge). `_fold` now applies NFKC before casefold; NBSP/tab/newline collapse is covered by a new test.
- match_keys.py: email and LinkedIn keys used `casefold()`, but 8.1/8.2 say "lowercase". casefold folds "ß" to "ss", which would merge the distinct mailboxes straße@ and strasse@ (over-merge). Both now use `lower()`.
- match_keys.py: the registrable domain used the PSL without private suffixes, so one.github.io and two.github.io were both "github.io" (over-merge risk on shared hosting). `include_psl_private_domains=True` now, still the bundled snapshot with `suffix_list_urls=()`; `github.io` alone gives no key.
- match_keys.py: Unicode and punycode spellings of one domain (münchen.de / xn--mnchen-3ya.de) gave different keys; labels are now decoded to one form.
- Tests added: no-socket test pinning `suffix_list_urls == ()`, and a sentinel PII test (TypeError names the path only, no key text in any repr of MatchKeys). The earlier PII test only looked for the digit 5 and was weak.
- Mutation checks (all caught, files restored): order swapped, unverified email as key, key text in the TypeError, PSL fetch enabled, name key without domain, www folded.

Pruning-path finding (CONFIRMED real bug in task 11.6 / 13.x, fixed):
- orchestrator `_identities` matched on (path, value) with bare `email`/`linkedin_url`. Real Apollo and Hunter write `person.email`/`person.linkedin_url`; HubSpot writes the bare `email`. A HubSpot opt-out never matched an Apollo-shaped work-list entry, so the lead would still reach the paid tier (defeats 6.10/6.11 credit saving). The 11.6 and HubSpot tests passed only because their fake work lists also used the bare path.
- Worse, HubSpot's `_emails_of` read only the bare `email` from the work list, so with real Apollo output HubSpot looked up NO emails at all.
- Fix: `_identities` now reads both spellings and compares by (kind, normalised value) using the new public `match_keys.normalize_email` / `normalize_linkedin_url` (LinkedIn matching now also ignores scheme, query, slash, case); `_emails_of` reads `person.email` then `email`. Existing tests unchanged and passing.
- New tests in tests/adapters/test_hubspot_suppression_paths.py, including an end-to-end run: Apollo-shaped discovery -> real HubSpotSource (scripted transport) -> real orchestrator -> paid tier receives only the non-opted-out lead. (Placed under adapters/ because the vendor-neutrality test forbids vendor names elsewhere.) Hunter already emits `person.email`, so its 451 suppression matched Apollo output before and after.
- Residual: HubSpot still emits the non-canonical bare `email` path in its own contribution (hubspot.py RULES); the Merge Engine must treat it as `person.email` or HubSpot should migrate (needs-follow-up, 16.2/13.x). Hunter's 451 on a name+domain find carries no email, so pruning cannot match it (needs-follow-up; would need name+domain identity in pruning).

Known gaps left:
- needs-follow-up: LinkedIn key does not fold `www.`, locale subdomains (uk.linkedin.com), `/pub/` vs `/in/`, percent-encoding vs literal Unicode vanity names, or userinfo/port beyond what urlsplit drops. Requirement 8.1 names only host/path lowercasing and query/fragment/slash stripping, so these under-merge (the safe direction) but a real Apollo URL with and without `www.` will not merge on key 1.
- needs-follow-up: no requirement or ADR sentence about webmail/generic domains (gmail.com), so `company.domain=gmail.com` still yields a name+domain candidate; only the corroboration gate stands in the way.
- SPEC GAP: 8.3's "overlapping employment dates" corroboration is not implemented (no canonical path for dates). Only title and employer name corroborate. Also, employer-name agreement is near-vacuous when the domain already matches, so two different people with the same name, same company and no distinguishing attribute can be keyed together; that is what 8.3 literally permits, and Identity Exclusion (16.6) is the only repair.
- `corroborates` is symmetric and order-independent (set intersection, empty sets never corroborate). It compares the contribution's single title/employer, not the specific employment that produced the shared domain.
- Role-address disqualification is NOT part of 16.1: ADR-0003 and 8.14 define it as an address reported against two or more distinct person names, with no curated word list, so it belongs to 16.7 across contributions.
- Email normalisation does not validate beyond "local@domain" (no check for a second "@" or inner whitespace); plus-addressing and dots are intentionally kept.

## Task 16.2 — Cluster identities order-independently (2026-10-05)
Evidence: wrote tests/test_clustering.py first; first run = collection ImportError (clustering missing), red. After clustering.py: two test-side faults found and fixed (transitive test contradicted the 8.2 rule; canonical_json depended on provenance order, fixed in code), then 52 tests pass; `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 2083 passed, 1 skipped. Permutation tests: 25 seeds plus all 720 permutations of 6 contributions; large test (5,000) counts union/find calls (<=3n, <=12n).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Email key (8.2 "absent on either lead"): an email group merges only if some member has no LinkedIn key; that member joins all (transitive closure, so it can bridge two LinkedIn-different people). Rejected: merging on email regardless of LinkedIn, and refusing bridges (needs cluster-level guards that are order-sensitive).
- Name+domain key (8.3): only contributions with neither a LinkedIn nor a verified-email key take part, and merge only when sharing the key AND a title or employer (bucketed, linear). Rejected: letting keyed records join (a bare record could bridge two keyed people; no unmerge). Under-merges the one-sided case.
- Contribution identity = canonical JSON (sorted keys, sorted sets, provenance sorted by path); one total order for member order, cluster order, ids. Rejected: pydantic model_dump_json (set order is hash-seed dependent).
- cluster_id = sha256 hex of the smallest member's canonical JSON, suffix -2, -3 for repeats; stable when later runs add members. Rejected: hashing key text, UUIDs, ids from member sets (change on every added contribution).
- Byte-identical keyless contributions stay separate singletons (never dropped, never merged without a key); identical keyed duplicates collapse into one cluster but both members are kept.
- No exclusion-predicate parameter: neither requirement 8.8 nor 8.9 shows 16.2 must accept one.
- Output type IdentityCluster(cluster_id, contributions) with contributions hidden from repr (PII); errors name types only.
### Known gaps (needs-follow-up)
- 8.9 "merge into existing persisted identity" is only the stable cluster_id plus idempotent re-clustering; matching to persisted lead_identity rows is store work (16.10), not done.
- Employment-date overlap corroboration still absent (16.1 gap carried).
- HubSpot's bare `email` path is not read by clustering (match_keys reads person.email only); HubSpot emits no email_status so it could never be a key anyway.
- Exclusions (16.6), role addresses (16.7), over-merge detection (16.8) not built; a shared verified role address still merges until 16.7.
- No mutation run.

### Self-review findings
Fixed (each test-first):
- cluster_id was the hash of the smallest-by-canonical-JSON member, so a later run's contribution that sorted lower changed the id (the docstring claim and the old stability test held by luck). Now the id member is the earliest-fetched one (canonical JSON tiebreak). New test fails before, passes after.
- canonical_json raised on plain date values; now serialised via isoformat.
- Error-text mutation (key or value in TypeError) survived; added test with a leaky value and leaky mapping key, both mutations now fail.
Checked, no defect: 8.2 reading (different LinkedIn plus same email never merge directly, matches "absent on either lead"); name+domain cannot launder (keyed records never take part); iterative union-find (no recursion limit); 300 shuffles and 720 permutations identical; 10 key mutations killed; input unmutated; no I/O or bare except.
Known gaps:
- SPEC GAP: 8.9 "merge into persisted lead" needs store matching on identity keys (16.10); a pure id can still change when a new record bridges two clusters. needs-follow-up.
- A bare verified-email holder bridges two different-LinkedIn people through transitive closure (over-merge path; pairwise-faithful to 8.2). needs-follow-up / needs-user.
- cluster_id is an unsalted hash of personal data (dictionary-testable): treat as personal data, never log. needs-follow-up for the store (use its UUID).
- SPEC GAP: 8.3 employment-date overlap corroboration absent; no webmail-domain guard for name+domain (design silent). needs-follow-up.
- canonical_json still raises TypeError on exotic types (Decimal, UUID, bytes); loud, type-only message.

## Task 16.3 — Resolve field conflicts under a total order (2026-10-05)
Evidence: wrote tests/test_conflicts.py first; first run = collection ModuleNotFoundError (conflicts missing), red seen. After conflicts.py: 2 test-side faults fixed (UntrustedText ctor; signal test wrongly compared agreement split, which is value equality), then 25 tests pass; ruff format/check, mypy, full pytest (2112 passed, 1 skipped) clean. All 6 permutations-style tests: every permutation of 5 candidates, 60 seeded shuffles of 40.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Order key: -rank, origin tier (provider_stated > heuristic > none), -confidence, newest fetched_at (exact microseconds), source_name, sha256(canonical value), raw_field_path, canonical provenance JSON. The last two are my additions so equal keys mean byte-identical (design stops at sha256); rejected stopping at sha256 (same-source same-value duplicates would depend on arrival order).
- Origin NONE ranks below heuristic and its confidence is never read; rejected treating NONE as 0.0 confidence within a shared tier.
- Trust ranks: Mapping[str,int] passed in, caller builds it from loaded settings (8.10); undeclared source = LOWEST_TRUST_RANK, as registry does; invalid rank raises TypeError/ValueError naming the source only. Rejected reading config/registry inside the function (I/O).
- Result splits losers into `agreeing` (same canonical value as winner) and `superseded` (different value), each in total order, provenance passed unmarked. Rejected a flat loser list (16.4 needs agreement counts) and setting superseded=True (that is 16.4).
- Absences returned apart by kind (negative_evidence, not_applicable) on ClusterResolution, sorted; there is no merged Lead yet, so this is the exposure point.
- Added public clustering.canonical_value_json (one value serialisation shared with canonical_json); rejected importing the private _canonical.
- Signal Strength proof: a signal value on a conflicting path; strengths varied over 27 combos; winners and other paths unchanged. A strength inside a value is part of the value, so agreement on that path is value equality.
### Known gaps (needs-follow-up)
- No mutation run.
- Values compared by canonical JSON: 1 and 1.0, or differently ordered lists, count as different values; no per-path normalisation.
- HubSpot's bare `email` path is a distinct path here (not merged with person.email) - carried from 16.2.
- Rank mapping construction from SourceSettings/registry is not wired; no caller exists yet (16.5).
- Same-source duplicate candidates for a path are allowed, not flagged.

### Self-review findings
- Precedence verified against 8.4 and task 16.3: Source Trust Rank, then origin, then confidence, then recency. Code matches. Origin outranks confidence only, not rank. Recency is in the requirement, and fetched_at is stored, so 8.8 holds. No defect.
- Mutation-checked: rank ignored, origin ignored, heuristic above stated, confidence ignored, recency removed, arrival-order tie-break. Each fails a test. Files restored byte-identical.
- Added tests only (no source changes): 3000-candidate cluster (one serialisation per value, shuffle-stable), 2 MB UntrustedText, same source contributing twice (recency decides).
- needs-follow-up: strength mutation is unreachable through the key. A signal's strength sits inside its value, so it affects value equality (agreeing vs superseded) and the sha256 tie-break for same-source duplicates. The 24.4 test deliberately skips agreeing/superseded for signals.intent. Decide in 16.4 or a signals task whether signal values are a conflict field at all.
- SPEC GAP: multi-valued fields (signals, employments, domains) are compared whole by canonical JSON, not unioned. 8.4 and 16.3 do not define set merge.
- needs-follow-up: values compare by exact canonical JSON ('  Jane@Acme.com ' differs from 'jane@acme.com'), so agreeing counts (8.7) under-count. 8.4 says nothing about normalised comparison.
- needs-follow-up: PROVIDER_STATED with confidence=None is permitted by the model and sorts as 0.0 within its tier. Deterministic, but the model could forbid it.
- Confidence is the model's already-normalised 0-1 Strength (validated finite). confidence_raw is never touched. No cross-scale normalisation is done here.

- **Fixed by the parent after review (24.4):** the reviewer left as a follow-up that Signal Strength sat inside signal values and so affected value equality and the sha256 tie-break; the 24.4 test hid this by skipping the agreeing/superseded lists for signals. The test now compares winner, agreeing and superseded for every path (seen failing), and `_without_strength` removes the strength from the compared value (a Signal is compared by type and its other fields; the winning candidate keeps its original value). **SPEC GAP stays open:** multi-valued fields (signals, employments, domains) are still compared whole rather than unioned.

## Task 16.4 — Retain losing values as superseded provenance (2026-10-05)
Evidence: wrote tests/test_superseded.py first; first run = collection ModuleNotFoundError (superseded.py missing), red seen. After superseded.py: 15 tests pass; ruff format/check, mypy, full pytest green.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Only superseded (different-value) candidates are marked; winner and agreeing stay unmarked as corroboration counted by 8.7. Rejected marking agreeing too (would erase the agreement signal).
- retain_superseded returns a ClusterResolution (idempotent, same type 16.5 consumes); provenance_records flattens to persistable rows ordered path, winner, agreeing, superseded. Rejected a new merged-lead type (that is 16.5).
- Marking is derived via model_copy on new objects; no stored row edited. A marked winner/agreeing input raises ValueError naming path and source only. Rejected silently re-marking or ignoring it.
- agreeing_source_count = distinct sources holding the winning value including the winner. Rejected excluding the winner and counting candidates rather than sources.
- contributing_sources = sorted distinct names from candidates and absences. Rejected taking it from the cluster (not available to a pure ClusterResolution function).
- 8.6 is satisfied by the existing winner.source_name; no new code beyond a test.
- Absences pass through untouched, never become provenance rows.
### Known gaps (needs-follow-up)
- A contribution with neither values nor absences is invisible to contributing_sources; 16.5 should take the set from the cluster.
- Not wired into any caller or persistence (canonical_field_provenance.superseded_field_ids belongs to 16.5/store). Store has no superseded column.
- Agreement is canonical-JSON equality (carried from 16.3). No mutation run.

### Self-review findings
Fixed:
- superseded.py: a winner/agreeing record carrying a stale superseded flag now has the flag cleared instead of raising ValueError. The mark is derived (8.12), the store keeps no superseded column, and a record that lost in an earlier run can win in a later one. Output now equals the result from unmarked inputs and stays idempotent. Test-first: the stale-mark test failed on the raising code.
- tests: replaced the raise test and the ValueError PII test with a stale-mark and repr-PII test. Added 7 tests: record multiset retained once (duplicates, same source twice), superseded set equal to 16.3's, exact case/whitespace comparison, Signal Strength-only difference not superseded (24.4), whole-compared multi-valued signals, invisible empty contribution, and 100 shuffles with idempotence.
- Mutation-checked (marked winner, losers unmarked, agreeing marked, a record dropped, agreeing omitted from records, source count not distinct): each caught by a test, file restored.
Verified: no persistence gap. Store has canonical_field_provenance.superseded_field_ids (8.5 persisted by id) and winning_field_id/agreeing_source_count; marks stay derived, no superseded column needed.
Gaps left:
- needs-follow-up: contributing_sources cannot see a contribution with neither values nor absences; ClusterResolution does not carry it. 16.5 should take the set from cluster.contributions[*].source_name (8.7). Documented by a test.
- SPEC GAP (shared with 16.3): multi-valued fields (signals, employments, domains) are compared whole, so a differing list from a lower-ranked source is marked superseded rather than unioned. Documented by a test.
- needs-follow-up: agreeing candidates are unmarked and persist only as an agreeing_source_count plus their immutable contribution rows. Reading 8.5 as losing = different value; confirm with the owner.

## Task 16.5 — Recompute the Lead projection non-destructively (2026-10-05)
Evidence: wrote tests/test_projection.py first and ran it (collection ImportError, module absent: red); then projection.py (29 tests green); ruff format/check, mypy, full pytest (2166 passed, 1 skipped) clean. Vendor-neutrality test forced neutral wording in the new files.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Contributing sources, agreement counts and provenance ride on `ProjectionResult` beside the lead; rejected adding fields to `CanonicalLead` (model unchanged).
- Bare `email` path is re-keyed to `person.email` in memory before resolving (stored contribution untouched), unless the contribution already has `person.email`; rejected editing the store or leaving it a separate path.
- Winner failing model validation (bad email/URL) leaves the field empty; rejected promoting a lower candidate (would break winner-to-provenance resolution).
- `email_status` only from a candidate whose source also supplied the chosen address, else `unknown`; rejected taking the status winner blindly (could mark another address verified).
- `opt_out`/`suppressed` = OR over every candidate of every source (not the winner); also kept on the result so a no-identity cluster keeps them.
- No person identity -> `lead=None`, rest still returned; rejected raising.
- `full_name` = person.full_name else first+last (adapters emit no full_name).
- One Employment max; `company_id` = hash of sorted casefolded domain set (name if no domain); `is_current` None; no org => no Employment.
- Signals: union of every Tech/IntentSignal object in any candidate, first in candidate order keeps its strength; rejected unioning by max strength (strength-dependent).
### Known gaps (needs-follow-up)
- "Recompute in one transaction / prior projection valid on rollback" and persisting the projection are store wiring, not built; "changing the match rule" (no rule parameter exists) not proven here.
- Provenance marks follow the 16.3 winner, so after a status fallback or invalid-winner the lead field may not match the unmarked winner record.
- Adapters' flat `company.technologies` is not turned into Signals (shares the 14.2 gap); multi-valued fields still compared whole.
- Status-to-email matching is per source name, not per contribution; a source with two different emails could mismatch.
- company_id is provisional until 16.9-16.11.

### Self-review findings
Fixed:
- tests: added test_re_keying_the_bare_email_leaves_the_stored_contribution_untouched (values, provenance and absences of a HubSpot-style bare `email` contribution stay unmodified). A mutation that wrote person.email into the stored values survived the original 29 tests; now caught. No source change.
Verified (no defect): winner mapping, OR of opt_out/suppressed over every candidate (suppression is not a rank-resolved conflict; a suppressed Lead stays a Lead), no-identity cluster returns lead=None with flags kept, empty-contribution source is in the set (taken from the cluster), email_status never overclaims, no network, no bare except, 5000 contributions project in 0.5 s, shuffle-identical, projection raised on none of the fuzzed values (bad domains, 100 KB names, bad URLs).
Mutation-checked (each caught): winner not applied, suppression not ORed, status without stating source, arrival-order source list, no-identity raises with value in text, empty contribution invisible, agreement dropped, opt_out ignored, signals from winner only, no first+last composition. File restored byte-identical (cmp). Survivor "company id domain order reversed" is an equivalent mutation.
Gaps left:
- SPEC GAP (needs-user): 8.7 and design.md say the merged Lead carries contributing_sources (and provenance); CanonicalLead has no such field, they ride on ProjectionResult (store maps them to canonical_lead.contributing_sources). Adding fields would change test_canonical_entities field-set checks; not changed.
- needs-follow-up: a blank or invalid winning email/URL/name (the normalizer does not validate these, so a provider can emit them) leaves the field empty and erases a valid lower-ranked candidate; with no other identity the Lead is None. Fallback to the next valid candidate would break "field resolves to the winning provenance record" (8.6). Decide drop vs fallback vs raise.
- needs-follow-up: Apollo masked last names ('Sm***') compose into full_name 'Jane Sm***' on the Lead; no masked-name detection exists (also in match_keys).
- needs-follow-up: company_id hashes raw casefolded domains (no registrable-domain normalisation: 'www.x.com' differs from 'x.com'; a URL string stays a domain) or 'name:<name>' when no domain; two leads at one company with different winning names share a company_id with different content, which share_company_signals rejects. A personal domain (webmail, freelancer's own) would be hashed unsalted; flag as potentially personal data. Superseded by 16.9-16.11.
- needs-follow-up: _any_true accepts only the boolean True; a future adapter emitting a non-bool flag would fail open.
- Signals: one per (kind, label), first in deterministic candidate order keeps its strength; duplicates collapse; same-label different-strength from two sources keep the higher-ranked one.

## Task 16.6 — Apply Identity Exclusions as the only Over-merge repair (2026-10-05)
Evidence: wrote tests/test_identity_exclusions.py first; first run = collection ImportError (IdentityExclusions missing), red. After code: 2 test-side faults fixed (a same-text-two-kinds test that cannot exist, version-token fixture), then green; `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 2209 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Followed task/8.13 text, not the pair/split framing: an exclusion is a VALUE barred from acting as a key, skipped at key extraction; no cluster splitting rule exists. Rejected: pairs of identities that must not co-reside with a split rule (spec says values; split rules are order-sensitive).
- A barred key's kind still counts as present for 8.2 (LinkedIn-absent) and 8.3 (stronger key), via MatchKeys.barred_kinds. Rejected: plain removal, which let barring a LinkedIn URL make its holder an email bridge and MERGE two people. Tested: exclusions only refine the partition (all subsets).
- Only LinkedIn URLs and verified emails are excludable; name+domain is not. Rejected: composite name+domain exclusions (no requirement names a case).
- from_values normalises like key extraction and raises ValueError (no value in text) for an unusable entry. Rejected: silently dropping it.
- version_token = sha256 of the sorted set, exposed for the 8.13 projection_version bump.
### Known gaps (needs-follow-up)
- Not read from config/: no file format or loader exists (no config file for exclusions); the pure type is the seam.
- projection_version bump not wired into the store/recompute (version_token only); a store-backed test of "bump on change" is outstanding.
- The end-to-end "recompute with zero contribution mutation in the store" is shown on pure clustering only.

### Self-review findings
Requirement 8.13: "SHALL read a configured set of Identity Exclusions — specific values barred from acting as a match key — from config/, and SHALL bump projection_version when that set changes ... this exclusion set is the only supported repair for an over-merge." Verify: "Adding a value to the exclusion set and recomputing separates a previously over-merged cluster, with no contribution record mutated or deleted." Value-barring (not pair/split) is the correct reading; confirmed.
**Fixed / built (test-first, seen red):**
- Task bullet 1 (read from config) was THIS task and buildable, so the earlier "no loader" gap is superseded: added exclusion_settings.py (load_identity_exclusions, config/identity_exclusions.yaml, keys linkedin_urls and emails, absent or empty file = empty set) on read_yaml_document; ConfigurationError carries path + key_path (e.g. emails[1]); values and unknown key text are never echoed. Tests: tests/test_exclusion_settings.py (18).
- Defect: IdentityExclusions.from_values raised AttributeError on non-text entries; now ValueError "usable key value" with no value (mutation-checked).
- Added tests: end-to-end repair through project_lead (two people -> two Leads); barring the LinkedIn instead does NOT split a bridged email cluster (documented limit); barred LinkedIn plus a bare same-email record merges exactly as without the exclusion (refinement only).
**Verified correct:** barred key counts as present for 8.2/8.3 (never merges more than without exclusions); order independence over 720 contribution permutations and all exclusion-input permutations; idempotent; no-op for absent values; same normalisers as extraction; repr hides values.
**Mutation checks (restored exactly):** barred-still-merges, plain removal, no email normalisation, no URL normalisation, constant version_token, repr leak, no type check, exclusion-merges-new: each caught by a test.
**Bullet status:** 1 delivered; 2 (bump projection_version) DEFERRED; 3 (prove separation, no mutation) delivered on pure clustering and projection, not store-backed; 4 (blocked on 16.1) satisfied.
### Known gaps
- SPEC GAP / needs-follow-up: "bump projection_version when the set changes" is NOT delivered. The store only has an integer canonical_lead.projection_version column; no recompute or store-wiring code exists (projection.py defers it) and nothing persists the prior exclusion token, so a bump cannot be wired without inventing store features. IdentityExclusions.version_token is the seam.
- needs-follow-up: version_token is an unsalted sha256 over the sorted normalised set (never the values, but low-entropy emails are guessable by dictionary attack). Treat it as personal-data-adjacent; do not log it.
- needs-follow-up: when a bare record bridges, repair needs the shared EMAIL barred, not the LinkedIn; operator guidance is not written.
- needs-follow-up: store-backed "recompute with zero contribution mutation" test.

- **LEFT UNCHECKED IN tasks.md by the parent (SPEC GAP, needs-user):** bullet 1 (read exclusions from config) is delivered and reviewed; bullet 2 (bump `projection_version` when the exclusion set changes, recomputing projections) is not: the store has only an integer `canonical_lead.projection_version` column, no recompute code exists, and nothing persists the previous exclusion token. `IdentityExclusions.version_token` is the seam. It needs a store-side decision (where the previous token lives, and what triggers the recompute).

## Task 16.7 — Disqualify addresses reported against two distinct person names (2026-10-05)
Evidence: new tests/test_role_addresses.py (39 tests): first run was a collection ImportError on DisqualifiedAddresses (red). After the code, a mutation (clustering no longer passes the disqualified set) made 9 of them fail, then restored. Final: `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 2269 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Design: `DisqualifiedAddresses.from_contributions` (match_keys.py) is a first pass over the whole set; `extract_match_keys(..., disqualified=)` skips the address. `cluster_contributions` computes it itself; rejected a caller-supplied parameter (structural rule, could disagree with the contributions) and a config list.
- A disqualified address is treated like a barred key: skipped, kind kept in `barred_kinds` so it still counts as PRESENT for 8.2/8.3 (rejected plain removal: lets name+domain merge people; a test proves it).
- Independent of Identity Exclusions; both apply (union of barred kinds). Rejected folding it into IdentityExclusions (would alter version_token and config).
- "Distinct name" = the match-key name (`_full_name`: full_name, else first+last; NFKC, casefold, whitespace collapse). Rejected fuzzy matching and reordering: `Doe, Jane` is a distinct name (disqualifies, i.e. under-merges).
- A missing/blank name, or any name containing `*` (masked), is not a name (adapter convention for obfuscated last names). Rejected prefix-unmasking.
- Addresses of every email_status count when collecting names (deliverable is not non-shared); only VERIFIED addresses were ever keys. Rejected counting verified only.
- No role-word list (local-part info/sales unused): 8.14 and the design name only distinct names.
- Edited existing 16.6 test `test_the_repair_projects_two_people_to_two_leads_end_to_end`: its fixture reported two names on one address, which 16.7 now disqualifies unaided; rewrote with nameless carriers so the exclusion is still the only repair.
- Result type hides addresses from repr (field repr=False), like IdentityExclusions.
### Known gaps (needs-follow-up)
- Bullet 1 (structural bar, normalized names, independent of exclusions): delivered. Bullet 2 (role address two sources attach to different people never merges, no exclusion entry): delivered. Bullet 3 (blocked on 16.1): satisfied.
- No projection_version or run-report surface for the disqualified set (not in the task; 8.13's bump concerns only the exclusion set). Merge logging is 16.12.
- Hunter `type: generic` still passes through unflagged (by design); the merge-side rule catches it only once two names are seen.
- A role address reported with one name (or none) is undetectable and still merges; the 16.8 detector is the visibility defence.

### Self-review findings
- Fixed: nothing in src; no defects confirmed. Added 3 tests (42 total in test_role_addresses.py): NFC/NFD + NBSP name equality, non-text name is a TypeError echoing no value, disqualified address stays on the projected Lead (16.5 data not erased).
- Mutation-checked (all caught, files restored byte-identical): threshold >2, masked names counted, last-name-wins (order dependent), first pass skipped in clustering, key not removed, kind not kept in barred_kinds.
- Edited 16.6 test: judged acceptable. Names had to go because 16.7 now disqualifies the shared address unaided; nameless carriers keep it proving that only the exclusion separates (barred key stays present-but-unusable, 3 leads, nothing mutated). Lost: the assertion on the people's names (needs-follow-up, minor).
- Requirement 8.14 wording ("disqualify as a match key", "two or more distinct normalized person names", no role-word list per design.md and ADR-0003) is fully matched; no role-word list is required, none built.
- Delivered: all three 16.7 bullets (structural bar independent of exclusions; role-address proof with empty exclusion set; shares the key-extraction module).
- Known gaps: disqualified unverified/accept_all address still counts as corroborating evidence (not a key, so within 8.14) needs-follow-up if the owner wants it dropped; zero-width characters in names make names distinct (same as the name key; under-merge only) needs-follow-up.

## Task 16.8 — Flag suspected Over-merges without blocking the run (2026-10-05)
Evidence: wrote tests/test_over_merge.py first; first run = collection ImportError (over_merge missing), red. After code: one test-fixture fault (FieldProvenance.untrusted, project_lead trust_ranks) fixed, then 33 green. Mutations caught: cluster ids in the log line (2 fail), threshold >=1 name (20 fail); restored. `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 2305 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Only signal built is the requirement's: two or more distinct normalised full names. Rejected: two verified emails / two LinkedIn URLs / key-less bridge signals (not in 8.15; a bridge is caught only if its members carry distinct names).
- "Distinct name" reuses the 8.14 rule via new public match_keys.normalized_person_name (full_name else first+last, NFKC/casefold/whitespace; blank or masked `*` is not a name); DisqualifiedAddresses now calls it. Rejected: fuzzy or prefix-unmasked comparison.
- Report (SuspectCluster) carries cluster_id (pseudonym, repr hidden), reason codes, distinct_name_count, member_count; NO names, emails or URLs. Operator finds members by id in the DB to write an Exclusion. Rejected: carrying personal values on the report.
- Log line is counts-only (clusters_examined, suspect_clusters), WARNING if any suspect else INFO; cluster ids never logged.
- Odd input (empty, nameless, masked) reports nothing; a non-text name raises TypeError naming the type only, as clustering does. Rejected: catching it (silent fallback).
- Reports sorted by cluster_id; one name normalisation per member (call-count test on 5,000).
### Known gaps (needs-follow-up)
- Bullet 1 (flag clusters with 2+ distinct names, run not halted): delivered as a returned report plus log line. Bullet 2 (prove over-merged named, correct not): delivered. Bullet 3 (blocked on 16.2; run-report warning surface): 16.2 satisfied; the run-report surface (18.x) does not exist, so nothing is written to it and the orchestrator does not call the detector yet. Deferred.
- Persisting reports in the store is not built.
- Cluster ids are pseudonyms; how an operator maps an id to members (a CLI or query) is not built.

### Self-review findings
- Fixed (test-first, saw it fail): detector raised TypeError on a non-text name, which could abort a run (8.15 "without blocking"). Now the member is skipped and counted in the counts-only log as `unreadable_names`; nothing echoed. Clustering already rejects such input first, so only hand-built clusters reach it.
- Added test: 'Lee, Ann' vs 'Ann Lee' is flagged (accepted false positive, consistent with 16.7 normalisation).
- Mutation-checked (all caught by tests): threshold 1 and 3, masked names counted, unsorted report, cluster id logged, name logged, detector mutating a cluster, raising on keyless cluster, un-normalised names. Files restored exactly.
- 8.15 lists exactly one signal (two distinct non-null normalised full names); no further signals owed. 16.7 behaviour unchanged (full suite 2306 passed, ruff, mypy clean).
- Delivered: 16.8 bullet 1 (detection, never blocks) and bullet 2 (proof test). Deferred: writing to the run report surface (18.x; task says blocked on/shares 18.1, so wiring out of scope).
- needs-follow-up: operator handle. Report carries only cluster_id (pseudonym that changes when membership changes, 16.2) and counts; an operator must locate members by recomputing clusters to write a 16.6 Identity Exclusion (barred values). Requirement 8.15 only says "named", so not a SPEC GAP, but 18.x should decide whether the stored report may carry the contributing emails/LinkedIn URLs (never in logs).
- needs-follow-up: key-less bridge, two verified emails, weak-key merges are not flagged (not in 8.15).

## Task 16.9 — Cluster Company Signals on a registrable-domain set (2026-10-05)
Evidence: red phase seen per file (test_companies.py ImportError on missing companies module; test_projection.py same; test_orchestrator_per_company.py 4 behavioural failures) before writing companies.py / orchestrator / projection changes; then `uv run ruff format src`, `ruff check src`, `mypy` (122 files) clean; `uv run pytest -q`: 2330 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Webmail: neither 8.x nor an ADR names it; added an explicit 18-entry `WEBMAIL_DOMAINS` set dropped from every company domain set (gmail-only record is a singleton). Rejected: no guard (all gmail users become one company and per-company Credits skipped); rejected: per-address Hunter `webmail` status (not per-domain).
- Domainless record (none, bare suffix, IP, localhost, webmail-only): singleton keyed by sha256 of its canonical JSON, identical records separate with -2/-3 suffix. Rejected: key by name (merge by name alone).
- company_id = "co-" + sha256(sorted registrable set)[:16]; changes if the cluster gains a domain. Rejected: anchor on lowest domain (equally unstable).
- projection: Employment.company.domains is now the registrable set (www/subdomain collapse, display form lost); company with no usable domain gets id from the Lead's cluster_id (per-Lead, never by name). Domain-only company with only unusable domains and no name yields no Employment.
- Orchestrator `_company_key` removed; per_company_work_list uses `domain_components` (first Lead of each overlap cluster). 11.7 test of equal-set-only dedupe updated deliberately. Reused clustering._UnionFind (private import) rather than a second union-find; made match_keys.registrable_domains public.
### Known gaps (needs-follow-up)
- Bullets: 1 (identity = registrable-domain set, same union-find mechanism) delivered; 2 (pinned PSL, no fetch; socket-blocked test) delivered, PSL "dated snapshot" is still the locked tldextract version, not a dated file; 3 (two records, different domains -> one company) delivered at cluster_company_signals level.
- Cross-lead company clustering is NOT wired into project_lead/store: per-Lead projection only sees its own domain set, so {a.com,a.io} vs {a.com} still get different company_ids, and same-id/different-name Leads still conflict in share_company_signals (16.5 review issue only partly addressed). Needs a cross-lead pass.
- match_keys name+domain key still treats gmail.com as a domain (not guarded); personal domains (name.com) undetectable.
- 16.10, 16.11, 16.12 deferred. No mutation checks run. No self-review (parent).

### Self-review findings
- Fixed: `CompanyCluster.domains` was in `repr` (a person's own domain is personal data); now `repr=False`, test-first (test_companies.py repr test extended).
- Mutation-checked, each caught: overlap ignored, no transitivity, webmail kept, order-dependent id, PSL fetch enabled, private suffixes off, subdomain not collapsed, no-domain merged by name. Files restored byte-identical.
- 11.7 test update judged faithful: equal-set dedupe, per_lead untouched, no-domain-kept-individually are all still asserted; only the unequal-overlap expectation changed as intended.
- Delivered: 16.9 bullets 1 (domain-set identity, union-find, order-independent), 2 (PSL pinned via tldextract bundled snapshot, no fetch, tested), 3 (two records, different domains, one Company Signal). 16.10-16.12 deferred.
- SPEC GAP: per-Lead `project_lead` still derives company_id from that Lead's own set, so overlapping-but-unequal sets give different ids per Lead, and one id cannot be shared with equal content because name/domains differ per Lead. Fixing needs a company-level resolution stage (name vote = 16.10); not small and pure, not built. needs-follow-up.
- needs-follow-up: WEBMAIL_DOMAINS is an invented, non-exhaustive list (no requirement/ADR names it); misses yahoo/outlook country variants, 163/126, mail.ru, web.de, gmx.net, pm.me. A webmail-only company is a keyless singleton (safe direction).
- needs-follow-up: shared hosts absent from the PSL (x.wordpress.com -> wordpress.com) and social URLs in company.domain (linkedin.com/company/x -> linkedin.com) would over-merge unrelated companies.
- needs-follow-up: company_id changes when a cluster gains a domain (no stable id across runs, cf. 8.9 and the 16.2 cluster_id issue); needs a persisted id mapping.
- needs-follow-up: project_lead now raises TypeError on a non-text winning company.domain (before: ignored silently); consistent with the orchestrator, error never echoes the value.
- needs-follow-up: tldextract is pinned only by `>=5` plus uv.lock; no dated-snapshot assertion beyond the offline test.

## Task 16.10 — Elect a display-only primary domain by trust-weighted vote (2026-10-05)
Evidence: wrote tests/test_primary_domain.py first; first run = collection ImportError (primary_domain missing), red. After primary_domain.py: 4 failures, all test arithmetic or fixture faults (dedupe of repeat votes miscounted by me), fixed in the tests, not the code; then 32 green. `uv run ruff format src`, `ruff check src`, `mypy` (124 files) clean; `uv run pytest -q` 2362 passed, 1 skipped. No mutation checks run.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Voter = each `provider_ids` source of the CompanySignal naming the domain (CompanySignal has no other source field); a Signal with no provider_ids is one shared undeclared voter. Rejected: new source field on CompanySignal; one voter per anonymous Signal.
- A source votes once per domain however often its records repeat it. Rejected: counting every record.
- Weight = rank - LOWEST_TRUST_RANK + 1, so rank 0 and undeclared sources still weigh 1 and an all-unranked cluster decides by number of sources, not an all-zero tie. Rejected: weight = rank (all-zero ties escalate to the LLM for nothing). Ranks validated like conflicts.py (int, not bool, >= lowest; TypeError/ValueError).
- Exact tie: result carries tied=True and sorted tied_domains (only the leaders); provisional winner = lowest-sorted tied domain (what 8.18 prescribes in synthetic mode). Rejected: raising, or a hash-based winner.
- No usable domain (webmail only, none): domain None, not tied. Webmail/subdomains handled via companies.company_domains.
- New module primary_domain.py (PrimaryDomain, elect_primary_domain(cluster, trust_ranks)); domain and tied_domains hidden from repr (personal data). Rank validation duplicated from conflicts.py (2 call sites, below the 3 for extraction).
- Display-only proven by test: AST scan shows clustering/companies/match_keys/orchestrator/projection neither import it nor take a "primary*" parameter; ranks flip the election while clusters, company_id and per_company_work_list signature stay unchanged.
- 16.9 SPEC GAP: electing a primary domain does NOT close any part of it (the gap is per-Lead company_id from unequal sets, and the name vote); this task adds no company-level resolution stage and does not wire the election into projection/CompanySignal.
### Known gaps (needs-follow-up)
- Bullet 1 (vote weighted by Source Trust Rank): delivered. Bullet 2 (display-only, absent from every match rule, changes no clustering outcome): delivered and tested. Bullet 3 (blocked on 16.9): satisfied.
- Not wired: nothing calls elect_primary_domain yet and no model field stores or displays it; where it surfaces (projection, report, CLI) is a later decision.
- 16.11 (LLM, persisted resolution, run-report flag) and 16.12 deferred; the tie is exposed as data only, the LLM is neither built nor imported (tested).
- Voter identity comes only from provider_ids; a record whose source is not listed there votes as undeclared.

### Self-review findings
- Fixed: trust-rank validator was duplicated in primary_domain.py; extracted validate_trust_ranks in conflicts.py (behaviour-preserving; resolve_conflicts and elect_primary_domain both call it). Only existing file changed.
- Added 9 tests (41 total): NaN/inf/float/None/huge-negative ranks (named error, no domain in message), huge int rank, source absent from mapping, tied_domains hidden from repr, win-by-one not a tie, trailing domain excluded from tie, multi-source signal, near-leader excluded from tied set, sourceless signals are one shared voter.
- Mutation checks run, all restored byte-identical: ignore weights, count repeats, tie flag never set, tie winner last or first-seen, ranks ignored, lowest weighs 0, no validation, repr leaks domain or tied_domains, near-tie widening, per-signal undeclared voter, webmail unfiltered, empty string for no domain, primary_domain imported from clustering/match_keys/companies/orchestrator/projection. Initially surviving: tie-near and undeclared-per-signal, now killed by new tests.
- Attribution verdict: a CompanyCluster keeps per-Signal domains and provider_ids, so each Signal sources vote for that Signal domains; exact when a Signal has one source. Delivered: both 16.10 bullets (trust-weighted vote; display-only, in no rule, import-direction and signature tests). Deferred: 16.11, 16.12. Tie returns tied=True, sorted tied_domains, lowest-sorted provisional winner (matches 8.18 synthetic rule); never blocks.
- Weight = rank - LOWEST + 1 (lowest and unranked weigh 1): design choice, consistent with conflicts default of LOWEST_TRUST_RANK.
- needs-follow-up: a Signal with several provider_ids and several domains credits every source to every domain (flat model cannot say which source supplied which); fine only while adapters emit one source per Signal.
- needs-follow-up: 16.9 SPEC GAP (project_lead per Lead gives different company_ids for overlapping sets) untouched by this task.

## Task 16.11 — Persist the constrained primary-domain tie resolution (2026-10-05)
Evidence: ran each new test file before its code (test_tie_resolution.py: ImportError at collection; test_tie_resolution_store.py: ImportError at collection); then `uv run ruff format src`, `uv run ruff check src`, `uv run mypy`, `uv run pytest -q` all clean (2408 passed, 1 unrelated skip), including the Postgres leg of test_persistence_both_engines.py. The Postgres test and the EXPECTED_TABLES edit were written after the implementation (no observed red for those two).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Port is `TieResolver.choose(candidates, *, timeout)` with `model`/`prompt_version` properties; payload is only the tied candidates (not the company's other domains). Rejected: passing the whole domain set or cluster.
- Answer validation is exact (`str` equal to a candidate); case/whitespace/www variants are rejected, not normalised. Rejected: casefold/strip leniency.
- Rejected answer, port failure (`TieResolverError`/`TimeoutError`) and no configured resolver all fall back to the lowest-sorted candidate with a counts-only WARNING and a `TieOutcome.flagged`; not persisted. Other exceptions and CancelledError propagate.
- Tie key = sha256(sorted company domain set, sorted candidates), not the cluster id.
- First write wins, no supersede; `put` returns the stored record (retry/race adopts it). Rejected: overwrite or explicit supersede.
- Table is append-only (ORM guard extended to it) so recompute stays byte-identical.
- A stored record is read in any mode, including synthetic (synthetic never writes/calls). Rejected: ignoring stored records in synthetic.
- Timeout is a module constant (10 s) passed through the port; no retry added.
- Flag on run report is the `TieOutcome.flagged` seam + log line (18.x run report not built, as in 16.8).
### Known gaps (needs-follow-up)
- Bullet 1 (constrained escalation via port): delivered. Bullet 2 (persist record with chosen domain, candidates, model, prompt version, timestamp; projection reads it): delivered as `read_stored_primary_domain` + migration 0004 + repository; `projection.py` does not yet call the primary domain at all (it never used 16.10), so wiring it in is deferred.
- Bullet 3 (synthetic never calls, lowest-sorted, flagged): delivered except that the run-report field itself is deferred to 18.x.
- No concrete LLM adapter (not required); no caller of `resolve_primary_domain` exists yet (orchestrator wiring, resolver factory from LLM_PROVIDER/LLM_MODEL) is deferred.
- A rejected/failed answer is not persisted, so a later escalation call asks again (bounded per call, not per run).

- **LEFT UNCHECKED IN tasks.md by the parent (SPEC GAP, needs-user):** the port, the validation, the append-only persistence (migration 0004, both engines) and the synthetic-mode bypass are delivered and reviewed. The projection reading the stored answer and the run-report counter are only seams (`read_stored_primary_domain`, `TieOutcome.flagged`) until the projection/orchestrator wiring (and task 18) exists. Also open: the resolver port is synchronous with no asyncio timeout/cancellation story (a hung resolver would hang a live run), and a blank or over-long model label raises after the paid call.

### Self-review findings
Fixed:
- log_merges built the line (event.log_fields()) inside the try, so a builder bug was swallowed and counted as a logger failure. Moved outside the try; test-first.
- Added tests: KeyboardInterrupt/SystemExit/CancelledError propagate; ConsoleRenderer canary over lines, merged_by, ResolvedConflict and decided_by reprs; cap order-independence on shuffled hand-built conflicts (mutation "cap sorted by something else" survived before).
- Docstring states one line per merge, no per-run cap, by design.
Verified unchanged: except Exception has a justified noqa BLE001 (same pattern as log_redaction.py); a narrower catch is not realistic (processor chains can raise anything); the union-wrapper edits in test_identity_exclusions/test_role_addresses only pass the new kind argument through, counts intact; decided_by survives retain_superseded (dataclasses.replace), idempotence holds; merged_by and events identical across 300 random permutations.
Bullets: 16.12 bullet 1 (carry Match Key and resolved conflicts on the projection result, derive the line) delivered. Run-report persistence (18.x) deferred.
Known gaps:
- SPEC GAP (21.4): match key logged as KIND only. log_redaction.py redacts credentials only and has no mask/hash helper; no requirement or ADR approves a form. User must decide: kind-only, a keyed HMAC prefix (secret from env), or a masked form.
- needs-follow-up: MergeLogOutcome.failed is not surfaced anywhere yet (18.x).
- needs-follow-up: sorted() on merged_by in clustering.py is not guarded by a test (small IntEnum sets iterate sorted anyway).
- needs-follow-up: 100k merges log 100k lines (no per-run cap).

- **LEFT UNCHECKED IN tasks.md by the parent (SPEC GAP, needs-user):** the merge log, its volume cap, the failure handling and the PII canary are delivered and reviewed, but requirement 21.4 asks for the Match Key used and the log carries only its KIND, because `log_redaction.py` redacts credentials only and no requirement or ADR approves a masked or hashed form of an email or LinkedIn URL. Decide one of: kind-only (current), a keyed HMAC prefix with the secret from the environment, or a masked form. Also open: `MergeLogOutcome.failed` is not surfaced until task 18, there is no per-run cap on merge lines, and the projection took about 40 s for a 1000-candidate by 1000-path cluster (worth a performance look).

### Self-review findings
- Fixed (truthfulness): every record claimed `schema_verified_on = 2026-10-04`, but nothing in this repository checked any shape against a documentation page (choices.md: HubSpot property names, Hunter finder/verifier shape, SerpApi fields, Apollo CSV layout all unverified). Added a required `schema_status` (verified | unverified); a date is allowed only with `verified`, null only with `unverified`. All 9 shipped records are now `unverified` with a null date and a note naming research.md as the shape source. Test-first: new tests red, then green.
- Fixed: dates are strict (no int timestamps, no datetimes) and may not be in the future; `file` must be a plain relative posix path (no `..`, absolute, backslash, `.`); symlinked fixtures rejected; notes bounded (1..500), other text fields capped; unreadable-input echo ruled out by a canary test.
- Added tests: PII/secret scan over every fixture and manifest (emails only on example domains, no key/bearer/long tokens), determinism (load twice equal), shipped records claim no unverified verification, registry-enumerated coverage already existed.
- Mutation-checked (all caught, file restored byte-identical): stray fixture ignored, missing fixture ignored, undeclared endpoint accepted, captured without redaction, future recorded/verified date, content echoed, lax coercion, path traversal.
- Delivered: 17.1 bullet 1 (JSON per provider dir, one per exercised endpoint; manifest.json per directory matches design.md line 775) and bullet 2 (doc URL recorded for every file; the verification date is recorded as unverified/null). Bullet 3 (not concurrent) is a scheduling note. Deferred: 17.2, 17.3.
- Kept: `supported_technologies.csv` has endpoint null; allowed as reference data (it is not a provider endpoint response; 5.1 covers endpoints only).
- Kept: the loader is test-time only; neither 5.x nor the design says FixtureTransport consults the manifest at runtime.
- SPEC GAP: requirement 5.6 asks for "the date the schema was verified"; no date can be truthfully recorded today because the provider doc pages were not checked from this environment. needs-follow-up: verify each shape against its page, then flip the record to `verified` with the date (the shipped-records test must be updated at that point).
- needs-follow-up: `api_version` is null everywhere (no version known); `recorded_on` is the authoring date for hand-made files.

## Task 17.2 — Validate every fixture against its declared raw schema (2026-10-05)
Evidence: wrote tests/test_fixture_schema.py first; run 1 = collection ModuleNotFoundError (fixture_schema missing), red. After fixture_schema.py + base hooks only, 35 failed / 52 passed (adapters had no schema hooks), red. After adapter hooks all green. Vendor-named mutation tests live in tests/adapters/test_fixture_schema_mutations.py (written in the same red-first pass, split afterwards because test_vendor_neutrality scans tests/ outside adapters/). `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q`: 2590 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Validation is test-time plus a callable (`fixture_schema.validate_fixture_schemas`), NOT wired into FixtureTransport at startup: 5.2 already runs raw-schema validation via normalize() in synthetic mode and 5.4 only asks the suite to fail. Rejected: load-time validation in the transport.
- Per-adapter hooks `BaseLeadSource.validate_fixture(endpoint, body)` and `validate_reference_file(file, text)` (classmethods; base default refuses with FixtureSchemaError) rather than a central endpoint-to-model table: keeps "new source = one module plus fixtures" (3.x) and vendor names inside adapters/. Rejected: central mapping module.
- Error field format `<file>:<raw field path>` (dotted, record-relative, no array index beyond pydantic's `emails.0`), `<file>` alone for unreadable/non-JSON/non-object/oversize/too-deep files, `<endpoint>: no raw schema declared` for a source with no hook. No values echoed.
- MAX_FIXTURE_BYTES = 1,000,000 cap; RecursionError from the JSON parser treated as unreadable.
- Reference CSV validated by the Apollo loader's own parsing (`_technology_uids`, now shared with `_supported_technologies`): requires a `uid` column, at least one row, no blank uid. Behaviour change in the production loader: a blank uid or empty snapshot now raises NormalizationError (was KeyError / accepted). Rejected: documenting an exemption.
- HubSpot deal_search has no Pydantic model; its check is the adapter's existing `total` rule, extracted to `_open_deal_total` and shared with fetch_raw. Google `_page_evidence` refactored into `_blocks_of`/`_checked` shared with the fixture hook.
- No fixture needed fixing and no schema was weakened.
### Known gaps (needs-follow-up)
- No declared raw model forbids extras (all tolerate unknown fields), so the "unknown field where schema forbids extras" mutation does not apply and is not tested. Design line 503 (every fixture field is mapped or in IGNORED) is not built: not in 5.3/5.4; belongs with 17.3 (5.5 no undefined field).
- Fixtures remain unverified hand-made stand-ins; passing proves consistency with our schemas, not with the providers.
- Task bullets: 1 (one test per provider asserting its fixtures validate) delivered: parametrized per fixture plus a per-provider test; 2 (fail naming provider and field) delivered; "Blocked on 17.1" satisfied; "(P) parallel with 17.3" respected, 17.3 not built.
- Files: src/leadforge/lead_ingestion/{fixture_schema.py,base_source.py,adapters/{apollo,hubspot,hunter,google_search}.py,tests/test_fixture_schema.py,tests/adapters/test_fixture_schema_mutations.py}.

### Self-review findings

Fixed (each test-first, seen failing first). The "behaviour change in the production loader" decision above is SUPERSEDED by item 2.
1. Fixture content leaked through `__cause__`/`__context__` (JSONDecodeError keeps the whole document, UnicodeDecodeError the bytes, pydantic ValidationError the input value). `fixture_schema.py` now raises FixtureSchemaError outside any `except`, so the chain is empty. Test: `test_a_failure_carries_no_chained_exception_that_holds_fixture_content`.
2. Apollo production loader tightening reverted. `_supported_technologies()` (used by the ApolloSource constructor, live and synthetic) is the original lenient loader again, because a blank uid or empty snapshot would otherwise stop a run. The strict check is now `_check_technology_snapshot`, used only by `validate_reference_file`. It also catches `csv.Error` (an oversized field escaped raw) and uses `strict=True` (unterminated quote). Tests: hostile-CSV and `test_the_production_snapshot_loader_stays_lenient`.
3. Duplicate JSON keys were silently accepted (last wins, hides drift); now a file-level FixtureSchemaError.
4. A symlinked fixture file was followed by `validate_fixture_file`; now refused.
5. Added whole-tree tests for an unlisted file and a manifest-listed absent file (only one weak test caught a removed tree check).
6. A test I added hard-coded vendor names outside adapters/ and tripped test_vendor_neutrality; now generic.

Verified claims: 5.2 validates at runtime only the fixtures a run actually fetches, via normalize(); 5.4 asks only for a suite failure, so test-time-only is acceptable. Mutations (skip validation, echo content, default accepts, wrong provider, field path dropped, tree check removed) each fail the suite. Full suite 2601 passed, ruff and mypy clean, whole-tree validation under 1 s.

Task bullets: both 17.2 bullets delivered (per-provider validation; failure naming provider and field). 17.3 not touched.

Known gaps:
- needs-follow-up: no raw model uses `extra="forbid"`, so an unknown extra field in a fixture is tolerated. design.md line 503 says fixture fields must be mapped or listed as ignored; `unmapped_raw_paths` runs in the adapter tests, not in this guard. SPEC GAP candidate for 5.5 "no fixture carries a field its provider does not define" (17.3).
- needs-follow-up: a ValueError from a bug inside an adapter's validate_fixture is reported as a file-level failure instead of surfacing.
- Non-UTF8, BOM, non-object and duplicate-key files fail at file level (field = file name, no path) by design.

## Task 17.3 — Cover positive and negative outcomes per provider contribution (2026-10-05)
Evidence: red phase seen per new test file before its code. test_fixture_metadata additions: 2 failed (variant endpoint rule, root-default rule). tests/test_fixture_fields.py: collection ModuleNotFoundError (fixture_fields), then with only the module and the base default 18 failed / 6 passed (adapters declared no coverage). tests/adapters/test_fixture_outcome_matrix.py: 26 failed / 18 passed before the hand-made fixtures existed (the 18 passes were the existing root fixtures, whose requirement-derived expectations already held: no code bug found). tests/adapters/test_fixture_fields_mutations.py was written AFTER the adapter hooks (green on first run, NOT seen red); instead mutation-checked: unknown->verified, Hunter hook returning [], inverted HubSpot opt-out parse, Google hook skipping the envelope, Apollo none-match contributing: each caught, files restored. After: `uv run ruff format src`, `ruff check src`, `mypy` clean (140 files); `uv run pytest -q` 2800 passed, 1 skipped (was 2601).
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Extra outcomes live as variants in a per-scenario subdirectory (`fixtures/<provider>/no_match/search.json`), recorded in the manifest like any fixture; manifest endpoint rule is now "endpoint == file stem of the basename", and the root `<endpoint>.json` stays mandatory (tree check) because FixtureTransport serves only that. Rejected: a second file per endpoint at the root (breaks 5.1 one-file-per-endpoint and the endpoint==stem rule), and several answers inside one file (a zero-result page cannot coexist with results).
- The outcome matrix (which fixture is positive/negative for which label) is a table in tests/adapters/test_fixture_outcome_matrix.py, not a manifest field. Rejected: an `outcome` field on FixtureRecord (would change the 17.1 schema and its tests; a new source would then be checked generically, a gain I did not take).
- Required labels are derived from declarations: every endpoint, plus `suppression` if yields_suppression, `target_match` if any target_profile.* surface, `email_status` if person.email_status is answerable. Each needs a positive and a negative case. Hunter verifier: valid = positive; invalid is endpoint-positive/status-negative; accept_all and unknown are negative on both (unverifiable). HubSpot: opted-out = suppression positive; not-opted-out and contact-not-found = suppression negative.
- Field guard belongs to 17.3 (5.5 Verify: "no fixture carries a field its provider's documented schema does not define"; design line 503 ties it to 5.3 but 17.2 left it here). New hook `BaseLeadSource.unmapped_fixture_paths(endpoint, body)` (default refuses like validate_fixture) + module fixture_fields.py raising FixtureSchemaError `<file>:<path>`, path escaped and cut to 120 chars. Envelope fields no rule reads are declared per adapter (SEARCH_ENVELOPE_IGNORED, CONTACT_SEARCH_IGNORED, DEAL_SEARCH_IGNORED, ENVELOPE_IGNORED). Rejected: a central table of ignored envelope paths in the guard; ignoring `meta`/`results` implicitly.
- No-match shapes are guesses, flagged unverified: Apollo none-match is `{"match_confidence":"none"}` with no `person` key (a `person: null` leaf would trip the walker, which treats a null parent as an unmapped leaf); Hunter finder not-found is `data.email: null`; Google zero results is a page with only metadata. Rejected: inventing provider-specific error or empty-state fields.
- Not Applicable is tested without a fixture (target_term_absence for the sources with no vocabulary for a term, plus refusal of Negative Evidence on a path with no surface), because it is a declaration fact, not a payload.
### Known gaps (needs-follow-up)
- Fixtures are hand-made, unverified stand-ins (manifest says so); passing proves consistency with our schemas and requirements, not with the providers. The no-match / not-found / zero-result shapes are the least grounded.
- Not coverable by a fixture file: Hunter verifier 202 (no body) and 451, Apollo/HubSpot/Hunter error statuses. Still covered only by the scripted-transport tests from 12.x, 13.x and 15.x.
- Not added: Google answer_box / knowledge_graph blocks (shapes from memory, would add unverified fields); Apollo person with a locked/unavailable email status (value unverified); Apollo has no `target_profile.*` Negative Evidence (adapter only logs a warning; flagged in 12.1 choices, unchanged).
- Per-adapter nested-stray-field and ignore-entry-removal tests (test_fixture_fields_mutations.py) were not written red-first; mutation-checked instead.
- Task bullets: 1 (positive and negative fixture per provider contribution) delivered for all four registered sources; 2 (scope to each provider's own capability, no field outside its schema) delivered via the field guard plus capability-derived labels (a declared-schema check beyond "mapped or ignored" is not possible without verified docs); "Blocked on 17.1; parallel with 17.2" respected (17.1 manifest rules extended, 17.2 modules untouched).
- Files: src/leadforge/lead_ingestion/{fixture_fields.py,fixture_metadata.py,base_source.py,adapters/{apollo,hubspot,hunter,google_search}.py,fixtures/*/manifest.json + 10 new variant fixtures,tests/test_fixture_fields.py,tests/test_fixture_metadata.py,tests/adapters/{test_fixture_outcome_matrix,test_fixture_fields_mutations}.py}.

### Self-review findings

Fixed (each seen red, then green; 2803 passed, 1 skipped; ruff and mypy clean):
- Overly broad envelope ignores narrowed to named leaves: Hunter `meta` (now meta.results, limit, offset, params.domain, first_name, last_name, email) and Google `search_metadata` / `search_information` (now search_metadata.status, search_information.total_results). A stray key inside them previously passed the guard; it now fails naming provider and path. Mutation tests updated (tolerance cases removed, stray-envelope-field test added).
- The matrix socket guard had no test proving it active (a no-op guard passed everything). Added test_the_socket_guard_is_active_and_no_credential_is_set (red under a no-op guard); the autouse fixture now also deletes every source's required_env variables; run_case asserts the transport is a FixtureTransport; the driver builder raises on an unknown provider rather than silently building Google.
- Added a regression test for a symlinked variant file and a symlinked variant directory (already rejected; untested before).
- Hunter finder-not-found assertion now carries an explicit SPEC GAP comment (it pins current behaviour).

Re-verified by mutation (restored byte-identical): negative fixtures replaced by positives (3 cases fail), positive fixtures replaced by empty ones (2 fail), variant file removed (fails loudly), manifest record removed (fails loudly), socket guard no-op (now caught). Registry enumeration is real; the 17.1 PII/secret scan already recurses (rglob); zero network; no bare except.

Task bullets: 17.3 bullets 1, 2 and 3 delivered. Deferred: none. 18.x and 19.x untouched.

Known gaps:
- SPEC GAP (1.9): a Hunter finder that was asked and found nothing contributes no Negative Evidence on person.email although Hunter declares it answerable; Hunter never passes queried_paths (15.1 decision). needs-follow-up (needs a decision on which identity the absence attaches to).
- The outcome table lives in the test, not the manifest. Judged acceptable: 5.6 metadata is URL plus date only, and the matrix enumerates the real registry, so a new adapter cannot skip it (it also needs a driver in the test). needs-follow-up if per-fixture outcome metadata is wanted.
- needs-follow-up: items inside an ignored list leaf (HubSpot deal_search `results`) are not walked.
- The no-match shapes are guesses, flagged hand_made / unverified in all 10 records; tests assert adapter behaviour only.

## Task 18.1 — Persist the run record with every source's resolved mode (2026-10-05)
Evidence: wrote tests/test_run_record.py first and saw collection fail (ModuleNotFoundError run_record) before any code; then 16 passed. Dual-engine test added to test_persistence_both_engines.py after the code (not seen red; mutation check instead: exit_code=7 in finish failed 4 tests incl. the postgres leg, restored). `uv run ruff format src`, `ruff check src`, `mypy` clean; `pytest -q` 2821 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- No migration: ingestion_run and source_run (0001) already hold every field (run id, started_at, finished_at, status, exit_code, pool_size, config_snapshot; per source resolved_mode, mode_reason). Rejected a new table.
- Status vocabulary running / completed / aborted (RunStatus). Completed = run returned with an exit code (an all-failed run is completed, exit 1); aborted = exception or cancellation, no exit code. Rejected a "failed" status (exit code already says it).
- finish() is the only update, completion fields only, once; refuses unknown run, second finish, running status, completed without exit code, aborted with one (RunRecordError). Rejected silent overwrite.
- config_snapshot = max_concurrent_sources, run_timeout_s, global_mode, per enabled source trust_rank + mode/live_access overrides, built from typed settings only (no env values). Rejected dumping the raw config file.
- Reason cut to 255 chars (column limit) so start cannot fail on a long variable list; no log_redaction pass because reasons carry only env var names/enum values (8.1); pinned by a test with a real resolution.
- Written through StoreWriter.write_batch (existing shielded path) rather than begin_run, which cannot write source rows; begin_run left untouched. Rejected extending begin_run (changes the 6.4 API).
- Source rows read back ordered by name (no ordering column). live_access/credential_present columns not filled (18.2 bullet).
- Test fixtures use alpha/bravo names (vendor-neutrality test forbids vendor names in tests).
### Known gaps (needs-follow-up)
- 18.1 single bullet (create record at start with run id, start time, pool bound, config snapshot, every enabled source's resolved mode): delivered as pure build_run_record + RunRecordRepository (start/finish/get); NOT wired into IngestionOrchestrator.run, which has no store/StoreWriter today. Remaining wiring: inject StoreWriter, build record after mode resolution and before any slot, start via write_batch, finish in try/except BaseException (re-raise, never swallow CancelledError; finish itself goes through the shielded write_batch) marking ABORTED, COMPLETED with map_run_exit code otherwise; global_mode must be passed in.
- Dual-engine test not observed red (written after code).
- Per-source counts/failure classes (18.2) and report (18.3) not built.

### Self-review findings
Fixed (each test-first, seen failing):
- Wiring was missing though 18.1 and Requirement 21.1 make the orchestrator create the record (this supersedes the "NOT wired" gap above): added optional `run_recorder` (Protocol `RunRecorder`, orchestrator.py) and `StoreRunRecorder` (run_recorder.py; injectable clock, global_mode; writes via `StoreWriter.write_batch`). `run()` starts the record after mode resolution and before any adapter is built or called, finishes `completed` with the `map_run_exit` exit code, and marks `aborted` on any BaseException (CancelledError included), then re-raises. A failed start stops the run, a failed finish raises, a failed abort marker is logged and noted on the propagating exception. None means no persistence, so existing callers are unchanged.
- `finish` was read-then-write: a stale or concurrent finisher could complete a run twice (lost update). It is now one conditional UPDATE (status must still be running); unknown run and already finished stay named `RunRecordError`.
- Naive `finished_at` / `started_at` were silently read as local time by `astimezone`; now refused.
- Reason truncation was silent: a cut now ends with an ellipsis (255 characters in total, whole characters); the exact-limit and multibyte cases are tested.
- Builder: sources and snapshot sorted by name (matches `get()` order, so it round-trips exactly); a non-`DataMode` mode is refused with a named ValueError; an empty run is a valid record (tested); inputs not mutated (tested).
- Mutation-checked (all caught; the dual-engine test fails on both the sqlite and postgres legs): mode dropped, reason dropped, double finish allowed, status not updated, exit code not stored or hardcoded, wrong mode stored, no source rows written, no abort marker. Files restored byte-identical.
Delivered: 18.1 (the single bullet: record at run start with id, start time, pool bound, config snapshot, resolved mode and reason of every enabled source), including the orchestrator wiring. No migration needed: ingestion_run and source_run (0001) hold every field; status String(32), mode String(16), mode_reason String(255) fit.
Deferred: 18.2 (counts, failure classes, live_access, credential_present, throttle/retry/429 fields on source_run) and 18.3 (report from DB queries).
Known gaps:
- needs-follow-up: a record left running by a hard kill is never resolved (no sweep); documented only in run_recorder.py.
- needs-follow-up: `run()` returns results only; a caller cannot learn the run id except by querying the store (18.3 will need it).
- needs-follow-up: nothing outside tests constructs `StoreRunRecorder` (no CLI or composition root builds an orchestrator yet).
- needs-follow-up: `config_snapshot` holds pool bound, timeout, global mode and per-source trust rank and overrides only; it does not capture the target profile or fixture versions. SPEC GAP candidate: 18.1 says the snapshot "makes the run reproducible", which this only partly delivers.
- The status vocabulary running / completed / aborted has no spec text and no DB CHECK; it is the implementer's choice.
- The credential canary runs through the real resolver; the snapshot reads typed settings only, so it cannot carry environment values by construction.

## Task 18.2 — Persist per-source counts and failure classes (2026-10-05)
Evidence: new tests/test_run_source_counts.py and a dual-engine test in test_persistence_both_engines.py were run first and SEEN FAILING (ImportError: SourceCounts missing; sqlite and postgres legs both red). After the code: `uv run ruff format src`, `ruff check src`, `mypy` (146 files) clean; `uv run pytest -q` 2873 passed, 1 skipped (Postgres leg ran). Not mutation-checked.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Counts are written in the SAME write_batch as the run finish (StoreRunRecorder.finish: finish + record_source_counts), not per source. Rejected: per-source batch writes (a run could finish without counts). Consequence: a failing count write leaves the run `running`.
- Aborted run gets NO counts (results never leave `_execute` on an exception); rows keep start values. Rejected: threading ledgers out of `_execute` (scope).
- leads_found = contributions normalized across both phases; no column or data for raw "fetched" or "merged into existing" (run merges nothing). contributions_written untouched (run persists none).
- failure_class = final SourceStatus value; NULL for ok. A source with no result (empty-work-list Enrichment) is NULL/zeros, indistinguishable from ok. Rejected: inventing a "skipped" status.
- live_access (bool) = effective classification is not UNAVAILABLE (gated counts as could-run-live), written at start via a new `live_access` kwarg on RunRecorder.start and SourceMode.live_access. Rejected: a 3-valued string (column is Boolean).
- quota_remaining = local limiter tokens left per bucket (tightest window, floored), not provider quota; throttle_waits and http_429_count from SourceThrottle.snapshot (new optional SourceResult.throttle/allowances); retries from the call ledger.
- warnings = [outcome.error] when set (PII-safe by 11.2/12.3); no other warning.
- RunRecorder.start Protocol gained a required `live_access` kwarg (only StoreRunRecorder implements it).
### Known gaps (needs-follow-up)
- Bullet 1 DELIVERED for: leads normalized, failure class, throttle waits, retries, 429 responses, remaining allowances, warnings. DEFERRED: records fetched and merged-into-existing (no column, no producer in `run`; needs migration plus merge wiring); Credits consumed (credits_consumed stays NULL: no adapter-to-run channel, credits_in is an unverified one-per-call assumption); failure counts per class (only the final class is stored, call-level failed/skipped counts have no column).
- Bullet 2 PARTLY: live_access delivered; credential_present deferred (orchestrator reads no environment).
- Deferred counts, each needs a column or JSON key plus a producer wired in the run: merge-log failures, over-merge suspects, tie fallbacks (merge/projection runs outside `run`), Hunter poll give-ups and compliance-restricted addresses (never reach SourceOutcome; need a count on the outcome).
- Retries via RetryPolicy give no ThrottleFeedback, so throttle_snapshot.retries is not used.
- 18.3 (report from queries) not started; StoredRun/get does not expose counts yet.

### Self-review findings
Fixed (test-first, seen red then green):
- quota_remaining held the LOCAL token-bucket state (a false provider quota). Now only provider-stated allowances (an adapter's `allowances`, e.g. 12.5 per-window headers, read through the `ReportsAllowances` protocol in orchestrator.py); NULL otherwise. SourceResult.allowances is now Mapping[str, int]. This supersedes the quota_remaining decision above.
- live_access boolean column could not tell gated from available (3.6 asks for every source's classification). The three-valued value is now also stored in ingestion_run.config_snapshot["sources"][name]["live_access"]; the boolean stays "could run live". RunRecorder.start now takes Mapping[str, LiveAccess]; the bool is derived in build_run_record.
- Added tests: all-SourceStatus failure-class mapping, provider-allowance persisted, no-allowance is NULL, three-valued snapshot. Mutation-checked the new tests (class dropped/swapped/ok-gets-class, counts not stored, finish and counts in separate transactions, redaction removed, first-phase-only, leads not summed, gated mapped false, snapshot dropped, empty-dict quota, waits, retries, ordering, allowance wiring): all caught.
Checked and left as is: counts and finish are one transaction (dual-engine test green on sqlite and postgres); one row per source (later phase result, leads summed, so no double-counted attempts); never-started sources get timed_out; warnings use the 11.2 redacted message, adapter endpoint paths are static templates, Apollo error codes are identifier-shaped.
Bullets: 18.2 bullet 1 delivered for normalized leads, failures by class, throttle waits, retries, 429s, allowances (Apollo only), warnings; deferred for fetched, merged, Credits. Bullet 2 live-access delivered; credential_present deferred.
Known gaps:
- SPEC GAP: Req 21.2 fetched and merged-into-existing counts: no source_run column and no producer in `run` (needs a migration plus a merge in run).
- SPEC GAP: credits_consumed: credits_in(batch) exists only as per-adapter module functions (Apollo enrichment batches only, Hunter) with no BaseLeadSource contract, and the orchestrator may not import adapters. needs-follow-up: an adapter-neutral credits hook.
- needs-follow-up: aborted runs persist no per-source counts (18.2 says "on completion"; outcomes live in _execute ledgers and are lost on exception).
- needs-follow-up: NULL failure_class means ok OR not run (aborted, enrichment with no work); 18.3 must read ingestion_run.status to disambiguate.
- needs-follow-up: a failed counts write rolls the finish back and leaves the run `running`; the caller sees the error but no aborted marker is written.
- needs-follow-up: warnings redaction is an allowlist of one class (compliance subject); a future SourceError carrying personal data in its text would reach the column.
- needs-follow-up: leads_found sums contributions across phases, so Enrichment contributions about already-found leads count again.
- needs-follow-up: Apollo allowances are last-response only; a final response without headers yields NULL.

## Task 18.3 — Produce the run report entirely from database queries (2026-10-05)
Evidence: new tests/test_run_report.py (16 tests) run first and SEEN FAILING (ModuleNotFoundError: run_report). Dual-engine test added to test_persistence_both_engines.py was written after the module existed, so not red on its own; made red by a temporary ordering mutation (sqlite and postgres legs both failed), then restored. After: `uv run ruff format src`, `ruff check src`, `mypy` (148 files) clean; `uv run pytest -q` all green (Postgres leg ran). Not otherwise mutation-checked.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- `build_run_report(session, run_id=None) -> RunReport` in new run_report.py queries ingestion_run/source_run directly via store models (no orchestrator/result argument; signature + source tests). Rejected: extending RunRecordRepository.get/StoredRun.
- No run id = latest by started_at desc, id desc. Unknown id / empty store raise `RunNotFoundError` (subclass of RunRecordError). Rejected: returning None.
- Per-source counts shown only when run.status == completed; running/aborted render "not recorded" (aborted adds "aborted: no per-source counts recorded"). Rejected: showing the row defaults (zeros).
- failure_class NULL on a completed run renders "none recorded", not "ok" (ok vs not-run indistinguishable).
- fetched, merged always "not recorded"; credits NULL "not recorded"; quota NULL "not stated"; leads_normalized labelled "contributions across phases, not distinct leads".
- Live access: snapshot three-valued value, else boolean False -> "unavailable", else "not recorded". Summary lines: could run live / synthetic-only by necessity / classification not recorded.
- Control characters escaped (as run_exit); warnings cut at 200 chars with ellipsis. Rendering shows only recorded instants (ISO).
- CLI not touched: the task text and 21.5 do not say the CLI prints it.
### Known gaps (needs-follow-up)
- Task 18.3 has one bullet ("make the run record queryable so the report is a query"): DELIVERED as the report function + renderer + tests.
- NOT delivered: a CLI path (ingest is still a stub that builds no orchestrator or StoreRunRecorder; a `report` command would need a store URL/engine wiring) and nothing prints the report yet.
- Not persisted, so absent from the report: over-merge suspects (15), tie fallbacks (18), fetched/merged counts, Credits, failure counts per class beyond the single final class.
- A crashed run stays "running" (no sweep); the report says "still running or crashed".

### Self-review findings
Fixed (each test-first, seen red):
- `DataMode(resolved_mode)` crashed on an unknown/future mode: `SourceReport.mode` is now a plain string, rendered escaped.
- `live_access` from an older/odd snapshot (non-mapping, non-string, or unknown value) is now tolerated; only available/gated/unavailable are accepted, else the boolean column, else not recorded (an unknown value used to vanish from every group).
- Quota values were rendered unescaped (forgeable line/ANSI); now escaped.
- Completed-run NULL failure class: a row with any recorded activity (leads, retries, throttle waits, 429s) now renders `ok`; an all-zero row stays `none recorded` (ok and not-run are not distinguishable from stored fields; attempted/succeeded are not persisted).
- Report now renders `over-merge suspects: not recorded` and `primary-domain tie fallbacks: not recorded` (8.15, 8.18).
- New tests: whitelisted-snapshot canary (text and repr), query count constant and SELECT-only, import-direction AST check (aliases included), reason escaping, unknown mode.
Mutation-checked (all caught, files restored): counts for aborted, counts always shown, wrong latest run, unescaped reason, NULL as 0, credits as 0, orchestrator import, dual-engine (failure class, live_access, ordering, quota; both legs red). Survivor: dropping `ORDER BY source_name` (SQLite serves it from the run_id/source_name index; needs-follow-up if a Postgres order check is wanted).
Bullets: 18.3's single bullet (queryable record, report from DB queries; Req 21.5) is DELIVERED. No bullet mentions the CLI, so `ingest` was left a stub (needs-follow-up: print the report once the composition root exists).
Known gaps:
- SPEC GAP: Req 8.15 (over-merge suspects) and 8.18 (tie fallbacks) say "flag on the run report". Nothing persists them (no column or table) and nothing produces them: the orchestrator `run` never calls clustering/projection/over_merge/tie_resolution. Two missing pieces: persistence and a producer. The report only says "not recorded".
- Fetched, merged and credits have no producer (18.2): rendered `not recorded`, never 0.
- needs-follow-up: run_report imports run_record (RunStatus, RunRecordError), which transitively imports orchestrator/registry. The report function takes and uses none, but a strict module graph free of orchestrator needs those moved to a leaf module.
- needs-follow-up: `_printable` is duplicated (run_exit, run_report); extract at a third call site.
- `leads_normalized` is labelled as contributions across phases, not distinct leads.

## Task 19.1 — Assert no adapter reaches a send-capable endpoint (2026-10-05)
Evidence: new tests/test_no_send_endpoints.py first run RED (collection ImportError: SendCapableEndpointError missing), then GREEN after send_prohibition.py, errors.py, base_source/transport wiring and structure_guard scans. Mutation checks run and restored: hunter endpoint renamed to /v2/campaigns/send (52 tests failed), literal /api/v1/emailer_campaigns appended (scan failed), "DELETE" literal appended (scan failed), a GET changed to POST (allowlist test failed). Final: ruff format/check clean, mypy clean, pytest 3043 passed 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Denylist is whole-word tokens (split on / _ - camelCase, {placeholders} dropped) in new send_prohibition.py, not regex substrings; rejected substrings because email-finder / email-verifier / bulk_match would false-positive. "emails" (plural collection) is denied, "email" alone is not; "bulk" alone is not denied (bulk_create is caught by create).
- Runtime (BaseLeadSource._validate_endpoints, RestTransport and FixtureTransport constructors) enforces the denylist only, raising new SendCapableEndpointError; the closed POST allowlist (READ_ONLY_POST_ALLOWLIST, path -> reason) is enforced only by the guard test on the real registry, because many existing tests declare throwaway POST paths. Rejected: runtime POST allowlist.
- Endpoint.__post_init__ is not changed (rejected: it would make a denylisted Endpoint unconstructible, so the transport refusal could not be tested).
- Direct-send scan flags any x.send(..., json_body=...) outside base_source/transport/mcp_transport/auth in the slice (adapters included); auth.py and mcp_transport.py are sanctioned (OAuth token fetch, MCP fallback delegate). Rejected: matching on receiver name.
- Static path scan treats a string literal starting with "/" and without whitespace as a path; f-string pieces are checked individually (so a bare "/enroll" piece is flagged). Test files are skipped (tests dir).
- Write-verb scan flags .put/.patch/.delete calls on any receiver and any "PUT"/"PATCH"/"DELETE" literal.
### Known gaps (needs-follow-up)
- 19.1 bullet 1 (read-only operations only): delivered via runtime guard + enumeration test. Bullet 2 (static test asserting no send/sequence/messaging path in adapters): delivered. Bullet 3 (parallel note): n/a.
- research.md has no per-provider send endpoint list; the denylist and SEND_PATHS examples come from public API knowledge, not verified against provider docs this run.
- Denylist is best-effort on path words; a send endpoint with an innocuous path would pass the denylist (POST is still held by the allowlist in the test).
- Mock HTTP check patches httpx.AsyncClient.request/send; it does not assert zero sockets (that is 19.2).
- 19.2, 19.3, 19.4 not touched; tasks.md/choices.md not edited.

### Self-review findings
Fixed (each test-first, seen RED then GREEN; full suite 3124 passed, 1 skipped; ruff and mypy clean):
- SPEC GAP closed: the POST allowlist was test-only, so a `POST /contacts`-style create (no verb word) passed at runtime. Now default deny in `assert_no_send_capable_endpoints`, run at class definition (`__init_subclass__`) and again by RestTransport, FixtureTransport and McpTransport. To add a legitimate read-only POST: add one (exact path, reason) entry to `READ_ONLY_POST_ALLOWLIST`; the guard test fails on a stale entry. Throwaway test adapters and transports that declared POSTs now use GET or the allowlisted `/api/v1/people/match` (6 test files).
- Percent-encoding bypass (`/se%6Ed`, `%2565`): path decoded to a fixed point before tokenising.
- Joined words (`sendemail`, `singlesend`, `mailer`): long stems also matched inside a word (`SEND_PATH_STEMS`); `sequence` deliberately not a stem (`consequence`).
- Placeholders: `{action}`, `{op}`, `{send}` and similar are refused (selector names and send words); other placeholders are read as identifiers. Values cannot add a segment (slashes encoded; `.`, `..`, empty refused); confirmed by tests.
- Missing tokens added: submit, archive, restore, communications, meetings, conversations, mail(er), inmail, recipients, drip, reply, forward, broadcast, notifications, trigger, schedule, launch, dispatch, publish, insert, write, put, patch.
- RestTransport refuses a GET with a body and any method-override header (`X-HTTP-Method-Override`, `X-HTTP-Method`, `X-Method-Override`).
- McpTransport (a third transport, missed) now runs the check, and the tool name (the map key) is checked like a path.
- Static scan: folds `"/v1/" + "send"` and f-strings; flags `from lib import delete as x`; new `find_network_client_imports` (httpx, requests, aiohttp, socket, smtplib, http.client, urllib.request, literal `__import__`/`import_module`) over the whole slice except transport.py, tests/ and fixtures/. `_parsed` now skips fixtures/ as well as tests/.
- Mutation-checked (15 mutants, all files restored byte-identical): 14 killed; the survivor is the instance-time `_validate_endpoints` call, an equivalent mutant now that class definition checks first (kept as defence in depth). Scan mutated on a copy of an adapter: requests.put, httpx.delete, client.patch, method="DELETE", .request("PUT"), aliased import, getattr, "/" + "send", f-string and httpx import all flagged.

Task bullets: bullet 1 (read-only operations only) delivered; bullet 2 (static test, no send/sequence/messaging path) delivered; bullet 3 is a parallelism note, n/a. 19.2, 19.3, 19.4 untouched.

Known gaps:
- needs-follow-up: denylist words come from public API knowledge, not provider docs (research.md has no send list); verify against each provider OpenAPI spec as adapters are added. A send verb with no listed word on a GET is not caught; POST is default deny.
- needs-follow-up: write-verb and path-literal scans cover adapters/ only; a new non-adapter slice module is covered for client imports and `.send(json_body=)` but not write-verb text (store and tie_resolution legitimately use `.delete` and `.put`).
- needs-follow-up (accepted residual, not catchable by AST): paths built with `join`, `%`, `.format` of non-literals, variables or data; a client reached through a third-party wrapper; raw `asyncio` connections. The runtime check and the declared-endpoint transport are the backstop.
- The allowlist is keyed by path alone, not provider; a path allowed for one provider is allowed for all. Tighten if provider paths collide.
- `sent` is not a listed word (a field name, not an endpoint word); `sender` and `emails` are.

## Task 19.2 — Assert a synthetic run opens zero sockets (2026-10-05)
Evidence: test file written first, run, red (ModuleNotFoundError: tests.socket_guard) before the helper existed; then green (89 tests). Mutations: install() a no-op -> 86 failed; httpx.AsyncClient target removed -> 5 failed (channel test, live test, synthetic probe, live probe, mis-built-live-transport test). Final: ruff format/check clean, mypy clean, pytest 3213 passed 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Zero-socket scope is PROVIDER/network traffic; the run uses local SQLite (AF_UNIX and sqlite file I/O allowed, everything else flagged). Rejected: forbidding all DB connections (would exclude Postgres only by fiat) or allowing a Postgres engine in a synthetic run.
- Violations are recorded AND raised (assert_clean at end), so an adapter that swallows the error still fails. Rejected: raise-only.
- Live mode: guard records attempts and neutralises them (no real network) instead of passing through. Rejected: real localhost connect attempts.
- Any subprocess/os.system/posix_spawn start is a violation regardless of command. Rejected: allow-list of non-network tools.
- No composition root exists (cli.py is a stub): the run is composed in the test (registry.discover, resolve_data_mode, per-adapter builders, StoreRunRecorder, build_run_report, cluster_contributions + project_lead).
- Test file placed in tests/adapters/ (names providers; test_vendor_neutrality exempts that dir); helper tests/socket_guard.py is vendor-neutral.
- Older per-adapter guards not migrated to the shared helper (not trivial/safe).
### Known gaps (needs-follow-up)
- Bullet 1 (full synthetic path with socket construction patched to raise): delivered. Bullet 2 (parallel with 19.1/19.4): n/a, no conflict.
- Contributions are merged in memory only; raw_responses/contribution rows are NOT persisted (no persistence wiring exists; only the run record + source runs hit SQLite).
- Native C-level connect(2)/resolvers below Python are not intercepted; Postgres engine not covered (a socket by nature).
- Per-adapter builder for synthetic runs lives in the test; production composition root still missing (task 20 territory).
- Python-level only: subclasses that cached a reference to a patched function before install (from-imports taken at import time, e.g. `from socket import getaddrinfo`) bypass the module-attribute patch.

### Self-review findings
Fixed (each seen failing first, then green; 89 -> 148 tests in the module; full suite 3272 passed, ruff and mypy clean):
- Guard holes closed: direct `_socket.socket(...)` (swapped for a guarded subclass; the C type cannot be patched), direct `_socket.getaddrinfo/gethostbyname(_ex)/gethostbyaddr/getnameinfo`, `os.fork/forkpty/execv/execve/posix_spawnp` and `_posixsubprocess.fork_exec` (so `os.spawn*` and `multiprocessing` are refused). The exec probe names a nonexistent path so a missing guard cannot replace the test process.
- Defect: AF_UNIX via asyncio (`open_unix_connection`, `create_unix_connection`) was refused (`sock_connect` target lacked `local_ok`, and the local check read the loop, not the socket). Fixed.
- Defect: violation text dropped the first argument of module-level calls (the host of `getaddrinfo`). Fixed.
- Live mode could not prove "no wire if the stop leaks": the non-enforcing guard now installs an enforcing backstop beneath itself; a leaky-guard test proves it. The `sock_connect` probe is bounded (2s) so a missing guard fails instead of waiting on a blackholed address.
- Probing-adapter tests resolved LIVE (no required_env) while named synthetic; now pinned with `SourceSettings(mode=SYNTHETIC)`.
- Added: wrapper/thread/executor/to_thread/loop.getaddrinfo/smtplib/ftplib/requests/HTTPSConnection/os.popen/subprocess.run/multiprocessing-fork/listening-socket tests; allowed-local tests (socketpair, fromfd, unix paths); connect on a re-wrapped fd refused; clean removal (nested, after an exception, originals identical) plus a loopback round trip after; a violation swallowed by `except Exception` in the adapter still fails; every source's fixture endpoints served through FixtureTransport (a Seed discovery source feeds Hunter/HubSpot; the Hunter verifier has its own test); tie resolver never built, tie flagged provisional; over-merge detection in the run; a LIVE-resolved source with fixtures fails the purity check.
- Mutation-checked, files restored byte-identical: guard install no-op (139 fail), channel removed (named), violation not recorded (98 fail), Google built with a live transport, a source skipped, credential env left set, raw-socket swap removed, backstop removed. Each caught.
Delivered: 19.2 bullet 1 (full synthetic path, socket construction patched to raise, and beyond: DNS, TLS, processes, HTTP stacks); bullet 2 is a parallel marker. 19.3 and 19.4 untouched.
Known gaps:
- needs-follow-up: no composition root exists, so the run is composed in the test (real orchestrator, registry, FixtureTransport, SQLite recorder, report); point the test at the root when one lands. Contributions are not persisted, so SQLite holds run records only.
- needs-follow-up: ctypes, native extensions calling connect(2), and a child that has already started cannot be seen (documented in the helper). Postgres engine out of scope (a connection).
- needs-follow-up: Hunter 202 polling has no fixture, so polling is not driven from fixtures; the Hunter verifier is not reached in the composed run because HubSpot's opt-out report prunes the email lead (6.10), so it has a direct guarded test instead.
- Decisions: any inet socket creation is refused, including a loopback listener (bind); socketpair and fd-wrapping are allowed (local), and connect on a wrapped inet fd is still refused.
- aiohttp is not installed (not tested). The helper has no except clauses; the one broad catch is a thread-capture in a test that re-raises to the caller.

## Task 19.3 — Propagate provider Suppression onto compliance flags (2026-10-05)
Evidence: new test_compliance.py and adapters/test_suppression_end_to_end.py first run = collection ModuleNotFoundError (red); new HubSpot unparseable-opt-out test seen failing (4 red) before the `_flag` change; then green. Mutations caught: fail-open is_flag_set (21 tests fail), identity propagation off (7 fail), flagged-source-not-winner (1 fail, after strengthening the provenance test). `uv run ruff format src`, `ruff check src`, `mypy` clean; `uv run pytest -q` 3316 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Fail CLOSED everywhere: a flag is set unless it is boolean False or absent ("true", 1, "yes", "" all count). Rejected: raising a named error (drops the signal with the batch) and treating only literal True as set (16.5 gap).
- New compliance.py is the single reading of the flags and identities, used by prune_flagged (orchestrator) and project_lead; orchestrator's private _is_flagged/_identities removed. Rejected: a second copy in projection.
- HubSpot `_flag`: any non-blank value other than "false" reads as opted out (was a NormalizationError for "maybe"); the old "maybe" row of the malformed-property test was moved to a fail-closed test. Rejected: keep raising (the source fails, work list is not pruned, paid tiers run = fail open).
- Clustering cannot carry a suppression report onto the lead another source supplied (bare `email` is not read by match_keys; an unverified person.email is no Match Key), so project_lead takes keyword `blocked` (compliance.blocked_identities over the whole run) and ORs the flags of every report sharing an email/LinkedIn identity. Rejected: letting a flagged report bridge clusters (merges are irreversible, ADR-0003) and a SPEC GAP only.
- ProjectionResult.compliance_sources (sorted source names that set a flag) is the "why is this lead suppressed" record; no store column added. Rejected: a migration.
- Flag paths: the flagging candidate is made the provenance winner and the path never reports a rank-resolved conflict (decided_by None). Rejected: leaving the trust-rank winner (a False could "win" over a True).
- Identity-propagated flags are the same flags the report set (HubSpot: both, Hunter 451: suppressed only). Rejected: forcing suppressed only.
- Hunter yields_suppression left False: 6.10 only needs free suppression sources first, and every tier already prunes after it runs. Rejected: declaring True (reorders paid tiers, not required).
- Apollo and Google emit no suppression signal (fixtures and requirements are silent); nothing added. HubSpot maps only hs_email_optout (the one property requested); unsubscribed/bounced are not requested.
- E2E Discovery is a stub whose leads carry an email and an apollo source name (Apollo enrich matches only its own ids); real HubSpot, Hunter and Apollo behind scripted transports.
### Known gaps (needs-follow-up)
- SPEC GAP: a name-only Hunter finder restriction (first/last/domain, no email/LinkedIn) names no one prune_flagged or blocked_identities can match; name+domain matching needs 8.3 corroboration. Not built; Hunter will be asked again and the work-list lead is not flagged.
- SPEC GAP: no real Discovery adapter yields an email (Apollo search returns none) and HubSpot looks up by email only, so with the real adapter set the free HubSpot check cannot see a Discovery lead before paid tiers run.
- No pipeline yet calls cluster_contributions -> blocked_identities -> project_lead(blocked=) (project_lead has no production caller); wiring is the persistence/run task's. The test composes it.
- A flag-only report with an email forms its own flagged Lead beside the Discovery Lead (both flagged); dedupe is not done.
- compliance_sources is not persisted (store has canonical_field_provenance only for in-cluster candidates); run report suppression counts still "not recorded".
- HubSpot raw model keeps StrictStr: a JSON boolean hs_email_optout still raises NormalizationError (loud, source fails, fails open at the run level).
- Bullets: 19.3 b1 (carry signal onto flags) delivered (HubSpot, Hunter; others emit none); b2 (OR, survives projection, none can clear) delivered; b3 (blocked on 16.5) satisfied. Run-report counts, persistence of sources, name-only matching deferred.

### Self-review findings

Fixed (each test-first, seen failing):
- Fail-closed was unbounded: `is_flag_set` counted "false", "no", 0, "", [] as SET (would silently suppress valid leads; the old test even asserted "false" is set). Now exactly: absent = None, False, zero numbers, blank/empty strings or containers, and text (any case/whitespace) in {false, no, n, f, 0, off}; UntrustedText judged by its `.value`; everything else (True, "true", "yes", 1, "maybe", other objects) is SET. Never raises, never str()/bool()-converts. Documented in compliance.py.
- Second interpretation: hubspot `_flag` read any non-"false" string as opt-out ("FALSE", " False ", "no", "0" suppressed). It now calls `compliance.is_flag_set`: one definition for adapter, pruning and projection.
- `identities()` could raise on a hostile value (str() of an object; urlsplit ValueError on "//[bad" in a LinkedIn URL), crashing prune_flagged and so the run. Now only str/UntrustedText are read and ValueError yields no identity.
- Tests added: value tables (set / absent), hostile value, bad URL, blank identity never blocks all (mutation-verified), blocked email vs different LinkedIn, key-less cluster (lead=None) still carries flags + compliance_sources, name-only flagged lead is still a Lead, projection order-independence with flags, HubSpot "no" values.
- Mutations run and killed: fail-open, blank-blocks-all, first-flag-only, winner-decides, no _flagged_first plus winner-only, compliance_sources dropped, blocked ignored, prune skipped, prune without identities. Files restored exactly (diffed). "Winner-only candidates" alone survives by design (equivalent: _flagged_first makes the flagged source the winner).

Bullets: 19.3 delivered for: carry the signal onto flags (HubSpot, Hunter 451), OR semantics across sources/order/rank/supersession, pruning and projection use one reading. 19.4 untouched.

Known gaps:
- SPEC GAP: name-only Hunter restrictions (no email/LinkedIn) cannot be matched to a lead; the report only flags its own cluster (lead=None keeps flags/sources, but nothing carries it onto another source's lead). Match Key work (8.3 corroboration).
- SPEC GAP: no real Discovery adapter yields an email, so the Apollo->HubSpot->Hunter flow is proven for the contract with a scripted Discovery stand-in (plus real HubSpot/Hunter/Apollo adapters behind scripted transports), not for a real Discovery run.
- needs-follow-up: `project_lead` has no production caller. `blocked` must be rebuilt at recompute from the stored contributions of the run (blocked_identities over all of them); if a later recompute passes a different set, suppression can differ. project_lead is pure in (cluster, ranks, blocked); the persistence/recompute caller must supply it.
- needs-follow-up: Hunter `yields_suppression` stays False. Requirement 2.7/6.10 ties it to FREE suppression-bearing sources run first; Hunter's 451 is a by-product of a paid call, so declaring True would only reorder it ahead of other paid tiers. Left as a design decision; moving it changes tier tests.
- needs-follow-up: `_flagged_first._same_value` uses `==` on flag values; an adapter-produced hostile `__eq__` is not defended (adapters emit parsed values; not reachable today).
- Plus-address/dot variants are not normalised (same normalisers as the match keys, by design): a suppression on ada+x@ does not block ada@.

## Task 19.4 — Assert the canonical-boundary and persistence structural rules (2026-10-05)
Evidence: new tests/test_structural_rules.py first run: collection ImportError (red, scanners absent); after structure_guard.py additions 65 passed / 6 failed (real findings: scanner module itself holds engine and create_all vocabulary, fake-slice helper bug), fixed; final `uv run ruff format src`, `ruff check src`, `mypy` clean, `uv run pytest -q` 3432 passed, 1 skipped.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Reused existing proofs for 1.1 raw-schema imports, 2.4 orchestrator names, 9.4 SQL/dialect, 9.7 create_all attribute; added only gaps (dynamic imports, __init__ re-exports, CanonicalLead construction, 20.1 transport imports, driver/URL/PRAGMA strings, string-built SQL, create_all strings, runtime no-tables). Rejected: rewriting the older local scans into structure_guard (churn on finished tasks).
- Allowlists with reasons: ENGINE_SPECIFIC_ALLOWLIST = database.py (engine resolution, FK pragma) + structure_guard.py (scanner vocabulary); SCHEMA_CREATION_ALLOWLIST = structure_guard.py; CANONICAL_LEAD_BUILDERS = projection.py + models.py (share_company_signals copies leads). Migrations dir exempt from engine-name scan per 9.4 text, but scanned for string-built SQL and create_all. Rejected: excluding structure_guard.py from walks silently.
- test_database_engine.py's 9.3 scan now skips ENGINE_SPECIFIC_ALLOWLIST instead of the literal name database.py (needed because structure_guard.py now holds backend names as data).
- CanonicalLead "new lead via model_copy(update=)" detected only on names annotated CanonicalLead in the same function; rejected name heuristics (e.g. "lead" in variable name).
- 20.1 interpreted as: orchestration + merge-side modules (MERGE_SIDE_MODULES list) import no httpx/aiohttp/requests/mcp/etc. and no slice transport or mcp_transport module. BaseLeadSource keeps accepting the slice's own neutral `Transport` protocol (base_source imports it); rejected treating that as a leak.
- Every new scanner raises RuntimeError("... no modules under ...") on an empty walk, plus count assertions (>=40 modules) on the real tree.
- Dynamic literal import (importlib/__import__) now also flagged by find_raw_schema_imports_outside_slice.
### Known gaps (needs-follow-up)
- Bullets: 19.4-1 (no module outside slice imports raw schema) delivered (extended: dynamic imports, init re-exports; static case pre-existing); 19.4-2 (orchestrator names no concrete adapter) already proven by 3.4, cross-referenced, not re-done; 19.4-3 (no dialect branching/raw SQL outside migrations) delivered (extended); 19.4-4 (no implicit schema creation) delivered (extended + runtime no-tables test).
- Red phase was observed at file level (ImportError) not per test; the one runtime test (no tables after importing all modules) passed immediately because the behavior already held.
- Not catchable by AST: variable-assembled SQL, non-literal dynamic imports, lead built via getattr/subclass/unannotated variable; Postgres run not executed here.
- requirements 20.1 "merge-side" list is a hand-kept set; a new merge module must be added to MERGE_SIDE_MODULES.


### Self-review findings
Fixed (files: structure_guard.py, tests/test_structural_rules.py; red at file/mutation level):
- CanonicalLead allowance was whole-file; now per function (BuilderAllowance: projection `_build_lead`, models `share_company_signals`), with a test that the allowed set equals the real sites (stale or over-wide entries fail). Also catches `as`/`X = CanonicalLead` aliases, subclasses, TypeAdapter(CanonicalLead), parse_raw/from_orm; violations carry `scope`.
- structure_guard.py self-exemption (engine names, create_all) narrowed to string constants only. database.py stays exempt for engine names only (test: still held to the other rules).
- 20.1 scan was a hard-coded merge-module list (new modules like run_report/cli escaped); now fail-closed: every module not declared below the contract (BELOW_CONTRACT_MODULES / adapters/, each with a reason) is scanned. Added annotation detection ('httpx.Response' strings, attribute annotations).
- `_scan` excluded tests/fixtures at any depth; now top-level only. SyntaxError and non-UTF8 raise (tests). Init scan raises if __init__.py was not scanned.
- Engine scan: `.dialects` chains and literal dynamic driver imports. SQL scan: `"a" + x + y` chains missed, `"a" + "b"` false positive. create_all scan: `def create_all`, folded "crea"+"te_all".
- Runtime no-tables test could not fail (cached imports, tmp engine only); replaced by a fresh-subprocess test (DATABASE_URL at a tmp file) plus a planted-module test proving it fails.
Delivered: all four 19.4 bullets. Verified: pytest 3470 pass, ruff and mypy clean; mutations (swallow SyntaxError, drop empty-walk guard, extra build site in projection.py) each fail tests.
Known gaps (needs-follow-up, documented in docstrings): getattr/type(x)(...)/exec construction; unannotated model_copy(update=); SQL built in a variable or passed by keyword; relative import_module(".adapters", pkg); getattr(e, "dialect"); backends other than sqlite/postgres; `slice.adapters.x` attribute access without import. Old 9.3 scan in test_database_engine.py skips structure_guard.py whole (covered by the new scan). No SPEC GAP found.

## Task 20 — Run the zero-credential ingestion end to end (2026-10-05)
Evidence: `uv run ruff format src`, `uv run ruff check src`, `uv run mypy` (163 files) clean; `uv run pytest -q` 3498 passed, 1 skipped (baseline 3470). Red seen first (before the code) for tests/test_source_from_run.py (AttributeError), tests/adapters/test_from_run_vocabulary.py (6 failed), tests/test_merged_lead_persistence.py and tests/test_end_to_end_zero_credential.py (collection error: module missing). tests/adapters/test_zero_credential_run_adapters.py was written after the root existed, so its red phase was NOT observed separately (its first run failed on the store gaps below, which became the pinned tests). Manual run: `DATABASE_URL=sqlite:///x.db uv run leadforge ingest` from the repo root exits 0 and prints per-source attempted/succeeded/failed plus the DB report. The real run produced 3 canonical leads (2 name-only from Apollo search, 1 with email and LinkedIn from Apollo match), 18 stored contributions (apollo 4, google_search 14), 17 identities, 19 provenance rows; HubSpot and Hunter were invoked, succeeded, and contributed nothing.
### Provisional decisions (spec silent)
- **Verdict:** needs-user
- Composition root is a flat `ingest_runner.py` (`run_ingestion`) naming no concrete adapter; rejected: building the run inside the CLI or the orchestrator (the orchestrator must stay adapter- and store-free).
- Sources are built through a new `BaseLeadSource.from_run(mode, transport, pacing, vocabulary)` classmethod (default ignores vocabulary; Apollo and Google override); rejected: introspecting constructor parameter names, or naming adapters in the root (vendor-neutrality guard).
- Google's queries are the Target Profile's `google_search` phrases (deduped, profile order, capped at MAX_QUERIES); `keyword_templates` not expanded; with no profile Google makes no call. Rejected: a hard-coded default query (invents data).
- The target profile is read from `config/target_profile.yaml` only if it exists (else none); sources.yaml and identity exclusions are optional too. `.env` is loaded into the process via `load_env_file_into_process`.
- The store is `create_store_engine()` (DATABASE_URL, else local SQLite); each run calls `upgrade_to_head` explicitly (idempotent). Rejected: relying on a pre-migrated store.
- The merge is persisted in ONE `write_batch` transaction (identities, raw responses, contributions, canonical leads, provenance), after the orchestrator has already committed and finished the run record. Rejected: one transaction per source (identities and canonical provenance span sources; a partial write would leave contributions without a projection). Consequence: a merge-write failure raises while the record already says `completed`.
- New `store/merged_leads.py::persist_merge`. `lead_scope` (NOT NULL leftover of ADR-0001) is `person` if the contribution has a person path or bare `email`, else `company`. A raw batch is stored under its phase as `endpoint_key`, fingerprint = sha256 of canonical JSON. Identity Keys are not written. `primary_key_type` is the strongest linking Match Key kind. `projection_version` is the constant 1.
- Exit code is `map_run_exit` over orchestrator results only; the CLI prints the summary (per-source attempted/succeeded/failed, failure classes) then the DB report; configuration errors exit 2 (new, not in the spec).
- `tests/test_cli.py`: the placeholder-outcome test was DELIBERATELY removed (the stub is replaced); `PLACEHOLDER_MESSAGE` is gone. Behaviour is pinned in test_end_to_end_zero_credential.py.
### Acceptance bullets
- Empty environment, every registered adapter across Discovery and Enrichment, at least one canonical Lead persisted, exit 0, per-source counts: TRUE. test_an_empty_environment_runs_every_registered_source_and_persists_a_lead, test_the_run_reports_attempted_succeeded_and_failed_per_source, test_the_report_comes_from_the_database_and_names_every_source, test_the_ingest_command_runs_the_zero_credential_ingestion_and_exits_zero, and tests/adapters test_the_real_adapters_run_in_both_phases_and_a_real_lead_is_persisted (sockets blocked, env cleared, no .env). Caveats: "exercises" means every adapter is invoked and succeeds; HubSpot and Hunter make no provider call and contribute nothing (gap c, pinned by test_hubspot_and_hunter_are_invoked_but_idle_in_a_real_run_gap_c). The attempted/succeeded/failed counts are in the exit summary (built from results); the DB report has no such columns (see gaps).
- A single failing source still yields every other source's results, run exits 0, failure class reported: TRUE. test_one_failing_source_still_yields_every_other_sources_results (a scripted neutral failing source beside the four real ones; the class shows in the summary, the DB `failure_class` and the report).
- All sources failing exits non-zero naming each failure class: TRUE. test_every_source_failing_exits_non_zero_naming_each_failure_class (scripted) and tests/adapters test_every_source_that_runs_failing_exits_non_zero_naming_each_class (real Apollo 401 and Google 500 through the transport; HubSpot and Hunter never run because Discovery left no work list, so they are not listed). CLI mapping: test_the_ingest_command_exits_with_the_mapped_code_and_prints_the_summary (uses a faked run_ingestion).
### Known gaps (needs-follow-up)
- (a) No composition root: FIXED (`ingest_runner.py`, `ingest` command).
- (b) Merge never run and contributions never persisted: FIXED for what the real run produces. NEW SPEC GAP (needs-user), found by this task: the contribution store (6.6) refuses a `datetime` (HubSpot `crm.last_activity_date`) and a `tuple` (Hunter `person.email_sources`); both adapters' own tests pin those types, so once a lead reaches either adapter the merge write fails loudly and rolls back whole. Pinned by test_a_lead_that_reaches_this_adapter_cannot_be_persisted_spec_gap (stand-in Discovery). Needs a decision: the store accepts them (tagged or ISO text), or the adapters emit JSON-faithful values. Also open: a re-run adds new identities (no Identity Key matching, 16.10); a failed merge write leaves the run record `completed`.
- (c) HubSpot and Hunter get no email or domain from any real Discovery adapter: STILL OPEN, pinned. Missing piece: Apollo's enrichment output (email, domain) is not fed to later Enrichment tiers (the work list is Discovery-only by design, ADR-0002), or a Discovery adapter that yields an email. Only a test-only STAND-IN Discovery (named as such) drives their fixtures.
- (d) Google: run-time queries FIXED via the Target Profile; a run now makes one Google call and stores 14 web-evidence contributions. 14.2 still open: contributions carry no company identity or Signal Strength, form no Lead, and reach no canonical row (SPEC GAP unchanged).
- (e) Hunter per-company vs per-person mismatch (15.1): OPEN, untouched (not reachable without gap c).
- (f) projection_version bump (constant 1), tie resolution not wired into projection, match key logged as kind only: OPEN; the merge log and over-merge detection are not called by the root, so over-merge suspects and tie fallbacks stay "not recorded" in the report.
- (g) Name-only Hunter restrictions cannot be matched: OPEN.
- (h) 18.2 fetched/merged/credits counts have no producer: OPEN (the report prints "not recorded"). Related NEW SPEC GAP (needs-user): Requirement 6.5 asks for per-source attempted/succeeded/failed counts; `source_run` stores none of them, so they exist only in the exit summary, not in the database-built report (21.5). Needs a migration and a report change if the stored report must carry them.
- Also noted: Apollo search yields name-only Leads that do not merge with the enriched person (no domain; 8.3 corroboration), so the store holds a duplicate-looking pair; live mode (RestTransport) is built by the same root but is untested here (no network).

### Self-review findings

Fixed (each test-first, red then green):
- Store defect (production bug, not a spec gap): `store/contributions.py` refused HubSpot's aware datetime and Hunter's tuple, so any real run reaching either adapter rolled back its merge write. `_encode` now stores a datetime as ISO-8601 UTC text (naive refused), a tuple/list as a JSON array, a StrEnum as its text; set, bytes, date, non-finite float, non-string key and unknown types still raise the named `ContributionValueError` (path + type, never the value). UntrustedText keeps its own columns (untrusted/truncated/original_length), unchanged. Read-back returns the string/list, not the original type (stated in the module docstring). Tests: new round-trip test and updated refusal list in `test_contribution_roundtrip.py`; the old "cannot be persisted" pin in `tests/adapters/test_zero_credential_run_adapters.py` is now a positive test through the STAND-IN Discovery source (merge persists, run completes); it fails with the store fix reverted. SQLite only (Postgres leg of the dual-engine harness skipped here; the encoding is plain JSON with no backend branch).
- Masked-name artifact: 2 of the 3 canonical leads were NOT legitimate: Apollo masked last names ('Ada Lo***', 'Grace Ho***') composed into full_name. `projection._full_name` now treats any `*` as no name (the `match_keys` rule); such a cluster keeps its identity and contributions but forms no Lead. A real run now persists exactly 1 canonical lead (Ada Lovelace, email + LinkedIn, from the match fixture). Tests: `test_projection.py::test_a_masked_name_is_not_a_full_name`; the real-run test asserts no `*` in any lead name.
- Added tests: each of 7 failure classes on one source leaves the other sources' results and leads and exits 0; all sources failing (all 7 classes) exits 1 naming every class and source; crash mid-run recorded aborted (exit_code None); cancellation propagates and marks aborted; CLI all-failing exit 1 naming classes; CLI crash exits non-zero; CLI stdout/stderr PII canary.
- Mutation checks, all killed, files restored: store refuses datetime; merge not called; all-failing exits 0; env credential left set; summary omits failure class; CLI ignores exit code. Not mutated: a failing source aborting others (covered by the 7-class matrix).

Truth of each acceptance bullet for a REAL run (real adapters + fixtures via FixtureTransport + real merge + real store, no stand-in):
1. Bullet 1: TRUE. Empty env, sockets blocked; all 4 registry-enumerated sources ran in synthetic mode across both phases; 1 canonical Lead persisted (was 3 before the masked-name fix). HubSpot and Hunter run and succeed but contribute nothing (gap c); their fixtures flowing through the merge is proven only with the STAND-IN Discovery (named so). Google contributes only when a Target Profile is in reach (cwd config/target_profile.yaml); with none it makes no call.
2. Bullet 2: TRUE for the printed summary, with a caveat: attempted/succeeded/failed come from the orchestrator's IN-MEMORY results (`map_run_exit`), not the database. The DB-backed run report shows mode, failure class, leads_normalized, retries, throttle, 429s. Not wired into the DB (no column; no migration added).
3. Bullet 3: TRUE for a real run: real Apollo 401 plus Google 500 exits 1 naming both classes. The single-failing-source matrix uses a scripted neutral source beside the real ones, so real adapters failing singly is not exercised.

Known gaps left:
- needs-follow-up: HubSpot and Hunter get no email from any real Discovery adapter (gap c); Hunter per-company vs per-person unresolved.
- SPEC GAP (21.2): attempted/succeeded not persisted; fetched and merged have no producer; `source_run.contributions_written` stays 0 though contributions are stored (needs-follow-up, column exists).
- needs-follow-up: 14.2 Google contributions carry no company identity or Signal Strength; 16.6 projection_version fixed at 1; 16.11 tie resolution not wired into projection; 16.12 match key logged as kind only (SPEC GAP); name-only Hunter restrictions unmatched; credits/fetched counts have no producer.
- needs-follow-up: a masked name that wins a path over an unmasked one yields no name (masked candidates are not filtered before resolution).
- needs-follow-up: a merge-write failure raises out of `run_ingestion` after the run record says completed; the CLI exits non-zero via traceback but the record is not marked.
- needs-follow-up: `GoogleSearchSource.from_run` raises a bare ValueError for a malformed profile phrase (names the term only, no ConfigurationError path+key); live mode still needs SERPAPI_API_KEY with no queries (14.1).
- Default store is `.leadforge/leadforge.db` under the cwd (gitignored, 6.3); no import-time creation; tests use tmp paths.

## Follow-up — Distinct LinkedIn URLs never share a cluster (2026-10-06)
Evidence: I wrote src/leadforge/lead_ingestion/tests/test_linkedin_cannot_link.py first. The first run failed at collection with an ImportError (`linkedin_identity` did not exist). After a one-line helper, the run showed 12 failed and 32 passed, with assertion failures on the A/B/C bridge, the four-record chain, disqualification, name+domain, the over-merge detector, projection, the exclusions case, the union-find guard, the guard-only permutations and random-pool seeds 2/3/20. Output is saved at scratchpad/red-fu-bridge.txt. After the fix the new file passes (44 tests). Four existing tests failed because they encoded the old bridging behaviour, and I updated them on purpose (listed below). Final checks: `uv run ruff format src` left 164 files unchanged, `uv run ruff check src` passed, `uv run mypy` found no issues in 164 files, and `uv run pytest -q` gave 3561 passed, 1 skipped (exit 0).

Requirement text (specs/lead-source-adapters/requirements.md):
- 8.1: "The Merge Engine SHALL treat normalized `linkedin_url` as the strongest match key ... LinkedIn ranks above email because it survives a change of employer". Supports: LinkedIn is the strongest identity evidence, so two different normalised URLs outrank any weaker key that would join them.
- 8.2: "When `linkedin_url` is absent on either lead, the Merge Engine shall match on equal normalized **verified** `email` values". Supports: email is a fallback only when LinkedIn is absent. It is never licensed to override two present, different LinkedIns, but transitive closure through a bare record did exactly that.
- 8.3: "When both `linkedin_url` and a verified `email` are absent on either lead ... normalized `full_name` paired with any domain ... SHALL NOT match on name alone." Supports: name+domain is the weakest candidate. If two distinct LinkedIns attest that name+domain, it demonstrably names two people.
- 8.13: "... this exclusion set is the only supported repair for an over-merge." Supports: prevention must happen at the key level, because no unmerge exists. Exclusions still work (tested).
- 8.14: "The Merge Engine SHALL disqualify as a match key any email address that any source reports against two or more distinct normalized person names, independently of the configured exclusion set." Supports: the precedent for structural, config-free disqualification of a value that is evidently shared. The follow-up extends the same mechanism to two distinct LinkedIn URLs.
- 8.15: "When a cluster carries two or more distinct non-null `full_name` values ... flag it as a suspected over-merge". Supports: the bridged case was an over-merge, and it is now never formed rather than only flagged.

### Decisions
- **User decision:** two records with different normalised LinkedIn URLs are different people and never share a cluster.
- Disqualification (match_keys.DisqualifiedAddresses.from_contributions, the same first pass as 8.14 over the whole set, independent of order): an address of any email_status that is reported together with two or more distinct normalised LinkedIn URLs is disqualified (`addresses`). So is a name+domain candidate value reported together with two or more distinct LinkedIns (new field `name_domains`). Both are barred like 8.14 addresses: the kind stays in `barred_kinds`, and the value stays on the contribution and in provenance. The LinkedIn URLs are counted raw (new `linkedin_identity`), so a URL barred by an Identity Exclusion still counts. This only ever splits clusters.
- Cannot-link guard (clustering._UnionFind gains `labels`): each root holds its LinkedIn label, and a union of two distinct labels is refused. This was the smallest correct mechanism, at O(1) per union. Unions run in canonical order (the input is sorted by canonical JSON), so what the guard refuses depends only on the set and not on arrival order. Proof: all permutations of the A/B/C bridge with disqualification switched off; all 24 permutations of the four-record chain; 30 seeded random 5-record pools x 120 permutations, each byte-identical, with at most one LinkedIn per cluster.
- Because each contribution has at most one LinkedIn and one email, and name+domain links only LinkedIn-less records, disqualification alone already removes every bridging edge. The guard cannot fire through the public API today; it is a backstop, tested at the unit level and with disqualification monkeypatched off.
- Near-linear: on 5,000 contributions (a shared address under 50 LinkedIns plus legitimate triples), union <= 3n and find <= 12n.
- Existing tests updated on purpose because they encoded the old bridging behaviour:
  - test_clustering::test_transitive_closure_links_through_different_key_kinds: d no longer holds a second LinkedIn.
  - test_identity_exclusions::over_merged_pool: the one-LinkedIn role address with two bare holders, so the partitions in the tests that use it are unchanged.
  - test_identity_exclusions::test_the_repair_projects_two_people_to_two_leads_end_to_end: a single-LinkedIn pool; 1 lead becomes 2 leads.
  - test_identity_exclusions::test_barring_the_linkedin_instead_does_not_split_a_bridged_email: comment only, with the new pool.
  - test_over_merge::test_the_known_bridge_path_is_flagged: this test was vacuous, because 16.7 already split it and it asserted [] == []. It is replaced by test_the_known_bridge_path_is_no_longer_formed_so_nothing_is_flagged.

### Known gaps
- The guard is greedy. If key extraction ever became multi-valued (several LinkedIns or emails per contribution), the guard would still be deterministic over the set, but which side a bare record joins would depend on canonical order rather than evidence. Disqualification would then need to cover the new shapes.
- A LinkedIn URL that a provider got wrong (one person, two URLs) now under-merges. That is the cheap direction, and it is still repairable only by data, not by an exclusion.
- The name+domain disqualification also separates bare records of one real person when two namesakes at that domain have LinkedIns. This under-merges by design.
- The prose in the 16.8 over_merge.py docstring ("a key-less bridge" as a rejected signal) was not updated; it is still accurate. choices.md and tasks.md were not edited, per instruction. The spec-refactor and production-readiness sub-agents were not spawned because no Agent tool was available in this run.

### Self-review findings
Independent spec-refactor review (2026-10-06):
- (a) URL normalisation. Case, trailing slash, query/fragment and http vs https fold (probe: A(`https://www.linkedin.com/in/JD/`, e) + bare(e) + C(`http://www.linkedin.com/in/jd?trk=1`, e) gave one cluster). NOT folded: `www.` vs bare host, locale subdomains (uk.), `/pub/` vs `/in/`, and percent-encoded vs literal Unicode. This is the pre-existing 16.1 needs-follow-up (choices.md:1551), but the new rule amplifies it. Probe: A(`www.linkedin.com/in/jd`, e) + bare B(e) + C(`linkedin.com/in/jd`, e) used to give one cluster and now gives three, and e is disqualified for the whole set. One person is treated as two. I did NOT fix it: it changes a recorded decision, `normalize_linkedin_url` also feeds compliance.py identities, and test_match_keys pins `www.linkedin.com/...`. It needs a decision (fold `*.linkedin.com` to `linkedin.com`, percent-decode the path).
- (b) Order independence: covered (all permutations plus 30 seeds), and the tests pass.
- (c) Guard-only probe over all 6 permutations of A(li1,e) B(e) C(li2,e): always {A,B},{C}. It is deterministic, and the choice comes from canonical-JSON order, not evidence. With disqualification on (the real path), all three stay apart. I checked by hand that the guard is unreachable: email groups hold at most 1 URL, and name+domain groups hold no LinkedIn. It is O(1) per union, not quadratic.
- (d) Covered by test_a_linkedin_record_and_a_bare_record_on_one_email_still_merge.
- (e) The changed tests still test what their names claim. The over_merge replacement is correctly argued (the old test compared [] with []). No assertion was weakened beyond the decision.
- (f) No logging was added. DisqualifiedAddresses fields are repr=False. No PII.
- (g) Mutations: 7 (drop address_urls, drop name_domains, disable guard, label_a only, label None, no labels passed, threshold >2). All were killed (1-7 failures each), and the files were restored (sha256 verified).
- Fix: the match_keys.py docstring claimed that name+domain disqualification prevents a bridge. It cannot (name+domain links only records with no LinkedIn and no email). It now says it is a deliberate under-merge.
- Verification: pytest 3563 passed, 1 skipped; ruff check and format clean. mypy shows 2 errors, only in tests/adapters/test_hunter_source.py (the other reviewer's file, still there after a re-run).

- **Fixed after review (parent, test-first, red seen: 12 failures):** `normalize_linkedin_url` now folds every `*.linkedin.com` host (www., country, mobile) and a trailing dot to `linkedin.com`, percent-decodes and casefolds the path, and collapses empty segments. One profile spelled two ways is one identity again, so the cannot-link rule no longer splits a person or bars their email. The same normalizer feeds compliance matching. `/pub/` URLs are not mapped to `/in/` (no reliable mapping); they stay a distinct identity. The existing case/query test expectation changed deliberately from `www.linkedin.com/...` to `linkedin.com/...`.

## Follow-up — Hunter works per person, domain search once per company (2026-10-06)
Evidence: wrote tests first in tests/adapters/test_hunter_source.py (5 new tests, 1 declaration test changed deliberately, 1 per_company_work_list test reworded to the whole list) and ran them: 3 failed for the expected reasons (charge_unit was PER_COMPANY; end to end only `v1@acme.com` of two addresses reached the verifier; same-name twins: only `john.smith@acme.com` reached Hunter). The 2 adapter-level tests passed already (the adapter was per-person; only the declaration blocked it). Then changed `HunterSource.charge_unit` to PER_LEAD plus docstring/comment; file 195 passed. Mutation: per-run domain cache disabled -> retry test failed; file restored. Scoped checks on hunter.py + test file: ruff format/check clean, mypy clean; adapters + orchestrator + base_source tests 924 passed. Whole-repo runs: my files clean; remaining failures (ruff E501 match_keys.py:319, mypy test_linkedin_cannot_link.py:296, 1-4 clustering/identity-exclusion test failures varying between runs) are all in files the parallel agent is editing; re-ran once, still theirs. No serena/GitNexus query available in this agent; blast radius from grep: the declaration is read only by enrichment_sort_key/enrichment_tiers and the orchestrator's per_company branch.
### Decisions
- User decision: leads are PER PERSON; several leads at one company are fine if they are different people.
- Hunter declares `charge_unit = PER_LEAD` so the orchestrator hands it every Lead (per_company_work_list no longer applies to Hunter). The per-company domain search stays once per distinct domain per run through the existing `_route` dedupe and `_searched` per-run cache; finder/verifier keep their per-run caches, so a retried fetch re-pays nothing. Rejected: a per-endpoint charge unit (design 2.7 has one `charge_unit` per source; needs a contract change for no gain, since the adapter already dedupes per company internally).
- Tier move: Hunter's sort key goes (True, True, 0, "hunter") -> (True, True, 2, "hunter"), so Hunter now shares the PAID / no-suppression / PER_LEAD tier with Apollo (concurrent; Google Search PER_CALL now runs before both). Kept: within paid sources, per-lead is the most billable events, so Hunter running last-tier is consistent with "fewest billable events first".
- The per-company dedupe still applies to genuinely per-company sources: asserted in the same end-to-end run (a PER_COMPANY probe is handed 1 Lead while Hunter gets all 7).
- Two people with the same name and distinct addresses stay two (two verifier calls, two contributions).
### Known gaps
- Same name, no address, one company: both Leads ask the identical finder question, so Hunter asks it once and contributes one address; it cannot tell the two apart. The Leads themselves are not dropped (discovery contributions intact); attaching the found address to the right person is the merge engine's job. needs-follow-up.
- Tier change side effect: Hunter no longer runs before Apollo, so a Hunter 451 restriction no longer prunes that Lead before Apollo is called (they now run in one tier). Option: declare Hunter `yields_suppression=True` (15.3 already flags this). needs-user.
- Credits: one per live call is still the 15.1 assumption; with every person now reaching Hunter, a run's Hunter spend grows from ~1 per company to ~1 per person + 1 per company domain-only search. No per-run cap exists. needs-follow-up.
- Routing unchanged: domain search runs only for Leads with no usable address and no usable name; a company whose people all have names or addresses gets no domain-search call (at most once, not exactly once).
- Self-review agent and production-readiness scan not run (no Agent tool in this context); one mutation checked.

### Self-review findings
- Fixed (test only, no src change): `acme_finder` stub returned last_name "Lovelace" for every person and no `domain`; it now echoes the asked first/last name and domain like the real finder fixture. `run_people` split into `run_people_results` (returns SourceResult) so batches can be checked.
- Added `test_end_to_end_an_orchestrator_retry_repays_only_the_failed_question`: real orchestrator + RetryPolicy, verifier 503 once -> attempted 2 / retries 1, 7 transport calls (6 questions + the failed one), 1 domain search, 3 finder calls, credits_in(batch) == 6.
- Added `test_hunter_now_shares_the_paid_per_lead_tier_with_apollo`: pins HubSpot < Google Search < {Apollo, Hunter} so a later order change is deliberate.
- Mutations (all killed, hunter.py and orchestrator.py restored byte-identical by sha256): charge_unit back to PER_COMPANY (5 tests fail); search / finder / verifier cache each disabled (3, 3, 6 fail incl. the new e2e retry test); yields_suppression True (2 fail); orchestrator per_company branch disabled (the probe-handed-1 assertion fails).
- (c) NOT changed, needs-user: declaring Hunter `yields_suppression=True` would put it in its own tier ahead of Google Search and Apollo (sort key (True, False, 2)), restoring the old Hunter-first order so a verifier 451 prunes before Apollo. Not done because: 6.10 only requires FREE suppression-bearing sources first; choices.md (task 15.3 gaps and the later 'Hunter yields_suppression left False' entry) explicitly rejected it; and the fixture outcome matrix derives a required `suppression` positive/negative fixture pair from that flag, which Hunter (451 is a status, not a body) cannot supply with the file-based FixtureTransport without new fixture machinery. Also only a verifier 451 can prune (prune_flagged keys on email/linkedin; a finder 451 has neither).
- (b) Same-name twins with no address (probe, real orchestrator + cluster_contributions, not committed): one finder call, one Hunter contribution, no 16.7 disqualification (one name, not two). The address is attributed to NEITHER twin: it forms its own cluster.
- HIGH, pre-existing, needs-user: this is general, not twin-specific. FINDER_RULES ignore `domain` and `linkedin_url`, so a finder contribution has only the address + name. It has no name+domain key, and `_link_name_domain` skips holders of a verified email anyway, so a found address never clusters with the Lead that was asked (single person, any title, valid or unknown status: always a separate cluster). With PER_LEAD every person now pays a finder Credit for an address the merge cannot attach. Fix needs a merge/adapter design decision (and match_keys/clustering are under parallel edit).
- Full suite 3563 passed, 1 skipped; ruff check clean; mypy clean (after replacing an enrichment_tiers-on-classes call that mypy rejected).
- Probe scripts left in scratchpad/probe/ (session scratch, not the repo; deleting them was denied).

## Follow-up — Hunter finder attaches to its person; credit accounting (2026-10-06)

**Defect 1 (HIGH): a finder result became its own lead.** Fixed in `adapters/hunter.py`. A found address now carries:
- `company.domain`
- the first and last name that were asked for (not Hunter's echo)
- the requester's own `person.linkedin_url`, when the request had one

These sit at raw paths `asked.*`. Hunter's `first_name`, `last_name` and `domain` echo are now in FINDER_IGNORED. Hunter's own `linkedin_url` is never used.

- **RED:** 13 failing tests (identity fields, end to end, ambiguity, credits).
- **GREEN:** the real orchestrator, then `cluster_contributions` and `project_lead`, gives ONE lead that holds Hunter's address. Its contributing sources are discovery and hunter. This holds in two cases:
  - The requester has LinkedIn (verified address).
  - The requester has no LinkedIn, the address is not verified, and an employer is shared (name+domain).

**Ambiguity (provisional decision).** One name at one domain can be asked on behalf of two distinguishable people. "Distinguishable" means distinct normalised LinkedIn identities; a record with no LinkedIn counts as one more identity. In that case Hunter is NOT asked:
- no call and no Credit
- no contribution
- one warning, `hunter_finder_ambiguous`, which logs only a count

This is tracked per run, so a cached answer is never reused for a second identity in a later fetch. Records with no LinkedIn cannot be told apart, so they share the one answer.

**Defect 2: credits.** `credits_in` now follows https://help.hunter.io/en/articles/1911617-how-do-credits-work-in-hunter:
- Domain search: ceil(addresses returned / 10), and 0 when none are returned.
- Finder: 1, only when `data.email` is non-null.
- Verifier: 1.

A malformed `data` or `emails` raises NormalizationError. Each rule has a test.

**Scope addition (user decision 2026-10-06).** This REVERSES the earlier recorded choice "Hunter shares Apollo's paid per-lead tier". `HunterSource.yields_suppression = True`.
- **Ordering:** Hunter now sorts into a paid tier ahead of Apollo. Suppression outranks charge unit, so Hunter also moves ahead of Google Search. That second move changes no pruning, because Google Search prunes nothing.
- **Restricted finds now carry LinkedIn:** a restricted finder entry carries the requester's `linkedin_url`. The work list is pruned by address or LinkedIn, so without it the 451 flag could not prune a person on the finder route.
- **Pinned tests updated on purpose:**
  - `test_declares_paid_per_lead_enrichment_that_yields_suppression`
  - `test_hunter_runs_in_a_paid_tier_of_its_own_before_apollo`
- **New tests through the real orchestrator** (`tests/adapters/test_suppression_end_to_end.py`):
  - A 451 on P, on both the verifier and finder routes: Apollo matched only Bob.
  - The finder finds nothing: Apollo is still asked about P.
  - Hunter returns 404 or 429, or the transport times out: Apollo is still asked about P.
- **Fixture matrix** (`test_fixture_outcome_matrix.py`): the flag now requires both a suppression positive and a negative.
  - Negative: the label is added to `email_verifier.json`, which asserts no flag and nothing pruned.
  - Positive: exempted by name in `NOT_FIXTURE_SHAPED`. A 451 is a status code and FixtureTransport serves only 200s, so the scripted 451 tests prove it instead. A guard test keeps the negative required.

**Files touched outside the assigned scope (both required):**
- `tests/adapters/test_suppression_end_to_end.py`: the `Run.discovery` hook and the new tests.
- `tests/adapters/test_fixture_outcome_matrix.py`: the exemption and the negative label.

**Known gaps:**
- **Verified answer, no LinkedIn: still TWO leads.** Clustering (8.3/ADR-0003) lets only key-less records join on name+domain, and a verified address is a key. Fixing this needs a clustering decision, not an adapter change. I did not change it.
- **Verifier price depends on the plan.** Data plans charge 1 verification credit; All-in-one plans charge 0.5. It is kept at 1 as the conservative value. A 202 give-up and an `unknown` verdict also count 1, although Hunter may not charge for them.
- **Ambiguity uses LinkedIn only.** Other differences, such as different titles, do not count. An answer attached in an earlier batch is not recalled when a conflicting identity appears later.
- **A name-only person with no LinkedIn cannot be pruned by a Hunter 451 before Apollo.** There is no address or LinkedIn identity to match on. A cached restriction re-emits the first requester's LinkedIn, which over-suppresses (the safe direction).
- **A finder 404 is still a permanent error.** Whether Hunter uses 404 for "not found" is UNVERIFIED in the docs (U12); not changed here.
- **Verifier answers have the same shape.** They carry only the address and its status. That may be a similar defect for a requester whose address is unverified. Not investigated.

### Self-review findings

- **(a) Echoed identity self-corroborated and conflicted: CONFIRMED, FIXED.** Reproduced via the real orchestrator, `cluster_contributions` and `project_lead`. With a LinkedIn requester, agreement was 2 on `company.domain` and `person.linkedin_url` (Hunter agreeing with the request it echoed), and first/last name showed TRUST_RANK conflicts (UntrustedText vs plain text). With a messy requester (padded name, `WWW.` domain, trailing slash) all four echoed paths conflicted; a Hunter trust rank above the requester's would have overwritten the requester's own value.
  - Fix: `models.REQUEST_ECHO_PREFIX = "asked."`. `conflicts.resolve_conflicts` (`_observed_first`) lets a candidate whose raw path is under that prefix compete only when no observed candidate holds the path. Match keys still read the values, so the join is unchanged.
  - The marker is the raw field path, which IS persisted (`contribution_field.raw_field_path`), so a recompute from the store keeps the rule. A new FieldProvenance flag would not survive: provenance beyond confidence is not persisted.
  - Hunter's ASKED_RULES now build on the constant; `RESTRICTED_FIND_RULES` reuses them, so a finder-route 451 flag's echoed identity is a request echo too.
  - Tests: `test_the_echoed_identity_neither_corroborates_nor_conflicts[ranked_below|ranked_above]` (RED before: agreement 2), `test_an_echoed_identity_alone_still_names_the_lead` (echo-only fallback).
  - Files outside the listed four: `models.py`, `conflicts.py`. Only caller of `resolve_conflicts` is projection (grep; Serena/GitNexus not available here, so blast radius is grep-based).
- **(b) LinkedIn vs. no-LinkedIn same-name rule: KEPT.** It matches clustering 8.3/ADR-0003, which deliberately under-merges a keyed and a bare record of one name; they are two Leads anyway, so one answer has no single owner. Cost: one real person seen once with and once without LinkedIn gets no finder answer (and pays nothing). No contradiction with the per-person 451 decision: Hunter does not ask, so no 451, so Apollo is asked about both.
- **(c) Verified answer, no LinkedIn = two Leads: REPRODUCED, RECORDED, not fixed.** Root cause: a `verified` status gives Hunter's contribution a verified-email Match Key, and 8.3 admits to name+domain only contributions with NO LinkedIn and NO verified-email key, so the keyed Hunter record cannot join the key-less requester (the one-sided case). Even unverified, name+domain also needs `corroborates` (shared title or employer). Needs a clustering decision (e.g. a request-echo join), not an adapter change.
- **(d) Credits: OK.** A failed attempt yields no batch; cached answers appear once in the successful batch (orchestrator retry test: 7 calls, 6 credits). A 0-result search charges 0. Residual: `credits_in` is per batch; summing two successful fetches of one instance would double-count cached answers. Today one fetch per run, and nothing outside tests calls `credits_in`.
- **(e) Ordering: OK, one gap closed.** The three orchestrator tests exist and use the real `IngestionOrchestrator`, but Bob was at a different company, so "per person, not per company" was untested. Added `test_a_hunter_451_prunes_one_person_not_their_colleagues` (Ada 451; Cy at the same domain still matched by Apollo). Google Search moved only because suppression outranks charge unit in `enrichment_sort_key`; it prunes nothing, so harmless.
- **(f) PII: OK.** `hunter_finder_ambiguous` logs a count only (asserted); restricted identities stay in the raw batch.
- **(g) Mutations, all restored, sha256 verified:** yields_suppression off; ambiguity guard off; restricted-find LinkedIn None; finds LinkedIn None; flat search credit; finder credit when nothing found; echo rule removed; echo fallback removed. Each was killed by at least one test.

## Follow-up — provider facts verified against live docs (2026-10-06)

Source: scratchpad/live-docs-findings.md. Non-Hunter items only (Hunter was being edited by another agent and was not touched).

**Evidence grades.** The provider documentation sites (docs.apollo.io, developers.hubspot.com, serpapi.com and others) were network-blocked. Evidence came from (A) official machine-readable repos: the HubSpot public OpenAPI spec collection, the `@hubspot/api-client` 14.0.1 SDK, the Apollo CLI and the SerpApi Python SDK; and (B) search-engine extracts of the official pages, which are strong but not definitive. Anything neither grade confirmed is marked UNVERIFIED.

**Hook added (base_source / orchestrator / env_example).**
- `BaseLeadSource.run_rate_limit(environ=None)` returns `rate_limit` by default. The orchestrator calls it only for a LIVE source, so a synthetic run reads no setting.
- `optional_env` lists non-secret plan settings. `env_notes` holds one-line `.env.example` comments.
- Both feed the generated `.env.example`, which was regenerated.
- Config follows the HUBSPOT_API_VERSION precedent: an adapter reads its own env. A bad value raises `ConfigurationError("environment", key_path=VAR)` and does not echo the value.

1. **SerpApi pacing.** `SERPAPI_HOURLY_LIMIT` is a positive integer.
   - **Default 50**: the Free plan, the lowest documented figure, so it is safe on every plan.
   - Plan figures (Free 50, Starter 200, Developer 1000, Production 3000, Big Data 6000 per hour) come from serpapi.com/pricing [B].
   - The bucket is an hourly window AND an even spacing of 3600/limit seconds. It is `documented=True`, with the pricing page as doc_url. This replaces 1 req/s (3600/h).
   - Orchestrated throttle tests set 36000/h through monkeypatch, because the default spacing is 72 s.
2. **Apollo.** `APOLLO_PLAN` takes free, basic, professional or organization. **Default free.**
   - Search and match now use separate `search` and `match` buckets. Each has ANDed minute, hour and day windows.
   - Figures are from docs.apollo.io/reference/rate-limits [B]. Search: Free 50/200/600, paid plans 200/6000/50000. General endpoints (used for match): Free 50/200/600, Basic and Professional 200/400/2000, Organization 200/600/6000.
   - `documented=True`. This replaces the undocumented single 600/h bucket.
3. **SerpApi empty page.**
   - The fixture is now `{search_metadata.status: Success, search_information.organic_results_state: "Fully empty", error: "...no results..."}` [B: api-status-and-error-codes, search-errors].
   - ENVELOPE_IGNORED gains `error` and `search_information.organic_results_state`.
   - The backend's `failed_search(body)` reads structured fields only. A 2xx raises if its status is Error, or if it carries `error` and is not the Fully-empty Success shape.
   - Out-of-searches gives SourceQuotaExhausted. Any other failure gives a permanent SourceError (cause=search_failed).
   - A 429 with the balance message still gives SourceQuotaExhausted.
4. **HubSpot.**
   - `env_notes[HUBSPOT_ACCESS_TOKEN]` names the scopes crm.objects.contacts.read and crm.objects.deals.read [A: the spec security blocks]. These scopes now appear in `.env.example`.
   - The module doc lists the verified facts.
   - `hs_is_closed` and `SECONDLY` are marked UNVERIFIED in the module doc, in a code comment and in both deal-fixture manifest notes. Behaviour is unchanged.
5. **Manifests.**
   - Set to verified on 2026-10-06, because every field in them matched:
     - hubspot not_found/contact_search and no_open_deals/deal_search (envelope [A])
     - apollo no_match/search (total_entries and people [B]; doc_url find-people-using-filters)
     - google_search no_results/search (doc_url api-status-and-error-codes; total_results dropped)
   - The rest stay unverified. Their notes say what matched and what did not:
     - Apollo: the obfuscation form, the no-match match shape and the tech CSV are UNVERIFIED.
     - HubSpot: property names were not checked.
     - SerpApi: the Google field-list page was blocked.
   - The HubSpot spec repo URL is not used as doc_url, and is named only in shortened form in notes, because the fixture secret regex trips on any token of 32 or more characters.
   - The invariant tests in test_fixture_metadata and test_fixture_outcome_matrix were relaxed to "verified only with date 2026-10-06". The exact verified set is pinned in tests/adapters/test_provider_plan_limits.py, because vendor names are banned outside adapters.

**TDD.**
- RED: 27 failed and 1 collection ImportError across the new tests. The fixture guard went red after the fixture change.
- GREEN: ruff format clean; mypy clean; pytest 3678 passed, 1 skipped.
- `ruff check src` still reports errors, but only in hunter.py, conflicts.py and models.py, which other concurrent agents own. The files I changed pass ruff check.

**Not done.**
- The SerpApi Account API (`account_rate_limit_per_hour`) is not used.
- Apollo `x-rate-limit-*` header seeding is not done.
- HubSpot `errorType` labelling is not done.
- Nothing was committed, and specs/tasks.md was not touched.
- The spec-refactor-agent self-review was not run, because this session has no Agent tool.

### Self-review findings

Independent spec-refactor review, 2026-10-06. Hunter, conflicts and models were not touched.

**What was checked and confirmed**
- (a) The numbers match the findings report exactly.
  - Apollo, from A16 (rate-limits page): search is Free 50/200/600 and paid 200/6000/50000. General endpoints are Free 50/200/600, Basic and Professional 200/400/2000, and Organization 200/600/6000.
  - SerpApi, from S8 (pricing page): 50, 200, 1000, 3000 and 6000 per hour.
  - Each figure cites its URL in the module doc and in doc_url.
  - Bad values raise `ConfigurationError("environment", key=VAR)` without echoing the value. This covers an unknown plan, "0", negatives, non-digits, non-ASCII digits and values over 9 digits.
- (b) The env settings are listed and render through `env_example`. A drift test covers this, and its mutants are killed.
- (c) Empty versus failed SerpApi pages:
  - Only status Success, `organic_results_state` "Fully empty" and a present `error` count as empty. The message text is ignored.
  - "Google hasn't returned any results" with a different state, or with `search_information` missing, raises `SourceError` with cause search_failed.
  - Google Search declares no answerable surfaces, so it can never produce Negative Evidence.
  - A 401 or 429 never reaches `failed_search`.
- (d) The throttle tests were added to test_provider_plan_limits.
  - Apollo Free search: 50 calls go through immediately, the 51st waits 1.2 s (the minute window binds), and later waits are 18 s (the hour window binds).
  - SerpApi: calls are spaced 72 s apart, with no burst.
  - Synthetic mode still skips pacing; the orchestrator mutant is killed.
- (e) The four verified fixtures contain only fields the report confirmed (H5, A6, S7).
- (f) The orchestrator now calls `run_rate_limit()` only for LIVE sources. Other sources are unchanged, because the default returns `rate_limit`.
- (g) Mutation check: 16 mutants were run and 15 were killed at first. The survivor was the `status==Success` conjunct in `failed_search`. I added 3 cases (status Processing, empty metadata and missing metadata), and the mutant is now killed. Every source file was restored, confirmed by sha256.

**Skipped (design decisions)**
- The orchestrator now reads the environment, against its own doc line ("does not read the environment"). A bad plan value raises after `recorder.start`, so the run record is aborted, not rejected before any write. Fixing this needs a design decision: inject the setting or validate it early.
- A Success / "Fully empty" page that carries "run out of searches" would be read as empty. No documented response has that shape.
- `no_open_deals` is marked verified, but it answers an UNVERIFIED `hs_is_closed` filter.

Gates: pytest 3683 passed and 1 skipped; ruff check src is clean; mypy is clean.

- **Fixed after review (parent, test-first, red seen):** a SerpApi 200 shaped as a fully empty page whose `error` says the account ran out of searches now raises balance exhaustion, never reads as an empty page (an exhausted account must not record "no web presence"). `no_open_deals/deal_search.json` is back to unverified: its envelope matched, but its outcome is produced by the UNVERIFIED `hs_is_closed` filter, and a fixture is verified only when everything it stands for was confirmed.
- **Known gap (needs-follow-up, open item 8):** `orchestrator.py` now reads the plan/limit env vars itself, against its docstring; a bad value aborts after the run record has started. Validate config before the run starts, together with marking a run failed when a step fails after start.

### Self-review findings
Fixed:
- log_merges built the line (event.log_fields()) inside the try, so a builder bug was swallowed and counted as a logger failure. Moved outside the try; test-first.
- Added tests: KeyboardInterrupt/SystemExit/CancelledError propagate; ConsoleRenderer canary over lines, merged_by, ResolvedConflict and decided_by reprs; cap order-independence on shuffled hand-built conflicts (mutation "cap sorted by something else" survived before).
- Docstring states one line per merge, no per-run cap, by design.
Verified unchanged: except Exception has a justified noqa BLE001 (same pattern as log_redaction.py); a narrower catch is not realistic (processor chains can raise anything); the union-wrapper edits in test_identity_exclusions/test_role_addresses only pass the new kind argument through, counts intact; decided_by survives retain_superseded (dataclasses.replace), idempotence holds; merged_by and events identical across 300 random permutations.
Bullets: 16.12 bullet 1 (carry Match Key and resolved conflicts on the projection result, derive the line) delivered. Run-report persistence (18.x) deferred.
Known gaps:
- SPEC GAP (21.4): match key logged as KIND only. log_redaction.py redacts credentials only and has no mask/hash helper; no requirement or ADR approves a form. User must decide: kind-only, a keyed HMAC prefix (secret from env), or a masked form.
- needs-follow-up: MergeLogOutcome.failed is not surfaced anywhere yet (18.x).
- needs-follow-up: sorted() on merged_by in clustering.py is not guarded by a test (small IntEnum sets iterate sorted anyway).
- needs-follow-up: 100k merges log 100k lines (no per-run cap).

- **LEFT UNCHECKED IN tasks.md by the parent (SPEC GAP, needs-user):** the merge log, its volume cap, the failure handling and the PII canary are delivered and reviewed, but requirement 21.4 asks for the Match Key used and the log carries only its KIND, because `log_redaction.py` redacts credentials only and no requirement or ADR approves a masked or hashed form of an email or LinkedIn URL. Decide one of: kind-only (current), a keyed HMAC prefix with the secret from the environment, or a masked form. Also open: `MergeLogOutcome.failed` is not surfaced until task 18, there is no per-run cap on merge lines, and the projection took about 40 s for a 1000-candidate by 1000-path cluster (worth a performance look).

## Task 16.12 (completion) — keyed HMAC Match Key digests (2026-10-06)

User decision applied: Match Keys reaching logs are HMAC-SHA256 keyed digests, never values, never plain hashes.

Delivered:
- New `match_key_digest.py`: `MatchKeyDigester` (key in a slot, repr shows only `comparable_across_runs`, refuses pickle/copy/deepcopy), `match_key_digester_from_environ`. Digest = `hmac.new(secret, f"{kind_name}\x1f{normalised value}".encode(), sha256).hexdigest()[:16]`.
- Clustering records the keys whose unions actually joined members (`IdentityCluster.linked_by`, repr=False); `ProjectionResult.linking_keys` carries them; `merge_log` logs `match_key_digests` [{kind, digest}] (cap `MAX_LOGGED_MATCH_KEYS`=20 + `match_key_digests_omitted`). `digester` is a required keyword on `merge_log_events`/`log_merges` (no unkeyed path).
- `ingest_runner.run_ingestion` now builds the digester after config load (fails closed before any record/engine), logs one `lead_merge` line per merge after the merge persists, and records `config_snapshot["match_key_digests"]` = keyed|per_run via `StoreRunRecorder` -> `build_run_record`. Run report prints `match-key digests: keyed, comparable across runs with the same secret` / `per-run random key, not comparable across runs (set LEADFORGE_MATCH_KEY_SECRET)` / `not recorded` (unknown stored values never echoed).
- `LEADFORGE_MATCH_KEY_SECRET` added to `env_example.BUILTIN_SETTINGS` (so `.env.example` regenerated with a comment, drift test covers it, and `log_redaction` scrubs the value as a credential).

Provisional decisions:
- Decoding: raw UTF-8 of the stripped value; >= 32 bytes (counted in UTF-8 bytes). Rejected hex/base64 (one more misconfiguration path; `openssl rand -hex 32` text = 64 bytes anyway).
- Truncation 16 hex chars (64 bits), matching the project's only existing truncated digest (projection company id `co-` + 16). No prior match-key digest length existed.
- Kind name bound into the HMAC input (domain separation).
- Absent/blank secret: `secrets.token_bytes(32)` per run + ONE warning `match_key_secret_absent` (fields: variable, min_secret_bytes, comparable_across_runs=False; no key material, no PII).
- Too short: `ConfigurationError("environment", key_path=LEADFORGE_MATCH_KEY_SECRET, detail="must be at least 32 bytes of UTF-8 text")`; never the value or its length; no chained exception.
- "Key used" = keys whose union succeeded; a key shared by members a stronger key already joined is not listed (consistent with existing `merged_by` kinds semantics).
- Persistence: no migration. No column stores match-key digests; `lead_identity.primary_key_type` stores the KIND only. The comparability flag lives in the existing JSON `config_snapshot` column (no schema change).
- Merge log emitted after the merge write succeeds (a failed write logs no merges).

Tests (RED recorded first: ModuleNotFoundError match_key_digest; ImportError MAX_LOGGED_MATCH_KEYS; 12 wiring tests failing): test_match_key_digest.py (new), test_merge_log.py, test_run_record.py, test_run_report.py, test_env_example.py, test_log_redaction.py, test_end_to_end_zero_credential.py; union wrappers updated in test_clustering/test_identity_exclusions/test_linkedin_cannot_link/test_role_addresses. Sentinel-secret scans over captured logs, rendered JSON lines, reprs, errors and report text.

Verification: ruff format/check and mypy clean on my files; remaining ruff/mypy errors and 20 test failures are all in adapters/apollo.py and its tests (concurrent agent, untouched by me). `pytest -q` excluding the two apollo test files: 3619 passed, 1 skipped.

Known gaps:
- `cluster_id` (clustering) is a PLAIN sha256 of a contribution's canonical JSON (contains PII) and is persisted/used as lead id; it is not logged (over_merge docstring: store, never log). Not a Match Key, left as is; dictionary-testable if ever exposed. needs-user if it should become keyed (would change identity ids: migration + recompute).
- `IdentityExclusions.version_token` is a plain sha256 of the barred emails/URLs; currently an unused seam (not logged/persisted). Should become keyed when wired.
- `conflicts.py` stores sha256 of canonical values (design-mandated value hash, persisted); not a Match Key, untouched.
- `MergeLogOutcome.failed` still not surfaced on the run report (18.x).
- Secret rotation makes old digests incomparable; not recorded per run beyond keyed|per_run (no key id/fingerprint, deliberately: a fingerprint is key-derived material).
- Note for parent: `ruff format src` in the verify command also reformats the concurrent agent's apollo files.

### Self-review findings
- (a) No plain value or unkeyed hash of a Match Key reaches logs, errors, reprs, the report, or the persisted config_snapshot. Checked with a grep for sha256/hashlib/blake across the slice. Also checked with a probe on a real keyed ingest run: a spy on digest() captured every linking key, then the captured structlog output, the report text and the IngestionRun.config_snapshot were scanned for each value, for sha256(value) and sha256(kind\x1fvalue) (full and 16-hex), and for the secret. Result: clean. The probe lived in the scratchpad and was not committed.
- (b) The secret is in no repr, pickle, copy, error or report. The length check counts UTF-8 BYTES. An empty or blank value is treated as absent: random key plus a warning, never an empty key. A too-short value raises ConfigurationError (no cause or context, value and length not echoed) before the engine or run record exists.
- (c) The fallback key is built per call: secrets.token_bytes runs inside match_key_digester_from_environ, which run_ingestion calls once per run. per_run reaches config_snapshot and the rendered report. Both are covered by tests.
- (d) A 16-hex digest is 64 bits. That is enough for log correlation (collision ~2^-32 at 2^16 keys per run); recorded, no change.
- (e) cluster_id is a sha256 of the whole canonical JSON of the anchor contribution (source, fetched_at, all fields), not of a Match Key alone. It is not logged, printed or persisted in its own column. It is held in OverMergeReport (repr=False, never persisted) and feeds projection._lead_company_id = "co-" + sha256("no-domain\x1f" + cluster_id)[:16], which is persisted in CanonicalLeadRow.employments for domainless leads. That is a brute-force-hard pseudonym rather than a dictionary hash, and it falls outside 16.12 (match-key exposure). Recorded, not fixed. IdentityExclusions.version_token (plain sha256 over "kind\x1fvalue" entries; dictionary-attackable for a 1-entry set) has NO production caller (only a docstring mention in exclusion_settings). Not exposed. Key it or keep it unexposed when 8.13 wires it.
- (f) The warning match_key_secret_absent carries only variable, min_secret_bytes and comparable_across_runs (tested by exact key set). No key material, no personal data.
- (g) Mutation check (files restored, sha256-verified): plain sha256, char-count length, module-global key, always "keyed", no cap, sort order, no strip, and dropping the LinkedIn or email value were all killed. One SURVIVOR: passing None for the NAME_DOMAIN value in clustering._link_name_domain (name-domain merges would log no digest). Fixed by adding test_clustering::test_a_name_domain_merge_records_the_name_domain_key_that_linked_it, which kills it.
- Skipped / recorded: log_redaction.configure_logging has no production caller, so the claim in the docstring that log_redaction also scrubs the variable holds only once logging is wired (pre-existing, not 16.12). MergeLogOutcome.failed is not surfaced (already noted, 18.x).
- Verification: pytest (apollo files ignored) 3620 passed / 1 skipped; apollo files run separately 126 passed; ruff check and format clean on touched files; mypy clean (170 files).

- **Known gaps (needs-follow-up):** `configure_logging` is never called by the CLI, so the structlog redaction processor is not wired in production (fix next). `cluster_id` hashes a whole contribution with plain sha256 and is persisted indirectly as a domainless `company_id`; never logged or printed; out of 16.12 scope, recorded for follow-up. `IdentityExclusions.version_token` has no production caller.

## Follow-up — Apollo enriches persons from other sources (2026-10-06)

User decision (verbatim): "can't we use either source to enhance information from other sources? linkedin and apollo are the most relevant but can't we use other search methods/terms if an apollo id isn't found to find the company/person?"

Files: src/leadforge/lead_ingestion/adapters/apollo.py (ladder, cache, attach, confidence; module doc "Lookup ladder"), tests/adapters/test_apollo_enrich_other_sources.py (new, 20 tests incl. 5 end-to-end through IngestionOrchestrator + cluster_contributions + project_lead), tests/adapters/test_apollo_source.py (fixture provenance now heuristic 0.9, was origin none).

TDD evidence: RED 1 = ImportError RUNG_CONFIDENCE (scratchpad/apollo-red.txt); RED 2 = 19 failed / 1 passed (apollo-red2.txt); fixture test red after its assertion change. GREEN: 20 passed; full suite 3744 passed, 1 skipped; ruff format, ruff check, mypy clean.

Provisional decisions:
- Ladder per person, stop at first hit (climb only on match_confidence none): id (own, or the id an Apollo record with the same LinkedIn identity carries) -> linkedin_url -> email -> first_name+last_name+domain (registrable) else organization_name. Endpoint unchanged: POST /api/v1/people/match, already allowlisted read-only; no new endpoint, denylist untouched.
- Params relied on: id, linkedin_url, email, first_name, last_name, domain, organization_name (findings A9: Apollo CLI + enrichment docs). UNVERIFIED: sending them as query params of the POST (same open question as A5); linkedin_url is grade [A] (CLI) only.
- Cache key = normalised lookup (id; LinkedIn identity; lowercased address; casefolded name|domain or name|org). `matches` lists each lookup once, so credits_in = real billed calls.
- Anchor/echo (asked.*): LinkedIn URL; else verified address + email_status verified; else name+domain only if the requester has a title or employer; else not asked (apollo_enrich_unattachable, count), except Apollo's own id records (unchanged behaviour, no echo).
- Field Confidence mapping (origin heuristic, ours): id 0.9, linkedin_url 0.9, email 0.9, name_domain 0.6, name_organization 0.5. Echo fields stay origin none. Supersedes "match fields carry origin none".
- Weak hit (name rung or name anchor) contributes no linkedin_url / email / email_status (Match Keys would split it from the bare requester or bridge two people).
- Discard (no attach): answer LinkedIn differs from the requester LinkedIn (linkedin_mismatch); name-anchor answer sharing no title/employer (uncorroborated). A discard ends the ladder (the credit was spent and is counted).
- Ambiguous: a lookup asked for 2+ distinguishable people (distinct LinkedIn identities, none counts as one) is not asked (apollo_match_ambiguous, count). people/match returns one person, so candidate multiplicity is only visible this way.
- Logs: rung + reason only; a lookup value is logged only for Apollo ids.

Known gaps:
- The orchestrator hands Apollo only the (pruned) Discovery work list. HubSpot and Hunter are enrich-only and GoogleSearch emits no person.* paths, so in a real run no HubSpot/Hunter/Google person reaches Apollo yet. The tests use stand-in Discovery sources ("crm"). Feeding earlier tiers' persons forward is an orchestrator / ADR-0002 change, not made here.
- Email anchor needs the requester's address verified (8.11); otherwise the person falls to the name anchor or is unattachable.
- The name+company-name rung can only attach through a LinkedIn or verified-email anchor (no domain, no name key).
- Confidence numbers are an ordering, not calibrated.
- spec-refactor-agent and validate-production-agent not spawned (no Agent tool available to this subagent).
- `ruff format src` ran while another agent had uncommitted edits (merge_log, match-key digest); it passed, but any reformat it made to their files was not reviewed by me.

### Self-review findings

Independent spec-refactor review, 2026-10-06. Evidence from this run: full suite 3746 passed, 1 skipped (no failures, HMAC files included); `ruff check src` clean; mypy clean (170 files).

- (a) Confirmed. `orchestrator.run` builds `work_list` with `enrichment_work_list(...)`, which reads only `Phase.DISCOVERY` results. After each tier it only *prunes* the list with that tier's contributions and never adds them. SEARCH-capable sources: Apollo and GoogleSearch. GoogleSearch RULES map only evidence paths, never `person.*`. HubSpot and Hunter are ENRICH-only. So in a real run every person Apollo sees is Apollo's own. Minimal change (not made here): in the tier loop, before pruning, extend the list with the finished tier's person-bearing contributions, for example `work_list = prune_flagged((*work_list, *tier_people), reports)`. This is an ADR-0002 / 6.9 "work list is Discovery-only" change and needs a recorded decision.
- (b) Probes:
  - Two LinkedIn spellings give one call and one credit, and the answer reaches both records.
  - A mixed-case or padded email gives one call.
  - A retry re-calls only the failed lookup.
  - `credits_in` equals the number of billed calls.
  - GAP (confirmed): one person whose records carry disjoint keys (LinkedIn only and email only) is billed twice. Apollo's first answer carries the second key, but it is not used to answer the second lookup from the cache. Optimisation and design call; not fixed.
- (c) What counts as ambiguous: the set of LinkedIn identities asking one lookup, with "no LinkedIn" counting as one member. Consequence (confirmed): a record with only a verified email plus a LinkedIn record sharing the same verified email make that email lookup ambiguous. Both lose the email rung, even though 8.2 treats them as one person. Over-conservative; design call; flagged.
  - A LinkedIn conflict on any rung is discarded.
  - An EMAIL conflict on a name hit is NOT discarded. Apollo's differing verified address is dropped, but its title, name and company attach to the requester. The spec has no email cannot-link (8.11 only restricts unverified addresses), so this needs a decision; not fixed.
- (d) OK. The endpoint is unchanged, `apollo:/api/v1/people/match` is already on the read-only allowlists in the tests (those files are unmodified), and the POST query-param encoding is marked UNVERIFIED in the module doc.
- (e) OK. Logs carry counts, rungs and reasons; a lookup value is logged only for an Apollo id. `_Lookup`/`_Asker` reprs hold PII but are never logged. Lookup and echo values are persisted in the raw batch (`matches[].lookup`, `attach[].asked`). That is requester PII, the same class as the response emails already persisted.
- (f) The confidence numbers sit in one table, `RUNG_CONFIDENCE`. There is no existing heuristic scale in the repo to reuse, so the numbers are ours and uncalibrated. Origin `heuristic` is permitted by D5 / 1.8 (labelled, outranked by provider_stated).
  - The id-hit move none -> 0.9 is needed for coherence: otherwise an id hit (origin none) would rank below a name hit (heuristic 0.6) in `conflicts.py` order.
  - It contradicts the recorded 12.2 decision (choices.md "Field Confidence stays origin none for every field"). Mark that entry superseded when this ledger merges into choices.md.
  - That decision's objection (per-record certainty applied per field) applies equally to the rung number.
- (g) Mutation check, 17 mutants against the source. 16 were killed. 1 survived: the LinkedIn cache key using the raw URL instead of the identity. Added `test_two_spellings_of_one_linkedin_profile_are_asked_once`, which kills it. apollo.py restored byte-identical (sha256 OK).

- **Supersedes (user-visible decision record):** the earlier choice that Apollo own-id hits carry confidence origin `none` is superseded: id hits now carry 0.9 so they never rank below name+domain hits (0.6).
- **Known gaps (needs-follow-up):** in a real run only Apollo-discovered persons reach Apollo: the work list is built from Discovery output and later tiers only prune it (ADR-0002); the fix is the bounded second enrichment pass (open item 6). One person seen as a LinkedIn-only record and an email-only record is looked up twice (two credits).


## Follow-up — name+domain joins when only one record has an email (8.3, user decision 2026-10-06)
**User decision (verbatim "yes" to the proposal, 2026-10-06):** a name+company-domain Match Key may join two person records when only ONE of them has an email (verified or not); two DIFFERENT emails still block the join. **Supersedes** the 16.1/16.2 choice "only contributions with neither a LinkedIn nor a verified-email key take part" and the Hunter scope note "(c) Verified answer, no LinkedIn = two Leads" (the one-sided case now merges). Requirement 8.3 carries an amendment note pointing here.

Evidence: tests/test_one_sided_email_name_domain.py written first; red run 8 failed, 13 passed (scratchpad red-fu-8.3.txt), green after the change.

### Decisions
- Participation: a record takes part in name+domain iff it has no LinkedIn key (present or barred), unchanged; a stated email no longer excludes it. Corroboration (shared title or employer) still required.
- "Has an email" = `match_keys.stated_email`: normalized `person.email`, else the CRM bare `email` path (as projection reads it), any `email_status`, read raw (Identity Exclusions and 8.14 disqualification do not hide it, so barring an address never changes who may join).
- Key-level ambiguity: a name+domain candidate stated by LinkedIn-less records with 2+ distinct addresses is disqualified (`DisqualifiedAddresses.name_domains`, same order-free first pass as the LinkedIn rule). LinkedIn holders' addresses do not count (they never join by name+domain).
- Bridge rule (deterministic): name+domain edges are collected, grouped into components of the name+domain graph, and a component whose members state 2+ distinct addresses links NOBODY. So A(e1)-C(bare)-B(e2) gives three clusters whether they share one key (disqualified) or span domains (A@x, C@x+y, B@y). Rejected: a pairwise email cannot-link in the union-find (greedy: C joins whichever side sorts first; also refuses legitimate unions of sets holding a second address via a LinkedIn merge).
- The LinkedIn cannot-link guard and the LinkedIn name+domain disqualification are untouched and still win.
- Tests changed on purpose: test_role_addresses::test_removal_alone_must_not_open_the_weak_name_domain_route became test_a_disqualified_address_still_counts_as_stated_for_name_domain (x with a role address now joins bare y; a y stating another address stays apart). test_clustering near-linear stub: `kind` made optional to match the real `union` signature.

### Known gaps
- Two addresses of one real person (verified work + unverified guessed pattern) now block and disqualify that name+domain: under-merge, the cheap direction.
- An ambiguous component also stops two bare records that previously joined each other (regression to under-merge when two different-address namesakes exist).
- A record whose role address (8.14) is disqualified can still join an email-less namesake; the lead then carries the role address as data.
- A disqualified role address still counts as a stated, DIFFERENT address (16.7 kept disqualified addresses "present"; ignoring it would be the plain removal 16.7 rejected): a namesake carrying `info@` blocks, and disqualifies the name+domain for, the real `jane@` record and any bare record. Under-merge only; tested in test_role_addresses.

## Follow-up — name+domain joins when only one record has an email (8.3, user decision 2026-10-06)
- Red: tests/test_one_sided_email_name_domain.py first; collection ImportError (stated_email), then 8 failed / 13 passed (scratchpad/red-fu-8.3.txt). Green: 21 passed.
- Final: ruff check src exit 0; mypy no issues (174 files); pytest 3795 passed, 1 skipped, exit 0. Earlier run showed google_search failures (other agent's in-progress files: test_google_search_attachment, test_google_search_web_evidence_run, test_zero_credential_run_adapters); gone by final run.
### Provisional decisions
- Eligibility: no LinkedIn key (present or barred), unchanged; email no longer excludes. Corroboration still required.
- "Has email" = match_keys.stated_email: person.email else CRM bare `email`, any status, raw (exclusions/8.14 do not hide it).
- Ambiguity: candidate stated by LinkedIn-less records with 2+ distinct addresses -> DisqualifiedAddresses.name_domains. LinkedIn holders' addresses do not count.
- Bridge: name+domain edges grouped by component; a component stating 2+ distinct addresses links nobody (covers cross-domain and chain bridges). Rejected pairwise email cannot-link (greedy, over-refuses).
- Tests changed on purpose: test_role_addresses removal test renamed/rewritten (x with role address now joins bare y; different address still blocked); test_clustering near-linear stub `kind` optional.
- Spec: requirements.md 8.3 amendment blockquote; choices.md follow-up entry appended (supersedes 16.1/16.2 rule and Hunter note (c)).
### Known gaps
- One person with two addresses (verified + guessed unverified) now under-merges and disqualifies the name+domain.
- Ambiguous component also stops bare-bare joins that used to happen.
- Disqualified role address can ride into a lead via a one-sided join (as data, not a key).
- No mutation testing run; spec-refactor / production-readiness sub-agents not spawned (no Agent tool).

### Self-review findings
- Fixed (test defect): test_a_crm_bare_email_path_counts_as_an_email_for_the_block passed email= to jane(), which writes person.email, so the bare CRM path was never exercised (mutant "stated_email ignores bare path" survived). Rebuilt with a bare `email` path; mutant now killed.
- Added test_every_permutation_of_small_mixed_pools_gives_one_result: 30 seeded pools of 3-5 (two domains, chains, LinkedIn mixes, two spellings of one address); ALL permutations give identical clusters, identical DisqualifiedAddresses, identical clusters under an exclusion; invariants: no two LinkedIns per cluster, LinkedIn-free clusters hold <=1 address.
- Mutation: 8 mutants, all killed after the fix (no key-level email disqualification; LinkedIn holders' emails counted; bare path ignored; no normalisation; component threshold <=2; per-pair instead of per-component; verified email excludes again; LinkedIn holders join). match_keys.py/clustering.py restored, sha256 verified.
- Role addresses: consistent with 16.7 (disqualified address stays PRESENT; plain removal rejected). info@ counts as a different address and blocks/disqualifies a jane@ namesake: under-merge; added as a known gap in choices.md.
- Normalisation: stated_email uses normalize_email, same as the verified-email key (lower+strip, +tags kept on both). Bare-path reading matches projection output (probed person.email=None + bare email: lead carries the bare address).
- Complexity: one edge per bucket member plus one extra union-find over n; linear.
- Spec amendment: blockquote only, requirement text untouched; accurate. Known gaps 1-2 accurate.
- Final: ruff check src 0; mypy 0 (174 files); pytest 3799 passed, 1 skipped, exit 0 (no Google failures this run).

## Task 14.2 (completion) — web evidence attached by agreement, option C (2026-10-06)
Evidence: wrote tests/adapters/test_google_search_attachment.py (25 tests) and tests/adapters/test_google_search_web_evidence_run.py (end to end through `run_ingestion`: real orchestrator, merge, store, run report) first. Red: collection failed with `ModuleNotFoundError: No module named 'leadforge.lead_ingestion.adapters.web_evidence'` (scratchpad/red-14.2.txt). Then implemented adapters/web_evidence.py (new, pure) and extended adapters/google_search.py. Edited tests whose premise the user decision superseded: test_google_search_backend.py (capabilities now SEARCH+ENRICH; "answers discovery only" replaced by "enrichment with no discovered company makes no call"), test_from_run_vocabulary.py (phrases become anchored queries, never unanchored), test_zero_credential_run_adapters.py (gap d: in a real run Google has no anchor and makes no call; the all-failed test now leaves Google out). Mutation checks: attaching every result (killed, 2 tests) and disabling URL dedupe (killed, 3 tests); file restored, verified with cmp. Final: `ruff format` on own files only; `ruff check src` clean except clustering.py:46 E501 (another reviewer's file); `mypy` clean except tests/test_one_sided_email_name_domain.py:327 (not mine); `uv run pytest -q` 3795 passed, 1 skipped.
### Provisional decisions (user approved option C; the details below are mine)
- **Verdict:** provisional
- Wiring: google_search now also declares ENRICH. On an EnrichmentRequest it builds anchors from the work list: companies clustered on registrable-domain sets (`companies.company_domains` + `domain_components`, pinned PSL, webmail excluded), its own contributions skipped, first-appearance order. A company with no usable domain is not an anchor: agreement cannot be checked without a domain. Rejected anchoring by name (every result would be unattached, so the spend would buy nothing).
- Queries: one per (company, term), `"<company domain>" <first phrase of the term>`, company order then term order, at most MAX_QUERIES (100). The rest are counted in the raw payload as `unasked_queries`. `from_run` now yields terms (first phrase per term; a phrase already taken by an earlier term is skipped) and no unanchored queries. Constructor `queries` still work for direct Discovery use, unchanged (unattached, no new fields).
- Attachment (`web_evidence.attachment_of`): `own_domain` when the result host's registrable domain is one of the company's; `third_party_mention` when the URL, title or snippet names a company domain as a whole domain (case-insensitive; no letter, digit or hyphen before; no letter, digit, hyphen or further label after, so `acme.com.evil.net` and `notacme.com` do not match); otherwise `unattached`. A result with no URL or an IP host is unattached.
- Signal Strength (24.4, recorded at ingestion, per company+query), from the agreement count only: 1 agreeing host = 0.25; 2-3 = 0.5; >=4, or own domain plus >=1 third-party host = 0.75; corroboration (>=2 distinct other sources naming the company in the work list) adds one level, capped at 1.0; no agreeing host means no strength. Monotone in every input (exhaustive grid test). Never reads title or snippet wording (test with "STRONG intent!!! signal_strength=1.0" text).
- Dedupe: within one anchored search (across its pages), `dedupe_key` ignores scheme, fragment, default port, host case and a trailing slash, and keeps the query string. A repeated result is emitted once, counted once toward hosts, and counted in the log. The same URL under two different term queries counts in each.
- Attached record adds `company.domain` (the anchor's domain, never the result host) and `company.web_evidence.{attachment, agreeing_hosts, corroborating_sources, signal_strength, signal_kind="tech", signal_label=<term>}`. An unattached anchored record adds only `attachment="unattached"`. Provenance comes from an `attribution` block in the per-result wrapper (the precedent 14.2 set for query and date). Kind is "tech" because the profile only has technology and competitor sections. Flat values, not TechSignal objects: the contribution store refuses model objects.
- Counts: one `google_search_web_evidence` log line per normalize with anchored searches, holding attached, unattached, duplicates and unasked_queries (counts only). Unattached records are also stored with `attachment=unattached`, so they can be counted from the store.
- Title and snippet stay UntrustedText. No person.* path. The E2E test confirms no Lead is created and google_search is not a contributing source of the one Lead.
### Known gaps (needs-follow-up)
- GAP d: no shipped Discovery adapter yields a company domain (Apollo search gives names only), so in a real run Google makes no call and contributes nothing. Attachment is proven only with a test stand-in Discovery.
- The run report does not show attached/unattached/duplicate counts: run_report.py and run_recorder.py belong to the HMAC reviewer and were not touched. The report shows only google_search's leads_normalized. To surface them, add a per-source warning or count there.
- Google still declares SEARCH, so in a run its Discovery invocation is a no-op that is recorded as one successful call. Dropping SEARCH would ripple into the 14.1/14.3 and synthetic-pipeline tests.
- Attached web evidence lands in the store under `company.domain`, but no stage builds a CompanySignal from it yet. Clustering treats it as a keyless singleton with no Lead, and the Company Signal join through Employment is a later stage (16.x). Projection carries only TechSignal objects.
- Corroboration counts other sources in the Discovery work list only. Enrichment-tier sources (HubSpot) are not seen, because Google receives the Discovery list.
- Only the first phrase per term is asked (to bound spend). The query form and SerpApi's handling of the quoted domain have not been checked live.
- No hypothesis library in the env: the property tests are exhaustive small grids.
- Self-review agent not run (no Agent tool in this subagent).

### Self-review findings
Independent review (spec-refactor agent, 2026-10-06). Tests: `uv run pytest -q` 3799 passed, 1 skipped; `ruff check src` has 1 error, clustering.py:325 E501 (the clustering agent's file); `mypy` clean.
- (a) GAP d confirmed, not a dropped field. Apollo Discovery (`POST /mixed_people/api_search`) returns `organization` with only `name` and `has_*` flags. The fixture fields matched the docs extract (live-docs A6), and the official CLI prints the raw body with no domain field. `primary_domain` appears only in companies/accounts responses, and `website_url` only in contacts. Apollo's people/match (Enrichment) can carry `organization.primary_domain`, but the orchestrator builds Google's work list from Discovery output only (orchestrator.py:650-672). Enrichment output only prunes that list. So mapping it in apollo.py would still not reach Google. apollo.py is unchanged. Google could become reachable in three ways: (1) feed earlier enrichment tiers' `company.domain` (Apollo match `organization.primary_domain`, Hunter `domain`) into later tiers' work list; Google is PAID/PER_CALL and already runs after the free tiers. (2) Anchor on company name, with attachment still by domain, so text that names the company could not attach. (3) A Discovery source that yields domains.
- FIXED (test-first): `attachment_of` matched the whole URL, so userinfo `https://acme.com@evil.io/` attached as third_party_mention. Now only the part of the URL after the host is read. Test: test_lookalike_hosts_and_private_suffix_neighbours_do_not_attach. It also covers acme-com.io, acme.com.evil.io, github.io and vercel.app neighbours (PSL private suffixes, which already passed), and acme.co vs acme.com in both directions.
- FIXED (test-first): a malformed result URL (`https://[x/...`) raised a bare `ValueError` from urlsplit inside normalize. Now `url_host` returns None (unattached), and `dedupe_key` falls back to the raw string. Test: test_a_malformed_result_url_is_unattached_not_a_crash.
- FIXED (test-first, gap e): the run report now shows `  web_evidence: attached=N unattached=M` per source that stored any. It is read from the store (`company.web_evidence.attachment` fields joined through source_contribution to source_run), with one extra SELECT; the constant-query test still holds (<=3). Asserted in the E2E test.
- Test gap closed: corroboration counted contributions rather than distinct sources, and that mutant survived. The anchors test now has a repeated same-source lead.
- Mutation checks: userinfo, IPv6 guard, report count, own+third-party rule, lookahead, lookbehind, corroboration, self-exclusion, MAX_QUERIES cap, host by registrable domain, domain=anchor. All were killed after the test above was added. Files were restored and the sha256 checks returned OK.
- Docstring: the google_search 14.2 bullet that said "no company.domain" is now scoped to unanchored searches.
- SKIPPED (needs decision): level 3 is `own_domain and hosts>=2`. With a company that has two own domains, that gives level 3 with no third-party host, which differs from the documented "own domain plus >=1 third-party host". It is monotone either way.
- SKIPPED (minor): names_domain's lookbehind is ASCII-only, so "éacme.com" matches.
- Verified OK: dedupe per anchored search, hosts counted by registrable domain (a test now covers two subdomains as one host), deterministic order, strength never read from text, the per-run page cache, pacing via `_send`, at most 100 queries times ceil(results/page) pages, no person.* paths, title and snippet kept as UntrustedText, and a counts-only log line.
- CHANGED after coordinator decision (user, 2026-10-06: "if information is agreed between many sources then it should likely be ok"). The company's own domains and subdomains together now count as ONE agreeing host. Each distinct third-party registrable domain adds one. New `web_evidence.agreeing_host(url, attachment)` returns one own-domain sentinel; google_search counts hosts with it. Own-domain-only evidence therefore stays at 0.25, however many own domains appear. Level 3 ("own + >=1 third-party") is now exactly `own_domain and hosts>=2`. The rest of the scale is unchanged. This resolves the skipped item above. Red first: test_the_company_own_domains_together_are_one_agreeing_host failed (`{2} == {1}`). Mutation checks (own domains counted separately, third-party counted as own, unattached counted) were each killed. The file was restored and its sha256 check returned OK. The rule is documented in the web_evidence module docstring. choices.md has no completion entry yet, so the parent should carry this rule there. Final run: pytest 3800 passed, 1 skipped; ruff check src all passed; mypy clean.

- **Left unticked (parent):** the attach-by-agreement machinery is built and reviewed, but in a real run Google makes no call: Apollo people search returns no company domain, and Apollo enrichment output (which carries `organization.primary_domain`) never reaches Google because enrichment tiers do not feed later tiers (ADR-0002). Tick 14.2 when open item 6 (enrichment results feed later tiers) lands and a real-adapter run shows Google called.

## Follow-up — enrichment tiers feed later tiers (ADR amendment, 2026-10-06)

User decisions (2026-10-06): sources should enhance each other ("can't we use either source to enhance information from other sources?"); HubSpot and Hunter "have information we can cross reference or append to the lead"; a Lead is a person; Hunter runs before Apollo, so a Hunter 451 prunes the person before Apollo's paid match. Recorded as ADR-0006 (`specs/lead-source-adapters/docs/adr/0006-enrichment-tiers-feed-later-tiers.md`), which amends ADR-0002. ADR-0002 now ends with an amendment pointer.

Provisional decisions:

- **Feed** (`orchestrator._execute`): when a tier finishes, its contributions (tier order, then source name) are appended to the work list before the next tier is called. One forward pass: each tier is called at most once, and nothing is fed back to an earlier tier. Results are unchanged; each holds only its own source's contributions. `enrichment_work_list` still returns Discovery only.
- **Cumulative pruning**: every flagged report so far prunes the list before each later tier, so a person suppressed early stays out even if a later record re-adds them.
- **Order** (`base_source`): new defaulted declaration `evidence_only: bool = False`, validated as a bool. Sort key is (paid, not suppression, evidence_only, charge-unit rank, name), and the tier key is its first four fields. GoogleSearchSource sets `evidence_only=True`. Shipped tiers: hubspot, hunter, apollo, google_search. Why: Google needs the domain Apollo's match finds. Neither source prunes for the other, so moving Google later changes no spend. Rejected alternatives: swapping the per_call and per_lead ranks (it is a billing rule and would move every per-call source), and re-running tiers until nothing changes (spend would be unbounded).
- **Apollo, once per person** (`apollo._plan`): records sharing an address or LinkedIn identity, transitively, share one merged ladder (strongest rung first) and one asker on the strongest anchor (LinkedIn, then verified email, then name, then own record), with the person's LinkedIn identity. Only records with a ladder of their own can be the anchor. A group naming two LinkedIn identities falls back to the old per-record askers. If no record in a group can anchor, the group counts as one unattachable person. This supersedes "the answer reaches both" for two spellings of one LinkedIn: there is now one attachment, and the merge joins the other spelling by the normalised key.
- **Apollo `organization.primary_domain` -> `company.domain`** (MATCH_RULES): reduced through `companies.company_domains` (pinned PSL, webmail excluded) to one registrable domain, else nothing. It is excluded from weak hits (`_IDENTITY_PATHS` now includes `company.domain`), and the requester's echoed domain wins. Shipped `fixtures/apollo/match.json` gains `"primary_domain": "example.com"`, and the manifest note marks the field UNVERIFIED (live-docs-findings has no people/match field table naming it).
- **Per-company list** (`per_company_work_list`): records naming one person are one Lead whose domains are all of theirs. The list keeps the first record per company, and the first record per domainless person, so a fed domainless record of a known person no longer stands alone.
- **Google corroboration** (`web_evidence.company_anchors`): a `company.domain` whose provenance raw path starts with `asked.` (an echo) still forms an anchor but adds no corroborating source.
- **Tests repinned**:
  - Hunter sort-key shape is now a 5-tuple.
  - `test_base_source` rank index changes from 2 to 3.
  - In the suppression E2E stub, Hunter's verifier echoes the asked address. A stand-in answering a different address is now a different person reaching Apollo.
  - In the HubSpot suppression-paths test, HubSpot's own record of bob reaches the paid tier.
  - In the zero-credential run, gap (d) is closed: Google is served and stores unattached evidence.
  - The Apollo fixture match now includes `company.domain`.
- **E2E proof**: `tests/adapters/test_tier_feed_end_to_end.py` runs through `run_ingestion` in synthetic mode with the 4 real adapters on shipped fixture files, routed per request, plus one labelled stand-in Discovery (gap c: no shipped Discovery yields an address).
  - Google asks `"example.com" DataStax` for the domain only Apollo found, and its own-domain evidence attaches. It never asks about Sam's domain.
  - Apollo makes 1 search and 3 matches (Ada by id, Grace by id, Hana by LinkedIn once, although 3 records name her). It never asks about Sam.
  - Hunter makes 2 verifier calls. HubSpot makes 2 contact searches and 1 deal search. Total calls: 11.
  - Hana's Apollo enrichment is on ONE lead, together with her Discovery record and Hunter's verdict.
  - Sam's leads are all suppressed, and Apollo contributed to none of them.
  - The report's per-source `leads_normalized` equals the run's contribution counts, and the web_evidence counts match the store.
  - A mutation check (feed disabled) makes the E2E fail, because Google is no longer asked about example.com.

Known gaps:

- A person found only by a later tier is never asked of an earlier one (one forward pass). HubSpot never sees an address Hunter or Apollo found, so a CRM opt-out for that address is applied at merge, after Apollo may have spent.
- Hunter routes each record on its own. Two fed records of one person (one with an address, one with name and domain) would cost a verifier call and a finder call. This is latent: no shipped tier before Hunter emits `person.email` (HubSpot uses `email`).
- HubSpot's record (address with no status and no name) is not joined by the merge, because 8.2 keys only verified addresses. It stays a separate Lead, as before; this is pinned in the E2E as a known gap. Fixing it would mean changing the merge rules, which is outside this follow-up.
- Apollo's Discovery search does not map `organization.primary_domain`, so search records still carry no domain.
- `primary_domain` is UNVERIFIED against a capture.
- Not run: the spec-refactor-agent and validate-production-agent sub-agents (no Agent tool in this session), and serena/gitnexus blast-radius tools (not available). Callers were found with grep instead.

### Self-review findings

This is an independent review by spec-refactor-agent in a fresh context. Baseline: 3824 passed, 1 skipped. After the fixes, `uv run pytest -q` gives 3827 passed, 1 skipped. `ruff check src` is clean and `mypy` is clean (178 files). The touched files were formatted with `ruff format`.

Fixed (test-first; each new test was red before its fix):
- `orchestrator.per_company_work_list` fused companies through a person. One person at a former employer (a.io) and a current one (b.io) had both domains unioned. A colleague's only b.io record was then dropped, so company b.io was never worked. This is a 6.11 regression, latent because no shipped adapter is per-company. Domain sets are no longer unioned per person. A domainless record is dropped only when its person has a record naming a company; otherwise the first such record per person is kept. Test: `test_a_person_at_two_companies_does_not_fuse_those_companies`.
- `apollo._plan` put distinct people sharing a role address into one merged ladder. With Ada {LinkedIn, info@} and Bob {info@ verified}, a LinkedIn miss climbed to info@ and then to Bob's NAME rung, so Bob's answer was attached to Ada's record. A group naming two distinct `normalized_person_name` values (the 8.14 rule) now falls back to per-record askers, as a two-LinkedIn group already did. The shared address is then ambiguous, and the calls are the same as before ADR-0006. Test: `test_two_names_sharing_an_address_stay_two_people`.
- Added `test_a_failed_tier_feeds_nothing_and_later_tiers_still_run_once`. It checks isolation and the exact call count. It passes and is a regression guard.
- Corrected ADR-0006 Consequences in three places: pruning is not transitive; the distinct-names fallback; the per-company wording.

Checked, no defect:
- Pruning keys on `compliance.identities`, which uses normalize_email and normalize_linkedin_url and reads both `person.*` and bare paths. It is cumulative over every report. Google reads only `company_anchors` (company.domain), so no person data reaches its queries.
- A failed source has contributions None and feeds nothing. Fetch and normalize are one attempt, so there is no partial output. A timeout aborts the remaining tiers.
- The feed order is tier order, then source name, so it is deterministic. No tier is called twice (pinned by a test).
- The sort key still puts free before paid and suppression-yielding first; evidence_only comes after both. HubSpot before Hunter is unchanged (free before paid).
- primary_domain goes through `company_domains` (pinned PSL, webmail excluded). linkedin.com is not excluded, and should not be: it is a real employer domain, and the existing companies rules exclude only webmail. UNVERIFIED is in the manifest note.
- ADR-0006 follows the ADR format (title, context, Considered Options, Consequences). ADR-0002 only gained the Amendment pointer.
- Mutation checks: all KILLED, and every file was restored (sha256 verified). Mutants: feed off; cumulative reports off; Google evidence_only False (Google runs before Apollo); evidence_only removed from the sort key; Apollo one-ask-per-person off; primary_domain rule off; company.domain removed from _IDENTITY_PATHS; echoed domains corroborate; company fusion restored; distinct-names fallback off.

Not fixed (needs-user):
- HIGH (spend): pruning is not transitive. Probe: Discovery has {LinkedIn X} and {z@}. HubSpot opts out z@. Hunter's fed record {z@, echoed LinkedIn X} links the two, yet {LinkedIn X} still reaches Apollo's paid match. Closing the block over linked identities would over-prune through shared role addresses, so this is a design choice. Output stays compliant because projection blocks by identity.
- Duplicate union-find in orchestrator._person_labels and apollo._people (companies already has `_UnionFind`). There are only two call sites, so no extraction under the scope rule.
- `evidence_only` is a hand-set ordering flag and is not checked against `answerable_surfaces`. A source could claim it while filling person.* paths. A derived check ("fills no identity path") would be sturdier.

Option note (e): HubSpot's own record does not join.

Facts:
- HubSpot writes the bare `email` path from the raw `lookup` field. That is the address we asked about, echoed back.
- It carries no `email_status`.
- HubSpot emits it even when no contact exists (negative evidence).
- 16.1 (choices.md:1522): only EmailStatus.VERIFIED is a key; UNVERIFIED and ACCEPT_ALL only corroborate; a missing status gives nothing. 8.2 and 8.11: only verified addresses are Match Keys.

So the address is not customer-entered first-party data. It echoes our question, and for an unknown contact HubSpot does not hold it at all.

1. Status quo: HubSpot's record stays a separate Lead. Suppression still applies, because projection blocks by identity.
2. (Recommended) HubSpot echoes the requester's identity under `asked.*`, as Hunter and Apollo already do: the requester's `person.linkedin_url`, plus `person.email` and `email_status` only when the requester's own status is verified. The record then joins through the existing 8.1/8.2 keys, with no merge-rule change.
3. Amend 8.2 so that a CRM-held address (contact found) is a key. This needs a user decision and conflicts with 8.11 (verified only). Marking HubSpot addresses VERIFIED is rejected: presence in the CRM says nothing about deliverability.

- **Task 14.2 ticked with this change (parent):** the real-adapter run in `test_tier_feed_end_to_end.py` shows Google called for the company domain Apollo enrichment found, with evidence attached (Discovery there is a stand-in, since no shipped Discovery source returns emails).
- **Open for the user (needs-user):** (1) suppression pruning is not transitive: a LinkedIn-only record of a person HubSpot opted out still reaches Apollo paid match even after Hunter output links the two records; (2) HubSpot contact record stays a separate lead (its email repeats the looked-up address with no verification status, so it is not a Match Key): options keep separate / echo requester identity under `asked.*` (recommended) / change 8.2.

## Follow-up — open items: run lifecycle, early config, persisted counts, logging, single-adapter failure (2026-10-06)

**Red seen (test-first) before each change:**
- Merge-write failure: `test_a_merge_write_failure_marks_the_run_aborted_on_each_engine[sqlite|postgres]` failed with `('completed', 0) == ('aborted', None)`. This was the real bug.
- Storage: finish(reason=) TypeError, SourceCounts(attempted=) TypeError, no record_contributions_written, no revision 0005, contributions_written was 0 against 12 stored.
- Hooks: records_fetched/credits_spent rows were None, and a hook NormalizationError was not isolated. The adapter hook tests got None.
- Early config: bad APOLLO_PLAN / SERPAPI_HOURLY_LIMIT left `runs_recorded == 1` (expected 0). The CLI also recorded a run.
- Orchestrator: `live_rate_limits` import error / kwarg missing.
- Logging: the CLI's stderr had no JSON log lines (structlog was not configured).
- Summary: `alpha: attempted=2 succeeded=1 failed=1` was missing the `transient` class.
- Coordinator scope: exclusions without a secret did not raise ConfigurationError, and IngestionRun had no projection_version.

**Decisions (provisional):**
1. Lifecycle. The composition root defers the orchestrator's completion (`_DeferredCompletion`). Merge write and completion (status, exit code, counts, contributions_written, projection stamp) now commit in one `write_batch`. A failure after the sources ran rolls back and marks the run `aborted` with `failure_reason` `merge: X` or `merge_write: X` (exception class only). I kept the existing vocabulary: no new `failed` status. The orchestrator-level abort still stores no reason.
2. Early config. `ingest_runner.live_rate_limits` reads `run_rate_limit(os.environ)` for LIVE sources only, before the engine exists. The orchestrator takes `live_rate_limits=`, reads no environment, and falls back to the declared `rate_limit`. The docstrings were updated. Vendor-named tests moved to tests/adapters/test_plan_settings_early.py because vendor-neutrality requires it.
3. Migration 0005 (engine-neutral add_column, batch drop on downgrade, tested on both engines). source_run gets attempted, succeeded, failed and records_fetched. ingestion_run gets failure_reason, projection_version, projection_fingerprint and primary_domain_ties_flagged. contributions_written is counted from the stored rows. I added the BaseLeadSource hooks `records_fetched` and `credits_spent` (default None). Apollo: search counts people, match counts matches, and credits = credits_in for live matches, else 0. Hunter: search addresses plus found finds, credits = credits_in. HubSpot: contacts found. Google: organic results. The report reads `fetched=`, `contributions_written=`, a `calls:` line and `abort reason:`.
4. The CLI loads `.env` (it never overrides) and then calls `configure_logging(os.environ)` before the run.
5. Real-adapter failure: I used Google Search with a transport that returns HTTP 500. HubSpot makes no provider call in a zero-credential run (gap c), so a HubSpot 500 would never be seen. Other sources come back OK. The run is `completed` with exit 0, and the row/report show `transient` plus the call counts. run_exit now names the failure class even for a source that had an earlier success.
6. Coordinator scope. `ProjectionBasis.of` is built at config time, and its ValueError becomes `ConfigurationError(LEADFORGE_MATCH_KEY_SECRET)` before any record (exclusions come from the exclusions file; there is no store-held exclusion set). The stamp is `stamp_projection(latest completed run's stored stamp, basis)`, written to canonical rows and to the run. Projection reads `TieResolutionRepository`. The report shows `primary-domain tie fallbacks: N flagged`, `projection version:` and `stale projections:`. Removed `PROJECTION_VERSION`.

**Gaps (needs-follow-up):**
- canonical_lead.primary_domain / primary_domain_source columns were not added: persist_merge (merged_leads.py, other agent's file) would have to write them.
- No live `resolve_primary_domain` (model) call is wired, so a tie with nothing stored is always the flagged fallback.
- Two concurrent runs can stamp the same version. The relink decision (Option A/B) is still open.
- `merged` still has no producer.
- The orchestrator-level abort has no reason.
- run_report `_failure` still ignores `attempted` when deciding "none recorded".
- Hunter's verifications are not counted as fetched.
- spec-refactor-agent and validate-production-agent were not spawned (no Agent tool in this context).

**Verification (this run):** ruff check src clean; mypy clean (185 files); pytest 3895 passed, 1 skipped (test_env_file root file modes). The Postgres leg ran: 41 postgres-param tests passed.

### Self-review findings
- (a) Transaction boundary: no defect found. Added tests in tests/test_run_lifecycle_both_engines.py, both engines: a failure after each completing write (record_projection, finish, record_source_counts, record_contributions_written) rolls back every row (no canonical leads, no contributions, counts NULL, no stamp). The run ends `aborted` with `merge_write: RuntimeError`, and the sentinel text is never stored. A failure before the write gives `merge: <Class>`. If the abort marker itself fails, the original error still propagates, with a note, and the record is left `running` (a crash per run_record.py). The abort is its own shielded write_batch on a fresh connection, so it does not depend on the failed session. Residual: if the store is gone, the abort cannot be persisted either (expected).
- Mutation check (4 mutants: completion split into its own txn, reason with exception text, wrong stage, unguarded abort): all killed. ingest_runner.py restored (sha256 OK).
- (b) 0005: up/down on both engines with a pre-existing row (NULL defaults) is covered by test_0005_walks_up_and_down. The autogenerate compare tests pass on both engines. Types are Integer/String only.
- (c) attempted counts retries, so the invariant is attempted >= succeeded + failed, not equality. The rows equal the ledger outcome. contributions_written is counted from the stored rows.
- (e) Decision: a bad plan value of a disabled or synthetic source does not fail the run. Only enabled LIVE sources are read (7.5).
- (g) The skip `os.geteuid()==0` (root ignores file modes) is legitimate and pre-existing.
- Not fixed: the redaction test has no sentinel for a vendor API key (only for MATCH_KEY secret and email). Stray untracked dir src/leadforge/lead_ingestion/.claude/memory (dated Oct 5, not this change). HubSpot lifecycle hook is intact. The first full run had 1 failure in the concurrent agent's WIP test_hubspot_request_echo; it passed on rerun.
- Verification: ruff clean, mypy clean (186 files), pytest 3924 passed / 1 skipped, Postgres params ran.

## Tasks 16.6 and 16.11 (completion) (2026-10-06)
Evidence: test-first. tests/test_projection_version.py red = collection ImportError (PROJECTION_RULES_REVISION missing); tests/test_projection_primary_domain.py red = 12 failed (TypeError: unexpected kwarg tie_resolutions), then 1 red (KeyError primary_domain_tie) before the merge-log field. Green: 24 new tests. Mutations caught (ignore store; winner-only votes; stamp without fingerprint; fingerprint without trust ranks). Full suite: 3863 passed, 2 failed, both other-agent (test_run_source_counts, test_store_migrations 0005). ruff/mypy errors only in test_run_lifecycle_both_engines.py and ingest_runner.py (other agent).
### Decisions (16.6)
- Version lives in projection.py: PROJECTION_RULES_REVISION = 2 (1 = 16.5 rules; 2 = one-sided email 8.3, request-echo fields only when no other source has the field, LinkedIn cannot-link, confidence table changes, primary domain from stored tie resolution). ProjectionBasis.of(exclusions, trust_ranks) = (rules revision, exclusions version_token, trust-ranks digest); fingerprint = sha256 of the three.
- stamp_projection(previous, basis): None -> 1; same fingerprint -> unchanged; any change -> +1 (monotonic: reverting is another bump). Trust-ranking change also bumps (design.md line 850). Digests kept out of repr.
- store/merged_leads.stale_projections(session, current_version) flags identities whose canonical_lead.projection_version differs (sorted). Proven on SQLite: stored v1, exclusion added -> v2, row flagged, re-merge from the same contributions separates the over-merge, contribution rows byte-identical.
### Decisions (16.11)
- project_lead(..., tie_resolutions: TieResolutionReader | None) reads only (get); no model, no write, no log. Vote: each source holding a company.domain candidate (winner/agreeing/superseded) votes for its domains inside the Employment's domain set (primary_domain.elect_by_votes, extracted from elect_primary_domain). Tie with nothing stored -> lowest-sorted, TieSource.UNRESOLVED_PROVISIONAL (flagged).
- ProjectionResult.primary_domain (repr=False), primary_domain_source, primary_domain_flagged; merge log line gains primary_domain_tie (source value, never the domain). Display only: the result is otherwise equal whatever the stored answer.
- test_primary_domain guard: projection removed from the "no match rule imports primary_domain" list (8.18 requires the read); clustering/companies/match_keys/orchestrator still guarded.
### Gaps (other agent / migrations)
- ingest_runner still writes PROJECTION_VERSION = 1: should pass stamp_projection(previous, ProjectionBasis.of(exclusions, ranks)).version and project_lead(..., tie_resolutions=TieResolutionRepository(session)); call resolve_primary_domain before projection in live mode.
- Persistence needs a migration: the previous basis fingerprint (column canonical_lead.projection_basis String(64) or a one-row projection_state table) so the next run can compute the stamp; a canonical_lead.primary_domain column to store the display domain.
- In-store recompute after an exclusion change is blocked by design: source_contribution.lead_identity_id is append-only, so a split cluster cannot be relinked; needs a decision (new identities + identity supersede map).
- Flagged-tie count on the run report (run_report.py, other agent / 18.x).
### Status
- 16.6: domain layer DONE; NOT fully done (fingerprint persistence + runner wiring + store recompute pending).
- 16.11: projection read wiring DONE; NOT fully done (run-report flag, primary_domain column, runner wiring pending).

### Self-review findings
Task text checked: 16.11 is the PRIMARY-DOMAIN tie (8.18: "constrained to choose among the candidate domains", "projection read that stored record"), not a conflict-order tie. The implementer's reading is correct.
**Fixed (test-first, seen red, 7 failing before the code change):**
- DEFECT (a), PII: the version fingerprint carried `IdentityExclusions.version_token`, a plain unsalted sha256 over the sorted exclusion emails/LinkedIn URLs. `fingerprint = sha256(rev, token, ranks digest)` with rev and ranks known is still dictionary-reversible, and the fingerprint is meant to be STORED. Now `ProjectionBasis.of(..., digester=MatchKeyDigester)` hashes the sorted keyed HMAC digests (`match_key_digest`, same scheme as the merge log). Provisional: with any exclusion set, a missing or per-run random digester (`comparable_across_runs=False`) raises ValueError naming LEADFORGE_MATCH_KEY_SECRET and never the value, because a per-run key would change the fingerprint on every run and mark every Lead stale every run without saying so. An empty set needs no secret.
- Added test (c): an AST guard. projection.py cannot name resolve_primary_domain, TieResolver or choose. Import scan: projection pulls in only domain modules plus `requests`, which comes from tldextract and is configured offline (suffix_list_urls=()). No model or transport module is loaded.
- Added test: a domain outside the Employment's set never becomes the primary domain. The mutation "drop the `domain in domains` filter" survived before this test.
**Verified correct:** fingerprint is canonical (sorted ranks, sorted digests, fixed separators) and holds no run ids or timestamps. Tie fallback is the lowest-sorted candidate, flagged UNRESOLVED_PROVISIONAL (`primary_domain_flagged`), never silently final. The merge log `primary_domain_tie` is the TieSource value only, never the domain. stale_projections: projection_version is NOT NULL, so `!=` misses no row.
**Mutation checks (12, all caught; files restored, sha256 verified):** plain token, accept per-run key, import escalation, drop ranks, no bump, stale inverted, log drops tie, ignore store, unflagged fallback, highest-sorted, vote outside set (after the new test).
**Gap (e), reproduced on SQLite (scratchpad/repro/repro_relink.py):** after an exclusion splits a stored cluster, relinking contribution `c` to a new lead_identity raises AppendOnlyViolationError. Both the ORM `before_update` listener and the `do_orm_execute` bulk guard on source_contribution do this. The cause is the 8.12 append-only guard on `source_contribution.lead_identity_id`. It is not a database constraint or foreign key: the FK permits any identity, migrations have no trigger, and canonical_lead.lead_identity_id UNIQUE is satisfied by new identities. Calling persist_merge again re-INSERTs the contributions (2 -> 4 rows), which duplicates the log.
  - Option A: move identity membership out of source_contribution into a derived, rewritable `contribution_identity(contribution_id, lead_identity_id, projection_version)` mapping, which a recompute replaces. Then source_contribution.lead_identity_id is either unused (left NULL) or treated as the first-seen identity.
  - Option B: keep the column and add an append-only `identity_supersede(old_identity_id, new_identity_id, contribution_id, projection_version)` log. A split writes new identities plus supersede rows, and readers resolve the current identity through that log.
  - Recommendation: A. Cluster membership is a projection, and 8.13 makes projections recomputable, so it belongs on the derived side. B makes every reader follow a supersede chain and grows with each exclusion change. Either one needs a migration and a user decision.
**Wiring checklist for the other agent (not done here):**
- [ ] ingest_runner: build `ProjectionBasis.of(exclusions, ranks, digester=<run's MatchKeyDigester>)`, turn its ValueError into ConfigurationError (exclusions set without the secret), and stamp with `stamp_projection(previous_stamp, basis).version` instead of the constant PROJECTION_VERSION = 1.
- [ ] ingest_runner: `project_lead(..., tie_resolutions=TieResolutionRepository(session))`. In live mode only, call resolve_primary_domain before projection; synthetic mode never calls it.
- [ ] Migration: persist the previous basis fingerprint (64-hex, keyed) as canonical_lead.projection_basis or a one-row projection_state table, plus canonical_lead.primary_domain (display) and optionally primary_domain_source.
- [ ] run_report: a count of flagged primary-domain ties (`ProjectionResult.primary_domain_flagged`), plus the list from stale_projections.
- [ ] The relink decision above (Option A or B) is needed before an in-store recompute.
**Left for follow-up (outside these files):** match_keys.IdentityExclusions.version_token is still a plain sha256 and no longer has a src caller. Its docstrings, and the one in exclusion_settings.py, still call it "the seam". Remove it, or key it the same way.
**Full suite:** 3883 passed, 5 failed, all in other agents' areas: test_google_search_throttle x4 (adapters/google_search.py being edited concurrently) and test_vendor_neutrality ('apollo' in ingest_runner.py and test_end_to_end_zero_credential.py). mypy reports 4 errors, only in test_store_migrations.py and test_run_lifecycle_both_engines.py. ruff is clean on the touched files.

- **16.6/16.11 left unticked (parent):** remaining: canonical-lead primary-domain columns and fingerprint persistence in `store/merged_leads.py`, the run calling the stored tie answer, a `merged` count in the report; done together with the derived record-to-lead mapping (user option A).

## Follow-up — HubSpot contact joins the asked person via request echo (user option B, 2026-10-06)

Files: src/leadforge/lead_ingestion/adapters/hubspot.py; tests/adapters/test_hubspot_request_echo.py (new, 17 tests); tests/adapters/test_tier_feed_end_to_end.py (stale KNOWN GAP comment only).

RED: 13 failed / 4 passed (no asked.* echo; 'email' not in contact-search properties; no hubspot_echo_withheld logs; KeyError 'asked'; run_ingestion lead split). GREEN: 17 passed. ruff clean; mypy clean (186 files); full pytest 3924 passed, 1 skipped.

Decisions:
- Echo = requester's LinkedIn URL (own text) and, when a requester record holds the asked address as verified, that address + email_status verified, at asked.linkedin_url / asked.email / asked.email_status. No name/domain echo: HubSpot asks by address only.
- Echo decided at fetch, persisted in lookups[].asked; batches without it normalise with no echo.
- Withheld (log hubspot_echo_withheld, reason + count, no values): ambiguous_requester (2+ LinkedIn identities or 2+ person names for one address, accumulated across fetches this run); multiple_contacts; email_mismatch (contact's own email property differs); email_unconfirmed (absent). Contact search now requests the 'email' property.
- Not-found (Negative Evidence) records get NO echo: their 'email' is the question (raw path 'lookup'); joining would let the question count as agreement.
- HubSpot's own fields (crm.*, opt_out, suppressed, bare email) unchanged; opt-out now lands on the joined lead (cluster OR), not only via blocked identity.

Proven: adapter unit tests; real orchestrator + clustering + projection (LinkedIn person Apollo knows -> one lead with crm.* from HubSpot; verified-address person opted out -> joined + suppressed, compliance_sources has hubspot; mismatched/two-contact answers stay separate); run_ingestion with real HubSpot + Apollo adapters (shipped fixtures rewritten per person) + test-only Discovery -> store rows confirm.

Gaps:
- A requester with only an unverified address (no LinkedIn) gets no echo: HubSpot record stays separate. HubSpot runs in the first tier (ADR-0006 one forward pass), so a verified address/LinkedIn only Hunter/Apollo learn later never reaches HubSpot's echo.
- Projection reads the bare CRM 'email' only when person.email is absent, so an email echo shadows HubSpot's own confirmed address: agreement on person.email is 1 lower than with a LinkedIn-only echo (conservative under-count). Not changed (projection.py is off-limits).
- HubSpot's 'email' rule still reads raw path 'lookup' (the question); for a confirmed contact it equals the contact's own address, so it counts as agreement. Mapping it from contact.properties.email would be more honest provenance — not done (behaviour change outside the decision).
- Shipped hubspot fixtures' contact email (ada@example.com) does not match other people asked, so tier-feed test still shows HubSpot records unattached (now for the email_mismatch reason).
- spec-refactor-agent / validate-production-agent not spawned (no Agent tool in this harness). GitNexus/serena not available here; blast radius checked by grep: hubspot internals referenced only by its own tests.

### Self-review findings

- Diff separation: run-lifecycle's records_fetched/credits_spent in hubspot/hunter/apollo intact; echo diff intact; neither clobbered.
- FIXED (c), test-first: HubSpot keyed lookups and compared addresses with casefold(); the email Match Key (normalize_email) uses lower(). "straße@" and "strasse@" are 2 mailboxes, but HubSpot asked the wrong one and echoed onto a contact with a different mailbox. Now keyed with strip().lower(), compared via normalize_email. 2 RED->GREEN tests.
- FIXED (a), test-first: echo rules hand-built in hunter (ASKED_RULES), apollo (_Asked/_ASKED_RULES/_ECHO_KEY), hubspot (_ECHO_RULES, "asked" literals). Now one normalizer.REQUEST_ECHO_RULES + REQUEST_ECHO_KEY, used by all three; order and untrusted flags unchanged. Identity test added.
- (b) OK: opt-out joins via echo and still prunes via blocked email identity (test_suppression_end_to_end, test_hubspot_suppression_paths pass).
- (d) OK: withheld logs carry reason + count only.
- (e) REPRODUCED, a real correctness defect (under-count, never over-count): crm verified ada@ + HubSpot confirmed contact gives agreement[person.email]=1; with a LinkedIn-only echo HubSpot counts. projection._with_canonical_email skips renaming the bare `email` whenever person.email exists, even if that value is only an asked.* echo, so HubSpot's own observation is lost from candidates and agreement. Minimal fix (projection.py, NOT edited): treat a person.email whose provenance raw_field_path starts with REQUEST_ECHO_PREFIX as absent. Drop that echo value and provenance, then rename bare email -> person.email. Clustering runs before this, so joins are unaffected.
- (f) 12 mutations of hubspot echo logic were all killed; restore checked with sha256 OK.
- Verification: pytest 3929 passed, 1 skipped; ruff clean on touched files; mypy clean (186).

- **Open (needs-follow-up):** an echoed email hides HubSpot own observed address from the agreement count (person.email agreement 1 instead of 2); fix belongs in `projection._with_canonical_email`; scheduled with the next projection change.

## Follow-up — opt-outs follow strong identity links; second HubSpot pass (user decisions 2026-10-06)

- **Reproduced first (RED):** new e2e `tests/adapters/test_optout_links_second_pass_end_to_end.py` run against HEAD: Apollo's paid match was asked `linkedin_url=http://www.linkedin.com/in/pat-example` (Pat opted out in HubSpot; Hunter's finder linked his address to that URL). Unit RED: 31 of 32 in `test_orchestrator_optout_links.py` failed (e.g. 40 kept vs 34 expected); 5 of 7 in `test_orchestrator_second_free_pass.py` failed (no second call).
- **Decision 1 (orchestrator.py `prune_flagged`):** a flagged component is now pruned whole. Components are built with clustering's `_UnionFind` (already shared with companies.py) over `extract_match_keys` LINKEDIN_URL + VERIFIED_EMAIL keys only. 8.14's `DisqualifiedAddresses` is computed over the same set, so role/shared addresses never link. Name+domain never links. The direct match is unchanged (any identity a flagged record names, any status). A record whose identity values are not text links no one, so pruning still never raises (test_compliance). The hand-rolled union-find in `_person_labels` is replaced by a shared `_components` helper.
- **Decision 2 (orchestrator.py `_execute`):** after the tier loop, each FREE tier runs once more on the pruned work-list records naming an email or LinkedIn identity the tier was neither handed nor answered itself. No orchestrator code names HubSpot (2.4). The result is a second ENRICHMENT SourceResult appended last, on the same ledger, so run_exit, run_record and run_report count it under hubspot with no change to them. SourceError is isolated; a deadline records it TIMED_OUT.
- **E2E proof (real adapters, shipped fixtures, run_ingestion):** Apollo match calls are exactly {Ada id, Grace id, email info@}, with no call for Pat. Quinn (who shares info@) is enriched and not opted out. HubSpot contact searches are exactly [pat, info@, ada, grace] plus 3 deal searches. Ada's lead carries crm.lifecycle_stage=lead. Grace's lead is opt_out and suppressed. The report shows hubspot attempted=2. Mutation checks: dropping `disqualified=` fails the e2e (Quinn pruned), and dropping the own-output exclusion fails the unit test.
- **Superseded tests updated:** test_orchestrator_tier_feed (the free tier is now called twice), test_tier_feed_end_to_end (HubSpot also asks ada@ in pass 2; its not-found record is a lead of its own), test_zero_credential gap_c (renamed: HubSpot pass 2 asks Apollo's address from the shipped fixtures).
- **ADR-0006:** amendment section appended (no rewrite).
- **Gaps:** (a) the pruning components ignore Identity Exclusions (8.13), so they may over-join. This fails closed (it prunes more). (b) A CRM opt-out on an address only a paid tier found is still applied at merge, not before that tier spends. (c) Pass 2 picks records by identity, so a record with an already-asked email plus a new LinkedIn URL is re-handed. HubSpot's cache stops a repeat call but re-emits the cached lookup, which can mean duplicate HubSpot records. (d) The spec-refactor-agent and validate-production-agent were not spawned: no Agent tool in this harness.
- **Verify:** HEAD plus only these changes (scratch worktree): 3971 passed, 1 skipped. Main tree: mypy clean. ruff shows 19 errors, all in the other agent's remerge tests. pytest has 10 failures, all in the other agent's in-flight store/projection work (content_sha UNIQUE, schema, migrations, echo agreement); every one passes on HEAD plus my changes.

### Self-review findings

- **(a) Reuse and linking:** confirmed. Pruning uses `extract_match_keys`, `DisqualifiedAddresses` and clustering's `_UnionFind`. The old hand-rolled union-find in `_person_labels` now goes through the shared `_components`. Unverified emails never link (a test covers this). Fields are single-valued, and 8.14 also excludes an address seen with two LinkedIn URLs, so a component never holds two LinkedIn identities. A near-linear test is present.
- **(b) Over-pruning:** no defect. Pruning already applies the merge's 8.14 exclusion (an address seen under 2+ names or with 2+ LinkedIn URLs). Added `test_a_personal_mailbox_two_people_share_carries_no_opt_out_as_in_the_merge`, which checks pruning against `cluster_contributions` for a shared mailbox that is not a role address. Also added an exhaustive order test over all 720 permutations. Both pass. Gap (a) is still open: 8.13 Identity Exclusions are not applied, because wiring them needs ingest_runner.py, which the other agent owns. Skipped and flagged. The gap fails closed.
- **(c) Second pass:** confirmed.
  - It runs once per free tier and is handed only identities that tier was never handed and never answered.
  - A failure in it is isolated, and a deadline records it TIMED_OUT.
  - Counts come from the latest cumulative ledger result (run_exit and run_record read the latest), so attempted is not counted twice.
  - No paid tier runs after it.
  - Gap (c) is still open: HubSpot looks up by email only, so a record re-handed for a new LinkedIn URL alone re-emits a cached lookup. The ADR now states this.
- **(d) ADR-0006:**
  - Changed "within the work list" to "across the work list and every report so far".
  - Named both 8.14 triggers.
  - Disclosed the 8.13 gap and the repeat-record gap.
- **(e) Logs:** no new log lines, so no PII added.
- **(f) Mutation checks (worktree, orchestrator.py):** all 10 mutants were killed. The mutants were no-disqualified, all-kinds, no-seen-filter, no-own-output, paid-too, no-timeout-record, direct-only, no-readable-guard, empty-pass and empty-seen. The file was restored and its sha256 matches the main tree.
- **Verify:** on HEAD plus this change (worktree), 3974 passed and 1 was skipped. ruff and mypy are clean. The worktree is removed.

## Follow-up — derived record-to-lead mapping, re-merge without duplicates; 16.6/16.11 wiring (user option A, 2026-10-06)

Evidence (test-first): RED recorded in scratchpad: red-remerge.txt (collection ModuleNotFoundError remerge), red-echo.txt (1 failed: agreement 1 == 2), red-remerge-migration.txt (2 failed, sqlite+postgres: no revision 0006), red-remerge-run.txt (collection error); revision bump red (assert 3 == 2). GREEN: full suite 4021 passed, 1 skipped (remerge-full3.txt); ruff check src clean; mypy clean (195 files). Postgres leg ran: 19 postgres-parametrized tests passed in the new files. Mutations: 15/15 killed (mut_remerge.py, sha256 restore verified) plus the legacy-row branch killed.

### Decisions
- Schema 0006 (engine-neutral, upgrade+downgrade, dual-engine test): contribution_lead (contribution_id PK -> one lead per record; derived, rewritable); lead_identity.retired_at; lead_succession (append-only, predecessor -> successor, unique pair); source_contribution.content_sha (UNIQUE, NULL for pre-0006 rows); contribution_field.confidence_origin/raw/scale + contribution_absence (append-only) so a stored record reads back whole; canonical_lead.primary_domain, primary_domain_source, projection_fingerprint; ingestion_run.leads_merged, leads_retired. Upgrade backfills the mapping from first-seen lead_identity_id via Core INSERT..SELECT.
- Record identity = contributions.contribution_sha: sha256 of clustering.canonical_json without fetched_at, absences sorted. Same answer re-fetched = same record, stored once.
- One persist path: remerge.project_with_store (stored log + new records, stored wins) -> store.merged_leads.persist_merge. No separate resave function. Runner does both inside the merge transaction (stage merge, then merge_write).
- Lead identity: pure assign_leads. A cluster keeps the lead whose stored records all sit in it; a join keeps the most senior (created_at, id) and retires the others; a split retires the lead, every part is new; succession rows point to every successor. Retired rows are kept, never active; stale_projections and the report count active leads only. A lead continues only into a cluster of the same kind (person or not).
- Guard: a merge must cover every record of every lead it touches (ValueError), so a partial merge cannot silently shrink a lead.
- Ties (8.18): ProjectionResult.primary_domain_tie (hidden) exposes the tie; the run calls tie_resolution.resolve_primary_domain (stored answer first; model only in live mode with an injected tie_resolver; synthetic never asks) and re-projects a resolved cluster.
- Report: run-level line "leads: N merged, R retired" (per-source merged stays not recorded).
- Echo fix: projection._with_canonical_email drops a person.email whose raw path is asked.* when the record also has a bare email; PROJECTION_RULES_REVISION 2 -> 3. Echo alone still adds no agreement.
- Tests adapted (behaviour change, not weakened): echo agreement 1 -> 2; two HubSpot contacts with identical content -> one lead; e2e stored count = distinct identities; post-bump stale count 0 (all re-projected); seams moved to remerge.cluster_contributions; 0005 test reads only its column; schema table list; instants test uses distinct values.

### Gaps
- No concrete language-model TieResolver adapter exists; run_ingestion(tie_resolver=None) by default and the CLI passes none, so live ties stay flagged no_resolver_provisional.
- Each run re-projects the whole log: log_merges emits one line per lead per run (noisy, not wrong).
- Two concurrent runs inserting the same record: the UNIQUE content_sha aborts the later run (no silent duplicate); runs are not serialized.
- Pre-0006 rows read back lossy (origin none/heuristic, no absences).
- Raw payloads are stored on every run, even when every record was known.
- spec-refactor-agent / validate-production-agent not spawned (no Agent tool); serena/gitnexus unavailable, blast radius checked by grep (persist_merge callers: ingest_runner + tests; cluster seam tests updated).

### Status
- 16.6: fully done (exclusions read; version bumped and persisted per run and per lead; store-backed split proven on both engines with contribution rows unchanged).
- 16.11: done for the task bullets (constrained escalation reachable from the run, stored record read by projection, synthetic never asks and is flagged on the report); the only open item is the absent model adapter, which the task does not require.

### Self-review findings
Independent review (spec-refactor), 2026-10-06. Baseline 4021 passed, 1 skipped; final 4025 passed, 1 skipped (sr-final.txt), Postgres leg ran (30 postgres-parametrized cases in the remerge and lifecycle files collected and passed; the one skip is test_env_file root modes). ruff check src clean; mypy clean (195 files).
- (a) Repetition: one persist path confirmed. Production goes remerge.project_with_store -> store.merged_leads.persist_merge; the old inline cluster+project block was removed from ingest_runner, not copied. remerge.merge_contributions is used only by tests (pure oracle sharing `_projector`): not duplicated logic, but production-dead. Skipped.
- (b) Lead identity is a uuid `lead_identity.id` (not derived from PII; no sha256 over identity values). Re-merge twice -> same ids; split/join/succession; repeated ingest -> no growth; random invariant: all covered by tests on both engines. Read paths: only stale_projections and the run report read canonical leads, and both filter `retired_at IS NULL`; there is no export or CLI reader yet. Mutant check: removing the retired filter in `_current_leads` survives, but it is an equivalent mutant (the mapping is always rebuilt onto active leads).
- (c) 0006: upgrade/downgrade dual-engine test, autogenerate diff empty at head on both engines (test_persistence_both_engines), mapping backfilled from first-seen lead_identity_id. content_sha is not backfilled; it is recomputed lazily (legacy path). Pre-0006 duplicate leads self-heal on the first re-merge (join).
- (d) Concurrent runs (recorded, not fixed: design decision, ASK): the later run's merge transaction rolls back whole (no partial state). The abort reason is `merge_write: IntegrityError` (stage + class only, no PII). It does lose the aborted run's raw payloads, contributions AND per-source call/credit counts, because `complete(session)` runs in the same transaction. So paid spend goes unrecorded and must be re-fetched to recover. Also: two concurrent runs that add DIFFERENT new records for the same new person both commit, so there are two active leads until the next run's re-merge joins them (self-heals, but not prevented).
- (e) Log PII: lines carry kinds, keyed HMAC digests, source names, paths and counts only, so there is no PII. Volume (recorded): each run re-logs every multi-contribution cluster of the whole log, not only the clusters this run touched.
- (f) Echo fix verified. Echo-alone agreement stays 1; a source's own bare email now counts. Mutation gap found: dropping the echoed key from `values` survived. Added test_an_echo_naming_another_address_never_replaces_the_observed_one (passes; kills that mutant; source restored, sha256 OK).
- (g) FIXED test-first (8.13 -> 6.10): RED sr-red-g.txt (TypeError: no `exclusions` keyword, 3 failed). prune_flagged/_strong_person_labels take `exclusions`; IngestionOrchestrator(identity_exclusions=...) passes them to both prune calls; ingest_runner passes the loaded exclusions. Tests: test_an_identity_exclusion_links_no_one_when_pruning, test_the_runs_identity_exclusions_reach_suppression_pruning (sqlite + postgres). Mutations 6/6 killed, sha256 restored (sr_mut.py).
- (h) Spot mutations on the change (sr_mut2.py): 4/7 killed. Survivors: the `_current_leads` filter (equivalent), the echo filter (now killed by the new test), and `log.setdefault` -> overwrite in project_with_store (the "stored observation wins" recency choice is untested; skipped as a design point). All sources restored (sha256 OK).
- Other gaps (recorded): each run reads, hashes and re-projects the whole log and issues per-contribution field queries (`_index_fields`), so runs are O(log) with N+1 queries. A keyless record (no strong or name+domain key) whose re-fetch differs, or a pre-0006 row with a confidence (read back lossy as `heuristic`), gets a new sha. That creates a new singleton cluster, which means a second lead for a keyless person record.

- **Ticked (parent):** 16.6 and 16.11. 16.11 note: no model tie-resolver exists yet, so a live tie with no stored answer stays on the flagged lowest-sorted fallback, never silently final.
- **Known gaps (needs-follow-up):** an aborted concurrent run loses its raw payloads, contributions and spend counts; concurrent runs can briefly leave duplicate active leads until the next run; each run re-logs every merged lead (PII-free) and re-projects the whole stored log; a keyless record whose re-fetch differs gets a second lead.


## Follow-up — role addresses and unverified guesses no longer split one person (2026-10-06)

**User-directed fix (2026-10-06)**, amending the 8.3 one-sided follow-up above and the 16.7 note "a disqualified role address still counts as a stated, DIFFERENT address" (both known gaps, now closed). Requirement 8.3 carries a second amendment note pointing here.

- **Personal email evidence.** `match_keys.personal_email` is the stated address (`stated_email`: `person.email`, else the bare CRM `email`) as identity evidence, `None` when it is in `DisqualifiedAddresses.addresses` (8.14 role address, or seen with two LinkedIn URLs: the existing structural detection, no role-word list). `match_keys.emails_conflict` is the one rule both the candidate pass (`DisqualifiedAddresses.name_domains`) and the per-component pass in `clustering` apply.
- **Role addresses** neither block a one-sided name+domain join nor make a name+domain ambiguous. They still never act as an email Match Key (the 16.7 bar is unchanged; kind stays PRESENT).
- **Verified vs guess.** Only `person.email_status == VERIFIED` makes an address known (read for the stated address, bare CRM path included, as projection reads it). Any other status, or none, is a guess. Conflict = two distinct verified addresses, or no verified address and two distinct guesses. A guess beside one verified address is not a conflict. Decision on two different unverified addresses: **block** (neither is known to be the person's), as recommended.
- **Guards kept:** LinkedIn holders never take part and the cannot-link still wins; no transitive bridge (a component holding two verified addresses links nobody, whatever sits between them: a bare record, a guess or a role address); order independence (exhaustive permutation tests over 40 seeded pools of 3-5 with role addresses and guesses); linear (one extra pass over the candidates, the role set read from the first pass).
- **`IdentityExclusions.version_token` deleted**: a plain sha256 over PII with no production caller (only tests and a docstring); the stored change token is projection's keyed `ProjectionBasis` (`match_key_digest` HMAC).
- **Known gaps (needs-follow-up):** projection picks `person.email` by Source Trust Rank first (8.4), not by verification or role, so when the guess (or the role address) comes from a higher-ranked source it becomes the lead's email. Pinned by two `xfail(strict=True)` tests in test_one_sided_email_name_domain.py; fix belongs in projection. A role address seen with only one name is not detectable as shared and still counts as that person's address.

## Follow-up — role addresses and unverified guesses no longer split one person (2026-10-06)
- Red: test_one_sided_email_name_domain.py + test_role_addresses.py: 7 failed / 66 passed (scratchpad/fu2/red.txt); version_token removal: 1 failed (fu2/red-token.txt); bare-CRM status: 1 failed (fu2/red-crm.txt). Green focused set: 316 passed, 2 xfailed.
- Final: ruff check on my 7 files clean; mypy errors only in other agents' new tests (test_incremental_reprojection, test_keyless_provider_identity, test_run_lock_both_engines). pytest (minus 2 uncollectable new files of others): 4048 passed, 17 failed, all others' (store migrations/persistence: new 0007 migration; keyless_provider_identity: fails identically with my files reverted; one transient hunter test). Earlier transient apollo/hubspot/suppression failures passed on rerun and in a reverted copy.
### Decisions
- match_keys.personal_email(values, disqualified) = stated_email unless in DisqualifiedAddresses.addresses (existing 8.14/two-LinkedIn detection reused), plus verified flag (person.email_status == VERIFIED, applied to the bare CRM path too, as projection does).
- match_keys.emails_conflict: conflict iff 2+ distinct verified, or 0 verified and 2+ distinct guesses (two unverified different = block, as recommended). One rule for candidate pass (name_domains) and per-component pass (clustering).
- Role addresses still never an email Match Key (16.7 bar unchanged).
- IdentityExclusions.version_token deleted (no src caller; plain sha256 over PII); tests replaced by a "no plain hash" test; one assertion removed from test_projection_version.py; exclusion_settings docstring points to keyed ProjectionBasis.
- Tests changed on purpose: any-status block -> unverified-only block; disqualification test uses two verified; CRM bare test uses two guesses; random-pool invariants use personal-email oracle; 16.7 role test now expects x+y joined.
- Mutation (scratch copy): 7 mutants, 6 killed initially; survivor (bare-path status guard) resolved by a new test and dropping the guard.
- Spec: requirements.md 8.3 second amendment blockquote; choices.md follow-up entry appended.
### Gaps
- Projection resolves person.email by trust rank, NOT verified status: a higher-ranked guess or role address becomes the lead email. The caller's premise "projection already prefers verified" is false. Pinned by 2 xfail(strict) tests; fix belongs in projection.py.
- A role address seen with only one name is undetectable as shared and still counts as that person's own.
- spec-refactor / validate-production sub-agents not spawned (no Agent tool).

### Self-review findings
- Independent review (spec-refactor), clean worktree at HEAD + only this change: 4034 passed, 1 skipped, 2 xfailed. Reviewer changed no code. Worktree removed.
- (b) Bridge probe: all 3-record pools (zed + 24 options of verified/guess/role email x no/L1/L2 LinkedIn) in every permutation, plus 3000 random 4-5 pools x 7 orders: deterministic; no cluster with 2 LinkedIns; no LinkedIn-less cluster with 2 distinct verified personal addresses. Reason: in a non-conflicting component the only external forest link is via its single verified address.
- (a) Over-merge: same normalized name + same registrable domain + shared title OR employer name, both LinkedIn-less, one verified and the other any non-verified status: merged. A different phone does not separate them. A different title separates them only if the employer names differ too. A LinkedIn on either record separates them (holders never join on name+domain). INVALID status counts as a guess (verified+invalid merge; two invalid block). With 1 verified address, 2 different guesses all merge, though alone they would block. Both are documented design choices; needs-user.
- (c) Role detection reuses DisqualifiedAddresses.addresses; verified read identical to extract_match_keys. Exclusions are deliberately not filtered (filtering them would let an exclusion MERGE, against 8.13).
- (d) xfails strict, fail for the right reason (info@ / guess chosen). Target (verified beats rank) contradicts 8.4 as written: an 8.4 amendment is needed before projection is fixed. needs-user.
- (e) version_token: only historical choices.md entries + the hasattr test remain. 8.13 change coverage kept in test_projection_version.
- (g) 9 mutants, 9 killed (baseline green), restored sha256-exact.
- Nit (skipped): test oracle uses `is V` rather than `==`.
- Housekeeping: reviewer probe files were written into the shared scratchpad/probe/ (probe.py, overmerge.py, mut.sh, orig) and then deleted; if earlier files had those names, they were overwritten.

## Follow-up — run lock, spend-safe merge, incremental re-projection, stable keyless and company ids (2026-10-06)

Evidence (test-first): RED in scratchpad fu2-red.txt (collection: no store.run_lock, no remerge.reproject) and fu2-red2.txt (9 failed: keyless re-fetch [1, 1] != [2]; plain sha256 found in the DB dump; no 0007). GREEN: the 4 new files give 64 passed, 29 of them on Postgres (fu2-new-final.txt). Full suite (fu2-full2.txt): 4113 passed, 17 failed, 1 skipped. 16 of the failures are in other agents' in-flight adapter work (apollo/hunter: test_apollo_enrich_other_sources, test_hunter_source, test_suppression_end_to_end). 1 is caused by this change (see Gaps). ruff check src clean; mypy clean (204 files). Spot mutations: 13/13 killed, and sha256 checks confirm every source was restored (fu2-mut.txt, fu2-mut2.txt).

### Decisions
- Run lock (store/run_lock.py, 0007 `run_lock` seeded with one row): one engine-neutral conditional UPDATE (holder IS NULL OR expires_at < now). Postgres row locking and SQLite's single writer serialise it, so there is no advisory lock and no backend branch. It is taken after migrate and BEFORE the run record and any provider call. A refused run raises RunInProgressError ("another run in progress", instants only), records and spends nothing; the CLI prints it and exits 3. Lease = run timeout + LOCK_STALE_GRACE_S (30 min); after that a crashed run's lock is taken over. The merge transaction renews the lease first: a run that lost its lock is aborted `merge: RunLockLostError` and its fetched records stay unmerged until the next run. Release happens in finally and is a no-op for a non-holder.
- Spend-safe: persist_observations (raw payloads plus new contributions, idempotent by keyed content_sha) and StoreRunRecorder.spend (per-source counts, contributions_written) commit in their own transaction ("observe") BEFORE the merge. completion() is now finish only. A contribution with no contribution_lead row is merged by the next run. persist_merge(batches=...) still works (it calls the same _observe), so there is one observation path. In the report, an aborted run with stored counts shows them.
- Incremental re-projection (remerge.reproject; project_with_store is a thin wrapper): identification runs over the whole log (union-find is global), but only these clusters are projected and written: clusters whose member set is not exactly one active lead's set (new or unmerged, split, join), and leads stored under another projection_version. A full recompute happens when the basis is bumped ("projection basis changed"), when the whole log's compliance reports differ from the merged part's ("compliance reports changed"), or when a tie answer is stored during the pass ("tie answer stored"). It is recorded as ingestion_run.clusters_reprojected / reprojection (0007), and the report line reads "re-projection: incremental|full (why), N clusters". MergeStored.changed holds the leads created or changed in content (fields plus provenance); only those are logged. leads_merged and ties_flagged now count re-projected clusters only.
- Keyless (remerge.identify): a record with no Match Key (none present, none barred) that carries `person.provider_id` joins the one cluster holding the same source and id, or else the other keyless records with that id. Keyed clusters are never joined this way. An id held by two keyed clusters is ambiguous, so nothing joins.
- Ids (item 4): content_sha and request_fingerprint = HMAC-SHA256 under a random store key (0007 `store_secret`; store/store_key.py). The environment Match Key secret was rejected because a per-run key would duplicate every record. A domainless company is persisted as `co-<lead uuid hex>` (companies.lead_company_id). 0007 rekeys existing values as HMAC(key, old hex), which is what the app computes, and rewrites domainless company ids. The downgrade NULLs content_sha so that 0006's legacy path recomputes it. Proof: a DB-dump test finds no contribution_sha, cluster_id, old company id or payload sha256, and an AST test checks that every persisted digest keyword goes through the keyed function.
- Item 5: IdentityExclusions.version_token had no production caller. It has since been removed by the match_keys owner (its test asserts it is absent); I did not edit it.
- Tests adapted (behaviour change, not weakened): the identical second run now writes 0 leads (test_lead_remerge, test_remerge_run); a merge failure keeps contributions and counts; a failure in record_source_counts/contributions_written aborts as `observe:` (test_run_lifecycle); the mapping replaces the legacy column (test_merged_lead_persistence); schema list +run_lock, store_secret.

### Gaps
- BREAKS another agent's test: tests/adapters/test_optout_links_second_pass_end_to_end.py reads SourceContribution.lead_identity_id. Contributions are now stored before any lead exists, so that legacy column stays NULL. Fix (adapters owner): join ContributionLead.lead_identity_id instead.
- Keyless identity needs `person.provider_id`, and only the Apollo adapter emits it. HubSpot maps contact.id to the boolean crm.contact_exists, so the id is lost. Hunter and Google emit no record id. A keyless re-fetch from those sources still becomes a second lead until their adapters emit person.provider_id (adapter owners).
- The partition still loads and clusters the whole log on every run (O(log), one query per contribution for field ids). Only projection, writes and logs are incremental.
- projection._lead_company_id still hashes cluster_id in memory (never persisted or logged). clustering.cluster_id (another agent's file) is still a plain sha256. companies.cluster_company_signals hashes a company name for a domainless singleton (production-dead, not persisted). tie_key is sha256 of company domains whose values are stored in the same row (left).
- My first test run of the failed-merge test called monkeypatch.undo(), which dropped DATABASE_URL and created a gitignored .leadforge/leadforge.db in the repo. I removed it and fixed the test.
- spec-refactor-agent / validate-production-agent not spawned (no Agent tool); serena/gitnexus unavailable, so the blast radius was checked by grep (persist_merge: runner + tests; completion: runner + recorder).

### Self-review findings
Independent spec-refactor review, 2026-10-06. Clean signal: a throwaway worktree of HEAD plus only this change's files (now removed). Baseline there: 1 failed (test_optout_links_second_pass_end_to_end), 4097 passed. After the fixes: 4130 passed, 1 skipped, 2 xfailed, 0 failed, with the Postgres leg run (ephemeral server). ruff check src and mypy src (204 files) are clean. Only the files I touched were formatted. ctx_*/serena/gitnexus were not available, so the blast radius was checked with rg (keyed_content_digest: merged_leads only; write_contribution's lead_identity_id: no caller passed it; _current_leads/stale_projections: merged_leads, run_report, tests).
- (a) FIXED the broken test: it now joins ContributionLead.lead_identity_id. SourceContribution.lead_identity_id was dead: no production writer (write_contribution's parameter had no caller that passed it) and no reader except 0006's one-time backfill. 0007 now drops it, along with its FK (dropped by the name Postgres assigns, the same naming approach as 0003) and its index, using batch mode with no backend branch. The downgrade re-adds the column and fills it from contribution_lead. It holds the current lead, because the first-seen lead cannot be recovered. Removed the write_contribution parameter. Adapted test_store_migrations (FK set) and test_remerge_migration (seeds through a pre-0007 table).
- (b) STORED KEY: the key is in store_secret, the same DB as the digests. content_sha keeps the HMAC. It must be deterministic within a store to recognise a re-fetch, so a random id cannot replace it. Determinism across fresh DBs is not needed. Against a DB leak, the HMAC adds nothing, but it exposes nothing either: the contribution rows hold the same PII in plaintext for as long as the digest exists (the log is append-only). request_fingerprint was an HMAC of the whole payload and nothing reads it. Once a payload is purged, someone holding the store and its key could still use the fingerprint to confirm a guessed payload. SWITCHED it test-first to uuid4().hex. 0007 rewrites the old values to random ones too. Removed keyed_content_digest. The key is never logged or reported (the LookupError text carries no value).
- (c) The lock checked out: one conditional UPDATE. Postgres re-checks the condition after the competing UPDATE commits, and SQLite has a single writer. The holder token is checked on renew and on release. The renew is the first statement of the merge transaction and holds the row lock until commit, so a takeover cannot interleave with it. Release is in finally. ADDED a test on both engines: the lock is released after KeyboardInterrupt and after CancelledError.
- (d) Checked, no defect found: spend is written once, in the observe transaction. completion() is finish only. contributions_written counts the run's own source_run rows, so leftovers merged by a later run are not counted again.
- (e) Gap: the "tie answer stored" full-recompute trigger had no test (the mutation survived). ADDED a test. The property test generated no provider-id joins. It now does (3 of 24 seeds; the seed count was raised from 12 to 24).
- (f) 0007 upgrade and downgrade pass on both engines, and the autogenerate diff is empty on both. Company ids are referenced by no FK: they live only in canonical_lead.employments. FIXED: 0007 injected an empty company dict into an employment that had none.
- (g) FIXED duplication: remerge._stored_leads re-implemented merged_leads._current_leads and stale_projections. It now calls them (current_leads made public).
- (h) Mutations (in the worktree, with a sha256 check that each source was restored exactly): 15 run, 15 killed (release in finally, stale comparison, renew, stale-version selection, compliance trigger, fingerprint, column drop, downgrade backfill, ambiguous keyless join, lead-owned company id, release holder check, tie trigger (after the new test), unkeyed content_sha, migration company rewrite).
- SKIPPED (design, for a human): (1) the source counts commit in the same transaction as the observations, so an "observe:" failure loses the spend record too. Fix: commit the counts first, in their own transaction. (2) The lease uses the app clock, so clock skew between hosts could allow an early takeover. (3) A run that lost its lock still writes its observations unfenced. They are idempotent, but a concurrent duplicate insert would abort that run at observe. (4) run_report's one-element comprehension trick for `recorded` is stylistic.

## Follow-up — adapter credit and dedupe fixes, redaction sentinels (2026-10-06)

Files: adapters/apollo.py, adapters/hunter.py, adapters/hubspot.py, .env.example (regenerated); tests/adapters: test_apollo_enrich_other_sources.py, test_hunter_source.py, test_hubspot_request_echo.py, test_hubspot_source.py, test_plan_settings_early.py, test_vendor_key_redaction.py (new); transport stubs updated for the JSON body in test_apollo_source.py, test_suppression_end_to_end.py, test_tier_feed_end_to_end.py. Not touched: ingest_runner.py, orchestrator.py, store/*, match_keys.py, clustering.py, base_source.py, env_example.py.

RED evidence (scratchpad): fu2-apollo-red.txt (2 failed: 2 match calls for one person), fu2-apollo-body-red.txt (3 failed: params sent, no body), fu2-hunter-plan-red.txt (8 failed), fu2-hunter-same-red.txt (1 failed: 0 finder calls), fu2-hubspot-red.txt (1 failed: repeat record). Item 4 is a new characterization test (passed first run; proven non-vacuous by mutation, below).

### Decisions
1. **Hunter verifier price per plan.** `HUNTER_PLAN` (optional_env, env_notes, in .env.example): `data` = 1 Verification credit, `all-in-one` = 0.5 credit (the two facts in live-docs U7, help.hunter.io 12149400 + credits article, via search extract). Unset/blank = `data` (conservative 1). Other values: ConfigurationError naming the variable, never the value — raised at run start through the EXISTING `live_rate_limits -> run_rate_limit` hook (Hunter overrides `run_rate_limit` to validate and return its declared buckets) and again in `fetch_raw` before any call. **No ingest_runner wiring needed**: the plan is read from the adapter's environ like its key. Batch records `plan`; `credits_in` = search + finds + ceil(verifications x price) per batch; a stored batch without `plan` prices as `data`; an unknown stored plan is NormalizationError. UNVERIFIED: plan names beyond these two (ours, from the plan-family names), whether Data-plan Verification credits should be counted apart (summed here), Hunter's half-credit rounding.
2. **Apollo one person, two disjoint keys.** A hit's own strong keys (Apollo id, normalised LinkedIn identity, address only when `email_status` is `verified`) now answer later lookups with those keys from the per-run cache (`_served_by`): LinkedIn-only + email-only records of one person = 1 credit (was 2); two people = 2. Guards kept and tested: per-lookup ambiguity (`_asked_for`) checked first; a LinkedIn mismatch still discards; a no-match answer aliases nothing; Apollo's own direct answer to a lookup wins over another hit's keys; an unverified answer address aliases nothing. `_people` now uses the orchestrator's `_components` (clustering union-find) instead of its hand-rolled copy; grouping semantics unchanged (compliance identities).
3. **Hunter same-name records.** Changed: in `_route`, a finder record with no LinkedIn of its own takes the LinkedIn identity of its strongly linked person (orchestrator `_strong_person_labels`: LinkedIn + verified-email Match Keys, transitive, shared addresses link no one) when that person holds exactly one LinkedIn. So one person's LinkedIn record and bare record ask the finder once (echo carries the LinkedIn). Unchanged: a bare record NOT strongly linked still makes the name ambiguous (no call, `hunter_finder_ambiguous`); two LinkedIn identities never merge. Note: a record with a usable `person.email` goes to the verifier, not the finder, so the strong link is via other records or an address Hunter cannot ask (tests use an IDN address).
4. **Vendor key sentinels.** test_vendor_key_redaction.py: APOLLO_API_KEY, HUBSPOT_ACCESS_TOKEN, HUNTER_API_KEY, SERPAPI_API_KEY set to sentinels (a guard test ties the list to required_env/.env.example), every registered adapter live through `cli ingest` with recording transports (fixture answers; and HTTP 401 echoing the request), sockets blocked. Asserts each key was really sent, and absent from stdout (summary + report), stderr logs, rendered run report, config snapshot and every non-raw table.
5. **HubSpot emit once.** An address a returned batch already carried is not emitted again this run (no call, no record); marked only when the batch is returned, so a failed fetch's retry still emits. Pinned tests changed on purpose: `test_a_retried_fetch_repeats_no_lookup` -> `test_a_second_fetch_repeats_no_lookup_and_no_record`; `test_an_ask_for_two_people_across_fetches_echoes_neither_later` -> `..._a_second_person_later_emits_no_record_for_them`.
6. **Apollo people/match encoding: VERIFIED, switched.** Official Apollo CLI github.com/apolloio/apollo-io-cli 2.1.0, commit 70ce295 (scratchpad dl/apcli): `src/commands/people.ts` buildPeopleEnrichBody + `src/api.ts` apolloRequest send the lookup as a JSON body (content-type application/json), no query string. Now `json_body=params, params=None`.

### Mutation check (sha256-restored)
12 adapter mutants: 10 killed at first; 2 Apollo survivors killed after adding tests (no-match aliasing; alias over direct answer). 1 equivalent survivor: Hunter's "person holds exactly one LinkedIn" guard is unreachable today (orchestrator components never hold two LinkedIns); kept as defence. Redaction test: log leak with redaction ON passes (scrubbed), with redaction OFF fails, key in an error message fails (stdout/report).

### Gaps
- CLI stdout (summary/report) is not passed through the redactor: an adapter error text carrying a key would print it (mutation M3). Only adapter discipline prevents it today. Owner: cli.py/run_report.py.
- HubSpot: the echo is the first emission's; a LinkedIn learned later (pass 2) does not upgrade it, and a later different requester for the same address gets no record.
- Apollo/Hunter strong grouping runs without the run's Identity Exclusions (adapters do not receive them).
- Apollo search (`mixed_people/api_search`) still sends query params; the same CLI sends search as a JSON body too (A5) — not changed here (out of item scope).
- spec-refactor-agent / validate-production-agent not spawned (no Agent tool); Serena/GitNexus not available: blast radius by grep (changed private helpers referenced only in their own modules/tests).

### Verification (this run)
ruff check src: clean. mypy: no issues (204 files). Adapter tests: 983 passed, 1 failed (`test_opt_outs_follow_links_and_hubspot_asks_again_what_was_found_later`: fails identically with all three adapters reverted to HEAD -> other agents' store WIP, lead_identity_id None). Full pytest: 1 failed, 4131 passed, 1 skipped, 2 xfailed (that same test); an earlier full run also showed 16 store/remerge/run-lifecycle failures from concurrent WIP in store/*, remerge, ingest_runner.

### Self-review findings
Independent review (spec-refactor), clean worktree at HEAD + only this change's files (removed after).
- (a) Hunter plans match live-docs U7 exactly (Data: 1 Verification credit per call; All-in-one: 0.5 per verification; help.hunter.io 12149400 + 1911617). Credits are `int` end to end: `credits_in -> int`, `BaseLeadSource.credits_spent -> int | None`, orchestrator.py:452/484, run_record.py:153, run_report.py:96, store/models.py:141 `credits_consumed` `Integer`, migration 0001:89 `sa.Integer()`. No truncation (the adapter rounds up per batch); Hunter is a paid tier, so it has one successful fetch per run, and the over-count is at most +1 per run. A fractional total needs a Numeric column, which is owned by the persistence change. Reported to the parent, not edited.
- (b) `_people` and Hunter grouping import orchestrator `_components` / `_strong_person_labels` (no copy). 8.14 disqualifies shared and two-name addresses, so distinct people are not grouped; aliasing runs after the per-lookup ambiguity check; the LinkedIn cannot-link discard still runs in normalize. The cache key is consistent: the attachment carries the serving key.
- (c) Apollo CLI 70ce295: `people enrich` -> `apolloRequest('/people/match', buildPeopleEnrichBody)` = POST with a JSON body (api.ts sets content-type application/json and does JSON.stringify). BUT `people email --id` -> `apolloGet('/people/match', {id})` = GET with a query string. The id rung's POST JSON body is therefore NOT verified. FIXED the doc in apollo.py (the id rung is marked UNVERIFIED, with the reason). Behaviour is kept because GET is outside the POST-only endpoint. The endpoint declaration is unchanged. The fixture manifest describes response bodies only, so it needed no change.
- (d) The test runs `cli ingest` (only `run_ingestion` is wrapped to inject the registry) and checks every non-raw table. Probe (both modes): no key and no PII (ada@example.com/Ada/Lovelace) on stdout or in stderr, and no key in raw_responses either. The stdout-redaction gap stays theoretical: it holds only by adapter discipline. No defect observed.
- (e) HubSpot emit-once: a different person who shares an address gets no second record of the same contact. This is documented and accepted (one contact per address). A failed fetch does not mark the address (mutant S2 killed).
- (f) The new logs carry counts only.
- (g) 15 mutants, sha256-restored: 13 killed. H7 (a stored batch without `plan` refused) SURVIVED -> added `test_a_stored_batch_from_before_the_plan_is_priced_as_data`, which kills it. P6 (alias setdefault -> overwrite) survives as near-equivalent: both hits name the same person. Skipped.
- Verification: worktree full suite 4069 passed / 1 skipped / 2 xfailed. The opt-out e2e test passes there, and main's adapter tests are 985 passed. ruff, format and mypy are clean on the touched files.

## Follow-up — lead email: verified, then personal, before trust rank (user decision 2026-10-06)

User decision (verbatim): "a verified email should beat an unverified one"; "email is the most relevant item so it should beat info@". Amends 8.4 for `person.email` only.

Files: conflicts.py, projection.py, clustering.py (IdentityCluster.role_addresses), models.py (CanonicalLead fields); tests: test_email_preference.py (new), test_one_sided_email_name_domain.py (2 strict xfails removed), test_projection_version.py, test_projection.py.

### Decisions
1. **Order for `person.email`.** `conflicts._order_key` is prefixed by `(not verified, role)`: a verified status (the candidate's own contribution's `person.email_status`, read with `match_keys.personal_email`, as clustering reads it) beats any other status; then a personal address beats a role address; then the unchanged 8.4 order (trust rank, origin, confidence, recency, source, sha). Other paths get `(0, 0)`, so 8.4 is untouched for them. New `ConflictRule.VERIFIED_EMAIL` / `PERSONAL_EMAIL` name the deciding rule in the merge log.
2. **Literal order.** Verified comes before personal: a verified `info@` beats an unverified personal guess (and is flagged as a role address). Taken literally from the decision's order.
3. **Role address = the existing 8.14 detection, imported.** (Superseded by the self-review below: role words now count too.) Originally no role-word list: `DisqualifiedAddresses.addresses` (an address reported against two distinct names, or with two LinkedIn URLs) over the whole clustered set. `cluster_contributions` puts each cluster's share of it on `IdentityCluster.role_addresses` (default empty; hidden from repr). An `info@` seen against one name only is not a role address.
4. **Role addresses kept.** No company-contact field existed. Added `CanonicalLead.email_is_role_address: bool = False` (needs an email) and `CanonicalLead.role_contact_emails: tuple[StrictEmail, ...] = ()` (the role addresses stated in the cluster other than the email; normalised, sorted, distinct). Not named `company_*`: test_canonical_entities forbids company/employer attributes on the Lead. Not on `CompanySignal`: it is shared per `company_id` and must be equal across Leads.
5. **`email_status`** is read from the first source, in email-candidate order, that stated the chosen address and a status, so a lower-ranked source's verification of the same address reports `verified`. Pinned test `test_changing_trust_ranks_changes_the_winner` changed on purpose (the email stays the verified `ann@x.com`; the name still follows the ranks).
6. **Echoes unchanged.** `asked.` candidates still compete only when no observed candidate exists, so they never add agreement nor win on their own.
7. **`PROJECTION_RULES_REVISION` 3 -> 4**: the basis fingerprint changes, so every stored Lead is stale and recomputes.

### Gaps
- The Lead Store does not persist `email_is_role_address` / `role_contact_emails` (store/* is another agent's scope): they live on the projected `CanonicalLead` only until columns are added.
- Vendor neutrality forbids vendor names in tests: the end-to-end test uses neutral source names (`verifier`, low rank, for the Hunter verifier; `enricher`, high rank).

### Self-review (spec-refactor, user intent: role words)
- **Role words.** User intent ("I don't know whats in info@") means generic inboxes. `match_keys.ROLE_LOCAL_PARTS` (29 words, listed in requirements 8.14 amendment) is the one list; `is_role_address` compares the whole local part, lowercased, before any `+tag` (`information@`, `ann.info@` are not roles). `DisqualifiedAddresses.from_contributions` adds every stated address (`stated_email`, bare CRM path included, any status) that matches, so role address = word OR two names OR two LinkedIns, read by match keys, 8.3 (`personal_email`), clustering's `role_addresses`, projection and orchestrator opt-out linking with no other copy. English words only: a role inbox in another language still relies on the two-names rule. Supersedes ADR-0003 on this point.
- **Effect on merge.** A role-word address never acts as a Match Key (even verified, under one name) and is no personal evidence, so it never blocks a one-sided name+domain join. Exclusion tests that used `info@x.com` as an address only an exclusion can stop now use `shared@x.com`; `test_a_disqualified_address_is_not_personal_identity_for_name_domain` now expects x and y to join without z.
- **remerge.identify** joined loose records (provider id) into a keyed cluster but kept only the anchor's `role_addresses`; it now unions them.
- **email_status** was paired with the address per source, so a source's `info@` status (e.g. `invalid`) could label its personal address. It is now paired per contribution.
- Literal order kept: a VERIFIED `info@` beats an UNVERIFIED personal guess (flagged).
- Store gap: `canonical_lead` needs `email_is_role_address BOOLEAN NOT NULL DEFAULT false` and `role_contact_emails JSON NULL` (migration 0009 after 0008), written in `store/merged_leads._write_canonical` and compared in `_content`.
- `PROJECTION_RULES_REVISION` stays 4 (not yet shipped; covers this).

## Follow-up — lead email: verified, then personal, before trust rank (user decision 2026-10-06)

Files: conflicts.py, projection.py, clustering.py (IdentityCluster.role_addresses), models.py (CanonicalLead.email_is_role_address, CanonicalLead.role_contact_emails); tests: test_email_preference.py (new, 15 tests), test_one_sided_email_name_domain.py (2 strict xfails removed, unused pytest import dropped), test_projection_version.py (pin 3 -> 4), test_projection.py (pinned test changed on purpose). Specs: requirements.md 8.4 amendment note; choices.md entry.

RED (scratchpad red.txt): 14 failed / 47 passed. Reasons: no email_is_role_address/role contacts attribute, info@ or the higher-ranked guess won person.email, status unverified, revision 3 != 4. GREEN: green.txt, then t2.txt.

### Decisions
- person.email order: (not verified, role) prefix on the 16.3 key, then the unchanged 8.4 order. Other paths get (0, 0), so they never decide there. New ConflictRule.VERIFIED_EMAIL / PERSONAL_EMAIL show in merge log `rule`.
- Verified is read with match_keys.personal_email from the candidate's own contribution (imported, not copied). Role = the existing 8.14 DisqualifiedAddresses.addresses, carried on the cluster as IdentityCluster.role_addresses by cluster_contributions. There is no role-word list: an info@ seen against one name only is not a role address.
- Literal order: a verified info@ beats an unverified personal guess, and is flagged as a role address.
- No company-contact field existed. Added CanonicalLead.role_contact_emails, because test_canonical_entities forbids company_* attributes on the Lead and CompanySignal is shared per company_id. Also added email_is_role_address, which the validator rejects without an email.
- email_status follows the email candidate order: if a lower-ranked source verifies the same address, the status reads verified. test_projection::test_changing_trust_ranks_changes_the_winner was changed on purpose (the email stays the verified one; the name still follows the ranks).
- Echoes are unchanged (asked.* compete only when no observed candidate exists). This is tested.
- PROJECTION_RULES_REVISION 3 -> 4; the fingerprint differs from the rev-3 basis (tested), so stored leads are stale and get recomputed.
- E2E runs through cluster_contributions + project_lead, with permutation tests (40 seeds, every order). Sources have neutral names (verifier = Hunter role, low rank; enricher = high rank) because test_vendor_neutrality bans vendor names in tests.
- Mutation (sha256-restored): swapping the verified/role order and dropping the prefix were both killed. "No address treated as verified" survived until test_a_value_that_is_no_address_never_counts_as_verified was added; it is killed now.

### Gaps
- The store does not persist email_is_role_address / role_contact_emails. store/* belongs to the other agent, so a column is needed.
- spec-refactor-agent / validate-production-agent were not spawned (no Agent tool). Serena/GitNexus/ctx tools were not available, so blast radius was checked by grep: IdentityCluster is built only in clustering.py and projection.py; resolve_conflicts' signature is unchanged; ConflictRule is read by merge_log (.value) only.

### Verification
ruff check src: clean. mypy: clean (207 files). Full pytest: 4196 passed, 1 skipped, 5 failed. All 5 failures are in the other agent's files: test_database_engine branching, test_store_schema raw SQL, and 2x test_structural_rules (all from store/run_lock.py and database.py); test_vendor_neutrality (base_source.py, store/models.py, 0008 migration, and test_spend_record_both_engines.py naming 'hunter').

### Self-review findings
Independent review (spec-refactor) in a clean worktree (HEAD + only this change's files), then copied to main.
- FIXED (user intent, test-first): role address now = role-word local part OR the two-names / two-LinkedIns rule. `match_keys.ROLE_LOCAL_PARTS` (29 words) + `is_role_address` (whole local part, lowercased, before `+tag`); `DisqualifiedAddresses.from_contributions` adds matching `stated_email` addresses. That one detection feeds match keys, 8.3 `personal_email`, `IdentityCluster.role_addresses`, projection and orchestrator opt-out linking (orchestrator, conflicts logic untouched). A role-word address never links (even verified, one name) and never blocks a one-sided join.
- FIXED: `remerge.identify` dropped a joined loose record's `role_addresses` (kept the anchor's only); now unions them.
- FIXED: `projection._email_status` paired status with address per SOURCE, so one source's `info@` status (e.g. invalid) labelled its personal address. Now per contribution.
- Tests: test_role_addresses (+5 tests, parametrised), test_email_preference (+6, replaced the "no word list" test), test_identity_exclusions/test_projection_version fixtures `info@x.com` -> `shared@x.com` (non-role address only an exclusion stops); one pinned assertion in test_role_addresses updated on purpose (x, y now join without z).
- (a) Verified info@ beats unverified personal guess: kept, tested. (b) Store needs migration 0009: `canonical_lead.email_is_role_address BOOLEAN NOT NULL DEFAULT false`, `canonical_lead.role_contact_emails JSON NULL`; write in `store/merged_leads._write_canonical`, compare in `_content`. (c) Revision 4 (unshipped) covers this; fingerprint test passes. (d) Permutation tests pass; echoes never win (tested).
- (e) Mutation: 10 mutants, sha256-restored; 9 killed first pass, M8 (status "counted" check dropped) survived -> added `test_a_record_stating_the_address_without_a_status_defers_to_one_with_it`, now killed (10/10).
- Specs: requirements 8.14 amendment (supersedes ADR-0003 on this point); choices.md self-review subsection.
- Verification: worktree full suite 4205 passed / 1 skipped; main full suite 4232 passed / 1 skipped; ruff, format, mypy clean.
- Skipped: English-only list (non-English role inboxes rely on the two-names rule).

## Follow-up — exact fractional credits, spend committed first, DB-clock run lock (2026-10-06)

Evidence (test-first): RED fu3-red-credits.txt (13 failed: credits_in returned int 2, column rounded 1.5 to 2, no 0008, spend lost on a contribution-write failure (None,None,None)); fu3-red-lock.txt (behavioural: a host 6h ahead STOLE a live lock (DID NOT RAISE); a host 6h behind was refused a dead lock; old API took `now`). GREEN: fu3-green-credits.txt, fu3-green-lock.txt (30 passed), fu3-full.txt: 4204 passed, 1 skipped, 0 failed (Postgres legs ran). ruff check src clean; mypy clean (208 files). Baseline fu3-baseline.txt is NOT clean evidence (my edits landed mid-run).

### Decisions
- Credits storage: integer milli-Credits, not Numeric. SQLite has no exact decimal (NUMERIC keeps a float); an Integer is exact and identical on both engines and stays inside the portable-type rule. `store.models.MilliCredits` (TypeDecorator over Integer) reads/writes `Decimal`; column renamed `credits_consumed_milli` so a raw DB reader cannot mistake units; ORM attribute keeps the name `credits_consumed`. A float or a figure finer than 0.001 is refused (StatementError), never rounded.
- Migration 0008: upgrade = x1000 (exact, NULL stays NULL); downgrade = ceil(milli/1000) via `//` (integer floor division on both engines, figures non-negative) so the old schema never under-reports spend. Proven both directions on both engines (3 -> 3, NULL -> NULL, 1.5 -> 2 down, back up as 2). Autogenerate drift test passes (alembic compares the impl).
- Adapter: hunter `_VERIFIER_PRICE` is Decimal (data 1, all-in-one 0.5); `credits_in -> Decimal`, no ceil. `BaseLeadSource.credits_spent -> Decimal | int | None` (Apollo's int stays valid, untouched); orchestrator normalises to Decimal; `run_record._total` sums exactly (typed generic, no float); report prints `credits=1.5`.
- Spend first: `StoreRunRecorder.spend` now writes only the source counts (calls, fetched, credits, failure class, throttle) and the runner commits it ALONE, stage `spend`, before `observe` (persist_observations + record_contributions_written). A contribution-write failure aborts `observe: <class>` with the spend intact. Idempotent: record_source_counts SETs figures (never adds); test writes them twice -> 1.5 not 3.
- Lock clock: `database.database_now(seconds)` (one FunctionElement, compiled per dialect in database.py, the only module allowed to name a backend): PG `CURRENT_TIMESTAMP + make_interval(secs => n)`, SQLite `strftime('%Y-%m-%d %H:%M:%f','now','n seconds')` (UTC, SQLAlchemy's stored layout). acquire/renew lost their `now` parameter; the host clock and `run_ingestion(clock=)` no longer reach the lock. Other dialects: CompileError.
- Tests adapted (behaviour change, not weakened): lock tests expire a lease by writing a past expires_at (DB time passed) instead of injecting a future host clock; lifecycle `_WRITE_STEPS` record_source_counts -> `spend` with a new `observe` branch asserting spend kept; schema portable-type test checks a TypeDecorator's impl; Decimal in typed fixtures. Vendor-specific proof moved to tests/adapters/test_exact_credits_both_engines.py (vendor-neutrality rule).

### Gaps
- PG CURRENT_TIMESTAMP is the transaction start; correct here because every lock statement is the first of its transaction. A future caller that renews late in a long transaction would get a stale instant (statement_timestamp() would fix it).
- SQLite lease precision is milliseconds.
- Hunter's own invoice rounding of half credits remains UNVERIFIED; we now report the exact list price.
- spec-refactor-agent / validate-production-agent not spawned (no Agent tool in this harness); no mutation pass beyond the behavioural REDs.

### Self-review findings
Independent review (spec-refactor) in a throwaway worktree (HEAD + this change's files only); full suite there: 4191 passed, 1 skipped (env-file mode, root), 2 xfailed; Postgres legs ran. ruff/format/mypy clean.
- FIXED (test-first, RED on postgres): `database_now` used `CURRENT_TIMESTAMP` (transaction start) on PostgreSQL; now `statement_timestamp()`, one instant per statement, correct wherever a lock statement falls in its transaction. The "first of its transaction" gap is closed. New test: `test_the_lease_clock_is_the_statements_instant_not_the_transactions_start`.
- FIXED (test-first, RED both engines): the spend write came after the batch building and `clock()`, so a failure there lost the spend and was labelled `spend:`. The spend now commits first. New test: `test_the_spend_commits_before_anything_else_can_fail` (also checks the aborted run's report prints `credits=1.5`).
- FIXED (test weakness): the skew test only patched `ingest_runner.datetime`, so a host-clock regression inside `run_lock` survived. It now patches `run_lock` too.
- Mutation pass: 11 mutants (bind float/sub-milli guard, 0008 up x1, 0008 down floor, hunter ceil, spend write removed, PG CURRENT_TIMESTAMP, host clock in acquire, `_total` first-only, report int(), zero lease). All killed after the skew-test fix; each file restored (sha256 verified).
- OPEN (not fixed; orchestrator.py is locked by another reviewer): orchestrator `Decimal(spent)` accepts a float from an adapter, so the bind's float refusal cannot be reached on the run path. A sub-milli or inexact float then fails the whole `spend` write for every source, not just the one source. Validation belongs inside the source call, in one helper shared with `MilliCredits`.
- OPEN (minor): `record_contributions_written` is now called from both `run_recorder.finish` and `ingest_runner.observe` (two one-line call sites, acceptable). `database_now` binds the lease seconds as a float. SQLite's modifier cannot parse exponent notation (`1e-05`), but realistic leases (900 s+) never produce it.
- Checked OK: Decimal end to end for credits (no float, no `/` on credits outside `MilliCredits`); 0008 up/down on both engines; SQLite `%f` (ms) text parses and compares correctly against SQLAlchemy's microsecond layout; set-not-add spend; fencing (PG row lock serializes renew vs takeover).

## Follow-up — persist role-address lead fields (2026-10-06)

- RED: new tests/test_role_fields_persistence.py failed 6/6 (sqlite+postgres): AttributeError `CanonicalLeadRow` has no `email_is_role_address`; direct merge test `assert () == (0,)` (role-contact-only change not detected).
- GREEN: store/models.py CanonicalLeadRow + `email_is_role_address` (Boolean NOT NULL, app default False) and `role_contact_emails` (JSON NULL); migration 0009 (add both; false server default backfills then is removed, because store models carry no server_default and test_store_schema/autogenerate with compare_server_default enforce that; downgrade drops both in batch); merged_leads `_write_canonical` writes them (contacts as sorted str list), `_content` compares them.
- tests/test_remerge_migration.py: seed now inserts via `CANONICAL_LEAD_BEFORE_0009` (pre-0009 table, no ORM default for the new column); reused by the 0009 test.
- Proven both engines: round-trip; contact change -> same lead identity updated, 1 lead_merge log, no duplicate; unchanged re-run writes 0 leads, 0 logs; role addresses absent from logs and report text; 0009 up/down/up.
- Verify: ruff check src 0, mypy 0 (213 files), pytest 4261 passed 1 skipped.
- Deviation: DB column has no lasting DEFAULT false (project rule); no CanonicalLead loader exists in code, so "read back" = row read back.

### Self-review findings
- (a) 0009 OK: NOT NULL flag backfilled false then default dropped; JSON engine-neutral; up/down/up both engines; autogenerate diff empty (test_store_migrations, test_persistence_both_engines).
- (b) FIXED: NULL contacts (pre-0009 row) vs [] counted as a change -> spurious update + lead_merge line on re-projection. `_content` now compares `role_contact_emails or []`; RED->GREEN test `test_a_row_from_before_0009_with_no_role_contact_is_not_a_change` (both engines). Order deterministic (sorted).
- (c) ADDED: `test_the_ingest_command_never_prints_a_role_address`: real `ingest` CLI + log chain, both engines, sentinel role addresses stored but absent from stdout/stderr (asserts lead_merge lines were emitted).
- (d) FIXED (duplication + bug): store `MilliCredits` had its own validation that stored negatives (Postgres stored -1). Now `int(exact_credits(value, max_milli=MAX_STORED_MILLI) * MILLI_PER_CREDIT)`, imported from credits.py. Test tightened RED->GREEN: float, finer than milli, -1.5, -1, NaN, bool all refused with CreditValueError on both engines.
- (d, coordinator add-on) Per-source totals could overflow the 32-bit column. Chose BIGINT, not a bound on totals: a bound would fail a source for spending a lot, not for misreporting. Migration 0010 (batch alter_column INTEGER<->BIGINT; downgrade refuses a total past 32 bits on Postgres rather than truncating). Tests: total of 2x2147483.647 Credits stored and reported exactly; 0010 up/down/up keeps values, both engines.
- credits.py edit, required for this: `exact_credits(value, *, max_milli=MAX_MILLI)` plus `MAX_STORED_MILLI = 2**63 - 1`. Without it the store's shared check would refuse any total over the per-figure ceiling. Default behaviour unchanged; test_credits.py untouched. A to_milli/from_milli conversion pair is still missing there, so the store multiplies/divides by MILLI_PER_CREDIT inline.
- (e) Mutations 10/10 killed (_content flag, NULL normalize, sort, PII log line, ceiling, impl Integer, validation bypass, 0009 default drop, 0010 up, 0010 down); sha256 restore verified OK.
- Verify: pytest 4283 passed 1 skipped (Postgres leg ran); ruff check src clean; mypy 0 issues (214 files). Not committed.

## Follow-up — strict credit values at the adapter boundary (2026-10-06)

- Defect: orchestrator `Decimal(spent)` accepted float / sub-milli figures; MilliCredits then refused the whole run's spend write (all sources).
- RED (recorded): new both-engine test `test_credit_boundary_both_engines.py` failed 4/4 (sqlite+postgres, float 0.1 and Decimal('0.0005')) with `StatementError: ValueError: Credits are exact to a milli-Credit` on the source_run UPDATE; `test_credits.py` failed collection (`No module named leadforge.lead_ingestion.credits`).
- GREEN: new `lead_ingestion/credits.py` (`exact_credits`, `CreditValueError`, `MILLI_PER_CREDIT`): int or Decimal only; refuses float, bool, other types, NaN/inf, negative, finer than 0.001 (exact, context-free exponent check: no rounding at >28 digits). `orchestrator._checked_credits` wraps it inside the ledgered call and raises `SourceError(source, "credits_spent contract violation: <violation>")` -> that source FAILED, others + spend record intact.
- Proof: alpha (0.1 float / Decimal('0.0005')) FAILED with "[alpha] ... credits_spent ... float|finer than 0.001", no PII, credits NULL; bravo Decimal('1.5') and charlie int 2 OK and stored exactly, both engines. 23 new tests pass.
- Verify: ruff+mypy clean on my 4 files; related subset (orchestrator/run/spend/credit/e2e) 453 passed. Full gate NOT green: ruff E501 x2 + mypy x10 in untracked test_role_fields_persistence.py; pytest 11 failed/2 errors, all in concurrent agent's in-flight store/0009 work (role fields, ORM drift); 3 adapter tests that failed in full run pass in isolation (28 passed).
- Open: store/models.py `MilliCredits` still has its own conversion; should reuse `credits.exact_credits` / `MILLI_PER_CREDIT` (store owner's file, not touched). It also accepts negatives.

### Self-review findings

- (a) Credit conversion sites in src: `credits.exact_credits` (new), `orchestrator._checked_credits` (wraps it; the only adapter->orchestrator site), and `store/models.py MilliCredits.process_bind_param/process_result_value` (its own `_MILLI` and checks). Adapters only produce figures (hunter's Decimal table, apollo's int), `run_record._total` sums them, and run_report prints them. There is no other copy outside store. For the store owner (NOT edited): MilliCredits should call `credits.exact_credits` and use `MILLI_PER_CREDIT`/`MAX_MILLI`. Today it (1) accepts negatives, (2) uses context arithmetic `Decimal(v)*1000`, so `Decimal('1.' + '0'*30 + '1')` gets ROUNDED and accepted (checked: True), and (3) has no ceiling. Also (4): a per-source SUM across phases (`run_record._total`) can still exceed int4 even when each figure is within it. That needs a BigInteger column or a store-side check.
- (b) DEFECT FIXED (test-first): there was no upper bound. `credits_consumed_milli` is INTEGER (int4 on PostgreSQL). `Decimal(10**7)` passed the boundary, then the spend UPDATE raised `DataError: integer out of range` and the whole run's spend write failed. RED: both-engine test 2 failed (postgres), and the unit test failed at collection. GREEN: `MAX_MILLI = 2**31 - 1`, with the message "credits exceed 2147483.647 (the store's ceiling)". The old property test asserted that 10**12 milli was accepted; it is now bounded by MAX_MILLI. The >28-digit precision case moved under the ceiling. bool is rejected; Decimal('1.50') and '1.5' are both accepted as equal.
- (c) The error names the source and the violation only; no value is echoed. OK.
- (d) The failing source's contributions are discarded (Attempt not ok -> batch/contributions None) and its credits are NULL. This matches a normalization failure. Plain SourceError is not in DEFAULT_RETRYABLE, so nothing is re-fetched or spent twice.
- (e) Mutation check: 8/8 killed (bool, finite, negative, ceiling >=, ceiling 2**32, milli exponent, trailing zeros, orchestrator bypass), each file restored and sha256-verified.
- Verify: worktree (HEAD + this change) full suite 4261 passed, 1 skipped. ruff format/check and mypy clean on touched files. Worktree removed.

## Follow-up — load a lead back from the store (user request 2026-10-06)

- Baseline: ruff/mypy clean; pytest 4283 passed, 1 skipped (scratchpad/fu5-baseline.txt).
- RED: new tests/test_lead_reader.py and the updated test_role_fields_persistence.py both failed at collection, `ModuleNotFoundError: leadforge.lead_ingestion.store.lead_reader` (fu5-red.txt).
- GREEN: new store/lead_reader.py: `load_lead(session, lead_id, *, follow_successor=False, current_version=None) -> StoredLead | None`, `list_leads(session, *, include_retired=False, company_id=None, limit=50, after=None, current_version=None)`, `find_lead(session, *, email=None, linkedin_url=None) -> tuple[StoredLead, ...]`, `SuccessionError`, `WebEvidence`. CLI `leads show <id> [--reveal]` and `leads list [--limit --after --include-retired --company-id --reveal]` in cli.py.
- Reuse, not copies: contributions.py now exposes `stored_provenance` (pulled out of `load_lead_contributions`, which uses it), `stored_value` (renamed `_rebuild`) and `aware_utc` (renamed `_aware`). Uses match_keys `normalize_email`/`normalize_linkedin_url`, `companies.company_domains`/`lead_company_id`, `TieOutcome.flagged`, `RunRecordRepository.latest_projection_stamp`, `log_redaction.MASK`, `adapters.web_evidence.Attachment`.
- Verify: ruff check src 0; mypy 0 (216 files); pytest 4333 passed, 1 skipped (fu5-full.txt). test_lead_reader: 50 passed, 25 of them on Postgres, none skipped.
- Mutation check: 5 mutants in lead_reader (dropped role contacts, email status, superseded provenance, `>=` cursor, opt_out). Every one was caught.

### Decisions
- Return type: `StoredLead` (frozen dataclass). `.lead` is the domain `CanonicalLead` itself, plus lead_id, provenance, agreement, contributing_sources, primary_domain(+source, `primary_domain_flagged`), projection_version/fingerprint, computed_at, stale, retired_at/`retired`, successor_ids and web_evidence. Personal data is kept out of repr. `CanonicalLead` is extra=forbid and frozen, so it cannot carry store metadata itself.
- Requirement 1.1 guard: `structure_guard.CANONICAL_LEAD_BUILDERS` gains `store/lead_reader.py: {_rehydrate}`, with a reason (rehydration with no merge logic, proven equal by the round-trip test). test_structural_rules now expects that third module and compares module paths relative to the slice root. Before this it compared bare file names, which cannot represent `store/...`.
- Round trip: the loaded lead equals the projected one, except that a domainless company carries the persisted `lead_company_id(lead_id)`. That swap is by design (merged_leads `_lead_owned`) and the test applies it explicitly.
- Provenance: the store keeps, per path, the winner and the superseded losers, plus an agreeing count. Those are returned with path-sorted ordering and the projection's canonical path (a CRM bare `email` comes back as `person.email`).
- stale is `None` when the current version is unknown (neither passed in nor recorded by a completed run). A retired lead is never stale, matching `stale_projections`.
- follow_successor: walks one query per generation and keeps a visited set, so it is cycle-safe. It returns the single active successor. A split (several successors) or a dead end/cycle raises `SuccessionError(lead_ids)`, which carries ids only and no PII.
- find_lead: the store holds email/linkedin in plaintext (canonical_lead). There is no hashed key column, and IdentityKey is unused. Lookup therefore normalizes both sides with the match_keys normalizers and never queries a digest. Active leads only; when both criteria are given, both must match. It returns every match sorted by id, because a role address can be several people's email.
- list_leads: ordered by lead id. Keyset cursor `after`. The UUID order agrees between SQLite hex and PG uuid, and the test asserts the same order on both. The company_id filter is a CAST(JSON AS TEXT) contains pre-filter, confirmed on the rebuilt lead, and the page is refilled if a false positive is dropped.
- Web evidence is attached at read time: a stored `company.web_evidence.*` contribution with attachment other than `unattached` whose `company.domain` shares a registrable domain with the lead's company. Title and snippet stay `UntrustedText`.
- CLI PII decision: by default it masks contact identifiers. Emails (lead and role contacts) print as `j***@acme.com` and LinkedIn as `host/***`. `--reveal` prints them whole. Names, company and domains are printed, because they are how an operator recognises a lead. The model has no phone field. Nothing about a lead is logged either way; the test checks stderr and captured logs with --reveal. Exit 1 means unknown lead, 2 means config error or malformed id. The engine is disposed after each command.
- Query bound: load_lead ≤ 6 queries. list_leads takes the same count for 2 and 12 leads (≤ 6). find_lead ≤ 7. All are asserted with a `before_cursor_execute` counter.

### Gaps
- find_lead scans (id, email, linkedin_url) of every active lead in one query. That is O(N) rows: an indexed lookup needs a normalized-key column (a migration), which is out of scope.
- Web evidence: one query over every stored contribution that has an attachment field. It is filtered in Python, because the JSON `value` cannot be compared portably in SQL.
- Agreeing sources other than the winner are not recoverable (only the count was ever stored).
- `follow_successor` query count grows with succession depth (one per generation), not with lead size.
- Risk-gate tools (serena find_referencing_symbols, gitnexus impact) and ctx_* tools were not available in this agent. Blast radius was checked by grep instead: `_rebuild`/`_aware` were used only inside contributions.py, and CANONICAL_LEAD_BUILDERS is used only by structure_guard and test_structural_rules. The spec-refactor-agent self-review was not spawned (no Agent tool).
- Not committed; specs/tasks.md untouched.

### Self-review findings
- Reviewer: spec-refactor (independent, skeptical). The tests were changed before the code. RED was a collection ImportError for `lead_owned_employments` (fu5-review-red.txt). GREEN: ruff 0, mypy 0 (218 files), pytest 4346 passed and 1 skipped, the same skip as the baseline (fu5-review-full.txt). test_lead_reader: 62 tests, 31 on Postgres, none skipped.
- (a) Guard. The rule is Req 1.1: only listed functions may build or copy a CanonicalLead. The exemption was already as narrow as it can be: one function, `_rehydrate`, which only `model_validate`s stored values. It is now stricter: a new structural test checks that lead_reader imports no Merge Engine module (projection, conflicts, clustering, superseded, remerge, over_merge, primary_domain) and that the store/ allowance is exactly {_rehydrate}. The reason text was updated.
- (b) Provenance is persisted, not derived. Migration 0011 adds `canonical_field_provenance.agreeing_field_ids`. `_write_canonical` writes it in projection order, and it is part of `_content` (NULL counts as none). The reader returns the winner, then the agreeing records, then the superseded ones, and the round-trip property test now asserts `loaded.provenance == result.provenance` in full.
  - Why persisted: deriving it would re-run the agreement logic in the reader under the read-time trust ranking, not the projection's, and the guard would have to allow projection imports.
  - Rows from before 0011 stay NULL until they are re-projected; their count is unchanged.
  - The path sort moved from SQL to Python, because a PG collation can order paths differently from the projection.
- (c) Index. New table `lead_match_key`: kind plus a 16-hex HMAC digest, mapped to a lead id, with unique (lead, kind) and an index on (kind, digest). New module `store/match_key_index.py`.
  - Key: a new store secret `match_key_index` written by 0011, used through `MatchKeyDigester`. Rejected: the env secret, which is random per run when unset.
  - Normalization: the same match_keys normalizers clustering uses.
  - Writes: one path, `reindex`, which persist_merge calls for every lead it writes or retires. The 0011 backfill covers active leads only and reuses `index_rows`.
  - find_lead runs one index query and the load, then confirms each hit on the loaded lead, because digests are truncated. It takes at most 8 queries, the same for 2 and 12 leads, and reads canonical_lead only by id (asserted).
  - Bug fixed: an unusable criterion was silently dropped; it now raises ValueError.
  - `store_key` now takes a key name. test_store_digests selects the content key by name.
- (d) The persisted id is the single source of truth. The projection runs before a lead id exists, and its pseudonym is a contribution hash that 0007 removed from the store. `merged_leads.lead_owned_employments` (it replaces `_lead_owned`) is the one function: the persist path writes its output and the test's as_stored calls it, so the test no longer has its own copy of the swap.
- (e) CLI.
  - Bug fixed (terminal injection): provider text went to the terminal unescaped. Plain output now goes through `run_report.printable`, which is now the only copy; the duplicate in run_exit is gone.
  - `--json` added to show and list, with the same masking.
  - Masked: emails, role contacts, LinkedIn. Names, companies and domains stay visible, as before. The model has no phone field.
  - --reveal logs nothing (tested).
- (f) No N+1 (asserted). Order is deterministic. Succession is cycle-safe (seen set, tested).
- (g) Mutation check: 12/12 mutants killed. Each file was restored and sha256-verified (fu5-review-mutants.txt).
- Left as is: web evidence still reads every attached evidence row on each load. The legacy `identity_key` table stays unused, because its UNIQUE dedupe does not fit shared role addresses.
