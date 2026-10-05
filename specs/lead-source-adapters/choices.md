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
