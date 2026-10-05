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
