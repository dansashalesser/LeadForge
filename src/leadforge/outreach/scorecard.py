"""Compare a search's stored Decisions with the demo answer key (requirement 15.2).

Each Lead of the search is matched to the person in the key by LinkedIn profile or
email.
For every outcome the key expects (selected, rejected, needs_enrichment) it counts the
people gathered and how many the pipeline decided otherwise, and for selected people the
same for the sequence they end in (email, fallback_email, stalled). A person the search
did not gather is counted apart: a search by one company is not wrong about
everyone else.
The output names outcomes and counts only, never a Lead's values.
"""

import uuid
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store.lead_reader import load_lead
from leadforge.outreach.tables import OutreachDecision, OutreachTriggerEvent

__all__ = ["OutcomeRow", "Scorecard", "render", "score_decisions"]

_FINAL = ("email", "fallback_email", "stalled")


@dataclass
class OutcomeRow:
    gathered: int = 0
    mismatched: int = 0
    wrong: Counter[str] = field(default_factory=Counter)


@dataclass
class Scorecard:
    decisions: dict[str, OutcomeRow]
    sequences: dict[str, OutcomeRow]
    leads_without_person: int
    people_not_gathered: int

    @property
    def mismatches(self) -> int:
        rows = [*self.decisions.values(), *self.sequences.values()]
        return sum(r.mismatched for r in rows)


def score_decisions(
    session: Session, search_id: uuid.UUID, key: Mapping[str, Any]
) -> Scorecard:
    people = _index(key)
    decisions = list(
        session.scalars(
            select(OutreachDecision).where(OutreachDecision.search_id == search_id)
        )
    )
    card = Scorecard({}, {}, 0, 0)
    seen: set[str] = set()
    for decision in decisions:
        person = _person(session, decision.lead_id, people)
        if person is None:
            card.leads_without_person += 1
            continue
        seen.add(person["subject"])
        want = person["outreach"]["decision"]
        row = card.decisions.setdefault(want, OutcomeRow())
        row.gathered += 1
        if decision.status != want:
            row.mismatched += 1
            row.wrong[decision.status] += 1
        if want == "selected" and decision.status == "selected":
            expected = person["outreach"]["sequence"]
            actual = _sequence(session, decision.id)
            seq = card.sequences.setdefault(expected, OutcomeRow())
            seq.gathered += 1
            if actual != expected:
                seq.mismatched += 1
                seq.wrong[actual] += 1
    card.people_not_gathered = len({p["subject"] for p in people.values()} - seen)
    return card


def render(card: Scorecard) -> str:
    lines = ["Decisions against the answer key", ""]
    lines += _table("expected decision", card.decisions)
    lines += ["", "Sequences of the selected"]
    lines += _table("expected sequence", card.sequences)
    lines += [
        "",
        f"mismatches: {card.mismatches}",
        f"leads with no person in the key: {card.leads_without_person}",
        f"people in the key this search did not gather: {card.people_not_gathered}",
    ]
    return "\n".join(lines) + "\n"


def _table(title: str, rows: Mapping[str, OutcomeRow]) -> list[str]:
    out = [f"{title:<22} gathered  mismatched  decided otherwise"]
    for name, row in sorted(rows.items()):
        wrong = ", ".join(f"{k}={v}" for k, v in sorted(row.wrong.items())) or "-"
        out.append(f"{name:<22} {row.gathered:>8}  {row.mismatched:>10}  {wrong}")
    if not rows:
        out.append("(nothing gathered)")
    return out


def _index(key: Mapping[str, Any]) -> dict[str, Any]:
    people: dict[str, Any] = {}
    for person in key["people"]:
        expect = person["expect"]
        if expect.get("lead") == "absent":
            continue
        for ident in _identifiers(expect.get("email"), expect.get("linkedin_url")):
            people.setdefault(ident, person)
    return people


def _person(
    session: Session, lead_id: uuid.UUID, people: Mapping[str, Any]
) -> Any | None:
    stored = load_lead(session, lead_id)
    if stored is None:
        return None
    for ident in _identifiers(stored.lead.email, stored.lead.linkedin_url):
        if ident in people:
            return people[ident]
    return None


def _sequence(session: Session, decision_id: uuid.UUID) -> str:
    kinds = list(
        session.scalars(
            select(OutreachTriggerEvent.kind).where(
                OutreachTriggerEvent.decision_id == decision_id
            )
        )
    )
    return next((k for k in kinds if k in _FINAL), "none")


def _identifiers(email: object, linkedin: object) -> list[str]:
    out: list[str] = []
    if linkedin:
        slug = str(linkedin).split("?")[0].rstrip("/").rsplit("/", 1)[-1].lower()
        out.append(f"linkedin:{slug}")
    if email:
        out.append(f"email:{str(email).lower()}")
    return out
