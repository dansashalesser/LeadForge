"""HubSpot Source adapter: the free CRM-state lookup for Enrichment (13.1).

For each lead on the work list that has an email, searches HubSpot contacts by that
email and, for every contact found, searches deals for an unclosed one. It contributes
CRM state (contact exists, lifecycle stage, owner, last activity date, open deal) and
the canonical compliance flags (13.2, 13.7). Both searches are POSTs because HubSpot
takes its query in a body; they read and write nothing in HubSpot, so they are
read-only ``Endpoint`` declarations. Client-side throttling with policy-aware backoff
is task 13.2: the ``search`` bucket declared here is what the base class paces every
search on, and ``classify_error`` reads HubSpot's ``policyName`` on a 429.
The transport is REST only: there is no MCP connection and no interactive
authorization step (13.8).

Provisional decisions (see choices.md, task 13.1):

* The API version is configuration, read from ``HUBSPOT_API_VERSION`` with the token
  when a live fetch starts, and placed in the path through the ``{version}``
  placeholder. There is no code default: an unset version fails, as a missing token
  does. It must be ``YYYY-MM``, which also rules out the legacy ``v3`` path. Synthetic
  mode needs neither.
* Canonical paths for CRM state are ``crm.contact_exists``, ``crm.lifecycle_stage``,
  ``crm.owner``, ``crm.last_activity_date`` and ``crm.has_open_deal``; the spec names
  the signals but no path. The compliance flags use the canonical ``opt_out`` and
  ``suppressed`` paths.
* HubSpot's one marketing opt-out property sets BOTH ``opt_out`` and ``suppressed``,
  since the spec asks for "the compliance flags" and names no second property. A
  value is read by ``compliance.is_flag_set`` (fails closed, task 19.3), not refused:
  refusing would drop the whole contact's signal.
* Only the lead's ``email`` is looked up (no LinkedIn or name lookup), for any work-list
  lead whatever its source. One contribution is made per contact found, so a duplicate
  contact flagged as opted out still flags the lead (the orchestrator matches by
  email). An email with no contact yields one contribution of Negative Evidence.
* Open deal presence is a second search, deals associated with the contact and not
  closed, read from ``total``. It is made once per contact found.
* Lookups are cached for the life of the source, so a retried fetch repeats no call.
  An address whose answer a returned batch already carried is not emitted again this
  run (follow-up fu2, 2026-10-06): the free second pass re-hands a record whose
  address was asked when it brings a new LinkedIn URL, and HubSpot asks by address
  only, so a repeat would be a duplicate record of the same contact. A fetch that
  fails returns no batch, so its addresses are still emitted by the retry. GAP: the
  echo is the first emission's; a LinkedIn URL learned later does not upgrade it.
* Lifecycle stage and owner are identifiers, not free text, so they are not
  ``UntrustedText``. No free-text property is requested.
* Property names (``notes_last_updated`` for last activity, ``hs_email_optout``,
  ``hs_is_closed``) and the ``associations.contact`` filter come from HubSpot's
  documented defaults and were not checked against a live portal.
* ``fixtures/hubspot/*.json`` are hand-made STAND-INS, not captured responses.
* At most 100 contacts are read per email; there is no paging.

Request echo (follow-up, user decision 2026-10-06, option B): a HubSpot contact
joins the person it was asked about, with no change to the merge rules.

* Each contribution answering a lookup carries the requester's identity at ``asked.*``
  (``REQUEST_ECHO_PREFIX``), as Hunter and Apollo do: the requester's LinkedIn URL
  (its own text) and, when a requester record holds the asked address as ``verified``,
  that address with ``email_status`` verified. Those are the Match Keys that put the
  contribution on that person's Lead; an echo never corroborates
  (``conflicts._observed_first``) and keeps origin ``none``. No name or domain is
  echoed: HubSpot is asked by address only, so neither was asked. A requester with no
  LinkedIn URL and no verified address gets no echo (nothing could join it).
* The echo is decided when asking and kept in the raw batch (``lookups[].asked``), so a
  replayed batch normalises the same way; a batch without it has no echo.
* Not echoed (``hubspot_echo_withheld``, a reason and a count, never a value): one ask
  made for two distinguishable people (two LinkedIn identities or two person names
  across this run's asks, ``ambiguous_requester``); several contacts for one ask
  (``multiple_contacts``); a contact whose own ``email`` property is another address
  (``email_mismatch``) or absent (``email_unconfirmed``). Such records still carry
  HubSpot's own fields, flags included, as before. The contact search therefore also
  requests the ``email`` property.
* An address unknown to HubSpot gets the echo too, but its record drops ``email``: that
  value is the question itself (raw path ``lookup``), not an address HubSpot holds, so
  it must not count as agreement on the requester's address. The echo joins the
  "not in the CRM" Negative Evidence to the person asked (follow-up, user decision
  2026-10-07: without it every lead missing from the CRM got an email-only twin).
  With no echo the record keeps ``email`` and stays a record of its own. A confirmed
  contact's ``email`` equals the contact's own address, so it is HubSpot's observation
  and does count.

Provider facts checked on 2026-10-06 (HubSpot's public OpenAPI specs,
https://github.com/HubSpot/HubSpot-public-api-spec-collection, latest date-versioned
rollout, and the official ``@hubspot/api-client`` 14.0.1; the developer docs were
network-blocked):

* Required private-app scopes: ``crm.objects.contacts.read`` for the contact search and
  ``crm.objects.deals.read`` for the deal search (each spec's ``security`` block).
  Without the deals scope the deal search answers 403. Both are named next to
  ``HUBSPOT_ACCESS_TOKEN`` in ``.env.example`` (``env_notes``).
* Verified: the search paths and host, the ``YYYY-MM`` version, the request body
  (``filterGroups``/``filters``/``EQ``), the response envelope (``total``, ``results[]``
  with ``id``, ``properties`` as nullable strings, ``createdAt``, ``updatedAt``,
  ``archived``), Bearer auth, the five-per-second search limit (per account, shared
  with other integrations), and the ``DAILY`` and ``TEN_SECONDLY_ROLLING`` policy names.
* Re-checked on the live developer docs, 2026-10-06 (spec repo HEAD 5892c4d):
  https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm shows a
  body of ``filterGroups`` alone (the spec's "required" ``after``/``sorts`` are not
  needed), "rate limited to five requests per second per account", "maximum number
  of supported objects per page is 200", and the ``associations.{objectType}``
  pseudo-property. https://developers.hubspot.com/docs/developer-tooling/platform/
  usage-guidelines: a 429 carries ``"errorType": "RATE_LIMIT"`` and ``policyName``
  ("will indicate which limit you hit (either daily or secondly)"), and "Responses
  from the search API endpoints will not include any of the rate limit headers".
* UNVERIFIED: the ``hs_is_closed`` deal property. HubSpot's default deal property list
  (https://knowledge.hubspot.com/properties/hubspots-default-deal-properties) names
  only "Is closed lost" and "Is Closed Won"; ``hs_is_closed`` appears only in
  community answers as an internal calculated property usable in search filters.
  Confirm with one deal search on a live portal. The literal ``SECONDLY`` is also
  unconfirmed (the docs say "secondly" in prose and name ``TEN_SECONDLY_ROLLING``).
  Behaviour does not depend on it: any 429 is still ``SourceRateLimited``.
"""

import re
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

import structlog
from pydantic import BaseModel, StrictInt, StrictStr

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
    retry_after_seconds,
)
from leadforge.lead_ingestion.compliance import is_flag_set
from leadforge.lead_ingestion.errors import (
    NormalizationError,
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
)
from leadforge.lead_ingestion.match_keys import (
    linkedin_identity,
    normalize_email,
    normalized_person_name,
)
from leadforge.lead_ingestion.models import (
    DataMode,
    EmailStatus,
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
from leadforge.lead_ingestion.transport import Transport, TransportResponse

if TYPE_CHECKING:
    from leadforge.lead_ingestion.pacing import SourcePacing

__all__ = ["MAX_CONTACTS_PER_LOOKUP", "HubSpotSource"]

MAX_CONTACTS_PER_LOOKUP = 100

_TOKEN_ENV = "HUBSPOT_ACCESS_TOKEN"
_VERSION_ENV = "HUBSPOT_API_VERSION"
_VERSION_SHAPE = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
_SEARCH_SPACING_S = 0.2
_DOCS = "https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm"

_CONTACT_SEARCH = Endpoint(
    method="POST", path="/crm/objects/{version}/contacts/search", bucket="search"
)
_DEAL_SEARCH = Endpoint(
    method="POST", path="/crm/objects/{version}/deals/search", bucket="search"
)

_DAILY_POLICIES = frozenset({"DAILY"})
# TEN_SECONDLY_ROLLING is documented; SECONDLY is UNVERIFIED (see module doc).
_SHORT_POLICIES = frozenset({"SECONDLY", "TEN_SECONDLY_ROLLING"})

_log = structlog.get_logger()

_CONTACT_PROPERTIES = (
    "email",  # the contact's own address: an echo needs it to equal the one asked
    "lifecyclestage",
    "hubspot_owner_id",
    "notes_last_updated",
    "hs_email_optout",
)
_OPT_OUT_RAW = "contact.properties.hs_email_optout"
_LOOKUP = "lookup"


class _Properties(BaseModel):
    email: StrictStr | None = None
    lifecyclestage: StrictStr | None = None
    hubspot_owner_id: StrictStr | None = None
    notes_last_updated: StrictStr | None = None
    hs_email_optout: StrictStr | None = None


class _Contact(BaseModel):
    id: StrictStr
    properties: _Properties


class _Record(BaseModel):
    lookup: StrictStr
    contact: _Contact | None = None
    open_deals_total: StrictInt | None = None
    asked: dict[str, StrictStr] | None = None


def _policy_of(body: object) -> str:
    """The upper-cased top-level ``policyName``, or ``""`` when absent or unreadable."""
    name = body.get("policyName") if isinstance(body, Mapping) else None
    return name.strip().upper() if isinstance(name, str) else ""


def _text(value: object) -> object:
    """A blank string is no value; anything else is kept as sent."""
    return None if isinstance(value, str) and not value.strip() else value


def _flag(value: object) -> object:
    value = _text(value)
    if value is None:
        return None
    # Only ever feeds the compliance flags (11.4): the one reading of a flag, so an
    # unreadable value is still an opt-out rather than a dropped signal.
    return is_flag_set(value)


def _when(value: object) -> object:
    value = _text(value)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("not a timestamp")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp without an offset")
    return parsed.astimezone(UTC)


def _exists(_: object) -> object:
    return True


def _any_open(value: object) -> object:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("not a count")
    return value > 0


class HubSpotSource(BaseLeadSource):
    name: ClassVar[str] = "hubspot"
    capabilities: ClassVar[frozenset[Capability]] = frozenset({Capability.ENRICH})
    rate_limit: ClassVar[Mapping[str, RateBucket]] = {
        "search": RateBucket(
            name="search",
            # The documented five per second, plus an even spacing of one per 0.2 s:
            # a bare token bucket would admit five at once and one more 0.2 s later,
            # six inside one second, which 13.4 forbids.
            windows=(
                RateWindow(requests=5, per_seconds=1.0),
                RateWindow(requests=1, per_seconds=_SEARCH_SPACING_S),
            ),
            documented=True,
            doc_url=_DOCS,
        )
    }
    answerable_surfaces: ClassVar[Mapping[str, frozenset[str]]] = {
        "crm.contact_exists": frozenset({"contact.id"}),
        "crm.lifecycle_stage": frozenset({"contact.properties.lifecyclestage"}),
        "crm.owner": frozenset({"contact.properties.hubspot_owner_id"}),
        "crm.last_activity_date": frozenset({"contact.properties.notes_last_updated"}),
        "crm.has_open_deal": frozenset({"open_deals_total"}),
        "opt_out": frozenset({_OPT_OUT_RAW}),
        "suppressed": frozenset({_OPT_OUT_RAW}),
    }
    # Both searches are free; the suppression flag lets the orchestrator prune the work
    # list before any credit-bearing source runs (ADR-0002).
    cost_class: ClassVar[CostClass] = CostClass.FREE
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_LEAD
    yields_suppression: ClassVar[bool] = True
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {
        "contact_search": _CONTACT_SEARCH,
        "deal_search": _DEAL_SEARCH,
    }
    required_env: ClassVar[tuple[str, ...]] = (_TOKEN_ENV, _VERSION_ENV)
    env_notes: ClassVar[Mapping[str, str]] = {
        _TOKEN_ENV: (
            "private app access token with scopes crm.objects.contacts.read and "
            "crm.objects.deals.read"
        )
    }
    docs_url: ClassVar[str] = _DOCS
    base_url: ClassVar[str] = "https://api.hubapi.com"

    RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule("email", _LOOKUP),
        FieldRule("crm.contact_exists", "contact.id", transform=_exists),
        FieldRule(
            "crm.lifecycle_stage", "contact.properties.lifecyclestage", transform=_text
        ),
        FieldRule("crm.owner", "contact.properties.hubspot_owner_id", transform=_text),
        FieldRule(
            "crm.last_activity_date",
            "contact.properties.notes_last_updated",
            transform=_when,
        ),
        FieldRule("crm.has_open_deal", "open_deals_total", transform=_any_open),
        FieldRule("opt_out", _OPT_OUT_RAW, transform=_flag),
        FieldRule("suppressed", _OPT_OUT_RAW, transform=_flag),
    )
    # HubSpot always returns these with a record; none is a CRM-state signal we use.
    IGNORED: ClassVar[frozenset[str]] = frozenset(
        {
            "contact.createdAt",
            "contact.updatedAt",
            "contact.archived",
            "contact.properties.createdate",
            "contact.properties.email",
            "contact.properties.hs_object_id",
            "contact.properties.lastmodifieddate",
        }
    )

    # Search envelope fields no rule reads: the contact search takes its answer from
    # ``results``, the deal search from ``total`` alone (a limit of one, so its single
    # result is never read).
    CONTACT_SEARCH_IGNORED: ClassVar[frozenset[str]] = frozenset({"total"})
    DEAL_SEARCH_IGNORED: ClassVar[frozenset[str]] = frozenset({"results"})

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
        # Lookups already made this run: a retried fetch must not repeat them.
        self._found: dict[str, list[Mapping[str, Any]]] = {}
        # Who each address was asked for this run: LinkedIn identities and names.
        self._asked_for: dict[str, tuple[set[str], set[str]]] = {}
        # Addresses a returned batch already answered: emitted once per run.
        self._emitted: set[str] = set()

    def _call_context(self) -> tuple[Mapping[str, str], Mapping[str, object]]:
        """Request headers and path parameters; synthetic mode needs neither."""
        if self.data_mode is DataMode.SYNTHETIC:
            return {}, {}
        credentials = resolve_credentials(self, self._environ)
        version = credentials[_VERSION_ENV].strip()
        if not _VERSION_SHAPE.fullmatch(version):
            # Never echo the value: it is only ever a name and a rule here.
            raise SourceError(
                self.name, f"{_VERSION_ENV} must be a YYYY-MM date version"
            )
        return (
            {"authorization": f"Bearer {credentials[_TOKEN_ENV]}"},
            {"version": version},
        )

    def classify_error(
        self, response: TransportResponse, *, endpoint: Endpoint
    ) -> SourceError | None:
        """HubSpot's reading of the conventional mapping, policy-aware on a 429 (13.6).

        A 429 whose top-level ``policyName`` is ``DAILY`` is ``SourceQuotaExhausted``:
        the day's allowance is spent and a retry cannot fix it. ``SECONDLY`` and
        ``TEN_SECONDLY_ROLLING`` are ``SourceRateLimited`` with ``Retry-After`` when
        usable. An absent, non-JSON, non-string or unknown policy is also
        ``SourceRateLimited``: retry is bounded by the policy, whereas halting the
        source would drop leads on a limit that may clear in seconds. This is the only
        place the policy is read, and error text names the policy, never the body.
        Every other status is the base default.
        """
        if response.status != 429:
            return super().classify_error(response, endpoint=endpoint)
        policy = _policy_of(response.body)
        if policy in _DAILY_POLICIES:
            return SourceQuotaExhausted(self.name, f"cause=policy_{policy.lower()}")
        return SourceRateLimited(
            self.name,
            cause=(
                f"policy_{policy.lower()}"
                if policy in _SHORT_POLICIES
                else "unrecognized_policy"
            ),
            retry_after_s=retry_after_seconds(response.headers),
        )

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        if not isinstance(request, EnrichmentRequest):
            raise SourceError(
                self.name, "hubspot answers an enrichment request only, not discovery"
            )
        requesters = {
            email: records
            for email, records in _requesters_of(request.work_list).items()
            if email not in self._emitted
        }
        lookups: list[Mapping[str, Any]] = []
        ambiguous = 0
        if requesters:
            headers, params = self._call_context()
            for email, records in requesters.items():
                if email not in self._found:
                    self._found[email] = await self._look_up(email, headers, params)
                entry: dict[str, Any] = {
                    "lookup": email,
                    "contacts": self._found[email],
                }
                asked = self._echo_for(email, records)
                if asked is None:
                    ambiguous += 1
                elif asked:
                    entry[REQUEST_ECHO_KEY] = asked
                lookups.append(entry)
        if ambiguous:
            _log.warning(
                "hubspot_echo_withheld", reason="ambiguous_requester", lookups=ambiguous
            )
        self._emitted.update(requesters)  # only now: a failed fetch emitted nothing
        return RawBatch(source_name=self.name, payload={"lookups": lookups})

    def _echo_for(
        self, email: str, records: list[LeadContribution]
    ) -> dict[str, str] | None:
        """The requester identity to echo; None when the ask was for two people."""
        identities, names = self._asked_for.setdefault(email, (set(), set()))
        linkedin: str | None = None
        verified: str | None = None
        for record in records:
            try:
                identity = linkedin_identity(record.values)
                name = normalized_person_name(record.values)
            except TypeError:
                raise NormalizationError(
                    self.name, raw_field_path="<record>", canonical_path="<unmapped>"
                ) from None
            if identity is not None:
                identities.add(identity)
                linkedin = linkedin or _text_of(record.values["person.linkedin_url"])
            if name is not None:
                names.add(name)
            address = _text_of(record.values.get("person.email"))
            status = record.values.get("person.email_status")
            if (
                address is not None
                and normalize_email(address) == email
                and status == EmailStatus.VERIFIED
            ):
                verified = normalize_email(address)
        if len(identities) > 1 or len(names) > 1:
            return None
        asked: dict[str, str] = {}
        if linkedin is not None:
            asked["linkedin_url"] = linkedin
        if verified is not None:
            asked["email"] = verified
            asked["email_status"] = EmailStatus.VERIFIED.value
        return asked

    async def _look_up(
        self, email: str, headers: Mapping[str, str], params: Mapping[str, object]
    ) -> list[Mapping[str, Any]]:
        response = await self._send(
            _CONTACT_SEARCH,
            params=params,
            json_body={
                "filterGroups": [
                    {
                        "filters": [
                            {"propertyName": "email", "operator": "EQ", "value": email}
                        ]
                    }
                ],
                "properties": list(_CONTACT_PROPERTIES),
                "limit": MAX_CONTACTS_PER_LOOKUP,
            },
            headers=headers,
        )
        results = _results_of(self.name, response.body)
        found: list[Mapping[str, Any]] = []
        for contact in results:
            contact_id = contact.get("id")
            if not isinstance(contact_id, str) or not contact_id:
                raise NormalizationError(
                    self.name, raw_field_path="results.id", canonical_path="<unmapped>"
                )
            found.append(
                {
                    "contact": contact,
                    "open_deals_total": await self._open_deals(
                        contact_id, headers, params
                    ),
                }
            )
        return found

    async def _open_deals(
        self, contact_id: str, headers: Mapping[str, str], params: Mapping[str, object]
    ) -> int:
        response = await self._send(
            _DEAL_SEARCH,
            params=params,
            json_body={
                "filterGroups": [
                    {
                        "filters": [
                            {
                                "propertyName": "associations.contact",
                                "operator": "EQ",
                                "value": contact_id,
                            },
                            # UNVERIFIED property name (see module doc).
                            {
                                "propertyName": "hs_is_closed",
                                "operator": "EQ",
                                "value": "false",
                            },
                        ]
                    }
                ],
                "limit": 1,
            },
            headers=headers,
        )
        return _open_deal_total(self.name, response.body)

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        payload = raw.payload
        lookups = payload.get("lookups") if isinstance(payload, Mapping) else None
        if not isinstance(lookups, list):
            raise NormalizationError(
                self.name, raw_field_path="lookups", canonical_path="<unmapped>"
            )
        fetched_at = datetime.now(UTC)

        def context_for(queried: frozenset[str]) -> NormalizationContext:
            return NormalizationContext(
                source_name=self.name,
                data_mode=self.data_mode,
                fetched_at=fetched_at,
                answerable_surfaces=self.answerable_surfaces,
                queried_paths=queried,
            )

        # Every CRM question was asked of a found contact, so a missing answer is
        # Negative Evidence; the email is the question itself. An unknown email never
        # reached the deal search, so "no open deal" was not asked of it.
        asked = frozenset(self.answerable_surfaces)
        context_found = context_for(asked)
        context_unknown = context_for(asked - {"crm.has_open_deal"})
        normalizer = Normalizer()
        contributions: list[LeadContribution] = []
        withheld: Counter[str] = Counter()
        for record in self._records(lookups, withheld):
            echoed = record.get(REQUEST_ECHO_KEY, {})
            own = self.RULES
            if record.get("contact") is None and echoed:
                # The asked address is the question, not an observation: on a record
                # that joins its requester it must not count as agreement.
                own = tuple(r for r in own if r.raw_field_path != _LOOKUP)
            rules = own + tuple(
                rule for key, rule in _ECHO_RULES.items() if key in echoed
            )
            checked = validate_raw_payload(self.name, _Record, record, rules)
            context = (
                context_unknown if record.get("contact") is None else context_found
            )
            contributions.append(normalizer.apply(checked, rules, context))
        for reason, count in sorted(withheld.items()):
            _log.info("hubspot_echo_withheld", reason=reason, lookups=count)
        return contributions

    def records_fetched(self, batch: RawBatch) -> int | None:
        """Contacts found across every lookup (an unknown email found none)."""
        lookups = (
            batch.payload.get("lookups") if isinstance(batch.payload, Mapping) else None
        )
        if not isinstance(lookups, list):
            raise NormalizationError(
                self.name, raw_field_path="lookups", canonical_path="<unmapped>"
            )
        self._records(lookups)  # the envelope checked, as normalize checks it
        return sum(len(entry["contacts"]) for entry in lookups)

    @classmethod
    def validate_fixture(cls, endpoint: str, body: object) -> None:
        if endpoint == "contact_search":
            for contact in _results_of(cls.name, body):
                record = {"lookup": "fixture", "contact": contact}
                validate_raw_payload(cls.name, _Record, record, cls.RULES)
        elif endpoint == "deal_search":
            _open_deal_total(cls.name, body)
        else:
            super().validate_fixture(endpoint, body)

    @classmethod
    def unmapped_fixture_paths(cls, endpoint: str, body: object) -> list[str]:
        if endpoint == "contact_search":
            results = _results_of(cls.name, body)
            assert isinstance(body, Mapping)  # _results_of refused anything else
            envelope = {k: v for k, v in body.items() if k != "results"}
            found = unmapped_raw_paths(envelope, (), cls.CONTACT_SEARCH_IGNORED)
            for contact in results:
                found += [
                    f"results.{path.removeprefix('contact.')}"
                    for path in unmapped_raw_paths(
                        {"contact": contact}, cls.RULES, cls.IGNORED
                    )
                ]
            return found
        if endpoint == "deal_search":
            total = _open_deal_total(cls.name, body)
            assert isinstance(body, Mapping)  # _open_deal_total refused anything else
            record = {"open_deals_total": total} | {
                k: v for k, v in body.items() if k != "total"
            }
            return unmapped_raw_paths(
                record, cls.RULES, cls.IGNORED | cls.DEAL_SEARCH_IGNORED
            )
        return super().unmapped_fixture_paths(endpoint, body)

    def _records(
        self, lookups: list[object], withheld: Counter[str] | None = None
    ) -> list[Mapping[str, Any]]:
        """One record per contact found, or one empty record for an unknown email.

        A record carries the lookup's echo (``asked``) only when it answers the asked
        person; each echo withheld is counted in ``withheld`` by reason.
        """
        records: list[Mapping[str, Any]] = []
        for entry in lookups:
            lookup = entry.get("lookup") if isinstance(entry, Mapping) else None
            contacts = entry.get("contacts") if isinstance(entry, Mapping) else None
            asked = (
                entry.get(REQUEST_ECHO_KEY, {}) if isinstance(entry, Mapping) else None
            )
            if (
                not isinstance(lookup, str)
                or not isinstance(contacts, list)
                or not isinstance(asked, Mapping)
                or not set(asked) <= set(_ECHO_RULES)
            ):
                raise NormalizationError(
                    self.name, raw_field_path="lookups", canonical_path="<unmapped>"
                )
            if not contacts:
                echo = {REQUEST_ECHO_KEY: dict(asked)} if asked else {}
                records.append({"lookup": lookup, "contact": None, **echo})
            if not all(isinstance(found, Mapping) for found in contacts):
                raise NormalizationError(
                    self.name, raw_field_path="contacts", canonical_path="<unmapped>"
                )
            reasons = [
                _withheld_reason(lookup, found, len(contacts)) if asked else None
                for found in contacts
            ]
            if withheld is not None:
                # One count per lookup and reason, not per contact.
                withheld.update({reason for reason in reasons if reason is not None})
            for found, reason in zip(contacts, reasons, strict=True):
                echo = (
                    {REQUEST_ECHO_KEY: dict(asked)} if asked and reason is None else {}
                )
                records.append({"lookup": lookup, **found, **echo})
        return records


def _results_of(provider: str, body: object) -> list[Mapping[str, Any]]:
    results = body.get("results") if isinstance(body, Mapping) else None
    if not isinstance(results, list) or not all(
        isinstance(r, Mapping) for r in results
    ):
        raise NormalizationError(
            provider, raw_field_path="results", canonical_path="<unmapped>"
        )
    return results


def _open_deal_total(provider: str, body: object) -> int:
    total = body.get("total") if isinstance(body, Mapping) else None
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        raise NormalizationError(
            provider, raw_field_path="total", canonical_path="<unmapped>"
        )
    return total


# The requester's identity a contribution echoes, at ``asked.<key>`` (option B).
_ECHO_RULES: Mapping[str, FieldRule] = {
    key: REQUEST_ECHO_RULES[key] for key in ("linkedin_url", "email", "email_status")
}


def _withheld_reason(
    lookup: str, found: Mapping[str, Any], contacts: int
) -> str | None:
    """Why a found contact is not the asked person, or None when it is."""
    if contacts > 1:
        return "multiple_contacts"
    contact = found.get("contact")
    properties = contact.get("properties") if isinstance(contact, Mapping) else None
    own = properties.get("email") if isinstance(properties, Mapping) else None
    if not isinstance(own, str) or not own.strip():
        return "email_unconfirmed"
    if normalize_email(own) != lookup:
        return "email_mismatch"
    return None


def _text_of(value: object) -> str | None:
    text = value.value if isinstance(value, UntrustedText) else value
    return text.strip() if isinstance(text, str) and text.strip() else None


def _requesters_of(
    work_list: tuple[LeadContribution, ...],
) -> dict[str, list[LeadContribution]]:
    """Distinct work-list emails (trimmed, lowercased, in order) and who holds each."""
    requesters: dict[str, list[LeadContribution]] = {}
    for contribution in work_list:
        # Apollo and Hunter write ``person.email``; the bare ``email`` is HubSpot's own.
        value = contribution.values.get(
            "person.email", contribution.values.get("email")
        )
        if isinstance(value, str) and value.strip():
            # lower(), as the email Match Key folds (normalize_email), never casefold().
            requesters.setdefault(value.strip().lower(), []).append(contribution)
    return requesters
