"""Live classifier eval (requirement 1.5): case format, scoring and report.

Not part of CI as a live run: these tests use the offline classifier and no network.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from leadforge.cli import app
from leadforge.lead_ingestion.catalog import CatalogProduct
from leadforge.outreach.errors import UsageClassifierUnavailableError
from leadforge.outreach.usage.classify import Judgement, OfflineClassifier
from leadforge.outreach.usage.cues import UsageCues
from leadforge.outreach.usage.eval import (
    DEFAULT_CASES,
    EvalCase,
    EvalCaseError,
    load_cases,
    render_report,
    run_eval,
)
from leadforge.outreach.usage.fetch import Passages
from leadforge.outreach.usage.records import Relationship

runner = CliRunner()


class _Fixed:
    def __init__(self, answers: list[Judgement | None]) -> None:
        self._answers = answers

    def classify(
        self, target: str, product: CatalogProduct, passages: Passages
    ) -> Judgement | None:
        return self._answers.pop(0)


def _case(cid: str, expected: str, subject: bool = True) -> EvalCase:
    return EvalCase.model_validate(
        {
            "id": cid,
            "target": "Acme",
            "product": "product_a",
            "aliases": [{"text": "Product A"}],
            "passages": ["x"],
            "expected": expected,
            "subject_is_target": subject,
        }
    )


def _j(rel: Relationship, subject: bool = True) -> Judgement:
    return Judgement(
        subject_is_target_company=subject,
        product_key="product_a",
        relationship=rel,
        quotes=(),
        confidence=0.7,
    )


# Verifies: specs/user-recognition/requirements.md#1.5
def test_seed_cases_load_and_cover_every_relationship() -> None:
    cases = load_cases(DEFAULT_CASES)
    assert len(cases) >= 20
    assert {c.expected for c in cases} == set(Relationship)
    assert len({c.id for c in cases}) == len(cases)


# Verifies: specs/user-recognition/requirements.md#1.5
def test_report_scores_per_relationship_confusion_and_dropped() -> None:
    cases = [
        _case("a", "uses_now"),
        _case("b", "uses_now"),
        _case("c", "evaluating"),
        _case("d", "unrelated"),
    ]
    clf = _Fixed(
        [
            _j(Relationship.USES_NOW),
            _j(Relationship.MENTIONS_ONLY),
            None,
            _j(Relationship.UNRELATED),
        ]
    )
    report = run_eval(cases, clf)
    assert report.total == 4
    assert report.correct == 2
    assert report.dropped == 1
    assert report.accuracy == 0.5
    assert report.per_relationship["uses_now"].correct == 1
    assert report.per_relationship["uses_now"].total == 2
    assert report.per_relationship["uses_now"].confusion == {
        "uses_now": 1,
        "mentions_only": 1,
    }
    assert report.per_relationship["evaluating"].confusion == {"dropped": 1}
    text = render_report(report)
    for word in ("accuracy", "uses_now", "dropped"):
        assert word in text


# Verifies: specs/user-recognition/requirements.md#1.5
def test_a_dropped_answer_is_never_a_hit() -> None:
    # A dropped answer is never a hit: the report must not flatter the classifier.
    report = run_eval([_case("a", "unrelated")], _Fixed([None]))
    assert report.correct == 0
    assert report.dropped == 1


# Verifies: specs/user-recognition/requirements.md#1.5
def test_wrong_subject_is_a_miss() -> None:
    report = run_eval(
        [_case("a", "uses_now", subject=True)],
        _Fixed([_j(Relationship.USES_NOW, subject=False)]),
    )
    assert report.correct == 0


def test_empty_case_set_is_rejected(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text("cases: []\n")
    with pytest.raises(EvalCaseError):
        load_cases(f)


def test_unknown_relationship_label_is_rejected(tmp_path: Path) -> None:
    f = tmp_path / "c.yaml"
    f.write_text(
        "cases: [{id: a, target: T, product: p, aliases: [{text: P}],"
        " passages: [x], expected: bogus}]\n"
    )
    with pytest.raises(EvalCaseError):
        load_cases(f)


def test_seed_cases_score_on_the_offline_classifier() -> None:
    report = run_eval(load_cases(DEFAULT_CASES), OfflineClassifier(UsageCues()))
    assert report.total >= 20
    assert 0.0 <= report.accuracy <= 1.0


def test_cli_offline_prints_accuracy() -> None:
    result = runner.invoke(app, ["outreach", "usage-eval"])
    assert result.exit_code == 0, result.output
    assert "accuracy" in result.output


def test_cli_llm_without_factory_fails_clearly() -> None:
    result = runner.invoke(app, ["outreach", "usage-eval", "--classifier", "llm"])
    assert result.exit_code != 0
    assert isinstance(result.exception, UsageClassifierUnavailableError) or (
        "classifier" in result.output.lower()
    )


def test_cli_rejects_unknown_classifier() -> None:
    result = runner.invoke(app, ["outreach", "usage-eval", "--classifier", "x"])
    assert result.exit_code != 0
