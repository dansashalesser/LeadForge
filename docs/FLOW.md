# How LeadForge works — one run, end to end

Walkthrough of one command. Every number named below lives in `config/outreach.yaml`, never in code — that is what makes the thresholds reviewable instead of hardcoded.

```bash
uv run leadforge outreach search users --product datastax
```

Goal: find people at companies that **use DataStax**, and draft outreach to them. Follow one person through: *Priya, Data Engineer at Acme (acme.com)*.

Steps that call a language model end with an **LLM** sub-section: where the call is, what problem it solves, and what keeps its output safe. Steps without one use no model and are plain rules over stored data.

The model is set by `LEADFORGE_LLM_PROVIDER` / `LEADFORGE_LLM_MODEL`, falling back to the `llm:` block in `config/outreach.yaml` (`anthropic`, `claude-sonnet-5-5`, 60 s timeout, `low` reasoning effort). The key comes from `<PROVIDER>_API_KEY`. LangChain builds the client, so switching provider means changing an environment variable, not editing code.

---

## 1. Plan the search

`users` mode looks the product up in `config/catalog/datastax.yaml` → vendor DataStax, aliases "DataStax" / "DataStax Astra", technology UID `datastax`. No model involved.

No product → `NoProductSelectedError`; unknown product → `UnknownCatalogKeyError`; unknown technology term → `UnknownTermError`. **Why:** all validation happens before the first provider call, so a typo costs nothing. The plan is printed for the operator before anything is spent.

### LLM: compiling free-text searches

Used by `leadforge outreach search free-text "data people at Cassandra shops"` (`compile_llm.py`, prompt `compile_search_v1`), not by `users` mode. A model turns the plain-words query into a Search Plan made of technology terms, titles, and seniorities.
- **Problem:** people describe a search in words that no keyword table can map reliably ("Cassandra shops", "data people").
- **Why the plan stays safe:** the model answers through structured output with no extra fields allowed, and every term must already be one the base profile knows. Anything else counts as a failed answer and is asked for again (`compile_retries: 2`), then the search stops with `PlanCompileError`. The query sits in an escaped `<query>` block, never in the system prompt, so words in it cannot become instructions.
- **Failure handling:** if a key is set and the call fails, the search stops with an error and does not quietly switch to the offline compiler. With no key, the offline compiler runs and the summary says so.

### LLM: drafting catalog entries for unknown vendors

`usage/drafts.py`, prompt `catalog_draft_v1`. A model drafts a vendor's catalog entry from its name and the provider's list of supported technologies.
- **Problem:** writing a catalog entry by hand for every new vendor (its aliases, products, ecosystem) is slow.
- **Why it is safe:** the draft is checked against the catalog schema, and every technology UID must come from the supplied list (`UnknownTechnologyUidError`). It lands in `config/catalog/drafts/`, and no search can use it until a person runs `leadforge outreach catalog approve <key>`. The model proposes; a human decides what goes into the catalog.

## 2. Ingest the people

Discovery: Apollo searches for people on companies with the `datastax` technology signal; Google Search collects web results. Then enrichment in **tiers**, derived from what each adapter declares about itself (`enrichment_sort_key`), not from a hardcoded list:

| # | Source | What it gives | Why in this position |
|---|---|---|---|
| 1 | **HubSpot** (free) | our CRM: lifecycle stage, open deals, opt-outs | free *and* it can suppress — every opt-out is known before a credit is spent |
| 2 | **Hunter** (paid) | email address + verifier verdict, LinkedIn URL | paid, but a `451` verdict suppresses the person — prune them before Apollo is asked about them |
| 3 | **Apollo** (paid) | title, seniority, departments, employment history, LinkedIn URL, company domain, tech signals | suppresses nothing, so it only runs on survivors — and Hunter has already supplied the address/name that makes the match land |
| 4 | **Google/SerpApi** (paid) | company web evidence, no identity of its own | needs the company domain Apollo produced; it prunes nothing, so last costs nothing |

Then Hunter runs again (Discovery hands it masked names, so its first call often has nothing to ask), and HubSpot runs again — both only for identities they never saw. **Why:** a late-discovered email is exactly where an opt-out hides, and HubSpot is free, so nothing moves.

After each tier, anyone flagged suppressed/opted-out leaves the work list, plus everyone strongly linked to them (same LinkedIn URL or verified email — never name+domain). Tiers also feed later tiers, one forward pass. Then contributions are clustered into one Lead per person; clusters that look like two people fused get flagged for a human rather than silently merged.

Priya survives: one Lead, verified email, LinkedIn URL, title "Data Engineer", employer Acme (acme.com).

Any source without an API key runs synthetic on bundled fixtures, so the whole run works with zero credentials.

## 3. Prove Acme actually uses DataStax

This is the expensive part, so it is ordered cheapest-useful-first (`usage/queries.py`). Queries run per company until it reaches `verified` or the budget ends:

`site:datastax.com "Acme"` → ATS job boards → `site:github.com` → `site:acme.com` → open web → LinkedIn snippets.

A result is only fetched if its snippet names an alias; fetching obeys `robots.txt`, caps at 1 MB, keeps ±600 chars around each mention, and **never fetches linkedin.com** (search snippets only — see the LinkedIn section in `README.md`). The classifier then says: is the subject really Acme, and is the relationship `uses_now` / `used_past` / `evaluating` / `vendor_or_partner`? An LLM answer is kept only if it validates and every quote is verbatim from the passage (see the LLM sub-section below). **Why:** a missing judgement is better than an invented one, and page text is untrusted input.
Budget per run: 200 searches, 300 fetches, 300 LLM calls, counted separately. Exhaustion grades the company `unverified` with reason `budget_exhausted` — an explicit "we ran out", never a fake negative.

Acme's grade (`usage/grade.py`):

| Found | Grade |
|---|---|
| newest evidence says they *left* DataStax | `negative` |
| ≥2 independent evidence classes, one of them strong | **`verified`** |
| one real content page | `likely` |
| only a technographic fingerprint | `unverified` |

Priya's Acme hits a DataStax case study **and** a Cassandra job ad → two independent classes, one strong → `verified`. **Why that bar:** a case study plus a job ad are hard to be wrong about together, while a single technographic fingerprint routinely is — so it can never qualify a company on its own. Evidence with no date is capped at medium strength; older than 730 days drops a notch; an ecosystem technology (Apache Cassandra) caps at `likely` unless `--include-ecosystem`.

### LLM: classifying usage passages

`usage/classify.py` (`LlmClassifier`), prompt `usage_v1`. This is the main model call in a `users` run: it labels each fetched passage before the grading above.
- **Problem:** keywords cannot tell these apart:
  - "Acme moved to DataStax" from "Acme moved off DataStax";
  - a customer from a DataStax reseller or integration partner;
  - Acme from another company with the same name;
  - a real usage claim from a page that only mentions DataStax in a sidebar.

  Getting this wrong either contacts the wrong people or gives a fake negative. Telling these cases apart means reading the sentence, which is what the model does.
- **How it is used:** the ±600-char passages go in escaped `<passage>` blocks and the company name in a `<target>` block. The system prompt tells the model to treat both as data and to answer only from the text, never from what it knows about the company. The answer is schema-constrained to one of six fixed labels (`uses_now`, `used_past`, `evaluating`, `vendor_or_partner`, `mentions_only`, `unrelated`), plus supporting quotes and a confidence.
- **Why it can't invent evidence:** `_kept` throws the answer away unless all of these hold:
  - the subject is the target company;
  - the product key matches;
  - every quote appears word for word in a passage.

  If the schema still fails after the retries, the passage stays unclassified.
- **What the model does not do:** it labels passages and nothing else. The grade (`grade.py`) and the person verdict in step 4 (`verdict.py`) are plain rules over those labels. So the model only supplies evidence, and fixed thresholds decide.
- **Cost and audit:** every call spends one `llm_calls` from the budget. Each judgement is stamped with the model, the prompt version, and an input hash, so a grade can be traced back to the exact prompt and passages. `leadforge outreach usage-eval --classifier llm` scores the classifier against labelled cases, so a prompt change is measured rather than assumed.
- **No key:** a live `users` run without a model key stops with `UsageClassifierUnavailableError`; it does not quietly drop to the keyword classifier. Synthetic and demo runs use the offline classifier (alias and co-term matching plus polarity cue phrases), so the whole flow still runs with zero credentials.

## 4. Is Priya a plausible user of it?

Her title is matched word-by-word against `config/catalog/roles.yaml`: "data engineer" is `core`. A recruiter or sales engineer would be `irrelevant`, which **beats** core — a Sales Engineer at a DataStax customer is not a user. `adjacent` (e.g. engineering manager) only lifts to core if a confirmed self-stated mention backs it.

Apollo match credits are spent only on people at `verified` or `likely` companies. **Why:** never pay to enrich someone whose employer we already rejected.

Verdict = company grade × person fit (`usage/verdict.py`): `verified` + `core` → **selected**. `verified` + unboosted `adjacent` → `manual_review`. `likely` + non-core → rejected. Vendor staff, vendor partners and people who left reject first. **Why:** everything borderline goes to a human queue, not to the outbox.

## 5. Qualify the Lead

Hard rules first: retired, opted out, suppressed, already a customer, or open deal → rejected at score 0 (all applicable reasons recorded, not just the first). No LinkedIn URL → `needs_enrichment`, because LinkedIn is always first contact.

Then a score — weighted mean of the terms that apply, each 0..1: competitor evidence 0.35, ICP fit 0.25, intent 0.15, contactability 0.15, source agreement 0.10, threshold **0.5**. Only applicable weights are counted, so a `workers` search isn't punished for having no competitor evidence. Contactability: LinkedIn 0.5, verified email +0.5, unverified/accept-all +0.25, a role address (`info@`) nothing.

In `users` mode the verdict **gates** and the score only ranks: a selected verdict passes with no threshold; a missing verdict is an error, never a pass. **Why:** evidence decides who we contact, the score only decides who we contact first.

## 6. Write the invite and the email

Facts are built from the Lead (max 3 hook facts per kind) and handed to the model, or to offline templates when no key is set. Either way the same four deterministic checks run: shape, length (invite 300 / subject 80 / email 1200), banned phrases ("touch base", "circle back", …), and **grounding** — every claim must cite a real fact and quote words actually in the message, and no name or number may appear that the Lead record never gave.

Fail → up to 3 regenerations → then `manual_review` with no message. **Why:** a hallucinated figure in a cold email is the one failure we cannot take back, so an unchecked message never ships. With a model key a judge also scores 1–5 on personalization, specificity, tone, clarity.

### LLM: writing the invite and the email

`llm_messages.py`, prompts `invite_v2` / `email_v2`. With no key, offline templates write the messages instead. One model call writes the invite, and another writes the email.
- **Problem:** template messages read the same for every Lead, and recipients notice. The model writes around Priya's own hook facts (her role, Acme's DataStax evidence) so each message is about her.
- **How the risk is contained:**
  - Lead facts reach the model only inside one escaped `<lead_facts>` block.
  - The system prompt holds only trusted limits from config.
  - The answer is structured output, which becomes a `Draft` and goes through the same four checks as an offline draft. Grounding is the check that catches invented claims.
  - On a retry, the failed checks are named back to the model, so the next attempt fixes the specific problem instead of guessing.
  - The invite and the email ship as a pair or not at all.
  - Each stored Message records the model and the prompt version.

### LLM: judging message quality

`judge.py`, prompt `judge_v1`. A second model call scores each message 1–5 on four points: personalization, specificity, tone, and clarity.
- **Problem:** the deterministic checks prove a message is *safe*; they cannot say whether it is *good*. The judge gives a quality signal per message, so prompt versions can be compared on numbers.
- **Its limits:** it only measures and never decides whether a message is stored. A failed call or an answer outside the rubric raises a named error rather than a made-up score. With no key it makes no call, and the summary reads `judge: off`.

## 7. Run the sequence

`triggers.py` derives what is owed from the stored events — there is no status column:

1. halted → nothing, ever
2. opted out / suppressed → halt
3. no invite yet → **LinkedIn invite** (always first)
4. invite accepted → email, 2 days later
5. invite unanswered after 5 days → fallback email if there's a verified non-role address, else `stalled`

**Why a fold over events:** the same events and time always produce the same actions, so `leadforge outreach tick` is safe to run repeatedly and the delays are testable.

Dispatch is **dry run only**: a database event, a console line, and a JSONL line in `outbox/dry_run.jsonl`. The module imports no transport, so nothing can leave the machine.

## 8. Read the result

The summary gives counts by status, invites fired, and notes naming each source's mode, the writer (model or offline), judge on/off, and "dry run, nothing sent" — so a demo can't be mistaken for live data. Then `leadforge outreach report --search <id>`, `outreach messages --search <id>`, `outreach scorecard --search <id>`.

---

**The three ideas behind every threshold:** validate before spending; require evidence or return `manual_review`/`unverified` rather than guessing; keep reruns deterministic so the thresholds can be tested instead of argued about.

**The same guardrails around every LLM call:**
- untrusted text goes in escaped blocks, never into the system prompt;
- the answer must fit a schema;
- anything the model claims must be checkable against its input;
- a failure becomes `None`, `manual_review`, or a named error, never a silent fallback.

The model reads and writes language; code decides what happens next.
