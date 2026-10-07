# Target Profile with a central translation table

The spec named DataStax, Astra DB, and Cassandra in seven requirements, which contradicted
both the binding "everything is agnostic and plug-and-play" constraint and the spec's own
12.13 (technology identifiers come from config, never hardcoded). What the system searches
for is now a configurable **Target Profile**; DataStax is the shipped example profile.

Providers express targeting in mutually unintelligible vocabularies — Apollo wants
snake_case string UIDs, ZoomInfo wants numeric tag IDs fetched from a lookup endpoint,
web search wants natural-language phrases, and several providers cannot express
technology targeting at all. The profile therefore stores a canonical term plus its
per-provider translations in one config file, rather than each adapter owning its own
lookup table.

## Considered Options

Per-adapter ownership of translations is the more orthodox vertical-slice answer and
keeps provider knowledge local. It was rejected because adding one target term would
then mean editing every adapter module — an N-file change for what should be a config
edit. Adding a *source* remains one class plus one config column either way.

## Consequences

- A provider with no entry for a term declares it cannot target that term, which is the
  **Not Applicable** state rather than a separate mechanism.
- Config-held external identifiers can go stale silently; configured identifiers are
  validated against the provider's own lookup surface at startup in live mode.
