"""Provider Suppression, end to end over the real adapters (task 19.3).

Real ``HubSpotSource``, ``HunterSource`` and ``ApolloSource`` behind scripted
transports, the real orchestrator, then the real clustering and projection. A stub
supplies Discovery because no real Discovery adapter yields an email (Apollo's
search returns none), and HubSpot looks leads up by email only.
"""

from collections.abc import Mapping
from typing import Any, ClassVar

import pytest
from structlog.testing import capture_logs

from leadforge.lead_ingestion.adapters.apollo import ApolloSource
from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.adapters.hunter import HunterSource
from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Endpoint,
    LeadContribution,
    RawBatch,
)
from leadforge.lead_ingestion.clustering import cluster_contributions
from leadforge.lead_ingestion.compliance import blocked_identities
from leadforge.lead_ingestion.errors import SourceTimedOut
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import (
    IngestionOrchestrator,
    SourceResult,
    SourceStatus,
)
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.projection import ProjectionResult, project_lead
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.tests.adapters import test_apollo_source as apollo_t
from leadforge.lead_ingestion.tests.adapters import test_hubspot_source as hubspot_t
from leadforge.lead_ingestion.tests.adapters import test_hunter_source as hunter_t
from leadforge.lead_ingestion.tests.test_orchestrator_enrichment_order import (
    contribution,
)
from leadforge.lead_ingestion.tests.test_orchestrator_phases import (
    REQUEST,
    SEARCH,
    Probe,
    Source,
    live,
)
from leadforge.lead_ingestion.transport import TransportResponse

ADA = "ada@example.com"
BOB = "bob@other.org"


class Discovery(Source):
    """Two leads that carry an email, as a CRM-aware Discovery source would."""

    name: ClassVar[str] = "discovery"
    capabilities = SEARCH

    async def fetch_raw(self, request: Any) -> RawBatch:
        return RawBatch(source_name=self.name, payload={"kind": request.kind})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return [
            contribution(
                "apollo",  # Apollo's enrichment matches only its own leads
                {
                    "person.provider_id": pid,
                    "person.email": email,
                    "person.full_name": name,
                    "company.domain": domain,
                },
            )
            for pid, email, name, domain in (
                ("p-ada", ADA, "Ada Lovelace", "example.com"),
                ("p-bob", BOB, "Bob Babbage", "other.org"),
            )
        ]


class ApolloTransport:
    """Records every call; search is empty, match answers a bare person."""

    def __init__(self) -> None:
        self.paths: list[str] = []

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        self.paths.append(endpoint.path)
        if endpoint.path == apollo_t.SEARCH_PATH:
            return apollo_t.page(0, 0)
        return apollo_t.matched()


def hubspot_answers(opted_out: str | None) -> hubspot_t.Scripted:
    def respond(endpoint: Endpoint, body: Mapping[str, object]) -> TransportResponse:
        if endpoint.path == hubspot_t.CONTACT_PATH and ADA in str(body):
            return hubspot_t.answers([hubspot_t.contact(optout=opted_out)])(
                endpoint, body
            )
        return hubspot_t.answers([])(endpoint, body)

    return hubspot_t.Scripted(respond)


class Run:
    discovery: ClassVar[type[Source]] = Discovery

    def __init__(
        self,
        hubspot: hubspot_t.Scripted,
        hunter: hunter_t.Routed,
        apollo: ApolloTransport,
    ) -> None:
        self.hubspot, self.hunter, self.apollo = hubspot, hunter, apollo
        self.results: tuple[SourceResult, ...] = ()
        self.logs: list[Any] = []

    async def go(self) -> None:
        probe = Probe()

        def build(
            source_class: type[BaseLeadSource],
            mode: DataMode,
            pacing: SourcePacing | None,
        ) -> BaseLeadSource:
            if source_class is HubSpotSource:
                return HubSpotSource(
                    mode, transport=self.hubspot, environ=hubspot_t.ENV
                )
            if source_class is HunterSource:
                return HunterSource(mode, transport=self.hunter, environ=hunter_t.ENV)
            if source_class is ApolloSource:
                return ApolloSource(
                    mode,
                    transport=self.apollo,
                    vocabulary=apollo_t.ONE_TERM,
                    environ=apollo_t.ENV,
                )
            assert issubclass(source_class, Source)
            return source_class(mode, probe)

        classes: list[type] = [
            self.discovery,
            HubSpotSource,
            HunterSource,
            ApolloSource,
        ]
        with capture_logs() as logs:
            self.results = await IngestionOrchestrator(
                SourceRegistry(classes),
                resolve_mode=live,
                build_source=build,
                max_concurrent_sources=4,
                run_timeout_s=30,
            ).run(REQUEST)
        self.logs = logs

    def contributions(self) -> list[LeadContribution]:
        return [c for r in self.results for c in (r.contributions or ())]

    def projected(self) -> list[ProjectionResult]:
        everything = self.contributions()
        blocked = blocked_identities(everything)
        return [
            project_lead(cluster, {}, blocked=blocked)
            for cluster in cluster_contributions(everything)
        ]

    def lead_of(self, address: str) -> list[ProjectionResult]:
        return [
            p
            for p in self.projected()
            if p.lead is not None and str(p.lead.email) == address
        ]


def echo_verdict(params: Mapping[str, object]) -> TransportResponse:
    """A valid verdict for the address asked, as Hunter's verifier answers."""
    return hunter_t.verdict(address=str(params["email"]))


def hunter_routed(**answers: Any) -> hunter_t.Routed:
    # The verifier echoes the asked address: since tiers feed later tiers (ADR-0006),
    # a stand-in answering a different address would be a different person reaching
    # Apollo's paid match.
    return hunter_t.Routed(**{"verifier": echo_verdict, **answers})


def asked_addresses(transport: hunter_t.Routed) -> list[str]:
    return [
        str(params["email"])
        for endpoint, params, _ in transport.calls
        if endpoint.path == hunter_t.VERIFIER_PATH
    ]


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    import socket

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("a suppression test opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket.socket, "sendto", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


# Verifies: specs/lead-source-adapters/requirements.md#13.7
async def test_a_hubspot_opt_out_flags_the_lead_and_keeps_it_from_every_paid_tier() -> (
    None
):
    run = Run(hubspot_answers("true"), hunter_routed(), ApolloTransport())
    await run.go()

    assert all(r.outcome.status is SourceStatus.OK for r in run.results)
    assert asked_addresses(run.hunter) == [BOB]  # never the opted-out address
    match_calls = [p for p in run.apollo.paths if p == apollo_t.MATCH_PATH]
    assert len(match_calls) == 1  # one paid match, for bob only

    # Every Lead holding the address (the Discovery one and HubSpot's own report) is
    # flagged, with the source that set it recorded.
    holders = run.lead_of(ADA)
    assert len(holders) >= 2
    for ada in holders:
        assert ada.lead is not None
        assert (ada.lead.suppressed, ada.lead.opt_out) == (True, True)
        assert ada.compliance_sources == ("hubspot",)
    for bob in run.lead_of(BOB):
        assert bob.lead is not None
        assert (bob.lead.suppressed, bob.lead.opt_out) == (False, False)
        assert bob.compliance_sources == ()


# Verifies: specs/lead-source-adapters/requirements.md#13.7
async def test_an_unreadable_hubspot_opt_out_value_still_keeps_the_lead_from_paid() -> (
    None
):
    run = Run(hubspot_answers("maybe"), hunter_routed(), ApolloTransport())
    await run.go()
    assert all(r.outcome.status is SourceStatus.OK for r in run.results)
    assert asked_addresses(run.hunter) == [BOB]
    for ada in run.lead_of(ADA):
        assert ada.lead is not None
        assert ada.lead.suppressed is True


# Verifies: specs/lead-source-adapters/requirements.md#13.7
async def test_a_hubspot_contact_that_is_not_opted_out_flags_nothing() -> None:
    run = Run(hubspot_answers("false"), hunter_routed(), ApolloTransport())
    await run.go()
    assert sorted(asked_addresses(run.hunter)) == [ADA, BOB]
    for p in run.projected():
        assert p.lead is not None
        assert (p.lead.suppressed, p.lead.opt_out) == (False, False)


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_hunter_451_on_one_address_flags_that_lead_and_leaks_nothing() -> None:
    def verifier(params: Mapping[str, object]) -> TransportResponse:
        if params["email"] == ADA:
            return hunter_t.restricted()
        return hunter_t.verdict(address=str(params["email"]))

    run = Run(
        hubspot_answers("false"), hunter_routed(verifier=verifier), ApolloTransport()
    )
    await run.go()

    assert all(r.outcome.status is SourceStatus.OK for r in run.results)
    flagged_by_hunter = [
        c
        for c in run.contributions()
        if c.source_name == "hunter" and c.values.get("suppressed") is True
    ]
    assert [set(c.values) for c in flagged_by_hunter] == [
        {"person.email", "suppressed"}
    ]
    # No contact datum from Hunter for that address beyond the flag itself.
    for c in run.contributions():
        if c.source_name == "hunter" and c.values.get("person.email") == ADA:
            assert set(c.values) == {"person.email", "suppressed"}

    # Every projected lead holding the address is suppressed, whichever source it came
    # from; the restriction is suppressed only (not opt_out), like the adapter says.
    holders = run.lead_of(ADA)
    assert holders
    for p in holders:
        assert p.lead is not None
        assert p.lead.suppressed is True
        assert p.lead.opt_out is False
        assert "hunter" in p.compliance_sources
    for bob in run.lead_of(BOB):
        assert bob.lead is not None
        assert bob.lead.suppressed is False


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_restricted_address_in_a_domain_search_answer_is_dropped() -> None:
    """15.3 re-proved: another answer in the batch cannot bring the address back."""

    def verifier(params: Mapping[str, object]) -> TransportResponse:
        if params["email"] == ADA:
            return hunter_t.restricted()
        return hunter_t.verdict(address=str(params["email"]))

    def search(params: Mapping[str, object]) -> TransportResponse:
        return hunter_t.found(
            str(params["domain"]),
            [hunter_t.email(ADA), hunter_t.email("dan@elsewhere.net")],
        )

    class Companies(Discovery):
        name: ClassVar[str] = "discovery"

        def normalize(self, raw: RawBatch) -> list[LeadContribution]:
            return [
                *super().normalize(raw),
                contribution(
                    "discovery",
                    {"person.full_name": "Dan", "company.domain": "elsewhere.net"},
                ),
            ]

    transport = hunter_routed(verifier=verifier, search=search)
    run = Run(hubspot_answers("false"), transport, ApolloTransport())
    probe = Probe()
    run_classes: list[type] = [Companies, HubSpotSource, HunterSource]

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        if source_class is HubSpotSource:
            return HubSpotSource(mode, transport=run.hubspot, environ=hubspot_t.ENV)
        if source_class is HunterSource:
            return HunterSource(mode, transport=transport, environ=hunter_t.ENV)
        assert issubclass(source_class, Source)
        return source_class(mode, probe)

    run.results = await IngestionOrchestrator(
        SourceRegistry(run_classes),
        resolve_mode=live,
        build_source=build,
        max_concurrent_sources=4,
        run_timeout_s=30,
    ).run(REQUEST)

    hunter_values = [c.values for c in run.contributions() if c.source_name == "hunter"]
    ada_rows = [v for v in hunter_values if v.get("person.email") == ADA]
    assert ada_rows == [{"person.email": ADA, "suppressed": True}]
    for p in run.lead_of(ADA):
        assert p.lead is not None
        assert p.lead.suppressed is True


# Verifies: specs/lead-source-adapters/requirements.md#11.4
async def test_the_compliance_path_never_puts_an_address_in_a_log_or_an_error() -> None:
    def verifier(params: Mapping[str, object]) -> TransportResponse:
        if params["email"] == ADA:
            return hunter_t.restricted()
        return hunter_t.verdict(address=str(params["email"]))

    for hubspot in (hubspot_answers("true"), hubspot_answers("maybe")):
        run = Run(hubspot, hunter_routed(verifier=verifier), ApolloTransport())
        await run.go()
        texts = [str(run.logs), *(str(r.outcome) for r in run.results)]
        texts += [repr(p) for p in run.projected()]
        for text in texts:
            for secret in (ADA, BOB, "Lovelace", hubspot_t.TOKEN, hunter_t.KEY):
                assert secret not in text


# --- Hunter runs before Apollo (user decision, 2026-10-06) -------------------------
#
# Hunter declares yields_suppression, so it runs in a paid tier ahead of Apollo: a
# Hunter 451 on a person prunes them from the work list before Apollo's paid match is
# asked about them. Anything short of a 451 (nothing found, an error, a timeout) must
# leave the person on the list, so Apollo is still asked.

ADA_LINKEDIN = "https://www.linkedin.com/in/ada-lovelace"


class NamedDiscovery(Discovery):
    """Ada with a name and a LinkedIn URL but no address (the finder route)."""

    name: ClassVar[str] = "discovery"

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        bob = super().normalize(raw)[1]
        ada = contribution(
            "apollo",
            {
                "person.provider_id": "p-ada",
                "person.first_name": "Ada",
                "person.last_name": "Lovelace",
                "person.linkedin_url": ADA_LINKEDIN,
                "company.domain": "example.com",
            },
        )
        return [ada, bob]


class ApolloMatches(ApolloTransport):
    """Also records which person each paid match asked about."""

    def __init__(self) -> None:
        super().__init__()
        self.ids: list[str] = []

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        if endpoint.path == apollo_t.MATCH_PATH:
            self.ids.append(str((params or {}).get("id")))
        return await super().send(
            endpoint, params=params, json_body=json_body, headers=headers
        )


class NamedRun(Run):
    discovery: ClassVar[type[Source]] = NamedDiscovery


def ada_only(answer: TransportResponse) -> Any:
    """A Hunter responder: ``answer`` for Ada, an ordinary verdict for anyone else."""

    def respond(params: Mapping[str, object]) -> TransportResponse:
        if params.get("email") == ADA or params.get("first_name") == "Ada":
            return answer
        return hunter_t.verdict(address=str(params.get("email", BOB)))

    return respond


def outcome_of(run: Run, name: str) -> SourceStatus:
    return next(r.outcome.status for r in run.results if r.source_name == name)


# Verifies: specs/lead-source-adapters/requirements.md#16.6
@pytest.mark.parametrize("run_class", [Run, NamedRun], ids=["verifier", "finder"])
async def test_a_hunter_451_prunes_that_person_before_apollos_paid_match(
    run_class: type[Run],
) -> None:
    refuse = ada_only(hunter_t.restricted())
    apollo = ApolloMatches()
    run = run_class(
        hubspot_answers("false"),
        hunter_routed(verifier=refuse, finder=refuse),
        apollo,
    )
    await run.go()
    assert outcome_of(run, "hunter") is SourceStatus.OK
    assert apollo.ids == ["p-bob"]  # never asked about the restricted person


class ColleagueDiscovery(Discovery):
    """Ada and a colleague at the SAME company, both on the finder route."""

    name: ClassVar[str] = "discovery"

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return [
            contribution(
                "apollo",
                {
                    "person.provider_id": pid,
                    "person.first_name": first,
                    "person.last_name": last,
                    "person.linkedin_url": f"https://www.linkedin.com/in/{slug}",
                    "company.domain": "example.com",
                },
            )
            for pid, first, last, slug in (
                ("p-ada", "Ada", "Lovelace", "ada-lovelace"),
                ("p-cy", "Cy", "Babbage", "cy-babbage"),
            )
        ]


class ColleagueRun(Run):
    discovery: ClassVar[type[Source]] = ColleagueDiscovery


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_hunter_451_prunes_one_person_not_their_colleagues() -> None:
    def respond(params: Mapping[str, object]) -> TransportResponse:
        if params.get("first_name") == "Ada":
            return hunter_t.restricted()
        return hunter_t.found_one(None)

    apollo = ApolloMatches()
    run = ColleagueRun(hubspot_answers("false"), hunter_routed(finder=respond), apollo)
    await run.go()
    assert outcome_of(run, "hunter") is SourceStatus.OK
    assert apollo.ids == ["p-cy"]  # the same company, a different person: still asked


# Verifies: specs/lead-source-adapters/requirements.md#16.6
async def test_a_finder_that_finds_nothing_leaves_the_person_for_apollo() -> None:
    apollo = ApolloMatches()
    nothing = ada_only(hunter_t.found_one(None))
    run = NamedRun(hubspot_answers("false"), hunter_routed(finder=nothing), apollo)
    await run.go()
    assert outcome_of(run, "hunter") is SourceStatus.OK
    assert sorted(apollo.ids) == ["p-ada", "p-bob"]


class TimingOut(hunter_t.Routed):
    """Every Hunter call times out at the transport, as ``RestTransport`` reports."""

    async def send(
        self,
        endpoint: Endpoint,
        *,
        params: Mapping[str, object] | None,
        json_body: Mapping[str, object] | None,
        headers: Mapping[str, str],
    ) -> TransportResponse:
        raise SourceTimedOut("hunter", f"{endpoint.path}: ReadTimeout")


# Verifies: specs/lead-source-adapters/requirements.md#16.6
@pytest.mark.parametrize(
    ("run_class", "hunter"),
    [
        (Run, hunter_t.answering(404)),
        (NamedRun, hunter_t.answering(404)),
        (NamedRun, hunter_t.answering(429)),
        (NamedRun, TimingOut()),
    ],
    ids=["verifier_404", "finder_404", "finder_429", "finder_timeout"],
)
async def test_a_hunter_failure_never_prunes_a_person_from_apollo(
    run_class: type[Run], hunter: hunter_t.Routed
) -> None:
    apollo = ApolloMatches()
    run = run_class(hubspot_answers("false"), hunter, apollo)
    await run.go()
    assert outcome_of(run, "hunter") is not SourceStatus.OK
    assert sorted(apollo.ids) == ["p-ada", "p-bob"]
