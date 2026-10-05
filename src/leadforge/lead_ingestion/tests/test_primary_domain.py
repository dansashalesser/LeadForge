"""Display-only primary domain, elected by a trust-weighted vote (task 16.10; 8.17)."""

import ast
import inspect
import itertools
import random
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from leadforge.lead_ingestion import (
    clustering,
    companies,
    match_keys,
    orchestrator,
    primary_domain,
    projection,
)
from leadforge.lead_ingestion.companies import (
    CompanyCluster,
    cluster_company_signals,
)
from leadforge.lead_ingestion.models import (
    CompanySignal,
    ProviderCompanyId,
    TechSignal,
)
from leadforge.lead_ingestion.primary_domain import (
    PrimaryDomain,
    elect_primary_domain,
)

RANKS: Mapping[str, int] = {"high": 5, "mid": 3, "low": 1}


def sig(source: str | None, *domains: str, name: str = "Acme") -> CompanySignal:
    ids = (ProviderCompanyId(source=source, id="x1"),) if source else ()
    return CompanySignal(
        company_id="view", name=name, provider_ids=ids, domains=domains
    )


def cluster(*signals: CompanySignal) -> CompanyCluster:
    (only,) = cluster_company_signals(signals)
    return only


def elect(
    signals: list[CompanySignal], ranks: Mapping[str, int] = RANKS
) -> PrimaryDomain:
    return elect_primary_domain(cluster(*signals), ranks)


def raw(signals: list[CompanySignal], ranks: Mapping[str, int]) -> PrimaryDomain:
    """Elect over signals as one cluster without clustering (disjoint domains)."""
    return elect_primary_domain(CompanyCluster("co-x", (), tuple(signals)), ranks)


def outcome(p: PrimaryDomain) -> tuple[Any, ...]:
    return (p.domain, p.tied, p.tied_domains)


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_the_domain_with_the_greatest_weight_wins() -> None:
    p = elect([sig("high", "a.com", "b.com"), sig("low", "b.com")])
    # a.com: 5+1 = 6; b.com: (5+1) + (1+1) = 8
    assert outcome(p) == ("b.com", False, ())


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_one_higher_ranked_source_outvotes_two_lower_ranked() -> None:
    signals = [sig("high", "a.com"), sig("low", "a.io"), sig("low", "a.io", "a.com")]
    # a.com: high(6) + low(2) = 8 ; a.io: low only, once (repeat) = 2
    assert elect(signals).domain == "a.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_weight_follows_rank_not_vote_count() -> None:
    signals = [
        sig("high", "big.com"),
        sig("low", "x.com", "big.com"),
        sig("mid", "y.com", "big.com"),
    ]
    signals += [sig("low", "x.com")]
    assert elect(signals).domain == "big.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_source_repeating_a_domain_votes_for_it_once() -> None:
    signals = [
        sig("low", "a.com"),
        sig("low", "a.com"),
        sig("low", "a.com"),
        sig("mid", "b.com", "a.com"),
    ]
    # a.com: low once (2) + mid (4) = 6 ; b.com: 4
    p = elect(signals)
    assert outcome(p) == ("a.com", False, ())


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_repetition_cannot_flip_a_result() -> None:
    once = elect([sig("low", "a.com", "b.com"), sig("high", "b.com", "a.com", "c.com")])
    again = elect(
        [
            sig("low", "a.com", "b.com"),
            sig("low", "a.com"),
            sig("high", "b.com", "a.com", "c.com"),
        ]
    )
    assert outcome(once) == outcome(again)


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_an_undeclared_source_takes_the_lowest_rank_and_still_counts() -> None:
    p = elect([sig("unknown", "a.com", "b.com"), sig("low", "b.com")])
    assert outcome(p) == ("b.com", False, ())  # (0+1)+(1+1) vs (0+1)


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_declared_lowest_rank_equals_an_undeclared_source() -> None:
    declared = elect([sig("s", "a.com", "b.com"), sig("t", "a.com", "b.com")], {"s": 0})
    assert declared.tied  # s (0) and t (undeclared) both weigh the same


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_all_zero_ranks_still_vote_by_count_of_sources() -> None:
    ranks = {"a": 0, "b": 0, "c": 0}
    p = elect([sig("a", "x.com"), sig("b", "x.com"), sig("c", "y.com", "x.com")], ranks)
    assert outcome(p) == ("x.com", False, ())


# Verifies: specs/lead-source-adapters/requirements.md#8.17
@pytest.mark.parametrize("bad", [-1, True, 1.5, "3"])
def test_a_bad_rank_is_rejected(bad: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        elect([sig("s", "a.com")], {"s": bad})


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_signal_with_no_source_is_one_undeclared_voter() -> None:
    p = elect([sig(None, "a.com", "b.com"), sig(None, "b.com"), sig("low", "a.com")])
    # anonymous voter: a.com 1, b.com 1 (repeat collapses); low: a.com 2 -> a.com 3
    assert outcome(p) == ("a.com", False, ())


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_webmail_and_nameless_records_cast_no_vote() -> None:
    p = elect(
        [
            sig("high", "gmail.com", "a.com"),
            sig("mid", "a.com", "b.com"),
            sig("low", "b.com"),
        ]
    )
    # a.com: 6+4 = 10; b.com: 4+2 = 6; gmail.com would be 6 if it voted
    assert outcome(p) == ("a.com", False, ())
    assert outcome(
        elect([sig("high", "gmail.com", "a.com"), sig("low", "a.com", "b.com")])
    ) == (
        "a.com",
        False,
        (),
    )
    assert elect([sig("high", "a.com")]).domain == "a.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_domainless_cluster_has_no_primary_domain() -> None:
    (only,) = cluster_company_signals([sig("high", "gmail.com")])
    assert outcome(elect_primary_domain(only, RANKS)) == (None, False, ())
    (named,) = cluster_company_signals([CompanySignal(company_id="v", name="Acme")])
    assert outcome(elect_primary_domain(named, RANKS)) == (None, False, ())


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_subdomains_vote_for_their_registrable_domain() -> None:
    p = elect([sig("high", "www.a.com", "b.com"), sig("mid", "mail.a.com")])
    assert p.domain == "a.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_an_exact_tie_is_reported_with_its_candidates_and_a_provisional_winner() -> (
    None
):
    p = elect([sig("high", "b.com", "a.com", "c.com")])  # one source, three equal votes
    assert outcome(p)[1:] == (True, ("a.com", "b.com", "c.com"))
    assert (
        p.domain == "a.com"
    )  # lowest-sorted candidate, as 8.18 does in synthetic mode


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_only_the_leaders_are_tied_candidates() -> None:
    p = elect([sig("high", "a.com", "b.com", "c.com"), sig("low", "c.com", "d.com")])
    # c.com: 8; a, b: 6; d: 2
    assert outcome(p) == ("c.com", False, ())
    q = elect([sig("high", "z.com", "y.com", "x.com"), sig("low", "x.com")])
    assert outcome(q) == ("x.com", False, ())


# Verifies: specs/lead-source-adapters/requirements.md#8.18
def test_a_tie_among_leaders_excludes_trailing_domains() -> None:
    p = elect(
        [
            sig("high", "z.com", "y.com"),
            sig("high", "w.com"),
            sig("low", "w.com", "z.com"),
        ]
    )
    # z: 6+2 = 8, w: 6+2 = 8, y: 6
    assert outcome(p) == ("w.com", True, ("w.com", "z.com"))


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_no_tie_means_no_candidates() -> None:
    assert elect([sig("high", "a.com")]).tied_domains == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_every_permutation_of_the_signals_gives_the_same_election() -> None:
    signals = [
        sig("high", "a.com", "b.com"),
        sig("mid", "b.com", "c.com"),
        sig("low", "c.com", "a.com"),
        sig(None, "a.com"),
        sig("unknown", "c.com", "a.com"),
    ]
    expected = outcome(elect(signals))
    for perm in itertools.permutations(signals):
        assert outcome(elect_primary_domain(cluster(*perm), RANKS)) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_every_permutation_of_a_tie_gives_the_same_candidates() -> None:
    signals = [
        sig("high", "b.com", "a.com"),
        sig("mid", "c.com", "a.com", "b.com"),
        sig("mid", "c.com"),
    ]
    # a: 6+4 = 10; b: 10; c: 8
    expected = outcome(elect(signals))
    assert expected == ("a.com", True, ("a.com", "b.com"))
    for perm in itertools.permutations(signals):
        assert outcome(elect_primary_domain(cluster(*perm), RANKS)) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_seeded_shuffles_give_the_same_election() -> None:
    rng = random.Random(1610)
    sources = ["high", "mid", "low", "unknown", None]
    pool = ["a.com", "b.com", "c.com", "d.com"]
    for _ in range(25):
        signals = [
            sig(rng.choice(sources), *rng.sample(pool, rng.randint(1, 3)))
            for _ in range(rng.randint(2, 12))
        ]
        # make them one company
        signals.append(sig("low", *pool))
        expected = outcome(elect(signals))
        for _ in range(8):
            shuffled = signals[:]
            rng.shuffle(shuffled)
            assert outcome(elect_primary_domain(cluster(*shuffled), RANKS)) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_election_is_idempotent_and_does_not_mutate_its_inputs() -> None:
    c = cluster(sig("high", "a.com", "b.com"), sig("low", "b.com"))
    ranks = dict(RANKS)
    before = (c, dict(ranks), [s.model_dump_json() for s in c.signals])
    first = elect_primary_domain(c, ranks)
    second = elect_primary_domain(c, ranks)
    assert first == second
    assert (c, ranks, [s.model_dump_json() for s in c.signals]) == before


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_cluster_with_the_election_is_still_the_same_cluster() -> None:
    c = cluster(sig("high", "a.com", "b.com"), sig("low", "b.com"))
    elect_primary_domain(c, RANKS)
    again = cluster_company_signals(list(c.signals))
    assert again == (c,)


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_signal_strength_never_votes() -> None:
    def build(strength: float) -> list[CompanySignal]:
        weak = CompanySignal(
            company_id="v",
            name="A",
            domains=("a.com", "b.com"),
            provider_ids=(ProviderCompanyId(source="low", id="1"),),
            tech_signals=(TechSignal(label="k8s", strength=strength),),
        )
        return [weak, sig("mid", "b.com", "a.com")]

    assert outcome(elect(build(0.0))) == outcome(elect(build(1.0)))
    assert "strength" not in inspect.getsource(primary_domain).lower().replace(
        "signal strength", ""
    ).replace("strength never", "")


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_the_election_never_reads_a_name() -> None:
    a = elect([sig("high", "a.com", "b.com", name="One")])
    b = elect([sig("high", "a.com", "b.com", name="Two")])
    assert outcome(a) == outcome(b)


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_personal_data_is_not_in_repr() -> None:
    p = elect([sig("high", "secret-person.com")])
    assert "secret-person" not in repr(p)


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_ranks_change_the_election_but_no_cluster_key_id_or_dedupe_outcome() -> None:
    signals = [sig("high", "a.com"), sig("low", "b.com"), sig("mid", "a.com", "b.com")]
    other = [sig("high", "a.com"), sig("low", "b.com"), sig("mid", "a.com", "b.com")]
    flipped = {"high": 1, "mid": 3, "low": 9}
    assert elect(signals).domain != elect(other, flipped).domain
    assert cluster_company_signals(signals) == cluster_company_signals(other)
    assert cluster(*signals).company_id == cluster(*other).company_id
    # Dedupe takes no election and no rank input at all.
    assert list(inspect.signature(orchestrator.per_company_work_list).parameters) == [
        "work_list"
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_no_match_rule_can_take_the_primary_domain() -> None:
    modules = (clustering, companies, match_keys, orchestrator, projection)
    for module in modules:
        tree = ast.parse(Path(inspect.getfile(module)).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("primary_domain"), module
                assert all(a.name != "primary_domain" for a in node.names), module
            if isinstance(node, ast.Import):
                assert all("primary_domain" not in a.name for a in node.names), module
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                params = [a.arg for a in node.args.args + node.args.kwonlyargs]
                assert not any("primary" in p for p in params), (module, node.name)


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_the_election_imports_no_model_client_and_does_no_io() -> None:
    tree = ast.parse(Path(inspect.getfile(primary_domain)).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    banned = (
        "socket",
        "httpx",
        "requests",
        "urllib",
        "anthropic",
        "openai",
        "sqlite",
        "sqlalchemy",
    )
    assert not [m for m in imported if m.startswith(banned)]


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_large_input_normalises_each_signal_once_and_is_near_linear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    real = companies.company_domains

    def counting(value: object) -> frozenset[str]:
        calls["n"] += 1
        return real(value)

    monkeypatch.setattr(primary_domain, "company_domains", counting, raising=True)
    n = 5000
    signals = [
        sig(
            ("high", "mid", "low")[i % 3],
            "hub.com",
            *([f"d{i % 40}.com"] * (i % 3 == 2)),
        )
        for i in range(n)
    ]
    c = CompanyCluster("co-x", (), tuple(signals))
    p = elect_primary_domain(c, RANKS)
    assert p.domain == "hub.com"
    assert calls["n"] == n


# Verifies: specs/lead-source-adapters/requirements.md#8.17
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), 2.0, None, -(10**30)])
def test_non_int_and_negative_ranks_are_named_errors(bad: Any) -> None:
    with pytest.raises((TypeError, ValueError)) as caught:
        elect([sig("s", "secret-person.com")], {"s": bad})
    assert "secret-person" not in str(caught.value)


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_huge_rank_is_exact_and_a_source_missing_from_the_mapping_weighs_one() -> (
    None
):
    huge = 10**40
    assert raw([sig("h", "a.com"), sig("x", "b.com")], {"h": huge}).domain == "a.com"
    assert outcome(raw([sig("x", "a.com"), sig("y", "b.com")], {})) == (
        "a.com",
        True,
        ("a.com", "b.com"),
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_tied_candidates_are_not_in_repr_and_a_win_by_one_is_not_a_tie() -> None:
    tie = raw(
        [sig("low", "tied-one.com"), sig("mid", "tied-two.com")], {"low": 1, "mid": 1}
    )
    assert tie.tied
    assert "tied-" not in repr(tie)
    win = raw([sig("mid", "a.com"), sig("low", "b.com"), sig("low", "b.com")], RANKS)
    assert outcome(win) == ("a.com", False, ())  # 4 vs 2 (low votes once)
    one = raw(
        [sig("mid", "z.com"), sig("low", "a.com"), sig("low", "a.com", "z.com")], RANKS
    )
    assert outcome(one) == ("z.com", False, ())  # z: 4+1, a: 1 (low votes once)


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_trailing_domain_is_excluded_from_a_tie_of_the_rest() -> None:
    p = raw([sig("mid", "m.com"), sig("mid", "n.com"), sig("low", "a.com")], RANKS)
    assert outcome(p) == ("m.com", True, ("m.com", "n.com"))


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_every_source_of_a_signal_votes_for_each_of_its_domains() -> None:
    both = CompanySignal(
        company_id="view",
        provider_ids=(
            ProviderCompanyId(source="high", id="1"),
            ProviderCompanyId(source="mid", id="2"),
        ),
        domains=("a.com", "b.com"),
    )
    assert outcome(raw([both, sig("low", "b.com")], RANKS)) == ("b.com", False, ())


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_a_domain_one_weight_behind_is_not_a_tied_candidate() -> None:
    p = raw([sig("mid", "m.com"), sig("low", "a.com")], RANKS)  # 4 vs 1, then 4 vs 3
    assert outcome(p) == ("m.com", False, ())
    close = raw([sig("mid", "m.com"), sig("mid2", "n.com")], {"mid": 3, "mid2": 2})
    assert outcome(close) == ("m.com", False, ())


# Verifies: specs/lead-source-adapters/requirements.md#8.17
def test_all_sourceless_signals_are_one_shared_voter() -> None:
    # Two sourceless records naming a.com vote once; one named low source for b.com
    # weighs the same as that single shared voter, so the result is an exact tie.
    p = raw([sig(None, "a.com"), sig(None, "a.com"), sig("low", "b.com")], {"low": 0})
    assert outcome(p) == ("a.com", True, ("a.com", "b.com"))
