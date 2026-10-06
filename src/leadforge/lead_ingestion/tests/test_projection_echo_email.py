"""An echoed ``asked.email`` never hides a CRM source's own observed address (8.7).

Reproduction (CRM request-echo follow-up, self-review (e)): a requester holds a
verified address; a CRM source answers with the same address under its bare ``email``
path and also carries the address it was asked about as a request echo under
``person.email``.
The echo used to block re-keying the bare ``email`` to ``person.email``, so the CRM
source's own observation was lost and the agreement count was 1 instead of 2. Echoed
fields still never add agreement themselves.
"""

from datetime import UTC, datetime
from typing import Any

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import IdentityCluster
from leadforge.lead_ingestion.models import (
    REQUEST_ECHO_PREFIX,
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
)
from leadforge.lead_ingestion.projection import project_lead

NOW = datetime(2026, 10, 6, 9, 0, tzinfo=UTC)
RANKS = {"crm": 1, "enricher": 2}


def _contribution(source: str, fields: dict[str, tuple[Any, str]]) -> LeadContribution:
    return LeadContribution(
        source_name=source,
        values={path: value for path, (value, _) in fields.items()},
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name=source,
                data_mode=DataMode.SYNTHETIC,
                fetched_at=NOW,
                raw_field_path=raw,
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            )
            for path, (_, raw) in fields.items()
        ),
    )


REQUESTER = _contribution(
    "enricher",
    {
        "person.email": ("ada@example.com", "email"),
        "person.email_status": ("verified", "email_status"),
    },
)


def _agreement(*members: LeadContribution) -> dict[str, int]:
    return dict(project_lead(IdentityCluster("c", members), RANKS).agreement)


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_an_echoed_email_does_not_hide_the_crm_sources_own_address() -> None:
    crm = _contribution(
        "crm",
        {
            "person.email": ("ada@example.com", f"{REQUEST_ECHO_PREFIX}email"),
            "email": ("ada@example.com", "properties.email"),
        },
    )
    result = project_lead(IdentityCluster("c", (REQUESTER, crm)), RANKS)
    assert dict(result.agreement)["person.email"] == 2
    raws = {
        (p.source_name, p.raw_field_path)
        for p in result.provenance
        if p.canonical_path == "person.email"
    }
    assert raws == {("enricher", "email"), ("crm", "properties.email")}


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_an_echo_alone_still_adds_no_agreement() -> None:
    crm = _contribution(
        "crm", {"person.email": ("ada@example.com", f"{REQUEST_ECHO_PREFIX}email")}
    )
    assert _agreement(REQUESTER, crm)["person.email"] == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_an_observed_person_email_still_keeps_the_bare_path_apart() -> None:
    crm = _contribution(
        "crm",
        {
            "person.email": ("ada@example.com", "properties.primary"),
            "email": ("other@example.com", "properties.email"),
        },
    )
    result = project_lead(IdentityCluster("c", (REQUESTER, crm)), RANKS)
    assert dict(result.agreement)["person.email"] == 2
    assert "email" in dict(result.agreement)


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_an_echo_naming_another_address_never_replaces_the_observed_one() -> None:
    # Asked about one address, the CRM answered with the contact's own (another).
    crm = _contribution(
        "crm",
        {
            "email": ("ada@example.com", "properties.email"),
            "person.email": ("ada.old@example.com", f"{REQUEST_ECHO_PREFIX}email"),
        },
    )
    result = project_lead(IdentityCluster("c", (crm,)), RANKS)
    assert result.lead is not None
    assert str(result.lead.email) == "ada@example.com"
    assert dict(result.agreement)["person.email"] == 1
