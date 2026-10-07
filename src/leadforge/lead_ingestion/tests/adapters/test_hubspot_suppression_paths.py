"""Suppression pruning against the paths the real adapters emit (6.10, 13.7).

Apollo contributes ``person.email`` / ``person.linkedin_url``; HubSpot reports a
suppression under the bare ``email`` path. Pruning must match across both spellings, or
a HubSpot opt-out would never keep a lead from a later Credit-bearing source.
"""

from collections.abc import Mapping
from typing import ClassVar

from leadforge.lead_ingestion.adapters.hubspot import HubSpotSource
from leadforge.lead_ingestion.base_source import (
    BaseLeadSource,
    Endpoint,
    EnrichmentRequest,
    LeadContribution,
    RawBatch,
)
from leadforge.lead_ingestion.models import DataMode
from leadforge.lead_ingestion.orchestrator import IngestionOrchestrator, prune_flagged
from leadforge.lead_ingestion.pacing import SourcePacing
from leadforge.lead_ingestion.registry import SourceRegistry
from leadforge.lead_ingestion.tests.adapters.test_hubspot_source import (
    CONTACT_PATH,
    ENV,
    Scripted,
    answers,
    contact,
)
from leadforge.lead_ingestion.tests.test_orchestrator_enrichment_order import (
    PAID,
    contribution,
)
from leadforge.lead_ingestion.tests.test_orchestrator_phases import (
    ENRICH,
    REQUEST,
    SEARCH,
    Probe,
    Source,
    live,
)
from leadforge.lead_ingestion.transport import TransportResponse


def values(
    work: tuple[LeadContribution, ...],
) -> list[Mapping[str, object]]:
    return [c.values for c in work]


def test_a_bare_email_report_prunes_a_canonical_path_work_list_entry() -> None:
    work = (
        contribution("apollo", {"person.email": "Ada@Example.com"}),
        contribution("apollo", {"person.email": "bob@example.com"}),
    )
    report = contribution("hubspot", {"email": "ada@example.com", "suppressed": True})
    assert values(prune_flagged(work, (report,))) == [
        {"person.email": "bob@example.com"}
    ]


def test_a_canonical_path_report_prunes_a_bare_email_work_list_entry() -> None:
    work = (contribution("x", {"email": "ada@example.com"}),)
    report = contribution(
        "hunter", {"person.email": "ADA@example.com", "suppressed": True}
    )
    assert prune_flagged(work, (report,)) == ()


def test_a_linkedin_url_matches_across_paths_and_query_strings() -> None:
    work = (
        contribution(
            "apollo", {"person.linkedin_url": "https://www.linkedin.com/in/Ada/?utm=1"}
        ),
    )
    report = contribution(
        "hubspot",
        {"linkedin_url": "http://www.linkedin.com/in/ada", "opt_out": True},
    )
    assert prune_flagged(work, (report,)) == ()


def test_an_email_never_matches_a_linkedin_url_of_the_same_text() -> None:
    work = (contribution("a", {"person.email": "x"}),)
    report = contribution("h", {"linkedin_url": "x", "suppressed": True})
    assert prune_flagged(work, (report,)) == work


class Discovery(Source):
    """Yields Apollo-shaped contributions (``person.email``)."""

    async def fetch_raw(self, request: object) -> RawBatch:
        return RawBatch(source_name=self.name, payload={"kind": "search"})

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return [
            contribution("apollo", {"person.email": e})
            for e in ("ada@example.com", "bob@example.com")
        ]


class Paid(Source):
    seen: ClassVar[list[str]] = []

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        return []


async def test_a_real_hubspot_opt_out_keeps_the_lead_from_a_later_paid_tier() -> None:
    probe = Probe()

    def lookup_only_ada(
        endpoint: Endpoint, body: Mapping[str, object]
    ) -> TransportResponse:
        filters = str(body)
        if endpoint.path == CONTACT_PATH and "ada@example.com" not in filters:
            return answers([])(endpoint, body)
        return answers([contact(optout="true")])(endpoint, body)

    transport = Scripted(lookup_only_ada)

    classes: list[type] = [
        type("D", (Discovery,), {"name": "apollo-search", "capabilities": SEARCH}),
        HubSpotSource,
        type("P", (Paid,), {"name": "paid", "capabilities": ENRICH, **PAID}),
    ]

    def build(
        source_class: type[BaseLeadSource], mode: DataMode, pacing: SourcePacing | None
    ) -> BaseLeadSource:
        if source_class is HubSpotSource:
            return HubSpotSource(mode, transport=transport, environ=ENV)
        assert issubclass(source_class, Source)
        return source_class(mode, probe)

    await IngestionOrchestrator(
        SourceRegistry(classes),
        resolve_mode=live,
        build_source=build,
        max_concurrent_sources=4,
        run_timeout_s=30,
    ).run(REQUEST)

    assert transport.calls, "HubSpot looked nothing up for an Apollo-shaped work list"
    request = probe.requests["paid:enrich"]
    assert isinstance(request, EnrichmentRequest)
    # Since tiers feed later tiers (ADR-0006), HubSpot's own record of bob (found or
    # not) reaches the paid tier too; ada, opted out, reaches it in no record.
    assert {
        c.values.get("person.email", c.values.get("email")) for c in request.work_list
    } == {"bob@example.com"}
