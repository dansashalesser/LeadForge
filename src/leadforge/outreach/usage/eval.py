"""Live classifier eval: score a classifier against human-labelled cases.

Cases are real pages a person labelled with a ``Relationship``; the report is accuracy
overall and per expected relationship, a confusion row each, and the count of dropped
(unclassified) answers. A dropped answer is never a hit. Run by ``outreach usage-eval``;
the live run needs network and a key, so it is not part of CI (requirement 1.5).
"""

from dataclasses import dataclass, field
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from leadforge.lead_ingestion.catalog import Alias, CatalogProduct
from leadforge.outreach.usage.classify import Classifier
from leadforge.outreach.usage.fetch import Passages
from leadforge.outreach.usage.records import Relationship

__all__ = [
    "DEFAULT_CASES",
    "EvalCase",
    "EvalCaseError",
    "EvalReport",
    "RelationshipScore",
    "load_cases",
    "render_report",
    "run_eval",
]

DEFAULT_CASES = Path(__file__).with_name("eval_cases.yaml")
DROPPED = "dropped"


class EvalCaseError(ValueError):
    """The cases file is unreadable, empty or holds an invalid case."""


class EvalCase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    target: str
    product: str
    aliases: tuple[Alias, ...]
    passages: tuple[str, ...] = Field(min_length=1)
    expected: Relationship
    subject_is_target: bool = True
    note: str = ""


@dataclass
class RelationshipScore:
    total: int = 0
    correct: int = 0
    confusion: dict[str, int] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


@dataclass
class EvalReport:
    total: int = 0
    correct: int = 0
    dropped: int = 0
    per_relationship: dict[str, RelationshipScore] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0


def load_cases(path: Path) -> list[EvalCase]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise EvalCaseError(
            f"cannot read cases at {path}: {type(exc).__name__}"
        ) from exc
    items = raw.get("cases") if isinstance(raw, dict) else None
    if not isinstance(items, list) or not items:
        raise EvalCaseError(f"{path} holds no cases")
    try:
        cases = [EvalCase.model_validate(item) for item in items]
    except ValidationError as exc:
        raise EvalCaseError(f"invalid case in {path}: {exc}") from exc
    if len({c.id for c in cases}) != len(cases):
        raise EvalCaseError(f"duplicate case ids in {path}")
    return cases


def run_eval(cases: list[EvalCase], classifier: Classifier) -> EvalReport:
    report = EvalReport()
    for case in cases:
        product = CatalogProduct(key=case.product, aliases=case.aliases)
        judgement = classifier.classify(
            case.target, product, Passages(url="eval", passages=case.passages)
        )
        score = report.per_relationship.setdefault(
            case.expected.value, RelationshipScore()
        )
        score.total += 1
        report.total += 1
        if judgement is None:
            report.dropped += 1
            got = DROPPED
            hit = False
        else:
            got = judgement.relationship.value
            hit = (
                judgement.relationship is case.expected
                and judgement.subject_is_target_company == case.subject_is_target
            )
        score.confusion[got] = score.confusion.get(got, 0) + 1
        if hit:
            score.correct += 1
            report.correct += 1
    return report


def render_report(report: EvalReport) -> str:
    lines = [
        f"accuracy: {report.accuracy:.0%} ({report.correct}/{report.total})",
        f"dropped: {report.dropped}",
        "per relationship (expected: correct/total, answers):",
    ]
    for rel in Relationship:
        score = report.per_relationship.get(rel.value)
        if score is None:
            continue
        answers = ", ".join(f"{k}={v}" for k, v in sorted(score.confusion.items()))
        lines.append(
            f"  {rel.value}: {score.correct}/{score.total} ({score.accuracy:.0%})"
            f" -> {answers}"
        )
    return "\n".join(lines)
