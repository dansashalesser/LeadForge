"""Order-independent identity clustering (task 16.2, Requirements 8.8, 8.9)."""

import itertools
import random
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest

from leadforge.lead_ingestion import clustering
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import (
    IdentityCluster,
    canonical_json,
    cluster_contributions,
)
from leadforge.lead_ingestion.match_keys import MatchKeyKind, extract_match_keys
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
    UntrustedText,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
V = EmailStatus.VERIFIED


def contribution(source: str = "s", **values: Any) -> LeadContribution:
    """Build a contribution; keys use ``__`` for ``.`` (person__email)."""
    mapped = {k.replace("__", "."): v for k, v in values.items()}
    provenance = tuple(
        FieldProvenance(
            canonical_path=path,
            source_name=source,
            data_mode=DataMode.SYNTHETIC,
            fetched_at=NOW,
            raw_field_path="raw",
            confidence_origin=ConfidenceOrigin.NONE,
            untrusted=isinstance(value, UntrustedText),
        )
        for path, value in mapped.items()
    )
    return LeadContribution(source_name=source, values=mapped, provenance=provenance)


def li(source: str, url: str, **extra: Any) -> LeadContribution:
    return contribution(source, person__linkedin_url=url, **extra)


def em(source: str, address: str, **extra: Any) -> LeadContribution:
    return contribution(source, person__email=address, person__email_status=V, **extra)


def nd(
    source: str, name: str, domain: str | list[str], **extra: Any
) -> LeadContribution:
    return contribution(source, person__full_name=name, company__domain=domain, **extra)


def groups(
    clusters: tuple[IdentityCluster, ...], pool: list[LeadContribution]
) -> set[frozenset[int]]:
    """Clusters as sets of indexes into ``pool`` (sources must be unique in pool)."""
    by_source = {c.source_name: i for i, c in enumerate(pool)}
    return {
        frozenset(by_source[m.source_name] for m in cl.contributions) for cl in clusters
    }


def serialise(clusters: tuple[IdentityCluster, ...]) -> list[tuple[str, list[str]]]:
    return [
        (cl.cluster_id, [canonical_json(m) for m in cl.contributions])
        for cl in clusters
    ]


def flatten(clusters: tuple[IdentityCluster, ...]) -> list[LeadContribution]:
    return [m for cl in clusters for m in cl.contributions]


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_shared_linkedin_key_merges() -> None:
    pool = [
        li("a", "https://www.linkedin.com/in/jd?x=1"),
        li("b", "https://WWW.linkedin.com/in/JD/"),
        li("c", "https://www.linkedin.com/in/other"),
    ]
    assert groups(cluster_contributions(pool), pool) == {
        frozenset({0, 1}),
        frozenset({2}),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.2
def test_shared_verified_email_merges_when_linkedin_is_absent() -> None:
    pool = [em("a", "Jane@X.com "), em("b", "jane@x.com"), em("c", "other@x.com")]
    assert groups(cluster_contributions(pool), pool) == {
        frozenset({0, 1}),
        frozenset({2}),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.2
def test_shared_email_does_not_join_two_contributions_with_different_linkedin() -> None:
    pool = [
        li("a", "linkedin.com/in/x", person__email="e@x.com", person__email_status=V),
        li("b", "linkedin.com/in/y", person__email="e@x.com", person__email_status=V),
    ]
    assert groups(cluster_contributions(pool), pool) == {
        frozenset({0}),
        frozenset({1}),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.2
def test_email_with_one_sided_linkedin_merges() -> None:
    pool = [
        li("a", "linkedin.com/in/x", person__email="e@x.com", person__email_status=V),
        em("b", "e@x.com"),
    ]
    assert groups(cluster_contributions(pool), pool) == {frozenset({0, 1})}


# Verifies: specs/lead-source-adapters/requirements.md#8.11
def test_unverified_email_never_merges() -> None:
    pool = [
        contribution(
            "a", person__email="e@x.com", person__email_status=EmailStatus.UNVERIFIED
        ),
        contribution(
            "b", person__email="e@x.com", person__email_status=EmailStatus.ACCEPT_ALL
        ),
        contribution("c", person__email="e@x.com"),
    ]
    assert len(cluster_contributions(pool)) == 3


# Verifies: specs/lead-source-adapters/requirements.md#8.11
def test_pair_sharing_only_an_unverified_email_merges_once_verified() -> None:
    unverified = contribution(
        "a", person__email="e@x.com", person__email_status=EmailStatus.UNVERIFIED
    )
    pair = [unverified, em("b", "e@x.com")]
    assert len(cluster_contributions(pair)) == 2
    pair = [em("a", "e@x.com"), em("b", "e@x.com")]
    assert len(cluster_contributions(pair)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_transitive_closure_links_through_different_key_kinds() -> None:
    # A~B by LinkedIn, B~C by email (C has no LinkedIn), C~D by email (D has none).
    # Follow-up: D used to hold a second LinkedIn (jd2) and was bridged in by C; a
    # different LinkedIn is a different person now, so that case lives in
    # test_linkedin_cannot_link.py and this test keeps one LinkedIn identity.
    pool = [
        li("a", "linkedin.com/in/jd"),
        li("b", "linkedin.com/in/jd", person__email="e@x.com", person__email_status=V),
        em("c", "e@x.com"),
        em("d", "e@x.com"),
        li("e", "linkedin.com/in/solo"),
    ]
    assert groups(cluster_contributions(pool), pool) == {
        frozenset({0, 1, 2, 3}),
        frozenset({4}),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_name_domain_merges_only_with_a_corroborating_attribute() -> None:
    pool = [
        nd("a", "Jane Doe", "acme.com", person__title="CTO"),
        nd("b", "jane  DOE", "www.acme.com", person__title="cto"),
        nd("c", "Jane Doe", "acme.com", person__title="Janitor"),
    ]
    assert groups(cluster_contributions(pool), pool) == {
        frozenset({0, 1}),
        frozenset({2}),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_name_and_domain_alone_never_merge() -> None:
    pool = [nd("a", "Jane Doe", "acme.com"), nd("b", "Jane Doe", "acme.com")]
    assert len(cluster_contributions(pool)) == 2


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_name_only_never_merges_even_with_corroboration() -> None:
    pool = [
        contribution("a", person__full_name="Jane Doe", person__title="CTO"),
        contribution("b", person__full_name="Jane Doe", person__title="CTO"),
    ]
    assert len(cluster_contributions(pool)) == 2


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_shared_employer_corroborates_and_any_domain_of_a_collection_counts() -> None:
    pool = [
        nd("a", "Jane Doe", ["old.com", "new.com"], company__name="Acme"),
        nd("b", "Jane Doe", "new.com", company__name="ACME"),
    ]
    assert len(cluster_contributions(pool)) == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_corroboration_is_pairwise_not_group_wide() -> None:
    # a and c share a title, b and d share another; no corroboration across the
    # two pairs, so two identities and never one.
    pool = [
        nd("a", "Jane Doe", "acme.com", person__title="CTO"),
        nd("b", "Jane Doe", "acme.com", person__title="Chair"),
        nd("c", "Jane Doe", "acme.com", person__title="CTO"),
        nd("d", "Jane Doe", "acme.com", person__title="Chair"),
    ]
    assert groups(cluster_contributions(pool), pool) == {
        frozenset({0, 2}),
        frozenset({1, 3}),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_name_domain_never_joins_a_contribution_that_has_a_stronger_key() -> None:
    # Two people with different LinkedIns must not be bridged by a bare third
    # record, and a keyed record is not pulled in by the weak key at all.
    pool = [
        li(
            "a",
            "linkedin.com/in/x",
            person__full_name="Jane Doe",
            company__domain="acme.com",
            person__title="CTO",
        ),
        li(
            "b",
            "linkedin.com/in/y",
            person__full_name="Jane Doe",
            company__domain="acme.com",
            person__title="CTO",
        ),
        nd("c", "Jane Doe", "acme.com", person__title="CTO"),
    ]
    assert len(cluster_contributions(pool)) == 3


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_contributions_without_keys_are_singletons_never_dropped() -> None:
    pool = [
        contribution("a"),
        contribution("b", person__title="CTO"),
        contribution("c", person__email="x@y.com"),  # no status: no key
        contribution("d", person__linkedin_url="https://linkedin.com/"),  # no path
    ]
    result = cluster_contributions(pool)
    assert len(result) == 4
    assert sorted(m.source_name for m in flatten(result)) == ["a", "b", "c", "d"]


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_empty_input_gives_no_clusters() -> None:
    assert cluster_contributions([]) == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_identical_keyless_contributions_stay_separate_with_distinct_ids() -> None:
    pool = [contribution("a"), contribution("a"), contribution("a")]
    result = cluster_contributions(pool)
    assert len(result) == 3
    assert len({cl.cluster_id for cl in result}) == 3


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_input_is_not_mutated_and_members_are_the_same_objects() -> None:
    pool = [li("b", "linkedin.com/in/x"), li("a", "linkedin.com/in/x")]
    before = list(pool)
    result = cluster_contributions(pool)
    assert pool == before
    assert [id(p) for p in pool] == [id(b) for b in before]
    assert {id(m) for m in result[0].contributions} == {id(p) for p in pool}


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_members_are_ordered_canonically_not_by_arrival() -> None:
    pool = [li("b", "linkedin.com/in/x"), li("a", "linkedin.com/in/x")]
    assert [m.source_name for m in cluster_contributions(pool)[0].contributions] == [
        "a",
        "b",
    ]


def mixed_pool() -> list[LeadContribution]:
    return [
        li("a", "linkedin.com/in/jd"),
        li("b", "linkedin.com/in/jd", person__email="e@x.com", person__email_status=V),
        em("c", "e@x.com"),
        nd("d", "Jane Doe", "acme.com", person__title="CTO"),
        nd("e", "Jane Doe", ["acme.com", "z.com"], person__title="CTO"),
        nd("f", "Jane Doe", "acme.com", person__title="Chair"),
        contribution("g"),
        contribution("g"),
        em("h", "other@x.com"),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.8
@pytest.mark.parametrize("seed", range(25))
def test_seeded_shuffles_give_byte_identical_clusters_ids_and_member_order(
    seed: int,
) -> None:
    pool = mixed_pool()
    expected = serialise(cluster_contributions(pool))
    shuffled = list(pool)
    random.Random(seed).shuffle(shuffled)
    assert serialise(cluster_contributions(shuffled)) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_every_permutation_of_a_small_set_gives_identical_clusters() -> None:
    pool = [
        li("a", "linkedin.com/in/jd"),
        li("b", "linkedin.com/in/jd", person__email="e@x.com", person__email_status=V),
        em("c", "e@x.com"),
        nd("d", "Jane Doe", "acme.com", person__title="CTO"),
        nd("e", "Jane Doe", "acme.com", person__title="CTO"),
        contribution("f"),
    ]
    expected = serialise(cluster_contributions(pool))
    perms = list(itertools.permutations(pool))
    assert len(perms) == 720
    for perm in perms:
        assert serialise(cluster_contributions(list(perm))) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.9
def test_clustering_is_idempotent_over_its_own_flattened_output() -> None:
    first = cluster_contributions(mixed_pool())
    second = cluster_contributions(flatten(first))
    assert serialise(second) == serialise(first)


# Verifies: specs/lead-source-adapters/requirements.md#8.9
def test_a_later_run_joins_the_existing_identity_and_adds_no_cluster() -> None:
    first_run = [li("a", "linkedin.com/in/jd"), em("b", "e@x.com")]
    before = cluster_contributions(first_run)
    later = li("c", "linkedin.com/in/jd", person__title="CTO")
    after = cluster_contributions([*first_run, later])
    assert len(after) == len(before)
    joined = next(cl for cl in after if later in cl.contributions)
    assert {m.source_name for m in joined.contributions} == {"a", "c"}
    # The identity a re-run joins keeps its id while its smallest member is unchanged.
    old = next(cl for cl in before if cl.contributions[0].source_name == "a")
    assert joined.cluster_id == old.cluster_id


# Verifies: specs/lead-source-adapters/requirements.md#8.9
def test_cluster_id_survives_a_lower_sorting_later_fetch() -> None:
    first_run = [li("a", "linkedin.com/in/jd")]
    old = cluster_contributions(first_run)[0].cluster_id
    # Sorts below the first run's record by canonical JSON (company.name < person.*).
    later = contribution(
        "z", person__linkedin_url="linkedin.com/in/jd", company__name="Acme"
    ).model_copy(
        update={
            "provenance": tuple(
                p.model_copy(update={"fetched_at": NOW + timedelta(days=1)})
                for p in contribution(
                    "z", person__linkedin_url="x", company__name="Acme"
                ).provenance
            )
        }
    )
    assert canonical_json(later) < canonical_json(first_run[0])
    after = cluster_contributions([*first_run, later])
    assert [cl.cluster_id for cl in after] == [old]


# Verifies: specs/lead-source-adapters/requirements.md#8.9
def test_replaying_the_same_contributions_does_not_add_clusters_for_keyed_leads() -> (
    None
):
    run = [li("a", "linkedin.com/in/jd"), em("b", "e@x.com")]
    assert len(cluster_contributions([*run, *run])) == len(run)


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_cluster_ids_are_distinct_stable_hex_and_clusters_sorted_by_id() -> None:
    result = cluster_contributions(mixed_pool())
    ids = [cl.cluster_id for cl in result]
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    assert all(len(i.split("-")[0]) == 64 for i in ids)


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_canonical_json_ignores_mapping_and_set_order() -> None:
    a = contribution(
        "s", person__title="x", company__domain=frozenset({"a.com", "b.com"})
    )
    b = contribution(
        "s", company__domain=frozenset({"b.com", "a.com"}), person__title="x"
    )
    assert canonical_json(a) == canonical_json(b)


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_canonical_json_rejects_unserialisable_values_naming_only_the_type() -> None:
    bad = contribution("s", person__title=object())
    with pytest.raises(TypeError, match="object"):
        canonical_json(bad)


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_no_personal_value_in_repr_or_error_text() -> None:
    secret = "zz-secret-person@example.com"
    pool = [em("a", secret), em("b", secret)]
    result = cluster_contributions(pool)
    assert secret not in repr(result)
    assert secret not in result[0].cluster_id
    with pytest.raises(TypeError) as err:
        cluster_contributions([contribution("s", person__email=secret, x__y=object())])
    assert secret not in str(err.value)


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_canonical_json_accepts_plain_dates() -> None:
    assert "2020-01-02" in canonical_json(
        contribution("s", person__title=date(2020, 1, 2))
    )


class _Leaky:
    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return self.text

    __str__ = __repr__

    def __hash__(self) -> int:
        return 1

    def __eq__(self, other: object) -> bool:
        return self is other


# Verifies: specs/lead-source-adapters/requirements.md#8.8
@pytest.mark.parametrize("make", [lambda x: x, lambda x: {x: 1}])
def test_error_text_never_carries_the_offending_value(make: Any) -> None:
    secret = "zz-secret-person@example.com"
    bad = contribution("s", person__title=make(_Leaky(secret)))
    with pytest.raises(TypeError) as err:
        canonical_json(bad)
    assert secret not in str(err.value)


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_large_input_uses_near_linear_union_operations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"union": 0, "find": 0}
    real_union, real_find = clustering._UnionFind.union, clustering._UnionFind.find

    def counting_union(
        self: Any,
        a: int,
        b: int,
        kind: MatchKeyKind | None = None,
        value: str | None = None,
    ) -> None:
        calls["union"] += 1
        real_union(self, a, b, kind, value)

    def counting_find(self: Any, a: int) -> int:
        calls["find"] += 1
        return real_find(self, a)

    monkeypatch.setattr(clustering._UnionFind, "union", counting_union)
    monkeypatch.setattr(clustering._UnionFind, "find", counting_find)

    n = 5000
    # One giant star on a shared email, many small LinkedIn/email clusters, a big
    # name+domain+title bucket: every shape that would blow up a pairwise loop.
    pool: list[LeadContribution] = []
    for i in range(n // 3):
        pool.append(em(f"star{i:05d}", "shared@x.com"))
    for i in range(n // 9):
        url = f"linkedin.com/in/c{i}"
        address = f"c{i}@x.com"
        pool.append(
            li(f"ca{i:05d}", url, person__email=address, person__email_status=V)
        )
        pool.append(li(f"cb{i:05d}", url))
        pool.append(em(f"cc{i:05d}", address))
    for i in range(n - len(pool)):
        pool.append(nd(f"nd{i:05d}", "Big Bucket", "acme.com", person__title="CTO"))
    result = cluster_contributions(pool)
    assert sum(len(cl.contributions) for cl in result) == n
    assert calls["union"] <= 3 * n
    assert calls["find"] <= 12 * n


# Task 16.12: the cluster records which Match Key kinds linked it (kinds, never values).
def _kinds(*members: LeadContribution) -> tuple[Any, ...]:
    clusters = cluster_contributions(members)
    assert len(clusters) == 1
    return clusters[0].merged_by


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_linkedin_merge_records_the_linkedin_kind() -> None:
    kinds = _kinds(li("a", "linkedin.com/in/x"), li("b", "LinkedIn.com/in/x/"))
    assert kinds == (MatchKeyKind.LINKEDIN_URL,)


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_an_email_merge_records_the_verified_email_kind() -> None:
    kinds = _kinds(em("a", "ann@x.com"), em("b", "ANN@x.com"))
    assert kinds == (MatchKeyKind.VERIFIED_EMAIL,)


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_name_domain_merge_records_the_name_domain_kind() -> None:
    kinds = _kinds(
        nd("a", "Ann Lee", "x.com", person__title="CTO"),
        nd("b", "Ann Lee", "x.com", person__title="CTO"),
    )
    assert kinds == (MatchKeyKind.NAME_DOMAIN,)


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_name_domain_merge_records_the_name_domain_key_that_linked_it() -> None:
    a = nd("a", "Ann Lee", "x.com", person__title="CTO")
    (cluster,) = cluster_contributions(
        [a, nd("b", "Ann Lee", "x.com", person__title="CTO")]
    )
    (key,) = (
        k for k in extract_match_keys(a).keys if k.kind is MatchKeyKind.NAME_DOMAIN
    )
    assert cluster.linked_by == (key,)


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_cluster_linked_by_two_kinds_lists_both_strongest_first() -> None:
    kinds = _kinds(
        li("a", "linkedin.com/in/x"),
        li("b", "linkedin.com/in/x", person__email="ann@x.com", person__email_status=V),
        em("c", "ann@x.com"),
    )
    assert kinds == (MatchKeyKind.LINKEDIN_URL, MatchKeyKind.VERIFIED_EMAIL)


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_singleton_cluster_records_no_kind() -> None:
    assert _kinds(li("a", "linkedin.com/in/x")) == ()


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_merged_by_ignores_arrival_order() -> None:
    members = [
        li("a", "linkedin.com/in/x"),
        li("b", "linkedin.com/in/x", person__email="ann@x.com", person__email_status=V),
        em("c", "ann@x.com"),
    ]
    seen = {
        tuple(c.merged_by for c in cluster_contributions(perm))
        for perm in itertools.permutations(members)
    }
    assert len(seen) == 1
