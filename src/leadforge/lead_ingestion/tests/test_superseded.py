"""Losing values retained as superseded provenance (task 16.4, Requirements 8.5-8.7)."""

import itertools
import random
from collections.abc import Iterable
from dataclasses import replace
from datetime import timedelta

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.conflicts import (
    ClusterResolution,
    FieldCandidate,
    resolve_conflicts,
)
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    DataMode,
    FieldProvenance,
    IntentSignal,
    SourceAbsence,
)
from leadforge.lead_ingestion.superseded import (
    agreeing_source_count,
    contributing_sources,
    provenance_records,
    retain_superseded,
)
from leadforge.lead_ingestion.tests.test_conflicts import (
    NO,
    NOW,
    PATH,
    PS,
    claim,
    cluster,
)

RANKS = {"a": 5, "b": 3, "c": 1, "d": 1}


def resolved(*members, ranks=None) -> ClusterResolution:  # type: ignore[no-untyped-def]
    return resolve_conflicts(cluster(*members), ranks or RANKS)


def three_way() -> list:  # type: ignore[type-arg]
    return [claim("a", "Head"), claim("b", "Head"), claim("c", "Dir")]


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_every_losing_value_keeps_its_own_record_marked_superseded() -> None:
    out = retain_superseded(resolved(*three_way()))
    field = out.field(PATH)
    assert [c.source_name for c in field.superseded] == ["c"]
    assert field.superseded[0].provenance.superseded is True
    assert field.superseded[0].provenance.source_name == "c"
    assert field.superseded[0].value == "Dir"


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_winner_and_agreeing_stay_unmarked_corroboration() -> None:
    field = retain_superseded(resolved(*three_way())).field(PATH)
    assert field.winner.provenance.superseded is False
    assert [c.source_name for c in field.agreeing] == ["b"]
    assert field.agreeing[0].provenance.superseded is False


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_marking_makes_new_objects_and_leaves_the_input_untouched() -> None:
    before = resolved(*three_way())
    out = retain_superseded(before)
    assert before.field(PATH).superseded[0].provenance.superseded is False
    assert out.field(PATH).superseded[0].provenance is not (
        before.field(PATH).superseded[0].provenance
    )
    assert out is not before


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_the_only_change_is_the_superseded_flag() -> None:
    before = resolved(*three_way()).field(PATH).superseded[0].provenance
    after = retain_superseded(resolved(*three_way())).field(PATH).superseded[0]
    assert after.provenance == before.model_copy(update={"superseded": True})


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_marking_twice_changes_nothing() -> None:
    once = retain_superseded(resolved(*three_way()))
    assert retain_superseded(once) == once


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_a_path_without_conflict_has_no_superseded_record() -> None:
    out = retain_superseded(resolved(claim("a", "Head"), claim("b", "Head")))
    assert out.field(PATH).superseded == ()
    assert all(not r.superseded for r in provenance_records(out))


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_records_are_every_candidate_by_path_then_winner_agreeing_superseded() -> None:
    members = [
        *three_way(),
        claim("a", "Acme", path="company.name"),
        claim("d", "Acme Inc", path="company.name"),
    ]
    records = provenance_records(retain_superseded(resolved(*members)))
    assert [(r.canonical_path, r.source_name, r.superseded) for r in records] == [
        ("company.name", "a", False),
        ("company.name", "d", True),
        (PATH, "a", False),
        (PATH, "b", False),
        (PATH, "c", True),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_records_do_not_depend_on_the_resolution_having_been_marked_first() -> None:
    raw = resolved(*three_way())
    assert provenance_records(raw) == provenance_records(retain_superseded(raw))


# Verifies: specs/lead-source-adapters/requirements.md#8.5
# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_stale_mark_on_a_winner_or_agreeing_record_is_recomputed_away() -> None:
    # The flag is derived: a mark left by an earlier run must not survive when the
    # record now wins or agrees, and must not crash the projection.
    field = resolved(*three_way()).field(PATH)

    def stale(candidate: FieldCandidate) -> FieldCandidate:
        return replace(
            candidate,
            provenance=candidate.provenance.model_copy(update={"superseded": True}),
        )

    dirty = ClusterResolution(
        (
            replace(
                field, winner=stale(field.winner), agreeing=(stale(field.agreeing[0]),)
            ),
        )
    )
    out = retain_superseded(dirty)
    assert out == retain_superseded(resolved(*three_way()))
    assert dirty.field(PATH).winner.provenance.superseded is True  # input untouched


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_no_losing_value_text_reaches_an_error_or_a_repr() -> None:
    secret = "ZZ-secret-person-text"
    field = resolved(claim("a", "Head"), claim("c", secret)).field(PATH)
    out = retain_superseded(ClusterResolution((field,)))
    assert secret not in repr(out)
    assert secret not in repr(out.field(PATH).superseded)
    assert secret not in repr(provenance_records(out))


# Verifies: specs/lead-source-adapters/requirements.md#8.5
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_output_is_identical_for_every_arrival_order() -> None:
    members = [
        claim("a", "Head"),
        claim("b", "Head", at=NOW + timedelta(days=1)),
        claim("c", "Dir", origin=PS, confidence=0.9),
        claim("d", "VP"),
        claim("d", "Acme", path="company.name"),
    ]
    expected = retain_superseded(resolved(*members))
    for order in itertools.permutations(members):
        assert retain_superseded(resolved(*order)) == expected
    shuffler = random.Random(16)
    for _ in range(50):
        shuffled = members[:]
        shuffler.shuffle(shuffled)
        assert provenance_records(retain_superseded(resolved(*shuffled))) == (
            provenance_records(expected)
        )


# Verifies: specs/lead-source-adapters/requirements.md#8.6
def test_each_field_resolves_to_the_source_that_supplied_the_winning_value() -> None:
    out = retain_superseded(resolved(*three_way()))
    assert out.field(PATH).winner.source_name == "a"
    assert out.field(PATH).winner.provenance.source_name == "a"


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_agreeing_source_count_includes_the_winner_and_counts_sources_once() -> None:
    out = retain_superseded(resolved(*three_way()))
    assert agreeing_source_count(out.field(PATH)) == 2
    lone = retain_superseded(resolved(claim("a", "x"), claim("c", "y")))
    assert agreeing_source_count(lone.field(PATH)) == 1
    twice = retain_superseded(
        resolved(claim("a", "x"), claim("a", "x", at=NOW + timedelta(days=1)))
    )
    assert agreeing_source_count(twice.field(PATH)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_contributing_sources_are_the_sorted_distinct_names_including_losers() -> None:
    absence = SourceAbsence(
        source_name="z",
        canonical_path="person.phone",
        kind=AbsenceKind.NOT_APPLICABLE,
    )
    members = [claim("d", "VP"), *three_way(), claim("a", "x", absences=(absence,))]
    out = retain_superseded(resolved(*members))
    assert contributing_sources(out) == ("a", "b", "c", "d", "z")


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_absences_are_carried_apart_and_never_become_provenance() -> None:
    neg = SourceAbsence(
        source_name="b",
        canonical_path=PATH,
        kind=AbsenceKind.NEGATIVE_EVIDENCE,
        raw_field_path="raw",
    )
    out = retain_superseded(resolved(claim("a", "Head", absences=(neg,))))
    assert out.negative_evidence == (neg,)
    assert out.not_applicable == ()
    assert [r.source_name for r in provenance_records(out)] == ["a"]


def _keys(records: Iterable[FieldProvenance]) -> list[tuple[str, str, str, str]]:
    return sorted(
        (r.source_name, r.canonical_path, r.raw_field_path, r.fetched_at.isoformat())
        for r in records
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_every_record_is_retained_exactly_once_even_with_duplicates() -> None:
    day = timedelta(days=1)
    members = [
        claim("a", "Head"),
        claim("b", "Head"),  # identical value from another source
        claim("b", "Head", at=NOW + day),  # same source, same field, twice
        claim("c", "Dir"),
        claim("c", "Dir2", at=NOW + 2 * day),  # same source, different values
        claim("d", "x", path="company.name"),
    ]
    sent = [r for m in members for r in m.provenance]
    out = provenance_records(retain_superseded(resolved(*members)))
    assert _keys(out) == _keys(sent)
    assert len(out) == len(sent)
    assert sum(r.superseded for r in out) == 2


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_superseded_set_equals_the_resolutions_and_flags_match() -> None:
    raw = resolved(*three_way(), claim("d", "VP"))
    out = retain_superseded(raw)
    for before, after in zip(raw.fields, out.fields, strict=True):
        assert [c.source_name for c in after.superseded] == [
            c.source_name for c in before.superseded
        ]
        assert [c.value for c in after.superseded] == [
            c.value for c in before.superseded
        ]
        assert all(c.provenance.superseded for c in after.superseded)
        assert not any(c.provenance.superseded for c in (after.winner, *after.agreeing))


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_exact_comparison_case_and_whitespace_make_a_different_value() -> None:
    out = retain_superseded(
        resolved(claim("a", "Head"), claim("b", "head"), claim("c", "Head "))
    )
    assert out.field(PATH).agreeing == ()
    assert [c.source_name for c in out.field(PATH).superseded] == ["b", "c"]


def _signal(source: str, label: str, strength: float, path: str) -> LeadContribution:
    provenance = FieldProvenance(
        canonical_path=path,
        source_name=source,
        data_mode=DataMode.SYNTHETIC,
        fetched_at=NOW,
        raw_field_path="raw",
        confidence_origin=NO,
        untrusted=False,
    )
    return LeadContribution(
        source_name=source,
        values={path: IntentSignal(label=label, strength=strength)},
        provenance=(provenance,),
    )


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_a_signal_differing_only_in_strength_is_not_superseded() -> None:
    out = retain_superseded(
        resolve_conflicts(
            cluster(
                _signal("a", "hiring", 0.1, "signals.intent"),
                _signal("b", "hiring", 0.9, "signals.intent"),
            ),
            RANKS,
        )
    )
    field = out.field("signals.intent")
    assert field.superseded == ()
    assert [c.source_name for c in field.agreeing] == ["b"]


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_whole_valued_signals_that_differ_mark_the_loser_superseded() -> None:
    # SPEC GAP (shared with 16.3): multi-valued fields are compared whole, not
    # unioned, so a different signal from a lower-ranked source is "superseded".
    out = retain_superseded(
        resolve_conflicts(
            cluster(
                _signal("a", "hiring", 0.5, "signals.intent"),
                _signal("c", "funding", 0.5, "signals.intent"),
            ),
            RANKS,
        )
    )
    assert [c.source_name for c in out.field("signals.intent").superseded] == ["c"]
    assert out.field("signals.intent").superseded[0].provenance.superseded is True


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_a_contribution_with_no_values_and_no_absences_is_not_visible() -> None:
    # Documents a known gap: ClusterResolution does not carry such a source.
    members = [claim("a", "Head"), LeadContribution(source_name="silent")]
    assert contributing_sources(retain_superseded(resolved(*members))) == ("a",)


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_idempotent_and_equal_to_the_unmarked_result_under_many_shuffles() -> None:
    members = [
        claim(s, v, at=NOW + timedelta(hours=i))
        for i, (s, v) in enumerate(
            [("a", "H"), ("b", "H"), ("b", "X"), ("c", "Y"), ("d", "H"), ("d", "Z")]
        )
    ]
    expected = retain_superseded(resolved(*members))
    shuffler = random.Random(164)
    for _ in range(100):
        shuffled = members[:]
        shuffler.shuffle(shuffled)
        out = retain_superseded(resolved(*shuffled))
        assert out == expected
        assert retain_superseded(out) == out
