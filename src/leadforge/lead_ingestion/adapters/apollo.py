"""Apollo.io Source adapter: credit-free Discovery search (task 12.1, Requirement 12).

Calls ``POST /api/v1/mixed_people/api_search``, which costs no credits and returns no
email or phone and only an obfuscated last name (12.8). Technographic targeting is a
set of snake_case technology UIDs read from the Target Profile's Apollo vocabulary
(12.12, 12.13); this module holds no UID of its own beyond the declared default every
adapter carries for a term. Enrichment (12.2) and error classification (12.3) are
separate tasks and are not built here, so ``capabilities`` is Discovery only.

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
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, StrictStr

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
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

__all__ = ["MAX_PAGE", "MAX_PER_PAGE", "ApolloSource"]

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


class ApolloSource(BaseLeadSource):
    name: ClassVar[str] = "apollo"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.SEARCH})
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
    # Discovery is free; the one credit-bearing path (12.2) is not built yet.
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_CALL
    yields_suppression: ClassVar[bool] = False
    live_access: ClassVar[LiveAccess] = LiveAccess.GATED
    target_vocabulary: ClassVar[Mapping[str, object]] = {
        "datastax": ["datastax"],
        "apache_cassandra": ["apache_cassandra"],
    }
    endpoints: ClassVar[Mapping[str, Endpoint]] = {"search": _SEARCH}
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
    ) -> None:
        super().__init__(mode, transport=transport)
        if per_page < 1:
            raise ValueError(f"per_page must be at least 1, got {per_page}")
        self._per_page = min(per_page, MAX_PER_PAGE)
        self._environ = environ
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
        response = await self.transport.send(
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

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        payload = raw.payload
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
