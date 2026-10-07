# Four providers at full depth rather than eight at surface depth

Requirements 12 through 19 specify eight provider adapters. Grilling added substantial
depth to the canonical model, the merge engine, and the run orchestrator, and the
project constraints call for small scale and clarity over volume. We cut the provider
count to four — Apollo, HubSpot, Google Search, Hunter — and kept the sophisticated
merge. Leadfeeder, UpLead, ZoomInfo, and Clay are deferred with their research intact,
not deleted.

The plug-and-play claim is proved by the runtime-registration test, not by the number of
adapters, so a fifth through eighth adapter demonstrate nothing the fourth did not.

## Considered Options

Keeping all eight and simplifying the merge was the alternative. It was rejected because
the merge is what distinguishes this from eight API wrappers, and that distinction is
what the work is assessed on.

## Consequences

- The four retained adapters were chosen for distinct architectural lessons: Apollo for
  the free-discovery/paid-enrichment split, HubSpot for the free suppression check that
  anchors cost ordering, Google Search for untrusted text and Company Signals, Hunter for
  verified-email keying, domain-batched charging, and inverted status-code conventions.
- OAuth2 client credentials leave the build with ZoomInfo. All four survivors use static
  header auth, so the token cache and refresh path is kept as a documented seam to stop
  the auth abstraction quietly collapsing to a single shape.
- The LLM domain-resolution fallback cannot execute in the demo, which runs synthetic
  with outbound calls prohibited. It is specified and seamed, not built.
