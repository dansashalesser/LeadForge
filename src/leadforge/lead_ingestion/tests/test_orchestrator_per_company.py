"""Per-company sources are called once per distinct Company Signal (task 11.7, 6.11).

A source declaring ``charge_unit: per_company`` is billed per company, so the
Enrichment work list it receives holds one Lead per distinct company, not one per Lead.
A company is identified by the domain set a work-list contribution carries at
``company.domain``; clustering overlapping sets is task 16.9 and is not built here.
The sources are throwaway subclasses that make one provider call per work-list item,
as an adapter does.
"""

import pytest

from leadforge.lead_ingestion.base_source import (
    ChargeUnit,
    CostClass,
    EnrichmentRequest,
    LeadContribution,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.orchestrator import per_company_work_list
from leadforge.lead_ingestion.tests.test_orchestrator_enrichment_order import (
    FREE_SUPPRESSING,
    Lookup,
    contribution,
    emails,
    make,
)
from leadforge.lead_ingestion.tests.test_orchestrator_phases import (
    ENRICH,
    REQUEST,
    SEARCH,
    Probe,
    orchestrator,
)

PER_COMPANY = {
    "cost_class": CostClass.PAID,
    "charge_unit": ChargeUnit.PER_COMPANY,
    "yields_suppression": False,
}
PER_LEAD = {**PER_COMPANY, "charge_unit": ChargeUnit.PER_LEAD}
PER_CALL = {**PER_COMPANY, "charge_unit": ChargeUnit.PER_CALL}


class Billed(Lookup):
    """Makes one provider call per work-list item, recorded as a probe event."""

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        if isinstance(request, EnrichmentRequest):
            for _ in request.work_list:
                self.probe.events.append(f"provider_call:{self.name}")
        return await super().fetch_raw(request)


def billed(name: str, **attrs: object) -> type[Lookup]:
    return type(f"B_{name}", (Billed,), {"name": name, "capabilities": ENRICH, **attrs})


def lead(email: str, domain: object | None = None) -> dict[str, object]:
    return {"email": email} | ({} if domain is None else {"company.domain": domain})


def calls(probe: Probe, source: str) -> int:
    return probe.events.count(f"provider_call:{source}")


def work(*leads: dict[str, object]) -> tuple[LeadContribution, ...]:
    return tuple(contribution("s", v) for v in leads)


# Verifies: specs/lead-source-adapters/requirements.md#6.11
async def test_n_leads_at_one_company_cost_one_per_company_call_n_per_lead() -> None:
    probe = Probe()
    classes = [
        billed("per_company", **PER_COMPANY),
        billed("per_lead", **PER_LEAD),
        make(
            "s",
            SEARCH,
            found_values=tuple(lead(f"p{i}@acme.com", "acme.com") for i in range(5)),
        ),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert calls(probe, "per_company") == 1
    assert calls(probe, "per_lead") == 5


# Verifies: specs/lead-source-adapters/requirements.md#6.11
async def test_two_leads_at_one_company_cause_exactly_one_per_company_call() -> None:
    probe = Probe()
    classes = [
        billed("per_company", **PER_COMPANY),
        make(
            "s",
            SEARCH,
            found_values=(
                lead("a@acme.com", "acme.com"),
                lead("b@acme.com", "acme.com"),
            ),
        ),
    ]

    results = await orchestrator(classes, probe).run(REQUEST)

    assert calls(probe, "per_company") == 1
    assert emails(probe, "per_company") == ["a@acme.com"]
    # The orchestrator still invokes the source once; the ledger counts that call.
    enrichment = next(r for r in results if r.source_name == "per_company")
    assert enrichment.outcome.attempted == 1


# Verifies: specs/lead-source-adapters/requirements.md#6.11
async def test_a_per_call_source_still_receives_every_lead() -> None:
    probe = Probe()
    classes = [
        billed("per_call", **PER_CALL),
        make(
            "s",
            SEARCH,
            found_values=(
                lead("a@acme.com", "acme.com"),
                lead("b@acme.com", "acme.com"),
            ),
        ),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert emails(probe, "per_call") == ["a@acme.com", "b@acme.com"]


# Verifies: specs/lead-source-adapters/requirements.md#6.11
async def test_per_company_deduplication_follows_suppression_pruning() -> None:
    probe = Probe()
    classes = [
        billed("per_company", **PER_COMPANY),
        make("free", ENRICH, reports={"a@acme.com": "suppressed"}, **FREE_SUPPRESSING),
        make(
            "s",
            SEARCH,
            found_values=(
                lead("a@acme.com", "acme.com"),
                lead("b@acme.com", "acme.com"),
            ),
        ),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert emails(probe, "per_company") == ["b@acme.com"]


# Verifies: specs/lead-source-adapters/requirements.md#6.11
def test_each_distinct_company_keeps_its_first_lead_in_work_list_order() -> None:
    items = work(
        lead("a1@a.com", "a.com"),
        lead("b1@b.com", "b.com"),
        lead("a2@a.com", "a.com"),
        lead("c1@c.com", "c.com"),
        lead("b2@b.com", "b.com"),
    )

    kept = per_company_work_list(items)

    assert [c.values["email"] for c in kept] == ["a1@a.com", "b1@b.com", "c1@c.com"]
    assert kept == per_company_work_list(items)  # deterministic
    assert all(any(k is i for i in items) for k in kept)  # the same objects, untouched
    assert len(items) == 5


# Verifies: specs/lead-source-adapters/requirements.md#6.11
def test_a_company_is_its_domain_set_ignoring_case_blanks_and_order() -> None:
    items = work(
        lead("a@x", ("Acme.com", "acme.io")),
        lead("b@x", ["acme.io ", "ACME.COM"]),
        lead("c@x", "ACME.com"),
        lead("d@x", frozenset({"acme.com", "acme.io"})),
    )

    # The first two (and the fourth) are the same two-domain set; "ACME.com" alone is a
    # different set, because overlap clustering is task 16.9.
    assert [c.values["email"] for c in per_company_work_list(items)] == [
        "a@x",
        "c@x",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#6.11
def test_leads_with_no_known_company_are_each_kept_never_merged() -> None:
    items = work(
        lead("a@x"),
        lead("b@x"),
        lead("c@x", "   "),
        lead("d@x", ()),
        lead("e@x", "acme.com"),
        lead("f@x", "acme.com"),
    )

    assert [c.values["email"] for c in per_company_work_list(items)] == [
        "a@x",
        "b@x",
        "c@x",
        "d@x",
        "e@x",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#6.11
def test_blank_domain_entries_are_not_part_of_the_company() -> None:
    items = work(
        lead("a@x", ("acme.com", "  ")),
        lead("b@x", "acme.com"),
        lead("c@x", ("", " ")),
        lead("d@x", ("  ", "")),
    )

    # A blank entry neither distinguishes a company nor makes blank-only Leads one.
    assert [c.values["email"] for c in per_company_work_list(items)] == [
        "a@x",
        "c@x",
        "d@x",
    ]


# Verifies: specs/lead-source-adapters/requirements.md#6.11
def test_an_empty_work_list_stays_empty() -> None:
    assert per_company_work_list(()) == ()


# Verifies: specs/lead-source-adapters/requirements.md#6.11
@pytest.mark.parametrize("bad", [42, ("acme.com", 7), b"acme.com"])
def test_a_domain_value_that_is_not_text_is_rejected_not_ignored(bad: object) -> None:
    with pytest.raises(TypeError, match=r"company\.domain"):
        per_company_work_list(work(lead("a@x", bad)))
