"""Non-destructive Lead projection (task 16.5, Requirements 8.12, 8.5-8.8)."""

import itertools
import random
from datetime import UTC, datetime, timedelta
from typing import Any

from leadforge.lead_ingestion.base_source import LeadContribution
from leadforge.lead_ingestion.clustering import IdentityCluster, canonical_json
from leadforge.lead_ingestion.companies import company_id_for
from leadforge.lead_ingestion.conflicts import ConflictRule
from leadforge.lead_ingestion.match_keys import MatchKeyKind
from leadforge.lead_ingestion.models import (
    AbsenceKind,
    ConfidenceOrigin,
    DataMode,
    EmailStatus,
    FieldProvenance,
    IntentSignal,
    SourceAbsence,
    TechSignal,
    UntrustedText,
)
from leadforge.lead_ingestion.projection import (
    ProjectionResult,
    ResolvedConflict,
    project_lead,
)

NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
RANKS = {"a": 5, "b": 3, "c": 1}


def contrib(
    source: str,
    values: dict[str, Any],
    *,
    at: datetime = NOW,
    absences: tuple[SourceAbsence, ...] = (),
) -> LeadContribution:
    records = tuple(
        FieldProvenance(
            canonical_path=path,
            source_name=source,
            data_mode=DataMode.SYNTHETIC,
            fetched_at=at,
            raw_field_path="raw",
            confidence_origin=ConfidenceOrigin.NONE,
            untrusted=isinstance(value, UntrustedText),
        )
        for path, value in values.items()
    )
    return LeadContribution(
        source_name=source, values=values, provenance=records, absences=absences
    )


def untrusted(text: str) -> UntrustedText:
    return UntrustedText(value=text, truncated=False, original_length=len(text))


def cluster(*members: LeadContribution) -> IdentityCluster:
    return IdentityCluster("cluster", tuple(members))


def project(*members: LeadContribution, ranks: dict[str, int] | None = None):  # type: ignore[no-untyped-def]
    return project_lead(cluster(*members), ranks or RANKS)


def sample() -> list[LeadContribution]:
    return [
        contrib(
            "a",
            {
                "person.email": "ann@x.com",
                "person.email_status": EmailStatus.VERIFIED,
                "person.full_name": "Ann A",
                "person.title": untrusted("CTO"),
                "company.domain": "x.com",
                "company.name": untrusted("X"),
                "opt_out": False,
            },
        ),
        contrib(
            "b",
            {
                "person.email": "ann@other.com",
                "person.linkedin_url": "https://www.linkedin.com/in/ann",
                "person.full_name": "Ann B",
                "suppressed": True,
            },
            at=NOW + timedelta(days=1),
        ),
        contrib("c", {"person.full_name": "A. A", "company.domain": "x.com"}),
        contrib("c", {"person.title": untrusted("Head")}, at=NOW - timedelta(days=1)),
    ]


# Verifies: specs/lead-source-adapters/requirements.md#8.6
def test_winning_values_map_onto_the_lead() -> None:
    out = project(*sample())
    assert out.lead is not None
    assert out.lead.email == "ann@x.com"
    assert out.lead.email_status is EmailStatus.VERIFIED
    assert str(out.lead.linkedin_url) == "https://www.linkedin.com/in/ann"
    assert out.lead.full_name == "Ann A"
    (employment,) = out.lead.employments
    assert employment.title == "CTO"
    assert employment.company.name == "X"
    assert employment.company.domains == ("x.com",)


# Verifies: specs/lead-source-adapters/requirements.md#8.4
def test_changing_trust_ranks_changes_the_winner() -> None:
    out = project(*sample(), ranks={"a": 1, "b": 9, "c": 1})
    assert out.lead is not None
    assert out.lead.email == "ann@other.com"
    assert out.lead.full_name == "Ann B"
    # The status described a's address, not b's, so it is not carried over.
    assert out.lead.email_status is EmailStatus.UNKNOWN


# Verifies: specs/lead-source-adapters/requirements.md#8.6
def test_full_name_is_composed_from_first_and_last_when_absent() -> None:
    out = project(
        contrib(
            "a",
            {
                "person.first_name": untrusted("Ann"),
                "person.last_name": untrusted("Lee"),
            },
        )
    )
    assert out.lead is not None
    assert out.lead.full_name == "Ann Lee"


# Verifies: specs/lead-source-adapters/requirements.md#8.14
def test_a_masked_name_is_not_a_full_name() -> None:
    """a provider obfuscates last names ('Lee' as 'L**'); the match rule already says a
    masked name is not a name, so the Lead must not carry it as full_name either."""
    composed = project(
        contrib(
            "a",
            {
                "person.first_name": untrusted("Ann"),
                "person.last_name": untrusted("L**"),
            },
        )
    )
    stated = project(contrib("a", {"person.full_name": "Ann L**"}))
    assert composed.lead is None
    assert stated.lead is None


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_every_permutation_gives_identical_result() -> None:
    members = sample()
    expected = project(*members)
    assert expected.lead is not None
    for order in itertools.permutations(members):
        got = project(*order)
        assert got == expected
        assert got.lead is not None
        assert got.lead.model_dump_json() == expected.lead.model_dump_json()
        assert [p.model_dump_json() for p in got.provenance] == [
            p.model_dump_json() for p in expected.provenance
        ]


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_seeded_shuffles_of_a_larger_cluster_are_identical() -> None:
    members = [
        *sample(),
        contrib("a", {"person.email": "ann@x.com"}, at=NOW + timedelta(hours=1)),
        contrib("b", {"person.email": "ann@x.com"}),
        contrib("c", {"person.full_name": "Ann A", "opt_out": True}),
    ]
    expected = project(*members)
    for seed in range(200):
        shuffled = members[:]
        random.Random(seed).shuffle(shuffled)
        assert project(*shuffled) == expected


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_projection_is_pure_and_leaves_contributions_untouched() -> None:
    members = sample()
    before = [canonical_json(m) for m in members]
    ranks = dict(RANKS)
    the_cluster = cluster(*members)
    first = project_lead(the_cluster, ranks)
    second = project_lead(the_cluster, ranks)
    assert first == second
    assert [canonical_json(m) for m in the_cluster.contributions] == before
    assert the_cluster.contributions == tuple(members)
    assert ranks == RANKS


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_recompute_after_rank_change_replaces_the_projection_only() -> None:
    members = sample()
    before = [canonical_json(m) for m in members]
    old = project(*members)
    new = project(*members, ranks={"a": 1, "b": 9, "c": 1})
    assert old != new
    assert [canonical_json(m) for m in members] == before
    assert project(*members) == old  # the old ranking still gives the old result


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_result_type_is_the_only_producer_of_a_lead() -> None:
    assert isinstance(project(*sample()), ProjectionResult)


# Verifies: specs/lead-source-adapters/requirements.md#8.5
def test_provenance_marks_losers_superseded_and_none_is_dropped() -> None:
    out = project(*sample())
    names = [p for p in out.provenance if p.canonical_path == "person.email"]
    assert {(p.source_name, p.superseded) for p in names} == {
        ("a", False),
        ("b", True),
    }
    total = sum(len(m.provenance) for m in sample())
    assert len(out.provenance) == total


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_carries_contributing_sources_and_agreement_counts() -> None:
    out = project(
        *sample(),
        contrib("b", {"person.email": "ann@x.com"}),
        contrib("c", {}),
    )
    assert out.contributing_sources == ("a", "b", "c")
    assert dict(out.agreement)["person.email"] == 2
    assert dict(out.agreement)["person.title"] == 1


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_a_contribution_with_no_values_and_no_absences_still_counts() -> None:
    out = project(contrib("a", {"person.full_name": "Ann"}), contrib("zz", {}))
    assert out.contributing_sources == ("a", "zz")


# Verifies: specs/lead-source-adapters/requirements.md#8.6
def test_opt_out_and_suppressed_from_any_source_are_never_lost() -> None:
    out = project(
        contrib(
            "a", {"person.email": "ann@x.com", "opt_out": False, "suppressed": False}
        ),
        contrib("c", {"opt_out": True, "suppressed": True}),
    )
    assert out.lead is not None
    assert out.lead.opt_out is True
    assert out.lead.suppressed is True


# Verifies: specs/lead-source-adapters/requirements.md#8.6
def test_a_suppression_flag_survives_a_higher_trust_source_saying_nothing() -> None:
    out = project(
        contrib("a", {"person.email": "ann@x.com"}),
        contrib("c", {"person.email": "ann@x.com", "suppressed": True}),
    )
    assert out.lead is not None
    assert out.lead.suppressed is True
    assert out.lead.opt_out is False  # suppressed alone does not imply opt-out


# Verifies: specs/lead-source-adapters/requirements.md#8.6
def test_compliance_flags_or_in_every_order() -> None:
    members = [
        contrib("a", {"person.full_name": "Ann", "opt_out": False}),
        contrib("b", {"suppressed": True}),
        contrib("c", {"opt_out": True, "suppressed": False}),
    ]
    for order in itertools.permutations(members):
        out = project(*order)
        assert out.lead is not None
        assert (out.lead.opt_out, out.lead.suppressed) == (True, True)


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_a_cluster_without_person_identity_forms_no_lead_but_keeps_its_flags() -> None:
    out = project(
        contrib("c", {"suppressed": True, "company.domain": "x.com"}),
        contrib("a", {"crm.owner": "z"}),
    )
    assert out.lead is None
    assert out.suppressed is True
    assert out.opt_out is False
    assert out.contributing_sources == ("a", "c")
    assert out.provenance  # still derived, nothing dropped


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_an_empty_cluster_is_a_defined_outcome() -> None:
    out = project_lead(IdentityCluster("empty", ()), RANKS)
    assert out.lead is None
    assert out.contributing_sources == ()
    assert (out.opt_out, out.suppressed) == (False, False)


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_email_status_without_email_resolves_to_unknown_not_an_error() -> None:
    out = project(
        contrib(
            "a", {"person.email_status": EmailStatus.VERIFIED, "person.full_name": "A"}
        )
    )
    assert out.lead is not None
    assert out.lead.email is None
    assert out.lead.email_status is EmailStatus.UNKNOWN


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_status_follows_the_source_that_stated_the_chosen_address() -> None:
    out = project(
        contrib("a", {"person.email": "ann@x.com"}),
        contrib(
            "c",
            {"person.email": "ann@x.com", "person.email_status": EmailStatus.VERIFIED},
        ),
        contrib(
            "b",
            {"person.email": "zed@y.com", "person.email_status": EmailStatus.INVALID},
        ),
    )
    assert out.lead is not None
    assert out.lead.email == "ann@x.com"
    assert out.lead.email_status is EmailStatus.VERIFIED


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_an_invalid_winning_value_leaves_the_field_empty_without_raising() -> None:
    out = project(
        contrib("a", {"person.email": "not an email", "person.full_name": "Ann"})
    )
    assert out.lead is not None
    assert out.lead.email is None
    assert "not an email" not in repr(out)


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_an_invalid_only_identity_forms_no_lead() -> None:
    out = project(contrib("a", {"person.email": "not an email"}))
    assert out.lead is None


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_crm_sources_bare_email_path_is_read_as_person_email() -> None:
    hub = contrib("a", {"email": "ann@x.com", "opt_out": True, "suppressed": True})
    out = project(hub, contrib("c", {"person.full_name": "Ann"}))
    assert out.lead is not None
    assert out.lead.email == "ann@x.com"
    assert (out.lead.opt_out, out.lead.suppressed) == (True, True)
    assert "email" in hub.values  # the stored contribution keeps its own path
    assert {p.canonical_path for p in hub.provenance} >= {"email"}


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_bare_email_competes_with_person_email_under_the_same_order() -> None:
    out = project(
        contrib("a", {"email": "hub@x.com"}),
        contrib("c", {"person.email": "api@x.com"}),
    )
    assert out.lead is not None
    assert out.lead.email == "hub@x.com"  # a outranks c
    emails = [p for p in out.provenance if p.canonical_path == "person.email"]
    assert {(p.source_name, p.superseded) for p in emails} == {
        ("a", False),
        ("c", True),
    }


# Verifies: specs/lead-source-adapters/requirements.md#8.3
def test_a_contribution_with_both_email_paths_keeps_the_bare_one_apart() -> None:
    both = contrib("a", {"email": "one@x.com", "person.email": "two@x.com"})
    out = project(both, contrib("c", {"person.full_name": "Ann"}))
    assert out.lead is not None
    assert out.lead.email == "two@x.com"


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_signals_from_every_source_are_carried_not_dropped() -> None:
    out = project(
        contrib(
            "a",
            {
                "person.full_name": "Ann",
                "company.signals": (TechSignal(label="aws", strength=0.4),),
            },
        ),
        contrib(
            "c",
            {
                "company.signals": (
                    TechSignal(label="gcp", strength=0.9),
                    IntentSignal(label="hiring", strength=0.2),
                )
            },
        ),
    )
    assert out.lead is not None
    assert [s.label for s in out.lead.tech_signals] == ["aws", "gcp"]
    assert [s.label for s in out.lead.intent_signals] == ["hiring"]


# Verifies: specs/lead-source-adapters/requirements.md#24.4
def test_signal_strength_never_influences_the_outcome() -> None:
    def build(sa: float, sc: float):  # type: ignore[no-untyped-def]
        return project(
            contrib(
                "a",
                {
                    "person.full_name": "Ann",
                    "company.signals": (TechSignal(label="aws", strength=sa),),
                },
            ),
            contrib(
                "c",
                {
                    "company.signals": (TechSignal(label="aws", strength=sc),),
                    "person.title": untrusted("T"),
                },
            ),
        )

    low, high = build(0.1, 0.9), build(0.9, 0.1)
    assert low.lead is not None
    assert high.lead is not None
    # The kept strength follows the trust order (source a), never the number.
    assert low.lead.tech_signals[0].strength == 0.1
    assert high.lead.tech_signals[0].strength == 0.9
    assert low.agreement == high.agreement
    assert [p.model_dump() for p in low.provenance] == [
        p.model_dump() for p in high.provenance
    ]
    assert low.lead.full_name == high.lead.full_name


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_company_id_is_one_id_per_domain_set() -> None:
    def company(domain: Any) -> str:
        out = project(
            contrib("a", {"person.full_name": "Ann", "company.domain": domain})
        )
        assert out.lead is not None
        return str(out.lead.employments[0].company.company_id)

    assert company("X.com ") == company(("x.com",)) == company(frozenset({"x.com"}))
    assert company("x.com") != company("y.com")
    assert company(("x.com", "y.com")) == company(("y.com", "x.com"))


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_company_id_and_domains_use_the_registrable_domain_set() -> None:
    def company(domain: Any) -> Any:
        out = project(
            contrib("a", {"person.full_name": "Ann", "company.domain": domain})
        )
        assert out.lead is not None
        return out.lead.employments[0].company

    plain, sub = company("acme.com"), company(("WWW.acme.com", "mail.acme.com"))
    assert plain.company_id == sub.company_id == company_id_for({"acme.com"})
    assert plain.domains == sub.domains == ("acme.com",)
    assert company("one.github.io").company_id != company("two.github.io").company_id


# Verifies: specs/lead-source-adapters/requirements.md#8.16
def test_a_company_with_no_usable_domain_is_per_lead_never_merged_by_name() -> None:
    def company(cluster_id: str, domain: Any = None) -> Any:
        values: dict[str, Any] = {"person.full_name": "Ann", "company.name": "Acme"}
        if domain is not None:
            values["company.domain"] = domain
        out = project_lead(IdentityCluster(cluster_id, (contrib("a", values),)), RANKS)
        assert out.lead is not None
        return out.lead.employments[0].company

    one, two = company("lead-1"), company("lead-2")
    assert one.company_id != two.company_id  # same name, still two companies
    assert one.company_id == company("lead-1").company_id  # stable
    assert company("lead-1", "gmail.com").company_id == one.company_id
    assert company("lead-1", "localhost").domains == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_no_employment_when_no_organization_is_known() -> None:
    out = project(
        contrib("a", {"person.full_name": "Ann", "person.title": untrusted("CTO")})
    )
    assert out.lead is not None
    assert out.lead.employments == ()


# Verifies: specs/lead-source-adapters/requirements.md#8.7
def test_absences_are_exposed_by_kind_and_never_compete() -> None:
    neg = SourceAbsence(
        canonical_path="person.email",
        source_name="c",
        kind=AbsenceKind.NEGATIVE_EVIDENCE,
        raw_field_path="email",
    )
    na = SourceAbsence(
        canonical_path="person.title", source_name="b", kind=AbsenceKind.NOT_APPLICABLE
    )
    out = project(
        contrib("a", {"person.full_name": "Ann"}, absences=(neg,)),
        contrib("b", {}, absences=(na,)),
    )
    assert out.negative_evidence == (neg,)
    assert out.not_applicable == (na,)
    assert out.lead is not None
    assert out.lead.email is None


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_result_repr_withholds_personal_data() -> None:
    out = project(*sample())
    text = repr(out)
    for secret in ("ann@x.com", "Ann A", "linkedin.com/in/ann"):
        assert secret not in text


# Verifies: specs/lead-source-adapters/requirements.md#8.12
def test_re_keying_the_bare_email_leaves_the_stored_contribution_untouched() -> None:
    crm = contrib(
        "c",
        {"email": "ann@x.com", "opt_out": True},
        absences=(
            SourceAbsence(
                canonical_path="email",
                source_name="c",
                kind=AbsenceKind.NEGATIVE_EVIDENCE,
                raw_field_path="email",
            ),
        ),
    )
    before = canonical_json(crm)
    paths = dict(crm.values)
    out = project(crm, contrib("a", {"person.full_name": "Ann"}))
    assert out.lead is not None
    assert out.lead.email == "ann@x.com"
    assert canonical_json(crm) == before
    assert dict(crm.values) == paths
    assert "person.email" not in crm.values
    assert crm.provenance[0].canonical_path == "email"
    assert crm.absences[0].canonical_path == "email"


# Task 16.12: the projection result carries the Match Key kinds and the resolved
# conflicts, so the log line is derived rather than hand-assembled.
def _two_source_cluster(merged_by: tuple[MatchKeyKind, ...]) -> IdentityCluster:
    return IdentityCluster(
        "cluster",
        (
            contrib("a", {"person.title": "CTO", "person.full_name": "Ann Lee"}),
            contrib("b", {"person.title": "CEO", "person.full_name": "Ann Lee"}),
            contrib("c", {"person.title": "CFO"}),
        ),
        merged_by,
    )


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_result_carries_the_match_key_kinds_of_the_cluster() -> None:
    both = (MatchKeyKind.LINKEDIN_URL, MatchKeyKind.VERIFIED_EMAIL)
    result = project_lead(_two_source_cluster(both), RANKS)
    assert result.match_keys == both
    assert result.contribution_count == 3


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_the_result_carries_only_the_paths_whose_conflict_was_resolved() -> None:
    result = project_lead(_two_source_cluster(()), RANKS)
    assert result.conflicts == (
        ResolvedConflict(
            canonical_path="person.title",
            winning_source="a",
            superseded_count=2,
            decided_by=ConflictRule.TRUST_RANK,
        ),
    )


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_a_projection_without_conflicts_carries_none() -> None:
    result = project(contrib("a", {"person.title": "CTO"}))
    assert result.conflicts == ()
    assert result.match_keys == ()
    assert result.contribution_count == 1


# Verifies: specs/lead-source-adapters/requirements.md#21.4
def test_no_personal_value_in_the_repr_of_the_conflicts() -> None:
    result = project_lead(
        IdentityCluster(
            "cluster",
            (
                contrib("a", {"person.title": "Zebulon-Title-A"}),
                contrib("b", {"person.title": "Hortensia-Title-B"}),
            ),
        ),
        RANKS,
    )
    assert "Zebulon" not in repr(result)
    assert "Hortensia" not in repr(result)


# Verifies: specs/lead-source-adapters/requirements.md#8.8
def test_conflicts_ignore_contribution_order() -> None:
    members = list(_two_source_cluster(()).contributions)
    seen = {
        project_lead(IdentityCluster("cluster", tuple(p)), RANKS).conflicts
        for p in itertools.permutations(members)
    }
    assert len(seen) == 1
