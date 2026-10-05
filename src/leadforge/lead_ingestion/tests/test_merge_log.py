"""Per-merge log events (task 16.12, Requirement 21.4): kinds, no personal data.

Decision under test: "the matching key used" is logged as the Match Key KIND (and the
kinds that linked the cluster), never as the key value, which is personal data.
"""

import asyncio
import itertools
import json
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import structlog

from leadforge.lead_ingestion import merge_log
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import cluster_contributions
from leadforge.lead_ingestion.conflicts import ConflictRule
from leadforge.lead_ingestion.merge_log import (
    MAX_LOGGED_CONFLICTS,
    MergeLogEvent,
    log_merges,
    merge_log_events,
)
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
)
from leadforge.lead_ingestion.projection import (
    ProjectionResult,
    ResolvedConflict,
    project_lead,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
RANKS = {"a": 5, "b": 3, "c": 1}
CANARIES = (
    "zebulon.quixote@canary-mail.example",
    "hortensia.vandermeer@canary-corp.example",
    "Zebulon Quixote",
    "Hortensia Vandermeer",
    "linkedin.com/in/zebulon-canary",
    "canary-corp.example",
    "canary-mail.example",
    "zebulon",
    "hortensia",
)


def contribution(source: str, at: datetime = NOW, **values: Any) -> LeadContribution:
    mapped = {k.replace("__", "."): v for k, v in values.items()}
    provenance = tuple(
        FieldProvenance(
            canonical_path=path,
            source_name=source,
            data_mode=DataMode.SYNTHETIC,
            fetched_at=at,
            raw_field_path="raw",
            confidence_origin=ConfidenceOrigin.NONE,
            untrusted=False,
        )
        for path in mapped
    )
    return LeadContribution(source_name=source, values=mapped, provenance=provenance)


def canary_contributions() -> list[LeadContribution]:
    shared = "linkedin.com/in/zebulon-canary"
    return [
        contribution(
            "a",
            person__linkedin_url=shared,
            person__full_name="Zebulon Quixote",
            person__email="zebulon.quixote@canary-mail.example",
            person__title="CTO",
            company__domain="canary-mail.example",
        ),
        contribution(
            "b",
            person__linkedin_url=shared,
            person__full_name="Hortensia Vandermeer",
            person__email="hortensia.vandermeer@canary-corp.example",
            person__title="CEO",
            company__domain="canary-corp.example",
        ),
        contribution("c", person__linkedin_url=shared, person__title="CFO"),
    ]


def projections(members: list[LeadContribution]) -> list[ProjectionResult]:
    return [project_lead(c, RANKS) for c in cluster_contributions(members)]


def event_fields(members: list[LeadContribution]) -> list[dict[str, Any]]:
    return [e.log_fields() for e in merge_log_events(projections(members))]


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_merge_logs_its_match_key_kind_and_resolved_conflicts() -> None:
    (event,) = merge_log_events(projections(canary_contributions()))
    fields = event.log_fields()
    assert fields["match_key"] == "linkedin_url"
    assert fields["match_keys"] == ["linkedin_url"]
    assert fields["contributions"] == 3
    assert fields["sources"] == 3
    by_path = {c["path"]: c for c in fields["conflicts"]}
    assert by_path["person.title"] == {
        "path": "person.title",
        "winner": "a",
        "superseded": 2,
        "rule": "trust_rank",
    }
    assert set(by_path) == {
        "person.title",
        "person.full_name",
        "person.email",
        "company.domain",
    }
    assert fields["conflict_count"] == 4
    assert fields["conflicts_omitted"] == 0


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_cluster_that_combined_nothing_logs_no_event() -> None:
    single = [contribution("a", person__linkedin_url="linkedin.com/in/solo")]
    assert merge_log_events(projections(single)) == ()
    assert merge_log_events([]) == ()


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_merge_without_conflicts_logs_an_empty_conflict_list() -> None:
    members = [
        contribution("a", person__linkedin_url="linkedin.com/in/x"),
        contribution("b", person__linkedin_url="linkedin.com/in/x"),
    ]
    (fields,) = event_fields(members)
    assert fields["conflicts"] == []
    assert fields["conflict_count"] == 0


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_strongest_kind_is_the_match_key_when_several_linked_the_cluster() -> None:
    v = EmailStatus.VERIFIED
    members = [
        contribution("a", person__linkedin_url="linkedin.com/in/x"),
        contribution(
            "b",
            person__linkedin_url="linkedin.com/in/x",
            person__email="p@x.example",
            person__email_status=v,
        ),
        contribution("c", person__email="p@x.example", person__email_status=v),
    ]
    (fields,) = event_fields(members)
    assert fields["match_key"] == "linkedin_url"
    assert fields["match_keys"] == ["linkedin_url", "verified_email"]


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_every_permutation_of_arrival_gives_identical_events() -> None:
    members = canary_contributions()
    seen = {
        json.dumps(event_fields(list(p)), sort_keys=True)
        for p in itertools.permutations(members)
    }
    assert len(seen) == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.8
@pytest.mark.parametrize("seed", range(5))
def test_the_set_of_events_ignores_arrival_and_result_order(seed: int) -> None:
    rng = random.Random(seed)
    members = [
        *canary_contributions(),
        contribution("a", person__linkedin_url="linkedin.com/in/o", person__title="X"),
        contribution("b", person__linkedin_url="linkedin.com/in/o", person__title="Y"),
    ]
    baseline = sorted(json.dumps(f, sort_keys=True) for f in event_fields(members))
    shuffled = members[:]
    rng.shuffle(shuffled)
    results = projections(shuffled)
    rng.shuffle(results)
    got = sorted(
        json.dumps(e.log_fields(), sort_keys=True) for e in merge_log_events(results)
    )
    assert got == baseline


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_events_follow_the_order_of_the_results() -> None:
    results = projections(
        [
            contribution("a", person__linkedin_url="linkedin.com/in/1"),
            contribution("b", person__linkedin_url="linkedin.com/in/1"),
            contribution("a", person__linkedin_url="linkedin.com/in/2"),
            contribution("b", person__linkedin_url="linkedin.com/in/2"),
            contribution("c", person__linkedin_url="linkedin.com/in/2"),
        ]
    )
    counts = [e.contributions for e in merge_log_events(results)]
    assert counts == [r.contribution_count for r in results if r.contribution_count > 1]


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_log_volume_is_bounded_for_a_cluster_with_thousands_of_conflicts() -> None:
    n = MAX_LOGGED_CONFLICTS * 100
    a = contribution("a", person__linkedin_url="linkedin.com/in/big")
    b = contribution("b", person__linkedin_url="linkedin.com/in/big")
    a = _with_paths(a, n, "x")
    b = _with_paths(b, n, "y")
    (event,) = merge_log_events(projections([a, b]))
    fields = event.log_fields()
    assert len(fields["conflicts"]) == MAX_LOGGED_CONFLICTS
    assert fields["conflict_count"] == n
    assert fields["conflicts_omitted"] == n - MAX_LOGGED_CONFLICTS
    paths = [c["path"] for c in fields["conflicts"]]
    assert paths == sorted(paths)


def _with_paths(base: LeadContribution, n: int, value: str) -> LeadContribution:
    values = dict(base.values)
    provenance = list(base.provenance)
    for i in range(n):
        path = f"extra.f{i:05d}"
        values[path] = value
        provenance.append(
            base.provenance[0].model_copy(update={"canonical_path": path})
        )
    return LeadContribution(
        source_name=base.source_name, values=values, provenance=tuple(provenance)
    )


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_emitter_logs_one_info_line_per_event() -> None:
    results = projections(canary_contributions())
    with structlog.testing.capture_logs() as logs:
        outcome = log_merges(results)
    assert [(e["event"], e["log_level"]) for e in logs] == [("lead_merge", "info")]
    assert logs[0]["match_key"] == "linkedin_url"
    assert (outcome.emitted, outcome.failed) == (1, 0)


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_no_personal_value_reaches_any_log_output_or_repr() -> None:
    members = canary_contributions()
    results = projections(members)
    cluster_ids = [c.cluster_id for c in cluster_contributions(members)]
    events = merge_log_events(results)
    with structlog.testing.capture_logs() as logs:
        log_merges(results)
    blobs = [repr(logs), json.dumps(logs, default=repr), repr(events), str(events)]
    blobs += [repr(e) for e in events] + [repr(r) for r in results]
    for blob in blobs:
        lowered = blob.lower()
        for canary in (*CANARIES, *cluster_ids):
            assert canary.lower() not in lowered


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_rendered_json_line_carries_no_personal_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    try:
        log_merges(projections(canary_contributions()))
    finally:
        structlog.reset_defaults()
    out = capsys.readouterr().out.lower()
    assert "lead_merge" in out
    for canary in CANARIES:
        assert canary.lower() not in out


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_logging_never_changes_the_run_result() -> None:
    members = canary_contributions()
    clusters = cluster_contributions(members)
    before = [project_lead(c, RANKS) for c in clusters]
    log_merges(before)
    after = [project_lead(c, RANKS) for c in cluster_contributions(members)]
    assert before == after
    assert cluster_contributions(members) == clusters


class _Boom:
    def info(self, *_: object, **__: object) -> None:
        raise RuntimeError("zebulon.quixote@canary-mail.example")


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_failing_logger_never_aborts_the_run_and_is_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(merge_log, "_log", _Boom())
    results = projections(canary_contributions()) * 2
    outcome = log_merges(results)
    assert (outcome.emitted, outcome.failed) == (0, 2)
    assert "zebulon" not in repr(outcome).lower()


class _Cancel:
    def info(self, *_: object, **__: object) -> None:
        raise asyncio.CancelledError


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_cancellation_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_log, "_log", _Cancel())
    with pytest.raises(asyncio.CancelledError):
        log_merges(projections(canary_contributions()))


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_odd_input_never_raises() -> None:
    odd = ProjectionResult(
        lead=None,
        contributing_sources=(),
        agreement=(),
        provenance=(),
        negative_evidence=(),
        not_applicable=(),
        opt_out=False,
        suppressed=False,
        contribution_count=2,
    )
    (event,) = merge_log_events([odd])
    assert isinstance(event, MergeLogEvent)
    assert event.log_fields()["match_key"] is None
    assert log_merges([odd]).emitted == 1
    assert log_merges([]).emitted == 0


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_the_recency_rule_is_logged_when_it_decides() -> None:
    members = [
        contribution("a", at=NOW, person__linkedin_url="l/x", person__title="CTO"),
        contribution(
            "a",
            at=NOW - timedelta(days=1),
            person__linkedin_url="l/x",
            person__title="CEO",
        ),
    ]
    (fields,) = event_fields(members)
    (conflict,) = fields["conflicts"]
    assert conflict["rule"] == "recency"


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_programming_error_building_the_line_is_not_hidden_by_the_emitter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(_self: MergeLogEvent) -> dict[str, Any]:
        raise TypeError("builder bug")

    monkeypatch.setattr(MergeLogEvent, "log_fields", broken)
    with pytest.raises(TypeError, match="builder bug"):
        log_merges(projections(canary_contributions()))


# Verifies: specs/lead-source-adapters/requirements.md#21.4
@pytest.mark.parametrize("exc", [KeyboardInterrupt, SystemExit, asyncio.CancelledError])
def test_no_base_exception_is_swallowed_by_the_emitter(
    monkeypatch: pytest.MonkeyPatch, exc: type[BaseException]
) -> None:
    class _Raise:
        def info(self, *_: object, **__: object) -> None:
            raise exc

    monkeypatch.setattr(merge_log, "_log", _Raise())
    with pytest.raises(exc):
        log_merges(projections(canary_contributions()))


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_real_renderer_and_every_new_record_carry_no_personal_value(
    capsys: pytest.CaptureFixture[str],
) -> None:
    members = canary_contributions()
    clusters = cluster_contributions(members)
    results = [project_lead(c, RANKS) for c in clusters]
    structlog.configure(
        processors=[structlog.dev.ConsoleRenderer(colors=False)],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    try:
        log_merges(results)
    finally:
        structlog.reset_defaults()
    blobs = [capsys.readouterr().out]
    for c in clusters:
        blobs += [repr(c), repr(c.merged_by)]
    for r in results:
        blobs += [repr(r.match_keys), repr(r.conflicts)]
        blobs += [repr(x) + repr(x.decided_by) for x in r.conflicts]
    assert "lead_merge" in blobs[0]
    for blob in blobs:
        for canary in CANARIES:
            assert canary.lower() not in blob.lower()


# Verifies: specs/lead-source-adapters/requirements.md#21.4
@pytest.mark.parametrize("seed", range(3))
def test_the_cap_keeps_the_same_first_paths_for_any_order_of_the_conflicts(
    seed: int,
) -> None:
    conflicts = [
        ResolvedConflict(f"extra.f{i:03d}", "a", 1, ConflictRule.RECENCY)
        for i in range(MAX_LOGGED_CONFLICTS + 10)
    ]
    random.Random(seed).shuffle(conflicts)
    result = ProjectionResult(
        lead=None,
        contributing_sources=("a", "b"),
        agreement=(),
        provenance=(),
        negative_evidence=(),
        not_applicable=(),
        opt_out=False,
        suppressed=False,
        conflicts=tuple(conflicts),
        contribution_count=2,
    )
    (event,) = merge_log_events([result])
    assert [c.canonical_path for c in event.conflicts] == [
        f"extra.f{i:03d}" for i in range(MAX_LOGGED_CONFLICTS)
    ]
    assert event.conflict_count == MAX_LOGGED_CONFLICTS + 10
