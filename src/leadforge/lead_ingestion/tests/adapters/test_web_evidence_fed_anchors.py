"""Company anchors over a fed work list (ADR-0006, follow-up 2026-10-06).

Since earlier Enrichment tiers feed Google's work list, records whose company domain
is only an echo of what the requester asked (raw path ``asked.*``) reach it too. They
still name the company (so an anchor exists) but are not an independent source naming
it, so they add no corroboration; a domain a source observed itself (Apollo's
``primary_domain``) does.
"""

from datetime import UTC, datetime

from leadforge.lead_ingestion.adapters.web_evidence import company_anchors
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.models import ConfidenceOrigin, DataMode, FieldProvenance


def record(source: str, domain: str, raw_path: str) -> LeadContribution:
    return LeadContribution(
        source_name=source,
        values={"company.domain": domain},
        provenance=(
            FieldProvenance(
                canonical_path="company.domain",
                source_name=source,
                data_mode=DataMode.SYNTHETIC,
                fetched_at=datetime(2026, 10, 6, tzinfo=UTC),
                raw_field_path=raw_path,
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            ),
        ),
    )


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_an_echoed_company_domain_names_the_company_but_does_not_corroborate() -> None:
    work = (
        record("discovery", "acme.com", "organization.domain"),
        record("apollo", "acme.com", "asked.domain"),
        record("hunter", "acme.com", "asked.domain"),
    )

    [anchor] = company_anchors(work, exclude_source="google_search")

    assert anchor.domains == ("acme.com",)
    assert anchor.corroborating_sources == 1


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_an_echo_only_company_is_still_an_anchor() -> None:
    [anchor] = company_anchors(
        (record("hunter", "acme.com", "asked.domain"),), exclude_source="google_search"
    )
    assert anchor.domains == ("acme.com",)
    assert anchor.corroborating_sources == 0


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_a_domain_a_source_observed_itself_corroborates() -> None:
    work = (
        record("discovery", "acme.com", "organization.domain"),
        record("apollo", "acme.com", "person.organization.primary_domain"),
    )

    [anchor] = company_anchors(work, exclude_source="google_search")

    assert anchor.corroborating_sources == 2
