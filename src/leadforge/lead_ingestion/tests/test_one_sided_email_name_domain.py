"""Name+domain joins when only one record has an email (8.3 follow-up).

User decision (2026-10-06): a name+company-domain Match Key may join two person
records when only ONE of them carries an email (verified or not); two DIFFERENT
emails still block the join. Every earlier guard stays: LinkedIn holders never take
part, two distinct LinkedIns never share a cluster, a name+domain value seen with
two or more distinct LinkedIns or (now) two or more distinct emails is disqualified,
and a bare record can never bridge two different addresses, in any input order.

User-directed fix (2026-10-06): a role address (one the 8.14 pass finds shared, such
as ``info@``) is not personal identity, and an unverified address (a guess, such as a
provider pattern guess) is not evidence against a verified one. Neither blocks a
join nor makes a name+domain ambiguous; two VERIFIED different addresses still
block, and so do two different unverified ones when no verified address is known.
"""

import itertools
import random
from datetime import UTC, datetime
from typing import Any

import pytest

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
    stated_email,
)
from leadforge.lead_ingestion.models import (
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
)
from leadforge.lead_ingestion.projection import project_lead

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
V = EmailStatus.VERIFIED
U = EmailStatus.UNVERIFIED
A = EmailStatus.ACCEPT_ALL
E1 = "jane@acme.com"
E2 = "j.doe@acme.com"
E3 = "doe.jane@acme.com"
ROLE = "info@acme.com"
RANKS = {s: i for i, s in enumerate("abcdefgh", start=1)}


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


def jane(
    source: str,
    email: str | None = None,
    status: EmailStatus = V,
    domain: str | list[str] = "acme.com",
    **extra: Any,
) -> LeadContribution:
    values: dict[str, Any] = {
        "person__full_name": "Jane Doe",
        "company__domain": domain,
        "person__title": "CTO",
        **extra,
    }
    if email is not None:
        values["person__email"] = email
        values["person__email_status"] = status
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


def assert_same_in_every_order(
    pool: list[LeadContribution], expected: set[frozenset[str]]
) -> None:
    reference = serialise(cluster_contributions(pool))
    for perm in itertools.permutations(pool):
        result = cluster_contributions(list(perm))
        assert serialise(result) == reference
        assert {
            frozenset(m.source_name for m in cl.contributions) for cl in result
        } == (expected)


def leads(pool: list[LeadContribution]) -> list[Any]:
    projected = [project_lead(cl, RANKS).lead for cl in cluster_contributions(pool)]
    return [lead for lead in projected if lead is not None]


def zed(source: str = "h") -> LeadContribution:
    """Another person reported with ``ROLE``, which makes it a shared address (8.14)."""
    return contribution(
        source,
        person__full_name="Zed Moss",
        person__email=ROLE,
        person__email_status=V,
        company__domain="acme.com",
        person__title="CEO",
    )


def assert_one_personal_email(
    members: tuple[LeadContribution, ...], pool: list[LeadContribution]
) -> None:
    """At most one verified address, or one guess when none is verified; roles aside."""
    role = DisqualifiedAddresses.from_contributions(pool).addresses
    verified: set[str] = set()
    guessed: set[str] = set()
    for member in members:
        address = stated_email(member.values)
        if address is not None and address not in role:
            is_verified = member.values.get("person.email_status") is V
            (verified if is_verified else guessed).add(address)
    assert len(verified) <= 1
    assert verified or len(guessed) <= 1


# --- the one-sided join ---------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_verified_email_record_and_an_email_less_record_join_by_name_domain() -> None:
    pool = [jane("a", E1), jane("b")]
    assert_same_in_every_order(pool, {frozenset("ab")})
    (cluster,) = cluster_contributions(pool)
    assert MatchKeyKind.NAME_DOMAIN in cluster.merged_by


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_end_to_end_the_merge_projects_one_lead_carrying_the_email() -> None:
    pool = [jane("a", E1), jane("b")]
    projected = leads(pool)
    assert len(projected) == 1
    assert str(projected[0].email) == E1
    assert projected[0].email_status is V


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_an_unverified_email_counts_as_an_email_and_still_joins_one_sided() -> None:
    pool = [jane("a", E1, U), jane("b")]
    assert_same_in_every_order(pool, {frozenset("ab")})


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_the_same_unverified_email_on_both_sides_is_not_a_conflict() -> None:
    pool = [jane("a", E1, U), jane("b", " JANE@acme.com", U)]
    assert_same_in_every_order(pool, {frozenset("ab")})


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_the_join_still_needs_a_corroborating_attribute() -> None:
    pool = [
        jane("a", E1),
        contribution("b", person__full_name="Jane Doe", company__domain="acme.com"),
    ]
    assert_same_in_every_order(pool, {frozenset("a"), frozenset("b")})


# --- two different emails block -------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_two_different_verified_emails_stay_two_leads_end_to_end() -> None:
    pool = [jane("a", E1), jane("b", E2)]
    assert_same_in_every_order(pool, {frozenset("a"), frozenset("b")})
    projected = leads(pool)
    assert len(projected) == 2
    assert {str(lead.email) for lead in projected} == {E1, E2}


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_two_different_unverified_emails_block_the_join() -> None:
    # Neither address is known to be the person's, so two guesses stay two leads.
    for first, second in [(U, U), (U, A), (A, A)]:
        pool = [jane("a", E1, first), jane("b", E2, second)]
        assert_same_in_every_order(pool, {frozenset("a"), frozenset("b")})


# --- no transitive bridge -------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_a_bare_record_between_two_addresses_joins_neither_in_any_order() -> None:
    pool = [jane("a", E1), jane("c"), jane("b", E2)]
    assert_same_in_every_order(pool, {frozenset("a"), frozenset("b"), frozenset("c")})
    projected = leads(pool)
    assert len(projected) == 3


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_the_bridge_rule_holds_for_unverified_addresses_too() -> None:
    pool = [jane("a", E1, U), jane("c"), jane("b", E2, U)]
    assert_same_in_every_order(pool, {frozenset("a"), frozenset("b"), frozenset("c")})


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_a_bare_record_cannot_bridge_two_addresses_across_two_domains() -> None:
    # Each name+domain value sees one address only, so the key-level rule does not
    # fire; the joined component would carry two addresses, so nobody joins.
    pool = [
        jane("a", E1, domain="acme.com"),
        jane("c", domain=["acme.com", "acme.io"]),
        jane("b", E2, domain="acme.io"),
    ]
    assert DisqualifiedAddresses.from_contributions(pool).name_domains == frozenset()
    assert_same_in_every_order(pool, {frozenset("a"), frozenset("b"), frozenset("c")})


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_a_chain_of_bare_records_cannot_bridge_two_addresses() -> None:
    pool = [
        jane("a", E1, domain="acme.com"),
        jane("c", domain=["acme.com", "acme.io"]),
        jane("d", domain=["acme.io", "acme.net"]),
        jane("b", E2, domain="acme.net"),
    ]
    assert_same_in_every_order(
        pool, {frozenset("a"), frozenset("b"), frozenset("c"), frozenset("d")}
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_an_unambiguous_component_on_two_domains_still_joins() -> None:
    pool = [
        jane("a", E1, domain="acme.com"),
        jane("c", domain=["acme.com", "acme.io"]),
        jane("b", E1, U, domain="acme.io"),
    ]
    assert_same_in_every_order(pool, {frozenset("abc")})


# --- disqualification -----------------------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_name_domain_seen_with_two_distinct_emails_is_disqualified() -> None:
    pool = [jane("a", E1), jane("b", E2), jane("c")]
    found = DisqualifiedAddresses.from_contributions(pool)
    assert len(found.name_domains) == 1
    assert found == DisqualifiedAddresses.from_contributions(reversed(pool))


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_one_distinct_email_in_any_spelling_does_not_disqualify() -> None:
    pool = [jane("a", E1), jane("b", " Jane@ACME.com ", U), jane("c")]
    assert DisqualifiedAddresses.from_contributions(pool).name_domains == frozenset()


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_emails_of_linkedin_holders_do_not_make_a_name_domain_ambiguous() -> None:
    # LinkedIn holders never join by name+domain, so their addresses cannot bridge.
    pool = [
        jane("a", E1, person__linkedin_url="linkedin.com/in/jd"),
        jane("b", E2, person__linkedin_url="linkedin.com/in/jd"),
        jane("c"),
        jane("d"),
    ]
    assert DisqualifiedAddresses.from_contributions(pool).name_domains == frozenset()
    assert partition(pool) == {frozenset("ab"), frozenset("cd")}


# --- the LinkedIn rules still win -----------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#8.1
# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_linkedin_holder_still_never_joins_by_name_domain() -> None:
    pool = [jane("a", E1, person__linkedin_url="linkedin.com/in/jd"), jane("b")]
    assert_same_in_every_order(pool, {frozenset("a"), frozenset("b")})


# Verifies: specs/lead-source-adapters/requirements.md#8.1
# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_two_linkedins_on_the_name_domain_still_disqualify_one_sided_joins() -> None:
    pool = [
        jane("p", person__linkedin_url="linkedin.com/in/one"),
        jane("q", person__linkedin_url="linkedin.com/in/two"),
        jane("a", E1),
        jane("c"),
    ]
    assert_same_in_every_order(
        pool, {frozenset("p"), frozenset("q"), frozenset("a"), frozenset("c")}
    )


# Verifies: specs/lead-source-adapters/requirements.md#8.1
def test_a_one_sided_join_never_puts_two_linkedins_in_one_cluster() -> None:
    # a reaches LinkedIn one through its verified address; b reaches LinkedIn two.
    # a and b have different addresses, so the bare c must not join them together.
    pool = [
        contribution(
            "p",
            person__linkedin_url="linkedin.com/in/one",
            person__email=E1,
            person__email_status=V,
        ),
        contribution(
            "q",
            person__linkedin_url="linkedin.com/in/two",
            person__email=E2,
            person__email_status=V,
        ),
        jane("a", E1),
        jane("b", E2),
        jane("c"),
    ]
    result = partition(pool)
    assert not any({"p", "q"} <= group for group in result)
    assert not any({"a", "b"} <= group for group in result)


# --- exclusions and role addresses ---------------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_barring_the_email_only_splits_never_merges() -> None:
    pool = [
        jane("a", E1),
        jane("c"),
        contribution("e", person__email=E1, person__email_status=V),
    ]
    assert partition(pool) == {frozenset("ace")}
    barred = IdentityExclusions.from_values(emails=[E1])
    assert partition(pool, barred) == {frozenset("ac"), frozenset("e")}


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_crm_bare_email_path_counts_as_an_email_for_the_block() -> None:
    # Built directly: ``jane(email=...)`` would write ``person.email``, not the bare
    # CRM ``email`` path this test is about.
    crm = contribution(
        "b",
        email=E2,
        person__full_name="Jane Doe",
        company__domain="acme.com",
        person__title="CTO",
    )
    assert "person.email" not in crm.values
    # Both unverified (a bare CRM address has no status): two guesses still block.
    pool = [jane("a", E1, U), crm]
    assert stated_email(crm.values) == E2
    assert_same_in_every_order(pool, {frozenset("a"), frozenset("b")})
    # With no status it is a guess, so it joins a verified address of the person.
    assert_same_in_every_order([jane("a", E1), crm], {frozenset("ab")})


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_status_stated_with_a_bare_crm_address_applies_to_it() -> None:
    # Projection re-keys the bare address as person.email and reads the status for
    # it, so clustering does too: a VERIFIED bare address is a known address.
    crm = contribution(
        "b",
        email=E2,
        person__email_status=V,
        person__full_name="Jane Doe",
        company__domain="acme.com",
        person__title="CTO",
    )
    assert_same_in_every_order([jane("a", E1), crm], {frozenset("a"), frozenset("b")})


# --- order independence over random pools ---------------------------------------


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_random_linkedin_less_pools_never_mix_two_emails_and_ignore_order() -> None:
    emails: list[tuple[str | None, EmailStatus]] = [
        (None, V),
        (E1, V),
        (E2, U),
        ("jd@acme.com", V),
    ]
    domains: list[str | list[str]] = ["acme.com", "acme.io", ["acme.com", "acme.io"]]
    for seed in range(40):
        rng = random.Random(seed)
        pool = []
        for source in "abcde":
            email, status = rng.choice(emails)
            pool.append(
                contribution(
                    source,
                    person__full_name=rng.choice(["Jane Doe", "John Roe"]),
                    company__domain=rng.choice(domains),
                    person__title=rng.choice(["CTO", "CEO"]),
                    **(
                        {"person__email": email, "person__email_status": status}
                        if email
                        else {}
                    ),
                )
            )
        reference = serialise(cluster_contributions(pool))
        for _ in range(30):
            rng.shuffle(pool)
            assert serialise(cluster_contributions(pool)) == reference
        for cluster in cluster_contributions(pool):
            assert_one_personal_email(cluster.contributions, pool)


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_every_permutation_of_small_mixed_pools_gives_one_result() -> None:
    # Property-style: 3-5 records mixing chains across two domains, LinkedIn holders,
    # and one address in two spellings. Every permutation must give the same clusters,
    # the same disqualified set, and the same clusters under an exclusion.
    emails: list[tuple[str | None, EmailStatus]] = [
        (None, V),
        (None, V),
        (E1, V),
        (" JANE@acme.com", U),
        (E2, U),
    ]
    domains: list[str | list[str]] = ["acme.com", "acme.io", ["acme.com", "acme.io"]]
    urls = [None, None, "linkedin.com/in/one", "linkedin.com/in/two"]
    barred = IdentityExclusions.from_values(emails=[E1])
    for seed in range(30):
        rng = random.Random(seed)
        pool = []
        for source in "abcde"[: rng.randint(3, 5)]:
            email, status = rng.choice(emails)
            url = rng.choice(urls)
            extra: dict[str, Any] = {"person__linkedin_url": url} if url else {}
            pool.append(jane(source, email, status, rng.choice(domains), **extra))
        reference = serialise(cluster_contributions(pool))
        disqualified = DisqualifiedAddresses.from_contributions(pool)
        under_exclusion = serialise(cluster_contributions(pool, barred))
        for perm in itertools.permutations(pool):
            order = list(perm)
            assert serialise(cluster_contributions(order)) == reference
            assert DisqualifiedAddresses.from_contributions(order) == disqualified
            assert serialise(cluster_contributions(order, barred)) == under_exclusion
        for cluster in cluster_contributions(pool):
            members = cluster.contributions
            urls_in = {m.values.get("person.linkedin_url") for m in members} - {None}
            assert len(urls_in) <= 1
            if not urls_in:
                assert_one_personal_email(members, pool)


# --- role addresses and guesses are not identity (user-directed fix 2026-10-06) --

# Projection resolves person.email by Source Trust Rank first (8.4), not by
# verification or role, so a higher-ranked source wins the field. Projection is
# outside this change; strict, so these fail loudly once projection is fixed.
PROJECTION_RANKS_BEFORE_VERIFICATION = pytest.mark.xfail(
    strict=True,
    reason="projection picks person.email by trust rank, not verified status",
)


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_role_address_does_not_block_a_namesakes_own_address_end_to_end() -> None:
    pool = [jane("a", ROLE), jane("b", E1), zed()]
    assert_same_in_every_order(pool, {frozenset("ab"), frozenset("h")})
    assert DisqualifiedAddresses.from_contributions(pool).name_domains == frozenset()
    projected = leads(pool)
    assert len(projected) == 2
    (own,) = [lead for lead in projected if lead.full_name == "Jane Doe"]
    assert str(own.email) == E1
    assert own.email_status is V


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_role_address_does_not_block_an_unverified_own_address() -> None:
    pool = [jane("a", ROLE), jane("b", E1, U), zed()]
    assert_same_in_every_order(pool, {frozenset("ab"), frozenset("h")})


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.14
@PROJECTION_RANKS_BEFORE_VERIFICATION
def test_a_role_address_never_becomes_the_email_from_a_higher_ranked_source() -> None:
    pool = [jane("g", ROLE), jane("b", E1), zed()]
    (own,) = [lead for lead in leads(pool) if lead.full_name == "Jane Doe"]
    assert str(own.email) == E1


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_role_address_still_never_links_two_people_as_an_email_key() -> None:
    # Same title and domain, different names: only the shared address could link
    # them, and a shared address is never a Match Key.
    pool = [jane("a", ROLE), zed("h"), zed("g")]
    clusters = cluster_contributions(pool)
    assert partition(pool) == {frozenset("a"), frozenset("gh")}
    for cluster in clusters:
        assert MatchKeyKind.VERIFIED_EMAIL not in cluster.merged_by


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_verified_address_and_a_different_guess_are_one_lead_end_to_end() -> None:
    for status in (U, A):
        pool = [jane("b", E1), jane("a", E2, status)]
        assert_same_in_every_order(pool, {frozenset("ab")})
        assert DisqualifiedAddresses.from_contributions(pool).name_domains == (
            frozenset()
        )
        (lead,) = leads(pool)
        assert str(lead.email) == E1
        assert lead.email_status is V


# Verifies: specs/lead-source-adapters/requirements.md#8.3
@PROJECTION_RANKS_BEFORE_VERIFICATION
def test_the_guess_never_becomes_the_email_even_from_a_higher_ranked_source() -> None:
    pool = [jane("a", E1), jane("g", E2, U)]
    (lead,) = leads(pool)
    assert str(lead.email) == E1
    assert lead.email_status is V


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_guesses_are_ignored_beside_one_verified_address() -> None:
    pool = [jane("a", E1), jane("b", E2, U), jane("c", E3, A), jane("d")]
    assert_same_in_every_order(pool, {frozenset("abcd")})


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_no_guess_or_role_address_bridges_two_verified_addresses() -> None:
    for middle in (jane("c", E3, U), jane("c", ROLE), jane("c", E1, U)):
        pool = [jane("a", E1), middle, jane("b", E2), zed()]
        assert_same_in_every_order(
            pool, {frozenset("a"), frozenset("b"), frozenset("c"), frozenset("h")}
        )
        projected = leads(pool)
        assert len(projected) == 4


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_a_guess_cannot_bridge_two_verified_addresses_across_domains() -> None:
    pool = [
        jane("a", E1, domain="acme.com"),
        jane("c", E3, U, domain=["acme.com", "acme.io"]),
        jane("b", E2, domain="acme.io"),
    ]
    assert DisqualifiedAddresses.from_contributions(pool).name_domains == frozenset()
    assert_same_in_every_order(pool, {frozenset("a"), frozenset("b"), frozenset("c")})


# Verifies: specs/lead-source-adapters/requirements.md#8.3
# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_every_permutation_of_small_pools_with_roles_and_guesses_agrees() -> None:
    # Property-style: 3-5 Jane records drawing verified, guessed and role addresses
    # over two domains, plus the Zed record that makes ROLE shared.
    emails: list[tuple[str | None, EmailStatus]] = [
        (None, V),
        (E1, V),
        (E2, V),
        (E2, U),
        (E3, A),
        (ROLE, V),
        (ROLE, U),
    ]
    domains: list[str | list[str]] = ["acme.com", "acme.io", ["acme.com", "acme.io"]]
    for seed in range(40):
        rng = random.Random(seed)
        pool = [zed()]
        for source in "abcd"[: rng.randint(2, 4)]:
            email, status = rng.choice(emails)
            pool.append(jane(source, email, status, rng.choice(domains)))
        reference = serialise(cluster_contributions(pool))
        disqualified = DisqualifiedAddresses.from_contributions(pool)
        for perm in itertools.permutations(pool):
            order = list(perm)
            assert serialise(cluster_contributions(order)) == reference
            assert DisqualifiedAddresses.from_contributions(order) == disqualified
        for cluster in cluster_contributions(pool):
            assert_one_personal_email(cluster.contributions, pool)
