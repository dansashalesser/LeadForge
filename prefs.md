# Project Preferences

_Captured 2026-10-04 from user direction in session (in place of interactive pref-elicit)._

## Declarative (what must be true)
- Everything is agnostic and plug-and-play: lead sources, LLM provider, sender, DB engine
- Adding a lead source = new class only; downstream stays untouched
- All provider data normalizes into one canonical lead structure / DB
- Adapters are real, working code against current official provider docs
- Demo runs end-to-end on synthetic data shaped like each provider's real responses — no keys needed
- Never send real messages (dry-run)
- Guardrails, evaluations, MCP (where useful), and Docker are core scope — "VERY important"

## Imperative (how to do it)
- Python
- LangChain (or equivalent) for LLM-agnostic layer; Claude default
- SQLite default, Postgres-capable
- `BaseLeadSource` base class with one subclass per provider
- `.env.example` documents required API keys

## Strategic vs Tactical
- Strategic: demonstrate GTM-engineering architecture (find → qualify → personalize → trigger → track) for a take-home review
- Tactical: small scale (handful of leads); clarity over volume

## Open
- Package manager (proposed: uv)
- Whether LeadForge itself exposes an MCP server
