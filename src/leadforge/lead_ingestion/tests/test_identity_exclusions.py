"""Identity Exclusions: values barred from acting as a Match Key (task 16.6, 8.13)."""

import itertools
import random
from datetime import UTC, datetime
from typing import Any

import pytest

from leadforge.lead_ingestion import clustering
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import canonical_json, cluster_contributions
from leadforge.lead_ingestion.match_keys import (
    IdentityExclusions,
    MatchKeyKind,
    extract_match_keys,
)
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
V = EmailStatus.VERIFIED
NONE = IdentityExclusions()


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


def li(source: str, url: str, **extra: Any) -> LeadContribution:
    return contribution(source, person__linkedin_url=url, **extra)


def em(source: str, address: str, **extra: Any) -> LeadContribution:
    return contribution(source, person__email=address, person__email_status=V, **extra)


def partition(
    pool: list[LeadContribution], exclusions: IdentityExclusions | None
) -> set[frozenset[str]]:
    result = cluster_contributions(pool, exclusions)
    return {frozenset(m.source_name for m in cl.contributions) for cl in result}


def serialise(
    pool: list[LeadContribution], exclusions: IdentityExclusions | None
) -> list[tuple[str, list[str]]]:
    return [
        (cl.cluster_id, [canonical_json(m) for m in cl.contributions])
        for cl in cluster_contributions(pool, exclusions)
    ]


def over_merged_pool() -> list[LeadContribution]:
    """Two people (different LinkedIn) bridged by a bare record on a shared address."""
    return [
        li(
            "a",
            "linkedin.com/in/ann",
            person__email="info@x.com",
            person__email_status=V,
        ),
        li(
            "b",
            "linkedin.com/in/bob",
            person__email="info@x.com",
            person__email_status=V,
        ),
        em("c", "info@x.com"),
        em("d", "ann@x.com"),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_adding_an_excluded_email_separates_a_previously_over_merged_cluster() -> None:
    pool = over_merged_pool()
    assert partition(pool, NONE) == {frozenset("abc"), frozenset("d")}
    barred = IdentityExclusions.from_values(emails=["Info@X.com "])
    assert partition(pool, barred) == {
        frozenset("a"),
        frozenset("b"),
        frozenset("c"),
        frozenset("d"),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_exclusion_mutates_no_contribution_and_drops_none() -> None:
    pool = over_merged_pool()
    before = [canonical_json(c) for c in pool]
    result = cluster_contributions(
        pool, IdentityExclusions.from_values(emails=["info@x.com"])
    )
    assert [canonical_json(c) for c in pool] == before
    assert sorted(
        canonical_json(m) for cl in result for m in cl.contributions
    ) == sorted(before)
    originals = {id(c) for c in pool}
    assert {id(m) for cl in result for m in cl.contributions} == originals


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_excluded_linkedin_url_is_skipped_during_key_extraction() -> None:
    c = li("a", "https://LinkedIn.com/in/Ann/?x=1")
    barred = IdentityExclusions.from_values(linkedin_urls=["linkedin.com/in/ann"])
    assert [k.kind for k in extract_match_keys(c).keys] == [MatchKeyKind.LINKEDIN_URL]
    assert extract_match_keys(c, barred).keys == ()
    pool = [li("a", "linkedin.com/in/ann"), li("b", "linkedin.com/in/ann")]
    assert partition(pool, barred) == {frozenset("a"), frozenset("b")}


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_an_exclusion_bars_only_the_named_value_of_its_own_kind() -> None:
    pool = [li("a", "linkedin.com/in/ann"), li("b", "linkedin.com/in/ann")]
    other_email = IdentityExclusions.from_values(emails=["ann@x.com"])
    other_url = IdentityExclusions.from_values(linkedin_urls=["linkedin.com/in/bob"])
    assert partition(pool, other_email) == {frozenset("ab")}
    assert partition(pool, other_url) == {frozenset("ab")}


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_barring_a_linkedin_key_never_lets_its_holder_bridge_on_email() -> None:
    # Barring the URL must not make a and b "LinkedIn-less" and merge them on email.
    pool = [
        li("a", "linkedin.com/in/ann", person__email="e@x.com", person__email_status=V),
        li("b", "linkedin.com/in/bob", person__email="e@x.com", person__email_status=V),
    ]
    assert partition(pool, NONE) == {frozenset("a"), frozenset("b")}
    barred = IdentityExclusions.from_values(linkedin_urls=["linkedin.com/in/ann"])
    assert partition(pool, barred) == {frozenset("a"), frozenset("b")}


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_barring_a_strong_key_never_opens_the_weak_name_domain_route() -> None:
    pool = [
        contribution(
            "a",
            person__full_name="Jane Doe",
            company__domain="acme.com",
            person__title="CTO",
            person__linkedin_url="linkedin.com/in/jd",
        ),
        contribution(
            "b",
            person__full_name="Jane Doe",
            company__domain="acme.com",
            person__title="CTO",
        ),
    ]
    barred = IdentityExclusions.from_values(linkedin_urls=["linkedin.com/in/jd"])
    assert partition(pool, barred) == {frozenset("a"), frozenset("b")}


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_an_exclusion_naming_nothing_present_is_a_no_op() -> None:
    pool = over_merged_pool()
    ghost = IdentityExclusions.from_values(
        emails=["nobody@nowhere.example"], linkedin_urls=["linkedin.com/in/ghost"]
    )
    assert serialise(pool, ghost) == serialise(pool, NONE)
    assert serialise(pool, ghost) == serialise(pool, None)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_applying_an_exclusion_twice_is_idempotent() -> None:
    pool = over_merged_pool()
    barred = IdentityExclusions.from_values(emails=["info@x.com"])
    once = cluster_contributions(pool, barred)
    flat = [m for cl in once for m in cl.contributions]
    twice = cluster_contributions(flat, barred)
    assert [
        (c.cluster_id, [canonical_json(m) for m in c.contributions]) for c in once
    ] == [(c.cluster_id, [canonical_json(m) for m in c.contributions]) for c in twice]
    assert IdentityExclusions.from_values(emails=["info@x.com", "INFO@x.com"]) == barred


def exclusion_pool() -> list[LeadContribution]:
    return [
        li(
            "a",
            "linkedin.com/in/ann",
            person__email="info@x.com",
            person__email_status=V,
        ),
        li(
            "b",
            "linkedin.com/in/bob",
            person__email="info@x.com",
            person__email_status=V,
        ),
        em("c", "info@x.com"),
        li("d", "linkedin.com/in/ann"),
        em("e", "ann@x.com"),
        li(
            "f",
            "linkedin.com/in/bob",
            person__email="ann@x.com",
            person__email_status=V,
        ),
    ]


EXCLUSION_SETS = [
    IdentityExclusions.from_values(emails=["info@x.com"]),
    IdentityExclusions.from_values(emails=["ann@x.com"]),
    IdentityExclusions.from_values(linkedin_urls=["linkedin.com/in/ann"]),
    IdentityExclusions.from_values(
        emails=["info@x.com"], linkedin_urls=["linkedin.com/in/bob"]
    ),
]


# Verifies: specs/lead-source-adapters/requirements.md#8.8
# Verifies: specs/lead-source-adapters/requirements.md#8.13
@pytest.mark.parametrize("exclusions", EXCLUSION_SETS)
def test_every_permutation_of_contributions_gives_identical_clusters(
    exclusions: IdentityExclusions,
) -> None:
    pool = exclusion_pool()[:6]
    expected = serialise(pool, exclusions)
    perms = list(itertools.permutations(pool))
    assert len(perms) == 720
    for perm in perms:
        assert serialise(list(perm), exclusions) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_every_permutation_of_the_exclusion_input_gives_the_same_set_and_clusters() -> (
    None
):
    emails = ["info@x.com", "ann@x.com", "z@x.com"]
    urls = ["linkedin.com/in/ann", "linkedin.com/in/bob"]
    pool = exclusion_pool()
    reference = IdentityExclusions.from_values(emails=emails, linkedin_urls=urls)
    expected = serialise(pool, reference)
    for e_perm in itertools.permutations(emails):
        for u_perm in itertools.permutations(urls):
            got = IdentityExclusions.from_values(emails=e_perm, linkedin_urls=u_perm)
            assert got == reference
            assert got.version_token == reference.version_token
            assert serialise(pool, got) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.8
# Verifies: specs/lead-source-adapters/requirements.md#8.13
@pytest.mark.parametrize("seed", range(25))
def test_seeded_shuffles_with_exclusions_give_byte_identical_clusters(
    seed: int,
) -> None:
    pool = [
        *exclusion_pool(),
        em("g", "g@x.com"),
        em("h", "g@x.com"),
        contribution("i"),
        contribution("i"),
    ]
    barred = IdentityExclusions.from_values(
        emails=["info@x.com"], linkedin_urls=["linkedin.com/in/bob"]
    )
    expected = serialise(pool, barred)
    shuffled = list(pool)
    random.Random(seed).shuffle(shuffled)
    assert serialise(shuffled, barred) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_an_exclusion_never_merges_anything_it_only_refines() -> None:
    pool = exclusion_pool()
    base = partition(pool, NONE)
    entries = [
        ("emails", "info@x.com"),
        ("emails", "ann@x.com"),
        ("linkedin_urls", "linkedin.com/in/ann"),
        ("linkedin_urls", "linkedin.com/in/bob"),
    ]
    for size in range(len(entries) + 1):
        for subset in itertools.combinations(entries, size):
            kwargs: dict[str, list[str]] = {"emails": [], "linkedin_urls": []}
            for kind, value in subset:
                kwargs[kind].append(value)
            refined = partition(pool, IdentityExclusions.from_values(**kwargs))
            for cluster in refined:
                assert any(cluster <= whole for whole in base)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_the_version_token_changes_exactly_when_the_set_changes() -> None:
    a = IdentityExclusions.from_values(emails=["a@x.com"])
    same = IdentityExclusions.from_values(emails=["A@x.com"])
    more = IdentityExclusions.from_values(emails=["a@x.com", "b@x.com"])
    as_url = IdentityExclusions.from_values(linkedin_urls=["x.com/in/a"])
    assert a.version_token == same.version_token
    assert (
        len(
            {
                a.version_token,
                more.version_token,
                as_url.version_token,
                NONE.version_token,
            }
        )
        == 4
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_exclusions_are_personal_data_and_never_reach_repr_or_errors() -> None:
    secret = "secret.person@private.example"
    barred = IdentityExclusions.from_values(emails=[secret])
    assert "secret" not in repr(barred)
    assert "private.example" not in repr(barred)
    with pytest.raises(ValueError, match="usable key value") as err:
        IdentityExclusions.from_values(
            emails=["not an address " + secret.replace("@", "")]
        )
    assert "secret" not in str(err.value)
    with pytest.raises(ValueError, match="usable key value") as err2:
        IdentityExclusions.from_values(linkedin_urls=["https://   "])
    assert "https" not in str(err2.value)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_large_input_with_exclusions_keeps_union_operations_near_linear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"union": 0, "find": 0}
    real_union, real_find = clustering._UnionFind.union, clustering._UnionFind.find

    def counting_union(self: Any, a: int, b: int) -> None:
        calls["union"] += 1
        real_union(self, a, b)

    def counting_find(self: Any, a: int) -> int:
        calls["find"] += 1
        return real_find(self, a)

    monkeypatch.setattr(clustering._UnionFind, "union", counting_union)
    monkeypatch.setattr(clustering._UnionFind, "find", counting_find)
    n = 5000
    pool = [em(f"star{i:05d}", "shared@x.com") for i in range(n // 2)]
    pool += [em(f"role{i:05d}", "info@x.com") for i in range(n // 4)]
    pool += [
        li(f"li{i:05d}", f"linkedin.com/in/p{i % 50}") for i in range(n - len(pool))
    ]
    barred = IdentityExclusions.from_values(
        emails=["info@x.com", "x1@x.com", "x2@x.com"],
        linkedin_urls=["linkedin.com/in/p3", "linkedin.com/in/p4"],
    )
    result = cluster_contributions(pool, barred)
    assert sum(len(cl.contributions) for cl in result) == n
    assert calls["union"] <= 3 * n
    assert calls["find"] <= 12 * n


def named(source: str, name: str, **extra: Any) -> LeadContribution:
    return contribution(source, person__full_name=name, **extra)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_the_repair_projects_two_people_to_two_leads_end_to_end() -> None:
    from leadforge.lead_ingestion.projection import project_lead

    pool = [
        named(
            "a",
            "Ann Lee",
            person__linkedin_url="linkedin.com/in/ann",
            person__email="info@x.com",
            person__email_status=V,
        ),
        named(
            "b",
            "Bob Ray",
            person__linkedin_url="linkedin.com/in/bob",
            person__email="info@x.com",
            person__email_status=V,
        ),
        em("c", "info@x.com"),
    ]
    ranks = {"a": 1, "b": 2, "c": 3}

    def leads(ex: IdentityExclusions) -> list[str]:
        out = [project_lead(c, ranks).lead for c in cluster_contributions(pool, ex)]
        return sorted(str(lead.full_name) if lead else "-" for lead in out)

    assert len(leads(NONE)) == 1
    assert leads(IdentityExclusions.from_values(emails=["INFO@x.com"])) == [
        "Ann Lee",
        "Bob Ray",
        "None",  # the nameless address-only record is its own lead
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_barring_the_linkedin_instead_does_not_split_a_bridged_email() -> None:
    # Documents the limit: the bare holder still links every email holder, so only
    # barring the shared email repairs this over-merge.
    pool = over_merged_pool()[:3]
    barred = IdentityExclusions.from_values(linkedin_urls=["linkedin.com/in/ann"])
    assert partition(pool, barred) == {frozenset("abc")}


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_a_barred_linkedin_with_a_bare_record_on_the_same_email_merges_as_before() -> (
    None
):
    pool = [
        li("a", "linkedin.com/in/ann", person__email="e@x.com", person__email_status=V),
        em("b", "e@x.com"),
    ]
    barred = IdentityExclusions.from_values(linkedin_urls=["linkedin.com/in/ann"])
    assert partition(pool, NONE) == partition(pool, barred) == {frozenset("ab")}
