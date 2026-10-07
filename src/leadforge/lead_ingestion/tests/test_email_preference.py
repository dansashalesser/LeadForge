"""The Lead's email: verified first, then personal, then 8.4 (user decision 2026-10-06).

User decision (2026-10-06, verbatim): "a verified email should beat an unverified one";
"email is the most relevant item so it should beat info@". For ``person.email`` only,
the winner is chosen by (1) a VERIFIED status over any other, (2) a personal address
over a role address (``info@`` or another role word, or one the 8.14 pass finds
shared), then (3) the 8.4 total order. A role address is never discarded: it stays on
the Lead as a company contact, and when a person has only role addresses it is their
email, flagged. Every other field keeps 8.4 unchanged. Proven through the real merge:
clustering, then projection.
"""

import itertools
import random
from typing import Any

import pytest

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import IdentityCluster, cluster_contributions
from leadforge.lead_ingestion.conflicts import ConflictRule, resolve_conflicts
from leadforge.lead_ingestion.models import (
    REQUEST_ECHO_PREFIX,
    CanonicalLead,
    EmailStatus,
)
from leadforge.lead_ingestion.projection import (
    PROJECTION_RULES_REVISION,
    ProjectionBasis,
    ProjectionResult,
    project_lead,
)
from leadforge.lead_ingestion.remerge import identify
from leadforge.lead_ingestion.tests.test_one_sided_email_name_domain import (
    E1,
    E2,
    ROLE,
    A,
    U,
    V,
    contribution,
    jane,
    zed,
)

# The verifier ranks below the enricher: its verified address must still win.
RANKS = {"enricher": 9, "crm": 5, "verifier": 2, "h": 1}
SALES = "sales@acme.com"


def merge(pool: list[LeadContribution]) -> list[ProjectionResult]:
    """The real merge: cluster the pool, project every cluster."""
    return [project_lead(c, RANKS) for c in cluster_contributions(pool)]


def jane_result(pool: list[LeadContribution]) -> ProjectionResult:
    (result,) = [
        r for r in merge(pool) if r.lead is not None and r.lead.full_name == "Jane Doe"
    ]
    return result


def jane_lead(pool: list[LeadContribution]) -> CanonicalLead:
    lead = jane_result(pool).lead
    assert lead is not None
    return lead


def email_rule(result: ProjectionResult) -> ConflictRule | None:
    rules = {c.canonical_path: c.decided_by for c in result.conflicts}
    return rules.get("person.email")


def assert_same_leads_in_every_order(pool: list[LeadContribution]) -> None:
    reference = [r.lead for r in merge(pool)]
    for perm in itertools.permutations(pool):
        assert [r.lead for r in merge(list(perm))] == reference


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_a_lower_ranked_verified_address_beats_a_higher_ranked_unverified_guess() -> (
    None
):
    pool = [jane("verifier", E1, V), jane("enricher", E2, U)]
    result = jane_result(pool)
    assert result.lead is not None
    assert str(result.lead.email) == E1
    assert result.lead.email_status is V
    assert email_rule(result) is ConflictRule.VERIFIED_EMAIL
    assert result.lead.email_is_role_address is False
    assert_same_leads_in_every_order(pool)


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_verified_beats_every_other_status_from_a_higher_rank() -> None:
    for status in (U, A, EmailStatus.UNKNOWN, EmailStatus.INVALID):
        lead = jane_lead([jane("verifier", E1, V), jane("enricher", E2, status)])
        assert str(lead.email) == E1


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_a_personal_address_beats_info_from_a_higher_rank_and_info_is_kept() -> None:
    pool = [jane("enricher", ROLE), jane("verifier", E1), zed()]
    result = jane_result(pool)
    assert result.lead is not None
    assert str(result.lead.email) == E1
    assert result.lead.email_is_role_address is False
    assert result.lead.role_contact_emails == (ROLE,)
    assert email_rule(result) is ConflictRule.PERSONAL_EMAIL
    # The role address is superseded, not discarded: its provenance stays too.
    assert any(
        p.canonical_path == "person.email" and p.source_name == "enricher"
        for p in result.provenance
    )
    assert_same_leads_in_every_order(pool)


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_a_person_with_only_info_keeps_it_as_their_email_flagged() -> None:
    pool = [jane("enricher", ROLE), zed()]
    lead = jane_lead(pool)
    assert str(lead.email) == ROLE
    assert lead.email_is_role_address is True
    assert lead.role_contact_emails == ()
    assert_same_leads_in_every_order(pool)


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_two_role_addresses_one_is_the_email_the_other_a_company_contact() -> None:
    pool = [
        jane("enricher", ROLE),
        jane("verifier", SALES, U),
        zed(),
        zed("crm").model_copy(
            update={"values": {**zed().values, "person.email": SALES}}
        ),
    ]
    lead = jane_lead(pool)
    assert str(lead.email) == ROLE  # verified beats unverified, both are roles
    assert lead.email_is_role_address is True
    assert lead.role_contact_emails == (SALES,)


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_verification_comes_before_role_a_verified_info_beats_a_guess() -> None:
    # The decision's order, literally: (1) verified, then (2) personal.
    pool = [jane("verifier", ROLE), jane("enricher", E1, U), zed()]
    lead = jane_lead(pool)
    assert str(lead.email) == ROLE
    assert lead.email_is_role_address is True


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_same_address_verified_by_a_lower_rank_reports_verified() -> None:
    result = jane_result([jane("enricher", E1, U), jane("verifier", E1, V)])
    assert result.lead is not None
    assert str(result.lead.email) == E1
    assert result.lead.email_status is V
    assert dict(result.agreement)["person.email"] == 2


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_the_status_is_the_one_stated_with_the_chosen_address_in_one_source() -> None:
    # One source, two records: its info@ status never labels the personal address.
    statuses = (U, A, EmailStatus.UNKNOWN, EmailStatus.INVALID)
    for own, other in itertools.permutations(statuses, 2):
        pool = [jane("enricher", E1, own), jane("enricher", ROLE, other)]
        lead = jane_lead(pool)
        assert str(lead.email) == E1
        assert lead.email_status is own
        assert_same_leads_in_every_order(pool)


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_trust_rank_still_decides_between_two_verified_personal_addresses() -> None:
    # Direct clustering would split two verified addresses; a hand-built cluster
    # isolates the order.
    cluster = IdentityCluster("c", (jane("verifier", E1), jane("enricher", E2)))
    resolution = resolve_conflicts(cluster, RANKS)
    email = resolution.field("person.email")
    assert email.winner.source_name == "enricher"
    assert email.decided_by is ConflictRule.TRUST_RANK


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_every_other_field_keeps_the_trust_rank_order() -> None:
    pool = [
        jane("verifier", E1, V, person__title="Engineer", company__name="Acme"),
        jane("enricher", E2, U, person__title="CTO", company__name="Acme"),
    ]
    result = jane_result(pool)
    assert result.lead is not None
    assert result.lead.employments[0].title == "CTO"
    rules = {c.canonical_path: c.decided_by for c in result.conflicts}
    assert rules["person.title"] is ConflictRule.TRUST_RANK
    assert rules["person.email_status"] is ConflictRule.TRUST_RANK


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_a_lone_info_under_one_name_is_a_role_address_by_its_local_part() -> None:
    # No second name makes info@ shared: the role-word list alone flags it.
    pool = [jane("enricher", ROLE), jane("verifier", E1)]
    result = jane_result(pool)
    assert result.lead is not None
    assert str(result.lead.email) == E1
    assert result.lead.role_contact_emails == (ROLE,)
    assert email_rule(result) is ConflictRule.PERSONAL_EMAIL
    assert_same_leads_in_every_order(pool)


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_role_word_guess_does_not_block_a_one_sided_join() -> None:
    # Two different unverified guesses would split; info@ is no personal evidence.
    pool = [jane("enricher", ROLE, U), jane("verifier", E1, U)]
    (cluster,) = cluster_contributions(pool)
    assert cluster.role_addresses == frozenset({ROLE})
    lead = jane_lead(pool)
    assert str(lead.email) == E1
    assert lead.email_status is U
    assert lead.role_contact_emails == (ROLE,)
    assert_same_leads_in_every_order(pool)


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_only_a_lone_info_is_the_email_flagged() -> None:
    lead = jane_lead([jane("enricher", ROLE, U)])
    assert str(lead.email) == ROLE
    assert lead.email_is_role_address is True


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_a_role_address_joined_by_provider_id_stays_a_role_address() -> None:
    keyed = jane("enricher", E1, U, person__provider_id="p1")
    # No name and an unverified address: keyless, joined to the keyed record by
    # source + provider id (remerge.identify).
    loose = contribution(
        "enricher",
        person__email=ROLE,
        person__email_status=U,
        person__provider_id="p1",
    )
    for pool in ([keyed, loose], [loose, keyed]):
        (cluster,) = identify(pool, None)
        assert cluster.role_addresses == frozenset({ROLE})
        lead = project_lead(cluster, RANKS).lead
        assert lead is not None
        assert str(lead.email) == E1
        assert lead.role_contact_emails == (ROLE,)


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_an_echoed_verified_address_neither_wins_nor_adds_agreement() -> None:
    echo = jane("verifier", E2, V).model_copy(
        update={
            "provenance": tuple(
                p.model_copy(update={"raw_field_path": f"{REQUEST_ECHO_PREFIX}email"})
                if p.canonical_path == "person.email"
                else p
                for p in jane("verifier", E2, V).provenance
            )
        }
    )
    cluster = IdentityCluster("c", (echo, jane("enricher", E1, U)))
    result = project_lead(cluster, RANKS)
    assert result.lead is not None
    assert str(result.lead.email) == E1
    assert dict(result.agreement)["person.email"] == 1
    alone = project_lead(IdentityCluster("c", (echo,)), RANKS).lead
    assert alone is not None
    assert str(alone.email) == E2


# Verifies: specs/lead-source-adapters/requirements.md#8.8 (property)
def test_every_permutation_of_mixed_email_pools_gives_one_projection() -> None:
    choices: list[tuple[str, EmailStatus]] = [
        (E1, V),
        (E1, U),
        (E2, U),
        (E2, A),
        (ROLE, V),
        (ROLE, U),
        (SALES, V),
    ]
    sources = ["enricher", "crm", "verifier"]
    for seed in range(40):
        rng = random.Random(seed)
        pool = [zed(), zed("crm2").model_copy(update={"values": _zed_sales()})]
        for source in sources[: rng.randint(1, 3)]:
            email, status = rng.choice(choices)
            pool.append(jane(source, email, status))
        reference = [(r.lead, r.agreement, r.conflicts) for r in merge(pool)]
        for perm in itertools.permutations(pool):
            assert [
                (r.lead, r.agreement, r.conflicts) for r in merge(list(perm))
            ] == reference
        for result in merge(pool):
            lead = result.lead
            assert lead is not None
            assert str(lead.email) not in {str(c) for c in lead.role_contact_emails}


def _zed_sales() -> dict[str, Any]:
    return {**zed().values, "person.email": SALES}


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_a_role_flag_needs_an_email() -> None:
    with pytest.raises(ValueError, match="role address given without email"):
        CanonicalLead(full_name="Jane Doe", email_is_role_address=True)


# Verifies: specs/lead-source-adapters/requirements.md#8.13
def test_the_email_rule_bumps_the_rules_revision_so_stored_leads_recompute() -> None:
    assert PROJECTION_RULES_REVISION == 4
    before = ProjectionBasis.of(None, RANKS, rules_revision=3)
    after = ProjectionBasis.of(None, RANKS)
    assert after.fingerprint != before.fingerprint


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_a_value_that_is_no_address_never_counts_as_verified() -> None:
    cluster = IdentityCluster(
        "c", (jane("enricher", "not-an-address", V), jane("verifier", E1, V))
    )
    lead = project_lead(cluster, RANKS).lead
    assert lead is not None
    assert str(lead.email) == E1


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_a_record_stating_the_address_without_a_status_defers_to_one_with_it() -> None:
    bare = contribution(
        "enricher",
        person__full_name="Jane Doe",
        company__domain="acme.com",
        person__title="CTO",
        person__email=E1,
    )
    pool = [bare, jane("crm", E1, U)]
    lead = jane_lead(pool)
    assert str(lead.email) == E1
    assert lead.email_status is U
    assert_same_leads_in_every_order(pool)
