"""Opt-outs follow strong identity links (user decision 2026-10-06, ADR-0006 amended).

User decision (2026-10-06): suppression pruning also prunes a person's OTHER records
linked by LinkedIn URL or VERIFIED email (the Match Key normalizers), transitively
within the work list, but never through a shared or role address (8.14's structural
detection) nor through name+domain. Deterministic, order-independent, near-linear.
"""

import itertools
import random

import pytest

from leadforge.lead_ingestion import clustering
from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.match_keys import IdentityExclusions
from leadforge.lead_ingestion.orchestrator import prune_flagged
from leadforge.lead_ingestion.tests.test_orchestrator_enrichment_order import (
    contribution,
)

P_EMAIL = "pat@acme.io"
P_LINKEDIN = "https://www.linkedin.com/in/pat-person"
ROLE = "info@acme.io"


def record(source: str, **values: object) -> LeadContribution:
    return contribution(
        source, {key.replace("__", "."): value for key, value in values.items()}
    )


OPT_OUT = record("crm", email=P_EMAIL, opt_out=True)


# Verifies: specs/lead-source-adapters/requirements.md#6.10
def test_a_linkedin_only_record_of_an_opted_out_person_is_pruned() -> None:
    linkedin_only = record("search", person__linkedin_url=P_LINKEDIN)
    # A later tier links the opted-out address to the LinkedIn URL.
    linking = record(
        "finder",
        person__email=P_EMAIL,
        person__email_status="verified",
        person__linkedin_url=P_LINKEDIN,
    )

    assert prune_flagged((linkedin_only, linking), (OPT_OUT,)) == ()


# Verifies: specs/lead-source-adapters/requirements.md#6.10
def test_a_link_is_followed_transitively_through_verified_addresses() -> None:
    # opt-out (email) -> linking (email + LinkedIn) -> second (LinkedIn + verified
    # other address) -> third (that verified address only).
    linking = record("a", person__email=P_EMAIL, person__linkedin_url=P_LINKEDIN)
    second = record(
        "b",
        person__linkedin_url=P_LINKEDIN.upper(),  # same normalised identity
        person__email="pat.home@else.io",
        person__email_status="verified",
    )
    third = record(
        "c", person__email=" Pat.Home@Else.io ", person__email_status="verified"
    )
    other = record(
        "d", person__email="someone@else.io", person__email_status="verified"
    )

    assert prune_flagged((third, other, second, linking), (OPT_OUT,)) == (other,)


# Verifies: specs/lead-source-adapters/requirements.md#6.10
def test_an_unverified_address_is_not_a_strong_link() -> None:
    linking = record(
        "a",
        person__email=P_EMAIL,
        person__linkedin_url=P_LINKEDIN,
    )
    second = record(
        "b",
        person__linkedin_url=P_LINKEDIN,
        person__email="pat.home@else.io",
        person__email_status="accept_all",
    )
    weakly_linked = record(
        "c", person__email="pat.home@else.io", person__email_status="accept_all"
    )

    assert prune_flagged((linking, second, weakly_linked), (OPT_OUT,)) == (
        weakly_linked,
    )


# Verifies: specs/lead-source-adapters/requirements.md#6.10
# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_shared_role_address_never_carries_an_opt_out_to_another_person() -> None:
    # Pat holds info@ too (with his LinkedIn); Quinn, a different person, holds it.
    pats_role = record(
        "a",
        person__email=ROLE,
        person__email_status="verified",
        person__full_name="Pat Person",
        person__linkedin_url=P_LINKEDIN,
    )
    quinn = record(
        "b",
        person__email=ROLE,
        person__email_status="verified",
        person__full_name="Quinn Other",
    )
    linking = record("c", person__email=P_EMAIL, person__linkedin_url=P_LINKEDIN)

    assert prune_flagged((pats_role, quinn, linking), (OPT_OUT,)) == (quinn,)


# Verifies: specs/lead-source-adapters/requirements.md#6.10
# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_name_and_domain_never_carry_an_opt_out() -> None:
    flagged = record(
        "crm",
        person__email=P_EMAIL,
        person__email_status="verified",
        person__full_name="Pat Person",
        person__title="CTO",
        company__domain="acme.io",
        opt_out=True,
    )
    namesake = record(
        "search",
        person__full_name="Pat Person",
        person__title="CTO",
        company__domain="acme.io",
    )

    assert prune_flagged((namesake,), (flagged,)) == (namesake,)


# Verifies: specs/lead-source-adapters/requirements.md#6.10
def test_a_flagged_work_list_record_prunes_its_linked_records_with_no_reports() -> None:
    flagged = record("search", person__linkedin_url=P_LINKEDIN, suppressed=True)
    linked = record(
        "search",
        person__linkedin_url=P_LINKEDIN,
        person__email=P_EMAIL,
        person__email_status="verified",
    )
    by_email = record("search", person__email=P_EMAIL, person__email_status="verified")
    unrelated = record("search", person__email="x@y.io")

    assert prune_flagged((flagged, linked, by_email, unrelated)) == (unrelated,)


MAILBOX = "family@home.io"


# Verifies: specs/lead-source-adapters/requirements.md#6.10
# Verifies: specs/lead-source-adapters/requirements.md#8.14
@pytest.mark.parametrize(
    ("pats_marks", "other_marks"),
    [
        # Seen under two names: the merge bars the address (8.14), so must pruning.
        ({"person__full_name": "Pat Person"}, {"person__full_name": "Sam Other"}),
        # Seen with two LinkedIn URLs and no names: likewise barred.
        ({}, {"person__linkedin_url": "https://www.linkedin.com/in/sam-other"}),
    ],
)
def test_a_personal_mailbox_two_people_share_carries_no_opt_out_as_in_the_merge(
    pats_marks: dict[str, str], other_marks: dict[str, str]
) -> None:
    def holder(source: str, **marks: str) -> LeadContribution:
        return record(
            source, person__email=MAILBOX, person__email_status="verified", **marks
        )

    pats = holder("a", person__linkedin_url=P_LINKEDIN, **pats_marks)
    other = holder("b", **other_marks)
    linking = record("c", person__email=P_EMAIL, person__linkedin_url=P_LINKEDIN)

    # The merge keeps the two holders apart; pruning follows the same exclusion.
    assert len(clustering.cluster_contributions((pats, other))) == 2
    assert prune_flagged((pats, other, linking), (OPT_OUT,)) == (other,)


# Verifies: specs/lead-source-adapters/requirements.md#6.10 (property, exhaustive)
def test_pruning_agrees_over_every_arrival_order_of_a_small_set() -> None:
    linked = record("a", person__email=P_EMAIL, person__linkedin_url=P_LINKEDIN)
    by_linkedin = record(
        "b",
        person__linkedin_url=P_LINKEDIN,
        person__email="pat.home@else.io",
        person__email_status="verified",
    )
    by_address = record(
        "c", person__email="pat.home@else.io", person__email_status="verified"
    )
    role = record(
        "d",
        person__email=ROLE,
        person__email_status="verified",
        person__full_name="Pat Person",
        person__linkedin_url=P_LINKEDIN,
    )
    quinn = record(
        "e",
        person__email=ROLE,
        person__email_status="verified",
        person__full_name="Quinn Other",
    )
    flagged = record(
        "f", person__linkedin_url="https://linkedin.com/in/x", opt_out=True
    )
    work = (linked, by_linkedin, by_address, role, quinn, flagged)

    for order in itertools.permutations(work):
        kept = prune_flagged(order, (OPT_OUT,))
        assert kept == (quinn,)


def _population() -> tuple[tuple[LeadContribution, ...], tuple[LeadContribution, ...]]:
    people = []
    for i in range(12):
        linkedin = f"https://linkedin.com/in/p{i}"
        address = f"p{i}@acme.io"
        people += [
            record("a", person__linkedin_url=linkedin, person__full_name=f"P {i}"),
            record(
                "b",
                person__linkedin_url=linkedin,
                person__email=address,
                person__email_status="verified",
                person__full_name=f"P {i}",
            ),
            record("c", person__email=address, person__email_status="verified"),
            record(
                "d",
                person__email=ROLE,
                person__email_status="verified",
                person__full_name=f"P {i}",
                **({"person__linkedin_url": linkedin} if i % 2 else {}),
            ),
        ]
    reports = tuple(
        record("crm", email=f"p{i}@acme.io", opt_out=True) for i in range(0, 12, 3)
    )
    return tuple(people), reports


# Verifies: specs/lead-source-adapters/requirements.md#6.10 (property)
@pytest.mark.parametrize("seed", range(25))
def test_pruning_does_not_depend_on_arrival_order(seed: int) -> None:
    people, reports = _population()
    expected = prune_flagged(people, reports)
    shuffled_people, shuffled_reports = list(people), list(reports)
    rng = random.Random(seed)
    rng.shuffle(shuffled_people)
    rng.shuffle(shuffled_reports)

    kept = prune_flagged(tuple(shuffled_people), tuple(shuffled_reports))

    assert {id(c) for c in kept} == {id(c) for c in expected}
    # Work-list order is kept.
    assert list(kept) == [c for c in shuffled_people if any(c is k for k in kept)]
    # The rule's answer: persons 0, 3, 6, 9 lose their three linked records; only
    # 3 and 9 (odd) lose their info@ record too, which carries their LinkedIn URL.
    assert len(expected) == len(people) - 4 * 3 - 2


# Verifies: specs/lead-source-adapters/requirements.md#6.10
def test_pruning_is_near_linear_in_the_work_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}
    real_union, real_find = clustering._UnionFind.union, clustering._UnionFind.find

    def counting_union(self: object, *args: object, **kwargs: object) -> None:
        calls["n"] += 1
        real_union(self, *args, **kwargs)  # type: ignore[arg-type]

    def counting_find(self: object, item: int) -> int:
        calls["n"] += 1
        return real_find(self, item)  # type: ignore[arg-type]

    monkeypatch.setattr(clustering._UnionFind, "union", counting_union)
    monkeypatch.setattr(clustering._UnionFind, "find", counting_find)
    # One person: a LinkedIn hub holding many verified addresses, each also held by
    # an address-only record; the opt-out names one of those addresses. An address
    # seen with two LinkedIn URLs is shared (8.14), so a hub is the longest chain.
    size = 1500
    hub = "https://linkedin.com/in/hub"
    chain = tuple(
        record(
            "x",
            person__email=f"n{i // 2}@acme.io",
            person__email_status="verified",
            **({"person__linkedin_url": hub} if i % 2 else {}),
        )
        for i in range(2 * size)
    )
    flagged = record("crm", email=f"n{size - 1}@acme.io", opt_out=True)

    assert prune_flagged(chain, (flagged,)) == ()
    assert calls["n"] <= 12 * len(chain)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
# Verifies: specs/lead-source-adapters/requirements.md#6.10
def test_an_identity_exclusion_links_no_one_when_pruning() -> None:
    # The opted-out address is linked to a LinkedIn URL that an Identity Exclusion
    # bars (8.13: it is no Match Key), so a stranger holding that URL is kept.
    linking = record(
        "finder",
        person__email=P_EMAIL,
        person__email_status="verified",
        person__linkedin_url=P_LINKEDIN,
    )
    stranger = record("search", person__linkedin_url=P_LINKEDIN)
    bar = IdentityExclusions.from_values(linkedin_urls=[P_LINKEDIN])

    assert prune_flagged((stranger, linking), (OPT_OUT,)) == ()
    assert prune_flagged((stranger, linking), (OPT_OUT,), exclusions=bar) == (stranger,)
