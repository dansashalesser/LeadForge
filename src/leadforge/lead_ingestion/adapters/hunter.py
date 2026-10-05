"""Hunter.io Source adapter: email discovery batched by domain (task 15.1).

Each Lead on the work list is routed to one Hunter endpoint (16.3): a known address to
``GET /v2/email-verifier``; a first and last name with a domain, and no address, to
``GET /v2/email-finder``; a Lead with only a company domain to
``GET /v2/domain-search``, once per distinct domain. Every answer becomes a Lead
contribution: the address, its deliverability verdict, Hunter's own confidence, and
where it was found (16.2). The adapter declares ``per_company`` so the orchestrator
hands it one Lead per company (11.7), and asks each distinct domain, address or name
pair only once per run. The key travels in the ``X-API-KEY`` header, never in the
query string (16.1).

Not built here: bounded polling of a verifier answer that is still running (HTTP 202) is
task 15.2; the 202 answer is recorded as no verdict. The inverted 403/429 mapping and
the 451 compliance restriction are task 15.3. Until 15.3, the base ``classify_error``
applies, so a Hunter 403 is still read as an authorization failure.

Provisional decisions (see choices.md, task 15.1):

* One page of at most 100 addresses per domain, no paging: each page costs Credits. A
  domain with more addresses logs ``hunter_domain_search_truncated`` with counts only.
* Routing, per Lead: a usable ``person.email`` wins (verifier); else usable
  ``person.first_name`` and ``person.last_name`` with a domain (finder); else every
  domain (domain search). A name is unusable if blank, over 100 characters, containing
  a control character or ``*`` (Apollo obfuscates last names, and a masked name would
  spend a Credit on a guess). A Lead with a name but no domain makes no call.
* The raw batch is ``{"searches": [{"domain", "response"}], "finds": [{"domain",
  "first_name", "last_name", "response"}], "verifications": [{"email", "response"}],
  "credits_billable"}``, each response verbatim (``None`` for a verifier 202).
  ``credits_in(batch)`` counts one Credit per live call of a real key and zero for the
  sandbox key or synthetic mode (16.8). Hunter's actual per-call billing was not
  verified; one per call is an assumption, not a quote.
* The sandbox is live mode whose key equals ``test-api-key``; there is no third data
  mode. The batch records it as not billable. The key value is compared only to price
  the batch (16.8 names the key); the data mode never comes from the credential.
* Hunter's per-address ``confidence`` (domain search) or ``score`` (finder, verifier),
  an integer 0-100, is a provider-stated Field Confidence on ``person.email`` only:
  normalized value ``n / 100``, verbatim raw string, scale
  ``hunter_confidence_0_100``. The ``Normalizer`` emits origin ``none``, so the adapter
  replaces that one provenance record. Absent confidence stays ``none``.
* ``verification.status`` maps: valid -> verified, accept_all -> accept_all, invalid ->
  invalid, unknown/webmail/disposable -> unknown. A missing verification or status
  contributes no ``person.email_status`` (the canonical default is unknown), so a guess
  Hunter never verified is never marked verified. Any other status is a
  ``NormalizationError``.
* Names, title and organization are ``UntrustedText``; the address, domain and source
  URIs are not. Source URIs are contributed as a tuple at ``person.email_sources``.
* A domain that is not hostname-shaped is skipped with ``hunter_domain_skipped`` (no
  value logged); a non-text ``company.domain`` is a ``NormalizationError``.
* Domain-search provenance raw paths are relative to a per-address wrapper: the
  response's ``data`` minus ``emails``, plus ``email`` (the address record). Finder and
  verifier paths are relative to the response's ``data``. A finder that finds no
  address, and a verifier still running, contribute nothing.
* The fixtures under ``fixtures/hunter/`` are hand-made STAND-INS, not captured
  responses; the verifier's ``data`` shape in particular is assumed.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any, ClassVar

import structlog
from pydantic import BaseModel, Field, StrictStr

from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Capability,
    ChargeUnit,
    CostClass,
    Endpoint,
    EnrichmentRequest,
    LeadContribution,
    RateBucket,
    RateWindow,
    RawBatch,
    SourceRequest,
    resolve_credentials,
)
from leadforge.lead_ingestion.errors import NormalizationError, SourceError
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
    UntrustedText,
)
from leadforge.lead_ingestion.normalizer import (
    FieldRule,
    NormalizationContext,
    Normalizer,
    validate_raw_payload,
)
from leadforge.lead_ingestion.transport import Transport, TransportResponse

if TYPE_CHECKING:
    from leadforge.lead_ingestion.pacing import SourcePacing

__all__ = [
    "CONFIDENCE_SCALE",
    "MAX_EMAILS_PER_DOMAIN",
    "SANDBOX_KEY",
    "HunterSource",
    "credits_in",
]

MAX_EMAILS_PER_DOMAIN = 100  # Hunter's documented ceiling for one domain-search page
SANDBOX_KEY = "test-api-key"  # Hunter's published key: dummy responses, no Credits
CONFIDENCE_SCALE = "hunter_confidence_0_100"

_KEY_HEADER = "X-API-KEY"
_KEY_ENV = "HUNTER_API_KEY"
_DOCS = "https://hunter.io/api-documentation/v2"
_EMAIL_PATH = "person.email"

_SEARCH = Endpoint(method="GET", path="/v2/domain-search", bucket="finder")
_FINDER = Endpoint(method="GET", path="/v2/email-finder", bucket="finder")
_VERIFIER = Endpoint(method="GET", path="/v2/email-verifier", bucket="verifier")
_ACCEPTED = 202  # the verifier is still running; polling it is task 15.2

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")
_MAX_DOMAIN_LENGTH = 253
_MAX_NAME_LENGTH = 100
_MAX_ADDRESS_LENGTH = 254
_ADDRESS = re.compile(r"[^\s@?#/\\:]+@" + _HOSTNAME.pattern)
_Score = Annotated[int, Field(strict=True, ge=0, le=100)]

_STATUS_MAP: Mapping[str, EmailStatus] = {
    "valid": EmailStatus.VERIFIED,
    "accept_all": EmailStatus.ACCEPT_ALL,
    "invalid": EmailStatus.INVALID,
    "unknown": EmailStatus.UNKNOWN,
    "webmail": EmailStatus.UNKNOWN,
    "disposable": EmailStatus.UNKNOWN,
}

_log = structlog.get_logger()


class _Source(BaseModel):
    uri: StrictStr


class _Verification(BaseModel):
    status: StrictStr | None = None


class _Email(BaseModel):
    value: StrictStr
    confidence: _Score | None = None
    first_name: StrictStr | None = None
    last_name: StrictStr | None = None
    position: StrictStr | None = None
    sources: list[_Source] | None = None
    verification: _Verification | None = None


class _Data(BaseModel):
    domain: StrictStr
    organization: StrictStr | None = None
    emails: list[_Email]


class _Response(BaseModel):
    data: _Data


class _Found(BaseModel):
    email: StrictStr | None = None
    score: _Score | None = None
    first_name: StrictStr | None = None
    last_name: StrictStr | None = None
    position: StrictStr | None = None
    company: StrictStr | None = None
    sources: list[_Source] | None = None
    verification: _Verification | None = None


class _FoundResponse(BaseModel):
    data: _Found


class _Verdict(BaseModel):
    email: StrictStr
    status: StrictStr
    score: _Score | None = None


class _VerdictResponse(BaseModel):
    data: _Verdict


def _email_status(value: object) -> object:
    return _STATUS_MAP[str(value)]


def _source_uris(value: object) -> object:
    if not isinstance(value, list):
        raise TypeError("sources must be a list")
    uris = tuple(source["uri"] for source in value)
    return uris or None


class HunterSource(BaseLeadSource):
    name: ClassVar[str] = "hunter"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.ENRICH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {
        "finder": RateBucket(
            name="finder",
            windows=(
                RateWindow(requests=15, per_seconds=1.0),
                RateWindow(requests=500, per_seconds=60.0),
            ),
            documented=True,
            doc_url=_DOCS,
        ),
        "verifier": RateBucket(
            name="verifier",
            windows=(
                RateWindow(requests=10, per_seconds=1.0),
                RateWindow(requests=300, per_seconds=60.0),
            ),
            documented=True,
            doc_url=_DOCS,
        ),
    }
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {
        _EMAIL_PATH: frozenset({"email.value"}),
        "person.email_status": frozenset({"email.verification.status"}),
        "person.first_name": frozenset({"email.first_name"}),
        "person.last_name": frozenset({"email.last_name"}),
        "person.title": frozenset({"email.position"}),
    }
    cost_class: ClassVar[CostClass] = CostClass.PAID
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_COMPANY
    yields_suppression: ClassVar[bool] = False
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {
        "domain_search": _SEARCH,
        "email_finder": _FINDER,
        "email_verifier": _VERIFIER,
    }
    required_env: ClassVar[tuple[str, ...]] = (_KEY_ENV,)
    docs_url: ClassVar[str] = _DOCS
    base_url: ClassVar[str] = "https://api.hunter.io"

    RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule("company.domain", "domain"),
        FieldRule("company.name", "organization", untrusted=True),
        FieldRule(_EMAIL_PATH, "email.value"),
        FieldRule("person.first_name", "email.first_name", untrusted=True),
        FieldRule("person.last_name", "email.last_name", untrusted=True),
        FieldRule("person.title", "email.position", untrusted=True),
        FieldRule(
            "person.email_status", "email.verification.status", transform=_email_status
        ),
        FieldRule("person.email_sources", "email.sources", transform=_source_uris),
    )
    FINDER_RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule("company.name", "company", untrusted=True),
        FieldRule(_EMAIL_PATH, "email"),
        FieldRule("person.first_name", "first_name", untrusted=True),
        FieldRule("person.last_name", "last_name", untrusted=True),
        FieldRule("person.title", "position", untrusted=True),
        FieldRule(
            "person.email_status", "verification.status", transform=_email_status
        ),
        FieldRule("person.email_sources", "sources", transform=_source_uris),
    )
    FINDER_IGNORED: ClassVar[frozenset[str]] = frozenset(
        {
            "score",  # read by the adapter into the email's provenance
            "domain",
            "accept_all",
            "twitter",
            "linkedin_url",
            "phone_number",
            "verification.date",
        }
    )
    VERIFIER_RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule(_EMAIL_PATH, "email"),
        FieldRule("person.email_status", "status", transform=_email_status),
    )
    VERIFIER_IGNORED: ClassVar[frozenset[str]] = frozenset({"score"})
    # Domain-level flags and fields of the address record that 16.2 does not ask for.
    IGNORED: ClassVar[frozenset[str]] = frozenset(
        {
            "disposable",
            "webmail",
            "accept_all",
            "pattern",
            "email.type",
            "email.confidence",  # read by the adapter into the email's provenance
            "email.seniority",
            "email.department",
            "email.linkedin",
            "email.twitter",
            "email.phone_number",
            "email.verification.date",
        }
    )

    def __init__(
        self,
        mode: DataMode,
        *,
        transport: Transport,
        environ: Mapping[str, str] | None = None,
        pacing: "SourcePacing | None" = None,
    ) -> None:
        super().__init__(mode, transport=transport, pacing=pacing)
        self._environ = environ
        # Calls already paid for this run: a retried fetch must not buy them again.
        self._searched: dict[str, Mapping[str, Any]] = {}
        self._found: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        self._verified: dict[str, Mapping[str, Any] | None] = {}

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        if not isinstance(request, EnrichmentRequest):
            raise SourceError(
                self.name, "hunter answers an enrichment request only, not discovery"
            )
        plan = _route(self.name, request.work_list)
        headers: Mapping[str, str] = {}
        billable = False
        asks = plan.domains or plan.names or plan.addresses
        if asks and self.data_mode is DataMode.LIVE:
            key = resolve_credentials(self, self._environ)[_KEY_ENV]
            headers = {_KEY_HEADER: key}
            billable = key != SANDBOX_KEY
        searches: list[Mapping[str, Any]] = []
        for domain in plan.domains:
            if domain not in self._searched:
                self._searched[domain] = await self._search(domain, headers)
            searches.append({"domain": domain, "response": self._searched[domain]})
        finds: list[Mapping[str, Any]] = []
        for name in plan.names:
            domain, first, last = name
            asked = (domain, first.casefold(), last.casefold())
            if asked not in self._found:
                self._found[asked] = await self._find(domain, first, last, headers)
            finds.append(
                {
                    "domain": domain,
                    "first_name": first,
                    "last_name": last,
                    "response": self._found[asked],
                }
            )
        verifications: list[Mapping[str, Any]] = []
        for address in plan.addresses:
            if address not in self._verified:
                self._verified[address] = await self._verify(address, headers)
            verifications.append(
                {"email": address, "response": self._verified[address]}
            )
        return RawBatch(
            source_name=self.name,
            payload={
                "searches": searches,
                "finds": finds,
                "verifications": verifications,
                "credits_billable": billable,
            },
        )

    def _checked(self, response: TransportResponse) -> Mapping[str, Any]:
        """The body of an answer, which must be an object with an object ``data``."""
        body = response.body
        if not isinstance(body, Mapping) or not isinstance(body.get("data"), Mapping):
            raise NormalizationError(
                self.name, raw_field_path="data", canonical_path="<unmapped>"
            )
        return body

    async def _search(
        self, domain: str, headers: Mapping[str, str]
    ) -> Mapping[str, Any]:
        params = {"domain": domain, "limit": MAX_EMAILS_PER_DOMAIN}
        response = await self._send(
            _SEARCH, params=params, json_body=None, headers=headers
        )
        body = self._checked(response)
        total = _total_of(body)
        listed = body["data"].get("emails")
        if total is not None and isinstance(listed, list) and total > len(listed):
            _log.warning(
                "hunter_domain_search_truncated", returned=len(listed), total=total
            )
        return body

    async def _find(
        self, domain: str, first: str, last: str, headers: Mapping[str, str]
    ) -> Mapping[str, Any]:
        params = {"domain": domain, "first_name": first, "last_name": last}
        response = await self._send(
            _FINDER, params=params, json_body=None, headers=headers
        )
        return self._checked(response)

    async def _verify(
        self, address: str, headers: Mapping[str, str]
    ) -> Mapping[str, Any] | None:
        response = await self._send(
            _VERIFIER, params={"email": address}, json_body=None, headers=headers
        )
        if response.status == _ACCEPTED:
            _log.info("hunter_verification_pending")
            return None
        return self._checked(response)

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        context = NormalizationContext(
            source_name=self.name,
            data_mode=self.data_mode,
            fetched_at=datetime.now(UTC),
            answerable_surfaces=self.answerable_surfaces,
        )
        normalizer = Normalizer()
        contributions: list[LeadContribution] = []
        for response in _responses(self.name, raw, "searches", required=True):
            assert response is not None
            validate_raw_payload(self.name, _Response, response, self.RULES)
            data = response["data"]
            shared = {k: v for k, v in data.items() if k != "emails"}
            for item in data["emails"]:
                contribution = normalizer.apply(
                    {**shared, "email": item}, self.RULES, context
                )
                contributions.append(
                    _with_stated_confidence(contribution, item.get("confidence"))
                )
        for response in _responses(self.name, raw, "finds"):
            assert response is not None
            validate_raw_payload(self.name, _FoundResponse, response, self.FINDER_RULES)
            data = response["data"]
            if data.get("email") is None:
                continue  # nothing found: no contribution, and no field question asked
            contribution = normalizer.apply(data, self.FINDER_RULES, context)
            contributions.append(
                _with_stated_confidence(contribution, data.get("score"))
            )
        for response in _responses(self.name, raw, "verifications"):
            if response is None:
                continue  # still running: no verdict yet
            validate_raw_payload(
                self.name, _VerdictResponse, response, self.VERIFIER_RULES
            )
            data = response["data"]
            contribution = normalizer.apply(data, self.VERIFIER_RULES, context)
            contributions.append(
                _with_stated_confidence(contribution, data.get("score"))
            )
        return contributions


def _with_stated_confidence(
    contribution: LeadContribution, stated: int | None
) -> LeadContribution:
    """Record Hunter's own confidence on the address's provenance, verbatim."""
    if stated is None:
        return contribution
    provenance = tuple(
        FieldProvenance.model_validate(
            {
                **record.model_dump(),
                "confidence_origin": ConfidenceOrigin.PROVIDER_STATED,
                "confidence": stated / 100,
                "confidence_raw": str(stated),
                "confidence_scale": CONFIDENCE_SCALE,
            }
        )
        if record.canonical_path == _EMAIL_PATH
        else record
        for record in contribution.provenance
    )
    return contribution.model_copy(update={"provenance": provenance})


def _total_of(body: Mapping[str, Any]) -> int | None:
    meta = body.get("meta")
    total = meta.get("results") if isinstance(meta, Mapping) else None
    return total if isinstance(total, int) and not isinstance(total, bool) else None


def credits_in(batch: RawBatch) -> int:
    """Credits spent: one per live call of a real key, none otherwise (16.8)."""
    payload = batch.payload
    billable = payload.get("credits_billable") if isinstance(payload, Mapping) else None
    if not isinstance(billable, bool):
        raise NormalizationError(
            batch.source_name,
            raw_field_path="credits_billable",
            canonical_path="<unmapped>",
        )
    if not billable:
        return 0
    return sum(
        len(_responses(batch.source_name, batch, key, required=key == "searches"))
        for key in ("searches", "finds", "verifications")
    )


def _responses(
    provider: str, batch: RawBatch, key: str, *, required: bool = False
) -> list[Mapping[str, Any] | None]:
    """The ``response`` of each call of one kind in a batch, the envelope checked."""
    payload = batch.payload
    entries = payload.get(key) if isinstance(payload, Mapping) else None
    if entries is None and not required and isinstance(payload, Mapping):
        return []
    if not isinstance(entries, list):
        raise NormalizationError(
            provider, raw_field_path=key, canonical_path="<unmapped>"
        )
    checked: list[Mapping[str, Any] | None] = []
    for entry in entries:
        response = entry.get("response") if isinstance(entry, Mapping) else None
        # Only a verification can have no answer yet (HTTP 202).
        if not (
            isinstance(response, Mapping)
            or (
                response is None
                and key == "verifications"
                and isinstance(entry, Mapping)
                and "response" in entry
            )
        ):
            raise NormalizationError(
                provider, raw_field_path=key, canonical_path="<unmapped>"
            )
        checked.append(response)
    return checked


@dataclass(frozen=True)
class _Plan:
    """What one work list asks Hunter, each question once, in work-list order."""

    domains: list[str]
    names: list[tuple[str, str, str]]
    addresses: list[str]


def _route(provider: str, work_list: tuple[LeadContribution, ...]) -> _Plan:
    """Send each Lead to the cheapest endpoint that answers it (16.3)."""
    domains: dict[str, None] = {}
    names: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    addresses: dict[str, None] = {}
    for contribution in work_list:
        company = _domains_of(provider, contribution)
        address = _address_of(provider, contribution)
        if address is not None:
            addresses[address] = None
            continue
        first = _name_of(contribution.values.get("person.first_name"))
        last = _name_of(contribution.values.get("person.last_name"))
        if first is not None and last is not None:
            if company:
                folded = (company[0], first.casefold(), last.casefold())
                names.setdefault(folded, (company[0], first, last))
            continue  # a person we cannot ask about is not a company-level question
        domains.update(dict.fromkeys(company))
    return _Plan(list(domains), list(names.values()), list(addresses))


def _address_of(provider: str, contribution: LeadContribution) -> str | None:
    value = contribution.values.get("person.email")
    if value is None:
        return None
    if not isinstance(value, str):
        raise NormalizationError(
            provider, raw_field_path="person.email", canonical_path="person.email"
        )
    address = value.strip().casefold()
    if (
        not address
        or len(address) > _MAX_ADDRESS_LENGTH
        or not _ADDRESS.fullmatch(address)
        or not address.isprintable()
    ):
        if address:
            _log.warning("hunter_address_skipped", reason="not_address_shaped")
        return None
    return address


def _name_of(value: object) -> str | None:
    """A name safe and worth asking about, else None."""
    text = value.value if isinstance(value, UntrustedText) else value
    if not isinstance(text, str):
        return None
    text = text.strip()
    if (
        not text
        or len(text) > _MAX_NAME_LENGTH
        or "*" in text  # a masked name is a guess Hunter would charge for
        or not text.isprintable()
    ):
        return None
    return text


def _domains_of(provider: str, contribution: LeadContribution) -> list[str]:
    """Distinct searchable domains of one Lead, in order."""
    value = contribution.values.get("company.domain")
    if value is None:
        return []
    items = [value] if isinstance(value, str) else value
    if not isinstance(items, tuple | list | set | frozenset) or not all(
        isinstance(i, str) for i in items
    ):
        raise NormalizationError(
            provider, raw_field_path="company.domain", canonical_path="company.domain"
        )
    found: dict[str, None] = {}
    for item in items:
        domain = item.strip().casefold()
        if not domain:
            continue
        if len(domain) > _MAX_DOMAIN_LENGTH or not _HOSTNAME.fullmatch(domain):
            _log.warning("hunter_domain_skipped", reason="not_hostname_shaped")
            continue
        found[domain] = None
    return list(found)
