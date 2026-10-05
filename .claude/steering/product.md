<!-- L0: LeadForge purpose — AI "hunter" PoC for competitor-user GTM outreach -->
# Product Overview

_updated_at: 2026-10-04 — filled from GTM Engineer take-home brief (replaces bootstrap TBDs)_

LeadForge is a proof-of-concept automated "hunter" workflow for go-to-market outreach. It finds users of a competitor product (target: **DataStax**), picks the relevant ones, writes personalized outreach with an LLM, and tracks what would have been sent. It is a take-home assignment for a GTM Engineer role. The point is to show how a high-level business need becomes an automated, AI-driven pipeline, not to run a production outreach system.

**Scale**: small. A handful of example leads end to end. Show the logic and architecture, not volume.

## Core Capabilities

1. **Multi-source lead identification**: find likely DataStax users (Astra DB / Cassandra) across many providers, not only LinkedIn. LinkedIn becomes an identity field (`linkedin_url`) supplied by the providers, not a scraped source.
2. **Normalization + enrichment**: each provider's raw shape is mapped to one canonical lead record. Leads found by several sources are merged, and any field still missing is filled from other providers when possible. Every field records which provider it came from.
3. **Qualification**: explicit, explainable metrics decide whether outreach is reasonable. Each lead ends as `selected`, `rejected`, or `needs_enrichment`, and the reasons are stored. Keep "gathered" and "selected" as separate states.
4. **AI personalization**: generate a LinkedIn connection invite (short; LinkedIn caps invite notes) and a follow-up email per selected lead.
5. **Trigger logic**: rules decide when each message fires (e.g. invite first, then the email after a condition or delay). It runs in **dry-run mode**: messages are logged to the console or a file and never sent to real people.
6. **Persistence and reporting**: store leads, selection decisions, and "sent" messages in a database, and produce a report covering gathered → selected → messaged.

## Lead Sources and Their Roles

_Added 2026-10-04. Providers do different jobs, so a source can fill more than one role._

| Role | Providers | Signal they contribute |
|------|-----------|------------------------|
| Discovery (people/company search) | Apollo.io, ZoomInfo, UpLead, Clay | Firmographics, titles, **technographics** (uses DataStax/Cassandra) |
| Intent / web signal | Google Search, Leadfeeder | Job posts, talks, and GitHub mentions of Astra/Cassandra; company website visits |
| Contact enrichment / verification | Hunter.io (also Apollo, UpLead, Clay) | Email discovery and deliverability |
| CRM check | HubSpot | Existing contact or customer, prior outreach, suppression |

Adding a source should take only a new adapter class and its fixtures. The pipeline, scoring, and DB stay unchanged.

## Qualification Model (direction)

Score dimensions, with weights and thresholds kept in config rather than code:
- **Competitor-usage evidence**: strength and recency of DataStax/Cassandra signals
- **ICP fit**: role or seniority (data/platform engineering decision makers), company size, industry
- **Intent**: website visits, hiring signals, public activity
- **Contactability**: verified email and/or LinkedIn URL present
- **Data confidence**: how many sources agree on a field
- **Hard excludes**: already in the CRM as a customer or open deal, on the suppression list, or contacted recently

Exact metrics are finalized in the spec design phase.

## Target Use Cases

- The hiring panel reviews how the candidate breaks down and automates a GTM motion.
- Demo run: one command processes a few DataStax-user leads through the whole pipeline and outputs a report.

## Value Proposition

Replaces manual "old school" prospecting (searching LinkedIn by hand, writing each message) with a repeatable pipeline: find → qualify → personalize → trigger → track. AI personalization does the per-lead work that manual outreach can't scale.

## Product Boundaries

- **Never send real messages.** Dry-run is the default and the only mode in the PoC.
- Respect platform ToS. Prefer official or third-party data APIs, or mock/fixture data, over aggressive scraping.
- The adapters are real, working code written against each provider's documented API. The demo runs on **synthetic data** that mirrors each provider's response schema, so no keys are needed.
- Evaluation is on clarity of logic, architecture, and AI usage, not data volume.

---
_Focus on patterns and purpose, not exhaustive feature lists_
