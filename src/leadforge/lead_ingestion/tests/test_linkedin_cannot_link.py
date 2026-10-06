"""Distinct LinkedIn URLs are distinct people and never share a cluster (follow-up).

User decision: two records with different normalised LinkedIn URLs are different people.
A key value (verified email, name+domain candidate) reported together with two or more
distinct LinkedIn URLs is disqualified as a Match Key, like 8.14's two-names rule; and a
final cannot-link guard in clustering refuses any union that would put two distinct
LinkedIn URLs in one cluster.
"""

import itertools
import random
from datetime import UTC, datetime
from typing import Any

import pytest

from leadforge.lead_ingestion import clustering
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import (
    IdentityCluster,
    canonical_json,
    cluster_contributions,
)
from leadforge.lead_ingestion.match_keys import (
    DisqualifiedAddresses,
    IdentityExclusions,
    MatchKeyKind,
    extract_match_keys,
    linkedin_identity,
)
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
)
from leadforge.lead_ingestion.over_merge import detect_over_merges
from leadforge.lead_ingestion.projection import project_lead

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
V = EmailStatus.VERIFIED
L1 = "linkedin.com/in/ann"
L2 = "linkedin.com/in/bob"
X = "x@acme.com"


def contribution(source: str, **values: Any) -> LeadContribution:
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


def rec(
    source: str, url: str | None = None, email: str | None = None, **extra: Any
) -> LeadContribution:
    values: dict[str, Any] = dict(extra)
    if url is not None:
        values["person__linkedin_url"] = url
    if email is not None:
        values["person__email"] = email
        values["person__email_status"] = V
    return contribution(source, **values)


def partition(
    pool: list[LeadContribution], exclusions: IdentityExclusions | None = None
) -> set[frozenset[str]]:
    return {
        frozenset(m.source_name for m in cl.contributions)
        for cl in cluster_contributions(pool, exclusions)
    }


def serialise(clusters: tuple[IdentityCluster, ...]) -> list[tuple[str, list[str]]]:
    return [
        (cl.cluster_id, [canonical_json(m) for m in cl.contributions])
        for cl in clusters
    ]


def linkedins_per_cluster(clusters: tuple[IdentityCluster, ...]) -> list[set[str]]:
    return [
        {u for m in cl.contributions if (u := linkedin_identity(m.values)) is not None}
        for cl in clusters
    ]


def bridge_pool() -> list[LeadContribution]:
    return [rec("a", L1, X), rec("b", L2, X), rec("c", None, X)]


# Verifies: specs/lead-source-adapters/requirements.md#8.1
# Verifies: specs/lead-source-adapters/requirements.md#8.2
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_a_bare_record_never_bridges_two_linkedins_in_any_order() -> None:
    pool = bridge_pool()
    expected = serialise(cluster_contributions(pool))
    for perm in itertools.permutations(pool):
        result = cluster_contributions(list(perm))
        assert {
            frozenset(m.source_name for m in cl.contributions) for cl in result
        } == {
            frozenset("a"),
            frozenset("b"),
            frozenset("c"),
        }
        assert serialise(result) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.1
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_four_records_chained_across_two_linkedins_stay_two_people_apart() -> None:
    # a~b share L1 (a legitimate merge); c is bare on X; d holds L2 on X.
    pool = [rec("a", L1), rec("b", L1, X), rec("c", None, X), rec("d", L2, X)]
    expected = serialise(cluster_contributions(pool))
    for perm in itertools.permutations(pool):
        result = cluster_contributions(list(perm))
        assert serialise(result) == expected
        assert all(len(urls) <= 1 for urls in linkedins_per_cluster(result))
    assert partition(pool) == {
        frozenset("ab"),
        frozenset("c"),
        frozenset("d"),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_an_address_seen_with_two_linkedins_is_disqualified_but_kept_as_data() -> None:
    pool = bridge_pool()
    found = DisqualifiedAddresses.from_contributions(pool)
    assert found.addresses == frozenset({X})
    keys = extract_match_keys(pool[2], disqualified=found)
    assert keys.keys == ()
    assert MatchKeyKind.VERIFIED_EMAIL in keys.barred_kinds
    for cl in cluster_contributions(pool):
        for m in cl.contributions:
            assert m.values["person.email"] == X


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_linkedin_spellings_that_normalise_equal_are_one_identity_not_two() -> None:
    pool = [
        rec("a", "https://LinkedIn.com/in/Ann/?trk=x", X),
        rec("b", "linkedin.com/in/ann#frag", X),
        rec("c", None, X),
    ]
    assert DisqualifiedAddresses.from_contributions(pool).addresses == frozenset()
    assert partition(pool) == {frozenset("abc")}


# Verifies: specs/lead-source-adapters/requirements.md#8.1
# Verifies: specs/lead-source-adapters/requirements.md#8.2
def test_same_linkedin_and_same_email_still_merge() -> None:
    assert partition([rec("a", L1, X), rec("b", L1, X)]) == {frozenset("ab")}
    assert partition([rec("a", L1, X), rec("b", L1, X), rec("c", None, X)]) == {
        frozenset("abc")
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.2
def test_a_linkedin_record_and_a_bare_record_on_one_email_still_merge() -> None:
    pool = [rec("a", L1, X), rec("c", None, X), rec("d", None, X)]
    assert partition(pool) == {frozenset("acd")}
    # An unrelated L2 on another address does not disqualify X.
    pool.append(rec("e", L2, "other@acme.com"))
    assert partition(pool) == {frozenset("acd"), frozenset("e")}


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_name_domain_value_seen_with_two_linkedins_is_disqualified() -> None:
    def jane(source: str, url: str | None = None) -> LeadContribution:
        return rec(
            source,
            url,
            person__full_name="Jane Doe",
            company__domain="acme.com",
            person__title="CTO",
        )

    # Without LinkedIn holders the two bare Janes merge on name+domain+title.
    assert partition([jane("c"), jane("d")]) == {frozenset("cd")}
    # Two distinct LinkedIns name that Jane Doe at acme.com: two people exist there,
    # so the candidate key cannot tell the bare records apart and is disqualified.
    pool = [jane("a", L1), jane("b", L2), jane("c"), jane("d")]
    found = DisqualifiedAddresses.from_contributions(pool)
    assert len(found.name_domains) == 1
    assert partition(pool) == {
        frozenset("a"),
        frozenset("b"),
        frozenset("c"),
        frozenset("d"),
    }
    # One LinkedIn only: the candidate stays usable for the bare records.
    assert partition([jane("a", L1), jane("c"), jane("d")]) == {
        frozenset("a"),
        frozenset("cd"),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_disqualified_values_never_reach_repr() -> None:
    pool = bridge_pool()
    found = DisqualifiedAddresses.from_contributions(pool)
    assert X not in repr(found)
    assert "acme" not in repr(found)


# Verifies: specs/lead-source-adapters/requirements.md#8.15
def test_the_over_merge_detector_no_longer_finds_the_bridged_case() -> None:
    # Only Ann's name is on X, so 8.14 alone does not disqualify it; d carries Bob's
    # name on L2. The old bridge made {a, b, c, d} one cluster with two names.
    pool = [
        rec("a", L1, X, person__full_name="Ann One"),
        rec("b", L2, X),
        rec("c", None, X),
        rec("d", L2, person__full_name="Bob Two"),
    ]
    clusters = cluster_contributions(pool)
    assert partition(pool) == {frozenset("a"), frozenset("bd"), frozenset("c")}
    assert detect_over_merges(clusters) == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_projection_yields_two_separate_leads_for_the_two_linkedins() -> None:
    # Namesakes: one name on X, so only the LinkedIn rule keeps them apart.
    pool = [
        rec("a", f"https://{L1}", X, person__full_name="Jane Doe"),
        rec("b", f"https://{L2}", X, person__full_name="Jane Doe"),
        rec("c", None, X),
    ]
    ranks = {"a": 1, "b": 2, "c": 3}
    clusters = cluster_contributions(pool)
    leads = [project_lead(cl, ranks).lead for cl in clusters]
    with_url = [lead for lead in leads if lead is not None and lead.linkedin_url]
    assert len(clusters) == 3
    assert len(with_url) == 2
    assert len({str(lead.linkedin_url) for lead in with_url}) == 2


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_identity_exclusions_still_split_a_single_linkedin_over_merge() -> None:
    pool = [rec("a", L1, X), rec("c", None, X), rec("d", None, X)]
    assert partition(pool) == {frozenset("acd")}
    barred = IdentityExclusions.from_values(emails=[X])
    assert partition(pool, barred) == {
        frozenset("a"),
        frozenset("c"),
        frozenset("d"),
    }
    barred_url = IdentityExclusions.from_values(linkedin_urls=[L1])
    # A barred LinkedIn still names an identity: a never joins b's L2 via X.
    assert partition(
        [rec("a", L1, X), rec("b", L2, X), rec("c", None, X)], barred_url
    ) == {
        frozenset("a"),
        frozenset("b"),
        frozenset("c"),
    }


# --- The cannot-link guard -------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_union_find_refuses_a_union_of_two_distinct_linkedins() -> None:
    forest = clustering._UnionFind(3, ("l1", None, "l2"))
    forest.union(0, 1, MatchKeyKind.VERIFIED_EMAIL)
    forest.union(1, 2, MatchKeyKind.VERIFIED_EMAIL)
    assert forest.find(0) == forest.find(1)
    assert forest.find(2) != forest.find(0)
    # Equal labels and unlabelled sets still join.
    same = clustering._UnionFind(3, ("l1", "l1", None))
    same.union(0, 1)
    same.union(1, 2)
    assert len({same.find(i) for i in range(3)}) == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_guard_alone_keeps_the_bridge_apart_order_independently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Switch the disqualification pass off: the guard is the only defence left, and
    # because unions run in canonical order the result still ignores arrival order.
    monkeypatch.setattr(
        DisqualifiedAddresses,
        "from_contributions",
        classmethod(lambda cls, contributions: cls()),
    )
    pool = bridge_pool()
    expected = serialise(cluster_contributions(pool))
    for perm in itertools.permutations(pool):
        result = cluster_contributions(list(perm))
        assert serialise(result) == expected
        assert all(len(urls) <= 1 for urls in linkedins_per_cluster(result))
    assert len(cluster_contributions(pool)) == 2


def random_pool(rng: random.Random, size: int) -> list[LeadContribution]:
    urls = [None, L1, L2, "https://linkedin.com/in/ANN/"]
    emails = [None, X, "y@acme.com"]
    names = [None, "Ann One"]
    return [
        rec(
            f"s{i}",
            rng.choice(urls),
            rng.choice(emails),
            **({"person__full_name": n} if (n := rng.choice(names)) else {}),
        )
        for i in range(size)
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.8
@pytest.mark.parametrize("seed", range(30))
def test_no_cluster_holds_two_linkedins_in_any_permutation(seed: int) -> None:
    pool = random_pool(random.Random(seed), 5)
    expected = serialise(cluster_contributions(pool))
    for perm in itertools.permutations(pool):
        result = cluster_contributions(list(perm))
        assert serialise(result) == expected
        assert all(len(urls) <= 1 for urls in linkedins_per_cluster(result))


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_large_bridged_input_stays_near_linear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"union": 0, "find": 0}
    real_union, real_find = clustering._UnionFind.union, clustering._UnionFind.find

    def counting_union(
        self: Any, a: int, b: int, kind: MatchKeyKind | None = None
    ) -> None:
        calls["union"] += 1
        real_union(self, a, b, kind)

    def counting_find(self: Any, a: int) -> int:
        calls["find"] += 1
        return real_find(self, a)

    monkeypatch.setattr(clustering._UnionFind, "union", counting_union)
    monkeypatch.setattr(clustering._UnionFind, "find", counting_find)

    n = 5000
    pool: list[LeadContribution] = []
    # A shared address under many LinkedIns (disqualified), plus legitimate triples.
    for i in range(n // 2):
        pool.append(rec(f"s{i:05d}", f"linkedin.com/in/p{i % 50}", "shared@acme.com"))
    i = 0
    while len(pool) < n:
        address = f"t{i}@acme.com"
        pool.append(rec(f"ta{i:05d}", f"linkedin.com/in/t{i}", address))
        pool.append(rec(f"tb{i:05d}", None, address))
        i += 1
    pool = pool[:n]
    result = cluster_contributions(pool)
    assert sum(len(cl.contributions) for cl in result) == n
    assert all(len(urls) <= 1 for urls in linkedins_per_cluster(result))
    assert calls["union"] <= 3 * n
    assert calls["find"] <= 12 * n
