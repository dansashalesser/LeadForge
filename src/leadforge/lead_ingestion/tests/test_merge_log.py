"""Per-merge log events (task 16.12, Requirement 21.4): no personal data.

Decisions under test: "the matching key used" is logged as the Match Key KIND (and the
kinds that linked the cluster) plus, per user decision, a keyed HMAC-SHA256 digest of
each key value that linked it; never the value, never a plain hash of it.
"""

import asyncio
import hashlib
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
from leadforge.lead_ingestion.match_key_digest import (
    MATCH_KEY_SECRET_ENV,
    MatchKeyDigester,
    match_key_digester_from_environ,
)
from leadforge.lead_ingestion.match_keys import MatchKey, MatchKeyKind
from leadforge.lead_ingestion.merge_log import (
    MAX_LOGGED_CONFLICTS,
    MAX_LOGGED_MATCH_KEYS,
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
SECRET = "merge-log-sentinel-secret-" + "s" * 32
DIGESTER = match_key_digester_from_environ({MATCH_KEY_SECRET_ENV: SECRET})
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
    return [
        e.log_fields()
        for e in merge_log_events(projections(members), digester=DIGESTER)
    ]


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_merge_logs_its_match_key_kind_and_resolved_conflicts() -> None:
    (event,) = merge_log_events(projections(canary_contributions()), digester=DIGESTER)
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
    assert merge_log_events(projections(single), digester=DIGESTER) == ()
    assert merge_log_events([], digester=DIGESTER) == ()


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
        json.dumps(e.log_fields(), sort_keys=True)
        for e in merge_log_events(results, digester=DIGESTER)
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
    counts = [e.contributions for e in merge_log_events(results, digester=DIGESTER)]
    assert counts == [r.contribution_count for r in results if r.contribution_count > 1]


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_log_volume_is_bounded_for_a_cluster_with_thousands_of_conflicts() -> None:
    n = MAX_LOGGED_CONFLICTS * 100
    a = contribution("a", person__linkedin_url="linkedin.com/in/big")
    b = contribution("b", person__linkedin_url="linkedin.com/in/big")
    a = _with_paths(a, n, "x")
    b = _with_paths(b, n, "y")
    (event,) = merge_log_events(projections([a, b]), digester=DIGESTER)
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
        outcome = log_merges(results, digester=DIGESTER)
    assert [(e["event"], e["log_level"]) for e in logs] == [("lead_merge", "info")]
    assert logs[0]["match_key"] == "linkedin_url"
    assert (outcome.emitted, outcome.failed) == (1, 0)


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_no_personal_value_reaches_any_log_output_or_repr() -> None:
    members = canary_contributions()
    results = projections(members)
    cluster_ids = [c.cluster_id for c in cluster_contributions(members)]
    events = merge_log_events(results, digester=DIGESTER)
    with structlog.testing.capture_logs() as logs:
        log_merges(results, digester=DIGESTER)
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
        log_merges(projections(canary_contributions()), digester=DIGESTER)
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
    log_merges(before, digester=DIGESTER)
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
    outcome = log_merges(results, digester=DIGESTER)
    assert (outcome.emitted, outcome.failed) == (0, 2)
    assert "zebulon" not in repr(outcome).lower()


class _Cancel:
    def info(self, *_: object, **__: object) -> None:
        raise asyncio.CancelledError


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_cancellation_is_not_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(merge_log, "_log", _Cancel())
    with pytest.raises(asyncio.CancelledError):
        log_merges(projections(canary_contributions()), digester=DIGESTER)


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
    (event,) = merge_log_events([odd], digester=DIGESTER)
    assert isinstance(event, MergeLogEvent)
    assert event.log_fields()["match_key"] is None
    assert log_merges([odd], digester=DIGESTER).emitted == 1
    assert log_merges([], digester=DIGESTER).emitted == 0


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
        log_merges(projections(canary_contributions()), digester=DIGESTER)


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
        log_merges(projections(canary_contributions()), digester=DIGESTER)


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
        log_merges(results, digester=DIGESTER)
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
    (event,) = merge_log_events([result], digester=DIGESTER)
    assert [c.canonical_path for c in event.conflicts] == [
        f"extra.f{i:03d}" for i in range(MAX_LOGGED_CONFLICTS)
    ]
    assert event.conflict_count == MAX_LOGGED_CONFLICTS + 10


def digest_entry(digester: MatchKeyDigester, kind: MatchKeyKind, value: str) -> Any:
    return {"kind": kind.name.lower(), "digest": digester.digest(MatchKey(kind, value))}


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_merge_logs_a_keyed_digest_of_the_key_value_that_linked_it() -> None:
    (fields,) = event_fields(canary_contributions())
    assert fields["match_key_digests"] == [
        digest_entry(
            DIGESTER, MatchKeyKind.LINKEDIN_URL, "linkedin.com/in/zebulon-canary"
        )
    ]
    assert fields["match_key_digests_omitted"] == 0


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_every_key_value_that_linked_the_cluster_is_digested_strongest_first() -> None:
    v = EmailStatus.VERIFIED
    members = [
        contribution("a", person__linkedin_url="linkedin.com/in/x"),
        contribution(
            "b",
            person__linkedin_url="linkedin.com/in/x",
            person__email="P@X.example",
            person__email_status=v,
        ),
        contribution("c", person__email="p@x.example", person__email_status=v),
    ]
    (fields,) = event_fields(members)
    assert fields["match_key_digests"] == [
        digest_entry(DIGESTER, MatchKeyKind.LINKEDIN_URL, "linkedin.com/in/x"),
        digest_entry(DIGESTER, MatchKeyKind.VERIFIED_EMAIL, "p@x.example"),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_key_shared_by_already_linked_members_is_not_reported_as_used() -> None:
    v = EmailStatus.VERIFIED
    members = [
        contribution(
            "a",
            person__linkedin_url="linkedin.com/in/x",
            person__email="p@x.example",
            person__email_status=v,
        ),
        contribution(
            "b",
            person__linkedin_url="linkedin.com/in/x",
            person__email="p@x.example",
            person__email_status=v,
        ),
    ]
    (fields,) = event_fields(members)
    assert fields["match_keys"] == ["linkedin_url"]
    assert [d["kind"] for d in fields["match_key_digests"]] == ["linkedin_url"]


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_same_secret_same_line_and_another_secret_another_digest() -> None:
    other = match_key_digester_from_environ({MATCH_KEY_SECRET_ENV: "o" * 40})
    results = projections(canary_contributions())
    (a,) = merge_log_events(results, digester=DIGESTER)
    (b,) = merge_log_events(
        results,
        digester=match_key_digester_from_environ({MATCH_KEY_SECRET_ENV: SECRET}),
    )
    (c,) = merge_log_events(results, digester=other)
    assert a.log_fields() == b.log_fields()
    assert a.log_fields()["match_key_digests"] != c.log_fields()["match_key_digests"]


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_logged_digest_is_not_a_plain_hash_of_the_value() -> None:
    (fields,) = event_fields(canary_contributions())
    (entry,) = fields["match_key_digests"]
    value = "linkedin.com/in/zebulon-canary"
    for text in (value, f"linkedin_url\x1f{value}", "https://" + value):
        assert hashlib.sha256(text.encode()).hexdigest()[:16] != entry["digest"]
        assert entry["digest"] not in hashlib.sha256(text.encode()).hexdigest()


# Verifies: specs/lead-source-adapters/requirements.md#21.3
def test_the_secret_never_reaches_any_rendered_line_or_repr(
    capsys: pytest.CaptureFixture[str],
) -> None:
    results = projections(canary_contributions())
    structlog.configure(
        processors=[structlog.processors.JSONRenderer()],
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=False,
    )
    try:
        log_merges(results, digester=DIGESTER)
    finally:
        structlog.reset_defaults()
    with structlog.testing.capture_logs() as logs:
        log_merges(results, digester=DIGESTER)
    events = merge_log_events(results, digester=DIGESTER)
    blob = capsys.readouterr().out + json.dumps(logs) + repr(events) + repr(DIGESTER)
    assert "match_key_digests" in blob
    assert SECRET not in blob
    assert SECRET.encode().hex() not in blob
    for canary in CANARIES:
        assert canary.lower() not in blob.lower()


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_digest_list_is_capped_with_an_omitted_count() -> None:
    keys = tuple(
        MatchKey(MatchKeyKind.NAME_DOMAIN, f"n{i:03d}\x1facme.example")
        for i in range(MAX_LOGGED_MATCH_KEYS + 7)
    )
    result = ProjectionResult(
        lead=None,
        contributing_sources=("a", "b"),
        agreement=(),
        provenance=(),
        negative_evidence=(),
        not_applicable=(),
        opt_out=False,
        suppressed=False,
        match_keys=(MatchKeyKind.NAME_DOMAIN,),
        linking_keys=tuple(reversed(keys)),
        contribution_count=2,
    )
    (event,) = merge_log_events([result], digester=DIGESTER)
    fields = event.log_fields()
    assert len(fields["match_key_digests"]) == MAX_LOGGED_MATCH_KEYS
    assert fields["match_key_digests_omitted"] == 7
    expected = sorted(DIGESTER.digest(k) for k in keys)[:MAX_LOGGED_MATCH_KEYS]
    assert [d["digest"] for d in fields["match_key_digests"]] == expected


# Verifies: specs/lead-source-adapters/requirements.md#21.3
def test_linking_keys_are_withheld_from_cluster_and_result_repr() -> None:
    members = canary_contributions()
    (cluster,) = cluster_contributions(members)
    (result,) = projections(members)
    assert cluster.linked_by == (
        MatchKey(MatchKeyKind.LINKEDIN_URL, "linkedin.com/in/zebulon-canary"),
    )
    assert result.linking_keys == cluster.linked_by
    for blob in (repr(cluster), repr(result)):
        assert "zebulon" not in blob.lower()
