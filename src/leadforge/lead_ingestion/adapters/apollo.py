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
  other request runs the Discovery search. Match runs only for work-list leads that
  carry this adapter's own ``person.provider_id`` (sent as ``id``); a lead from
  another source, or without an id, is not called, so no credit is spent on it.
  One call per distinct id.
* Cost declarations: PAID, PER_LEAD, no Suppression yield (match returns no opt-out
  flag). Discovery stays free; the declaration describes the adapter's Enrichment tier.
* The raw batch for Enrichment is ``{"matches": [{"lookup", "response"}]}``, each
  response verbatim, so a no-match outcome stays in the persisted evidence.
  ``credits_in(batch)`` derives the credit count from it (a pure function: there is
  no run-record channel yet). A no-match is also logged as ``apollo_no_match``.
* ``match_confidence`` is a closed set (high, medium, low, none); anything else, or a
  non-none match without a person, is a ``NormalizationError``, never a silent bill.
* ``match_confidence`` is per record, not per field, so every field's Field Confidence
  origin stays ``none``; mapping high/medium/low to numbers would be invention.
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
"""

import csv
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Literal

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
)
from leadforge.lead_ingestion.errors import NormalizationError, SourceError
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.normalizer import (
    FieldRule,
    NormalizationContext,
    Normalizer,
    validate_raw_payload,
)
from leadforge.lead_ingestion.transport import Transport

if TYPE_CHECKING:
    from leadforge.lead_ingestion.pacing import SourcePacing

__all__ = ["MAX_PAGE", "MAX_PER_PAGE", "ApolloSource", "credits_in"]

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

_SEARCH = Endpoint(
    method="POST", path="/api/v1/mixed_people/api_search", bucket="default"
)

_MATCH = Endpoint(method="POST", path="/api/v1/people/match", bucket="default")

_ID_PATH = "person.provider_id"
_NO_MATCH = "none"

_log = structlog.get_logger()


class _Technology(BaseModel):
    uid: StrictStr


class _Organization(BaseModel):
    name: StrictStr | None = None
    current_technologies: list[_Technology] | None = None


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


class ApolloSource(BaseLeadSource):
    name: ClassVar[str] = "apollo"
    capabilities: ClassVar[frozenset[Capability]] = frozenset(
        {Capability.SEARCH, Capability.ENRICH}
    )
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {
        "default": RateBucket(
            name="default",
            windows=(RateWindow(requests=600, per_seconds=3600.0),),
            documented=False,  # plan-dependent figure from the design, not a quote
            doc_url=_DOCS,
        )
    }
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
    docs_url: ClassVar[str] = _DOCS
    base_url: ClassVar[str] = "https://api.apollo.io"

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
        # Answers already paid for this run: a retried fetch must not buy them again.
        self._matched: dict[str, Mapping[str, Any]] = {}
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
        if not 200 <= response.status < 300:
            raise SourceError(self.name, f"search returned status {response.status}")
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
        """One ``people/match`` call per distinct Apollo id on the work list."""
        ids = _apollo_ids(self.name, request.work_list)
        matches: list[Mapping[str, Any]] = []
        if ids:
            headers = self._headers()
            for lookup in ids:
                if lookup not in self._matched:
                    self._matched[lookup] = await self._match(lookup, headers)
                matches.append({"lookup": lookup, "response": self._matched[lookup]})
        return RawBatch(source_name=self.name, payload={"matches": matches})

    async def _match(
        self, lookup: str, headers: Mapping[str, str]
    ) -> Mapping[str, Any]:
        response = await self._send(
            _MATCH, params={"id": lookup}, json_body=None, headers=headers
        )
        if not 200 <= response.status < 300:
            raise SourceError(self.name, f"match returned status {response.status}")
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

    def _normalize_matches(self, raw: RawBatch) -> list[LeadContribution]:
        context = NormalizationContext(
            source_name=self.name,
            data_mode=self.data_mode,
            fetched_at=datetime.now(UTC),
            answerable_surfaces=self.answerable_surfaces,
        )
        normalizer = Normalizer()
        contributions: list[LeadContribution] = []
        for lookup, response in _match_entries(self.name, raw):
            if response["match_confidence"] == _NO_MATCH:
                _log.info("apollo_no_match", lookup=lookup)
                continue
            if response.get("person") is None:
                raise NormalizationError(
                    self.name, raw_field_path="person", canonical_path="<unmapped>"
                )
            contributions.append(normalizer.apply(response, self.MATCH_RULES, context))
        return contributions


def credits_in(batch: RawBatch) -> int:
    """Credits an Enrichment batch spent: one per match, none for ``none`` (12.9)."""
    return sum(
        1
        for _, response in _match_entries(batch.source_name, batch)
        if response["match_confidence"] != _NO_MATCH
    )


def _match_entries(
    provider: str, batch: RawBatch
) -> list[tuple[str, Mapping[str, object]]]:
    """Each ``(lookup, response)`` of an Enrichment batch, the response validated."""
    payload = batch.payload
    entries = payload.get("matches") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        raise NormalizationError(
            provider, raw_field_path="matches", canonical_path="<unmapped>"
        )
    checked: list[tuple[str, Mapping[str, object]]] = []
    for entry in entries:
        lookup = entry.get("lookup") if isinstance(entry, Mapping) else None
        response = entry.get("response") if isinstance(entry, Mapping) else None
        if not isinstance(lookup, str) or not isinstance(response, Mapping):
            raise NormalizationError(
                provider, raw_field_path="matches", canonical_path="<unmapped>"
            )
        checked.append(
            (
                lookup,
                validate_raw_payload(
                    provider, _Match, response, ApolloSource.MATCH_RULES
                ),
            )
        )
    return checked


def _apollo_ids(provider: str, work_list: tuple[LeadContribution, ...]) -> list[str]:
    """Distinct Apollo person ids of the work list's own leads, in work-list order."""
    ids: dict[str, None] = {}
    for contribution in work_list:
        value = contribution.values.get(_ID_PATH)
        if (
            contribution.source_name == provider
            and isinstance(value, str)
            and value.strip()
        ):
            ids[value] = None
    return list(ids)


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
