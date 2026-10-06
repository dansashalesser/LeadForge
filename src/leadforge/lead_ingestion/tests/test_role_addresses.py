"""Addresses reported against two distinct names (task 16.7, Requirement 8.14)."""

import itertools
import random
from datetime import UTC, datetime
from typing import Any

import pytest

from leadforge.lead_ingestion import clustering
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import canonical_json, cluster_contributions
from leadforge.lead_ingestion.match_keys import (
    DisqualifiedAddresses,
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


def named(
    source: str,
    address: str,
    name: str | None,
    status: EmailStatus = V,
    **extra: Any,
) -> LeadContribution:
    if name is not None:
        extra["person__full_name"] = name
    return contribution(
        source, person__email=address, person__email_status=status, **extra
    )


def partition(pool: list[LeadContribution]) -> set[frozenset[str]]:
    return {
        frozenset(m.source_name for m in cl.contributions)
        for cl in cluster_contributions(pool)
    }


def serialise(pool: list[LeadContribution]) -> list[tuple[str, list[str]]]:
    return [
        (cl.cluster_id, [canonical_json(m) for m in cl.contributions])
        for cl in cluster_contributions(pool)
    ]


def role_pool() -> list[LeadContribution]:
    """A role address two sources attach to different people, plus one repeat."""
    return [
        named("a", "info@x.com", "Ann Lee"),
        named("b", "info@x.com", "Bob Ray"),
        named("c", "info@x.com", "ann lee"),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_role_address_two_sources_attach_to_different_people_never_merges_them() -> (
    None
):
    # No Identity Exclusion is passed: the disqualification is structural.
    assert partition(role_pool()) == {
        frozenset("a"),
        frozenset("b"),
        frozenset("c"),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_one_name_on_many_contributions_does_not_disqualify() -> None:
    pool = [named(s, "ann@x.com", "Ann Lee") for s in "abc"]
    assert partition(pool) == {frozenset("abc")}
    assert DisqualifiedAddresses.from_contributions(pool).addresses == frozenset()


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_spellings_of_one_name_count_as_one_under_match_key_normalisation() -> None:
    pool = [
        named("a", "ann@x.com", "Ann Lee"),
        named("b", "ann@x.com", "  ann   LEE "),
        named(
            "c", "ann@x.com", None, person__first_name="ANN", person__last_name="Lee"
        ),
        named(
            "d", "ann@x.com", "\uff21\uff4e\uff4e \uff2c\uff45\uff45"
        ),  # fullwidth, NFKC-equal
    ]
    assert partition(pool) == {frozenset("abcd")}


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_no_fuzzy_matching_a_reordered_name_is_a_distinct_name() -> None:
    pool = [named("a", "ann@x.com", "Ann Lee"), named("b", "ann@x.com", "Lee, Ann")]
    assert partition(pool) == {frozenset("a"), frozenset("b")}


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_blank_missing_and_masked_names_are_not_distinct_names() -> None:
    pool = [
        named("a", "ann@x.com", "Ann Lee"),
        named("b", "ann@x.com", None),
        named("c", "ann@x.com", "   "),
        named(
            "d", "ann@x.com", None, person__first_name="Ann", person__last_name="L***"
        ),
        named("e", "ann@x.com", "Ann L***"),
    ]
    assert DisqualifiedAddresses.from_contributions(pool).addresses == frozenset()
    assert partition(pool) == {frozenset("abcde")}


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_address_comparison_uses_the_match_key_normalisation() -> None:
    pool = [named("a", "Info@X.com ", "Ann Lee"), named("b", "info@x.com", "Bob Ray")]
    assert partition(pool) == {frozenset("a"), frozenset("b")}


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_name_reported_with_an_unverified_address_still_counts() -> None:
    pool = [
        named("a", "info@x.com", "Ann Lee"),
        named("b", "info@x.com", "Bob Ray", EmailStatus.UNVERIFIED),
        named("c", "info@x.com", "Ann Lee"),
    ]
    assert partition(pool) == {frozenset("a"), frozenset("b"), frozenset("c")}


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_the_disqualified_set_is_the_whole_input_not_a_pair() -> None:
    pool = [*role_pool(), named("d", "bob@x.com", "Bob Ray")]
    found = DisqualifiedAddresses.from_contributions(pool)
    assert found.addresses == frozenset({"info@x.com"})
    assert (
        found.addresses
        == DisqualifiedAddresses.from_contributions(reversed(pool)).addresses
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_disqualified_address_is_skipped_but_its_kind_stays_present() -> None:
    c = named("a", "info@x.com", "Ann Lee")
    found = DisqualifiedAddresses.from_contributions(role_pool())
    assert [k.kind for k in extract_match_keys(c).keys] == [MatchKeyKind.VERIFIED_EMAIL]
    keys = extract_match_keys(c, disqualified=found)
    assert keys.keys == ()
    assert keys.barred_kinds == frozenset({MatchKeyKind.VERIFIED_EMAIL})


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_removal_alone_must_not_open_the_weak_name_domain_route() -> None:
    # Dropping the role address would make x look key-less and merge it with y on
    # name+domain+title; as with a barred key it stays PRESENT, so they stay apart.
    pool = [
        named(
            "x",
            "info@x.com",
            "Jane Doe",
            company__domain="acme.com",
            person__title="CTO",
        ),
        contribution(
            "y",
            person__full_name="Jane Doe",
            company__domain="acme.com",
            person__title="CTO",
        ),
        named("z", "info@x.com", "Zed Moss"),
    ]
    assert partition(pool) == {frozenset("x"), frozenset("y"), frozenset("z")}


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_role_address_never_bridges_two_linkedin_less_people_via_other_keys() -> None:
    pool = [
        named("a", "info@x.com", "Ann Lee", person__linkedin_url="linkedin.com/in/ann"),
        named("b", "info@x.com", "Bob Ray", person__linkedin_url="linkedin.com/in/bob"),
        named("c", "info@x.com", "Cy Poe"),
    ]
    assert partition(pool) == {frozenset("a"), frozenset("b"), frozenset("c")}


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_other_keys_of_the_same_people_still_merge() -> None:
    pool = [
        named("a", "info@x.com", "Ann Lee", person__linkedin_url="linkedin.com/in/ann"),
        named("b", "info@x.com", "Bob Ray", person__linkedin_url="linkedin.com/in/bob"),
        contribution("c", person__linkedin_url="linkedin.com/in/ann"),
    ]
    assert partition(pool) == {frozenset("ac"), frozenset("b")}


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_disqualification_drops_nothing_and_mutates_nothing() -> None:
    pool = role_pool()
    before = [canonical_json(c) for c in pool]
    result = cluster_contributions(pool)
    assert [canonical_json(c) for c in pool] == before
    assert {id(m) for cl in result for m in cl.contributions} == {id(c) for c in pool}
    for cl in result:
        for m in cl.contributions:
            assert m.values["person.email"] == "info@x.com"
            assert "person.email" in {p.canonical_path for p in m.provenance}


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_disqualification_is_independent_of_and_composes_with_exclusions() -> None:
    pool = [
        *role_pool(),
        named("d", "bar@x.com", "Dee Fox"),
        named("e", "bar@x.com", "Dee Fox"),
    ]
    none = IdentityExclusions()
    assert cluster_contributions(pool, none) == cluster_contributions(pool)
    barred = IdentityExclusions.from_values(emails=["bar@x.com"])
    got = {
        frozenset(m.source_name for m in cl.contributions)
        for cl in cluster_contributions(pool, barred)
    }
    assert got == {frozenset(s) for s in "abcde"}
    # Also barring the role address changes nothing: it is already disqualified.
    role = IdentityExclusions.from_values(emails=["info@x.com"])
    assert cluster_contributions(pool, role) == cluster_contributions(pool)


def order_pool() -> list[LeadContribution]:
    return [
        named("a", "info@x.com", "Ann Lee"),
        named("b", "info@x.com", "Bob Ray"),
        named("c", "ann@x.com", "Ann Lee"),
        named("d", "ann@x.com", "ann lee"),
        contribution("e", person__linkedin_url="linkedin.com/in/ann"),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.14 (property)
def test_every_permutation_gives_identical_clusters_and_disqualified_set() -> None:
    pool = order_pool()
    expected = serialise(pool)
    expected_set = DisqualifiedAddresses.from_contributions(pool).addresses
    assert expected_set == frozenset({"info@x.com"})
    for perm in itertools.permutations(pool):
        assert serialise(list(perm)) == expected
        assert DisqualifiedAddresses.from_contributions(perm).addresses == expected_set


# Verifies: specs/lead-source-adapters/requirements.md#8.14 (property)
@pytest.mark.parametrize("seed", range(20))
def test_seeded_shuffles_give_byte_identical_clusters(seed: int) -> None:
    pool = order_pool() + [
        named(f"f{i}", f"r{i % 3}@y.com", f"P{i % 4}") for i in range(12)
    ]
    expected = serialise(pool)
    shuffled = list(pool)
    random.Random(seed).shuffle(shuffled)
    assert serialise(shuffled) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_late_contribution_with_a_new_name_disqualifies_for_all_earlier_ones() -> (
    None
):
    early = [named("a", "ann@x.com", "Ann Lee"), named("b", "ann@x.com", "Ann Lee")]
    assert partition(early) == {frozenset("ab")}
    assert partition([*early, named("c", "ann@x.com", "Cy Poe")]) == {
        frozenset("a"),
        frozenset("b"),
        frozenset("c"),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_clustering_is_idempotent_over_its_own_flattened_output() -> None:
    once = cluster_contributions(order_pool())
    flat = [m for cl in once for m in cl.contributions]
    twice = cluster_contributions(flat)
    assert [(c.cluster_id, c.contributions) for c in once] == [
        (c.cluster_id, c.contributions) for c in twice
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_large_input_stays_near_linear(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"union": 0, "find": 0}
    real_union, real_find = clustering._UnionFind.union, clustering._UnionFind.find

    def counting_union(
        self: Any, a: int, b: int, kind: Any = None, value: str | None = None
    ) -> None:
        calls["union"] += 1
        real_union(self, a, b, kind, value)

    def counting_find(self: Any, a: int) -> int:
        calls["find"] += 1
        return real_find(self, a)

    monkeypatch.setattr(clustering._UnionFind, "union", counting_union)
    monkeypatch.setattr(clustering._UnionFind, "find", counting_find)

    n = 5000
    pool: list[LeadContribution] = []
    for i in range(n // 2):  # one giant role address with a distinct name each
        pool.append(named(f"role{i:05d}", "info@x.com", f"Person {i}"))
    for i in range(n - len(pool)):  # many small clusters on one name per address
        pool.append(named(f"p{i:05d}", f"u{i // 3}@x.com", f"Name {i // 3}"))
    result = cluster_contributions(pool)
    assert sum(len(cl.contributions) for cl in result) == n
    assert len(result) == n // 2 + len({i // 3 for i in range(n - n // 2)})
    assert calls["union"] <= 3 * n
    assert calls["find"] <= 12 * n


class _Leak:
    """Stands in for a result type that exposes its personal values in ``repr``."""

    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:
        return f"_Leak({self.text!r})"


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_no_address_or_name_in_repr_or_error_text() -> None:
    secret_address, secret_name = "zz-secret@example.com", "Zelda Secretname"
    pool = [
        named("a", secret_address, secret_name),
        named("b", secret_address, "Other Person"),
    ]
    found = DisqualifiedAddresses.from_contributions(pool)
    assert found.addresses == frozenset({secret_address})
    texts = [
        repr(found),
        str(found),
        repr(extract_match_keys(pool[0], disqualified=found)),
    ]
    for text in texts:
        assert secret_address not in text
        assert secret_name.lower() not in text.lower()
    # Mutation check: the same assertion must fail for a type that does leak.
    assert secret_address in repr(_Leak(secret_address))

    class Boom:
        def __repr__(self) -> str:
            return "x"

    bad = contribution("s", person__email=Boom(), person__full_name=secret_name)
    with pytest.raises(TypeError) as err:
        DisqualifiedAddresses.from_contributions([bad])
    assert secret_name not in str(err.value)


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_composed_and_decomposed_accents_are_one_name_and_nbsp_is_a_space() -> None:
    pool = [
        named("a", "jose@x.com", "Jos" + chr(0xE9) + " Lee"),
        named("b", "jose@x.com", "Jose" + chr(0x301) + chr(0xA0) + " LEE"),
    ]
    assert DisqualifiedAddresses.from_contributions(pool).addresses == frozenset()


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_non_text_name_is_a_named_error_that_echoes_no_value() -> None:
    bad = contribution("s", person__email="a@x.com", person__full_name=12345678)
    with pytest.raises(TypeError) as err:
        DisqualifiedAddresses.from_contributions([bad])
    assert "12345678" not in str(err.value)


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_disqualified_address_stays_on_the_projected_lead() -> None:
    from leadforge.lead_ingestion.projection import project_lead

    pool = [named("a", "info@x.com", "Ann Lee"), named("b", "info@x.com", "Bob Ray")]
    clusters = cluster_contributions(pool)
    assert len(clusters) == 2
    ranks = {"a": 1, "b": 2}
    for cl in clusters:
        lead = project_lead(cl, ranks).lead
        assert lead is not None
        assert "info@x.com" in repr(lead.model_dump(mode="json")).lower()
