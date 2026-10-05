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
