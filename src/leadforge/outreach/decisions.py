"""A Decision: one verdict per gathered Lead, with the reasons behind it (6.1, 6.6).

Stored in ``outreach_decision``. The score is kept as an integer number of thousandths,
so it is exact on every engine, and ``reasons`` is never empty: a Lead nobody can
explain is a Lead nobody should contact.
"""

import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from leadforge.outreach.tables import OutreachDecision

__all__ = [
    "Decision",
    "DecisionStatus",
    "EvidenceRef",
    "Reason",
    "load_decisions",
    "milli",
    "record_decisions",
]

DecisionStatus = Literal["selected", "rejected", "needs_enrichment", "manual_review"]
_MILLI = Decimal("0.001")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class EvidenceRef(_Frozen):
    """One cited piece of usage evidence, as stored in a Reason (design: Qualify)."""

    model_config = ConfigDict(frozen=True, extra="forbid", populate_by_name=True)

    evidence_class: Annotated[str, Field(min_length=1, alias="class")]
    source: str
    url: Annotated[str, Field(min_length=1)]
    observed_on: date | None = None
    quote: str
    relationship: str


class Reason(_Frozen):
    """One thing that decided or scored a Lead.

    ``code`` names a rule (``retired``, ``below_threshold``) or a score term
    (``icp_fit``); ``value`` and ``weight`` are set for a score term, ``note`` says why
    in words that carry no personal data.
    """

    code: Annotated[str, Field(min_length=1)]
    value: Decimal | None = None
    weight: Decimal | None = None
    note: str | None = None
    evidence_refs: tuple[EvidenceRef, ...] = ()


class Decision(_Frozen):
    lead_id: uuid.UUID
    status: DecisionStatus
    score: Annotated[Decimal, Field(ge=0, le=1)]
    reasons: Annotated[tuple[Reason, ...], Field(min_length=1)]


def milli(value: Decimal) -> int:
    """``value`` in thousandths, rounded half to even."""
    return int((value / _MILLI).to_integral_value(ROUND_HALF_EVEN))


def _stored(reason: Reason) -> dict[str, object]:
    """The reason as JSON; ``evidence_refs`` only where there are some, so reasons
    without citations are stored exactly as before."""
    out = reason.model_dump(mode="json", by_alias=True)
    if not reason.evidence_refs:
        del out["evidence_refs"]
    return out


def record_decisions(
    session: Session,
    search_id: uuid.UUID,
    decisions: tuple[Decision, ...],
    *,
    now: datetime,
) -> None:
    """Store one row per Decision for the search; the caller commits."""
    session.add_all(
        OutreachDecision(
            search_id=search_id,
            lead_id=d.lead_id,
            status=d.status,
            score_milli=milli(d.score),
            reasons=[_stored(r) for r in d.reasons],
            decided_at=now,
        )
        for d in decisions
    )
    session.flush()


def load_decisions(session: Session, search_id: uuid.UUID) -> tuple[Decision, ...]:
    """The stored Decisions of a search, ordered by lead id."""
    rows = session.scalars(
        select(OutreachDecision)
        .where(OutreachDecision.search_id == search_id)
        .order_by(OutreachDecision.lead_id)
    )
    return tuple(
        Decision.model_validate(
            {
                "lead_id": r.lead_id,
                "status": r.status,
                "score": Decimal(r.score_milli) * _MILLI,
                "reasons": r.reasons,
            }
        )
        for r in rows
    )
