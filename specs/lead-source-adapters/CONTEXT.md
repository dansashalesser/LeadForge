# Lead Ingestion

The language of the Lead Ingestion Layer — the stage that turns heterogeneous provider
APIs into persisted, deduplicated records with per-field provenance. Terms here are
binding on `requirements.md` and `design.md`.

## Language

### Entities

**Lead**:
A single identifiable human.
_Avoid_: contact, prospect, record, person-lead

**Company Signal**:
Company-level information about an organization, carrying no person identity.
_Avoid_: company lead, company-scoped lead, account

**Signal**:
A single piece of evidence about a **Lead** or a **Company Signal**, carrying its own
strength.
_Avoid_: evidence, datapoint, indicator

**Signal Strength**:
How much weight a **Signal** carries when lead strength is compared.
_Avoid_: score, confidence, weight, rank

### Certainty

**Source Trust Rank**:
Our own standing opinion of a provider, independent of any particular value it supplies.
_Avoid_: priority, source score, reliability

**Field Confidence**:
A provider's own stated certainty about one value it supplied.
_Avoid_: accuracy, match score, quality

**Confidence Origin**:
Whether a **Field Confidence** was stated by the provider or inferred by us.
_Avoid_: source of truth, derivation

### Identity

**Employment**:
A relationship between a **Lead** and a **Company Signal**, current or historical.
_Avoid_: job, role, position, company_domain

**Match Key**:
An attribute strong enough to assert that two records describe the same **Lead**.
_Avoid_: identifier, dedupe key, fingerprint

**Identity Exclusion**:
A specific value barred from acting as a **Match Key** because it describes more than
one person.
_Avoid_: denylist, blocklist, placeholder list

**Over-merge**:
Two distinct humans wrongly resolved to one **Lead**.
_Avoid_: false positive, collision

**Under-merge**:
One human wrongly resolved to two **Leads**.
_Avoid_: false negative, duplicate, split

### Targeting and evidence

**Target Profile**:
The configurable definition of who we are looking for — technologies, competitors, and
keywords. DataStax is one example profile, never a built-in assumption.
_Avoid_: ICP, criteria, search config, DataStax filter

**Negative Evidence**:
A source that could answer a question, was asked, and reported no match.
_Avoid_: no data, empty, missing, null result

**Not Applicable**:
A source that was never able to answer the question, because its API carries no such
field.
_Avoid_: unsupported, missing, no data

### Run phases

**Discovery**:
The phase that finds **Leads** and **Company Signals** without spending provider credits.
_Avoid_: search phase, fetch, collection

**Enrichment**:
The phase that adds detail to **Leads** already found by **Discovery**.
_Avoid_: hydration, augmentation, lookup

**Suppression**:
A provider-supplied statement that a **Lead** must never be contacted.
_Avoid_: opt-out, do-not-contact, exclusion, blocklist

**Credit**:
A provider's unit of billing, consumed by **Enrichment** and never by **Discovery**.
_Avoid_: quota, token, call cost

**Verified Email**:
An address a provider has confirmed deliverable for one specific person.
_Avoid_: valid email, confirmed email, good email

## Relationships

- A **Lead** is exactly one human; a record with no person identity is never a **Lead**
- A **Company Signal** describes one organization and attaches to zero or more **Leads**
- A **Lead** carries zero or more **Signals**; a **Company Signal** carries zero or more **Signals**
- Each **Signal** has one **Signal Strength**
- Each supplied value carries at most one **Field Confidence**, which carries one **Confidence Origin**
- **Source Trust Rank** decides a conflict first; **Field Confidence** only breaks its ties
- **Signal Strength** is recorded during ingestion and never decides a conflict
- A **Lead** has zero or more **Employments**; each names one **Company Signal**
- **Match Keys**, strongest first: LinkedIn URL, **Verified Email**, then name plus any
  **Employment** domain with a second agreeing attribute
- An **Identity Exclusion** is the only repair for an **Over-merge**; no unmerge exists
- **Negative Evidence** lowers a **Lead**'s strength; **Not Applicable** never does

## Example dialogue

> **Dev:** "A web search says Acme Corp is hiring Cassandra engineers. Is that a **Lead**?"
> **Domain expert:** "No — there's no human in it. That's a **Company Signal** about Acme. When Apollo later gives us a named engineer at acme.com, that's a **Lead**, and the Acme **Company Signal** attaches to it through an **Employment**."
> **Dev:** "So if two engineers at Acme turn up, do they share it?"
> **Domain expert:** "Yes. One **Company Signal**, two **Employments**, two **Leads**. Sharing it doesn't make them the same person."
> **Dev:** "And if Apollo has her at acme.com but Hunter has her at acme.io?"
> **Domain expert:** "Same company — the **Company Signal** owns both domains. And she'd already have matched on LinkedIn anyway, which is why that's the strongest **Match Key**: it survives her changing jobs. Her work email doesn't."

## Flagged ambiguities

- "lead" was used to mean both a person and a visiting company — resolved: a **Lead**
  is always a single human. Company-level data is a **Company Signal**, a separate
  entity. There is no company-scoped **Lead** and no `lead_scope` attribute.
- "confidence" was used for provider certainty, our opinion of a provider, and
  qualification weight — resolved into **Field Confidence**, **Source Trust Rank**, and
  **Signal Strength**, which are three separate things.
- "no data" was used for both "asked, no match" and "could never answer" — resolved:
  **Negative Evidence** and **Not Applicable**. Collapsing them biases scoring toward
  whichever providers happen to carry the field.
- DataStax was treated as a domain concept — resolved: it is one **Target Profile**,
  supplied as configuration. No requirement names it.
