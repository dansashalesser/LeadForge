"""Apollo's match under the tier feed (ADR-0006, follow-up 2026-10-06).

Earlier tiers now feed Apollo's work list, so one person can arrive as several records
(Discovery's, HubSpot's, Hunter's). Apollo asks once per PERSON: records naming the same
address or LinkedIn identity share one ladder, strongest rung first, so a person is
never paid for twice. Apollo's ``organization.primary_domain`` becomes
``company.domain`` (registrable, pinned Public Suffix List), which is what Google
anchors on in the next tier. Field name UNVERIFIED against a capture (live-docs-findings
lists no people/match field table); hand-made stand-ins only.
"""

import json
from typing import Any

import pytest
from structlog.testing import capture_logs

from leadforge.lead_ingestion.adapters.apollo import credits_in
from leadforge.lead_ingestion.transport import TransportResponse

from .test_apollo_enrich_other_sources import (
    ADA_EMAIL,
    ADA_LINKEDIN,
    NO_MATCH,
    OTHER_LINKEDIN,
    Scripted,
    answer,
    apollo,
    by,
    enrich,
    person,
)


def with_domain(response: TransportResponse, domain: object) -> TransportResponse:
    body: dict[str, Any] = json.loads(json.dumps(response.body))  # a deep copy
    body["person"]["organization"]["primary_domain"] = domain
    return TransportResponse(status=200, headers={}, body=body)


# ------------------------------------------------------- one question per person


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_a_person_named_by_two_records_is_asked_once_by_the_strongest_rung() -> (
    None
):
    transport = Scripted(lambda params: answer())
    source = apollo(transport)
    work = enrich(
        person(source="apollo", provider_id="apollo-ada", email=ADA_EMAIL),
        # An earlier tier's record of the same person: a verified address.
        person(source="hunter", email=ADA_EMAIL, email_status="verified"),
    )

    batch = await source.fetch_raw(work)

    assert transport.matches == [{"id": "apollo-ada"}]
    assert credits_in(batch) == 1


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_a_linkedin_record_and_an_address_record_of_one_person_cost_one() -> None:
    transport = Scripted(by("linkedin_url"))
    source = apollo(transport)
    work = enrich(
        person(linkedin=ADA_LINKEDIN, email=ADA_EMAIL),
        person(source="hunter", email=ADA_EMAIL, email_status="verified"),
    )

    with capture_logs() as logs:
        batch = await source.fetch_raw(work)
    found = source.normalize_checked(batch)

    assert transport.matches == [{"linkedin_url": ADA_LINKEDIN}]
    assert credits_in(batch) == 1
    assert found  # the answer still reaches the person
    assert {c.values["person.provider_id"] for c in found} == {"apollo-ada"}
    assert not [e for e in logs if e["event"] == "apollo_match_ambiguous"]


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_a_record_with_no_anchor_of_its_own_rides_on_its_persons_anchor() -> None:
    transport = Scripted(by("email"))
    source = apollo(transport)
    work = enrich(
        # Unverified address, no title or employer: on its own, unattachable.
        person(source="crm", email=ADA_EMAIL),
        person(source="hunter", email=ADA_EMAIL, email_status="verified"),
    )

    with capture_logs() as logs:
        batch = await source.fetch_raw(work)

    assert transport.matches == [{"email": ADA_EMAIL}]
    assert not [e for e in logs if e["event"] == "apollo_enrich_unattachable"]
    assert len(source.normalize_checked(batch)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_two_linkedin_profiles_sharing_an_address_stay_two_people() -> None:
    transport = Scripted(by("linkedin_url"))
    source = apollo(transport)
    work = enrich(
        person(linkedin=ADA_LINKEDIN, email=ADA_EMAIL),
        person(source="web", linkedin=OTHER_LINKEDIN, email=ADA_EMAIL),
    )

    await source.fetch_raw(work)

    assert transport.matches == [
        {"linkedin_url": ADA_LINKEDIN},
        {"linkedin_url": OTHER_LINKEDIN},
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.14
async def test_two_names_sharing_an_address_stay_two_people() -> None:
    # A shared (role) address reported against two distinct names names no one
    # person (8.14). Folding Bob into Ada would climb one merged ladder past Ada's
    # LinkedIn miss onto the shared address and then Bob's NAME, pinning whatever
    # answers about Bob onto Ada's record. Asked as two people, the shared address is
    # ambiguous and neither climbs past it.
    transport = Scripted(lambda params: NO_MATCH)
    source = apollo(transport)
    work = enrich(
        person(linkedin=ADA_LINKEDIN, email="info@acme.com"),
        person(
            source="web",
            first="Bob",
            last="Builder",
            email="info@acme.com",
            email_status="verified",
        ),
    )

    await source.fetch_raw(work)

    assert transport.matches == [{"linkedin_url": ADA_LINKEDIN}]


# ------------------------------------------------ the company domain Apollo found


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_the_matched_organizations_primary_domain_is_the_company_domain() -> None:
    transport = Scripted(lambda params: with_domain(answer(), "WWW.Acme-Data.COM"))
    source = apollo(transport)

    batch = await source.fetch_raw(
        enrich(person(source="apollo", provider_id="apollo-ada", domain=None))
    )
    [found] = source.normalize_checked(batch)

    assert found.values["company.domain"] == "acme-data.com"
    [record] = [p for p in found.provenance if p.canonical_path == "company.domain"]
    assert record.raw_field_path == "person.organization.primary_domain"


# Verifies: specs/lead-source-adapters/requirements.md#12.3
@pytest.mark.parametrize("unusable", ["gmail.com", "localhost", "", "com"])
async def test_an_unusable_primary_domain_contributes_no_company_domain(
    unusable: str,
) -> None:
    transport = Scripted(lambda params: with_domain(answer(), unusable))
    source = apollo(transport)
    batch = await source.fetch_raw(
        enrich(person(source="apollo", provider_id="apollo-ada", domain=None))
    )
    [found] = source.normalize_checked(batch)
    assert "company.domain" not in found.values


# Verifies: specs/lead-source-adapters/requirements.md#12.3
async def test_the_requesters_asked_domain_wins_over_apollos_primary_domain() -> None:
    transport = Scripted(by("first_name", with_domain(answer(), "other.com")))
    source = apollo(transport)

    batch = await source.fetch_raw(
        enrich(person(employer="Acme Corp", title="CTO"))  # name + acme.com anchor
    )
    [found] = source.normalize_checked(batch)

    assert found.values["company.domain"] == "acme.com"  # the echo, never Apollo's


# Verifies: specs/lead-source-adapters/requirements.md#8.3
async def test_a_weak_name_hit_contributes_no_company_domain_of_apollos() -> None:
    # A name + company-name hit is weak: Apollo's domain with Apollo's name would form
    # a name + domain Match Key that could join a different person (8.3).
    transport = Scripted(by("organization_name", with_domain(answer(), "acme.com")))
    source = apollo(transport)

    batch = await source.fetch_raw(
        enrich(person(domain=None, linkedin=ADA_LINKEDIN, employer="Acme Corp"))
    )
    found = source.normalize_checked(batch)

    assert transport.matches[-1].get("organization_name") == "Acme Corp"
    assert found
    assert all("company.domain" not in c.values for c in found)


# Verifies: specs/lead-source-adapters/requirements.md#12.9
async def test_one_person_gets_one_attachment_on_their_strongest_anchor() -> None:
    # Two anchors for one person would put one answer on two records the merge may
    # not join (a name anchor and an address anchor): the answer would split the Lead.
    transport = Scripted(by("email"))
    source = apollo(transport)
    work = enrich(
        person(source="crm", email=ADA_EMAIL, title="CTO"),  # a name anchor
        person(source="hunter", email=ADA_EMAIL, email_status="verified"),
    )

    batch = await source.fetch_raw(work)
    [found] = source.normalize_checked(batch)

    assert transport.matches == [{"email": ADA_EMAIL}]
    assert found.values["person.email"] == ADA_EMAIL  # the address anchor's echo
