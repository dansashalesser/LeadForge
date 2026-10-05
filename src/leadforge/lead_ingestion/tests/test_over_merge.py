"""Over-merge detector (task 16.8, Requirement 8.15): flags, never blocks or repairs."""

import itertools
import random
from datetime import UTC, datetime
from typing import Any

import pytest
import structlog

from leadforge.lead_ingestion import over_merge
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import (
    IdentityCluster,
    canonical_json,
    cluster_contributions,
)
from leadforge.lead_ingestion.match_keys import normalized_person_name
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
)
from leadforge.lead_ingestion.over_merge import (
    OverMergeReason,
    detect_over_merges,
)
from leadforge.lead_ingestion.projection import project_lead

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
V = EmailStatus.VERIFIED
SECRET_NAME = "Zebulon Quixote"
SECRET_OTHER = "Hortensia Vandermeer"


def contribution(source: str = "s", **values: Any) -> LeadContribution:
    mapped = {k.replace("__", "."): v for k, v in values.items()}
    provenance = tuple(
        FieldProvenance(
            canonical_path=path,
            source_name=source,
            data_mode=DataMode.SYNTHETIC,
            fetched_at=NOW,
            raw_field_path="raw",
            confidence_origin=ConfidenceOrigin.NONE,
            untrusted=False,
        )
        for path in mapped
    )
    return LeadContribution(source_name=source, values=mapped, provenance=provenance)


def named(source: str, name: str | None, **extra: Any) -> LeadContribution:
    """A contribution on the shared LinkedIn URL, so named people merge."""
    values: dict[str, Any] = {"person__linkedin_url": "linkedin.com/in/shared"}
    if name is not None:
        values["person__full_name"] = name
    return contribution(source, **values, **extra)


def cluster_of(*members: LeadContribution) -> IdentityCluster:
    (cluster,) = cluster_contributions(members)
    return cluster


def over_merged() -> IdentityCluster:
    return cluster_of(named("a", SECRET_NAME), named("b", SECRET_OTHER))


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_a_cluster_with_two_distinct_names_is_named_on_the_report() -> None:
    cluster = over_merged()
    (report,) = detect_over_merges([cluster])
    assert report.cluster_id == cluster.cluster_id
    assert report.reasons == (OverMergeReason.DISTINCT_FULL_NAMES,)
    assert report.distinct_name_count == 2
    assert report.member_count == 2


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_a_correctly_merged_cluster_is_not_named() -> None:
    cluster = cluster_of(named("a", "Jane Doe"), named("b", "  JANE   doe "))
    assert len(cluster.contributions) == 2
    assert detect_over_merges([cluster]) == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_names_match_under_normalisation_not_by_text() -> None:
    # NFC vs NFD, NBSP, case and spacing are one name under the match-key rules.
    nfc = "Jos" + chr(0xE9) + " Garc" + chr(0xED) + "a"
    nfd = "Jose" + chr(0x301) + "  GARCI" + chr(0x301) + "A"
    nbsp = "jos" + chr(0xE9) + chr(0xA0) + "garc" + chr(0xED) + "a"
    cluster = cluster_of(named("a", nfc), named("b", nfd), named("c", nbsp))
    assert detect_over_merges([cluster]) == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.15
@pytest.mark.parametrize(
    "members",
    [
        [("a", None), ("b", "Jane Doe")],  # null name is not a distinct name
        [("a", None), ("b", None)],
        [("a", "Jane D*"), ("b", "Jane Doe")],  # masked name is not a name
        [("a", "J**"), ("b", "   ")],
        [("a", "Jane Doe")],
    ],
)
def test_null_blank_and_masked_names_are_not_distinct_names(
    members: list[tuple[str, str | None]],
) -> None:
    cluster = cluster_of(*(named(s, n) for s, n in members))
    assert detect_over_merges([cluster]) == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_first_plus_last_name_counts_like_a_full_name() -> None:
    first_last = contribution(
        "a",
        person__linkedin_url="linkedin.com/in/shared",
        person__first_name="Ann",
        person__last_name="Lee",
    )
    cluster = cluster_of(first_last, named("b", "Bob Ray"))
    (report,) = detect_over_merges([cluster])
    assert report.distinct_name_count == 2


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_three_names_are_counted() -> None:
    cluster = cluster_of(named("a", "A A"), named("b", "B B"), named("c", "C C"))
    (report,) = detect_over_merges([cluster])
    assert (report.distinct_name_count, report.member_count) == (3, 3)


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_the_known_bridge_path_is_flagged() -> None:
    # Two LinkedIn URLs, one verified email, bridged by a record with no LinkedIn.
    one = contribution(
        "a",
        person__linkedin_url="linkedin.com/in/one",
        person__email="x@acme.com",
        person__email_status=V,
        person__full_name="Ann One",
    )
    two = contribution(
        "b",
        person__linkedin_url="linkedin.com/in/two",
        person__email="x@acme.com",
        person__email_status=V,
        person__full_name="Bob Two",
    )
    bridge = contribution("c", person__email="x@acme.com", person__email_status=V)
    clusters = cluster_contributions([one, two, bridge])
    flagged = detect_over_merges(clusters)
    assert [r.cluster_id for r in flagged] == [
        c.cluster_id for c in clusters if len(c.contributions) == 3
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_only_the_over_merged_cluster_of_several_is_named() -> None:
    good = cluster_of(
        contribution(
            "g1", person__linkedin_url="linkedin.com/in/g", person__full_name="G G"
        ),
        contribution(
            "g2", person__linkedin_url="linkedin.com/in/g", person__full_name="G G"
        ),
    )
    bad = over_merged()
    reports = detect_over_merges([good, bad])
    assert [r.cluster_id for r in reports] == [bad.cluster_id]


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_reports_are_sorted_by_cluster_id() -> None:
    clusters = [
        cluster_of(
            contribution(
                f"a{i}",
                person__linkedin_url=f"linkedin.com/in/p{i}",
                person__full_name="A A",
            ),
            contribution(
                f"b{i}",
                person__linkedin_url=f"linkedin.com/in/p{i}",
                person__full_name="B B",
            ),
        )
        for i in range(6)
    ]
    ids = [r.cluster_id for r in detect_over_merges(clusters)]
    assert len(ids) == 6
    assert ids == sorted(ids)


def pool() -> list[LeadContribution]:
    return [
        named("a", "Ann One"),
        named("b", "Bob Two"),
        contribution(
            "c", person__linkedin_url="linkedin.com/in/k", person__full_name="K K"
        ),
        contribution(
            "d", person__linkedin_url="linkedin.com/in/k", person__full_name="k k"
        ),
        contribution("e", person__full_name="Solo"),
        contribution("f", person__email="q@z.com", person__email_status=V),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_every_permutation_of_clusters_and_members_gives_identical_reports() -> None:
    baseline = detect_over_merges(cluster_contributions(pool()))
    assert len(baseline) == 1
    for ordering in itertools.permutations(pool()):
        clusters = cluster_contributions(ordering)
        for cluster_order in itertools.islice(itertools.permutations(clusters), 24):
            assert detect_over_merges(cluster_order) == baseline
    # also member order inside a cluster, which the input to the detector may vary
    (bad,) = [
        c
        for c in cluster_contributions(pool())
        if len(c.contributions) == 2 and c.cluster_id == baseline[0].cluster_id
    ]
    for members in itertools.permutations(bad.contributions):
        reordered = IdentityCluster(bad.cluster_id, members)
        assert detect_over_merges([reordered]) == baseline


# Verifies: specs/lead-source-adapters/requirements.md#8.15
@pytest.mark.parametrize("seed", range(10))
def test_seeded_shuffles_give_identical_reports(seed: int) -> None:
    rng = random.Random(seed)
    big: list[LeadContribution] = []
    for i in range(40):
        url = f"linkedin.com/in/p{i}"
        names = ["Same Name", "Same Name"] if i % 3 else ["One Name", "Two Name"]
        for j, name in enumerate(names):
            big.append(
                contribution(
                    f"s{i}_{j}", person__linkedin_url=url, person__full_name=name
                )
            )
    baseline = detect_over_merges(cluster_contributions(big))
    assert len(baseline) == 14
    shuffled = list(big)
    rng.shuffle(shuffled)
    clusters = list(cluster_contributions(shuffled))
    rng.shuffle(clusters)
    assert detect_over_merges(clusters) == baseline


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_detection_is_idempotent_and_leaves_clusters_untouched() -> None:
    clusters = cluster_contributions(pool())
    before = [
        (c.cluster_id, [canonical_json(m) for m in c.contributions]) for c in clusters
    ]
    first = detect_over_merges(clusters)
    assert detect_over_merges(clusters) == first
    assert detect_over_merges(clusters) == first
    after = [
        (c.cluster_id, [canonical_json(m) for m in c.contributions]) for c in clusters
    ]
    assert after == before
    assert [c.contributions for c in clusters] == [
        c.contributions for c in cluster_contributions(pool())
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_empty_and_odd_input_never_raises() -> None:
    assert detect_over_merges([]) == ()
    assert detect_over_merges(iter(())) == ()
    assert detect_over_merges([IdentityCluster("id", ())]) == ()
    assert detect_over_merges(c for c in [over_merged()]) != ()


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_a_non_text_name_never_aborts_the_run_and_is_counted_not_echoed() -> None:
    bad = contribution("a", person__full_name=SECRET_NAME.encode())
    other = contribution("b", person__full_name=SECRET_OTHER)
    third = contribution("c", person__full_name="Third Person")
    with structlog.testing.capture_logs() as logs:
        reports = detect_over_merges([IdentityCluster("id", (bad, other, third))])
    assert [r.distinct_name_count for r in reports] == [2]  # unreadable one skipped
    assert logs[0]["unreadable_names"] == 1
    assert SECRET_NAME not in repr(logs)
    assert "bytes" not in repr(logs)


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_the_run_output_is_identical_with_and_without_the_detector() -> None:
    def run(detect: bool) -> tuple[Any, ...]:
        clusters = cluster_contributions(pool())
        if detect:
            detect_over_merges(clusters)
        ranks = {c.source_name: 1 for cl in clusters for c in cl.contributions}
        leads = [project_lead(c, ranks) for c in clusters]
        return (
            [
                (c.cluster_id, [canonical_json(m) for m in c.contributions])
                for c in clusters
            ],
            [
                (
                    r.lead.model_dump_json() if r.lead else None,
                    r.contributing_sources,
                    r.agreement,
                    [p.model_dump_json() for p in r.provenance],
                )
                for r in leads
            ],
        )

    assert run(True) == run(False)


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_the_over_merge_is_reported_not_repaired() -> None:
    clusters = cluster_contributions([named("a", "Ann One"), named("b", "Bob Two")])
    assert len(clusters) == 1  # detector exists, yet the cluster is whole
    assert len(detect_over_merges(clusters)) == 1
    assert len(clusters[0].contributions) == 2


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_no_personal_value_in_repr_or_str_of_a_report() -> None:
    (report,) = detect_over_merges([over_merged()])
    text = repr(report) + str(report) + repr(OverMergeReason.DISTINCT_FULL_NAMES)
    for secret in (SECRET_NAME, SECRET_OTHER, "zebulon", "linkedin"):
        assert secret.lower() not in text.lower()
    assert report.cluster_id not in repr(report)


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_the_log_line_is_counts_only() -> None:
    with structlog.testing.capture_logs() as logs:
        (report,) = detect_over_merges([over_merged(), cluster_of(named("g", "G G"))])
    assert logs == [
        {
            "event": "over_merge_detection",
            "log_level": "warning",
            "clusters_examined": 2,
            "suspect_clusters": 1,
            "unreadable_names": 0,
        }
    ]
    blob = repr(logs)
    for secret in (SECRET_NAME, SECRET_OTHER, report.cluster_id, "linkedin"):
        assert secret not in blob


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_a_clean_run_logs_at_info_with_zero_suspects() -> None:
    with structlog.testing.capture_logs() as logs:
        detect_over_merges([cluster_of(named("g", "G G"))])
    assert logs == [
        {
            "event": "over_merge_detection",
            "log_level": "info",
            "clusters_examined": 1,
            "suspect_clusters": 0,
            "unreadable_names": 0,
        }
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_large_input_normalises_each_contribution_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    real = normalized_person_name

    def counting(values: Any) -> str | None:
        calls["n"] += 1
        return real(values)

    monkeypatch.setattr(over_merge, "normalized_person_name", counting)
    n = 5000
    members = [
        contribution(
            f"s{i:05d}",
            person__linkedin_url="linkedin.com/in/big",
            person__full_name=f"N{i % 7}",
        )
        for i in range(n)
    ]
    clusters = cluster_contributions(members)
    reports = detect_over_merges(clusters)
    assert reports[0].distinct_name_count == 7
    assert reports[0].member_count == n
    assert calls["n"] == n


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_surname_first_spelling_is_flagged_as_the_accepted_false_positive() -> None:
    """'Lee, Ann' vs 'Ann Lee' are distinct under 16.7; a flag only costs a look."""
    (report,) = detect_over_merges(
        [cluster_of(named("a", "Lee, Ann"), named("b", "Ann Lee"))]
    )
    assert report.distinct_name_count == 2
