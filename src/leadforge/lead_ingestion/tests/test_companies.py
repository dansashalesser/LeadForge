"""Company Signal clustering on a registrable-domain set (task 16.9; 8.16)."""

import itertools
import random
import re
from collections.abc import Iterable
from typing import Any

import pytest

from leadforge.lead_ingestion import clustering, companies, match_keys
from leadforge.lead_ingestion.companies import (
    WEBMAIL_DOMAINS,
    CompanyCluster,
    cluster_company_signals,
    company_domains,
    company_id_for,
    domain_components,
)
from leadforge.lead_ingestion.models import CompanySignal


def sig(name: str | None, *domains: str) -> CompanySignal:
    return CompanySignal(company_id="provider-view", name=name, domains=domains)


def shape(clusters: Iterable[CompanyCluster]) -> list[tuple[Any, ...]]:
    """Everything observable about a clustering, so equality means byte-identical."""
    return [
        (c.company_id, c.domains, tuple(m.model_dump_json() for m in c.signals))
        for c in clusters
    ]


def names(cluster: CompanyCluster) -> list[str | None]:
    return [m.name for m in cluster.signals]


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_two_records_for_one_company_under_different_domains_are_one_company() -> None:
    clusters = cluster_company_signals(
        [sig("Acme", "acme.com"), sig("Acme Inc", "acme.com", "acme.io")]
    )

    assert len(clusters) == 1
    assert clusters[0].domains == ("acme.com", "acme.io")
    assert sorted(n or "" for n in names(clusters[0])) == ["Acme", "Acme Inc"]


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_subdomain_and_www_forms_reduce_to_the_registrable_domain() -> None:
    clusters = cluster_company_signals(
        [sig("A", "www.acme.com"), sig("B", "mail.acme.com"), sig("C", "ACME.com")]
    )

    assert [c.domains for c in clusters] == [("acme.com",)]


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_overlap_is_transitive_closure() -> None:
    clusters = cluster_company_signals(
        [
            sig("A", "a.com"),
            sig("B", "b.com"),
            sig("C", "a.com", "b.com"),
            sig("D", "c.com", "d.com"),
            sig("E", "d.com"),
            sig("F", "z.com"),
        ]
    )

    assert sorted(c.domains for c in clusters) == [
        ("a.com", "b.com"),
        ("c.com", "d.com"),
        ("z.com",),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_the_public_suffix_list_decides_what_is_registrable() -> None:
    clusters = cluster_company_signals(
        [
            sig("A", "shop.example.co.uk"),
            sig("B", "other.co.uk"),
            sig("C", "one.github.io"),
            sig("D", "two.github.io"),
        ]
    )

    # co.uk is a suffix, so the two UK companies stay apart; so do two github.io
    # tenants (private suffixes are included in the pinned snapshot).
    assert sorted(c.domains for c in clusters) == [
        ("example.co.uk",),
        ("one.github.io",),
        ("other.co.uk",),
        ("two.github.io",),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_punycode_and_unicode_spellings_are_one_domain() -> None:
    clusters = cluster_company_signals(
        [sig("A", "xn--bcher-kva.de"), sig("B", "bücher.de")]
    )

    assert len(clusters) == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_a_company_with_no_usable_domain_is_a_singleton_never_dropped() -> None:
    records = [
        sig("Acme"),
        sig("Acme"),  # identical to the first: still separate, never merged by name
        sig("Acme", "acme.com"),
        sig("Acme", "localhost"),
        sig("Acme", "127.0.0.1"),
        sig("Acme", "co.uk"),  # a bare public suffix names no company
    ]

    clusters = cluster_company_signals(records)

    assert len(clusters) == 6
    assert sum(len(c.signals) for c in clusters) == 6
    assert len({c.company_id for c in clusters}) == 6
    assert sorted(c.domains for c in clusters) == [(), (), (), (), (), ("acme.com",)]


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_a_singleton_id_is_stable_and_ignores_input_order() -> None:
    a, b = sig("Acme"), sig("Beta")

    forward = cluster_company_signals([a, b, a])
    backward = cluster_company_signals([a, a, b])

    assert shape(forward) == shape(backward)
    assert cluster_company_signals([a])[0].company_id in {c.company_id for c in forward}
    assert [c.company_id for c in forward if names(c) == ["Acme"]] == sorted(
        c.company_id for c in forward if names(c) == ["Acme"]
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_webmail_domains_never_cluster_companies() -> None:
    clusters = cluster_company_signals(
        [
            sig("Ann", "gmail.com"),
            sig("Bob", "gmail.com"),
            sig("Cy", "Outlook.com"),
            sig("Acme", "acme.com", "gmail.com"),
            sig("Acme Co", "acme.com"),
        ]
    )

    assert "gmail.com" in WEBMAIL_DOMAINS
    assert sorted(c.domains for c in clusters) == [(), (), (), ("acme.com",)]
    assert sorted(len(c.signals) for c in clusters) == [1, 1, 1, 2]


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_company_domains_is_the_normalised_registrable_set_without_webmail() -> None:
    assert company_domains("WWW.Acme.com") == frozenset({"acme.com"})
    assert company_domains(("a.com", "mail.a.com", "gmail.com")) == frozenset({"a.com"})
    assert company_domains(None) == frozenset()
    assert company_domains("  ") == frozenset()
    assert company_domains("localhost") == frozenset()
    with pytest.raises(TypeError, match=r"company\.domain"):
        company_domains(42)


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_company_id_derives_from_the_domain_set_only() -> None:
    one = cluster_company_signals([sig("Acme", "acme.com", "acme.io")])[0]
    two = cluster_company_signals([sig("Other name", "www.acme.io", "ACME.com")])[0]
    wider = cluster_company_signals([sig("Acme", "acme.com", "acme.io", "acme.net")])[0]

    assert one.company_id == two.company_id == company_id_for({"acme.com", "acme.io"})
    assert one.company_id != wider.company_id
    assert re.fullmatch(r"co-[0-9a-f]{16}", one.company_id)
    # Not the order the domains arrive in.
    assert company_id_for(["b.com", "a.com"]) == company_id_for(["a.com", "b.com"])


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_every_ordering_of_a_small_input_gives_identical_clusters() -> None:
    records = [
        sig("A", "a.com"),
        sig("B", "b.com", "a.com"),
        sig("C", "c.com"),
        sig(None, "mail.c.com", "d.org"),
        sig("N"),
        sig("N"),
    ]
    expected = shape(cluster_company_signals(records))

    assert len(expected) == 4
    for permutation in itertools.permutations(records):
        assert shape(cluster_company_signals(permutation)) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_seeded_shuffles_of_a_large_input_give_identical_clusters() -> None:
    rng = random.Random(20261005)
    pool = [
        sig(f"n{i}", *{f"d{rng.randrange(40)}.com" for _ in range(rng.randrange(0, 3))})
        for i in range(150)
    ]
    expected = shape(cluster_company_signals(pool))

    for _ in range(25):
        shuffled = pool[:]
        rng.shuffle(shuffled)
        assert shape(cluster_company_signals(shuffled)) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_clustering_is_idempotent_and_does_not_mutate_its_input() -> None:
    records = (
        sig("A", "a.com"),
        sig("B", "a.com", "b.com"),
        sig("C"),
        sig("D", "z.io"),
    )
    before = [r.model_dump_json() for r in records]

    first = cluster_company_signals(records)
    again = cluster_company_signals(m for c in first for m in c.signals)

    assert [r.model_dump_json() for r in records] == before
    assert shape(again) == shape(first)


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_clusters_and_members_come_out_in_one_canonical_order() -> None:
    clusters = cluster_company_signals(
        [sig("Z", "z.com"), sig("B", "a.com"), sig("A", "a.com")]
    )

    assert [c.company_id for c in clusters] == sorted(c.company_id for c in clusters)
    members = [clustering.canonical_value_json(m) for m in clusters[0].signals]
    assert members == sorted(members)


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_empty_input_gives_no_clusters() -> None:
    assert cluster_company_signals([]) == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_domain_components_label_each_company_by_its_lowest_index() -> None:
    sets = [
        frozenset({"a.com"}),
        frozenset(),
        frozenset({"b.com"}),
        frozenset({"a.com", "b.com"}),
        frozenset(),
        frozenset({"c.com"}),
    ]

    assert domain_components(sets) == (0, 1, 0, 0, 4, 5)
    assert domain_components([]) == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_large_input_uses_near_linear_union_operations(
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
    pool: list[CompanySignal] = []
    for i in range(n // 2):  # one giant star on a shared domain
        pool.append(sig(f"star{i}", "hub.com", f"s{i}.com"))
    for i in range(n // 5):  # small chains
        pool.append(sig(f"chain{i}", f"c{i}.com", f"c{i + 1}.com"))
    for i in range(n - len(pool)):  # many singletons, some with no domain
        pool.append(sig(f"solo{i}", f"solo{i}.com") if i % 2 else sig(f"none{i}"))
    clusters = cluster_company_signals(pool)

    assert sum(len(c.signals) for c in clusters) == n
    assert calls["union"] <= 3 * n
    assert calls["find"] <= 12 * n


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_the_pinned_suffix_snapshot_is_the_only_source_and_no_socket_opens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import socket

    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("company clustering opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)

    assert match_keys._PSL.suffix_list_urls == ()
    assert not hasattr(companies, "_PSL")  # one pinned extractor, in match_keys
    clusters = cluster_company_signals([sig("A", "shop.example.co.uk")])
    assert clusters[0].domains == ("example.co.uk",)


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_repr_and_errors_carry_no_company_content() -> None:
    cluster = cluster_company_signals([sig("Sentinel Name", "sentinel-co.com")])[0]
    assert "Sentinel" not in repr(cluster)
    assert "sentinel-co" not in repr(cluster)  # a personal domain is personal data
    with pytest.raises(TypeError) as raised:
        company_domains(("ok.com", 12345))
    assert "12345" not in str(raised.value)
