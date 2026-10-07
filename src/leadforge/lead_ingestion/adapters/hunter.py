"""Hunter.io Source adapter: email discovery batched by domain (task 15.1).

Each Lead on the work list is routed to one Hunter endpoint (16.3): a known address to
``GET /v2/email-verifier``; a first and last name with a domain, and no address, to
``GET /v2/email-finder``; a Lead with only a company domain to
``GET /v2/domain-search``, once per distinct domain. Every answer becomes a Lead
contribution: the address, its deliverability verdict, Hunter's own confidence, and
where it was found (16.2). A Lead is a person, and several people at one company are
several Leads (user decision, 2026-10-06), so the adapter declares ``per_lead``: the
orchestrator hands it every Lead, and each person reaches the finder or verifier. Only
the domain search is a per-company call; it is asked once per distinct domain per run
(the per-run domain cache), as is each address and name pair. The key travels in the
``X-API-KEY`` header, never in the query string (16.1).

A verifier answer that is still running (HTTP 202) is polled (16.4, task 15.2), see
below. Error classification is Hunter's own (task 15.3): see ``classify_error``.

Provisional decisions (see choices.md, task 15.1):

* One page of at most 100 addresses per domain, no paging: each page costs Credits. A
  domain with more addresses logs ``hunter_domain_search_truncated`` with counts only.
  The docs give the domain search ``limit`` a default of 10 and state no maximum (100
  is Discover's documented maximum; UNVERIFIED for domain search). A ``free`` plan asks
  for 10: "This error [``pagination_error``] can also be returned if the limit
  additioned to the offset is higher than 10 for a Free plan user"
  (https://hunter.io/api-documentation/v2#domain-search, read 2026-10-06).
* Routing, per Lead: a usable ``person.email`` wins (verifier); else usable
  ``person.first_name`` and ``person.last_name`` with a domain (finder); else every
  domain (domain search). A name is unusable if blank, over 100 characters, containing
  a control character or ``*`` (Apollo obfuscates last names, and a masked name would
  spend a Credit on a guess). A Lead with a name but no domain makes no call.
* The raw batch is ``{"searches": [{"domain", "response"}], "finds": [{"domain",
  "first_name", "last_name", "linkedin_url", "response"}], "verifications": [{"email",
  "response"}], "credits_billable", "plan"}``, each response verbatim (``None`` for a
  verifier 202, a give-up or a 222). ``credits_in(batch)`` follows Hunter's API
  billing for a real key's live batch, zero for the sandbox key or synthetic mode
  (16.8): a domain search costs ``ceil(addresses returned / 10)`` (none for none; "1
  Search credit for 1 to 10 email addresses per domain returned" on Data plans, "1
  credit per 1 to 10 email addresses returned per domain" on All-in-one); a finder 1
  only when it found an address ("1 credit per call, charged only if an email is
  found"; the API reference: "If no email can be found, no credit is charged"); a
  verification the plan's price. The plan is ``HUNTER_PLAN``, a non-secret setting:
  ``data`` ("1 Verification credit per call"), ``all-in-one`` ("0.5 credits per
  call") or ``free`` (an All-in-one plan on https://hunter.io/pricing: 0.5, and a
  domain search capped at 10 addresses). Sources, read in full on 2026-10-06:
  https://help.hunter.io/en/articles/12149400-hunter-api-for-data-plans,
  https://help.hunter.io/en/articles/1970956-hunter-api (All-in-one) and
  https://help.hunter.io/en/articles/1911617-how-do-credits-work-in-hunter. Hunter
  also does not charge a repeat of the same question within a billing month, so a
  sum over runs can overstate the bill. Unset or blank means ``data``, the
  conservative price; any other value is a ``ConfigurationError`` naming the
  variable, never the value, raised when a live run starts (``run_rate_limit``) and
  again before a live fetch spends. The batch records the plan (``plan``) so a
  stored batch is priced as it was bought. Credits are an exact ``Decimal``
  (follow-up fu3, 2026-10-06): three all-in-one verifications are 1.5, never rounded
  and never a float; the store keeps them exact (migration 0008). The plan names
  are this setting's own labels for Hunter's two plan families ("Data Platform" and
  "All-in-one outreach platform") and its Free tier. UNVERIFIED: whether a Data plan's
  separate Verification credit type should be
  counted apart from search credits (it is summed here), and how Hunter itself rounds
  half credits on its invoice. A verification Hunter could not finish (202 give-up,
  222) or answered ``unknown`` is still priced, also conservative: the credits article
  says "No credits are used if Hunter can't find or verify an email", while both API
  articles price the verifier "per call"; which one governs an ``unknown`` answer is
  UNVERIFIED.
* A found address carries the person it was asked for (follow-up, 2026-10-06): the
  domain and the name asked (not Hunter's echo) and the requester's own LinkedIn URL
  when it had one (never Hunter's ``linkedin_url``), at raw paths ``asked.*``, so the
  normal Match Keys join it to that person. One name question at one domain asked on
  behalf of two distinguishable people (distinct normalised LinkedIn identities, a
  LinkedIn-less record counting as one more) is not asked at all
  (``hunter_finder_ambiguous`` with a count only, no Credit): one answer cannot belong
  to both. That holds across fetches of one run; an answer already attached in an
  earlier batch is not recalled. Records with no LinkedIn are indistinguishable and
  share the one answer. A record with no LinkedIn of its own that is strongly linked
  (LinkedIn URL or verified-address Match Keys, transitively, the orchestrator's
  pruning components, so a shared address links no one) to a person holding exactly
  one LinkedIn takes that identity (follow-up fu2, 2026-10-06): one person's records
  ask once. A bare record not so linked still makes the name ambiguous.
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
* Polling (16.4): a 202 asks the same verifier question again, through the same paced
  ``_send`` (each poll takes verifier capacity), until a final answer, ``poll_attempts``
  polls (default 5), or ``poll_budget_s`` seconds in all (default 30, measured from
  before the first request, pacing waits included). The wait before a poll is the
  ``Retry-After`` hint when it is a number, else ``poll_interval_s`` (default 2); never
  less than the interval, never more than the budget left, and none after the last
  poll. Giving up is no verdict (nothing contributed, so the status stays unknown), one
  ``hunter_verification_unfinished`` warning with the reason and the poll count, and no
  exception: it must not abort a paid batch. The give-up is cached like a verdict, so a
  retried fetch does not restart the poll; an error or cancel mid-poll caches nothing.
  Hunter does not charge for polling ("all the requests in this case are counted only
  once", Email Verifier docs, read 2026-10-06), so the batch counts one verification
  per address, not per poll. A 222 ("The verification failed because of an unexpected
  response from the remote SMTP server ... We recommend to retry later") is also no
  verdict: logged with reason ``smtp_failure``, not retried in this run, and priced
  like a give-up (conservative). The clock and sleep are injected, so tests never
  wait; the default ``asyncio.sleep`` is cancellable, so a run timeout cuts a poll
  short.
* Status conventions (16.7, 15.3), classified once in ``classify_error``: 403 is
  ``SourceRateLimited`` (with ``Retry-After`` when usable), 429 is
  ``SourceQuotaExhausted`` (halts the source for the run), 451 is
  ``SourceComplianceRestricted``; everything else is the base default (401 is
  unauthorized). The 451 carries no person: its ``subject`` is the endpoint path,
  because the classifier sees no request and a subject must never be an address.
* A 451 from the finder or verifier (16.6) is ISOLATED to that question: the call is
  caught in ``fetch_raw`` (that error type only), recorded in the batch under
  ``restricted_verifications`` (``{"email"}``) or ``restricted_finds`` (``{"domain",
  "first_name", "last_name", "linkedin_url"}``), and the rest of the batch goes on.
  Each becomes one contribution carrying ``suppressed`` = True and the identity the
  Lead was asked by (the address; or the name, domain and the requester's LinkedIn URL
  when it had one), and no verdict, confidence or source. ``yields_suppression`` is
  True, so Hunter runs in a paid tier ahead of Apollo and a 451 prunes the person (by
  address or LinkedIn identity) before Apollo's paid match (user decision,
  2026-10-06). A name-only person with no LinkedIn cannot be pruned that way. The
  restriction is cached like a verdict, so a retried fetch does not ask again. A 451
  from a domain search names no person, so it is not isolated: it fails the fetch as
  ``SourceComplianceRestricted``. A 451 is assumed not to be billed.
* The fixtures under ``fixtures/hunter/`` are hand-made STAND-INS, not captured
  responses; the verifier's ``data`` shape in particular is assumed.
"""

import asyncio
import math
import os
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, NamedTuple

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
    retry_after_seconds,
)
from leadforge.lead_ingestion.errors import (
    ConfigurationError,
    NormalizationError,
    SourceComplianceRestricted,
    SourceError,
    SourceQuotaExhausted,
    SourceRateLimited,
)
from leadforge.lead_ingestion.match_keys import linkedin_identity, normalize_email
from leadforge.lead_ingestion.models import (
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
from leadforge.lead_ingestion.orchestrator import _strong_person_labels
from leadforge.lead_ingestion.throttle import Clock, Sleep
from leadforge.lead_ingestion.transport import Transport, TransportResponse

if TYPE_CHECKING:
    from leadforge.lead_ingestion.pacing import SourcePacing

__all__ = [
    "CONFIDENCE_SCALE",
    "DEFAULT_PLAN",
    "DEFAULT_POLL_ATTEMPTS",
    "DEFAULT_POLL_BUDGET_S",
    "DEFAULT_POLL_INTERVAL_S",
    "MAX_EMAILS_PER_DOMAIN",
    "PLAN_ENV",
    "SANDBOX_KEY",
    "HunterSource",
    "credits_in",
]

MAX_EMAILS_PER_DOMAIN = 100  # one domain-search page; no documented maximum
SANDBOX_KEY = "test-api-key"  # Hunter's published key: dummy responses, no Credits
CONFIDENCE_SCALE = "hunter_confidence_0_100"

_KEY_HEADER = "X-API-KEY"
_KEY_ENV = "HUNTER_API_KEY"
_DOCS = "https://hunter.io/api-documentation/v2"
_EMAIL_PATH = "person.email"

_SEARCH = Endpoint(method="GET", path="/v2/domain-search", bucket="finder")
_FINDER = Endpoint(method="GET", path="/v2/email-finder", bucket="finder")
_VERIFIER = Endpoint(method="GET", path="/v2/email-verifier", bucket="verifier")
_ACCEPTED = 202  # the verifier is still running: ask again (16.4)
# "The verification failed because of an unexpected response from the remote SMTP
# server ... We recommend to retry later" (Email Verifier errors): no verdict.
_SMTP_FAILURE = 222
_FORBIDDEN = 403  # Hunter: the rate limit was reached (16.7)
_TOO_MANY = 429  # Hunter: the usage limit is spent (16.7)
_RESTRICTED = 451  # Hunter: personal-data processing is restricted (16.6)
DEFAULT_POLL_ATTEMPTS = 5
DEFAULT_POLL_BUDGET_S = 30.0
DEFAULT_POLL_INTERVAL_S = 2.0

_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOSTNAME = re.compile(rf"{_LABEL}(?:\.{_LABEL})+")
_MAX_DOMAIN_LENGTH = 253
_MAX_NAME_LENGTH = 100
_MAX_ADDRESS_LENGTH = 254
_ADDRESSES_PER_CREDIT = 10  # Domain Search: one Credit per 1-10 addresses returned
_CREDITS_DOCS = (
    "https://help.hunter.io/en/articles/1911617-how-do-credits-work-in-hunter"
)
PLAN_ENV = "HUNTER_PLAN"
DEFAULT_PLAN = "data"  # the dearer verification price, safe on every plan
_FREE_PLAN_PAGE = 10  # Free plan: limit + offset above 10 is a ``pagination_error``


class _PlanTerms(NamedTuple):
    verifier_price: Decimal  # Credits one Email Verifier call costs
    domain_limit: int  # the ``limit`` one domain search may ask for


# Plan -> its terms (help.hunter.io and hunter.io/api-documentation/v2, read
# 2026-10-06). Free is an All-in-one plan (hunter.io/pricing lists it with Starter,
# Growth and Scale), so it pays the All-in-one price.
_PLANS: Mapping[str, _PlanTerms] = {
    "data": _PlanTerms(Decimal(1), MAX_EMAILS_PER_DOMAIN),
    "all-in-one": _PlanTerms(Decimal("0.5"), MAX_EMAILS_PER_DOMAIN),
    "free": _PlanTerms(Decimal("0.5"), _FREE_PLAN_PAGE),
}
_ADDRESS = re.compile(r"[^\s@?#/\\:]+@" + _HOSTNAME.pattern)
# The finder question echoed back (``REQUEST_ECHO_RULES`` keys), in this order.
_ASKED_KEYS = ("domain", "first_name", "last_name", "linkedin_url")
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
    # Null in Hunter's documented "no results" answer (Domain Search, 200 OK).
    domain: StrictStr | None = None
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
    # Per lead, not per company: the finder and verifier are per-person calls, so the
    # orchestrator must not collapse a company's people to one (11.7). The per-company
    # domain search is still paid once per domain by the per-run cache in fetch_raw.
    charge_unit: ClassVar[ChargeUnit] = ChargeUnit.PER_LEAD
    # Discovery's people carry masked names and no address, so the first call is
    # often handed nothing it can ask about; it runs again for the addresses and names
    # the paid match supplied (user decision 2026-10-07). Per-run caches keep every
    # lookup to one call.
    rerun_for_new_identities: ClassVar[bool] = True
    # A 451 flags the person suppressed (16.6). Declaring it puts Hunter in a paid tier
    # ahead of Apollo, so the flag prunes that person before Apollo's paid match (user
    # decision, 2026-10-06; reverses the earlier "one tier with Apollo").
    yields_suppression: ClassVar[bool] = True
    target_vocabulary: ClassVar[Mapping[str, object]] = {}
    endpoints: ClassVar[Mapping[str, Endpoint]] = {
        "domain_search": _SEARCH,
        "email_finder": _FINDER,
        "email_verifier": _VERIFIER,
    }
    required_env: ClassVar[tuple[str, ...]] = (_KEY_ENV,)
    optional_env: ClassVar[tuple[str, ...]] = (PLAN_ENV,)
    env_notes: ClassVar[Mapping[str, str]] = {
        PLAN_ENV: (
            f"optional: your Hunter plan, one of {', '.join(_PLANS)}; prices an Email "
            f"Verifier call (data 1 credit, all-in-one and free 0.5; {_CREDITS_DOCS}) "
            f"and caps a free plan's domain search at {_FREE_PLAN_PAGE} addresses; "
            f"unset means {DEFAULT_PLAN}"
        )
    }
    docs_url: ClassVar[str] = _DOCS
    base_url: ClassVar[str] = "https://api.hunter.io"

    @classmethod
    def plan(cls, environ: Mapping[str, str] | None = None) -> str:
        """The plan ``HUNTER_PLAN`` names; unset or blank is ``DEFAULT_PLAN``."""
        env = os.environ if environ is None else environ
        plan = env.get(PLAN_ENV, "").strip().casefold() or DEFAULT_PLAN
        if plan not in _PLANS:
            # The value is not echoed, as for every configuration error.
            raise ConfigurationError(
                "environment",
                key_path=PLAN_ENV,
                detail=f"must be one of {', '.join(_PLANS)}",
            )
        return plan

    @classmethod
    def run_rate_limit(
        cls, environ: Mapping[str, str] | None = None
    ) -> Mapping[str, RateBucket]:
        """The declared buckets (no plan changes them), once ``HUNTER_PLAN`` is valid.

        Read when a live run starts, so an unusable plan fails before any record.
        """
        cls.plan(environ)
        return cls.rate_limit

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
        FieldRule("person.title", "position", untrusted=True),
        FieldRule(
            "person.email_status", "verification.status", transform=_email_status
        ),
        FieldRule("person.email_sources", "sources", transform=_source_uris),
    )
    FINDER_IGNORED: ClassVar[frozenset[str]] = frozenset(
        {
            "score",  # read by the adapter into the email's provenance
            # The identity comes from the question asked (ASKED_RULES), not the echo.
            "first_name",
            "last_name",
            "domain",
            "accept_all",
            "twitter",
            "linkedin_url",
            "phone_number",
            "verification.date",
        }
    )
    # The person a found address was asked for (follow-up 2026-10-06): the domain and
    # name asked, and the requester's LinkedIn URL when it had one, so the normal Match
    # Keys join the address to that person. Paths are under the ``asked`` wrapper the
    # adapter adds beside the response's ``data``; the values are the finds entry's.
    # The prefix marks them request echoes: conflict resolution never lets them
    # corroborate, conflict with or outrank the requester's own values (models).
    ASKED_RULES: ClassVar[tuple[FieldRule, ...]] = tuple(
        REQUEST_ECHO_RULES[key] for key in _ASKED_KEYS
    )
    VERIFIER_RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule(_EMAIL_PATH, "email"),
        FieldRule("person.email_status", "status", transform=_email_status),
    )
    # A 451 (16.6): the identity the Lead was asked by, flagged, nothing Hunter said.
    RESTRICTED_VERIFY_RULES: ClassVar[tuple[FieldRule, ...]] = (
        FieldRule(_EMAIL_PATH, "email"),
        FieldRule("suppressed", "restricted"),
    )
    # The identity is the request echoed, like ASKED_RULES; only the flag is Hunter's.
    RESTRICTED_FIND_RULES: ClassVar[tuple[FieldRule, ...]] = (
        *ASKED_RULES,
        FieldRule("suppressed", "restricted"),
    )
    VERIFIER_IGNORED: ClassVar[frozenset[str]] = frozenset({"score"})
    # Beside ``data``, every response carries ``meta``: an echo of the request and the
    # result counts. Nothing in it is a lead field; each leaf is named, not the subtree,
    # so a field Hunter adds there still fails the fixture guard.
    ENVELOPE_IGNORED: ClassVar[frozenset[str]] = frozenset(
        {
            "meta.results",
            "meta.results_approximate",
            "meta.limit",
            "meta.offset",
            "meta.params.domain",
            "meta.params.first_name",
            "meta.params.last_name",
            "meta.params.email",
        }
    )
    # Domain-level flags and fields of the address record that 16.2 does not ask for.
    IGNORED: ClassVar[frozenset[str]] = frozenset(
        {
            "disposable",
            "webmail",
            "accept_all",
            "pattern",
            "linked_domains",
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
        poll_attempts: int = DEFAULT_POLL_ATTEMPTS,
        poll_budget_s: float = DEFAULT_POLL_BUDGET_S,
        poll_interval_s: float = DEFAULT_POLL_INTERVAL_S,
        clock: Clock = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        super().__init__(mode, transport=transport, pacing=pacing)
        if (
            isinstance(poll_attempts, bool)
            or not isinstance(poll_attempts, int)
            or poll_attempts < 0
        ):
            raise ValueError("poll_attempts must be a non-negative integer")
        if not _is_finite(poll_budget_s) or poll_budget_s <= 0:
            raise ValueError("poll_budget_s must be a positive finite number")
        if not _is_finite(poll_interval_s) or poll_interval_s < 0:
            raise ValueError("poll_interval_s must be a non-negative finite number")
        self._poll_attempts = poll_attempts
        self._poll_budget_s = float(poll_budget_s)
        self._poll_interval_s = float(poll_interval_s)
        self._clock = clock
        self._sleep = sleep
        self._environ = environ
        # Calls already paid for this run: a retried fetch must not buy them again.
        self._searched: dict[str, Mapping[str, Any]] = {}
        self._found: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        # Who each name question was asked for this run (normalised LinkedIn identity,
        # None for a record without one): one answer never serves two of them.
        self._asked_for: dict[tuple[str, str, str], set[str | None]] = {}
        self._verified: dict[str, Mapping[str, Any] | None] = {}
        # Questions Hunter refused with a 451 (16.6): asked once, never again.
        self._restricted_names: dict[tuple[str, str, str], Mapping[str, Any]] = {}
        self._restricted_addresses: set[str] = set()

    def classify_error(
        self, response: TransportResponse, *, endpoint: Endpoint
    ) -> SourceError | None:
        """Hunter's inverted reading of the conventional mapping (16.6, 16.7).

        403 is throttling, not authorization: ``SourceRateLimited`` with
        ``Retry-After`` when usable. 429 means the usage limit is spent:
        ``SourceQuotaExhausted``, which halts the source for the run. 451 is restricted
        personal-data processing: ``SourceComplianceRestricted``, whose subject is the
        endpoint path, never the person. Every other status is the base default. Error
        text names the cause, never the body.
        """
        status = response.status
        if status == _FORBIDDEN:
            return SourceRateLimited(
                self.name,
                cause="http_403_rate_limit",
                retry_after_s=retry_after_seconds(response.headers),
            )
        if status == _TOO_MANY:
            return SourceQuotaExhausted(self.name, "cause=http_429_usage_limit")
        if status == _RESTRICTED:
            return SourceComplianceRestricted(self.name, subject=endpoint.path)
        return super().classify_error(response, endpoint=endpoint)

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        if not isinstance(request, EnrichmentRequest):
            raise SourceError(
                self.name, "hunter answers an enrichment request only, not discovery"
            )
        plan = _route(self.name, request.work_list)
        headers: Mapping[str, str] = {}
        billable = False
        priced = DEFAULT_PLAN
        asks = plan.domains or plan.names or plan.addresses
        if asks and self.data_mode is DataMode.LIVE:
            priced = self.plan(self._environ)  # before any call: no unknown price
            key = resolve_credentials(self, self._environ)[_KEY_ENV]
            headers = {_KEY_HEADER: key}
            billable = key != SANDBOX_KEY
        searches: list[Mapping[str, Any]] = []
        for domain in plan.domains:
            if domain not in self._searched:
                self._searched[domain] = await self._search(
                    domain, _PLANS[priced].domain_limit, headers
                )
            searches.append({"domain": domain, "response": self._searched[domain]})
        finds: list[Mapping[str, Any]] = []
        ambiguous = 0
        for ask in plan.names:
            domain, first, last = ask.domain, ask.first, ask.last
            asked = (domain, first.casefold(), last.casefold())
            if asked in self._restricted_names:
                continue
            identities = self._asked_for.setdefault(asked, set())
            identities |= ask.identities
            if len(identities) > 1:
                ambiguous += 1  # distinguishable people share the name: no answer fits
                continue
            if asked not in self._found:
                try:
                    self._found[asked] = await self._find(domain, first, last, headers)
                except SourceComplianceRestricted:
                    self._restricted_names[asked] = {
                        "domain": domain,
                        "first_name": first,
                        "last_name": last,
                        # The work list is pruned by address or LinkedIn identity, so
                        # the flag must name the requester's to reach them.
                        "linkedin_url": ask.linkedin_url,
                    }
                    continue
            finds.append(
                {
                    "domain": domain,
                    "first_name": first,
                    "last_name": last,
                    "linkedin_url": ask.linkedin_url,
                    "response": self._found[asked],
                }
            )
        if ambiguous:
            _log.warning("hunter_finder_ambiguous", questions=ambiguous)
        verifications: list[Mapping[str, Any]] = []
        for address in plan.addresses:
            if address in self._restricted_addresses:
                continue
            if address not in self._verified:
                try:
                    self._verified[address] = await self._verify(address, headers)
                except SourceComplianceRestricted:
                    self._restricted_addresses.add(address)
                    continue
            verifications.append(
                {
                    "email": address,
                    "linkedin_url": plan.address_linkedin.get(address),
                    "response": self._verified[address],
                }
            )
        return RawBatch(
            source_name=self.name,
            payload={
                "searches": searches,
                "finds": finds,
                "verifications": verifications,
                "restricted_finds": [
                    self._restricted_names[ask.key]
                    for ask in plan.names
                    if ask.key in self._restricted_names
                ],
                "restricted_verifications": [
                    {"email": address}
                    for address in plan.addresses
                    if address in self._restricted_addresses
                ],
                "credits_billable": billable,
                "plan": priced,
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
        self, domain: str, limit: int, headers: Mapping[str, str]
    ) -> Mapping[str, Any]:
        params = {"domain": domain, "limit": limit}
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
        started = self._clock()
        polls = 0
        while True:
            response = await self._send(
                _VERIFIER, params={"email": address}, json_body=None, headers=headers
            )
            if response.status not in (_ACCEPTED, _SMTP_FAILURE):
                return self._checked(response)
            left = self._poll_budget_s - (self._clock() - started)
            if response.status == _SMTP_FAILURE:
                reason = "smtp_failure"
            elif polls >= self._poll_attempts:
                reason = "attempts"
            elif left <= 0:
                reason = "budget"
            else:
                hint = retry_after_seconds(response.headers)
                wanted = self._poll_interval_s if hint is None else hint
                await self._sleep(min(max(wanted, self._poll_interval_s), left))
                polls += 1
                continue
            _log.warning("hunter_verification_unfinished", reason=reason, polls=polls)
            return None  # no verdict: the address stays unknown, the batch goes on

    def records_fetched(self, batch: RawBatch) -> int | None:
        """Addresses Hunter returned: every domain-search address, and each finder
        answer that found one. Verifications check an address already held."""
        listed = sum(
            len(_listed(self.name, response))
            for response in _responses(self.name, batch, "searches", required=True)
        )
        found = sum(
            1
            for entry in _find_entries(self.name, batch)
            if entry["response"]["data"].get("email") is not None
        )
        return listed + found

    def credits_spent(self, batch: RawBatch) -> Decimal | None:
        """``credits_in``: Hunter's billing; the sandbox and synthetic spend none."""
        return credits_in(batch)

    @classmethod
    def validate_fixture(cls, endpoint: str, body: object) -> None:
        checks: dict[str, tuple[type[BaseModel], tuple[FieldRule, ...]]] = {
            "domain_search": (_Response, cls.RULES),
            "email_finder": (_FoundResponse, cls.FINDER_RULES),
            "email_verifier": (_VerdictResponse, cls.VERIFIER_RULES),
        }
        if endpoint not in checks:
            super().validate_fixture(endpoint, body)
            return
        model, rules = checks[endpoint]
        if not isinstance(body, Mapping):
            raise NormalizationError(
                cls.name, raw_field_path="data", canonical_path="<unmapped>"
            )
        validate_raw_payload(cls.name, model, body, rules)

    @classmethod
    def unmapped_fixture_paths(cls, endpoint: str, body: object) -> list[str]:
        if endpoint not in cls.endpoints:
            return super().unmapped_fixture_paths(endpoint, body)
        data = body.get("data") if isinstance(body, Mapping) else None
        if not isinstance(body, Mapping) or not isinstance(data, Mapping):
            raise NormalizationError(
                cls.name, raw_field_path="data", canonical_path="<unmapped>"
            )
        envelope = {k: v for k, v in body.items() if k != "data"}
        found = unmapped_raw_paths(envelope, (), cls.ENVELOPE_IGNORED)
        if endpoint == "domain_search":
            emails = data.get("emails")
            if not isinstance(emails, list):
                raise NormalizationError(
                    cls.name, raw_field_path="data.emails", canonical_path="<unmapped>"
                )
            shared = {k: v for k, v in data.items() if k != "emails"}
            found += [
                f"data.{path}"
                for path in unmapped_raw_paths(shared, cls.RULES, cls.IGNORED)
            ]
            for item in emails:
                found += [
                    f"data.emails.{path.removeprefix('email.')}"
                    for path in unmapped_raw_paths(
                        {"email": item}, cls.RULES, cls.IGNORED
                    )
                ]
            return found
        rules, ignored = (
            (cls.FINDER_RULES, cls.FINDER_IGNORED)
            if endpoint == "email_finder"
            else (cls.VERIFIER_RULES, cls.VERIFIER_IGNORED)
        )
        return found + [
            f"data.{path}" for path in unmapped_raw_paths(data, rules, ignored)
        ]

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        context = NormalizationContext(
            source_name=self.name,
            data_mode=self.data_mode,
            fetched_at=datetime.now(UTC),
            answerable_surfaces=self.answerable_surfaces,
        )
        normalizer = Normalizer()
        contributions: list[LeadContribution] = []
        refused = {
            entry["email"].strip().casefold()
            for entry in _restrictions(
                self.name, raw, "restricted_verifications", ("email",)
            )
        }  # an address Hunter refused keeps nothing another answer says about it (16.6)
        for response in _responses(self.name, raw, "searches", required=True):
            assert response is not None
            validate_raw_payload(self.name, _Response, response, self.RULES)
            data = response["data"]
            shared = {k: v for k, v in data.items() if k != "emails"}
            for item in data["emails"]:
                if _is_refused(item.get("value"), refused):
                    continue
                contribution = normalizer.apply(
                    {**shared, "email": item}, self.RULES, context
                )
                contributions.append(
                    _with_stated_confidence(contribution, item.get("confidence"))
                )
        for entry in _find_entries(self.name, raw):
            response = entry["response"]
            validate_raw_payload(self.name, _FoundResponse, response, self.FINDER_RULES)
            data = response["data"]
            if data.get("email") is None:
                continue  # nothing found: no contribution, and no field question asked
            if _is_refused(data["email"], refused):
                continue
            contribution = normalizer.apply(
                {**data, REQUEST_ECHO_KEY: _asked_of(entry)},
                self.FINDER_RULES + self.ASKED_RULES,
                context,
            )
            contributions.append(
                _with_stated_confidence(contribution, data.get("score"))
            )
        asked = _verified_addresses(raw)
        linkedin_of_verification = _verification_linkedin(raw)
        mismatched = 0
        for index, response in enumerate(_responses(self.name, raw, "verifications")):
            if response is None:
                continue  # still running: no verdict yet
            validate_raw_payload(
                self.name, _VerdictResponse, response, self.VERIFIER_RULES
            )
            data = response["data"]
            if _is_refused(data.get("email"), refused):
                continue
            if normalize_email(str(data["email"])) != asked[index]:
                # A verdict on another address says nothing about the one asked
                # (follow-up 2026-10-07): never store it as that address's status.
                mismatched += 1
                continue
            # The requester's LinkedIn joins the verdict to its person whatever
            # the status (follow-up 2026-10-07): only a verified address is a Match
            # Key, so without it every other verdict was a Lead of its own.
            url = linkedin_of_verification[index]
            echo = {REQUEST_ECHO_KEY: {"linkedin_url": url}} if url else {}
            rules = self.VERIFIER_RULES + (
                (REQUEST_ECHO_RULES["linkedin_url"],) if url else ()
            )
            contribution = normalizer.apply({**data, **echo}, rules, context)
            contributions.append(
                _with_stated_confidence(contribution, data.get("score"))
            )
        if mismatched:
            _log.warning(
                "hunter_verdict_withheld", reason="email_mismatch", count=mismatched
            )
        for key, fields, rules in (
            (
                "restricted_finds",
                ("domain", "first_name", "last_name"),
                self.RESTRICTED_FIND_RULES,
            ),
            ("restricted_verifications", ("email",), self.RESTRICTED_VERIFY_RULES),
        ):
            for entry in _restrictions(self.name, raw, key, fields):
                flagged = (
                    {REQUEST_ECHO_KEY: _asked_of(entry)}
                    if key == "restricted_finds"
                    else dict(entry)
                )
                contributions.append(
                    normalizer.apply({**flagged, "restricted": True}, rules, context)
                )
        return contributions


def _asked_of(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The question a finds entry records: the identity echoed back, never Hunter's."""
    return {key: entry.get(key) for key in _ASKED_KEYS}


def _is_refused(address: object, refused: set[str]) -> bool:
    return isinstance(address, str) and address.strip().casefold() in refused


def _is_finite(value: object) -> bool:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:  # an int too large for a float
        return False


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


def credits_in(batch: RawBatch) -> Decimal:
    """Credits a real key's live batch spent, by Hunter's billing rules (16.8).

    A domain search costs one per 1-10 addresses returned (none for none); a finder
    one only when it found an address; a verification the batch's plan price, exactly
    (a batch without ``plan`` predates it: ``data``). The sandbox key and synthetic
    mode spend none.
    """
    payload = batch.payload
    billable = payload.get("credits_billable") if isinstance(payload, Mapping) else None
    plan = payload.get("plan", DEFAULT_PLAN) if isinstance(payload, Mapping) else None
    terms = _PLANS.get(plan) if isinstance(plan, str) else None
    price = None if terms is None else terms.verifier_price
    if not isinstance(billable, bool) or price is None:
        raise NormalizationError(
            batch.source_name,
            raw_field_path="credits_billable" if price is not None else "plan",
            canonical_path="<unmapped>",
        )
    if not billable:
        return Decimal(0)
    provider = batch.source_name
    searched = sum(
        math.ceil(len(_listed(provider, response)) / _ADDRESSES_PER_CREDIT)
        for response in _responses(provider, batch, "searches", required=True)
    )
    found = sum(
        1
        for response in _responses(provider, batch, "finds")
        if _data_of(provider, response, "finds").get("email") is not None
    )
    verified = len(_responses(provider, batch, "verifications"))
    return searched + found + verified * price


def _data_of(
    provider: str, response: Mapping[str, Any] | None, key: str
) -> Mapping[str, Any]:
    data = response.get("data") if isinstance(response, Mapping) else None
    if not isinstance(data, Mapping):
        raise NormalizationError(
            provider, raw_field_path=f"{key}.data", canonical_path="<unmapped>"
        )
    return data


def _listed(provider: str, response: Mapping[str, Any] | None) -> list[Any]:
    emails = _data_of(provider, response, "searches").get("emails")
    if not isinstance(emails, list):
        raise NormalizationError(
            provider, raw_field_path="searches.data.emails", canonical_path="<unmapped>"
        )
    return emails


def _find_entries(provider: str, batch: RawBatch) -> list[Mapping[str, Any]]:
    """Each finds entry: the question asked (text) and Hunter's answer (checked)."""
    if not _responses(provider, batch, "finds"):  # checks the list and each response
        return []
    entries: list[Mapping[str, Any]] = batch.payload["finds"]
    for entry in entries:
        linkedin = entry.get("linkedin_url")
        if not all(
            isinstance(entry.get(field), str)
            for field in ("domain", "first_name", "last_name")
        ) or not (linkedin is None or isinstance(linkedin, str)):
            raise NormalizationError(
                provider, raw_field_path="finds", canonical_path="<unmapped>"
            )
    return entries


def _verification_linkedin(batch: RawBatch) -> list[str | None]:
    """The requester LinkedIn URL each verification recorded, in batch order."""
    payload = batch.payload
    entries = payload.get("verifications") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        return []
    return [
        entry["linkedin_url"]
        if isinstance(entry, Mapping) and isinstance(entry.get("linkedin_url"), str)
        else None
        for entry in entries
    ]


def _verified_addresses(batch: RawBatch) -> list[str | None]:
    """The address each verification asked about, normalised, in batch order."""
    payload = batch.payload
    entries = payload.get("verifications") if isinstance(payload, Mapping) else None
    if not isinstance(entries, list):
        return []
    return [
        normalize_email(str(entry["email"]))
        if isinstance(entry, Mapping) and isinstance(entry.get("email"), str)
        else None
        for entry in entries
    ]


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


def _restrictions(
    provider: str, batch: RawBatch, key: str, fields: tuple[str, ...]
) -> list[Mapping[str, Any]]:
    """The questions Hunter refused with a 451, each a mapping of text ``fields``."""
    payload = batch.payload
    if not isinstance(payload, Mapping) or key not in payload:
        return []
    entries = payload[key]
    if not isinstance(entries, list) or not all(
        isinstance(entry, Mapping)
        and all(isinstance(entry.get(field), str) for field in fields)
        and isinstance(entry.get("linkedin_url"), str | None)
        for entry in entries
    ):
        raise NormalizationError(
            provider, raw_field_path=key, canonical_path="<unmapped>"
        )
    return entries


@dataclass(frozen=True)
class _Ask:
    """One finder question and who asked it: their LinkedIn identities (None: none)."""

    domain: str
    first: str
    last: str
    linkedin_url: str | None  # the request's own text, when one identity had one
    identities: frozenset[str | None]

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.domain, self.first.casefold(), self.last.casefold())


@dataclass(frozen=True)
class _Plan:
    """What one work list asks Hunter, each question once, in work-list order."""

    domains: list[str]
    names: list["_Ask"]
    addresses: list[str]
    # The one LinkedIn URL of the person(s) holding each address, if exactly one.
    address_linkedin: dict[str, str | None]


def _route(provider: str, work_list: tuple[LeadContribution, ...]) -> _Plan:
    """Send each Lead to the cheapest endpoint that answers it (16.3)."""
    domains: dict[str, None] = {}
    names: dict[tuple[str, str, str], tuple[str, str, str]] = {}
    askers: dict[tuple[str, str, str], dict[str | None, str | None]] = {}
    addresses: dict[str, None] = {}
    holders: dict[str, dict[str | None, str | None]] = {}
    person = _strong_person_labels(work_list)
    linkedin = _person_linkedin(provider, work_list, person)
    for index, contribution in enumerate(work_list):
        company = _domains_of(provider, contribution)
        address = _address_of(provider, contribution)
        if address is not None:
            addresses[address] = None
            identity, url = _linkedin_of(provider, contribution)
            if identity is None:
                identity, url = linkedin.get(person[index], (None, None))
            holders.setdefault(address, {}).setdefault(identity, url)
            continue
        first = _name_of(contribution.values.get("person.first_name"))
        last = _name_of(contribution.values.get("person.last_name"))
        if first is not None and last is not None:
            if company:
                folded = (company[0], first.casefold(), last.casefold())
                names.setdefault(folded, (company[0], first, last))
                identity, url = _linkedin_of(provider, contribution)
                if identity is None:  # the one LinkedIn its person holds, if any
                    identity, url = linkedin.get(person[index], (None, None))
                askers.setdefault(folded, {}).setdefault(identity, url)
            continue  # a person we cannot ask about is not a company-level question
        domains.update(dict.fromkeys(company))
    asks = [
        _Ask(
            *names[folded],
            linkedin_url=next(iter(askers[folded].values()))
            if len(askers[folded]) == 1
            else None,
            identities=frozenset(askers[folded]),
        )
        for folded in names
    ]
    # A verdict joins its requester by LinkedIn only when one identity holds the
    # address: a shared address stays nobody's in particular.
    address_linkedin = {
        address: next(iter(held.values()))
        if len(held) == 1 and None not in held
        else None
        for address, held in holders.items()
    }
    return _Plan(list(domains), asks, list(addresses), address_linkedin)


def _person_linkedin(
    provider: str, work_list: tuple[LeadContribution, ...], person: list[int]
) -> dict[int, tuple[str, str]]:
    """Each strongly linked person's LinkedIn identity and text, when it has one only.

    ``person`` labels records linked by a LinkedIn URL or verified-address Match Key,
    transitively (the orchestrator's pruning components: a shared address links no
    one). A record with no LinkedIn of its own is then known by its person's, so two
    records of one person never make one name question ambiguous. A person holding
    two LinkedIn identities lends neither.
    """
    held: dict[int, dict[str, str]] = {}
    for label, contribution in zip(person, work_list, strict=True):
        identity, url = _linkedin_of(provider, contribution)
        if identity is not None and url is not None:
            held.setdefault(label, {}).setdefault(identity, url)
    return {
        label: next(iter(found.items()))
        for label, found in held.items()
        if len(found) == 1
    }


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
