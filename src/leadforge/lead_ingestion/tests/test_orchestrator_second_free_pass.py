"""A second, free pass of the free tier (user decision 2026-10-06; ADR-0006 amended).

User decision (2026-10-06): after the single forward pass, the free tier (the CRM
lookup) runs once more ONLY for the identities first seen after it ran (found by a
paid tier). Exactly once; nothing already asked is asked again; its findings and
opt-outs are results like any other; no paid tier runs after it; a failure in it is
isolated; its calls count under the same source. The sources are throwaway subclasses.
"""

import asyncio
from typing import ClassVar

from leadforge.lead_ingestion.base_source import (
    Capability,
    EnrichmentRequest,
    LeadContribution,
    RawBatch,
    SourceRequest,
)
from leadforge.lead_ingestion.errors import SourceError
from leadforge.lead_ingestion.orchestrator import (
    Phase,
    SourceResult,
    SourceStatus,
)
from leadforge.lead_ingestion.tests.test_orchestrator_enrichment_order import (
    FREE_SUPPRESSING,
    PAID,
    Lookup,
    contribution,
)
from leadforge.lead_ingestion.tests.test_orchestrator_phases import (
    ENRICH,
    REQUEST,
    SEARCH,
    Probe,
    orchestrator,
)

P_LINKEDIN = "https://linkedin.com/in/pat"


class Recording(Lookup):
    """Records every Enrichment work list it is handed, per call, in ``asked``.

    ``answers`` maps an email asked to the values reported for it (so a second call
    reports only about what it was asked); ``fail_on_call`` fails that call (1-based).
    """

    answers: ClassVar[dict[str, dict[str, object]]] = {}
    fail_on_call: ClassVar[int | None] = None
    hang_on_call: ClassVar[int | None] = None

    async def fetch_raw(self, request: SourceRequest) -> RawBatch:
        if isinstance(request, EnrichmentRequest):
            calls = self.probe.__dict__.setdefault("asked", [])
            calls.append((self.name, request.work_list))
            count = sum(1 for n, _ in calls if n == self.name)
            if self.fail_on_call == count:
                raise SourceError(self.name, "pass failed")
            if self.hang_on_call == count:
                await asyncio.Event().wait()
        return await super().fetch_raw(request)

    def normalize(self, raw: RawBatch) -> list[LeadContribution]:
        if not self.answers or raw.payload["kind"] == "search":
            return super().normalize(raw)
        asked = self.probe.__dict__["asked"]
        work = [w for n, w in asked if n == self.name][-1]
        return [
            contribution(self.name, {"email": email, **self.answers[email]})
            for email in dict.fromkeys(
                str(c.values.get("email") or c.values.get("person.email")) for c in work
            )
            if email in self.answers
        ]


def make(
    name: str, capabilities: frozenset[Capability], **attrs: object
) -> type[Recording]:
    return type(
        f"R_{name}", (Recording,), {"name": name, "capabilities": capabilities, **attrs}
    )


def asked(probe: Probe, source: str) -> list[list[dict[str, object]]]:
    return [
        [dict(c.values) for c in work]
        for name, work in probe.__dict__.get("asked", [])
        if name == source
    ]


def enrichment(results: tuple[SourceResult, ...], source: str) -> list[SourceResult]:
    return [
        r for r in results if r.source_name == source and r.phase is Phase.ENRICHMENT
    ]


async def run(
    classes: list[type], probe: Probe, run_timeout_s: float = 30
) -> tuple[SourceResult, ...]:
    return await orchestrator(classes, probe, run_timeout_s=run_timeout_s).run(REQUEST)


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_the_free_tier_is_asked_again_only_about_what_a_later_tier_found() -> (
    None
):
    probe = Probe()
    classes = [
        make(
            "paid",
            ENRICH,
            report_values=(
                {"email": "new@b.io"},
                {"email": "a@a.io", "person.title": "CTO"},  # already asked: not again
            ),
            **PAID,
        ),
        make("free", ENRICH, **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    results = await run(classes, probe)

    assert asked(probe, "free") == [[{"email": "a@a.io"}], [{"email": "new@b.io"}]]
    assert len(asked(probe, "paid")) == 1
    # The second pass comes after every paid tier.
    events = [e for e in probe.events if e.endswith(":enrich")]
    assert events[-2:] == ["start:free:enrich", "end:free:enrich"]
    assert events.count("start:paid:enrich") == 1
    # Two Enrichment results for the free source, the second after all others, with
    # the cumulative ledger (counted under the same source).
    first, second = enrichment(results, "free")
    assert results[-1] is second
    assert (first.outcome.attempted, second.outcome.attempted) == (1, 2)
    assert second.outcome.succeeded == 2


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_nothing_new_means_no_second_pass() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, report_values=({"email": "a@a.io", "x": 1},), **PAID),
        make("free", ENRICH, **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    results = await run(classes, probe)

    assert len(asked(probe, "free")) == 1
    assert len(enrichment(results, "free")) == 1


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_new_linkedin_identity_alone_is_new() -> None:
    probe = Probe()
    found = {"email": "a@a.io", "person.linkedin_url": P_LINKEDIN}
    classes = [
        make("paid", ENRICH, report_values=(found,), **PAID),
        make("free", ENRICH, **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    await run(classes, probe)

    assert asked(probe, "free")[1:] == [[found]]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_an_opted_out_person_is_never_asked_in_the_second_pass() -> None:
    probe = Probe()
    classes = [
        make(
            "paid",
            ENRICH,
            report_values=(
                # Pat, by a LinkedIn URL the free tier never saw, linked to the address
                # the free tier opted out.
                {"person.email": "a@a.io", "person.linkedin_url": P_LINKEDIN},
                {"email": "new@b.io"},
            ),
            **PAID,
        ),
        make("free", ENRICH, answers={"a@a.io": {"opt_out": True}}, **FREE_SUPPRESSING),
        make("s", SEARCH, found=("a@a.io", "c@c.io")),
    ]

    await run(classes, probe)

    assert asked(probe, "free")[1:] == [[{"email": "new@b.io"}]]


# Verifies: specs/lead-source-adapters/requirements.md#6.10
# Verifies: specs/lead-source-adapters/requirements.md#6.1
async def test_the_second_pass_reports_its_opt_outs_and_fields_as_results() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, report_values=({"email": "new@b.io"},), **PAID),
        make(
            "free",
            ENRICH,
            answers={"new@b.io": {"opt_out": True, "crm.lifecycle_stage": "lead"}},
            **FREE_SUPPRESSING,
        ),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    results = await run(classes, probe)

    _, second = enrichment(results, "free")
    assert [c.values for c in second.contributions or ()] == [
        {"email": "new@b.io", "opt_out": True, "crm.lifecycle_stage": "lead"}
    ]
    assert second.batch is not None


# Verifies: specs/lead-source-adapters/requirements.md#6.1
async def test_a_failure_in_the_second_pass_is_isolated() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, report_values=({"email": "new@b.io"},), **PAID),
        make(
            "free",
            ENRICH,
            answers={"a@a.io": {"crm.lifecycle_stage": "customer"}},
            fail_on_call=2,
            **FREE_SUPPRESSING,
        ),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    results = await run(classes, probe)

    first, second = enrichment(results, "free")
    # The first pass's findings are kept; the second records the failure only.
    assert [c.values for c in first.contributions or ()] == [
        {"email": "a@a.io", "crm.lifecycle_stage": "customer"}
    ]
    assert second.contributions is None
    assert second.batch is None
    assert second.outcome.status is SourceStatus.FAILED
    assert (second.outcome.succeeded, second.outcome.failed) == (1, 1)
    # Every other source is untouched.
    assert all(
        r.outcome.status is SourceStatus.OK for r in results if r.source_name != "free"
    )


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_a_paid_tier_is_never_given_a_second_pass() -> None:
    probe = Probe()
    classes = [
        make("paid_b", ENRICH, report_values=({"email": "late@d.io"},), **PAID),
        make(
            "paid_a",
            ENRICH,
            report_values=({"email": "new@b.io"},),
            **{**PAID, "yields_suppression": True},
        ),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    await run(classes, probe)

    assert len(asked(probe, "paid_a")) == 1
    assert len(asked(probe, "paid_b")) == 1


# Verifies: specs/lead-source-adapters/requirements.md#6.6
async def test_a_second_pass_cut_short_by_the_deadline_is_recorded_timed_out() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, report_values=({"email": "new@b.io"},), **PAID),
        make(
            "free",
            ENRICH,
            answers={"a@a.io": {"crm.lifecycle_stage": "customer"}},
            hang_on_call=2,
            **FREE_SUPPRESSING,
        ),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    results = await run(classes, probe, run_timeout_s=0.5)

    first, second = enrichment(results, "free")
    assert first.contributions is not None
    assert len(first.contributions) == 1
    assert second.contributions is None
    assert second.outcome.status is SourceStatus.TIMED_OUT
    assert (second.outcome.succeeded, second.outcome.failed) == (1, 1)
    [paid] = enrichment(results, "paid")
    assert paid.outcome.status is SourceStatus.OK


# Verifies: specs/lead-source-adapters/requirements.md#6.10
async def test_what_the_free_tier_answered_itself_is_not_asked_of_it_again() -> None:
    probe = Probe()
    classes = [
        make("paid", ENRICH, **PAID),
        make(
            "free", ENRICH, report_values=({"email": "own@b.io"},), **FREE_SUPPRESSING
        ),
        make("s", SEARCH, found=("a@a.io",)),
    ]

    results = await run(classes, probe)

    assert len(asked(probe, "free")) == 1
    assert len(enrichment(results, "free")) == 1
