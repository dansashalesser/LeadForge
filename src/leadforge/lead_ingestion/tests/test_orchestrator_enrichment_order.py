"""Enrichment order and suppression pruning (task 11.6, Requirement 6.10; ADR-0002).

The order of Enrichment sources comes only from declared ``cost_class``,
``charge_unit`` and ``yields_suppression``. Sources with the same declarations form a
tier; tiers run one after another so a suppression found early prunes the work list
before a later (credit-bearing) tier is called. The sources are throwaway subclasses.
"""

from datetime import UTC, datetime
from typing import ClassVar

from leadforge.lead_ingestion.base_source import (
    Capability,
    ChargeUnit,
    CostClass,
    EnrichmentRequest,
    LeadContribution,
    RawBatch,
)
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
)
from leadforge.lead_ingestion.orchestrator import Phase, prune_flagged
from leadforge.lead_ingestion.tests.test_orchestrator_phases import (
    ENRICH,
    REQUEST,
    SEARCH,
    Probe,
    Source,
    orchestrator,
)

FREE_SUPPRESSING = {
    "cost_class": CostClass.FREE,
    "charge_unit": ChargeUnit.PER_CALL,
    "yields_suppression": True,
}
PAID = {
    "cost_class": CostClass.PAID,
    "charge_unit": ChargeUnit.PER_LEAD,
    "yields_suppression": False,
}


def contribution(source: str, values: dict[str, object]) -> LeadContribution:
    return LeadContribution(
        source_name=source,
        values=values,
        provenance=tuple(
            FieldProvenance(
                canonical_path=path,
                source_name=source,
                data_mode=DataMode.SYNTHETIC,
                fetched_at=datetime(2026, 1, 1, tzinfo=UTC),
                raw_field_path=f"raw.{path}",
                confidence_origin=ConfidenceOrigin.NONE,
                untrusted=False,
            )
            for path in values
        ),
    )


class Lookup(Source):
    """Discovery yields one contribution per ``found`` email (flagged ones carry the
    flag in ``found_flags``); Enrichment reports ``reports`` as ``{email: flag}``."""

    found: ClassVar[tuple[str, ...]] = ()
    found_flags: ClassVar[dict[str, str]] = {}
    reports: ClassVar[dict[str, str]] = {}
    # Verbatim values, for leads or reports keyed by something other than a clean email.
    found_values: ClassVar[tuple[dict[str, object], ...]] = ()
    report_values: ClassVar[tuple[dict[str, object], ...]] = ()

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        if self.found_values or self.report_values:
            values = (
                self.found_values
                if raw.payload["kind"] == "search"
                else self.report_values
            )
            return [contribution(self.name, dict(v)) for v in values]
        if raw.payload["kind"] == "search":
            return [
                contribution(
                    self.name,
                    {
                        "email": e,
                        **(
                            {self.found_flags[e]: True} if e in self.found_flags else {}
                        ),
                    },
                )
                for e in self.found
            ]
        return [
            contribution(self.name, {"email": e, flag: True})
            for e, flag in self.reports.items()
        ]


def make(
    name: str, capabilities: frozenset[Capability], **attrs: object
) -> type[Lookup]:
    return type(
        f"L_{name}", (Lookup,), {"name": name, "capabilities": capabilities, **attrs}
    )


def emails(probe: Probe, source: str) -> list[object]:
    request = probe.requests[f"{source}:enrich"]
    assert isinstance(request, EnrichmentRequest)
    return [c.values["email"] for c in request.work_list]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_free_suppression_source_finishes_before_any_credit_source_starts() -> (
    None
):
    probe = Probe()
    # Registry order and name order both put the credit-bearing source first.
    classes = [
        make("a_paid", ENRICH, **PAID),
        make("b_paid", ENRICH, **PAID),
        make("z_free", ENRICH, **FREE_SUPPRESSING),
        make("s", SEARCH, found=("x@a.io",)),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    events = [e for e in probe.events if e.endswith(":enrich")]
    assert events.index("end:z_free:enrich") < events.index("start:a_paid:enrich")
    assert events.index("end:z_free:enrich") < events.index("start:b_paid:enrich")


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_sources_in_one_tier_still_run_concurrently_under_the_bound() -> None:
    probe = Probe()
    classes = [
        make("p1", ENRICH, **PAID),
        make("p2", ENRICH, **PAID),
        make("s", SEARCH, found=("x@a.io",)),
    ]

    await orchestrator(classes, probe, bound=4).run(REQUEST)

    assert probe.peak == 2


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_suppressed_lead_reaches_no_credit_bearing_source() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make("free", ENRICH, reports={"b@a.io": "suppressed"}, **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io", "b@a.io", "c@a.io")),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert emails(probe, "free") == ["a@a.io", "b@a.io", "c@a.io"]
    assert emails(probe, "paid") == ["a@a.io", "c@a.io"]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_an_opted_out_lead_is_removed_like_a_suppressed_one() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make("free", ENRICH, reports={"a@a.io": "opt_out"}, **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io", "b@a.io")),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert emails(probe, "paid") == ["b@a.io"]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_lead_discovery_already_marked_suppressed_reaches_no_enrichment() -> (
    None
):
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "s",
            SEARCH,
            found=("a@a.io", "b@a.io"),
            found_flags={"a@a.io": "suppressed"},
        ),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert emails(probe, "paid") == ["b@a.io"]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_suppression_found_in_one_tier_prunes_every_later_tier() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "free_plain",
            ENRICH,
            cost_class=CostClass.FREE,
            charge_unit=ChargeUnit.PER_CALL,
            yields_suppression=False,
            reports={"a@a.io": "suppressed"},
        ),
        make("s", SEARCH, found=("a@a.io", "b@a.io")),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert emails(probe, "paid") == ["b@a.io"]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_fully_suppressed_work_list_calls_no_later_source() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make("free", ENRICH, reports={"a@a.io": "suppressed"}, **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    results = await orchestrator(classes, probe).run(REQUEST)

    assert "start:paid:enrich" not in probe.events
    assert [(r.source_name, r.phase) for r in results] == [
        ("s", Phase.DISCOVERY),
        ("free", Phase.ENRICHMENT),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_suppression_report_naming_no_known_lead_removes_nothing() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make("free", ENRICH, reports={"nobody@x.io": "suppressed"}, **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert emails(probe, "paid") == ["a@a.io"]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_failed_suppression_source_removes_nothing_and_is_recorded() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "free",
            ENRICH,
            fail_unauthorized=True,
            fail_phase="enrich",
            **FREE_SUPPRESSING,
        ),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    results = await orchestrator(classes, probe).run(REQUEST)

    assert emails(probe, "paid") == ["a@a.io"]
    free = next(r for r in results if r.source_name == "free")
    assert free.outcome.status.value == "unauthorized"


# Verifies: specs/lead-source-adapters/requirements.md#2.7
# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_new_source_lands_in_its_tier_with_no_edit_outside_its_own_class() -> (
    None
):
    probe = Probe()
    registered = [
        make("paid", ENRICH, **PAID),
        make("s", SEARCH, found=("a@a.io",)),
    ]
    await orchestrator(registered, probe).run(REQUEST)
    assert probe.events.index("start:paid:enrich") > 0

    # A source that exists only inside this test, declaring only its attributes.
    throwaway = make("zzz_brand_new", ENRICH, **FREE_SUPPRESSING)
    probe = Probe()
    await orchestrator([*registered, throwaway], probe).run(REQUEST)

    events = [e for e in probe.events if e.endswith(":enrich")]
    assert events.index("end:zzz_brand_new:enrich") < events.index("start:paid:enrich")


def work_values(probe: Probe, source: str) -> list[dict[str, object]]:
    request = probe.requests[f"{source}:enrich"]
    assert isinstance(request, EnrichmentRequest)
    return [dict(c.values) for c in request.work_list]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_report_naming_a_lead_by_linkedin_url_alone_removes_it() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "free",
            ENRICH,
            report_values=(
                {"linkedin_url": "https://li.example/in/a", "opt_out": True},
            ),
            **FREE_SUPPRESSING,
        ),
        make(
            "s",
            SEARCH,
            found_values=(
                {"email": "a@a.io", "linkedin_url": "https://li.example/in/a"},
                {"email": "b@a.io", "linkedin_url": "https://li.example/in/b"},
            ),
        ),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert emails(probe, "paid") == ["b@a.io"]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_identity_matching_ignores_case_and_surrounding_whitespace() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "free",
            ENRICH,
            report_values=({"email": "  A@A.IO ", "suppressed": True},),
            **FREE_SUPPRESSING,
        ),
        make("s", SEARCH, found=("a@a.io", "b@a.io")),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert emails(probe, "paid") == ["b@a.io"]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_blank_identity_value_never_ties_leads_together() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "free",
            ENRICH,
            report_values=({"email": "", "suppressed": True},),
            **FREE_SUPPRESSING,
        ),
        make(
            "s",
            SEARCH,
            found_values=({"email": ""}, {"email": "   "}, {"email": "b@a.io"}),
        ),
    ]

    await orchestrator(classes, probe).run(REQUEST)

    assert len(work_values(probe, "paid")) == 3


# Verifies: specs/lead-source-adapters/requirements.md#6.10
def test_pruning_returns_a_new_tuple_and_leaves_the_input_untouched() -> None:
    work = (
        contribution("s", {"email": "a@a.io"}),
        contribution("s", {"email": "b@a.io"}),
    )
    report = contribution("r", {"email": "a@a.io", "suppressed": True})

    pruned = prune_flagged(work, (report,))

    assert len(work) == 2
    assert pruned == (work[1],)


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_halted_free_tier_does_not_block_the_credit_tier() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make("free", ENRICH, hang_phase="enrich", **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    results = await orchestrator(classes, probe, run_timeout_s=0.2).run(REQUEST)

    # The deadline hit while the first tier hung: the later tier is accounted for too.
    by_name = {(r.source_name, r.phase): r.outcome.status.value for r in results}
    assert by_name[("free", Phase.ENRICHMENT)] == "timed_out"
    assert by_name[("paid", Phase.ENRICHMENT)] == "timed_out"
