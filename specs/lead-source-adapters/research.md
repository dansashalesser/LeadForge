# Provider API Research — lead-source-adapters

_Researched 2026-10-04 from official provider docs. Feeds the requirements and design phases. Re-verify anything marked ⚠ before going live._

## Summary of Changes in 2025–2026 That Affect the Design

| Provider | Change | Design impact |
|----------|--------|---------------|
| Google Search | Custom Search JSON API **closed to new customers**; it shuts down for everyone **2027-01-01** | `GoogleSearchSource` takes a pluggable backend: `serpapi` by default, `cse` only for existing customers |
| HubSpot | **Date-based API versions** since 2026-03-30; latest is `2026-09`. v3 is "legacy", still working, with deprecation to be announced | Use `/crm/objects/2026-09/...`; keep the version as a config constant |
| Leadfeeder | Dealfront rebranded back to Leadfeeder; **new public API** (`/v1/web-visits/...`); legacy `Token token=` API still runs | Build against the new API with the `X-Api-Key` header |
| ZoomInfo | New **GTM API** (`/gtm/data/v1/...`, JSON:API, OAuth2 client credentials); Enterprise JWT API is now "legacy" | Build against the GTM API; cache tokens by `expires_in` |
| Clay | New **public API** (`api.clay.com/public/v0`) with search + routines; table writes not allowed through the API | REST adapter for search; routines for enrichment |
| Apollo | Search endpoint is `mixed_people/api_search` (0 credits, no emails); top-level error fields removed **2027-02-16** | Search, then a separate enrich call for contact data; branch on `error_details.code` |
| HubSpot / ZoomInfo / Clay | Official **MCP servers** exist | MCP transport is an option, not the default (see the MCP section) |

## Common Pattern: Search Is Free, Enrich Costs Credits

Apollo, ZoomInfo, and Clay all split cheap search (preview data, no email or phone) from paid enrichment. The pipeline should **search wide, qualify on preview data, and enrich only selected leads**. This shapes the base class capabilities (`search()` and `enrich()` are separate) and the order of the qualification spec.

---

## Apollo.io

- **Auth**: `x-api-key: <key>` header. OAuth bearer is for partners only.
- **People search**: `POST https://api.apollo.io/api/v1/mixed_people/api_search`. Params go in the query string, even though it's a POST.
  - Filters: `person_titles[]`, `include_similar_titles`, `person_seniorities[]` (`owner, founder, c_suite, partner, vp, head, director, manager, senior, entry, intern`), `q_organization_domains_list[]` (≤1000), `organization_num_employees_ranges[]` (e.g. `"1,10"`), `person_locations[]`, `q_keywords`
  - **Technographics**: `currently_using_any_of_technology_uids[]` / `currently_using_all_of_technology_uids[]` / `currently_not_using_any_of_technology_uids[]`. UIDs are snake_case (spaces and dots become `_`). ⚠ Confirm the DataStax/Cassandra UIDs, likely `datastax` / `apache_cassandra`.
  - Pagination: `page`, `per_page` ≤100, max 500 pages
  - Response: `{total_entries, people[{id, first_name, last_name_obfuscated, title, last_refreshed_at, has_email, has_city, has_state, has_country, has_direct_phone, organization{name, has_industry, has_phone, has_employee_count, ...}}]}`
  - **0 credits; returns no emails or phones**, and last names are obfuscated
- **People enrichment**: `POST https://api.apollo.io/api/v1/people/match`. Params go in the query string: `id | email | linkedin_url | first_name+last_name+domain`, plus `reveal_personal_emails`.
  - `person{id, first_name, last_name, name, linkedin_url, title, headline, email, email_status, seniority, departments, city, state, country, employment_history[], organization{id, name, primary_domain, website_url, linkedin_url, industry, estimated_num_employees, annual_revenue, technology_names[], current_technologies[{uid, name, category}], ...}}`, `match_confidence` (high/medium/low/none)
  - Costs 1 credit (none when `match_confidence=none`). Phone and waterfall options are async (webhook or poll); **out of scope**.
- **Rate limits**: depend on the plan (the docs' example is 600/hour per endpoint). A 429 carries `error_details.code=USAGE.RATE_LIMIT.API_RATE_LIMIT_EXCEEDED` and may include `retry_after_seconds`.
- **Errors**: 401 invalid key, 403 endpoint not allowed, 422 bad params

## HubSpot (CRM check / suppression)

- **Auth**: private app token, `Authorization: Bearer <token>`, scope `crm.objects.contacts.read` (plus companies read)
- **Search**: `POST https://api.hubapi.com/crm/objects/2026-09/contacts/search` (legacy path: `/crm/v3/objects/contacts/search`)
  - Body: `{filterGroups:[{filters:[{propertyName, operator, value|values}]}], properties:[], limit (≤200), after, sorts, query?}`. Up to 6 filter groups. Operators: `EQ, NEQ, IN, NOT_IN, CONTAINS_TOKEN, HAS_PROPERTY, NOT_HAS_PROPERTY, GT, GTE, LT, LTE, BETWEEN, NOT_CONTAINS_TOKEN`
  - Response: `{total, results[{id, properties{...all strings}, createdAt, updatedAt, archived}], paging.next.after}`
  - Properties to request: `email, firstname, lastname, jobtitle, company, lifecyclestage, hs_lead_status, hs_linkedin_url`, plus `notes_last_contacted` and `hs_email_optout`. ⚠ Confirm the exact names through the Properties API.
- **Role**: used mainly for lookups (is this email or domain already a contact or customer? when was it last contacted? opted out?), not for discovery. It feeds the hard excludes in qualification.
- **MCP**: the remote server `mcp.hubspot.com` (GA 2026-04-13) needs OAuth 2.1 with PKCE, which **doesn't fit a headless pipeline**. The local `@hubspot/mcp-server` accepts a private app token and is a possible MCP transport.

## Google Search (intent / web signals)

- **Backend `serpapi` (default)**: `GET https://serpapi.com/search?engine=google&q=...&api_key=...&start=N`
  - `organic_results[{position, title, link, displayed_link, snippet, source, date?}]`, `search_information{total_results, ...}`, `serpapi_pagination{next, ...}`. Identical cached searches (1h) are free.
- **Backend `cse` (legacy, existing customers only, ends 2027-01-01)**: `GET https://customsearch.googleapis.com/customsearch/v1?key&cx&q&num(≤10)&start&dateRestrict`. `start+num ≤ 100`.
  - `items[{title, link, displayLink, snippet, pagemap}]`, `searchInformation.totalResults`, `queries.nextPage`
- **Use**: query templates for DataStax/Cassandra evidence, e.g. `"Astra DB" OR "DataStax" site:linkedin.com/in`, job posts (`"Cassandra" "DataStax" jobs`), conference talks, and GitHub. Output: company domains, person names, and LinkedIn URLs with **evidence snippets**.
- **Security**: snippets are untrusted text. Store them as evidence data and never put them into prompt instructions.

## Leadfeeder (website-visit intent)

- **Auth**: `X-Api-Key: <key>`, or OAuth with scope `web_visits:read`. The user needs the `manage_api` permission.
- **Visitor companies**: `GET https://api.leadfeeder.com/v1/web-visits/companies?account_id&start_date&end_date&page[num]&page[size](≤100)&custom_feed_id&include=company`
  - Response: `{data[{id, type:"company_location", relationships{company{...CompanyV1 when included}, location{attributes{city, country, country_code, region, ...}}}}], meta{pagination{page_count, page_num, total_count}, request_id}}`
  - CompanyV1: `name, alternative_names, url, alternative_urls, industries{industry[{code, name}]}, employee_count, employee_range, address{city, country, country_code, ...}, social_media_profiles{linkedin[{url}]}, description, revenue, founded_year, intent{score, score_tier, last_updated_at}, web_engagement{last_visit_date}, meta{do_not_contact, num_contacts}, keywords`
  - Gets `account_id` from the List Accounts endpoint. Per-visit details are in a separate WebVisitV1 schema (`started_at, visit_length, page_depth, landing_page_path, ...`).
- **Output**: **companies, not people**. They feed the intent score and become domains for people lookup (Apollo, Hunter, UpLead).
- **Rate limits**: not published as numbers. 429 codes are `rate_limit_exceeded`, `quota_exceeded`, and `too_many_requests`.

## Hunter.io (contact enrichment / verification)

- **Auth**: `X-API-KEY` header (an `api_key` query param or Bearer also works). **`test-api-key`** returns dummy data for domain-search, email-finder, and verifier, which is useful for smoke tests.
- **Domain Search**: `GET /v2/domain-search?domain&limit(≤100)&offset&seniority&department&decision_maker&job_titles`. Returns `data{domain, organization, pattern, accept_all, emails[{value, type, confidence, first_name, last_name, position, seniority, department, decision_maker, linkedin, twitter, phone_number, sources[], verification{date, status}}]}`, `meta{results, limit, offset}`.
- **Email Finder**: `GET /v2/email-finder?domain|company|linkedin_handle&first_name&last_name|full_name`. Returns `data{email, score, first_name, last_name, position, linkedin_url, company, verification{status}, sources[]}`.
- **Email Verifier**: `GET /v2/email-verifier?email`. Returns `status ∈ {valid, invalid, accept_all, webmail, disposable, unknown}` and `score`. **202 means still running, so poll.**
- **Combined enrich**: `GET /v2/combined/find?email`. Returns `data{person{name{fullName, givenName, familyName}, employment{domain, name, title, seniority}, linkedin{handle}, geo{...}}, company{name, domain, category{industry}, metrics{employees}, tech[], ...}}`
- **Unusual status codes**: **403 = rate limit, 429 = quota exhausted, 451 = person opted out (`claimed_email`)**. Treat 451 as a permanent suppression and record it.
- **Rate limits**: 15/s and 500/min (the verifier is 10/s and 300/min)

## UpLead (discovery + enrichment)

- **Auth**: `Authorization: <api_key>` (raw key, no Bearer)
- **Base**: `https://api.uplead.com/v2/`
- **Prospector** (discovery): `GET|POST /prospector-search`. Requires `domain`; optional `job_function`, `management_level` (M, D, VP, C, CX), `title`/`titles[]`, `title_search_mode`, `country`, `email_status`, `page`, `per_page` (≤100).
  - `data{results[{id, first_name, last_name, title, job_function, job_sub_function, management_level, email, email_status, phone_number, linkedin_url, industry, domain, company_name, city, state, country}], meta{total, page, next_page, last_page}}`, `userInfo.availableCredits`
- **Person**: `/person-search` with `email` | `first_name+last_name+domain` | `id`
- **Company**: `/company-search` with `domain` | `company` | `id`. Returns `company_name, domain, employees (range enum), revenue (range enum), industry, sic_code, naics_code, linkedin_url, year_founded, ...`. **No technographics.**
- **Combined**: `/combined-search?email`
- **Rate limits**: 500/min with `X-RateLimit-*` and `Retry-After` headers. Costs 1 credit per returned record; contacts with an email are billed only if the status is valid or accept_all.
- **Errors**: 400, 401, 403 (paused/no subscription), 429

## ZoomInfo (GTM API)

- **Auth**: OAuth2 client credentials. `POST https://api.zoominfo.com/gtm/oauth/v1/token` with Basic auth (`client_id:client_secret`), form body `grant_type=client_credentials`, optional `scope`. Returns `access_token`, `expires_in` (the docs example is 1000s), and `token_type=Bearer`. **Cache the token and refresh it before it expires**; don't authenticate on every call.
  - Scopes: `api:data:contact`, `api:data:company`
- **Headers**: `Content-Type: application/vnd.api+json` (JSON:API)
- **Contact search**: `POST https://api.zoominfo.com/gtm/data/v1/contacts/search?page[number]&page[size](≤100)&sort`. Body: `{data:{type:"ContactSearch", attributes:{jobTitle, managementLevel, companyWebsite, techAttributeTagList, requiredFields, employeeRangeMin, country, ...}}}`.
  - `data[{id, type:"Contact", attributes{firstName, lastName, jobTitle, managementLevel, contactAccuracyScore, company{id, name}, hasEmail, hasDirectPhone, ...}}]`, `links{next}`, `meta{totalResults}`
  - **Free (no credits), no emails.** Valid tech tag IDs come from the Lookup endpoint.
- **Contact enrich**: `POST /gtm/data/v1/contacts/enrich` (≤25 per call, uses credits)
- **Technologies**: `POST /gtm/data/v1/companies/technologies/enrich`, body `{data:{type:"TechnologyEnrich", attributes:{companyId}}}`. Returns `data[{attributes{product, vendor, category, categoryParent, ...}}]`. Costs 1 credit per company. **This is the strongest DataStax-usage signal.**
- **MCP**: available (docs.gtm.ai "Connect to ZoomInfo MCP"). Not the default transport.
- **Errors**: `{errors[{id, code, status, title, detail}]}`; 429 when over the request limits

## Clay (public API)

- **Auth**: `clay-api-key: <key>` header (personal key, created under Settings → Account → API keys)
- **Base**: `https://api.clay.com/public/v0`
- **Search**: two steps.
  1. `POST /search/query-mode` with `{query: "<Clay advanced search query>"}` returns `{search_id, source_type: people|companies}`. ⚠ Get the query grammar from `GET` "query reference".
  2. `POST /search/query-mode/{search_id}/run` with `{limit: 1..500, default 20}` returns `{data[], has_more, source_type, exhaustion_reason?, period_quota?}`. The server keeps the cursor, so you call again for the next page.
  - People: `{clay_profile_id, name, first_name, last_name, linkedin_url?, location{name, city, state_or_province}, matched_experiences[{company, title, location, start_date, end_date}]}`
  - Companies: `{clay_company_id, name, domain, size, type, country, industry, location, description, linkedin_url, annual_revenue, total_funding_amount_range_usd}`
- **Enrich**: routines/functions, either execute against 1–100 items or run an async batch and poll. ⚠ Read the schemas before implementing.
- **Limits**: per-workspace rate limit (429 + `Retry-After`). Result quotas depend on the plan (Free: 50/request, 100 per 30 days). Going over a quota returns **402**.
- **Tables**: read-only, Enterprise only. Webhooks are an inbound-only legacy pattern; not used.

---

## MCP Transport Assessment

| Provider | MCP option | Headless-friendly? | Decision |
|----------|-----------|--------------------|----------|
| HubSpot | Remote `mcp.hubspot.com` (OAuth 2.1 PKCE); local `@hubspot/mcp-server` (private app token) | Local server only | REST default; MCP adapter is optional |
| ZoomInfo | GTM MCP | ⚠ check its auth | REST default |
| Clay | MCP via plugin (admin-configured) | Interactive | REST default |

Conclusion: **REST is the default transport for all providers.** The transport stays an injectable seam (`Transport` protocol: `RestTransport`, `McpTransport`) so one MCP-backed adapter can be shown as proof without making the pipeline depend on interactive OAuth.

## Cross-Provider Normalization Notes

- **Identity keys**, strongest first: verified email > LinkedIn URL (normalized: lowercase, no trailing slash, `/in/<handle>`) > (normalized full name + company domain) > provider-native ID
- **Company domain**: normalize by removing the scheme, `www.`, and path, and lowercasing. Leadfeeder supplies `url`; ZoomInfo search wants `http://www.x.com`.
- **Seniority vocabularies differ**: Apollo (`c_suite, vp, director, ...`), ZoomInfo (`C Level Exec, VP Level Exec, Director, Manager, Non Manager`), UpLead (`C, CX, VP, D, M`), Hunter (`junior, senior, executive`). Map them to one canonical enum.
- **Employee counts**: some providers give ranges (UpLead enum, Leadfeeder `employee_range`, Clay `size`), others integers (Apollo, Leadfeeder `employee_count`). Store both `min` and `max`.
- **Email status**: Apollo `email_status`, Hunter `verification.status`, UpLead `email_status`. Map them to `{verified, accept_all, unverified, invalid, unknown}`.
- **Tech evidence**: Apollo `current_technologies[]`, ZoomInfo technologies enrich, Hunter company `tech[]`, Google snippets. Store each as a `TechSignal{source, technology, evidence, observed_at}`.
- **Suppression signals**: Hunter 451, HubSpot `hs_email_optout`, Leadfeeder `meta.do_not_contact`. Each must survive the merge (OR semantics).

## Env Vars (for .env.example)

```
APOLLO_API_KEY=
HUBSPOT_ACCESS_TOKEN=
HUBSPOT_API_VERSION=2026-09
GOOGLE_SEARCH_BACKEND=serpapi        # serpapi | cse
SERPAPI_API_KEY=
GOOGLE_CSE_API_KEY=                  # legacy, existing customers only
GOOGLE_CSE_CX=
LEADFEEDER_API_KEY=
LEADFEEDER_ACCOUNT_ID=
HUNTER_API_KEY=
UPLEAD_API_KEY=
ZOOMINFO_CLIENT_ID=
ZOOMINFO_CLIENT_SECRET=
CLAY_API_KEY=
DATABASE_URL=sqlite:///leadforge.db
LEADFORGE_MODE=synthetic             # synthetic | live
```

## Sources
- Apollo: https://docs.apollo.io/reference/people-api-search, https://docs.apollo.io/reference/people-enrichment
- HubSpot: https://developers.hubspot.com/docs/developer-tooling/platform/versioning, https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm, https://developers.hubspot.com/docs/build-with-ai/remote-mcp-server
- Google: https://developers.google.com/custom-search/v1/overview, https://serpapi.com/search-api
- Leadfeeder: https://docs.leadfeeder.com/api/public/retrieve-visitor-companies-4160962e0
- Hunter: https://hunter.io/api-documentation/v2
- UpLead: https://docs.uplead.com/
- ZoomInfo: https://docs.gtm.ai/reference/searchinterface_searchcontact.md, https://docs.gtm.ai/reference/enrichinterface_enrichtechnology.md, https://docs.gtm.ai/docs/client-credentials-flow.md
- Clay: https://developers.clay.com/api-reference/search/create-a-search-from-a-clay-search-query.md, https://developers.clay.com/api-reference/search/run-the-query-mode-iterator-and-return-the-next-page-of-results.md, https://developers.clay.com/public-api/rate-limits.md
---

# Addendum — Design-Phase Discovery (2026-10-04)

_Appended during the design phase. Covers only findings new to this round; it does not restate the entries above. Scope was deliberately narrow: re-verify the four items the requirements phase left marked ⚠ or unconfirmed, rather than re-researching settled provider facts._

## Summary
- **Feature**: `lead-source-adapters`
- **Discovery Scope**: Complex Integration (eight external providers, greenfield repository at `Initial commit`)
- **Key Findings**:
  - Apollo publishes an authoritative technology-UID list as a downloadable CSV — the long-standing ⚠ on DataStax/Cassandra UIDs becomes checkable rather than guessed.
  - Leadfeeder publishes **no numeric rate limits**, which contradicts Requirement 15.4's "documented limit of 100 requests per minute".
  - Clay's query-mode run endpoint schema is now fully confirmed, including HTTP 402 and a closed `exhaustion_reason` enum.
  - Clay returns `X-RateLimit-*` headers, enabling proactive pacing rather than reactive 429 handling.

## Research Log

### Apollo — technology UID resolution (closes ⚠ from the Apollo section above)
- **Context**: Requirement 12.13 makes the DataStax/Cassandra UIDs config-driven precisely because the correct values were unverified. The open question was whether any authoritative list exists at all.
- **Sources Consulted**: https://docs.apollo.io/reference/people-api-search
- **Findings**:
  - UIDs are strings with underscores substituted for spaces and periods; documented examples are `salesforce`, `google_analytics`, `wordpress_org`.
  - Apollo publishes **1,500+ technologies as a downloadable CSV** at `https://api.apollo.io/v1/auth/supported_technologies_csv`. There is no JSON lookup or search endpoint for UIDs.
  - Companion parameters confirmed: `currently_using_all_of_technology_uids[]`, `currently_not_using_any_of_technology_uids[]`.
  - `per_page` carries **no documented schema maximum**. The only published ceiling is endpoint-level: a 50,000-record display cap described as "100 records per page, up to 500 pages".
  - Rate-limit response headers (`x-minute-requests-left` and siblings) are **not documented on this page**. The 429 body cites a legacy 600-calls-per-hour cap and the structured code `USAGE.RATE_LIMIT.API_RATE_LIMIT_EXCEEDED` with `context.window`.
  - Removal of legacy root-level error fields on 2027-02-16 re-confirmed.
- **Implications**: ship a dated CSV snapshot as `fixtures/apollo/supported_technologies.csv` and validate every UID in `config/technology_uids.yaml` against it at startup, warning on unknowns. Enforce the 100/500 pagination cap client-side, since the schema will not enforce it. Treat 12.5's quota headers as best-effort — the requirement already says "tolerating their absence", which turns out to be load-bearing rather than defensive. *(Superseded 2026-10-06: the full list is Apollo's data and is not vendored or read at run time. The repo keeps a header-plus-configured-rows excerpt, `fixtures/apollo/supported_technologies_excerpt.csv`, held equal to the configuration by a test; `scripts/check_apollo_technologies.py` checks configured UIDs against the live list (needs `APOLLO_API_KEY`). At startup only the UID format is checked; see choices.md.)*

### Leadfeeder — rate limits are undocumented (contradicts Requirement 15.4)
- **Context**: Requirement 15.4 asserts "the documented limit of 100 requests per minute", while the Leadfeeder section above says limits are "not published as numbers". Resolving the discrepancy was necessary before declaring a `RateBucket`.
- **Sources Consulted**: https://docs.leadfeeder.com/api/public/retrieve-visitor-companies-4160962e0
- **Findings**:
  - **No requests-per-second, per-minute, or per-month figures appear anywhere in the reference.** Only the existence of throttling is documented, via 429.
  - Three distinct 429 error codes, not one: `rate_limit_exceeded` (per-second), `quota_exceeded` (monthly request quota), `too_many_requests` (legacy API-gateway throttling).
  - Limits attach to the **credential** — "configured for the API key / OAuth application" — not to the Leadfeeder account.
  - `X-Api-Key` header confirmed as the API-key scheme; OAuth bearer also supported, with `WWW-Authenticate: Bearer realm="api.leadfeeder.com"` on 401.
  - Query parameters confirmed: `account_id`, `start_date`, `end_date` (all required, ISO 8601), `page[num]`, `page[size]` (default 20, max 100), `custom_feed_id`, `include` (only legal value `company`).
  - Documentation inconsistency: the 403 `insufficient_scope` example names scope `companies:read`, while the endpoint description requires `web_visits:read`. Probably boilerplate, but worth knowing if a live 403 appears.
- **Implications**: 15.4's premise is false. The 100/min figure may have been inferred from `page[size] ≤ 100`. Design implements 100/min as a **self-imposed configurable default** with a `documented: False` marker surfaced in the registry listing, and recommends rewording 15.4. The three-way 429 code split is richer than the requirement anticipated and should drive distinct handling: per-second throttle is retryable, monthly quota exhaustion is not.

### Clay — query-mode run endpoint schema (closes ⚠ on Clay search)
- **Context**: The Clay section above flagged the query grammar and response schema as unverified, and recorded 402-on-quota from a secondary source.
- **Sources Consulted**: https://developers.clay.com/api-reference/search/run-the-query-mode-iterator-and-return-the-next-page-of-results.md, https://developers.clay.com/public-api/rate-limits.md
- **Findings**:
  - Request body `NextSearchResultsBody`: single optional field `limit`, integer, default 20, min 1, max 500. `additionalProperties: false`. An empty body is valid.
  - Response is a `oneOf` discriminated by `source_type` (constant `people` or `companies`). Shared envelope: `data` (required), `has_more` (required bool), `source_type` (required), `exhaustion_reason` (optional, **closed enum: `query_limit` | `no_more_results`**), `period_quota` (optional object with all four of `limit`, `used`, `remaining`, `resets_at` required). `additionalProperties: false` throughout.
  - `clay_profile_id` and `clay_company_id` are **numbers, not strings**.
  - Documented status codes: 200, 400, 401, **402**, 403, 404, 429 — all errors return `ErrorResponse{message: string}` with no additional properties. 402 confirmed as documented, though the page gives it no description.
  - Rate limits: **no numeric values published**. Per-workspace limit, 429 with `Retry-After` in seconds, plus `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` "when Clay sends them". Clay's own guidance recommends exponential backoff with jitter and modest async polling intervals.
  - The rate-limits page does **not** mention 402 or any quota concept; the 402 confirmation comes from the run-endpoint status list.
- **Implications**: Clay's `additionalProperties: false` maps cleanly onto Pydantic `extra="forbid"`, satisfying Requirement 1.4's strictness at the raw-schema layer for free. The closed `exhaustion_reason` enum makes Requirement 19.6's stop condition exhaustive rather than open-ended. Use `X-RateLimit-*` for proactive pacing instead of waiting for 429. Clay's enrichment routine/function schemas remain unverified — build that path last, behind fixtures.

## Design Decisions

### Decision: Synthetic mode as transport substitution, not an adapter branch
- **Context**: Requirements 4.1, 5.2, 7.5, and 11.5 collectively demand that synthetic mode traverse the identical normalization path while making zero network calls and incurring zero throttle.
- **Alternatives Considered**:
  1. A `if self.data_mode is SYNTHETIC:` branch inside each adapter's `fetch_raw()`.
  2. A third `Transport` implementation, `FixtureTransport`, selected at construction.
- **Selected Approach**: Option 2. The adapter is handed an already-bound transport and never learns which it got.
- **Rationale**: turns four acceptance criteria from behaviours a test must police into structural guarantees. `FixtureTransport` holds no socket, so 4.1 and 11.5 cannot be violated; there is only one `normalize()` body, so 5.2 cannot drift.
- **Trade-offs**: fixture loading must mimic transport-level concerns (status codes, headers) for error-path tests. Accepted — it is less code than keeping two branches honest in eight adapters.
- **Follow-up**: confirm `FixtureTransport` can synthesize the header sets the error-classification tests need (Apollo quota headers, Clay `X-RateLimit-*`, `Retry-After`).

### Decision: Error classification on the adapter, retry dispatch on exception type
- **Context**: Hunter inverts the HTTP convention — 403 means rate-limited and 429 means quota-exhausted (16.7) — while every other provider follows it.
- **Alternatives Considered**:
  1. A shared status-code table with per-provider exceptions.
  2. A `classify_error()` hook on `BaseLeadSource` with a conventional default, overridden once.
- **Selected Approach**: Option 2, with the orchestrator's retry policy keyed on `SourceError` subclass rather than status code.
- **Rationale**: the inversion stays entirely inside the one adapter that has it. Nothing above `BaseLeadSource` ever sees an HTTP status, which also serves Requirement 20.1's ban on transport-specific types above the interface.
- **Trade-offs**: one more overridable method on the base class.
- **Follow-up**: none.

### Decision: Extend Requirement 8.4's conflict ordering into a total order
- **Context**: 8.4 orders conflict winners by trust rank, then confidence, then recency. 8.8 requires byte-identical merged output under source shuffling. Those three keys can tie — routinely so in synthetic mode, where fixture timestamps are fixed.
- **Alternatives Considered**:
  1. Accept non-determinism on exact ties and weaken the 8.8 test to field-set equality.
  2. Append deterministic tiebreaks after 8.4's three keys.
- **Selected Approach**: Option 2 — append `source_name` then `sha256(canonical_value)`.
- **Rationale**: the appended components never override 8.4's stated three; they only decide cases 8.4 leaves undefined. Without them 8.8 is not implementable as written.
- **Trade-offs**: the tiebreak is arbitrary from a data-quality standpoint. Acceptable, since by construction it only fires when the specified criteria are exhausted.
- **Follow-up**: state the full ordering in the merge module docstring so a future reader does not mistake the extra keys for a deviation from 8.4.

## Risks & Mitigations
- Union-find over-merge via placeholder emails or role LinkedIn URLs, with no unmerge path available (8.12) — mitigate with a configurable placeholder denylist excluded from key extraction, plus `projection_version` so a rule fix and recompute is the sanctioned repair. Highest-consequence failure mode in the design.
- Apollo technology-UID snapshot going stale — mitigate with startup validation against the shipped CSV plus the zero-match run warning required by 12.13. *(Superseded 2026-10-06: the full list is Apollo's data and is not vendored or read at run time. The repo keeps a header-plus-configured-rows excerpt, `fixtures/apollo/supported_technologies_excerpt.csv`, held equal to the configuration by a test; `scripts/check_apollo_technologies.py` checks configured UIDs against the live list (needs `APOLLO_API_KEY`). At startup only the UID format is checked; see choices.md.)*
- Clay enrichment routine/function schemas still unverified — build the Clay enrich path last, behind fixtures.
- Requirement 9.5's Postgres leg skipping silently when `TEST_POSTGRES_URL` is unset — make it a required CI gate against the `docker-compose` Postgres profile, otherwise the criterion is unmet on a developer machine.

## References
- [Apollo People Search API](https://docs.apollo.io/reference/people-api-search) — technology UID format, the supported-technologies CSV, pagination ceiling, 429 structure
- [Clay — run the query-mode iterator](https://developers.clay.com/api-reference/search/run-the-query-mode-iterator-and-return-the-next-page-of-results.md) — request/response schema, status codes including 402
- [Clay — public API rate limits](https://developers.clay.com/public-api/rate-limits.md) — 429 behaviour, `Retry-After`, `X-RateLimit-*` headers
- [Leadfeeder — retrieve visitor companies](https://docs.leadfeeder.com/api/public/retrieve-visitor-companies-4160962e0) — auth header, query parameters, absence of numeric rate limits, three-way 429 error codes
