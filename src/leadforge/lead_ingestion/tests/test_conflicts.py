"""Field-conflict resolution under a total order (task 16.3, Requirements 8.4+)."""

import itertools
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from leadforge.lead_ingestion import conflicts
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import IdentityCluster, canonical_value_json
from leadforge.lead_ingestion.conflicts import (
    ConflictRule,
    FieldResolution,
    resolve_conflicts,
)
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    ConfidenceOrigin,
    DataMode,
    FieldProvenance,
    IntentSignal,
    SourceAbsence,
    UntrustedText,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
PATH = "person.title"
PS = ConfidenceOrigin.PROVIDER_STATED
HE = ConfidenceOrigin.HEURISTIC
NO = ConfidenceOrigin.NONE


def claim(
    source: str,
    value: Any,
    *,
    origin: ConfidenceOrigin = NO,
    confidence: float | None = None,
    at: datetime = NOW,
    path: str = PATH,
    absences: tuple[SourceAbsence, ...] = (),
) -> LeadContribution:
    stated = origin is PS
    provenance = FieldProvenance(
        canonical_path=path,
        source_name=source,
        data_mode=DataMode.SYNTHETIC,
        fetched_at=at,
        raw_field_path="raw",
        confidence_origin=origin,
        untrusted=isinstance(value, UntrustedText),
        confidence=confidence,
        confidence_raw=str(confidence) if stated else None,
        confidence_scale="unit" if stated else None,
    )
    return LeadContribution(
        source_name=source,
        values={path: value},
        provenance=(provenance,),
        absences=absences,
    )


def untrusted(text: str) -> UntrustedText:
    return UntrustedText(value=text, truncated=False, original_length=len(text))


def cluster(*members: LeadContribution) -> IdentityCluster:
    return IdentityCluster("cluster", tuple(members))


def winner_source(
    members: list[LeadContribution], ranks: dict[str, int] | None = None
) -> str:
    result = resolve_conflicts(cluster(*members), ranks or {})
    return result.field(PATH).winner.source_name


def order_of(
    members: list[LeadContribution], ranks: dict[str, int] | None = None
) -> list[tuple[str, str]]:
    resolution = resolve_conflicts(cluster(*members), ranks or {}).field(PATH)
    return [
        (c.source_name, c.value)
        for c in (resolution.winner, *resolution.agreeing, *resolution.superseded)
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_higher_trust_rank_wins_over_everything_else() -> None:
    a = claim("a", "A", origin=PS, confidence=1.0, at=NOW + timedelta(days=9))
    b = claim("b", "B", origin=HE, confidence=0.1)
    assert winner_source([a, b], {"a": 1, "b": 5}) == "b"


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_provider_stated_beats_heuristic_at_equal_rank_even_with_lower_number() -> None:
    a = claim("a", "A", origin=HE, confidence=0.99)
    b = claim("b", "B", origin=PS, confidence=0.2)
    assert winner_source([a, b]) == "b"


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_origin_none_is_outranked_by_heuristic_and_stated() -> None:
    n = claim("a", "N")
    h = claim("b", "H", origin=HE, confidence=0.0)
    p = claim("c", "P", origin=PS, confidence=0.0)
    assert [s for s, _ in order_of([n, h, p])] == ["c", "b", "a"]


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_higher_confidence_wins_within_one_origin() -> None:
    a = claim("a", "A", origin=PS, confidence=0.4)
    b = claim("b", "B", origin=PS, confidence=0.7)
    assert winner_source([a, b]) == "b"


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_confidence_is_compared_only_after_origin_and_never_for_origin_none() -> None:
    a = claim("a", "A", origin=HE, confidence=0.4)
    b = claim("b", "B", origin=HE, confidence=0.4)
    assert winner_source([a, b]) == "a"  # tie falls through to source name


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_more_recent_fetch_wins_when_rank_and_confidence_tie() -> None:
    a = claim("a", "A", at=NOW)
    b = claim("b", "B", at=NOW + timedelta(microseconds=1))
    assert winner_source([a, b]) == "b"


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_source_name_then_value_hash_break_remaining_ties() -> None:
    assert winner_source([claim("b", "B"), claim("a", "A")]) == "a"
    same_source = [claim("a", "X"), claim("a", "Y")]
    forward = order_of(same_source)
    assert forward == order_of(list(reversed(same_source)))
    assert {v for _, v in forward} == {"X", "Y"}


# Verifies: specs/lead-source-adapters/requirements.md#8.10
def test_undeclared_source_takes_the_lowest_rank() -> None:
    a = claim("a", "A")
    b = claim("b", "B")
    assert winner_source([a, b], {"b": 1}) == "b"
    assert winner_source([a, b], {"b": 0}) == "a"  # declared 0 equals undeclared


# Verifies: specs/lead-source-adapters/requirements.md#8.10
def test_changing_a_rank_changes_the_outcome_without_code_edit() -> None:
    a = claim("a", "A")
    b = claim("b", "B")
    assert winner_source([a, b], {"a": 2, "b": 1}) == "a"
    assert winner_source([a, b], {"a": 1, "b": 2}) == "b"


# Verifies: specs/lead-source-adapters/requirements.md#8.10
@pytest.mark.parametrize("bad", [-1, True, 1.5, "1"])
def test_invalid_rank_is_rejected_naming_the_source_only(bad: Any) -> None:
    with pytest.raises((TypeError, ValueError)) as caught:
        resolve_conflicts(cluster(claim("a", "SECRET-VALUE")), {"a": bad})
    assert "SECRET-VALUE" not in str(caught.value)
    assert "'a'" in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_losers_are_split_into_agreeing_and_superseded() -> None:
    members = [
        claim("a", "Chief", origin=PS, confidence=0.9),
        claim("b", "Chief"),
        claim("c", "Head"),
    ]
    field = resolve_conflicts(cluster(*members), {}).field(PATH)
    assert field.winner.source_name == "a"
    assert [c.source_name for c in field.agreeing] == ["b"]
    assert [c.source_name for c in field.superseded] == ["c"]
    # Provenance travels with each candidate, unmarked: retention is task 16.4.
    assert field.superseded[0].provenance.source_name == "c"
    assert field.superseded[0].provenance.superseded is False
    assert field.superseded[0].value == "Head"


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_a_single_contribution_resolves_to_itself_with_no_losers() -> None:
    field = resolve_conflicts(cluster(claim("a", "Only")), {}).field(PATH)
    assert field.winner.value == "Only"
    assert field.agreeing == ()
    assert field.superseded == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_each_path_resolves_independently_and_results_are_sorted_by_path() -> None:
    a = claim("a", "T-a", path="person.title")
    b = claim("b", "T-b", path="person.title")
    c = claim("a", "N-a", path="person.full_name")
    d = claim("b", "N-b", path="person.full_name")
    result = resolve_conflicts(cluster(a, b, c, d), {"b": 3})
    assert [f.canonical_path for f in result.fields] == [
        "person.full_name",
        "person.title",
    ]
    assert all(f.winner.source_name == "b" for f in result.fields)
    assert isinstance(result.fields[0], FieldResolution)


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_every_permutation_gives_identical_winner_and_loser_order() -> None:
    members = [
        claim("a", "X", origin=PS, confidence=0.5),
        claim("b", "Y", origin=PS, confidence=0.5),
        claim("c", "X", origin=HE, confidence=0.9),
        claim("d", "Z"),
        claim("e", "Y", origin=PS, confidence=0.5),  # ties b on everything but source
    ]
    expected = order_of(members, {"d": 0, "e": 0})
    for perm in itertools.permutations(members):
        assert order_of(list(perm), {"d": 0, "e": 0}) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_seeded_shuffles_of_a_larger_set_give_identical_results() -> None:
    members = [
        claim(
            f"s{i % 4}",
            f"V{i % 5}",
            origin=(PS, HE, NO)[i % 3],
            confidence=None if i % 3 == 2 else (i % 4) / 4,
            at=NOW + timedelta(seconds=i % 2),
        )
        for i in range(40)
    ]
    ranks = {"s0": 1, "s1": 1}
    baseline = resolve_conflicts(cluster(*members), ranks)
    for seed in range(60):
        shuffled = members[:]
        random.Random(seed).shuffle(shuffled)
        again = resolve_conflicts(cluster(*shuffled), ranks)
        assert again == baseline


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_fully_identical_claims_are_interchangeable() -> None:
    one = claim("a", "X")
    two = claim("a", "X")
    result = resolve_conflicts(cluster(one, two), {}).field(PATH)
    assert result.winner.value == "X"
    assert len(result.agreeing) == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_pure_input_not_mutated_and_result_is_repeatable() -> None:
    members = [claim("a", "A"), claim("b", "B")]
    before = [m.model_dump_json() for m in members]
    ranks = {"a": 1}
    first = resolve_conflicts(cluster(*members), ranks)
    assert [m.model_dump_json() for m in members] == before
    assert ranks == {"a": 1}
    assert resolve_conflicts(cluster(*members), ranks) == first


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_untrusted_text_values_stay_untrusted_and_do_not_leak_in_repr() -> None:
    a = claim("a", untrusted("SECRET-BIO"))
    b = claim("b", untrusted("OTHER-BIO"), at=NOW + timedelta(days=1))
    result = resolve_conflicts(cluster(a, b), {})
    field = result.field(PATH)
    assert isinstance(field.winner.value, UntrustedText)
    assert field.winner.source_name == "b"
    assert "SECRET-BIO" not in repr(result)
    assert "OTHER-BIO" not in repr(result)


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_absences_never_compete_and_are_exposed_by_kind() -> None:
    neg = SourceAbsence(
        canonical_path=PATH,
        source_name="n",
        kind=AbsenceKind.NEGATIVE_EVIDENCE,
        raw_field_path="raw.title",
    )
    na = SourceAbsence(
        canonical_path=PATH, source_name="z", kind=AbsenceKind.NOT_APPLICABLE
    )
    members = [
        claim("a", "A", absences=(na,)),
        claim("n", "B", absences=(neg,)),
    ]
    result = resolve_conflicts(cluster(*members), {"n": 9, "z": 9})
    assert result.negative_evidence == (neg,)
    assert result.not_applicable == (na,)
    field = result.field(PATH)
    assert {field.winner.source_name, *(c.source_name for c in field.superseded)} == {
        "a",
        "n",
    }
    flipped = resolve_conflicts(cluster(*reversed(members)), {"n": 9, "z": 9})
    assert flipped == result


# Verifies: specs/lead-source-adapters/requirements.md#1.9
def test_a_path_with_only_an_absence_has_no_field_resolution() -> None:
    na = SourceAbsence(
        canonical_path=PATH, source_name="z", kind=AbsenceKind.NOT_APPLICABLE
    )
    empty = LeadContribution(source_name="z", absences=(na,))
    result = resolve_conflicts(cluster(empty), {})
    assert result.fields == ()
    assert result.not_applicable == (na,)
    with pytest.raises(KeyError):
        result.field(PATH)


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_absence_does_not_unseat_a_value_even_from_a_higher_ranked_source() -> None:
    neg = SourceAbsence(
        canonical_path=PATH,
        source_name="hi",
        kind=AbsenceKind.NEGATIVE_EVIDENCE,
        raw_field_path="raw",
    )
    hi = LeadContribution(source_name="hi", absences=(neg,))
    result = resolve_conflicts(cluster(hi, claim("lo", "V")), {"hi": 9})
    assert result.field(PATH).winner.source_name == "lo"


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_signal_strength_never_changes_a_merge_outcome() -> None:
    def build(strengths: tuple[float, float, float]) -> list[LeadContribution]:
        def with_signal(source: str, strength: float, **kw: Any) -> LeadContribution:
            base = claim(source, source.upper(), **kw)
            signal = IntentSignal(label="hiring", strength=strength)
            provenance = FieldProvenance(
                canonical_path="signals.intent",
                source_name=source,
                data_mode=DataMode.SYNTHETIC,
                fetched_at=NOW,
                raw_field_path="raw",
                confidence_origin=NO,
                untrusted=False,
            )
            return LeadContribution(
                source_name=source,
                values={**base.values, "signals.intent": signal},
                provenance=(*base.provenance, provenance),
            )

        return [
            with_signal("a", strengths[0]),
            with_signal("b", strengths[1], origin=PS, confidence=0.3),
            with_signal("c", strengths[2], origin=HE, confidence=0.9),
        ]

    def outcome(strengths: tuple[float, float, float]) -> list[Any]:
        # Every path, signals included: winner, who agrees and who is superseded.
        result = resolve_conflicts(cluster(*build(strengths)), {"c": 1})
        return [
            (
                f.canonical_path,
                f.winner.source_name,
                [c.source_name for c in f.agreeing],
                [c.source_name for c in f.superseded],
            )
            for f in result.fields
        ]

    baseline = outcome((0.5, 0.5, 0.5))
    for combo in itertools.product((0.0, 0.2, 1.0), repeat=3):
        assert outcome(combo) == baseline  # type: ignore[arg-type]


def test_large_cluster_serialises_each_value_once_and_is_order_independent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[None] = []
    real = canonical_value_json

    def counting(value: Any) -> str:
        calls.append(None)
        return real(value)

    monkeypatch.setattr(conflicts, "canonical_value_json", counting)
    members = [
        claim(f"s{i % 50}", f"V{i % 7}", origin=HE, confidence=(i % 10) / 10)
        for i in range(3000)
    ]
    first = resolve_conflicts(cluster(*members), {})
    assert len(calls) == 3000
    random.Random(7).shuffle(members)
    assert resolve_conflicts(cluster(*members), {}) == first


def test_huge_untrusted_text_resolves_without_str_conversion() -> None:
    big = "x" * 2_000_000
    result = resolve_conflicts(
        cluster(claim("a", untrusted(big)), claim("b", untrusted(big))), {"a": 1}
    )
    field_ = result.field(PATH)
    assert field_.winner.source_name == "a"
    assert [c.source_name for c in field_.agreeing] == ["b"]


def test_same_source_twice_resolves_by_recency_regardless_of_order() -> None:
    old = claim("a", "OLD", at=NOW - timedelta(days=1))
    new = claim("a", "NEW", at=NOW)
    assert winner_source([old, new]) == winner_source([new, old]) == "a"
    assert order_of([old, new]) == order_of([new, old])
    assert order_of([old, new])[0] == ("a", "NEW")


# Task 16.12: the rule that decided a conflict, as data beside the winner.
def _decided(*members: LeadContribution, ranks: dict[str, int]) -> Any:
    return resolve_conflicts(cluster(*members), ranks).field(PATH).decided_by


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_deciding_rule_is_trust_rank() -> None:
    got = _decided(claim("a", "x"), claim("b", "y"), ranks={"a": 5, "b": 3})
    assert got is ConflictRule.TRUST_RANK


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_deciding_rule_is_confidence_origin() -> None:
    got = _decided(
        claim("a", "x", origin=PS, confidence=0.1),
        claim("b", "y", origin=HE, confidence=0.9),
        ranks={"a": 5, "b": 5},
    )
    assert got is ConflictRule.CONFIDENCE_ORIGIN


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_deciding_rule_is_confidence() -> None:
    got = _decided(
        claim("a", "x", origin=HE, confidence=0.9),
        claim("b", "y", origin=HE, confidence=0.1),
        ranks={"a": 5, "b": 5},
    )
    assert got is ConflictRule.CONFIDENCE


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_deciding_rule_is_recency() -> None:
    got = _decided(
        claim("a", "x", at=NOW),
        claim("b", "y", at=NOW - timedelta(days=1)),
        ranks={"a": 5, "b": 5},
    )
    assert got is ConflictRule.RECENCY


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_deciding_rule_is_the_tie_break_when_every_rule_ties() -> None:
    got = _decided(claim("a", "x"), claim("b", "y"), ranks={"a": 5, "b": 5})
    assert got is ConflictRule.TIE_BREAK


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_rule_is_measured_against_the_closest_loser() -> None:
    # c loses on trust rank, b ties with the winner down to recency: b is the closest.
    got = _decided(
        claim("a", "x", at=NOW),
        claim("b", "y", at=NOW - timedelta(days=1)),
        claim("c", "z"),
        ranks={"a": 5, "b": 5, "c": 1},
    )
    assert got is ConflictRule.RECENCY


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_no_rule_is_named_without_a_conflict() -> None:
    assert _decided(claim("a", "x"), ranks={"a": 5}) is None
    assert _decided(claim("a", "x"), claim("b", "x"), ranks={"a": 5, "b": 3}) is None


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_the_deciding_rule_ignores_arrival_order() -> None:
    members = [
        claim("a", "x", at=NOW),
        claim("b", "y", at=NOW - timedelta(days=1)),
        claim("c", "z"),
    ]
    ranks = {"a": 5, "b": 5, "c": 1}
    seen = {_decided(*perm, ranks=ranks) for perm in itertools.permutations(members)}
    assert seen == {ConflictRule.RECENCY}
