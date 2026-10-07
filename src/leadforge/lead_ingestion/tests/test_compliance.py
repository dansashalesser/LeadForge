"""Suppression propagation onto the canonical compliance flags (task 19.3; 11.4).

A provider's Suppression reaches ``CanonicalLead.suppressed`` / ``opt_out`` through
clustering and projection whatever the source's trust rank, arrival order, conflicts or
supersession; a flag is only ever added, never cleared; a value that cannot be read as
a flag fails CLOSED; the Lead stays a Lead; no personal value reaches an error or log.
"""

import itertools
import random
from typing import Any

import pytest
import structlog

from leadforge.lead_ingestion.clustering import IdentityCluster
from leadforge.lead_ingestion.compliance import (
    blocked_identities,
    identities,
    is_flag_set,
)
from leadforge.lead_ingestion.conflicts import ConflictRule
from leadforge.lead_ingestion.orchestrator import prune_flagged
from leadforge.lead_ingestion.projection import project_lead
from leadforge.lead_ingestion.tests.test_orchestrator_enrichment_order import (
    contribution,
)
from leadforge.lead_ingestion.tests.test_projection import (
    RANKS,
    cluster,
    contrib,
    project,
    untrusted,
)

ADA = "ada@example.com"


SET_VALUES: list[object] = [
    True,
    1,
    2,
    -1,
    1.0,
    float("nan"),
    "true",
    "True",
    " TRUE ",
    "yes",
    "1",
    "maybe",
    "suppressed",
    ["x"],
    ("x",),
    {"a": 1},
    object(),
    untrusted("opted out"),
]
ABSENT_VALUES: list[object] = [
    None,
    False,
    0,
    0.0,
    "",
    "   ",
    "false",
    "False",
    " FALSE ",
    "no",
    "No",
    "n",
    "f",
    "0",
    "off",
    [],
    (),
    {},
    set(),
    b"",
    untrusted("false"),
    untrusted(""),
]


# Verifies: specs/lead-source-adapters/requirements.md#11.4
@pytest.mark.parametrize("value", SET_VALUES, ids=repr)
def test_a_value_that_says_or_may_say_yes_is_a_set_flag(value: object) -> None:
    assert is_flag_set(value) is True


# Verifies: specs/lead-source-adapters/requirements.md#11.4
@pytest.mark.parametrize("value", ABSENT_VALUES, ids=repr)
def test_a_value_that_says_no_or_nothing_is_not_a_flag(value: object) -> None:
    assert is_flag_set(value) is False


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_a_hostile_value_cannot_crash_the_reading() -> None:
    class Hostile:
        def __bool__(self) -> bool:
            raise RuntimeError("no")

        def __str__(self) -> str:
            raise RuntimeError("no")

        def __eq__(self, other: object) -> bool:
            raise RuntimeError("no")

        __hash__ = None  # type: ignore[assignment]

    assert is_flag_set(Hostile()) is True
    assert identities(contribution("a", {"person.email": Hostile()})) == frozenset()


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_an_unparseable_linkedin_url_names_no_one_and_does_not_raise() -> None:
    report = contribution("a", {"linkedin_url": "//[bad", "suppressed": True})
    assert identities(report) == frozenset()
    assert prune_flagged((report,)) == ()  # still flagged itself


# Verifies: specs/lead-source-adapters/requirements.md#11.4
@pytest.mark.parametrize("flag", ["suppressed", "opt_out"])
@pytest.mark.parametrize("value", ["true", 1, "yes", "maybe"])
def test_projection_treats_an_unparseable_flag_as_set(flag: str, value: object) -> None:
    out = project(contrib("a", {"person.email": ADA, flag: value}))
    assert out.lead is not None
    assert getattr(out.lead, flag) is True
    assert getattr(out, flag) is True


# Verifies: specs/lead-source-adapters/requirements.md#6.10
def test_pruning_treats_an_unparseable_flag_as_set() -> None:
    work = (contribution("a", {"person.email": ADA}),)
    report = contribution("x", {"person.email": ADA, "suppressed": "true"})
    assert prune_flagged(work, (report,)) == ()
    not_a_flag = contribution("y", {"person.email": ADA, "suppressed": "False"})
    assert prune_flagged(work, (not_a_flag,)) == work
    assert prune_flagged((report,)) == ()


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_a_losing_lower_trust_flag_still_counts_in_every_rank_and_order() -> None:
    members = [
        contrib("a", {"person.email": ADA, "suppressed": False, "opt_out": False}),
        contrib("b", {"person.email": ADA, "suppressed": True}),
        contrib("c", {"person.email": ADA, "opt_out": True}),
    ]
    for ranks in itertools.permutations([1, 3, 5]):
        rank_map = dict(zip("abc", ranks, strict=True))
        for order in itertools.permutations(members):
            out = project(*order, ranks=rank_map)
            assert out.lead is not None
            assert (out.lead.suppressed, out.lead.opt_out) == (True, True)


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_a_later_source_saying_false_never_clears_a_flag() -> None:
    flagged = contrib("c", {"person.email": ADA, "suppressed": True})
    clear = contrib("a", {"person.email": ADA, "suppressed": False})
    for order in ((flagged, clear), (clear, flagged)):
        out = project(*order)
        assert out.lead is not None
        assert out.lead.suppressed is True


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_the_flagged_lead_stays_a_lead_with_its_identity() -> None:
    out = project(
        contrib("a", {"person.email": ADA, "person.full_name": untrusted("Ada L")}),
        contrib("c", {"person.email": ADA, "suppressed": True}),
    )
    assert out.lead is not None
    assert str(out.lead.email) == ADA
    assert out.lead.suppressed is True


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_a_suppression_is_not_reported_as_a_rank_resolved_conflict() -> None:
    out = project(
        contrib("a", {"person.email": ADA, "suppressed": False}),
        contrib("c", {"person.email": ADA, "suppressed": True}),
    )
    assert "suppressed" not in {c.canonical_path for c in out.conflicts}
    assert ConflictRule.TRUST_RANK not in {c.decided_by for c in out.conflicts}


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_provenance_names_the_flagging_source_as_the_winner_of_the_flag() -> None:
    out = project(
        contrib("a", {"person.email": ADA, "suppressed": False}),
        contrib("c", {"person.email": ADA, "suppressed": True}),
    )
    flag_records = [p for p in out.provenance if p.canonical_path == "suppressed"]
    assert {p.source_name for p in flag_records} == {"a", "c"}
    winner, loser = flag_records  # winner first, as persisted
    assert (winner.source_name, winner.superseded) == ("c", False)
    assert (loser.source_name, loser.superseded) == ("a", True)
    assert out.compliance_sources == ("c",)


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_compliance_sources_name_every_source_that_set_a_flag() -> None:
    out = project(
        contrib("a", {"person.email": ADA}),
        contrib("b", {"person.email": ADA, "opt_out": True}),
        contrib("c", {"person.email": ADA, "suppressed": True}),
    )
    assert out.compliance_sources == ("b", "c")
    clean = project(contrib("a", {"person.email": ADA, "suppressed": False}))
    assert clean.compliance_sources == ()


# Verifies: specs/lead-source-adapters/requirements.md#11.4 (property)
def test_random_flag_combinations_never_clear_a_flag_any_source_set() -> None:
    rng = random.Random(1905)
    domain: list[Any] = [True, False, None, "true", 1, "false", 0, ""]
    for _ in range(300):
        members = []
        for index in range(rng.randint(1, 5)):
            values: dict[str, Any] = {"person.email": ADA}
            for flag in ("suppressed", "opt_out"):
                value = rng.choice(domain)
                if value is not None:
                    values[flag] = value
            members.append(contrib(f"s{index}", values))
        ranks = {m.source_name: rng.randint(1, 9) for m in members}
        rng.shuffle(members)
        out = project_lead(cluster(*members), ranks)
        assert out.lead is not None
        for flag in ("suppressed", "opt_out"):
            expected = any(is_flag_set(m.values.get(flag)) for m in members)
            assert getattr(out.lead, flag) is expected
            assert getattr(out, flag) is expected


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_a_report_blocks_every_identity_spelling_it_carries() -> None:
    report = contribution(
        "crm",
        {
            "email": " Ada@Example.com ",
            "person.linkedin_url": "https://www.linkedin.com/in/Ada/?x=1",
            "opt_out": True,
            "suppressed": True,
        },
    )
    blocked = blocked_identities([report])
    assert set(blocked) == set(identities(report))
    assert len(blocked) == 2
    for pairs in blocked.values():
        assert pairs == {("opt_out", "crm"), ("suppressed", "crm")}


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_an_unflagged_or_unidentified_report_blocks_nothing() -> None:
    assert not blocked_identities([contribution("a", {"person.email": ADA})])
    assert not blocked_identities(
        [contribution("a", {"suppressed": True, "person.full_name": "Ada"})]
    )
    assert not blocked_identities([contribution("a", {"email": "  ", "opt_out": True})])


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_a_flag_reported_in_another_cluster_for_the_same_address_reaches_the_lead() -> (
    None
):
    lead = contrib(
        "discovery", {"person.email": ADA, "person.full_name": untrusted("Ada")}
    )
    report = contrib("crm", {"email": ADA.upper(), "opt_out": True, "suppressed": True})
    blocked = blocked_identities([lead, report])
    out = project_lead(cluster(lead), RANKS, blocked=blocked)
    assert out.lead is not None
    assert (out.lead.suppressed, out.lead.opt_out) == (True, True)
    assert out.compliance_sources == ("crm",)
    # Without the blocked set the Lead is not flagged: it is the carrier.
    bare = project_lead(cluster(lead), RANKS).lead
    assert bare is not None
    assert bare.suppressed is False


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_a_flag_for_another_address_does_not_reach_the_lead() -> None:
    lead = contrib("discovery", {"person.email": ADA})
    report = contrib("crm", {"email": "bob@example.com", "suppressed": True})
    out = project_lead(cluster(lead), RANKS, blocked=blocked_identities([report]))
    assert out.lead is not None
    assert out.lead.suppressed is False
    assert out.compliance_sources == ()


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_the_linkedin_form_reaches_a_lead_that_has_no_email() -> None:
    lead = contrib(
        "discovery",
        {
            "person.linkedin_url": "https://linkedin.com/in/ada",
            "person.title": untrusted("VP"),
        },
    )
    report = contribution(
        "crm", {"linkedin_url": "http://linkedin.com/in/ada/", "suppressed": True}
    )
    out = project_lead(cluster(lead), RANKS, blocked=blocked_identities([report]))
    assert out.lead is not None
    assert out.lead.suppressed is True


# Verifies: specs/lead-source-adapters/requirements.md#11.4 (property)
def test_identity_propagation_ignores_arrival_order() -> None:
    lead = contrib("discovery", {"person.email": ADA})
    report = contrib("finder", {"person.email": ADA, "suppressed": True})
    for order in itertools.permutations([lead, report]):
        blocked = blocked_identities(order)
        out = project_lead(IdentityCluster("c", (lead,)), RANKS, blocked=blocked)
        assert out.lead is not None
        assert out.lead.suppressed is True


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_no_personal_value_reaches_a_log_or_the_result_repr() -> None:
    canary = "canary.person@canary-example.org"
    with structlog.testing.capture_logs() as logs:
        lead = contrib("discovery", {"person.email": canary})
        report = contrib("finder", {"person.email": canary, "suppressed": "true"})
        blocked = blocked_identities([lead, report])
        out = project_lead(cluster(lead), RANKS, blocked=blocked)
        prune_flagged((lead,), (report,))
    assert canary not in str(logs)
    assert canary not in repr(out)


# Verifies: specs/lead-source-adapters/requirements.md#6.10
@pytest.mark.parametrize("blank", ["", "   ", None, 0, "@", "nobody"])
def test_a_blank_identity_never_blocks_every_lead(blank: object) -> None:
    work = (
        contribution("a", {"person.email": ADA}),
        contribution("b", {"person.email": blank}),
        contribution("c", {"person.full_name": "No Identity"}),
    )
    report = contribution("x", {"person.email": blank, "email": blank, "opt_out": True})
    assert prune_flagged(work, (report,)) == work


# Verifies: specs/lead-source-adapters/requirements.md#6.10
def test_a_blocked_email_prunes_a_lead_that_has_another_linkedin() -> None:
    work = (
        contribution(
            "a",
            {"person.email": ADA, "person.linkedin_url": "https://linkedin.com/in/x"},
        ),
        contribution("b", {"person.email": "bob@example.com"}),
    )
    report = contribution("x", {"email": f" {ADA.upper()} ", "suppressed": True})
    assert prune_flagged(work, (report,)) == (work[1],)


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_a_cluster_with_no_lead_still_carries_the_flag_and_its_source() -> None:
    out = project(contrib("crm", {"suppressed": True, "opt_out": True}))
    assert out.lead is None
    assert (out.suppressed, out.opt_out) == (True, True)
    assert out.compliance_sources == ("crm",)


# Verifies: specs/lead-source-adapters/requirements.md#11.4
def test_a_flagged_lead_with_only_a_name_is_still_a_lead() -> None:
    out = project(
        contrib("a", {"person.full_name": untrusted("Ada L"), "suppressed": True})
    )
    assert out.lead is not None
    assert out.lead.email is None
    assert out.lead.suppressed is True


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_the_projection_with_flags_ignores_contribution_order() -> None:
    members = [
        contrib("a", {"person.email": ADA, "suppressed": False}),
        contrib("b", {"person.email": ADA, "suppressed": True}),
        contrib("c", {"person.email": ADA, "opt_out": "yes", "suppressed": "true"}),
    ]
    outs = [
        project_lead(cluster(*order), RANKS)
        for order in itertools.permutations(members)
    ]
    assert all(o == outs[0] for o in outs)
