"""Selection: hard rules, needs_enrichment, a configured score, reasons (4.1, 4.2)."""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.models import (
    CanonicalLead,
    CompanySignal,
    Employment,
    IntentSignal,
    TechSignal,
)
from leadforge.lead_ingestion.store import models as m
from leadforge.lead_ingestion.store.lead_reader import CrmState, StoredLead
from leadforge.lead_ingestion.store.migrate import upgrade_to_head
from leadforge.outreach.config import QualifyConfig, load_outreach_config
from leadforge.outreach.decisions import (
    Decision,
    load_decisions,
    milli,
    record_decisions,
)
from leadforge.outreach.qualify import decide, decide_all
from leadforge.outreach.search_plan import SearchPlan
from leadforge.outreach.searches import start_search

CONFIG = load_outreach_config(
    Path(__file__).resolve().parents[4] / "config" / "outreach.yaml"
).qualify
NOW = datetime(2026, 10, 7, tzinfo=UTC)
LINKEDIN = "https://www.linkedin.com/in/someone"
LABELS = ("term a", "alias a")


def _plan(**over: object) -> SearchPlan:
    fields: dict[str, object] = {
        "mode": "users",
        "query": "q",
        "terms": ("term_a",),
        "compiler": "offline",
        **over,
    }
    return SearchPlan.model_validate(fields)


def _employment(
    *,
    tech: float | None = 0.9,
    intent: float | None = 0.8,
    title: str | None = "Head of Data",
) -> Employment:
    return Employment(
        company=CompanySignal(
            company_id="c1",
            name="Acme",
            domains=("acme.example",),
            tech_signals=(TechSignal(label="Term A", strength=tech),) if tech else (),
            intent_signals=(IntentSignal(label="hiring", strength=intent),)
            if intent
            else (),
        ),
        title=title,
        is_current=True,
    )


def _lead(**over: object) -> CanonicalLead:
    fields: dict[str, object] = {
        "email": "pat@acme.example",
        "email_status": "verified",
        "linkedin_url": LINKEDIN,
        "full_name": "Pat Doe",
        "employments": (_employment(),),
        **over,
    }
    return CanonicalLead.model_validate(fields)


def _stored(
    lead: CanonicalLead | None = None,
    *,
    retired_at: datetime | None = None,
    successor_ids: tuple[uuid.UUID, ...] = (),
    agreement: tuple[tuple[str, int], ...] = (("person.email", 2), ("person.name", 2)),
    contributing_sources: tuple[str, ...] = ("one", "two"),
) -> StoredLead:
    return StoredLead(
        lead_id=uuid.uuid4(),
        lead=lead or _lead(),
        provenance=(),
        agreement=agreement,
        contributing_sources=contributing_sources,
        primary_domain=None,
        primary_domain_source=None,
        projection_version=1,
        projection_fingerprint=None,
        computed_at=NOW,
        stale=False,
        retired_at=retired_at,
        successor_ids=successor_ids,
        web_evidence=(),
    )


def _codes(decision: Decision) -> list[str]:
    return [r.code for r in decision.reasons]


# Verifies: outreach requirements 6.1
def test_a_good_lead_is_selected_and_every_score_term_is_a_reason() -> None:
    decision = decide(_stored(), CrmState(), _plan(), CONFIG, LABELS)

    assert decision.status == "selected"
    assert decision.score >= CONFIG.threshold
    assert _codes(decision) == [
        "competitor_evidence",
        "icp_fit",
        "intent",
        "contactability",
        "source_agreement",
    ]
    assert all(r.weight is not None and r.value is not None for r in decision.reasons)


# Verifies: outreach requirements 6.3
@pytest.mark.parametrize(
    ("stored", "crm", "code"),
    [
        (_stored(retired_at=NOW), CrmState(), "retired"),
        (_stored(successor_ids=(uuid.uuid4(),)), CrmState(), "retired"),
        (_stored(_lead(opt_out=True)), CrmState(), "opted_out"),
        (_stored(_lead(suppressed=True)), CrmState(), "suppressed"),
        (_stored(), CrmState(lifecycle_stage="Customer"), "customer_stage"),
        (_stored(), CrmState(has_open_deal=True), "open_deal"),
    ],
)
def test_each_hard_rule_rejects_with_a_named_reason_and_a_zero_score(
    stored: StoredLead, crm: CrmState, code: str
) -> None:
    decision = decide(stored, crm, _plan(), CONFIG, LABELS)

    assert decision.status == "rejected"
    assert _codes(decision) == [code]
    assert decision.score == 0


# Verifies: outreach requirements 6.3
def test_every_hard_rule_that_applies_is_named() -> None:
    stored = _stored(_lead(opt_out=True, suppressed=True))

    decision = decide(stored, CrmState(has_open_deal=True), _plan(), CONFIG, LABELS)

    assert _codes(decision) == ["opted_out", "suppressed", "open_deal"]


# Verifies: outreach requirements 6.3
def test_a_lead_not_yet_a_customer_with_no_open_deal_is_not_rejected() -> None:
    crm = CrmState(contact_exists=True, lifecycle_stage="lead", has_open_deal=False)

    assert decide(_stored(), crm, _plan(), CONFIG, LABELS).status == "selected"


# Verifies: outreach requirements 6.2
def test_a_lead_with_an_email_and_no_linkedin_is_never_selected() -> None:
    decision = decide(_stored(_lead(linkedin_url=None)), CrmState(), _plan(), CONFIG)

    assert decision.status == "needs_enrichment"
    assert _codes(decision)[0] == "no_linkedin_url"


# Verifies: outreach requirements 6.2
def test_no_linkedin_url_outranks_a_perfect_score_but_not_a_hard_rule() -> None:
    plain = decide(
        _stored(_lead(linkedin_url=None)), CrmState(), _plan(), CONFIG, LABELS
    )
    barred = decide(
        _stored(_lead(linkedin_url=None, opt_out=True)), CrmState(), _plan(), CONFIG
    )

    assert plain.status == "needs_enrichment"
    assert barred.status == "rejected"


# Verifies: outreach requirements 6.4
def test_a_role_address_adds_nothing_to_contactability() -> None:
    personal = decide(_stored(), CrmState(), _plan(), CONFIG, LABELS)
    role = decide(
        _stored(_lead(email="info@acme.example", email_is_role_address=True)),
        CrmState(),
        _plan(),
        CONFIG,
        LABELS,
    )

    def contact(d: Decision) -> Decimal:
        return next(
            r.value
            for r in d.reasons
            if r.code == "contactability" and r.value is not None
        )

    assert contact(personal) == 1
    assert contact(role) == Decimal("0.5")


# Verifies: outreach requirements 6.5
def test_a_lead_below_the_threshold_is_rejected_with_the_score_in_the_reason() -> None:
    weak = _lead(email=None, email_status="unknown", employments=())

    decision = decide(_stored(weak, agreement=()), CrmState(), _plan(), CONFIG, LABELS)

    assert decision.status == "rejected"
    assert decision.score < CONFIG.threshold
    assert _codes(decision)[0] == "below_threshold"
    assert decision.reasons[0].value == decision.score


# Verifies: outreach requirements 6.5
def test_changing_a_weight_changes_the_score_with_no_code_change() -> None:
    heavier = CONFIG.model_copy(
        update={
            "weights": CONFIG.weights.model_copy(
                update={
                    "source_agreement": Decimal(5),
                    "competitor_evidence": Decimal(0),
                }
            )
        }
    )
    lone = _stored(contributing_sources=("one",), agreement=(("person.email", 1),))

    before = decide(lone, CrmState(), _plan(), CONFIG, LABELS)
    after = decide(lone, CrmState(), _plan(), heavier, LABELS)

    assert before.score != after.score
    assert after.score < before.score


# Verifies: outreach requirements 6.5
def test_changing_the_threshold_changes_the_outcome() -> None:
    stored = _stored()
    strict = CONFIG.model_copy(update={"threshold": Decimal(1)})
    plan = _plan()

    assert decide(stored, CrmState(), plan, CONFIG, LABELS).status == "selected"
    assert decide(stored, CrmState(), plan, strict, LABELS).status == "rejected"


# Verifies: outreach requirements 6.5
def test_competitor_evidence_needs_a_signal_naming_a_plan_term() -> None:
    other = _lead(employments=(_employment(),))
    with_alias = decide(_stored(other), CrmState(), _plan(), CONFIG, LABELS)
    without = decide(_stored(other), CrmState(), _plan(), CONFIG, ("unrelated",))

    def evidence(d: Decision) -> Decimal | None:
        return next(r.value for r in d.reasons if r.code == "competitor_evidence")

    assert evidence(with_alias) == Decimal("0.9")
    assert evidence(without) == 0


# Verifies: outreach requirements 6.5
def test_a_search_with_no_technology_leaves_competitor_evidence_out_of_the_mean() -> (
    None
):
    plan = _plan(mode="workers", terms=(), domains=("acme.example",), company="Acme")
    lead = _lead(employments=(_employment(tech=None),))

    decision = decide(_stored(lead), CrmState(), plan, CONFIG)
    competitor = decision.reasons[0]

    assert (competitor.code, competitor.value) == ("competitor_evidence", None)
    assert competitor.note is not None
    assert "not applicable" in competitor.note
    assert decision.status == "selected"


# Verifies: outreach requirements 6.5
def test_icp_fit_asks_for_a_title_that_holds_a_wanted_title_or_seniority() -> None:
    wanted = _plan(titles=("head of data",))
    other = _plan(titles=("sales",), seniorities=("director",))

    def icp(plan: SearchPlan) -> Decimal:
        d = decide(_stored(), CrmState(), plan, CONFIG, LABELS)
        return next(
            r.value for r in d.reasons if r.code == "icp_fit" and r.value is not None
        )

    assert icp(wanted) == 1
    assert icp(other) == Decimal(2) / Decimal(3)


# Verifies: outreach requirements 6.1
def test_every_gathered_lead_gets_exactly_one_decision() -> None:
    gathered = [
        _stored(),
        _stored(_lead(linkedin_url=None)),
        _stored(_lead(opt_out=True)),
        _stored(retired_at=NOW),
        _stored(_lead(email=None, email_status="unknown", employments=())),
    ]

    decisions = decide_all(gathered, {}, _plan(), CONFIG, LABELS)

    assert len(decisions) == len(gathered)
    assert {d.lead_id for d in decisions} == {s.lead_id for s in gathered}
    by_status = [d.status for d in decisions]
    assert {"selected", "rejected", "needs_enrichment"} <= set(by_status)
    assert all(s in {"selected", "rejected", "needs_enrichment"} for s in by_status)


# Verifies: outreach requirements 6.1
def test_a_lead_gathered_twice_is_refused() -> None:
    one = _stored()

    with pytest.raises(ValueError, match="twice"):
        decide_all([one, one], {}, _plan(), CONFIG)


# Verifies: outreach requirements 6.1
def test_crm_state_is_looked_up_per_lead() -> None:
    barred, free = _stored(), _stored()

    decisions = decide_all(
        [barred, free],
        {barred.lead_id: CrmState(has_open_deal=True)},
        _plan(),
        CONFIG,
        LABELS,
    )
    by_lead = {d.lead_id: d for d in decisions}

    assert by_lead[barred.lead_id].status == "rejected"
    assert by_lead[free.lead_id].status == "selected"


# Verifies: outreach requirements 6.6
def test_decisions_are_stored_with_their_score_and_non_empty_reasons() -> None:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        upgrade_to_head(conn)
    stored = [
        _stored(),
        _stored(_lead(opt_out=True)),
        _stored(_lead(linkedin_url=None)),
    ]
    decisions = decide_all(stored, {}, _plan(), CONFIG, LABELS)

    with Session(engine) as session, session.begin():
        for s in stored:
            session.add(m.LeadIdentity(id=s.lead_id, created_at=NOW))
        session.flush()
        search_id = start_search(session, _plan(), now=NOW)
        record_decisions(session, search_id, decisions, now=NOW)
    with Session(engine) as session:
        loaded = load_decisions(session, search_id)
        rows = session.execute(
            sa.text("select reasons, score_milli from outreach_decision")
        ).all()

    assert {d.lead_id: d for d in loaded} == {d.lead_id: d for d in decisions}
    assert all(r.reasons for r in loaded)
    assert all(row.reasons and row.reasons != "[]" for row in rows)
    assert milli(Decimal("0.6666")) == 667
    assert milli(Decimal("0.0005")) == 0
    assert milli(Decimal("0.0015")) == 2


def test_the_shipped_config_is_what_these_tests_score_against() -> None:
    assert isinstance(CONFIG, QualifyConfig)
    assert CONFIG.threshold < 1
