"""HubSpot Source adapter: the free CRM-state lookup for Enrichment (13.1).

For each lead on the work list that has an email, searches HubSpot contacts by that
email and, for every contact found, searches deals for an unclosed one. It contributes
CRM state (contact exists, lifecycle stage, owner, last activity date, open deal) and
the canonical compliance flags (13.2, 13.7). Both searches are POSTs because HubSpot
takes its query in a body; they read and write nothing in HubSpot, so they are
read-only ``Endpoint`` declarations. Client-side throttling with policy-aware backoff
is task 13.2 in tasks.md; this module only declares the documented ``search`` bucket
that the base class paces on. The transport is REST only: there is no MCP connection
and no interactive authorization step (13.8).

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
  since the spec asks for "the compliance flags" and names no second property.
* Only the lead's ``email`` is looked up (no LinkedIn or name lookup), for any work-list
  lead whatever its source. One contribution is made per contact found, so a duplicate
  contact flagged as opted out still flags the lead (the orchestrator matches by
  email). An email with no contact yields one contribution of Negative Evidence.
* Open deal presence is a second search, deals associated with the contact and not
  closed, read from ``total``. It is made once per contact found.
* Lookups are cached for the life of the source, so a retried fetch repeats no call.
* Lifecycle stage and owner are identifiers, not free text, so they are not
  ``UntrustedText``. No free-text property is requested.
* Property names (``notes_last_updated`` for last activity, ``hs_email_optout``,
  ``hs_is_closed``) and the ``associations.contact`` filter come from HubSpot's
  documented defaults and were not checked against a live portal.
* ``fixtures/hubspot/*.json`` are hand-made STAND-INS, not captured responses.
* At most 100 contacts are read per email; there is no paging.
"""

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, ClassVar

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

__all__ = ["MAX_CONTACTS_PER_LOOKUP", "HubSpotSource"]

MAX_CONTACTS_PER_LOOKUP = 100

_TOKEN_ENV = "HUBSPOT_ACCESS_TOKEN"
_VERSION_ENV = "HUBSPOT_API_VERSION"
_VERSION_SHAPE = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])")
_DOCS = "https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm"

_CONTACT_SEARCH = Endpoint(
    method="POST", path="/crm/objects/{version}/contacts/search", bucket="search"
)
_DEAL_SEARCH = Endpoint(
    method="POST", path="/crm/objects/{version}/deals/search", bucket="search"
)

_CONTACT_PROPERTIES = (
    "lifecyclestage",
    "hubspot_owner_id",
    "notes_last_updated",
    "hs_email_optout",
)
_OPT_OUT_RAW = "contact.properties.hs_email_optout"


class _Properties(BaseModel):
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


def _text(value: object) -> object:
    """A blank string is no value; anything else is kept as sent."""
    return None if isinstance(value, str) and not value.strip() else value


def _flag(value: object) -> object:
    value = _text(value)
    if value is None:
        return None
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("not a boolean flag")


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
            windows=(RateWindow(requests=5, per_seconds=1.0),),
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
    docs_url: ClassVar[str] = _DOCS
    base_url: ClassVar[str] = "https://api.hubapi.com"

    RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule("email", "lookup"),
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

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        if not isinstance(request, EnrichmentRequest):
            raise SourceError(
                self.name, "hubspot answers an enrichment request only, not discovery"
            )
        emails = _emails_of(request.work_list)
        lookups: list[Mapping[str, Any]] = []
        if emails:
            headers, params = self._call_context()
            for email in emails:
                if email not in self._found:
                    self._found[email] = await self._look_up(email, headers, params)
                lookups.append({"lookup": email, "contacts": self._found[email]})
        return RawBatch(source_name=self.name, payload={"lookups": lookups})

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
        body = response.body
        total = body.get("total") if isinstance(body, Mapping) else None
        if not isinstance(total, int) or isinstance(total, bool) or total < 0:
            raise NormalizationError(
                self.name, raw_field_path="total", canonical_path="<unmapped>"
            )
        return total

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
        for record in self._records(lookups):
            checked = validate_raw_payload(self.name, _Record, record, self.RULES)
            context = (
                context_unknown if record.get("contact") is None else context_found
            )
            contributions.append(normalizer.apply(checked, self.RULES, context))
        return contributions

    def _records(self, lookups: list[object]) -> list[Mapping[str, object]]:
        """One record per contact found, or one empty record for an unknown email."""
        records: list[Mapping[str, object]] = []
        for entry in lookups:
            lookup = entry.get("lookup") if isinstance(entry, Mapping) else None
            contacts = entry.get("contacts") if isinstance(entry, Mapping) else None
            if not isinstance(lookup, str) or not isinstance(contacts, list):
                raise NormalizationError(
                    self.name, raw_field_path="lookups", canonical_path="<unmapped>"
                )
            if not contacts:
                records.append({"lookup": lookup, "contact": None})
            for found in contacts:
                if not isinstance(found, Mapping):
                    raise NormalizationError(
                        self.name,
                        raw_field_path="contacts",
                        canonical_path="<unmapped>",
                    )
                records.append({"lookup": lookup, **found})
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


def _emails_of(work_list: tuple[LeadContribution, ...]) -> list[str]:
    """Distinct work-list emails, trimmed and case-folded, in work-list order."""
    emails: dict[str, None] = {}
    for contribution in work_list:
        value = contribution.values.get("email")
        if isinstance(value, str) and value.strip():
            emails[value.strip().casefold()] = None
    return list(emails)
