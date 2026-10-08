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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from leadforge.lead_ingestion.store.lead_reader import load_lead
from leadforge.outreach.tables import OutreachDecision, OutreachTriggerEvent

__all__ = [
    "ConfusionRow",
    "OutcomeRow",
    "Scorecard",
    "UserVerdict",
    "UsersScorecard",
    "render",
    "render_users",
    "score_decisions",
    "score_users",
    "score_users_search",
    "users_verdicts",
]

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


@dataclass
class ConfusionRow:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0


@dataclass(frozen=True)
class UserVerdict:
    """One key person: the scenario, the label, and the decision status.

    `status` is None for a person the search did not gather.
    """

    scenario: str
    is_user: bool
    status: str | None

    @property
    def selected(self) -> bool:
        # manual_review, rejected, needs_enrichment, not gathered: not selected.
        return self.status == "selected"


@dataclass
class UsersScorecard:
    """Users-mode accuracy on `is_user`.

    A metric whose denominator is zero is None, never a made-up 0.0: precision is
    None when nothing was selected, recall when the key has no true user, F1 when
    either is None or both are 0.
    """

    scenarios: dict[str, ConfusionRow]

    def _total(self, attr: str) -> int:
        return sum(getattr(r, attr) for r in self.scenarios.values())

    @property
    def precision(self) -> float | None:
        tp, fp = self._total("true_positive"), self._total("false_positive")
        return tp / (tp + fp) if tp + fp else None

    @property
    def recall(self) -> float | None:
        tp, fn = self._total("true_positive"), self._total("false_negative")
        return tp / (tp + fn) if tp + fn else None

    @property
    def f1(self) -> float | None:
        p, r = self.precision, self.recall
        if p is None or r is None or p + r == 0:
            return None
        return 2 * p * r / (p + r)


def score_users(verdicts: Iterable[UserVerdict]) -> UsersScorecard:
    scenarios: dict[str, ConfusionRow] = {}
    for v in verdicts:
        row = scenarios.setdefault(v.scenario, ConfusionRow())
        if v.is_user and v.selected:
            row.true_positive += 1
        elif v.is_user:
            row.false_negative += 1
        elif v.selected:
            row.false_positive += 1
        else:
            row.true_negative += 1
    return UsersScorecard(scenarios)


def users_verdicts(
    session: Session, search_id: uuid.UUID, key: Mapping[str, Any]
) -> list[UserVerdict]:
    """One verdict per labelled key person, from the search's stored Decisions."""
    people = _index(key)
    status: dict[str, str] = {}
    for decision in session.scalars(
        select(OutreachDecision).where(OutreachDecision.search_id == search_id)
    ):
        person = _person(session, decision.lead_id, people)
        if person is not None:
            status[person["subject"]] = decision.status
    labelled = {p["subject"]: p for p in people.values() if "usage" in p["expect"]}
    return [
        UserVerdict(
            scenario=p["scenario"],
            is_user=bool(p["expect"]["usage"]["is_user"]),
            status=status.get(subject),
        )
        for subject, p in labelled.items()
    ]


def score_users_search(
    session: Session, search_id: uuid.UUID, key: Mapping[str, Any]
) -> UsersScorecard:
    return score_users(users_verdicts(session, search_id, key))


def render_users(card: UsersScorecard) -> str:
    lines = ["Users mode against the answer key (is_user)", ""]
    lines += [
        f"precision: {_ratio(card.precision)}",
        f"recall: {_ratio(card.recall)}",
        f"f1: {_ratio(card.f1)}",
        "",
        f"{'scenario':<22} tp  fp  fn  tn",
    ]
    for name, r in sorted(card.scenarios.items()):
        lines.append(
            f"{name:<22} {r.true_positive:>2}  {r.false_positive:>2}  "
            f"{r.false_negative:>2}  {r.true_negative:>2}"
        )
    return "\n".join(lines) + "\n"


def _ratio(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.3f}"
