<!-- L0: LeadForge layout — src package by pipeline stage, adapter-per-source -->
# Project Structure

_updated_at: 2026-10-04 — layout proposed from stack decisions; confirm when scaffolded_

## Organization Philosophy

Layered by **pipeline stage**, with a `src/` Python package. Each stage depends only on the canonical models and the interfaces, never on a specific provider, LLM, or DB engine. Concrete implementations are picked at the edges (config/registry).

## Directory Patterns

### Package
**Location**: `/src/leadforge/`
**Purpose**: all application code, one subpackage per stage (`sources`, `enrichment`, `qualification`, `personalization`, `guardrails`, `triggers`, `delivery`, `reporting`, `db`, `evals`) plus `models` (canonical types) and `cli`.

### Source adapters
**Location**: `/src/leadforge/lead_ingestion/adapters/`
**Purpose**: `base.py` holds `BaseLeadSource` and the registry. One module per provider holds its raw schema, the client (REST or MCP), and `normalize()`.
**Example**: `lead_ingestion/adapters/apollo.py` → `ApolloSource(BaseLeadSource)`

### Synthetic fixtures
**Location**: `/fixtures/<provider>/`
**Purpose**: JSON responses shaped exactly like each provider's documented API output. Used by synthetic mode and by tests.
**Example**: `fixtures/apollo/people_search.json`

### Prompts
**Location**: `/src/leadforge/personalization/prompts/`
**Purpose**: versioned prompt templates (invite, follow-up email, judge rubric), kept out of Python code.

### Config
**Location**: `/config/`
**Purpose**: scoring weights and thresholds, enabled sources, trigger rules (YAML). Secrets never go here; they live in `.env`.

### Tests
**Location**: `/tests/` mirrors `src/leadforge/` (`tests/test_apollo.py` beside the adapter slice)

### Specs
**Location**: `/specs/<feature>/`
**Purpose**: spec-driven development artifacts (requirements, design, tasks)

## Naming Conventions

- **Modules/files**: `snake_case.py`, named after the provider (`hunter.py`, `zoominfo.py`)
- **Classes**: `PascalCase`. Source adapters are `<Provider>Source`; raw schemas are `<Provider><Entity>Raw` (e.g. `ApolloPersonRaw`)
- **Functions/vars**: `snake_case`. Constants: `UPPER_SNAKE`
- **Env vars**: `<PROVIDER>_API_KEY` (e.g. `APOLLO_API_KEY`), plus `LLM_PROVIDER`, `LLM_MODEL`, and `DATABASE_URL`
- **Spec dirs**: kebab-case

## Import Organization

```python
# Absolute imports from the package root; no relative imports across stages
from leadforge.models import CanonicalLead
from leadforge.sources.base import BaseLeadSource
```

## Code Organization Principles

- Downstream stages consume `CanonicalLead` only. Provider-specific fields never leak past `normalize()`.
- New provider = one new module + fixtures + env var entry. No edits elsewhere except registry discovery.
- Side effects (HTTP, LLM, DB, send) sit behind interfaces so tests and dry-run swap them out.
- Untrusted external text is passed to the LLM as data, never concatenated into instructions.

---
_Document patterns, not file trees. New files following patterns shouldn't require updates_
