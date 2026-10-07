"""Select Leads: hard rules first, then a score from config (requirement 6).

Rules, in order, each leaving a named reason:

1. retired or superseded, opted out, suppressed, already a customer, has an open deal:
   ``rejected``. Every one that applies is recorded; the Lead's score is 0.
2. no LinkedIn URL: ``needs_enrichment``. LinkedIn is always the first contact, so such
   a Lead is never selected, whatever else it has.
3. otherwise a score; at or above the threshold the Lead is ``selected``, below it
   ``rejected`` (``below_threshold``).

The score is the weighted mean of the terms that apply to the search, each a value from
0 to 1: competitor evidence (only where the plan searches by technology), ICP fit,
intent, contactability and source agreement. A term that does not apply is left out of
the mean and said so in the reasons, so a workers search is not marked down for having
no competitor evidence. Weights and threshold come from ``QualifyConfig``.

A role address (``info@``) is not a person's contact: it adds nothing to
contactability.
"""

import uuid
from collections.abc import Collection, Mapping
from decimal import Decimal

from leadforge.lead_ingestion.models import CanonicalLead, EmailStatus, Employment
from leadforge.lead_ingestion.store.lead_reader import CrmState, StoredLead
from leadforge.outreach.config import QualifyConfig
from leadforge.outreach.decisions import Decision, Reason
from leadforge.outreach.search_plan import SearchPlan

__all__ = ["decide", "decide_all"]

_ZERO = Decimal(0)
_ONE = Decimal(1)
_HALF = Decimal("0.5")
_QUARTER = Decimal("0.25")
_PLACES = Decimal("0.001")


def decide_all(
    leads: Collection[StoredLead],
    crm: Mapping[uuid.UUID, CrmState],
    plan: SearchPlan,
    cfg: QualifyConfig,
    labels: Collection[str] = (),
) -> tuple[Decision, ...]:
    """Exactly one Decision per gathered Lead, in lead-id order."""
    ordered = sorted(leads, key=lambda s: s.lead_id)
    if len({s.lead_id for s in ordered}) != len(ordered):
        raise ValueError("a Lead was gathered twice")
    return tuple(
        decide(s, crm.get(s.lead_id, CrmState()), plan, cfg, labels) for s in ordered
    )


def decide(
    stored: StoredLead,
    crm: CrmState,
    plan: SearchPlan,
    cfg: QualifyConfig,
    labels: Collection[str] = (),
) -> Decision:
    """The Decision for one Lead. ``labels`` are the names the plan's terms go by in
    technology evidence (see ``compile_offline.phrases_of``)."""
    rejected = _hard_rejections(stored, crm, cfg)
    if rejected:
        return Decision(
            lead_id=stored.lead_id, status="rejected", score=_ZERO, reasons=rejected
        )
    score, terms = _score(stored, plan, cfg, labels)
    if stored.lead.linkedin_url is None:
        reason = Reason(code="no_linkedin_url", note="LinkedIn is the first contact")
        return Decision(
            lead_id=stored.lead_id,
            status="needs_enrichment",
            score=score,
            reasons=(reason, *terms),
        )
    if score >= cfg.threshold:
        return Decision(
            lead_id=stored.lead_id, status="selected", score=score, reasons=terms
        )
    below = Reason(
        code="below_threshold", value=score, note=f"threshold is {cfg.threshold}"
    )
    return Decision(
        lead_id=stored.lead_id, status="rejected", score=score, reasons=(below, *terms)
    )


def _hard_rejections(
    stored: StoredLead, crm: CrmState, cfg: QualifyConfig
) -> tuple[Reason, ...]:
    stages = {s.casefold() for s in cfg.customer_stages}
    stage = (crm.lifecycle_stage or "").casefold()
    checks = (
        ("retired", stored.retired or bool(stored.successor_ids)),
        ("opted_out", stored.lead.opt_out),
        ("suppressed", stored.lead.suppressed),
        ("customer_stage", stage in stages),
        ("open_deal", crm.has_open_deal is True),
    )
    return tuple(Reason(code=code) for code, hit in checks if hit)


def _score(
    stored: StoredLead, plan: SearchPlan, cfg: QualifyConfig, labels: Collection[str]
) -> tuple[Decimal, tuple[Reason, ...]]:
    weights = cfg.weights
    values: tuple[tuple[str, Decimal, Decimal | None, str | None], ...] = (
        (
            "competitor_evidence",
            weights.competitor_evidence,
            _competitor_evidence(stored, labels, plan.terms) if plan.terms else None,
            None if plan.terms else "not applicable: the search names no technology",
        ),
        ("icp_fit", weights.icp_fit, _icp_fit(stored, plan), None),
        ("intent", weights.intent, _intent(stored), None),
        ("contactability", weights.contactability, _contactability(stored), None),
        (
            "source_agreement",
            weights.source_agreement,
            _source_agreement(stored),
            None,
        ),
    )
    reasons = tuple(
        Reason(code=code, value=value, weight=weight, note=note)
        for code, weight, value, note in values
    )
    applicable = [(w, v) for _, w, v, _ in values if v is not None]
    total = sum((w for w, _ in applicable), _ZERO)
    if total == 0:
        return _ZERO, reasons
    score = sum((w * v for w, v in applicable), _ZERO) / total
    return score.quantize(_PLACES), reasons


def _in_post(lead: CanonicalLead) -> tuple[Employment, ...]:
    """Employments not reported as past: a provider that does not say is not saying
    the person has left."""
    return tuple(e for e in lead.employments if e.is_current is not False)


def _competitor_evidence(
    stored: StoredLead, labels: Collection[str], terms: Collection[str]
) -> Decimal:
    """The strongest evidence that the Lead's company uses a term the plan names.

    From technology signals whose label names a term, and from the attached web
    evidence of the company whose own signal is a technology named by the plan.
    """
    wanted = {label.casefold() for label in labels}
    lead = stored.lead
    tech = [*lead.tech_signals]
    for employment in _in_post(lead):
        tech += employment.company.tech_signals
    hits = [
        Decimal(str(s.strength))
        for s in tech
        if any(w in s.label.casefold() for w in wanted)
    ]
    hits += _web_strengths(stored, "tech", set(terms))
    return max(hits, default=_ZERO)


def _web_strengths(
    stored: StoredLead, kind: str, labels: set[str] | None
) -> list[Decimal]:
    """Strengths of the Signals the Lead's web evidence carries, of one kind, and for
    the given labels when there are any."""
    out: list[Decimal] = []
    for record in stored.web_evidence:
        values = record.values
        if values.get("signal_kind") != kind:
            continue
        if labels is not None and values.get("signal_label") not in labels:
            continue
        strength = values.get("signal_strength")
        if isinstance(strength, int | float):
            out.append(Decimal(str(strength)))
    return out


def _icp_fit(stored: StoredLead, plan: SearchPlan) -> Decimal:
    """Share of the fit checks met: a current employer, a title, and, when the plan
    asks for titles or seniority, a title that holds one of them."""
    current = _in_post(stored.lead)
    named = [e for e in current if e.company.name or e.company.domains]
    titled = [e for e in current if e.title]
    checks = [bool(named), bool(titled)]
    asked = {w.casefold() for w in (*plan.titles, *plan.seniorities)}
    if asked:
        checks.append(any(_title_matches(e, asked) for e in titled))
    return Decimal(sum(checks)) / Decimal(len(checks))


def _title_matches(employment: Employment, asked: set[str]) -> bool:
    title = (employment.title or "").casefold()
    return any(a in title for a in asked)


def _intent(stored: StoredLead) -> Decimal:
    lead = stored.lead
    strengths = [s.strength for s in lead.intent_signals]
    for employment in _in_post(lead):
        strengths += [s.strength for s in employment.company.intent_signals]
    best = Decimal(str(max(strengths, default=0.0)))
    return max([best, *_web_strengths(stored, "intent", None)])


def _contactability(stored: StoredLead) -> Decimal:
    """LinkedIn counts half; a personal email the other half (a verified address in
    full, an unverified or accept-all one a quarter). A role address counts nothing."""
    lead = stored.lead
    score = _HALF if lead.linkedin_url is not None else _ZERO
    if lead.email is None or lead.email_is_role_address:
        return score
    if lead.email_status is EmailStatus.VERIFIED:
        return score + _HALF
    if lead.email_status in (EmailStatus.ACCEPT_ALL, EmailStatus.UNVERIFIED):
        return score + _QUARTER
    return score


def _source_agreement(stored: StoredLead) -> Decimal:
    """Of the paths with a winning value, the share that two or more sources agree on;
    0 for a Lead only one source knows."""
    if len(stored.contributing_sources) < 2 or not stored.agreement:
        return _ZERO
    agreed = sum(1 for _, count in stored.agreement if count >= 2)
    return Decimal(agreed) / Decimal(len(stored.agreement))
