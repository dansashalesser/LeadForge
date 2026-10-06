"""Enrichment tiers feed later tiers (ADR-0006, amending ADR-0002; 2026-10-06).

User decision (verbatim): "can't we use either source to enhance information from
other sources?". So each finished tier's contributions join the work list of every LATER
tier, in one bounded forward pass: no tier is called twice, none is fed back, and a
suppression found by any earlier tier still prunes what a later tier adds. An
evidence-only source runs after the identity-yielding sources of its class, so it sees
the company domain a per-lead match found. The sources are throwaway subclasses.
"""

from leadforge.lead_ingestion.base_source import (
    ChargeUnit,
    CostClass,
)
from leadforge.lead_ingestion.orchestrator import (
    Phase,
    SourceResult,
    per_company_work_list,
)
from leadforge.lead_ingestion.tests.test_orchestrator_enrichment_order import (
    FREE_SUPPRESSING,
    PAID,
    contribution,
    emails,
    make,
    work_values,
)
from leadforge.lead_ingestion.tests.test_orchestrator_phases import (
    ENRICH,
    REQUEST,
    SEARCH,
    Probe,
    orchestrator,
)

FREE_PLAIN = {
    "cost_class": CostClass.FREE,
    "charge_unit": ChargeUnit.PER_LEAD,
    "yields_suppression": False,
}
PAID_SUPPRESSING = {**PAID, "yields_suppression": True}
EVIDENCE_ONLY = {
    "cost_class": CostClass.PAID,
    "charge_unit": ChargeUnit.PER_CALL,
    "yields_suppression": False,
    "evidence_only": True,
}


def enrich_starts(probe: Probe, source: str) -> int:
    return probe.events.count(f"start:{source}:enrich")


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_a_field_an_earlier_tier_adds_reaches_every_later_tier() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "free",
            ENRICH,
            report_values=({"email": "a@a.io", "company.domain": "a.io"},),
            **FREE_SUPPRESSING,
        ),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    await orchestrator_run(classes, probe)

    # Discovery first, then the earlier tier's contribution, in tier order.
    assert work_values(probe, "paid") == [
        {"email": "a@a.io"},
        {"email": "a@a.io", "company.domain": "a.io"},
    ]


# Verifies: specs/lead-source-adapters/requirements.md#6.9
async def test_a_person_an_earlier_tier_found_is_worked_by_a_later_tier() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "suppressing_paid",
            ENRICH,
            report_values=({"email": "new@b.io"},),
            **PAID_SUPPRESSING,
        ),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    await orchestrator_run(classes, probe)

    assert emails(probe, "suppressing_paid") == ["a@a.io"]
    assert emails(probe, "paid") == ["a@a.io", "new@b.io"]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_the_feed_is_one_forward_pass_with_no_tier_called_twice() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, report_values=({"email": "late@c.io"},), **PAID),
        make("free", ENRICH, report_values=({"email": "b@b.io"},), **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    results = await orchestrator_run(classes, probe)

    assert enrich_starts(probe, "free") == 1
    assert enrich_starts(probe, "paid") == 1
    # Nothing a later tier found is fed back to an earlier one.
    assert emails(probe, "free") == ["a@a.io"]
    assert emails(probe, "paid") == ["a@a.io", "b@b.io"]
    # Results keep each source's own contributions only (the feed copies nothing in).
    paid = next(
        r for r in results if (r.source_name, r.phase) == ("paid", Phase.ENRICHMENT)
    )
    assert [c.values for c in paid.contributions or ()] == [{"email": "late@c.io"}]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_an_earlier_suppression_prunes_a_person_a_later_tier_re_adds() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "suppressing_paid",
            ENRICH,
            # Re-introduces the suppressed person with a fresh, unflagged record.
            report_values=({"email": "a@a.io", "person.title": "CTO"},),
            **PAID_SUPPRESSING,
        ),
        make("free", ENRICH, reports={"a@a.io": "suppressed"}, **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io", "b@a.io")),
    ]

    await orchestrator_run(classes, probe)

    assert emails(probe, "suppressing_paid") == ["b@a.io"]
    assert emails(probe, "paid") == ["b@a.io"]


# Verifies: specs/lead-source-adapters/requirements.md#6.11
async def test_a_per_company_tier_still_gets_one_lead_per_company_of_the_fed_list() -> (
    None
):
    probe = Probe()
    classes = [
        make(
            "per_company",
            ENRICH,
            cost_class=CostClass.PAID,
            charge_unit=ChargeUnit.PER_COMPANY,
            yields_suppression=False,
        ),
        make(
            "free",
            ENRICH,
            report_values=(
                {"email": "y@a.io", "company.domain": "a.io"},
                {"email": "z@b.io", "company.domain": "b.io"},
            ),
            **FREE_PLAIN,
        ),
        make(
            "s",
            SEARCH,
            found_values=({"email": "x@a.io", "company.domain": "a.io"},),
        ),
    ]

    await orchestrator_run(classes, probe)

    assert emails(probe, "per_company") == ["x@a.io", "z@b.io"]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_an_evidence_only_source_runs_after_the_tier_that_finds_the_domain() -> (
    None
):
    probe = Probe()
    classes = [
        make("evidence", ENRICH, **EVIDENCE_ONLY),
        make(
            "matcher",
            ENRICH,
            report_values=({"email": "a@a.io", "company.domain": "found.io"},),
            **PAID,
        ),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    await orchestrator_run(classes, probe)

    events = [e for e in probe.events if e.endswith(":enrich")]
    assert events.index("end:matcher:enrich") < events.index("start:evidence:enrich")
    assert {"email": "a@a.io", "company.domain": "found.io"} in work_values(
        probe, "evidence"
    )


async def orchestrator_run(
    classes: list[type], probe: Probe
) -> tuple[SourceResult, ...]:
    return await orchestrator(classes, probe).run(REQUEST)


# Verifies: specs/lead-source-adapters/requirements.md#6.11
def test_a_fed_record_of_a_known_person_does_not_stand_for_a_company_of_its_own() -> (
    None
):
    discovered = contribution("s", {"email": "x@a.io", "company.domain": "a.io"})
    # An earlier tier's records: the same person with no domain, and one new
    # domainless person named twice.
    same_person = contribution("t", {"email": "X@a.io"})
    new_person = contribution("t", {"email": "y@b.io"})
    new_again = contribution("u", {"email": "y@b.io", "person.title": "CTO"})

    kept = per_company_work_list((discovered, same_person, new_person, new_again))

    assert kept == (discovered, new_person)


# Verifies: specs/lead-source-adapters/requirements.md#6.11
def test_a_person_at_two_companies_does_not_fuse_those_companies() -> None:
    # One person recorded at a former and a current employer, plus a colleague at the
    # second: both companies are still worked, each once.
    former = contribution("s", {"email": "x@a.io", "company.domain": "a.io"})
    current = contribution("t", {"email": "x@a.io", "company.domain": "b.io"})
    colleague = contribution("s", {"email": "z@b.io", "company.domain": "b.io"})

    kept = per_company_work_list((former, current, colleague))

    assert kept == (former, current)


# Verifies: specs/lead-source-adapters/requirements.md#6.1
async def test_a_failed_tier_feeds_nothing_and_later_tiers_still_run_once() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "free",
            ENRICH,
            fail_unauthorized=True,
            fail_phase="enrich",
            report_values=({"email": "never@b.io"},),
            **FREE_SUPPRESSING,
        ),
        make("s", SEARCH, found_values=({"email": "a@a.io"},)),
    ]

    await orchestrator_run(classes, probe)

    assert enrich_starts(probe, "free") == 1
    assert enrich_starts(probe, "paid") == 1
    assert emails(probe, "paid") == ["a@a.io"]
