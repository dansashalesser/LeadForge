"""Apollo.io Source adapter: Discovery search and Enrichment match (12.1, 12.2).

Calls ``POST /api/v1/mixed_people/api_search``, which costs no credits and returns no
email or phone and only an obfuscated last name (12.8). Technographic targeting is a
set of snake_case technology UIDs read from the Target Profile's Apollo vocabulary
(12.12, 12.13); this module holds no UID of its own beyond the declared default every
adapter carries for a term. Error classification (12.3 in tasks.md) is a separate task.

Enrichment (task 12.2) calls ``POST /api/v1/people/match``, the only Credit-bearing
path: one credit per match, none when ``match_confidence`` is ``none`` (12.8, 12.9).
It is a POST because Apollo takes it so; it reads a person and writes nothing to
Apollo's data, so it is a read-only ``Endpoint``. There is no webhook or phone path
(12.14).

Provisional decisions (see choices.md, task 12.2):

* A request is told apart by type: an ``EnrichmentRequest`` runs match calls, any
  other request runs the Discovery search. (Superseded 2026-10-06: match now runs for
  people from any source, see "Lookup ladder" below.)
* Cost declarations: PAID, PER_LEAD, no Suppression yield (match returns no opt-out
  flag). Discovery stays free; the declaration describes the adapter's Enrichment tier.
* The raw batch for Enrichment is ``{"matches": [{"lookup", "rung", "response"}],
  "attach": [{"lookup", "rung", "anchor", "asked", "corroborate"}]}``: one match per
  distinct lookup actually asked (verbatim, so a no-match stays in the persisted
  evidence) and one attachment per hit and requester identity.
  ``credits_in(batch)`` derives the credit count from it (a pure function: there is
  no run-record channel yet). A no-match is also logged as ``apollo_no_match``.
* ``match_confidence`` is a closed set (high, medium, low, none); anything else, or a
  non-none match without a person, is a ``NormalizationError``, never a silent bill.
* ``match_confidence`` is per record, not per field, and is not mapped to numbers
  (that would be invention). The Field Confidence is ours instead: see the ladder.
* No Negative Evidence is emitted for match: no field question is declared as queried.

Provisional decisions (see choices.md, task 12.1):

* One search is made per configured UID, not one ``any_of`` search over all of them,
  because only a per-UID total can say which identifier matched nothing (12.13).
  People found under several UIDs are kept once, by Apollo person id.
* A page shorter than the page size ends paging for that UID; page 500 is the hard end.
* Synthetic mode needs no key and sends no credential header; live mode resolves
  ``APOLLO_API_KEY`` from the environment when a fetch starts.
* The supported-technology snapshot is read at construction (startup); an unknown UID
  warns, it does not fail, since the snapshot can be older than Apollo's list.
* The raw models tolerate unknown fields (Apollo adds some without notice); a field the
  models and ``RULES``/``IGNORED`` do not name is caught by the fixture test, not at
  run time. Types of the named fields are strict, so a wrong shape raises.
* Provider free text (names, title, company name) is ``UntrustedText``; identifiers and
  the technology list are not.
* ``fixtures/apollo/supported_technologies.csv`` is a hand-made four-row STAND-IN, not
  Apollo's published list; the snapshot date below is the stand-in's date.
* Warnings go to the structured log; there is no run-record warning channel yet.

Lookup ladder (follow-up, user decision 2026-10-06: Apollo enriches people other
sources found, by other search terms when no Apollo id is known):

* Each work-list person, from any source, is asked by the first rung it has and climbs
  on a ``none`` answer only, stopping at the first hit: (1) the Apollo id (its own
  record's, or the one an Apollo record with the same LinkedIn identity carries), (2)
  ``linkedin_url``, (3) ``email``, (4) ``first_name`` + ``last_name`` + ``domain``
  (registrable), else ``organization_name`` when there is no domain. Parameter names
  are those Apollo's own CLI sends to people/match and its enrichment docs name
  (live-docs-findings A9). They travel as the POST's JSON body, no query string
  (VERIFIED 2026-10-06 against Apollo's own CLI, github.com/apolloio/apollo-io-cli
  2.1.0, commit 70ce295: ``src/commands/people.ts`` ``buildPeopleEnrichBody`` builds
  the body and ``src/api.ts`` ``apolloRequest`` sends it as JSON; supersedes the
  query-parameter form). That covers the LinkedIn, address and name rungs only: the
  same CLI asks by Apollo ``id`` with ``GET /people/match?id=`` (``people.ts`` ``email``
  command, ``apolloGet``), so the id rung's POST JSON body is UNVERIFIED (kept: one
  POST endpoint, no GET allowed). A masked or blank name (any ``*``) is never a term.
* Per-run cache keyed by the normalised lookup (id; LinkedIn identity; lowercased
  address; casefolded name with domain or company name): duplicates and a retried
  fetch never ask twice. ``matches`` lists each lookup once, so ``credits_in`` counts
  real billed calls. A hit also answers the lookups its own strong keys name (its
  Apollo id, LinkedIn identity, and address when Apollo marks it verified): one
  person seen once by LinkedIn and once by address alone costs one Credit. The
  ambiguity and cannot-link rules below still apply to the lookup it serves.
* The answer carries the requester's identity at ``asked.*`` (``REQUEST_ECHO_PREFIX``)
  so the normal Match Keys put it on that person, and the echo never corroborates. The
  anchor is the requester's strongest key: its LinkedIn URL; else its verified address
  (with ``email_status`` verified); else name + domain, only when it has a title or
  employer for 8.3's corroboration; else (not Apollo's own record) the person is NOT
  asked (``apollo_enrich_unattachable``, a count): no answer could reach it.
* Field Confidence is origin ``heuristic`` on every field Apollo observed, by the rung
  that hit (``RUNG_CONFIDENCE``): id, LinkedIn, email 0.9; name + domain 0.6; name +
  company name 0.5. Echoed fields keep origin ``none``. Numbers are ours, provisional.
* A weak hit (a name rung, or a name anchor) contributes no LinkedIn URL, address or
  address status from Apollo: those are Match Keys, and a name-only match would split
  the answer from its bare requester (8.3) or bridge two people. The raw answer keeps
  them as evidence.
* Not attached: an answer whose LinkedIn identity differs from the requester's
  (cannot-link; ``apollo_match_discarded`` reason ``linkedin_mismatch``); a name-anchor
  answer whose title and employer both differ from the requester's (reason
  ``uncorroborated``); and any lookup asked on behalf of two distinguishable people
  (distinct LinkedIn identities, none counting as one more), which is not asked at all
  (``apollo_match_ambiguous``, a count). People/match returns one person, so "several
  candidates" is only visible this way. A discard ends that person's ladder.
* Logs name a rung and a reason, never a lookup value, except an Apollo id.

Rate limits (follow-up, 2026-10-06; supersedes the single 600-per-hour bucket):

* Apollo's limits depend on the plan and are per minute, per hour and per day, ANDed
  in one bucket. Search and match are paced on separate buckets, because Apollo
  publishes a separate table for search endpoints. Figures (``_PLAN_LIMITS``) are from
  https://docs.apollo.io/reference/rate-limits, read through a search-engine extract
  on 2026-10-06 (the page was network-blocked): search, Free 50/min 200/h 600/day and
  paid plans 200/min 6,000/h 50,000/day; other endpoints, Free 50/min 200/h 600/day,
  Basic and Professional 200/min 400/h 2,000/day, Organization 200/min 600/h
  6,000/day.
* The plan is ``APOLLO_PLAN`` (free, basic, professional, organization), a non-secret
  setting read when a live run starts. Unset or blank means free, the lowest figures,
  so the default is safe on every plan. Any other value is a ``ConfigurationError``
  naming the variable, never the value.
"""

import csv
import io
import json
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal, Self

import structlog
from pydantic import BaseModel, StrictStr

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    EnrichmentRequest,
    LeadContribution,
    LiveAccess,
    RateBucket,
    RateWindow,
    RawBatch,
    SourceRequest,
    resolve_credentials,
    retry_after_seconds,
)
from leadforge.lead_ingestion.companies import company_domains
from leadforge.lead_ingestion.compliance import identities
from leadforge.lead_ingestion.errors import (
    ConfigurationError,
    NormalizationError,
    SourceError,
    SourceRateLimited,
    SourceUnauthorized,
)
from leadforge.lead_ingestion.match_keys import (
    MatchKeyKind,
    MatchKeys,
    extract_match_keys,
    linkedin_identity,
    normalize_linkedin_url,
    normalized_person_name,
    registrable_domains,
)
from leadforge.lead_ingestion.models import (
    REQUEST_ECHO_PREFIX,
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
    UntrustedText,
)
from leadforge.lead_ingestion.normalizer import (
    REQUEST_ECHO_KEY,
    REQUEST_ECHO_RULES,
    FieldRule,
    NormalizationContext,
    Normalizer,
    unmapped_raw_paths,
    validate_raw_payload,
)
from leadforge.lead_ingestion.orchestrator import _components
from leadforge.lead_ingestion.transport import Transport, TransportResponse

if TYPE_CHECKING:
    from leadforge.lead_ingestion.pacing import SourcePacing

__all__ = [
    "DEFAULT_PLAN",
    "MAX_PAGE",
    "MAX_PER_PAGE",
    "PLAN_ENV",
    "RUNG_CONFIDENCE",
    "ApolloSource",
    "credits_in",
    "plan_rate_limit",
]

MAX_PER_PAGE = 100
MAX_PAGE = 500  # Apollo's documented display ceiling: 100 x 500 = 50,000 records

SUPPORTED_TECHNOLOGIES_SNAPSHOT = (
    Path(__file__).parent.parent / "fixtures" / "apollo" / "supported_technologies.csv"
)
# Date of the hand-made stand-in CSV, not of a real Apollo export (see module doc).
SUPPORTED_TECHNOLOGIES_SNAPSHOT_DATE = "2026-10-05"

_UID_PARAM = "currently_using_any_of_technology_uids[]"
_KEY_HEADER = "x-api-key"
_KEY_ENV = "APOLLO_API_KEY"
_DOCS = "https://docs.apollo.io/reference/people-api-search"

_RATE_LIMITS_DOCS = "https://docs.apollo.io/reference/rate-limits"
PLAN_ENV = "APOLLO_PLAN"
DEFAULT_PLAN = "free"  # the lowest documented limits, safe on every plan

_SEARCH = Endpoint(
    method="POST", path="/api/v1/mixed_people/api_search", bucket="search"
)

_MATCH = Endpoint(method="POST", path="/api/v1/people/match", bucket="match")

# Plan -> ((search per minute, hour, day), (match per minute, hour, day)). From
# https://docs.apollo.io/reference/rate-limits, read 2026-10-06 through a search-engine
# extract (the page itself was network-blocked): search endpoints have their own
# table; people/match falls under the general-endpoint table.
_Windows = tuple[int, int, int]
_PLAN_LIMITS: Mapping[str, tuple[_Windows, _Windows]] = {
    "free": ((50, 200, 600), (50, 200, 600)),
    "basic": ((200, 6000, 50_000), (200, 400, 2000)),
    "professional": ((200, 6000, 50_000), (200, 400, 2000)),
    "organization": ((200, 6000, 50_000), (200, 600, 6000)),
}


def _bucket(name: str, windows: _Windows) -> RateBucket:
    per_minute, per_hour, per_day = windows
    return RateBucket(
        name=name,
        windows=(
            RateWindow(requests=per_minute, per_seconds=60.0),
            RateWindow(requests=per_hour, per_seconds=3600.0),
            RateWindow(requests=per_day, per_seconds=86_400.0),
        ),
        documented=True,
        doc_url=_RATE_LIMITS_DOCS,
    )


def plan_rate_limit(plan: str) -> Mapping[str, RateBucket]:
    """The ``search`` and ``match`` buckets of one documented Apollo plan."""
    search, match = _PLAN_LIMITS[plan]
    return {
        _SEARCH.bucket: _bucket(_SEARCH.bucket, search),
        _MATCH.bucket: _bucket(_MATCH.bucket, match),
    }


# Field Confidence (origin heuristic) of an answer, by the rung that found it.
RUNG_CONFIDENCE: Mapping[str, float] = {
    "id": 0.9,
    "linkedin_url": 0.9,
    "email": 0.9,
    "name_domain": 0.6,
    "name_organization": 0.5,
}

_ID_PATH = "person.provider_id"
_NO_MATCH = "none"

# Apollo's documented stable code for throttling (12.11); read from error_details only.
RATE_LIMIT_CODE = "USAGE.RATE_LIMIT.API_RATE_LIMIT_EXCEEDED"
_CODE_SHAPE = re.compile(r"[A-Za-z0-9_.]{1,100}")
_NO_CODE = "no_error_code"
_MAX_ALLOWANCE_DIGITS = 15
_ALLOWANCE_HEADERS = {
    "x-minute-requests-left": "minute",
    "x-hourly-requests-left": "hour",
    "x-24-hour-requests-left": "day",
}

_log = structlog.get_logger()


class _Technology(BaseModel):
    uid: StrictStr


class _Organization(BaseModel):
    name: StrictStr | None = None
    current_technologies: list[_Technology] | None = None
    primary_domain: StrictStr | None = None


class _Person(BaseModel):
    id: StrictStr
    first_name: StrictStr | None = None
    last_name_obfuscated: StrictStr | None = None
    title: StrictStr | None = None
    linkedin_url: StrictStr | None = None
    organization: _Organization | None = None


class _MatchedPerson(BaseModel):
    id: StrictStr
    first_name: StrictStr | None = None
    last_name: StrictStr | None = None
    title: StrictStr | None = None
    linkedin_url: StrictStr | None = None
    email: StrictStr | None = None
    email_status: StrictStr | None = None
    organization: _Organization | None = None


class _Match(BaseModel):
    match_confidence: Literal["high", "medium", "low", "none"]
    person: _MatchedPerson | None = None


def _company_domain(value: object) -> str | None:
    """Apollo's ``primary_domain`` as one registrable domain (pinned PSL), else None.

    Webmail, a bare suffix, ``localhost`` and blank name no company (``companies``).
    """
    domains = company_domains(value)
    return next(iter(domains)) if len(domains) == 1 else None


class ApolloSource(BaseLeadSource):
    name: ClassVar[str] = "apollo"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.SEARCH, Capability.ENRICH}
    )
    # The conservative default; a live run is paced on ``run_rate_limit``.
    rate_limit: ClassVar[Mapping[str, RateBucket]] = plan_rate_limit(DEFAULT_PLAN)
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {
        "person.title": frozenset({"title"}),
        "person.linkedin_url": frozenset({"linkedin_url"}),
        "company.name": frozenset({"organization.name"}),
        "company.technologies": frozenset({"organization.current_technologies"}),
        "target_profile.datastax": frozenset({_UID_PARAM}),
        "target_profile.apache_cassandra": frozenset({_UID_PARAM}),
    }
    # Search is free; people/match, the only credit-bearing path, costs one per lead.
    cost_class: ClassVar[CostClass] = CostClass.PAID
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_LEAD
    yields_suppression: ClassVar[bool] = False
    live_access: ClassVar[LiveAccess] = LiveAccess.GATED
    target_vocabulary: ClassVar[Mapping[str, object]] = {
        "datastax": ["datastax"],
        "apache_cassandra": ["apache_cassandra"],
    }
    endpoints: ClassVar[Mapping[str, Endpoint]] = {
        "search": _SEARCH,
        "match": _MATCH,
    }
    required_env: ClassVar[tuple[str, ...]] = (_KEY_ENV,)
    optional_env: ClassVar[tuple[str, ...]] = (PLAN_ENV,)
    env_notes: ClassVar[Mapping[str, str]] = {
        PLAN_ENV: (
            f"optional: your Apollo plan, one of {', '.join(_PLAN_LIMITS)}; sizes the "
            f"rate limits ({_RATE_LIMITS_DOCS}); unset means {DEFAULT_PLAN}"
        )
    }
    docs_url: ClassVar[str] = _DOCS
    base_url: ClassVar[str] = "https://api.apollo.io"

    @classmethod
    def run_rate_limit(
        cls, environ: Mapping[str, str] | None = None
    ) -> Mapping[str, RateBucket]:
        """The documented windows of the plan named by ``APOLLO_PLAN`` (7.1)."""
        env = os.environ if environ is None else environ
        plan = env.get(PLAN_ENV, "").strip().casefold() or DEFAULT_PLAN
        if plan not in _PLAN_LIMITS:
            # The value is not echoed, as for every configuration error.
            raise ConfigurationError(
                "environment",
                key_path=PLAN_ENV,
                detail=f"must be one of {', '.join(_PLAN_LIMITS)}",
            )
        return plan_rate_limit(plan)

    RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule("person.provider_id", "id"),
        FieldRule("person.first_name", "first_name", untrusted=True),
        FieldRule("person.last_name", "last_name_obfuscated", untrusted=True),
        FieldRule("person.title", "title", untrusted=True),
        FieldRule("person.linkedin_url", "linkedin_url"),
        FieldRule("company.name", "organization.name", untrusted=True),
        FieldRule("company.technologies", "organization.current_technologies"),
    )
    MATCH_RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule(_ID_PATH, "person.id"),
        FieldRule("person.first_name", "person.first_name", untrusted=True),
        FieldRule("person.last_name", "person.last_name", untrusted=True),
        FieldRule("person.title", "person.title", untrusted=True),
        FieldRule("person.linkedin_url", "person.linkedin_url"),
        FieldRule("person.email", "person.email"),
        FieldRule("person.email_status", "person.email_status"),
        FieldRule("company.name", "person.organization.name", untrusted=True),
        FieldRule("company.technologies", "person.organization.current_technologies"),
        FieldRule(
            "company.domain",
            "person.organization.primary_domain",
            transform=_company_domain,
        ),
    )
    # Search returns presence flags and a refresh time, none of which we contribute.
    IGNORED: ClassVar[frozenset[str]] = frozenset(
        {
            "last_refreshed_at",
            "has_email",
            "has_city",
            "has_state",
            "has_country",
            "has_direct_phone",
            "organization.has_industry",
            "organization.has_phone",
            "organization.has_employee_count",
            # Match: the record-level certainty is read by the adapter, not contributed.
            "match_confidence",
        }
    )

    # Search envelope fields that carry no lead: the size of the whole result set.
    SEARCH_ENVELOPE_IGNORED: ClassVar[frozenset[str]] = frozenset({"total_entries"})

    def __init__(
        self,
        mode: DataMode,
        *,
        transport: Transport,
        vocabulary: Mapping[str, object] | None = None,
        environ: Mapping[str, str] | None = None,
        per_page: int = MAX_PER_PAGE,
        pacing: "SourcePacing | None" = None,
    ) -> None:
        super().__init__(mode, transport=transport, pacing=pacing)
        if per_page < 1:
            raise ValueError(f"per_page must be at least 1, got {per_page}")
        self._per_page = min(per_page, MAX_PER_PAGE)
        self._environ = environ
        # Answers already paid for this run (the batch's ``matches`` entry, by lookup
        # key): a retried fetch must not buy them again.
        self._matched: dict[str, Mapping[str, Any]] = {}
        # A lookup a hit already answers: the hit's own strong keys (Apollo id,
        # LinkedIn identity, verified address) -> the lookup that bought it.
        self._served_by: dict[str, str] = {}
        # LinkedIn identities (None: none) that asked each lookup, across fetches.
        self._asked_for: dict[str, set[str | None]] = {}
        self._allowances: dict[str, int] = {}
        self._uids = _uids_of(
            self.target_vocabulary if vocabulary is None else vocabulary
        )
        supported = _supported_technologies()
        for uid in self._uids:
            if uid not in supported:
                _log.warning(
                    "apollo_unknown_technology_uid",
                    uid=uid,
                    snapshot_date=SUPPORTED_TECHNOLOGIES_SNAPSHOT_DATE,
                )

    @classmethod
    def from_run(
        cls,
        mode: DataMode,
        *,
        transport: Transport,
        pacing: "SourcePacing | None",
        vocabulary: Mapping[str, object] | None,
    ) -> Self:
        """Technology UIDs come from the Target Profile when one was read (12.1)."""
        return cls(mode, transport=transport, pacing=pacing, vocabulary=vocabulary)

    @property
    def allowances(self) -> Mapping[str, int]:
        """Requests left per window (``minute``, ``hour``, ``day``), last response.

        A window Apollo did not report, or reported unreadably, has no entry: absence
        means unknown, never zero (12.5).
        """
        return dict(self._allowances)

    def _note_response(self, response: TransportResponse) -> None:
        self._allowances = _allowances_in(response.headers)

    def classify_error(
        self, response: TransportResponse, *, endpoint: Endpoint
    ) -> SourceError | None:
        """Apollo's reading of the conventional mapping (12.4-12.11).

        Reads only ``error_details.code``, never a top-level error field or message
        text. The documented rate-limit code, or a 429, is ``SourceRateLimited``; a
        401 or 403 is ``SourceUnauthorized`` naming the endpoint and the scope cause.
        Everything else is the base default. Error text names paths and codes, never
        a body.
        """
        status = response.status
        if 200 <= status < 300:
            return None
        code = _error_code(response.body)
        if code == RATE_LIMIT_CODE or status == 429:
            return SourceRateLimited(
                self.name,
                cause=code or "unrecognized_429",
                retry_after_s=retry_after_seconds(response.headers),
            )
        if status in (401, 403):
            return SourceUnauthorized(
                self.name, endpoint=endpoint.path, scope_cause=code or _NO_CODE
            )
        error = super().classify_error(response, endpoint=endpoint)
        if type(error) is SourceError and code:
            return SourceError(
                self.name, f"{endpoint.path} returned status {status} code={code}"
            )
        return error

    def _headers(self) -> Mapping[str, str]:
        if self.data_mode is DataMode.SYNTHETIC:
            return {}
        return {_KEY_HEADER: resolve_credentials(self, self._environ)[_KEY_ENV]}

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        if isinstance(request, EnrichmentRequest):
            return await self._enrich(request)
        headers = self._headers()
        people: dict[str, Mapping[str, Any]] = {}
        for uid in self._uids:
            matched = False
            for number in range(1, MAX_PAGE + 1):
                found = await self._search(uid, number, headers)
                matched = matched or bool(found)
                for person in found:
                    people.setdefault(str(person.get("id")), person)
                if len(found) < self._per_page:
                    break
            if not matched:
                _log.warning("apollo_technology_no_matches", uid=uid)
        return RawBatch(
            source_name=self.name, payload={"people": list(people.values())}
        )

    async def _search(
        self, uid: str, number: int, headers: Mapping[str, str]
    ) -> list[Mapping[str, Any]]:
        response = await self._send(
            _SEARCH,
            params={_UID_PARAM: uid, "page": number, "per_page": self._per_page},
            json_body=None,
            headers=headers,
        )
        body = response.body
        found = body.get("people") if isinstance(body, Mapping) else None
        if not isinstance(found, list) or not all(
            isinstance(p, Mapping) for p in found
        ):
            raise NormalizationError(
                self.name, raw_field_path="people", canonical_path="<unmapped>"
            )
        return found

    async def _enrich(self, request: EnrichmentRequest) -> RawBatch:
        """Each person climbs the lookup ladder once, stopping at the first hit."""
        askers, unattachable = _plan(self.name, request.work_list)
        if unattachable:
            _log.info("apollo_enrich_unattachable", persons=unattachable)
        for asker in askers:
            for lookup in asker.ladder:
                self._asked_for.setdefault(lookup.key, set()).add(asker.identity)
        matches: dict[str, Mapping[str, Any]] = {}
        attach: dict[str, Mapping[str, Any]] = {}
        ambiguous: set[str] = set()
        headers = self._headers() if askers else {}
        for asker in askers:
            for lookup in asker.ladder:
                if len(self._asked_for[lookup.key]) > 1:
                    ambiguous.add(lookup.key)  # one answer cannot fit two people
                    break
                key = await self._answer(lookup, headers)
                matches.setdefault(key, self._matched[key])
                if self._matched[key]["response"].get("match_confidence") != _NO_MATCH:
                    entry = asker.attachment(replace(lookup, key=key))
                    attach.setdefault(json.dumps(entry, sort_keys=True), entry)
                    break
        if ambiguous:
            _log.warning("apollo_match_ambiguous", lookups=len(ambiguous))
        return RawBatch(
            source_name=self.name,
            payload={
                "matches": list(matches.values()),
                "attach": list(attach.values()),
            },
        )

    async def _answer(self, lookup: "_Lookup", headers: Mapping[str, str]) -> str:
        """The key of the paid answer to ``lookup``, asking Apollo only when none is.

        A lookup an earlier hit already answers (the hit's Apollo id, LinkedIn identity
        or verified address equals it) is served by that hit: one person seen once by
        LinkedIn and once by address alone costs one Credit, not two.
        """
        key = (
            lookup.key
            if lookup.key in self._matched
            else self._served_by.get(lookup.key, lookup.key)
        )
        if key not in self._matched:
            response = await self._match(lookup.params, headers)
            self._matched[key] = {
                "lookup": key,
                "rung": lookup.rung,
                "response": response,
            }
            for alias in _answer_keys(response):
                self._served_by.setdefault(alias, key)
        return key

    async def _match(
        self, params: Mapping[str, str], headers: Mapping[str, str]
    ) -> Mapping[str, Any]:
        response = await self._send(
            _MATCH, params=None, json_body=params, headers=headers
        )
        if not isinstance(response.body, Mapping):
            raise NormalizationError(
                self.name, raw_field_path="person", canonical_path="<unmapped>"
            )
        return response.body

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        payload = raw.payload
        if isinstance(payload, Mapping) and "matches" in payload:
            return self._normalize_matches(raw)
        found = payload.get("people") if isinstance(payload, Mapping) else None
        if not isinstance(found, list):
            raise NormalizationError(
                self.name, raw_field_path="people", canonical_path="<unmapped>"
            )
        context = NormalizationContext(
            source_name=self.name,
            data_mode=self.data_mode,
            fetched_at=datetime.now(UTC),
            answerable_surfaces=self.answerable_surfaces,
        )
        normalizer = Normalizer()
        return [
            normalizer.apply(
                validate_raw_payload(self.name, _Person, person, self.RULES),
                self.RULES,
                context,
            )
            for person in found
        ]

    def records_fetched(self, batch: RawBatch) -> int | None:
        """People a search page returned, or match answers an Enrichment received."""
        payload = batch.payload
        key = (
            "matches"
            if isinstance(payload, Mapping) and "matches" in payload
            else "people"
        )
        records = payload.get(key) if isinstance(payload, Mapping) else None
        if not isinstance(records, list):
            raise NormalizationError(
                self.name, raw_field_path=key, canonical_path="<unmapped>"
            )
        return len(records)

    def credits_spent(self, batch: RawBatch) -> int | None:
        """Search is free; a live match batch spends ``credits_in``; synthetic none."""
        payload = batch.payload
        if self.data_mode is not DataMode.LIVE or not (
            isinstance(payload, Mapping) and "matches" in payload
        ):
            return 0
        return credits_in(batch)

    @classmethod
    def validate_fixture(cls, endpoint: str, body: object) -> None:
        if endpoint == "match":
            checked = _require(body)
            validate_raw_payload(cls.name, _Match, checked, cls.MATCH_RULES)
            return
        if endpoint != "search":
            super().validate_fixture(endpoint, body)
            return
        people = _require(body).get("people")
        if not isinstance(people, list):
            raise NormalizationError(
                cls.name, raw_field_path="people", canonical_path="<unmapped>"
            )
        for person in people:
            validate_raw_payload(cls.name, _Person, _require(person), cls.RULES)

    @classmethod
    def unmapped_fixture_paths(cls, endpoint: str, body: object) -> list[str]:
        if endpoint == "match":
            return unmapped_raw_paths(_require(body), cls.MATCH_RULES, cls.IGNORED)
        if endpoint != "search":
            return super().unmapped_fixture_paths(endpoint, body)
        checked = _require(body)
        people = checked.get("people")
        if not isinstance(people, list):
            raise NormalizationError(
                cls.name, raw_field_path="people", canonical_path="<unmapped>"
            )
        envelope = {k: v for k, v in checked.items() if k != "people"}
        found = unmapped_raw_paths(envelope, (), cls.SEARCH_ENVELOPE_IGNORED)
        for person in people:
            found += [
                f"people.{path}"
                for path in unmapped_raw_paths(_require(person), cls.RULES, cls.IGNORED)
            ]
        return found

    @classmethod
    def validate_reference_file(cls, file: str, text: str) -> None:
        if file != SUPPORTED_TECHNOLOGIES_SNAPSHOT.name:
            super().validate_reference_file(file, text)
            return
        _check_technology_snapshot(cls.name, text)

    def _normalize_matches(self, raw: RawBatch) -> list[LeadContribution]:
        context = NormalizationContext(
            source_name=self.name,
            data_mode=self.data_mode,
            fetched_at=datetime.now(UTC),
            answerable_surfaces=self.answerable_surfaces,
        )
        normalizer = Normalizer()
        responses: dict[str, Mapping[str, object]] = {}
        for lookup, rung, response in _match_entries(self.name, raw):
            if response["match_confidence"] == _NO_MATCH:
                # An Apollo id is a pseudonymous provider id; every other lookup is
                # personal data, so only its rung is logged.
                named = {"lookup": lookup} if rung == "id" else {}
                _log.info("apollo_no_match", rung=rung, **named)
                continue
            if response.get("person") is None:
                raise NormalizationError(
                    self.name, raw_field_path="person", canonical_path="<unmapped>"
                )
            responses[lookup] = response
        contributions: list[LeadContribution] = []
        for entry in _attach_entries(self.name, raw):
            hit = responses.get(entry.lookup)
            if hit is None:
                raise NormalizationError(
                    self.name, raw_field_path="attach", canonical_path="<unmapped>"
                )
            found = self._attached(entry, hit, normalizer, context)
            if found is not None:
                contributions.append(found)
        return contributions

    def _attached(
        self,
        entry: "_Attachment",
        response: Mapping[str, object],
        normalizer: Normalizer,
        context: NormalizationContext,
    ) -> LeadContribution | None:
        """The answer as a contribution on the requester, or None when it cannot be."""
        person = response["person"]
        theirs = person.get("linkedin_url") if isinstance(person, Mapping) else None
        mine = normalize_linkedin_url(entry.asked.get("linkedin_url"))
        if (
            mine is not None
            and isinstance(theirs, str)
            and normalize_linkedin_url(theirs) not in (None, mine)
        ):
            # Cannot-link: a different LinkedIn profile is a different person.
            _log.info("apollo_match_discarded", rung=entry.rung, reason=_MISMATCH)
            return None
        weak = entry.rung not in _STRONG_RUNGS or entry.anchor == _NAME_ANCHOR
        asked = [rule for key, rule in REQUEST_ECHO_RULES.items() if key in entry.asked]
        echoed = {rule.canonical_path for rule in asked}
        rules = [
            rule
            for rule in self.MATCH_RULES
            if rule.canonical_path not in echoed
            and not (weak and rule.canonical_path in _IDENTITY_PATHS)
        ] + asked
        contribution = normalizer.apply(
            {**response, REQUEST_ECHO_KEY: dict(entry.asked)}, rules, context
        )
        if entry.anchor == _NAME_ANCHOR and not _corroborated(
            self.name, contribution, entry.corroborate
        ):
            _log.info("apollo_match_discarded", rung=entry.rung, reason=_UNCORROBORATED)
            return None
        return _with_rung_confidence(contribution, entry.rung)


def _error_code(body: object) -> str | None:
    """``error_details.code`` when present and identifier-shaped, else ``None``."""
    details = body.get("error_details") if isinstance(body, Mapping) else None
    code = details.get("code") if isinstance(details, Mapping) else None
    if isinstance(code, str) and _CODE_SHAPE.fullmatch(code):
        return code
    return None


def _allowances_in(headers: Mapping[str, str]) -> dict[str, int]:
    found: dict[str, int] = {}
    for header, window in _ALLOWANCE_HEADERS.items():
        raw = headers.get(header, "").strip()
        # Bounded: int() refuses very long digit strings, and no real count needs them.
        if raw.isascii() and raw.isdigit() and len(raw) <= _MAX_ALLOWANCE_DIGITS:
            found[window] = int(raw)
    return found


def credits_in(batch: RawBatch) -> int:
    """Credits an Enrichment batch spent: one per real match, none for ``none`` (12.9).

    ``matches`` holds one entry per distinct lookup, so a cached answer shared by
    several people, or reused by a retried fetch, counts once.
    """
    return sum(
        1
        for _, _, response in _match_entries(batch.source_name, batch)
        if response["match_confidence"] != _NO_MATCH
    )


def _match_entries(
    provider: str, batch: RawBatch
) -> list[tuple[str, str, Mapping[str, object]]]:
    """Each ``(lookup, rung, response)`` of an Enrichment batch, response validated."""
    payload = batch.payload
    entries = payload.get("matches") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        raise NormalizationError(
            provider, raw_field_path="matches", canonical_path="<unmapped>"
        )
    checked: list[tuple[str, str, Mapping[str, object]]] = []
    for entry in entries:
        lookup = entry.get("lookup") if isinstance(entry, Mapping) else None
        rung = entry.get("rung", "id") if isinstance(entry, Mapping) else None
        response = entry.get("response") if isinstance(entry, Mapping) else None
        if (
            not isinstance(lookup, str)
            or not isinstance(rung, str)
            or rung not in RUNG_CONFIDENCE
            or not isinstance(response, Mapping)
        ):
            raise NormalizationError(
                provider, raw_field_path="matches", canonical_path="<unmapped>"
            )
        checked.append(
            (
                lookup,
                rung,
                validate_raw_payload(
                    provider, _Match, response, ApolloSource.MATCH_RULES
                ),
            )
        )
    return checked


# --- the lookup ladder (follow-up, user decision 2026-10-06) -------------------------

_STRONG_RUNGS = frozenset({"id", "linkedin_url", "email"})
# Fields that are Match Keys: a weak answer must not give the person a new one. The
# company domain is one with the name (name + domain, 8.3), so Apollo's own
# ``primary_domain`` is dropped from a weak answer too (ADR-0006).
_IDENTITY_PATHS = frozenset(
    {"person.linkedin_url", "person.email", "person.email_status", "company.domain"}
)
_OWN_ANCHOR = "own"
_NAME_ANCHOR = "name"
_ANCHORS = frozenset({_OWN_ANCHOR, "linkedin_url", "email", _NAME_ANCHOR})
_MISMATCH = "linkedin_mismatch"
_UNCORROBORATED = "uncorroborated"
_MAX_NAME_LENGTH = 100
_MAX_ORGANIZATION_LENGTH = 200
_MAX_ADDRESS_LENGTH = 254
_ADDRESS = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


_ASKED_KEYS = frozenset(REQUEST_ECHO_RULES)  # the requester's values, never Apollo's


@dataclass(frozen=True)
class _Lookup:
    """One ``people/match`` question; ``key`` is the per-run cache key."""

    key: str
    rung: str
    params: Mapping[str, str]


@dataclass(frozen=True)
class _Attachment:
    """How a hit is put on its requester: the rung, the anchor and the echo."""

    lookup: str
    rung: str
    anchor: str
    asked: Mapping[str, str]
    corroborate: frozenset[str]


@dataclass(frozen=True)
class _Asker:
    """One work-list person: the ladder and the identity an answer must carry."""

    ladder: tuple[_Lookup, ...]
    anchor: str
    asked: Mapping[str, str]
    identity: str | None  # the requester's normalised LinkedIn identity
    corroborate: tuple[str, ...]

    def attachment(self, lookup: _Lookup) -> dict[str, Any]:
        return {
            "lookup": lookup.key,
            "rung": lookup.rung,
            "anchor": self.anchor,
            "asked": dict(self.asked),
            "corroborate": list(self.corroborate),
        }


def _attach_entries(provider: str, batch: RawBatch) -> list[_Attachment]:
    payload = batch.payload
    entries = payload.get("attach") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        raise NormalizationError(
            provider, raw_field_path="attach", canonical_path="<unmapped>"
        )
    checked: list[_Attachment] = []
    for entry in entries:
        item = entry if isinstance(entry, Mapping) else {}
        lookup, rung, anchor = item.get("lookup"), item.get("rung"), item.get("anchor")
        asked, corroborate = item.get("asked"), item.get("corroborate")
        if (
            not isinstance(lookup, str)
            or not isinstance(rung, str)
            or rung not in RUNG_CONFIDENCE
            or not isinstance(anchor, str)
            or anchor not in _ANCHORS
            or not isinstance(asked, Mapping)
            or not set(asked) <= _ASKED_KEYS
            or not all(isinstance(v, str) for v in asked.values())
            or not isinstance(corroborate, list)
            or not all(isinstance(v, str) for v in corroborate)
        ):
            raise NormalizationError(
                provider, raw_field_path="attach", canonical_path="<unmapped>"
            )
        checked.append(
            _Attachment(lookup, rung, anchor, dict(asked), frozenset(corroborate))
        )
    return checked


def _corroborated(
    provider: str, contribution: LeadContribution, requester: frozenset[str]
) -> bool:
    """8.3's further gate: Apollo's own title or employer equals the requester's."""
    keys = _keys_of(provider, contribution)
    found = {f"title:{t}" for t in keys.titles} | {
        f"employer:{e}" for e in keys.employers
    }
    return bool(found & requester)


def _with_rung_confidence(
    contribution: LeadContribution, rung: str
) -> LeadContribution:
    """Our certainty of the identity, by rung, on every field Apollo observed."""
    provenance = tuple(
        record
        if record.raw_field_path.startswith(REQUEST_ECHO_PREFIX)
        else FieldProvenance.model_validate(
            {
                **record.model_dump(),
                "confidence_origin": ConfidenceOrigin.HEURISTIC,
                "confidence": RUNG_CONFIDENCE[rung],
            }
        )
        for record in contribution.provenance
    )
    return contribution.model_copy(update={"provenance": provenance})


def _plan(
    provider: str, work_list: tuple[LeadContribution, ...]
) -> tuple[list[_Asker], int]:
    """Who to ask about, and how; plus the count of people no answer could reach.

    Asked once per person (ADR-0006): records naming one address or LinkedIn identity
    share one ladder, strongest rung first, and the answer attaches once, on the
    strongest anchor any of them has (LinkedIn, verified address, name, own record), so
    one answer never lands on two records the merge might not join. Only records with a
    ladder of their own can be the anchor. A group naming two LinkedIn identities, or
    two distinct person names (a shared role address, 8.14), is not one person: each
    record is asked as itself, as before.
    """
    own_ids: dict[str, str] = {}
    for contribution in work_list:
        own = _own_id(provider, contribution)
        identity, _ = _linkedin_of(provider, contribution)
        if own is not None and identity is not None:
            own_ids.setdefault(identity, own)
    askers: list[_Asker] = []
    unattachable = 0
    for group in _people(work_list):
        records = [work_list[index] for index in group]
        ladders = [_ladder(provider, c, own_ids) for c in records]
        linked = {_linkedin_of(provider, c)[0] for c in records} - {None}
        names = {_name_of(provider, c) for c in records} - {None}
        if len(records) > 1 and len(linked) <= 1 and len(names) <= 1:
            # One person: one ladder, one asker on the strongest anchor, one answer.
            merged = _merged(ladders)
            found = [
                asker
                for c, own in zip(records, ladders, strict=True)
                if own and (asker := _asker(provider, c, merged)) is not None
            ]
            if found:
                best = min(found, key=lambda asker: _ANCHOR_RANK[asker.anchor])
                askers.append(replace(best, identity=next(iter(linked), None)))
            elif any(ladders):
                unattachable += 1
            continue
        for contribution, ladder in zip(records, ladders, strict=True):
            if not ladder:
                continue
            asker = _asker(provider, contribution, ladder)
            if asker is None:
                unattachable += 1
            else:
                askers.append(asker)
    return askers, unattachable


# The person's strongest anchor first; a LinkedIn anchor carries the cannot-link check.
_ANCHOR_RANK = {"linkedin_url": 0, "email": 1, "name": 2, "own": 3}
_RUNG_RANK = {rung: rank for rank, rung in enumerate(RUNG_CONFIDENCE)}


def _merged(ladders: list[tuple[_Lookup, ...]]) -> tuple[_Lookup, ...]:
    """The person's ladder: every record's lookups once, strongest rung first."""
    seen: dict[str, _Lookup] = {}
    for ladder in ladders:
        for lookup in ladder:
            seen.setdefault(lookup.key, lookup)
    return tuple(sorted(seen.values(), key=lambda lookup: _RUNG_RANK[lookup.rung]))


def _people(work_list: tuple[LeadContribution, ...]) -> list[list[int]]:
    """Record indices per person: a shared address or LinkedIn identity, transitively.

    The components are the orchestrator's (clustering's union-find), not a copy.
    Groups come in order of their first record; records keep work-list order.
    """
    groups: dict[int, list[int]] = {}
    labels = _components([identities(c) for c in work_list])
    for index, label in enumerate(labels):
        groups.setdefault(label, []).append(index)
    return list(groups.values())


def _linkedin_key(identity: str) -> str:
    return f"linkedin_url:{identity}"


def _email_key(address: str) -> str:
    return f"email:{address}"


def _answer_keys(response: Mapping[str, Any]) -> set[str]:
    """The lookups a hit answers by its own strong keys; none for a no-match.

    The Apollo id, the LinkedIn identity, and the address only when Apollo marks it
    verified (8.11: an unverified address is no Match Key, so it identifies no one).
    """
    person = response.get("person")
    if response.get("match_confidence") == _NO_MATCH or not isinstance(person, Mapping):
        return set()
    keys: set[str] = set()
    own = person.get("id")
    if isinstance(own, str) and own.strip():
        keys.add(own)
    url = person.get("linkedin_url")
    identity = normalize_linkedin_url(url) if isinstance(url, str) else None
    if identity is not None:
        keys.add(_linkedin_key(identity))
    address = person.get("email")
    if isinstance(address, str) and person.get("email_status") == "verified":
        keys.add(_email_key(address.strip().lower()))
    return keys


def _ladder(
    provider: str, contribution: LeadContribution, own_ids: Mapping[str, str]
) -> tuple[_Lookup, ...]:
    """The person's lookups, strongest first: id, LinkedIn, email, name."""
    values = contribution.values
    identity, url = _linkedin_of(provider, contribution)
    known = _own_id(provider, contribution) or (
        own_ids.get(identity) if identity else None
    )
    ladder: list[_Lookup] = []
    if known is not None:
        ladder.append(_Lookup(known, "id", {"id": known}))
    if identity is not None and url is not None:
        ladder.append(
            _Lookup(_linkedin_key(identity), "linkedin_url", {"linkedin_url": url})
        )
    address = _address_of(provider, contribution)
    if address is not None:
        ladder.append(_Lookup(_email_key(address), "email", {"email": address}))
    first = _term(values.get("person.first_name"), _MAX_NAME_LENGTH)
    last = _term(values.get("person.last_name"), _MAX_NAME_LENGTH)
    if first is not None and last is not None:
        named = f"{first.casefold()}|{last.casefold()}"
        domains = _domains_of(provider, contribution)
        organization = _term(values.get("company.name"), _MAX_ORGANIZATION_LENGTH)
        if domains:
            ladder.append(
                _Lookup(
                    f"name_domain:{named}|{domains[0]}",
                    "name_domain",
                    {"first_name": first, "last_name": last, "domain": domains[0]},
                )
            )
        elif organization is not None:
            ladder.append(
                _Lookup(
                    f"name_organization:{named}|{organization.casefold()}",
                    "name_organization",
                    {
                        "first_name": first,
                        "last_name": last,
                        "organization_name": organization,
                    },
                )
            )
    return tuple(ladder)


def _asker(
    provider: str, contribution: LeadContribution, ladder: tuple[_Lookup, ...]
) -> _Asker | None:
    """The anchor an answer joins the requester by: its strongest Match Key.

    None when the requester has no key an answer could share (no LinkedIn, no verified
    address, and no name+domain with a title or employer to corroborate), unless it is
    Apollo's own record, asked by its id as before.
    """
    identity, url = _linkedin_of(provider, contribution)
    keys = _keys_of(provider, contribution)
    if identity is not None and url is not None:
        return _Asker(ladder, "linkedin_url", {"linkedin_url": url}, identity, ())
    for key in keys.keys:
        if key.kind is MatchKeyKind.VERIFIED_EMAIL:
            echo = {"email": key.value, "email_status": EmailStatus.VERIFIED.value}
            return _Asker(ladder, "email", echo, None, ())
    named = [lookup for lookup in ladder if lookup.rung == "name_domain"]
    corroborate = tuple(
        sorted(
            {f"title:{t}" for t in keys.titles}
            | {f"employer:{e}" for e in keys.employers}
        )
    )
    if named and corroborate:
        params = named[0].params
        echo = {k: params[k] for k in ("first_name", "last_name", "domain")}
        return _Asker(ladder, _NAME_ANCHOR, echo, None, corroborate)
    if _own_id(provider, contribution) is not None:
        return _Asker(ladder, _OWN_ANCHOR, {}, None, ())
    return None


def _keys_of(provider: str, contribution: LeadContribution) -> MatchKeys:
    try:
        return extract_match_keys(contribution)
    except TypeError:
        raise NormalizationError(
            provider, raw_field_path="<record>", canonical_path="<unmapped>"
        ) from None


def _name_of(provider: str, contribution: LeadContribution) -> str | None:
    """The person name as 8.14 compares it, or None when the record names no one."""
    try:
        return normalized_person_name(contribution.values)
    except TypeError:
        raise NormalizationError(
            provider, raw_field_path="<record>", canonical_path="<unmapped>"
        ) from None


def _own_id(provider: str, contribution: LeadContribution) -> str | None:
    """This adapter's own Apollo person id on the record, when usable."""
    value = contribution.values.get(_ID_PATH)
    if (
        contribution.source_name == provider
        and isinstance(value, str)
        and value.strip()
    ):
        return value
    return None


def _linkedin_of(
    provider: str, contribution: LeadContribution
) -> tuple[str | None, str | None]:
    """The requester's normalised LinkedIn identity and its own text, or Nones."""
    try:
        identity = linkedin_identity(contribution.values)
    except TypeError:
        raise NormalizationError(
            provider,
            raw_field_path="person.linkedin_url",
            canonical_path="person.linkedin_url",
        ) from None
    if identity is None:
        return None, None
    value = contribution.values["person.linkedin_url"]
    text = value.value if isinstance(value, UntrustedText) else value
    return identity, str(text).strip()


def _address_of(provider: str, contribution: LeadContribution) -> str | None:
    value = contribution.values.get("person.email")
    if value is None:
        return None
    if not isinstance(value, str):
        raise NormalizationError(
            provider, raw_field_path="person.email", canonical_path="person.email"
        )
    address = value.strip().lower()
    if (
        len(address) > _MAX_ADDRESS_LENGTH
        or not _ADDRESS.fullmatch(address)
        or not address.isprintable()
    ):
        return None
    return address


def _term(value: object, limit: int) -> str | None:
    """A name safe and worth asking about, else None; a masked name never is."""
    text = value.value if isinstance(value, UntrustedText) else value
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or len(text) > limit or "*" in text or not text.isprintable():
        return None
    return text


def _domains_of(provider: str, contribution: LeadContribution) -> list[str]:
    try:
        return registrable_domains(contribution.values.get("company.domain"))
    except TypeError:
        raise NormalizationError(
            provider, raw_field_path="company.domain", canonical_path="company.domain"
        ) from None


def _uids_of(vocabulary: Mapping[str, object]) -> tuple[str, ...]:
    """Every technology UID in the vocabulary, first appearance order, no repeats."""
    uids: dict[str, None] = {}
    for term, value in vocabulary.items():
        items = [value] if isinstance(value, str) else value
        if not isinstance(items, list | tuple) or not all(
            isinstance(i, str) and i.strip() for i in items
        ):
            raise ValueError(
                f"apollo vocabulary for term {term!r} must be a UID or a list of UIDs"
            )
        uids.update(dict.fromkeys(items))
    return tuple(uids)


def _supported_technologies() -> frozenset[str]:
    with SUPPORTED_TECHNOLOGIES_SNAPSHOT.open(encoding="utf-8", newline="") as handle:
        return frozenset(row["uid"] for row in csv.DictReader(handle))


def _check_technology_snapshot(provider: str, text: str) -> None:
    """Fixture-guard check of the snapshot: a ``uid`` column, rows, no blank uid.

    Stricter than the loader a run uses (a blank uid there is merely a uid that matches
    nothing), so a damaged snapshot fails the suite without stopping a live run. The
    error is raised outside the ``except`` so no csv text rides along in its context.
    """
    uids: list[str | None] = []
    readable = True
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        uids = [row.get("uid") for row in reader]
        readable = "uid" in (reader.fieldnames or ())
    except csv.Error:
        readable = False
    if not readable or not uids or not all(u and u.strip() for u in uids):
        raise NormalizationError(
            provider, raw_field_path="uid", canonical_path="<unmapped>"
        )


def _require(value: object) -> Mapping[str, object]:
    """A fixture record must be a JSON object; anything else is a schema break."""
    if not isinstance(value, Mapping):
        raise NormalizationError(
            ApolloSource.name, raw_field_path="<record>", canonical_path="<unmapped>"
        )
    return value
